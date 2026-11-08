from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional, Protocol

from .config import NO_STACK
from .models import ContainerState, HealthStatus


_STOP_LOOKBACK_SECONDS = 24 * 60 * 60


def parse_depends_on(label: Optional[str]) -> list[str]:
    """Compose records what a service depends on as `db:service_healthy:false,...`, so
    this is known for every stack without needing to read its Compose file."""
    return [part.split(":")[0] for part in (label or "").split(",") if part.strip()]


def _parse_docker_time(value: Optional[str]) -> Optional[datetime]:
    """Docker gives nanoseconds, which datetime can't read, and year 1 for never."""
    try:
        parsed = datetime.fromisoformat(f"{value[:19]}+00:00")
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.year > 1 else None


class ContainerSource(Protocol):
    def list_states(self) -> list[ContainerState]:
        ...

    def get_logs(self, name: str, lines: int = 200,
                 since_seconds: int | None = None) -> str:
        ...

    def inspect(self, name: str) -> dict:
        ...


_HEALTH_MAP = {
    "healthy": HealthStatus.HEALTHY,
    "unhealthy": HealthStatus.UNHEALTHY,
    "starting": HealthStatus.STARTING,
}


def compute_stats(raw: dict, limit_bytes: int) -> dict:
    """Derive cpu%/mem from a ``container.stats(stream=False)`` payload and the
    CONFIGURED memory limit (HostConfig.Memory; 0 = unlimited). Pure; any missing
    or malformed key degrades that field to None rather than raising."""
    out = {"cpu_percent": None, "mem_bytes": None,
           "mem_limit_bytes": (int(limit_bytes) if limit_bytes else None),
           "mem_percent": None}
    mem = (raw or {}).get("memory_stats") or {}
    usage = mem.get("usage")
    if isinstance(usage, (int, float)):
        inactive = (mem.get("stats") or {}).get("inactive_file")
        inactive = int(inactive) if isinstance(inactive, (int, float)) else 0
        mem_bytes = max(0, int(usage) - inactive)
        out["mem_bytes"] = mem_bytes
        if limit_bytes and limit_bytes > 0:
            out["mem_percent"] = round(mem_bytes / limit_bytes * 100, 1)
    cpu = (raw or {}).get("cpu_stats") or {}
    pre = (raw or {}).get("precpu_stats") or {}
    try:
        cpu_delta = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        system_delta = cpu["system_cpu_usage"] - pre["system_cpu_usage"]
        online = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or []) or 1
        out["cpu_percent"] = (round(cpu_delta / system_delta * online * 100, 1)
                              if system_delta > 0 else 0.0)
    except (KeyError, TypeError, ZeroDivisionError):
        pass
    return out


class DockerAdapter:
    """Read-only view of containers via a docker-py-compatible client.

    When `project_name` is set, only containers belonging to that compose project
    (label `com.docker.compose.project`) are watched — so Digdown ignores
    unrelated containers on the same host. Pass a list to watch several projects
    (e.g. ["shop", "grafana"]); empty watches every container.
    """

    def __init__(self, client, project_name="", exclude_projects="",
                 collect_resource_stats=True):
        self._client = client
        self.collect_resource_stats = collect_resource_stats
        # Accept a single project name or a list of them; normalise to a set.
        if isinstance(project_name, str):
            names = [project_name] if project_name else []
        else:
            names = [n for n in project_name if n]
        self.project_names = set(names)
        # Projects to never watch (e.g. a stack intentionally taken down) —
        # takes precedence over the allowlist.
        if isinstance(exclude_projects, str):
            excl = [exclude_projects] if exclude_projects else []
        else:
            excl = [n for n in exclude_projects if n]
        self.exclude_names = set(excl)

    def _in_scope(self, labels: dict) -> bool:
        project = (labels or {}).get("com.docker.compose.project") or NO_STACK
        if project in self.exclude_names:
            return False
        if not self.project_names:
            return True
        return project in self.project_names

    def _sample_stats(self, running):
        """running: list of (cid, limit_bytes). Returns {cid: compute_stats(...)},
        parallelized; a per-container stats failure is swallowed (cid omitted)."""
        if not running:
            return {}

        def one(item):
            cid, limit = item
            try:
                raw = self._client.api.stats(cid, stream=False)
                return cid, compute_stats(raw, limit)
            except Exception:  # noqa: BLE001 - a vanished container must not break the poll
                return cid, None

        out = {}
        with ThreadPoolExecutor(max_workers=min(8, len(running))) as ex:
            for cid, res in ex.map(one, running):
                if res is not None:
                    out[cid] = res
        return out

    def stopped_on_purpose(self, target: str) -> bool:
        """Was this container stopped by someone rather than failing? Stopping an app
        that ignores the signal exits with 137, exactly like a crash, and only Docker's
        event log shows the difference. The log is short: on a busy machine events
        can roll off in minutes, so ask soon after it goes down."""
        now = int(time.time())
        events = self._client.events(
            since=now - _STOP_LOOKBACK_SECONDS, until=now, decode=True,
            filters={"container": target, "event": ["start", "kill", "stop"]})
        last = None
        for event in events:
            if event.get("Action") in ("start", "kill", "stop"):
                last = event["Action"]
        return last in ("kill", "stop")

    def list_states(self) -> list[ContainerState]:
        """One cheap summary call enumerates every container; we inspect only the
        in-scope ones, then sample ``container.stats()`` for the RUNNING ones in
        parallel. Round-trips: 1 + len(in-scope) inspects + len(running) stats."""
        inspected = []
        for summary in self._client.api.containers(all=True):
            if not self._in_scope(summary.get("Labels") or {}):
                continue
            cid = summary.get("Id", "")
            inspected.append((summary, self._client.api.inspect_container(cid) or {}))

        stats_by_cid = {}
        if self.collect_resource_stats:
            running = [
                (s.get("Id", ""), int(((a.get("HostConfig") or {}).get("Memory")) or 0))
                for s, a in inspected
                if ((a.get("State") or {}).get("Status")) == "running"
            ]
            stats_by_cid = self._sample_stats(running)

        states: list[ContainerState] = []
        for summary, attrs in inspected:
            state = attrs.get("State", {}) or {}
            cfg = attrs.get("Config", {}) or {}
            host_cfg = attrs.get("HostConfig", {}) or {}
            health_status = (state.get("Health") or {}).get("Status")
            names = summary.get("Names") or []
            cid = summary.get("Id", "")
            name = (attrs.get("Name") or (names[0] if names else "")).lstrip("/")
            cfg_labels = cfg.get("Labels") or (summary.get("Labels") or {})
            res = stats_by_cid.get(cid) or {}
            states.append(
                ContainerState(
                    id=str(cid)[:12],
                    name=name,
                    service=cfg_labels.get("com.docker.compose.service"),
                    project=cfg_labels.get("com.docker.compose.project", ""),
                    status=state.get("Status") or summary.get("State", "unknown"),
                    health=_HEALTH_MAP.get(health_status, HealthStatus.NONE),
                    exit_code=state.get("ExitCode"),
                    restart_count=attrs.get("RestartCount", 0),
                    oom_killed=bool(state.get("OOMKilled", False)),
                    image=summary.get("Image") or cfg.get("Image", ""),
                    finished_at=_parse_docker_time(state.get("FinishedAt")),
                    depends_on=parse_depends_on(cfg_labels.get("com.docker.compose.depends_on")),
                    restart_policy=(host_cfg.get("RestartPolicy") or {}).get("Name", ""),
                    cpu_percent=res.get("cpu_percent"),
                    mem_bytes=res.get("mem_bytes"),
                    # Stats only cover running containers, and a container that ran
                    # out of memory is exactly the one whose limit is needed.
                    mem_limit_bytes=res.get("mem_limit_bytes") or host_cfg.get("Memory") or None,
                    mem_percent=res.get("mem_percent"),
                )
            )
        return states

    def _find(self, name: str):
        containers = self._client.containers.list(all=True)
        for c in containers:
            if c.name == name:
                return c
        # A service name alone, such as `db`, may exist in several stacks.
        for c in containers:
            if (c.labels or {}).get("com.docker.compose.service") == name:
                return c
        return None

    def get_logs(self, name: str, lines: int = 200,
                 since_seconds: int | None = None) -> str:
        c = self._find(name)
        if c is None:
            return ""
        kwargs: dict = {"tail": lines}
        if since_seconds is not None:
            kwargs["since"] = int(time.time()) - since_seconds
        raw = c.logs(**kwargs)
        return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)

    def inspect(self, name: str) -> dict:
        c = self._find(name)
        if c is None:
            return {}
        attrs = c.attrs or {}
        cfg = attrs.get("Config", {}) or {}
        host_cfg = attrs.get("HostConfig", {}) or {}
        # Env arrives as ["KEY=value", ...]; keep names→values internally so the
        # tool layer can expose names only (values are secrets until redaction).
        env = {}
        for item in cfg.get("Env", []) or []:
            key, _, val = item.partition("=")
            env[key] = val
        return {
            "image": cfg.get("Image", ""),
            "command": " ".join(cfg.get("Cmd") or []) or (cfg.get("Entrypoint") and
                       " ".join(cfg.get("Entrypoint"))) or "",
            "restart_policy": (host_cfg.get("RestartPolicy") or {}).get("Name", ""),
            "env": env,
        }
