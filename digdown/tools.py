"""Read-only tools for the Diagnostician.

Least privilege: only read operations, and even then secret-bearing fields are
withheld — `inspect_container` exposes env var *names* but never values (full
all tool output is redacted). Tool output is returned as a
string observation and labeled untrusted, never as instructions to the model.
Errors are returned as soft observations so the agent can adapt rather than crash.
"""
from __future__ import annotations

from typing import Any, Optional

from .discovery import Project
from .host_tools import system_report

# Tools that look a target up as a local Docker container. They are meaningless
# for a remote target (a grafana alert on a service not in the local
# fleet) — there they only ever return "no container named X", which the agent
# misreads as evidence the target was deleted. Withheld unless the target is a
# known local container/service.
_CONTAINER_TOOLS = frozenset({"get_container_state", "get_recent_logs",
                              "inspect_container"})


def _find_state(states, target: str):
    for s in states:
        if s.name == target or s.service == target:
            return s
    return None


class DiagnosticTools:
    def __init__(self, source, project: Optional[Project] = None,
                 log_tail_lines: int = 200, redactor=None,
                 allowed_hosts=None, http_fetch=None,
                 host_introspection: bool = False, disk_path: str = "/",
                 host_runner=None):
        self.source = source
        self.project = project or Project()
        self.log_tail_lines = log_tail_lines
        self.redactor = redactor
        self.allowed_hosts = allowed_hosts
        self._http_fetch = http_fetch
        self.host_introspection = host_introspection
        self.disk_path = disk_path
        self.host_runner = host_runner

    def _redact(self, text: str) -> str:
        return self.redactor.redact(text) if self.redactor is not None else text

    # ---- tool implementations (each returns a string observation) ----

    def get_container_state(self, target: str) -> str:
        s = _find_state(self.source.list_states(), target)
        if s is None:
            return f"no container named {target!r}"
        line = (
            f"{s.name}: status={s.status} health={s.health.value} "
            f"exit_code={s.exit_code} restart_count={s.restart_count} "
            f"oom_killed={s.oom_killed} image={s.image or '?'}"
        )
        parts = []
        if s.mem_percent is not None and s.mem_bytes is not None and s.mem_limit_bytes:
            parts.append(
                f"memory={round(s.mem_bytes / 1_000_000)}/"
                f"{round(s.mem_limit_bytes / 1_000_000)}MB({s.mem_percent}%)")
        if s.cpu_percent is not None:
            parts.append(f"cpu={s.cpu_percent}%")
        if parts:
            line += " " + " ".join(parts)
        return line

    def get_recent_logs(self, target: str, lines: Optional[int] = None) -> str:
        n = min(lines or self.log_tail_lines, self.log_tail_lines)
        getter = getattr(self.source, "get_logs", None)
        if getter is None:
            return "logs unavailable from this source"
        raw = getter(target, n) or ""
        tail = "\n".join(raw.splitlines()[-n:])
        if not tail:
            return f"no logs for {target!r}"
        # Logs are attacker-influenceable: labeled untrusted, treated as data,
        # and run through the redactor so no secret reaches the model.
        return (f"<untrusted-logs target={target}>\n{self._redact(tail)}\n"
                f"</untrusted-logs>")

    def get_topology(self) -> str:
        if not self.project.services:
            return "no topology discovered"
        lines = []
        for svc in self.project.services:
            deps = self.project.depends_on.get(svc, [])
            lines.append(f"{svc} depends on: {', '.join(deps) if deps else '(none)'}")
        return "\n".join(lines)

    def inspect_container(self, target: str) -> str:
        inspector = getattr(self.source, "inspect", None)
        if inspector is None:
            return "inspect unavailable from this source"
        info = inspector(target) or {}
        if not info:
            return f"no inspect data for {target!r}"
        env = info.get("env") or {}
        env_names = sorted(env.keys()) if isinstance(env, dict) else list(env)
        parts = [
            f"image={info.get('image', '?')}",
            f"command={info.get('command', '?')}",
            f"restart_policy={info.get('restart_policy', '?')}",
            f"env_names={env_names}",  # names only — values are secrets
        ]
        return self._redact("\n".join(parts))  # a command line can hold a password

    def http_probe(self, url: str) -> str:
        if self._http_fetch is None:
            return "http probing unavailable"
        from urllib.parse import urlparse
        # Fail-closed: no allowlist (None) or empty allowlist → refuse immediately,
        # do not make any network request.
        # NOTE: we deliberately do NOT block loopback / RFC-1918 / link-local ranges
        # here.  Internal hosts (compose DNS names, tunnelled ports on 127.0.0.1) are
        # INTENTIONAL probe targets; IP-range blocking would break the feature.  The
        # allowlist configured via `probes:` in digdown.yml is the security boundary.
        if not self.allowed_hosts:
            return "refused: no probe allowlist configured"
        raw_host = urlparse(url).hostname or ""
        # Normalize: lowercase + strip a single trailing DNS dot so that "WEB." and
        # "web" are treated as the same host.
        host = raw_host.lower().rstrip(".")
        normalized_allowlist = {h.lower().rstrip(".") for h in self.allowed_hosts}
        if host not in normalized_allowlist:
            return f"refused: {host!r} is not an in-scope probe host"
        status, latency_ms, body, error = self._http_fetch(url, 5000)
        if error is not None:
            return f"request failed: {error}"
        snippet = "\n".join((body or "").splitlines()[:20])
        return (f"status={status} latency_ms={round(latency_ms)}\n"
                f"<untrusted-body url={url}>\n{self._redact(snippet)}\n</untrusted-body>")

    def redacted_logs(self, target: str, lines: int = 200, max_lines: int = 1000) -> str:
        if target not in self.valid_targets():
            return f"no logs for {target!r}"
        n = min(max(1, lines), max_lines)
        getter = getattr(self.source, "get_logs", None)
        if getter is None:
            return "logs unavailable from this source"
        raw = getter(target, n) or ""
        tail = "\n".join(raw.splitlines()[-n:])
        return self._redact(tail) if tail else f"no logs for {target!r}"

    def system_inspect(self) -> str:
        if not self.host_introspection:
            return "host introspection is disabled"
        return self._redact(system_report(self.source, self.disk_path,
                                           runner=self.host_runner))

    def valid_targets(self) -> set[str]:
        """Real, addressable entities — live container names + compose service
        names. The allow-list validates plan targets against this set."""
        names: set[str] = set(self.project.services)
        for s in self.source.list_states():
            names.add(s.name)
            if s.service:
                names.add(s.service)
        return names

    # ---- registry surface ----

    def schemas(self, target: Optional[str] = None) -> list[dict[str, Any]]:
        """Tool schemas offered to the agent, scoped to what is useful here.

        * Container-inspection tools are withheld when `target` is given but is
          not a known local container/service (a remote grafana target) —
          they would only return phantom "no container named X" noise.
        * `http_probe` is withheld when no probe allowlist is configured: there
          is nothing it could legitimately GET, so don't tempt the model into a
          "refused" observation it then cites as evidence.
        """
        def fn(name, desc, props, required):
            return {
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "required": required,
                    },
                },
            }

        target_field = {"type": "string",
                        "description": "container or compose service name"}
        schemas = [
            fn("get_container_state", "Current status/health/exit code of a container.",
               {"target": target_field}, ["target"]),
            fn("get_recent_logs", "Recent log lines for a container (tail).",
               {"target": target_field,
                "lines": {"type": "integer", "description": "how many tail lines"}},
               ["target"]),
            fn("get_topology", "Service dependency graph from the compose file.",
               {}, []),
            fn("inspect_container",
               "Image, command, restart policy, and env var names (no values).",
               {"target": target_field}, ["target"]),
            fn("http_probe",
               "GET an in-scope app URL; returns status, latency, and a body snippet.",
               {"url": {"type": "string", "description": "full http(s) URL to GET"}},
               ["url"]),
        ]

        if self.host_introspection:
            schemas.append(fn(
                "system_inspect",
                "Read-only snapshot of the HOST Digdown runs on and its containers: "
                "load, memory, disk usage (incl. docker images/volumes/build-cache), "
                "top processes, and per-container cpu/mem. Use for host-level incidents "
                "(e.g. disk_full) or to find a resource-hog container. Takes no arguments.",
                {}, []))

        drop: set[str] = set()
        if target is not None and target not in self.valid_targets():
            drop |= set(_CONTAINER_TOOLS)
        if not self.allowed_hosts:
            drop.add("http_probe")
        return [s for s in schemas if s["function"]["name"] not in drop]

    def call(self, name: str, arguments: dict[str, Any]) -> str:
        impl = {
            "get_container_state": self.get_container_state,
            "get_recent_logs": self.get_recent_logs,
            "get_topology": self.get_topology,
            "inspect_container": self.inspect_container,
            "http_probe": self.http_probe,
            "system_inspect": self.system_inspect,
        }.get(name)
        if impl is None:
            return f"unknown tool {name!r}"
        try:
            return impl(**(arguments or {}))
        except TypeError as e:
            return f"bad arguments for {name}: {e}"
        except Exception as e:  # noqa: BLE001 - surfaced as observation
            return f"tool {name} failed: {e}"
