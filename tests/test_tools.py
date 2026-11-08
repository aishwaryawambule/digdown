from digdown.discovery import Project
from digdown.models import ContainerState, HealthStatus
from digdown.tools import DiagnosticTools


class FakeDockerSource:
    def __init__(self, states, logs=None, inspects=None):
        self._states = states
        self._logs = logs or {}
        self._inspects = inspects or {}

    def list_states(self):
        return self._states

    def get_logs(self, name, lines=200):
        return self._logs.get(name, "")

    def inspect(self, name):
        return self._inspects.get(name, {})


def _tools():
    states = [
        ContainerState(id="1", name="web", service="web", status="exited",
                       exit_code=137, oom_killed=True, restart_count=4),
        ContainerState(id="2", name="db", service="db", status="running",
                       health=HealthStatus.HEALTHY),
    ]
    logs = {"web": "boom\nOOMKilled\n" + "line\n" * 500}
    inspects = {"web": {"image": "web:1", "command": "node app.js",
                        "env": {"DB_HOST": "db", "DB_PASSWORD": "hunter2"}}}
    project = Project(services=["web", "db"], depends_on={"web": ["db"], "db": []})
    return DiagnosticTools(FakeDockerSource(states, logs, inspects), project,
                           log_tail_lines=50)


def test_get_container_state():
    out = _tools().call("get_container_state", {"target": "web"})
    assert "exited" in out and "137" in out


def test_get_container_state_unknown_target_is_soft_error():
    out = _tools().call("get_container_state", {"target": "nope"})
    assert "no container" in out.lower()


def test_get_recent_logs_truncates_to_tail():
    out = _tools().call("get_recent_logs", {"target": "web"})
    assert "OOMKilled" not in out.split("\n")[0]  # tail keeps the last lines
    assert out.count("\n") <= 51  # ~log_tail_lines


def test_get_topology_reflects_depends_on():
    out = _tools().call("get_topology", {})
    assert "web" in out and "db" in out


def test_inspect_exposes_env_names_not_values():
    out = _tools().call("inspect_container", {"target": "web"})
    assert "DB_PASSWORD" in out          # name is shown
    assert "hunter2" not in out          # value is never shown


def test_schemas_and_unknown_tool():
    t = _tools()
    names = {s["function"]["name"] for s in t.schemas()}
    assert {"get_container_state", "get_recent_logs", "get_topology",
            "inspect_container"} <= names
    assert "unknown tool" in t.call("does_not_exist", {}).lower()


def test_schemas_drops_container_tools_for_nonlocal_target():
    # A grafana/external target that is not a known local container/service must
    # not be offered the container-inspection tools: looking them up only yields
    # "no container named X", which the agent misreads as evidence of deletion.
    names = {s["function"]["name"]
             for s in _tools().schemas(target="shop-admin")}
    assert "get_container_state" not in names
    assert "inspect_container" not in names
    assert "get_recent_logs" not in names
    assert "get_topology" in names            # source-agnostic tool stays


def test_schemas_keeps_container_tools_for_local_target():
    names = {s["function"]["name"] for s in _tools().schemas(target="web")}
    assert "get_container_state" in names and "inspect_container" in names


def test_schemas_drops_http_probe_when_no_allowlist():
    # Fail-closed at the registry: with no probe allowlist there is nothing the
    # agent can legitimately GET, so don't offer http_probe at all — that removes
    # the "refused: not in scope" line the model otherwise cites as evidence.
    names = {s["function"]["name"] for s in _tools().schemas()}  # allowed_hosts=None
    assert "http_probe" not in names


def test_schemas_offers_http_probe_when_allowlist_present():
    t = DiagnosticTools(FakeDockerSource([], {}, {}), None,
                        allowed_hosts={"app.example"})
    names = {s["function"]["name"] for s in t.schemas()}
    assert "http_probe" in names


def test_valid_targets_includes_container_and_service_names():
    targets = _tools().valid_targets()
    assert {"web", "db"} <= targets


def test_logs_are_redacted():
    from digdown.redactor import Redactor, SecretRegistry, MASK
    reg = SecretRegistry(); reg.register("supersecrettoken99")
    states = [ContainerState(id="1", name="web", status="running")]
    logs = {"web": "starting up\nAUTH=supersecrettoken99\nready\n"}
    tools = DiagnosticTools(FakeDockerSource(states, logs, {}), None,
                            redactor=Redactor(reg))
    out = tools.call("get_recent_logs", {"target": "web"})
    assert "supersecrettoken99" not in out
    assert MASK in out


def test_redacted_logs_returns_clean_redacted_tail():
    from digdown.redactor import Redactor, SecretRegistry, MASK
    reg = SecretRegistry(); reg.register("MY_SECRET_VALUE")
    states = [ContainerState(id="1", name="web", status="running")]
    logs = {"web": "starting up\ntoken=MY_SECRET_VALUE\nready\n"}
    tools = DiagnosticTools(FakeDockerSource(states, logs, {}), None,
                            log_tail_lines=100, redactor=Redactor(reg))
    out = tools.redacted_logs("web", lines=50)
    assert "MY_SECRET_VALUE" not in out
    assert "<untrusted-logs" not in out      # clean, no LLM wrapper
    assert out.strip() != ""


def test_redacted_logs_unknown_target_returns_soft_error():
    from digdown.redactor import Redactor, SecretRegistry
    reg = SecretRegistry()
    states = [ContainerState(id="1", name="web", status="running")]
    tools = DiagnosticTools(FakeDockerSource(states, {}, {}), None,
                            redactor=Redactor(reg))
    out = tools.redacted_logs("nonexistent", lines=50)
    assert "no logs" in out.lower()


def test_redacted_logs_respects_max_lines_cap():
    """redacted_logs must never return more lines than max_lines regardless of
    how many lines the source provides or the caller requests."""
    many_lines = "\n".join(f"line {i}" for i in range(10_000))
    states = [ContainerState(id="1", name="web", status="running")]
    tools = DiagnosticTools(
        FakeDockerSource(states, {"web": many_lines}, {}),
        None,
        log_tail_lines=200,
    )
    # Request far more lines than max_lines allows (default max_lines=1000).
    out = tools.redacted_logs("web", lines=10_000_000)
    line_count = len(out.splitlines())
    assert line_count <= 1000, (
        f"redacted_logs returned {line_count} lines; expected ≤ 1000 (max_lines cap)"
    )


def test_http_probe_returns_status_and_redacted_body():
    from digdown.tools import DiagnosticTools

    class _Redactor:
        def redact(self, text):
            return text.replace("sk-secret", "«redacted»")

    fetch = lambda url, timeout: (200, 12.0, "ok sk-secret tail", None)
    t = DiagnosticTools(source=None, allowed_hosts={"web"}, http_fetch=fetch,
                        redactor=_Redactor())
    out = t.http_probe("http://web:8000/health")
    assert "status=200" in out
    assert "«redacted»" in out and "sk-secret" not in out
    assert "<untrusted-body" in out


def test_http_probe_refuses_out_of_scope_host():
    from digdown.tools import DiagnosticTools
    fetch = lambda url, timeout: (200, 1.0, "x", None)
    t = DiagnosticTools(source=None, allowed_hosts={"web"}, http_fetch=fetch)
    assert "refused" in t.http_probe("http://evil.example/x").lower()


def test_http_probe_reports_request_error():
    from digdown.tools import DiagnosticTools
    fetch = lambda url, timeout: (None, 5.0, "", "timed out")
    t = DiagnosticTools(source=None, allowed_hosts={"web"}, http_fetch=fetch)
    assert "failed" in t.http_probe("http://web/x").lower()


# ---- SSRF hardening tests ----

def test_http_probe_refused_when_allowed_hosts_is_none():
    """Fail-closed: when no allowlist is configured, ALL probe requests must be
    refused. The old code was fail-open (None skipped the check entirely)."""
    from digdown.tools import DiagnosticTools
    calls = []
    fetch = lambda url, timeout: calls.append(url) or (200, 1.0, "ok", None)
    t = DiagnosticTools(source=None, allowed_hosts=None, http_fetch=fetch)
    out = t.http_probe("http://169.254.169.254/metadata")
    assert "refused" in out.lower(), f"Expected refusal, got: {out!r}"
    assert calls == [], "fetch must NOT be called when allowlist is None"


def test_http_probe_refused_when_allowed_hosts_is_empty_set():
    """Fail-closed: an empty allowlist (no hosts permitted) also refuses all
    requests without calling the underlying fetch function."""
    from digdown.tools import DiagnosticTools
    calls = []
    fetch = lambda url, timeout: calls.append(url) or (200, 1.0, "ok", None)
    t = DiagnosticTools(source=None, allowed_hosts=set(), http_fetch=fetch)
    out = t.http_probe("http://internal-service/secret")
    assert "refused" in out.lower(), f"Expected refusal, got: {out!r}"
    assert calls == [], "fetch must NOT be called when allowlist is empty"




def test_http_probe_normalizes_host_case_and_trailing_dot():
    """Host normalization: uppercase hostname and trailing dot in the URL should
    still match an allowlist entry stored in lowercase without a trailing dot."""
    from digdown.tools import DiagnosticTools
    calls = []
    fetch = lambda url, timeout: calls.append(url) or (200, 5.0, "pong", None)
    t = DiagnosticTools(source=None, allowed_hosts={"web"}, http_fetch=fetch)
    out = t.http_probe("http://WEB./health")
    assert calls != [], "fetch SHOULD be called when normalized host is in allowlist"
    assert "status=200" in out, f"Expected successful probe result, got: {out!r}"


# ---- Resource monitoring (live memory/cpu) ----

class _Src:
    def __init__(self, states):
        self._states = states
    def list_states(self):
        return self._states
    def get_logs(self, *a, **k):
        return ""
    def inspect(self, *a, **k):
        return {}


def test_get_container_state_includes_resource_line():
    s = ContainerState(id="1", name="api", status="running",
                       mem_bytes=240_000_000, mem_limit_bytes=256_000_000,
                       mem_percent=94.0, cpu_percent=12.0)
    tools = DiagnosticTools(_Src([s]), None)
    out = tools.get_container_state("api")
    assert "memory=240/256MB(94.0%)" in out
    assert "cpu=12.0%" in out


def test_get_container_state_omits_resource_line_when_absent():
    s = ContainerState(id="1", name="api", status="running")  # no stats
    out = DiagnosticTools(_Src([s]), None).get_container_state("api")
    assert "memory=" not in out and "cpu=" not in out


def test_system_inspect_offered_only_when_enabled():
    off = _tools()  # host_introspection defaults False
    assert "system_inspect" not in {s["function"]["name"] for s in off.schemas()}
    on = DiagnosticTools(FakeDockerSource([]), Project(), host_introspection=True,
                         host_runner=lambda argv: "")
    assert "system_inspect" in {s["function"]["name"] for s in on.schemas()}


def test_system_inspect_disabled_refuses_even_if_called():
    assert _tools().call("system_inspect", {}) == "host introspection is disabled"


def test_system_inspect_runs_when_enabled():
    states = [ContainerState(id="1", name="api", status="running",
                             cpu_percent=5.0, mem_percent=70.0)]
    t = DiagnosticTools(FakeDockerSource(states), Project(), host_introspection=True,
                        host_runner=lambda argv: " load average: 0.1" if argv[0] == "uptime" else "")
    out = t.call("system_inspect", {})
    assert "[CONTAINERS]" in out and "api" in out and "[LOAD]" in out
