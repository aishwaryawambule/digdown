"""The newcomer path: safe defaults, first-run setup, and the bundled dashboard."""
import os
import stat

import httpx
import pytest
import yaml

from digdown import access, checks
from digdown.api import create_app
from digdown.config import Config, ConfigError, load_config, save_config, unknown_keys
from digdown.events import EventBus
from digdown.llm import LLMResponse, ToolCall
from digdown.models import Incident, IncidentState, Signal, SignalType
from digdown.store import Store


def _client(app, host="localhost:8088"):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url=f"http://{host}")


def _app(tmp_path, config=None, **kw):
    store = Store(str(tmp_path / "t.db"))
    return store, create_app(store, EventBus(), config or Config(), **kw)


def _proposed(store):
    sig = Signal(source="docker", type=SignalType.CRASH, target="web")
    return store.add_incident(Incident(source="docker", target="web", signal=sig,
                                       dedup_key=sig.dedup_key,
                                       state=IncidentState.PROPOSED))


# ---- config ----

def test_default_bind_is_this_computer_only():
    assert Config().host == "127.0.0.1"


def test_bad_yaml_reports_a_readable_error(tmp_path):
    p = tmp_path / "digdown.yml"
    p.write_text("mode: [unclosed")
    with pytest.raises(ConfigError, match="isn't valid YAML"):
        load_config(str(p))


def test_bad_mode_names_the_setting(tmp_path):
    p = tmp_path / "digdown.yml"
    p.write_text("mode: yolo\n")
    with pytest.raises(ConfigError, match="mode: .*observe, propose, execute"):
        load_config(str(p))


def test_environment_overrides_the_file(tmp_path, monkeypatch):
    p = tmp_path / "digdown.yml"
    p.write_text("port: 1234\n")
    monkeypatch.setenv("DIGDOWN_PORT", "9999")
    assert load_config(str(p)).port == 9999


def test_unknown_keys_are_found(tmp_path):
    p = tmp_path / "digdown.yml"
    p.write_text("mode: observe\npol_interval_seconds: 5\n")
    assert unknown_keys(str(p)) == ["pol_interval_seconds"]


def test_save_config_is_owner_only_and_keeps_a_backup(tmp_path):
    p = tmp_path / "digdown.yml"
    p.write_text("# my notes\nport: 1234\n")
    save_config(str(p), {"llm_api_key": "sk-secret"})
    assert yaml.safe_load(p.read_text()) == {"port": 1234, "llm_api_key": "sk-secret"}
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    backup = tmp_path / "digdown.yml.bak"
    assert "# my notes" in backup.read_text()
    assert stat.S_IMODE(os.stat(backup).st_mode) == 0o600


def test_save_config_refuses_values_that_would_not_load(tmp_path):
    p = tmp_path / "digdown.yml"
    with pytest.raises(ConfigError):
        save_config(str(p), {"mode": "yolo"})
    assert not p.exists()


# ---- access ----

@pytest.mark.anyio
async def test_cross_site_post_cannot_approve_a_fix(tmp_path):
    store, app = _app(tmp_path)
    inc = _proposed(store)
    async with _client(app) as c:
        r = await c.post(f"/api/incidents/{inc.id}/approve", json={},
                         headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert store.get_incident(inc.id).state == IncidentState.PROPOSED


@pytest.mark.anyio
async def test_same_origin_post_is_allowed(tmp_path):
    store, app = _app(tmp_path)
    inc = _proposed(store)
    async with _client(app) as c:
        r = await c.post(f"/api/incidents/{inc.id}/approve", json={},
                         headers={"Origin": "http://localhost:8088"})
    assert r.status_code == 200


@pytest.mark.anyio
async def test_no_token_rejects_other_hostnames(tmp_path):
    _, app = _app(tmp_path, local_only=True)
    async with _client(app, host="evil.example") as c:
        r = await c.get("/api/incidents")
    assert r.status_code == 403
    assert "auth_token" in r.json()["detail"]
    async with _client(app) as c:
        assert (await c.get("/api/incidents")).status_code == 200


@pytest.mark.anyio
async def test_token_protects_reads_but_not_health(tmp_path):
    _, app = _app(tmp_path, Config(auth_token="a-long-enough-token"))
    async with _client(app) as c:
        assert (await c.get("/api/incidents")).status_code == 401
        assert (await c.get("/api/health")).status_code == 200
        ok = await c.get("/api/incidents",
                         headers={"Authorization": "Bearer a-long-enough-token"})
    assert ok.status_code == 200


@pytest.mark.anyio
async def test_login_sets_a_cookie_that_works_for_writes(tmp_path):
    store, app = _app(tmp_path, Config(auth_token="a-long-enough-token"))
    inc = _proposed(store)
    async with _client(app) as c:
        before = (await c.get("/api/session")).json()
        assert before == {"auth_required": True, "authenticated": False}
        assert (await c.post("/api/session", json={"token": "wrong"})).status_code == 401
        login = await c.post("/api/session", json={"token": "a-long-enough-token"})
        assert login.status_code == 200
        assert "a-long-enough-token" not in login.headers["set-cookie"]
        assert "httponly" in login.headers["set-cookie"].lower()
        assert "samesite=strict" in login.headers["set-cookie"].lower()
        assert (await c.get("/api/session")).json()["authenticated"] is True
        assert (await c.post(f"/api/incidents/{inc.id}/approve", json={})).status_code == 200
        await c.delete("/api/session")
        assert (await c.get("/api/incidents")).status_code == 401


def test_exposed_process_gets_a_generated_token_that_persists(tmp_path):
    cfg = Config(host="0.0.0.0", db_path=str(tmp_path / "o.db"))
    token = access.ensure_token(cfg)
    assert token and cfg.auth_token == token and len(token) >= 24
    path = tmp_path / "digdown.token"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    again = Config(host="0.0.0.0", db_path=str(tmp_path / "o.db"))
    assert access.ensure_token(again) == token


def test_local_processes_need_no_token(tmp_path):
    assert access.ensure_token(Config(db_path=str(tmp_path / "o.db"))) is None
    published_locally = Config(host="0.0.0.0", db_path=str(tmp_path / "o.db"))
    assert access.ensure_token(published_locally, port_is_local=True) is None
    assert not (tmp_path / "digdown.token").exists()


# ---- status: never claim to be watching when it isn't ----

@pytest.mark.anyio
async def test_status_reports_docker_down(tmp_path):
    _, app = _app(tmp_path, status_info={"first_run": False, "docker_error": "no socket"})
    async with _client(app) as c:
        s = (await c.get("/api/status")).json()
    assert s["watching"] is False
    assert s["docker"]["ok"] is False
    assert s["docker"]["error"] == "no socket"
    assert s["docker"]["fix"]


@pytest.mark.anyio
async def test_status_reports_ai_off(tmp_path):
    _, app = _app(tmp_path, Config(agent_enabled=False))
    async with _client(app) as c:
        s = (await c.get("/api/status")).json()
    assert s["ai"]["enabled"] is False
    assert "llm_api_key" not in str(s)


# ---- setup ----

def _setup_app(tmp_path, restarts=None, **cfg):
    path = tmp_path / "digdown.yml"
    calls = restarts if restarts is not None else []
    store, app = _app(tmp_path, Config(**cfg), config_file=str(path),
                      status_info={"first_run": True}, restart=lambda: calls.append(1))
    return path, app


@pytest.mark.anyio
async def test_setup_writes_config_and_restarts(tmp_path):
    restarts = []
    path, app = _setup_app(tmp_path, restarts)
    async with _client(app) as c:
        r = await c.post("/api/setup", json={
            "project_name": ["shop"], "agent_enabled": True, "llm_provider": "openai",
            "llm_base_url": "http://localhost:11434/v1", "llm_model": "qwen2.5:14b",
            "mode": "observe"})
    assert r.status_code == 200
    saved = yaml.safe_load(path.read_text())
    assert saved["project_name"] == ["shop"]
    assert saved["llm_model"] == "qwen2.5:14b"
    assert restarts == [1]


@pytest.mark.anyio
async def test_setup_without_ai_is_detection_only(tmp_path):
    path, app = _setup_app(tmp_path)
    async with _client(app) as c:
        r = await c.post("/api/setup", json={"agent_enabled": False, "mode": "observe"})
    assert r.status_code == 200
    assert yaml.safe_load(path.read_text())["agent_enabled"] is False


@pytest.mark.anyio
async def test_setup_ignores_fields_outside_the_form(tmp_path):
    path, app = _setup_app(tmp_path)
    async with _client(app) as c:
        await c.post("/api/setup", json={"agent_enabled": False, "mode": "observe",
                                         "host": "0.0.0.0", "auth_token": "x",
                                         "host_introspection": True})
    saved = yaml.safe_load(path.read_text())
    assert not {"host", "auth_token", "host_introspection"} & set(saved)


@pytest.mark.anyio
async def test_setup_rejects_a_bad_mode(tmp_path):
    restarts = []
    path, app = _setup_app(tmp_path, restarts)
    async with _client(app) as c:
        r = await c.post("/api/setup", json={"mode": "yolo"})
    assert r.status_code == 422
    assert not path.exists() and restarts == []


@pytest.mark.anyio
async def test_setup_requires_the_token_when_one_is_set(tmp_path):
    path, app = _setup_app(tmp_path, auth_token="a-long-enough-token")
    async with _client(app) as c:
        r = await c.post("/api/setup", json={"mode": "observe"})
    assert r.status_code == 401
    assert not path.exists()


@pytest.mark.anyio
async def test_setup_is_refused_in_the_demo(tmp_path):
    store, app = _app(tmp_path, demo=True, config_file=str(tmp_path / "digdown.yml"))
    async with _client(app) as c:
        r = await c.post("/api/setup", json={"mode": "observe"})
    assert r.status_code == 409


@pytest.mark.anyio
async def test_setup_options_never_return_secrets(tmp_path):
    _, app = _app(tmp_path, Config(llm_api_key="sk-secret"),
                  config_file=str(tmp_path / "digdown.yml"),
                  docker_factory=lambda: (_ for _ in ()).throw(RuntimeError("down")))
    async with _client(app) as c:
        r = await c.get("/api/setup")
    assert r.status_code == 200
    assert "sk-secret" not in r.text
    assert r.json()["docker"]["status"] == "fail"
    assert r.json()["stacks"] == []


# ---- AI test ----

class _Llm:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc = resp, exc

    def __call__(self, provider, **kw):
        return self

    def chat(self, messages, tools=None):
        if self.exc:
            raise self.exc
        return self.resp


def test_ai_check_passes_when_the_model_calls_the_tool():
    llm = _Llm(LLMResponse(tool_calls=[ToolCall(id="1", name="ping", arguments={})]))
    assert checks.test_ai("openai", model="m", llm_factory=llm).status == checks.OK


def test_ai_check_fails_a_model_that_cannot_call_tools():
    r = checks.test_ai("openai", model="m", llm_factory=_Llm(LLMResponse(content="hi")))
    assert r.status == checks.FAIL
    assert "tool" in r.fix.lower()


def test_ai_check_explains_a_rejected_key():
    e = Exception("bad")
    e.status_code = 401
    r = checks.test_ai("openai", model="m", llm_factory=_Llm(exc=e))
    assert r.status == checks.FAIL and "key" in r.detail.lower()


def test_ai_check_explains_an_unreachable_service():
    r = checks.test_ai("openai", model="m", base_url="http://x/v1",
                       llm_factory=_Llm(exc=Exception("Connection error.")))
    assert r.status == checks.FAIL and "http://x/v1" in r.detail


def test_ollama_models_lists_names_and_tolerates_absence():
    fetched = []
    def fetch(url):
        fetched.append(url)
        return b'{"models":[{"name":"b"},{"name":"a"}]}'
    assert checks.ollama_models("http://localhost:11434/v1", fetch) == ["a", "b"]
    assert fetched == ["http://localhost:11434/api/tags"]
    def down(url):
        raise OSError("refused")
    assert checks.ollama_models("http://localhost:11434/v1", down) == []


# ---- doctor ----

def test_doctor_reports_docker_down_with_a_fix(tmp_path):
    def down():
        raise RuntimeError("no socket")
    results = {c.name: c for c in checks.run_all(str(tmp_path / "none.yml"),
                                                 client_factory=down)}
    assert results["Docker"].status == checks.FAIL
    assert results["Docker"].fix
    assert results["Settings file"].status == checks.WARN


def test_doctor_reports_a_broken_config_file(tmp_path):
    p = tmp_path / "digdown.yml"
    p.write_text("mode: yolo\n")
    results = {c.name: c for c in checks.run_all(str(p), client_factory=lambda: _NoDocker())}
    assert results["Settings file"].status == checks.FAIL


class _NoDocker:
    class containers:
        @staticmethod
        def list(all=True):
            return []


def test_network_check_flags_exposure_without_a_token():
    r = checks.check_network(Config(host="0.0.0.0"))
    assert r.status == checks.WARN


# ---- dashboard is served by the backend ----

@pytest.fixture
def dashboard(tmp_path):
    d = tmp_path / "static"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<html>dashboard</html>")
    (d / "assets" / "a.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("nope")
    return d


@pytest.mark.anyio
async def test_serves_the_dashboard_and_falls_back_to_it(tmp_path, dashboard):
    _, app = _app(tmp_path, static_dir=dashboard)
    async with _client(app) as c:
        root = await c.get("/")
        asset = await c.get("/assets/a.js")
        deep = await c.get("/containers/web")
    assert root.status_code == 200 and "dashboard" in root.text
    assert "frame-ancestors 'none'" in root.headers["content-security-policy"]
    assert asset.text == "console.log(1)"
    assert "immutable" in asset.headers["cache-control"]
    assert "dashboard" in deep.text


@pytest.mark.anyio
async def test_static_route_cannot_escape_its_folder(tmp_path, dashboard):
    _, app = _app(tmp_path, static_dir=dashboard)
    async with _client(app) as c:
        r = await c.get("/..%2fsecret.txt")
    assert "nope" not in r.text


@pytest.mark.anyio
async def test_unknown_api_path_is_a_404_not_the_dashboard(tmp_path, dashboard):
    _, app = _app(tmp_path, static_dir=dashboard)
    async with _client(app) as c:
        assert (await c.get("/api/nope")).status_code == 404


@pytest.mark.anyio
async def test_missing_dashboard_explains_itself(tmp_path):
    _, app = _app(tmp_path, static_dir=tmp_path / "absent")
    async with _client(app) as c:
        r = await c.get("/")
    assert r.status_code == 200
    assert "build-dashboard" in r.text


# ---- a verified fix must show as fixed, not "cleared" ----

@pytest.mark.anyio
async def test_list_marks_incidents_whose_fix_was_verified(tmp_path):
    from digdown.executor_models import ExecResult, Execution
    store, app = _app(tmp_path)
    fixed = _proposed(store)
    store.update_state(fixed.id, IncidentState.RESOLVED)
    store.add_execution(Execution(incident_id=fixed.id, result=ExecResult.RESOLVED,
                                  verified=True, rolled_back=False, steps=[], snapshot={},
                                  detail="ok"))
    async with _client(app) as c:
        rows = (await c.get("/api/incidents")).json()
    assert rows[0]["execution"]["verified"] is True


# ---- an approval in suggest-only mode must not get stuck ----

@pytest.mark.anyio
async def test_idle_approval_can_be_dismissed_only_outside_execute_mode(tmp_path):
    store, app = _app(tmp_path, Config(mode="propose"))
    inc = _proposed(store)
    store.update_state(inc.id, IncidentState.APPROVED)
    async with _client(app) as c:
        assert (await c.post(f"/api/incidents/{inc.id}/dismiss")).status_code == 200
    store, app = _app(tmp_path, Config(mode="execute"))
    running = _proposed(store)
    store.update_state(running.id, IncidentState.APPROVED)
    async with _client(app) as c:
        assert (await c.post(f"/api/incidents/{running.id}/dismiss")).status_code == 409


# ---- a saved key must only go to the service it was saved for ----

@pytest.mark.anyio
async def test_blank_key_reuses_the_saved_one_only_for_the_same_service(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(checks, "test_ai", lambda provider, **kw: (
        seen.append(kw["api_key"]), checks.Check("AI model", checks.OK, "ok"))[1])
    cfg = Config(llm_base_url="https://saved.example/v1", llm_api_key="sk-saved")
    _, app = _app(tmp_path, cfg, config_file=str(tmp_path / "digdown.yml"))
    async with _client(app) as c:
        await c.post("/api/setup/test-ai", json={
            "llm_provider": "openai", "llm_base_url": "https://saved.example/v1", "llm_model": "m"})
        await c.post("/api/setup/test-ai", json={
            "llm_provider": "openai", "llm_base_url": "https://evil.example/v1", "llm_model": "m"})
    assert seen == ["sk-saved", ""]


# ---- the choice made in setup is the one that takes effect ----

@pytest.mark.anyio
async def test_setup_mode_replaces_an_earlier_settings_change(tmp_path):
    path = tmp_path / "digdown.yml"
    store, app = _app(tmp_path, config_file=str(path), restart=lambda: None)
    async with _client(app) as c:
        await c.patch("/api/config", json={"key": "mode", "value": "execute"})
        assert store.list_settings()["mode"] == "execute"
        await c.post("/api/setup", json={"mode": "observe"})
    assert "mode" not in store.list_settings()


# ---- started before Docker: picks it up without anyone restarting it ----

def test_restarts_itself_when_docker_appears(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setattr(asyncio, "sleep", _no_sleep(asyncio.sleep))
    restarts, answers = [], iter([False, False, True])
    store = Store(str(tmp_path / "t.db"))
    app = create_app(store, EventBus(), Config(), status_info={"docker_error": "down"},
                     restart=lambda: restarts.append(1), docker_ready=lambda: next(answers))
    from starlette.testclient import TestClient
    with TestClient(app, base_url="http://localhost"):
        for _ in range(100):
            if restarts:
                break
            import time
            time.sleep(0.02)
    assert restarts == [1]


def _no_sleep(real):
    async def sleep(_seconds):
        await real(0)
    return sleep


# ---- a saved mode from an older copy, or a restart mid-task, must not hang ----

def _with_state(store, state, target="web"):
    sig = Signal(source="docker", type=SignalType.CRASH, target=target)
    return store.add_incident(Incident(source="docker", target=target, signal=sig,
                                       dedup_key=f"k-{target}", state=state))


def test_recovery_after_a_restart_mid_task(tmp_path):
    from digdown.agent_models import Diagnosis
    from digdown.pipeline import recover_interrupted
    store = Store(str(tmp_path / "t.db"))
    fresh = _with_state(store, IncidentState.INVESTIGATING, "a")
    known = _with_state(store, IncidentState.INVESTIGATING, "b")
    store.add_diagnosis(Diagnosis(incident_id=known.id, summary="s", root_cause="r"))
    queued = _with_state(store, IncidentState.TRIAGED, "c")
    applying = _with_state(store, IncidentState.EXECUTING, "d")
    checking = _with_state(store, IncidentState.VERIFYING, "e")
    approved = _with_state(store, IncidentState.APPROVED, "f")
    recover_interrupted(store, Config(mode="execute"))
    state = lambda i: store.get_incident(i.id).state  # noqa: E731
    assert state(fresh) == IncidentState.DETECTED      # will be diagnosed again
    assert state(queued) == IncidentState.DETECTED
    assert state(known) == IncidentState.DETECTED      # still needs a fix planned
    assert state(approved) == IncidentState.PROPOSED   # never ran, so ask again
    for interrupted in (applying, checking):           # may be half applied: say so
        assert state(interrupted) == IncidentState.FAILED
        assert "restarted" in store.get_incident(interrupted.id).failure_reason


def test_recovery_leaves_an_idle_approval_alone_when_only_suggesting(tmp_path):
    from digdown.pipeline import recover_interrupted
    store = Store(str(tmp_path / "t.db"))
    approved = _with_state(store, IncidentState.APPROVED)
    recover_interrupted(store, Config(mode="propose"))
    assert store.get_incident(approved.id).state == IncidentState.APPROVED


# ---- never offer a fix that can't run here ----

def test_fixes_needing_the_docker_command_are_unavailable_without_it(tmp_path, monkeypatch):
    from digdown import allowlist
    from digdown.plan_models import ActionType
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n")
    monkeypatch.setattr(allowlist.shutil, "which", lambda _: None)
    needs_cli = {ActionType.RECREATE, ActionType.SET_ENV_VAR}
    assert allowlist.unavailable_actions(str(compose)) == needs_cli
    monkeypatch.setattr(allowlist.shutil, "which", lambda _: "/usr/bin/docker")
    assert allowlist.unavailable_actions(str(compose)) == set()
    assert allowlist.unavailable_actions(str(tmp_path / "missing.yml")) == needs_cli


# ---- nothing is watched until the person has said what to watch ----

def test_first_run_watches_nothing_until_setup_is_finished(tmp_path, monkeypatch):
    from digdown import main as main_module
    built = []

    class Client:
        def ping(self):
            return True

    monkeypatch.setattr(main_module.docker, "from_env", lambda **kw: Client())
    monkeypatch.setattr(main_module, "Watcher", lambda **kw: built.append(kw))
    monkeypatch.setenv("DIGDOWN_DB_PATH", str(tmp_path / "o.db"))
    app, _ = main_module.build_app(str(tmp_path / "digdown.yml"))
    assert app.state.first_run is True
    assert built == []  # otherwise it would record, then diagnose, what was never chosen

    (tmp_path / "digdown.yml").write_text("agent_enabled: false\n")
    main_module.build_app(str(tmp_path / "digdown.yml"))
    assert len(built) == 1


@pytest.mark.anyio
async def test_first_run_is_not_reported_as_docker_trouble(tmp_path):
    _, app = _app(tmp_path, status_info={"first_run": True})
    async with _client(app) as c:
        s = (await c.get("/api/status")).json()
    assert s["docker"]["ok"] is True
    assert s["watching"] is False


def test_does_not_restart_in_a_loop_while_waiting_for_setup(tmp_path, monkeypatch):
    from digdown import main as main_module

    class Client:
        def ping(self):
            return True

    seen = {}
    monkeypatch.setattr(main_module.docker, "from_env", lambda **kw: Client())
    monkeypatch.setattr(main_module, "create_app",
                        lambda *a, **kw: seen.update(kw) or type("A", (), {"state": type("S", (), {})()})())
    monkeypatch.setenv("DIGDOWN_DB_PATH", str(tmp_path / "o.db"))
    main_module.build_app(str(tmp_path / "digdown.yml"))
    assert seen["docker_ready"] is None  # Docker is fine: nothing to wait for


# ---- "everything" means everything, and recreate can't hit the wrong stack ----

def test_choosing_everything_is_not_narrowed_by_a_compose_file_nearby(tmp_path):
    from digdown.discovery import Project
    from digdown.main import _watch_scope
    nearby = Project(name="digdown", services=["digdown"])
    p = tmp_path / "digdown.yml"
    p.write_text("project_name: []\n")
    assert _watch_scope(load_config(str(p)), nearby) == []
    p.write_text('project_name: ""\n')
    assert _watch_scope(load_config(str(p)), nearby) == []
    p.write_text("project_name: [shop]\n")
    assert _watch_scope(load_config(str(p)), nearby) == ["shop"]
    p.write_text("mode: observe\n")  # never said: stays scoped to its own compose file
    assert _watch_scope(load_config(str(p)), nearby) == ["digdown"]


# ---- the containers page must not hammer Docker on every request ----

class _CountingSource:
    def __init__(self):
        self.calls = 0

    def list_states(self):
        self.calls += 1
        from digdown.models import ContainerState
        return [ContainerState(id="1", name="fresh", status="running")]


class _Watcher:
    probes = []
    stats_ring = None
    host_ring = None
    host_vitals = None

    def __init__(self, last_states):
        self.source = _CountingSource()
        self.last_states = last_states


@pytest.mark.anyio
async def test_services_uses_the_latest_check_instead_of_asking_docker_again(tmp_path):
    from digdown.models import ContainerState
    watcher = _Watcher([ContainerState(id="1", name="from-last-check", status="running")])
    _, app = _app(tmp_path, watcher=watcher)
    async with _client(app) as c:
        for _ in range(3):
            body = (await c.get("/api/services")).json()
    assert [s["name"] for s in body["services"]] == ["from-last-check"]
    assert watcher.source.calls == 0


@pytest.mark.anyio
async def test_services_asks_docker_before_the_first_check_has_finished(tmp_path):
    watcher = _Watcher(None)
    _, app = _app(tmp_path, watcher=watcher)
    async with _client(app) as c:
        body = (await c.get("/api/services")).json()
    assert [s["name"] for s in body["services"]] == ["fresh"]
    assert watcher.source.calls == 1


def test_watcher_keeps_the_latest_check(store, bus, config):
    from digdown.models import ContainerState
    from digdown.watcher import Watcher
    from tests.conftest import FakeSource
    state = ContainerState(id="1", name="web", status="running")
    w = Watcher(source=FakeSource([state]), store=store, bus=bus, config=config)
    assert w.last_states is None
    w.poll_once()
    assert [s.name for s in w.last_states] == ["web"]


def test_recovery_in_explain_only_keeps_a_cause_already_found(tmp_path):
    from digdown.agent_models import Diagnosis
    from digdown.pipeline import recover_interrupted
    store = Store(str(tmp_path / "t.db"))
    known = _with_state(store, IncidentState.INVESTIGATING, "a")
    store.add_diagnosis(Diagnosis(incident_id=known.id, summary="s", root_cause="r"))
    fresh = _with_state(store, IncidentState.INVESTIGATING, "b")
    recover_interrupted(store, Config(mode="observe"))
    assert store.get_incident(known.id).state == IncidentState.DIAGNOSED
    assert store.get_incident(fresh.id).state == IncidentState.DETECTED


def test_network_check_trusts_a_port_published_only_to_this_computer(monkeypatch):
    monkeypatch.setenv("DIGDOWN_PORT_IS_LOCAL", "1")
    assert checks.check_network(Config(host="0.0.0.0")).status == checks.OK


# ---- auto heal and notifications through the API ----

def _heal_app(tmp_path, **config):
    return _app(tmp_path, Config(**config), config_file=str(tmp_path / "digdown.yml"))


@pytest.mark.anyio
async def test_turning_auto_heal_on_needs_the_mode_that_allows_fixes(tmp_path):
    _, app = _heal_app(tmp_path, mode="propose")
    async with _client(app) as c:
        r = await c.patch("/api/config", json={"key": "auto_heal_stacks", "value": ["shop"]})
    assert r.status_code == 409 and "Fix after I approve" in r.json()["detail"]


@pytest.mark.anyio
async def test_auto_heal_can_be_turned_on_and_shows_in_status(tmp_path):
    store, app = _heal_app(tmp_path, mode="execute")
    async with _client(app) as c:
        r = await c.patch("/api/config", json={"key": "auto_heal_stacks", "value": ["shop"]})
        assert r.status_code == 200
        status = (await c.get("/api/status")).json()
    assert status["auto_heal"]["stacks"] == ["shop"]
    assert store.list_settings()["auto_heal_stacks"] == ["shop"]


@pytest.mark.anyio
async def test_auto_heal_can_always_be_turned_off(tmp_path):
    _, app = _heal_app(tmp_path, mode="propose", auto_heal_stacks=["shop"])
    async with _client(app) as c:
        r = await c.patch("/api/config", json={"key": "auto_heal_stacks", "value": []})
    assert r.status_code == 200


def test_a_saved_auto_heal_choice_survives_a_restart():
    from digdown.config import apply_overrides
    config = Config()
    apply_overrides(config, {"auto_heal_stacks": ["shop"], "auto_heal_max_attempts": 2})
    assert config.auto_heal_stacks == ["shop"] and config.auto_heal_max_attempts == 2


@pytest.mark.anyio
async def test_incident_list_marks_what_auto_heal_fixed(tmp_path):
    from digdown.plan_models import Approval, Plan
    store, app = _heal_app(tmp_path)
    incident = _proposed(store)
    store.add_plan(Plan(incident_id=incident.id, summary="Restarted web"))
    store.add_approval(Approval(incident_id=incident.id, decision="approve", identity="auto-heal"))
    async with _client(app) as c:
        row = (await c.get("/api/incidents")).json()[0]
    assert row["auto_healed"] is True and row["heal_summary"] == "Restarted web"


@pytest.mark.anyio
async def test_incident_list_does_not_mark_a_fix_you_approved(tmp_path):
    from digdown.plan_models import Approval
    store, app = _heal_app(tmp_path)
    incident = _proposed(store)
    store.add_approval(Approval(incident_id=incident.id, decision="approve"))
    async with _client(app) as c:
        assert "auto_healed" not in (await c.get("/api/incidents")).json()[0]


@pytest.mark.anyio
async def test_saving_an_alert_address_keeps_it_secret(tmp_path):
    from digdown.config import load_config
    _, app = _heal_app(tmp_path)
    url = "https://ntfy.sh/private-topic"
    async with _client(app) as c:
        assert (await c.put("/api/notifications", json={"url": url})).json() == {"set": True}
        assert (await c.get("/api/notifications")).json() == {"set": True}
        assert url not in (await c.get("/api/config")).text
    assert load_config(str(tmp_path / "digdown.yml")).notify_webhook_url == url


@pytest.mark.anyio
async def test_an_alert_address_must_be_a_web_address(tmp_path):
    _, app = _heal_app(tmp_path)
    async with _client(app) as c:
        r = await c.put("/api/notifications", json={"url": "file:///etc/passwd"})
    assert r.status_code == 422


@pytest.mark.anyio
async def test_saving_an_alert_address_takes_effect_straight_away(tmp_path):
    from digdown.notifier import Notifier

    class P:
        notifier = Notifier()
    store = Store(str(tmp_path / "t.db"))
    app = create_app(store, EventBus(), Config(), pipeline=P(), config_file=str(tmp_path / "o.yml"))
    async with _client(app) as c:
        await c.put("/api/notifications", json={"url": "https://ntfy.sh/x"})
        assert len(P.notifier.channels) == 1
        await c.put("/api/notifications", json={"url": ""})
    assert P.notifier.channels == []


@pytest.mark.anyio
async def test_a_test_alert_reports_failure_in_plain_words(tmp_path, monkeypatch):
    import digdown.api as api_module

    class Boom:
        def __init__(self, url):
            pass

        def __call__(self, payload):
            raise OSError("connection refused")
    monkeypatch.setattr(api_module, "WebhookChannel", Boom)
    _, app = _heal_app(tmp_path)
    async with _client(app) as c:
        r = await c.post("/api/notifications/test", json={"url": "https://ntfy.sh/x"})
    assert r.status_code == 200 and r.json()["ok"] is False
    assert "connection refused" in r.json()["detail"]


@pytest.mark.anyio
async def test_a_test_alert_is_sent(tmp_path, monkeypatch):
    import digdown.api as api_module
    sent = []

    class Fake:
        def __init__(self, url):
            self.url = url

        def __call__(self, payload):
            sent.append((self.url, payload))
    monkeypatch.setattr(api_module, "WebhookChannel", Fake)
    _, app = _heal_app(tmp_path)
    async with _client(app) as c:
        r = await c.post("/api/notifications/test", json={"url": "https://ntfy.sh/x"})
    assert r.json()["ok"] is True and sent[0][0] == "https://ntfy.sh/x"


@pytest.mark.anyio
async def test_the_demo_will_not_save_an_alert_address(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    app = create_app(store, EventBus(), Config(), demo=True, config_file=str(tmp_path / "o.yml"))
    async with _client(app) as c:
        r = await c.put("/api/notifications", json={"url": "https://ntfy.sh/x"})
    assert r.status_code == 409


# ---- choosing what to watch, and the config page ----

class _Stacks:
    class containers:
        @staticmethod
        def list(all=True):
            class C:
                def __init__(self, project):
                    self.labels = {"com.docker.compose.project": project} if project else {}
            return [C("photos"), C("photos"), C("wiki"), C("")]


def _apps_app(tmp_path, **config):
    restarts = []
    store = Store(str(tmp_path / "t.db"))
    app = create_app(store, EventBus(), Config(**config), config_file=str(tmp_path / "digdown.yml"),
                     docker_factory=lambda: _Stacks(), restart=lambda: restarts.append(1))
    return app, restarts


@pytest.mark.anyio
async def test_every_app_on_the_machine_is_listed_watched_or_not(tmp_path):
    from digdown.config import NO_STACK
    app, _ = _apps_app(tmp_path, project_name=["photos"], auto_heal_stacks=["photos"], mode="execute")
    async with _client(app) as c:
        data = (await c.get("/api/apps")).json()
    assert data["all"] is False
    assert {s["name"]: (s["containers"], s["watched"], s["auto_heal"]) for s in data["stacks"]} == {
        "photos": (2, True, True), "wiki": (1, False, False), NO_STACK: (1, False, False)}


@pytest.mark.anyio
async def test_watching_everything_is_the_default_list(tmp_path):
    app, _ = _apps_app(tmp_path, project_name=[])
    async with _client(app) as c:
        data = (await c.get("/api/apps")).json()
    assert data["all"] is True and all(s["watched"] for s in data["stacks"])


@pytest.mark.anyio
async def test_choosing_apps_saves_and_restarts(tmp_path):
    from digdown.config import load_config
    app, restarts = _apps_app(tmp_path)
    async with _client(app) as c:
        r = await c.put("/api/apps", json={"all": False, "stacks": ["wiki"]})
    assert r.status_code == 200 and restarts == [1]
    assert load_config(str(tmp_path / "digdown.yml")).project_name == ["wiki"]


@pytest.mark.anyio
async def test_choosing_everything_saves_an_empty_list(tmp_path):
    from digdown.config import load_config
    app, _ = _apps_app(tmp_path, project_name=["wiki"])
    async with _client(app) as c:
        await c.put("/api/apps", json={"all": True, "stacks": []})
    config = load_config(str(tmp_path / "digdown.yml"))
    assert config.project_name == [] and "project_name" in config.model_fields_set


@pytest.mark.anyio
async def test_watching_nothing_is_refused(tmp_path):
    app, restarts = _apps_app(tmp_path)
    async with _client(app) as c:
        r = await c.put("/api/apps", json={"all": False, "stacks": []})
    assert r.status_code == 422 and restarts == []


@pytest.mark.anyio
async def test_the_demo_cannot_change_what_it_watches(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    app = create_app(store, EventBus(), Config(), demo=True, config_file=str(tmp_path / "o.yml"))
    async with _client(app) as c:
        assert (await c.put("/api/apps", json={"all": True})).status_code == 409


@pytest.mark.anyio
async def test_a_startup_setting_can_be_changed_from_the_config_page(tmp_path):
    from digdown.config import load_config
    app, restarts = _apps_app(tmp_path)
    async with _client(app) as c:
        r = await c.put("/api/config/file", json={"key": "log_tail_lines", "value": 500})
    assert r.status_code == 200 and restarts == [1]
    assert load_config(str(tmp_path / "digdown.yml")).log_tail_lines == 500


@pytest.mark.anyio
@pytest.mark.parametrize("key", ["host", "port", "auth_token", "db_path", "llm_api_key", "notify_webhook_url",
                                 "compose_path", "env_path", "host_introspection"])
async def test_network_and_secret_settings_stay_off_the_config_page(tmp_path, key):
    app, restarts = _apps_app(tmp_path)
    async with _client(app) as c:
        r = await c.put("/api/config/file", json={"key": key, "value": "0.0.0.0"})
    assert r.status_code == 409 and restarts == []


@pytest.mark.anyio
async def test_a_bad_value_on_the_config_page_is_refused(tmp_path):
    app, restarts = _apps_app(tmp_path)
    async with _client(app) as c:
        r = await c.put("/api/config/file", json={"key": "log_tail_lines", "value": "lots"})
    assert r.status_code == 422 and restarts == []


@pytest.mark.anyio
async def test_the_config_view_marks_what_the_page_can_change(tmp_path):
    app, _ = _apps_app(tmp_path)
    async with _client(app) as c:
        fields = {f["key"]: f for f in (await c.get("/api/config")).json()["fields"]}
    assert fields["log_tail_lines"]["file_editable"] is True
    assert fields["host"]["file_editable"] is False


@pytest.mark.anyio
async def test_auto_heal_activity_is_available(tmp_path):
    store, app = _app(tmp_path)
    inc = _proposed(store)
    store.append_audit(inc.id, actor="auto-heal", event="auto_heal_skipped", payload={"reason": "Why not."})
    async with _client(app) as c:
        rows = (await c.get("/api/auto-heal/activity")).json()
    assert rows[0]["reason"] == "Why not." and rows[0]["target"] == "web"


# ---- auto heal from setup, and for every app ----

@pytest.mark.anyio
async def test_setup_can_turn_on_auto_heal_for_every_app(tmp_path):
    path, app = _setup_app(tmp_path)
    async with _client(app) as c:
        r = await c.post("/api/setup", json={"agent_enabled": True, "mode": "execute",
                                             "auto_heal_all": True})
    assert r.status_code == 200
    saved = yaml.safe_load(path.read_text())
    assert saved["mode"] == "execute" and saved["auto_heal_all"] is True


@pytest.mark.anyio
async def test_setup_choice_replaces_an_earlier_auto_heal_setting(tmp_path):
    path = tmp_path / "digdown.yml"
    store, app = _app(tmp_path, Config(mode="execute"), config_file=str(path),
                      status_info={"first_run": False}, restart=lambda: None)
    store.set_setting("auto_heal_all", True, identity="you")
    async with _client(app) as c:
        await c.post("/api/setup", json={"mode": "execute", "auto_heal_all": False})
    assert "auto_heal_all" not in store.list_settings()


@pytest.mark.anyio
async def test_setup_options_say_whether_every_app_is_looked_after(tmp_path):
    _, app = _app(tmp_path, Config(mode="execute", auto_heal_all=True),
                  config_file=str(tmp_path / "digdown.yml"),
                  docker_factory=lambda: (_ for _ in ()).throw(RuntimeError("down")))
    async with _client(app) as c:
        r = await c.get("/api/setup")
    assert r.json()["current"]["auto_heal_all"] is True


@pytest.mark.anyio
async def test_auto_heal_for_every_app_needs_fixes_allowed(tmp_path):
    store, app = _app(tmp_path, Config(mode="propose"))
    async with _client(app) as c:
        r = await c.patch("/api/config", json={"key": "auto_heal_all", "value": True})
    assert r.status_code == 409


@pytest.mark.anyio
async def test_status_says_when_auto_heal_covers_every_app(tmp_path):
    _, app = _app(tmp_path, Config(mode="execute", auto_heal_all=True), demo=True)
    async with _client(app) as c:
        status = (await c.get("/api/status")).json()
    assert status["auto_heal"]["all"] is True


# ---- website checks ----

def _checks_app(tmp_path, restarts=None, **cfg):
    path = tmp_path / "digdown.yml"
    calls = restarts if restarts is not None else []
    store, app = _app(tmp_path, Config(**cfg), config_file=str(path),
                      status_info={"first_run": False}, restart=lambda: calls.append(1))
    return path, app


@pytest.mark.anyio
async def test_website_checks_are_listed(tmp_path):
    path, app = _checks_app(tmp_path, probes={"http": [
        {"name": "photos", "url": "http://photos:2283/", "target": "photos-web"}]})
    async with _client(app) as c:
        r = await c.get("/api/checks")
    assert r.json() == [{"name": "photos", "url": "http://photos:2283/", "target": "photos-web"}]


@pytest.mark.anyio
async def test_website_checks_are_saved_and_keep_log_checks(tmp_path):
    restarts = []
    path, app = _checks_app(tmp_path, restarts,
                            probes={"logs": [{"target": "proxy", "name": "errors"}]})
    async with _client(app) as c:
        r = await c.put("/api/checks", json=[
            {"url": "http://wiki:3000/health", "target": "wiki-web"}])
    assert r.status_code == 200
    saved = yaml.safe_load(path.read_text())["probes"]
    assert saved["http"][0]["url"] == "http://wiki:3000/health"
    assert saved["http"][0]["target"] == "wiki-web"
    assert saved["http"][0]["name"]
    assert saved["logs"][0]["target"] == "proxy"
    assert restarts == [1]


@pytest.mark.anyio
async def test_two_checks_on_one_container_get_different_names(tmp_path):
    path, app = _checks_app(tmp_path)
    async with _client(app) as c:
        await c.put("/api/checks", json=[{"url": "http://wiki:3000/", "target": "wiki"},
                                         {"url": "http://wiki:3000/api", "target": "wiki"}])
    names = [h["name"] for h in yaml.safe_load(path.read_text())["probes"]["http"]]
    assert len(set(names)) == 2


@pytest.mark.parametrize("check", [
    {"url": "file:///etc/passwd", "target": "wiki"},
    {"url": "wiki:3000", "target": "wiki"},
    {"url": "http://wiki:3000/", "target": ""},
])
@pytest.mark.anyio
async def test_a_bad_website_check_is_refused(tmp_path, check):
    restarts = []
    path, app = _checks_app(tmp_path, restarts)
    async with _client(app) as c:
        r = await c.put("/api/checks", json=[check])
    assert r.status_code == 422
    assert not path.exists() and restarts == []


@pytest.mark.anyio
async def test_website_checks_need_the_token(tmp_path):
    path, app = _checks_app(tmp_path, auth_token="a-long-enough-token")
    async with _client(app) as c:
        r = await c.put("/api/checks", json=[])
    assert r.status_code == 401


@pytest.mark.anyio
async def test_website_checks_cannot_be_changed_in_the_demo(tmp_path):
    store, app = _app(tmp_path, demo=True, config_file=str(tmp_path / "digdown.yml"))
    async with _client(app) as c:
        r = await c.put("/api/checks", json=[])
    assert r.status_code == 409
