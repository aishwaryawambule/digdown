"""The write adapter — the ONLY component that mutates the system.

Agents never touch this; only the Executor does, once a fix is approved by a person
or by the auto heal rules in heal.py. It
knows how to capture a pre-change snapshot, apply an allow-listed action, and
roll it back. Compose operations go through an injectable `compose_runner` so the
executor's logic can be tested without a real Docker daemon.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from .allowlist import HOST_TARGET
from .plan_models import Action, ActionType


class WriteAdapter(Protocol):
    def capture(self, target: str) -> dict[str, Any]: ...
    def apply(self, action: Action) -> None: ...
    def rollback(self, action: Action, snapshot: dict[str, Any]) -> None: ...


def _default_compose_runner(compose_path: str) -> Callable[[list[str]], None]:
    def run(args: list[str]) -> None:
        subprocess.run(["docker", "compose", "-f", compose_path, *args], check=True)
    return run


class DockerWriter:
    def __init__(self, client, compose_path: str, env_path: Optional[str] = None,
                 compose_runner: Optional[Callable[[list[str]], None]] = None):
        self._client = client
        self.compose_path = compose_path
        self.env_path = env_path
        self.compose_runner = compose_runner or _default_compose_runner(compose_path)

    def _container(self, target: str):
        containers = self._client.containers.list(all=True)
        for c in containers:
            if c.name == target:
                return c
        # Many stacks have a service called `db`. Restarting the wrong app's
        # database is far worse than failing, so a shared name is refused.
        matches = [c for c in containers
                   if (c.labels or {}).get("com.docker.compose.service") == target]
        if len(matches) > 1:
            names = ", ".join(c.name for c in matches)
            raise LookupError(f"{target!r} matches more than one container ({names})")
        if matches:
            return matches[0]
        raise LookupError(f"no container for target {target!r}")

    def _read_env(self) -> str:
        if self.env_path and Path(self.env_path).exists():
            return Path(self.env_path).read_text()
        return ""

    def capture(self, target: str) -> dict[str, Any]:
        if target == HOST_TARGET:
            return {}  # a disk cleanup has nothing to restore
        c = self._container(target)
        attrs = c.attrs or {}
        host_cfg = attrs.get("HostConfig") or {}
        return {
            "image": attrs.get("Image") or (attrs.get("Config") or {}).get("Image", ""),
            "status": (attrs.get("State") or {}).get("Status", "unknown"),
            "mem_limit_bytes": host_cfg.get("Memory", 0),
            "memswap_limit_bytes": host_cfg.get("MemorySwap", 0),
            "env_backup": self._read_env(),
        }

    def _force_recreate(self, target: str) -> None:
        self.compose_runner(["up", "-d", "--force-recreate", "--no-deps", target])

    def apply(self, action: Action) -> None:
        if action.type == ActionType.RESTART:
            self._container(action.target).restart(timeout=10)
        elif action.type == ActionType.START:
            self._container(action.target).start()
        elif action.type == ActionType.RECREATE:
            self._force_recreate(action.target)
        elif action.type == ActionType.SET_ENV_VAR:
            self._set_env(action.params["key"], action.params["value"])
            self._force_recreate(action.target)
        elif action.type == ActionType.SET_RESOURCE_LIMIT:
            c = self._container(action.target)
            current = ((c.attrs or {}).get("HostConfig") or {}).get("Memory", 0) or 0
            new = action.params["mem_limit_bytes"]
            if current <= 0:
                raise ValueError(
                    f"{action.target} has no memory limit to raise (unlimited)")
            if new <= current:
                raise ValueError(
                    f"new mem_limit {new} must exceed current {current}")
            c.update(mem_limit=new, memswap_limit=new)
        elif action.type == ActionType.CLEAN_DOCKER:
            # Only what nothing uses. Never containers or volumes, which hold people's data.
            self._client.images.prune(filters={"dangling": True})
            self._client.api.prune_builds()
        else:  # unreachable: types are allow-listed upstream
            raise ValueError(f"unsupported action {action.type!r}")

    def rollback(self, action: Action, snapshot: dict[str, Any]) -> None:
        if action.type in (ActionType.RESTART, ActionType.START, ActionType.CLEAN_DOCKER):
            return  # restoring a running process is a no-op, and deleted data is gone
        if action.type == ActionType.SET_RESOURCE_LIMIT:
            self._container(action.target).update(
                mem_limit=snapshot.get("mem_limit_bytes"),
                memswap_limit=snapshot.get("memswap_limit_bytes"))
            return
        if action.type == ActionType.SET_ENV_VAR and self.env_path is not None:
            Path(self.env_path).write_text(snapshot.get("env_backup", ""))
        # recreate / set_env_var: redeploy from the restored definition
        self._force_recreate(action.target)

    def _set_env(self, key: str, value: str) -> None:
        if self.env_path is None:
            raise RuntimeError("set_env_var requires env_path to be configured")
        lines = [ln for ln in self._read_env().splitlines()
                 if not ln.startswith(f"{key}=")]
        lines.append(f"{key}={value}")
        Path(self.env_path).write_text("\n".join(lines) + "\n")
