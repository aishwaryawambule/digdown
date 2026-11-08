"""Found reviewing Digdown before its first public release."""
import httpx
import pytest

from digdown import access
from digdown.allowlist import validate_action
from digdown.api import create_app
from digdown.config import Config
from digdown.events import EventBus
from digdown.executor import Executor
from digdown.models import ContainerState, Incident, IncidentState, Signal, SignalType
from digdown.plan_models import Action, ActionType, Approval, Plan
from digdown.redactor import Redactor, SecretRegistry
from digdown.store import Store
from digdown.tools import DiagnosticTools


def _client(app, host="localhost:8088"):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{host}")


# ---- the .env file must never be stored or sent anywhere ----

class _Writer:
    def __init__(self):
        self.restored = None

    def capture(self, target):
        return {"status": "running", "env_backup": "DB_PASSWORD=hunter2hunter2\n"}

    def apply(self, action):
        pass

    def rollback(self, action, snapshot):
        self.restored = snapshot.get("env_backup")


class _Verifier:
    def verify(self, incident):
        return False, "still down"


def _run_fix(store, bus, writer=None, action=ActionType.RESTART):
    writer = writer or _Writer()
    sig = Signal(source="docker", type=SignalType.CRASH, target="web")
    incident = store.add_incident(Incident(source="docker", target="web", signal=sig, dedup_key="k",
                                           state=IncidentState.APPROVED))
    store.add_plan(Plan(incident_id=incident.id, summary="s", kind="automated", critic2_supported=True,
                        actions=[Action(type=action, target="web",
                                        params={"key": "A", "value": "b"} if action == ActionType.SET_ENV_VAR else {})]))
    store.add_approval(Approval(incident_id=incident.id, decision="approve"))
    Executor(store, bus, Config(mode="execute"), writer, _Verifier(), lambda: {"web"}).execute(incident)
    return incident, writer


def test_the_env_file_is_not_saved_with_the_execution(store, bus):
    incident, _ = _run_fix(store, bus)
    saved = store.latest_execution_for(incident.id).snapshot
    assert "hunter2hunter2" not in str(saved) and "env_backup" not in str(saved)
    assert saved["web"]["status"] == "running"  # the rest of the record is kept


def test_the_env_file_is_not_in_the_audit_log_or_live_events(store, bus):
    incident, _ = _run_fix(store, bus)
    assert "hunter2hunter2" not in str(store.audit_for(incident.id))
    assert "hunter2hunter2" not in str(bus.events)


def test_undoing_a_fix_can_still_restore_the_env_file(store, bus):
    _, writer = _run_fix(store, bus, action=ActionType.SET_ENV_VAR)
    assert writer.restored == "DB_PASSWORD=hunter2hunter2\n"


@pytest.mark.anyio
async def test_an_old_database_never_returns_the_env_file(tmp_path):
    from digdown.executor_models import ExecResult, Execution
    store = Store(str(tmp_path / "t.db"))
    sig = Signal(source="docker", type=SignalType.CRASH, target="web")
    incident = store.add_incident(Incident(source="docker", target="web", signal=sig, dedup_key="k"))
    store.add_execution(Execution(incident_id=incident.id, result=ExecResult.FAILED, verified=False,
                                  rolled_back=False, steps=[], detail="old",
                                  snapshot={"web": {"status": "running", "env_backup": "DB_PASSWORD=hunter2hunter2"}}))
    app = create_app(store, EventBus(), Config())
    async with _client(app) as c:
        assert "hunter2hunter2" not in (await c.get(f"/api/incidents/{incident.id}")).text


# ---- reaching Digdown from another container ----

@pytest.mark.anyio
async def test_another_container_cannot_claim_to_be_this_computer(tmp_path):
    # host.docker.internal is how other containers reach the host.
    app = create_app(Store(str(tmp_path / "t.db")), EventBus(), Config(), local_only=True)
    async with _client(app, host="host.docker.internal:8088") as c:
        assert (await c.get("/api/status")).status_code == 403


def test_the_docker_install_requires_a_token():
    # With Docker's socket, a takeover is root on the machine, and a Host header
    # proves nothing to a container that can reach the port.
    from pathlib import Path
    compose = Path(__file__).resolve().parent.parent / "docker-compose.yml"
    assert "DIGDOWN_PORT_IS_LOCAL" not in compose.read_text()


def test_a_published_port_gets_a_token_by_default(tmp_path):
    config = Config(host="0.0.0.0", db_path=str(tmp_path / "t.db"))
    assert access.ensure_token(config, port_is_local=False)
    assert config.auth_token


# ---- what reaches the AI ----

def test_a_password_on_a_container_command_line_is_masked():
    class Source:
        def inspect(self, name):
            return {"image": "redis", "command": "redis-server --requirepass hunter2hunter2", "env": {}}

    registry = SecretRegistry()
    registry.register("hunter2hunter2")
    tools = DiagnosticTools(Source(), None, redactor=Redactor(registry))
    assert "hunter2hunter2" not in tools.inspect_container("redis")


def test_a_password_flag_is_masked_even_if_nothing_declared_it():
    assert "hunter2hunter2" not in Redactor().redact("redis-server --requirepass hunter2hunter2")
    assert "hunter2hunter2" not in Redactor().redact("mysqld --password=hunter2hunter2")


# ---- what the AI can write ----

@pytest.mark.parametrize("key,value", [
    ("BAD\nINJECTED", "x"), ("HAS SPACE", "x"), ("A=B", "x"), ("OK", "line1\nINJECTED=1"), ("OK", "a\rb"),
])
def test_a_setting_cannot_smuggle_extra_lines_into_the_env_file(key, value):
    action = Action(type=ActionType.SET_ENV_VAR, target="web", params={"key": key, "value": value})
    assert validate_action(action, {"web"})


def test_a_normal_setting_is_allowed():
    action = Action(type=ActionType.SET_ENV_VAR, target="web", params={"key": "APP_API_KEY", "value": "abc 123"})
    assert validate_action(action, {"web"}) == []


# ---- the page itself ----

@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/", "/index.html", "/some/page"])
async def test_every_page_has_the_security_policy(tmp_path, path):
    (tmp_path / "index.html").write_text("<html></html>")
    app = create_app(Store(str(tmp_path / "t.db")), EventBus(), Config(), static_dir=tmp_path)
    async with _client(app) as c:
        r = await c.get(path)
    assert r.status_code == 200 and "frame-ancestors 'none'" in r.headers.get("content-security-policy", "")
