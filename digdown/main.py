from __future__ import annotations

import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

import docker

from . import access
from .allowlist import unavailable_actions
from .api import create_app
from .config import apply_overrides, config_path, load_config
from .discovery import discover_project
from .docker_adapter import DockerAdapter
from .events import EventBus
from .executor import Executor
from .llm import make_llm
from .notifier import Notifier, WebhookChannel
from .pipeline import Pipeline, recover_interrupted
from .probes import build_http_probe, build_log_probe, probe_hosts, urllib_fetch
from .redactor import Redactor, SecretRegistry
from .store import Store
from .tools import DiagnosticTools
from .verifier import Verifier
from .stats_ring import StatsRing, HostRing
from .host_tools import host_vitals as _host_vitals_fn
from .watcher import Watcher
from .write_adapter import DockerWriter

log = logging.getLogger(__name__)


def _disk_percent(path: str):
    def fn() -> float:
        usage = shutil.disk_usage(path)
        return usage.used / usage.total * 100.0
    return fn


def _build_redactor(compose_path: str, env_path: str, extra_secrets=()) -> Redactor:
    """Seed the secret registry from .env + compose `environment` so a secret is
    masked even when echoed in a log line. Ordinary settings such as MODE=server stay
    readable, or container names turn to gibberish. `extra_secrets` registers
    additional values Digdown holds itself."""
    import yaml

    reg = SecretRegistry()
    for secret in extra_secrets:
        if secret:
            reg.register(secret)
    p = Path(env_path)
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                reg.register_secret_settings({key.strip(): value.strip()})
    cp = Path(compose_path)
    if cp.exists():
        try:
            data = yaml.safe_load(cp.read_text()) or {}
            for spec in (data.get("services") or {}).values():
                env = (spec or {}).get("environment")
                if isinstance(env, dict):
                    reg.register_secret_settings({k: str(v) for k, v in env.items()})
                elif isinstance(env, list):
                    for item in env:
                        if isinstance(item, str) and "=" in item:
                            key, value = item.split("=", 1)
                            reg.register_secret_settings({key: value})
        except Exception:  # noqa: BLE001 - bad compose shouldn't crash startup
            pass
    return Redactor(reg)


class _NotWatchingYet(Exception):
    """First run: Docker is fine, and setup will say what to watch."""


def _watch_scope(config, project) -> list[str]:
    """Which Compose projects to watch; empty means every container. A value in the
    settings file wins, including an empty one, because "everything" is a real choice
    and a compose file that happens to be in this folder mustn't narrow it. With no
    value at all, stay within the compose file's own project."""
    if "project_name" in config.model_fields_set:
        return config.project_name
    return [project.name] if project.name else []


def restart_process() -> None:
    """Start over in place, with the same command line. Sockets close on exec, so
    the port is free again immediately."""
    time.sleep(0.3)  # let the response that triggered this reach the browser
    os.execv(sys.executable, sys.orig_argv)


def _docker_ready() -> bool:
    try:
        docker.from_env(timeout=3).ping()
        return True
    except Exception:  # noqa: BLE001 - not up yet
        return False


def build_app(path: Optional[str] = None):
    path = path or config_path()
    first_run = not Path(path).exists()
    config = load_config(path)
    if first_run:
        # No model has been chosen yet, so don't try to diagnose with the default.
        config.agent_enabled = False
    generated_token = access.ensure_token(
        config, port_is_local=bool(os.environ.get("DIGDOWN_PORT_IS_LOCAL")))
    store = Store(config.db_path)
    apply_overrides(config, store.list_settings())   # persisted runtime overrides
    recover_interrupted(store, config)
    base_config = load_config(path)                   # pristine YAML/defaults for reset-to-YAML
    status_info: dict = {"first_run": first_run}
    bus = EventBus()
    watcher = None
    pipeline = None
    executor = None
    client = None
    project = discover_project(config.compose_path)
    project_names = _watch_scope(config, project)
    http_probe = build_http_probe(config)
    probes = [http_probe] if http_probe else []
    try:
        # A short look first: the real client's timeout is long enough for slow
        # container operations, which would make a hung Docker look like a hung start.
        docker.from_env(timeout=5).ping()
        if first_run:
            # Nothing has been chosen yet. Watching now would record containers the
            # person may be about to exclude, and send them to the AI after setup.
            raise _NotWatchingYet()
        client = docker.from_env()
        adapter = DockerAdapter(client, project_name=project_names,
                                exclude_projects=config.exclude_projects,
                                collect_resource_stats=config.collect_resource_stats)
        log_probe = build_log_probe(config, adapter)
        if log_probe:
            probes.append(log_probe)
        stats_ring = StatsRing()
        host_ring = HostRing()
        watcher = Watcher(source=adapter, store=store, bus=bus, config=config,
                          disk_percent=_disk_percent(config.disk_path),
                          project=project, probes=probes,
                          stats_ring=stats_ring,
                          host_ring=host_ring,
                          host_vitals=lambda: _host_vitals_fn(config.disk_path))
    except _NotWatchingYet:
        adapter = None
    except Exception as exc:  # noqa: BLE001 - Docker may be absent/down
        log.warning("Can't reach Docker, so nothing is being watched (%s)", exc)
        status_info["docker_error"] = " ".join(str(exc).split())[:200]
        adapter = None

    env_path = config.env_path or str(
        Path(config.compose_path).resolve().parent / ".env")

    if adapter is not None and config.agent_enabled:
        # Diagnostician/Critic/Planner over the configured LLM provider
        # (OpenAI-compatible, e.g. Ollama, or first-party Claude).
        # Construction opens no connection; a bad credential/endpoint surfaces per-incident.
        redactor = _build_redactor(config.compose_path, env_path,
                                   extra_secrets=[])
        notifier = Notifier(
            channels=[WebhookChannel(config.notify_webhook_url)]
            if config.notify_webhook_url else [])
        llm = make_llm(config.llm_provider, base_url=config.llm_base_url,
                       api_key=config.llm_api_key, model=config.llm_model,
                       temperature=config.llm_temperature,
                       max_tokens=config.llm_max_tokens,
                       auth_token=config.llm_auth_token)
        tools = DiagnosticTools(adapter, project, config.log_tail_lines,
                                redactor=redactor,
                                allowed_hosts=probe_hosts(config) or None,
                                http_fetch=urllib_fetch,
                                host_introspection=config.host_introspection,
                                disk_path=config.disk_path)
        pipeline = Pipeline(store, bus, config, llm, tools, redactor=redactor,
                            notifier=notifier,
                            unavailable_actions=unavailable_actions(config.compose_path),
                            compose_project=project.name
                            or Path(config.compose_path).resolve().parent.name.lower())

        # The executor is always constructed (construction opens no connection) so
        # a runtime `mode -> execute` flip takes effect without a restart. A fix runs
        # only when config.mode == "execute" AND someone clicks Approve in _decide, or
        # the stack is opted in to auto heal and the rules in heal.py allow it.
        writer = DockerWriter(client, config.compose_path, env_path=env_path)
        verifier = Verifier(adapter, config, probes=probes,
                            disk_percent=_disk_percent(config.disk_path))
        executor = Executor(store, bus, config, writer, verifier,
                            tools.valid_targets, notifier=notifier)
        if config.mode == "execute":
            log.warning("EXECUTE mode: approved fixes will modify your stack.")

    app = create_app(store, bus, config, base_config=base_config, watcher=watcher,
                     pipeline=pipeline, executor=executor,
                     local_only=not config.auth_token, status_info=status_info,
                     config_file=path, restart=restart_process,
                     # Only worth waiting for if Docker was actually the problem.
                     docker_ready=_docker_ready if "docker_error" in status_info else None)
    app.state.generated_token = generated_token
    app.state.first_run = first_run
    return app, config


def main():
    from .cli import cli

    raise SystemExit(cli(["start"]))


if __name__ == "__main__":
    main()
