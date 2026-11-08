import asyncio
import threading

import httpx
import pytest
import uvicorn

from digdown.agent_models import Diagnosis
from digdown.api import create_app, run_diagnosis_safely
from digdown.config import Config
from digdown.events import EventBus
from digdown.executor_models import Execution, ExecResult, StepResult
from digdown.llm import FakeLLM
from digdown.models import (ContainerState, HealthStatus, Incident,
                                  IncidentState, Signal, SignalType)
from digdown.pipeline import Pipeline
from digdown.plan_models import Action, ActionType, Plan, RiskLevel
from digdown.store import Store
from digdown.tools import DiagnosticTools


def _make(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    bus = EventBus()
    app = create_app(store, bus, Config())
    return store, bus, app


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://test")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_health_reports_mode(tmp_path):
    _, _, app = _make(tmp_path)
    async with _client(app) as c:
        r = await c.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "mode": "observe"}


@pytest.mark.anyio
async def test_incidents_list_and_get(tmp_path):
    store, _, app = _make(tmp_path)
    sig = Signal(source="docker", type=SignalType.CRASH, target="web")
    inc = store.add_incident(Incident(source="docker", target="web", signal=sig,
                                      dedup_key=sig.dedup_key))
    async with _client(app) as c:
        listing = await c.get("/api/incidents")
        single = await c.get(f"/api/incidents/{inc.id}")
        missing = await c.get("/api/incidents/9999")
    assert listing.status_code == 200
    assert listing.json()[0]["target"] == "web"
    assert single.json()["signal"]["type"] == "crash"
    assert missing.status_code == 404


@pytest.mark.anyio
async def test_stream_delivers_published_event(tmp_path):
    """SSE test: run a real uvicorn server in a background thread so that
    httpx can stream responses without the buffering limitation of ASGITransport.
    """
    _, bus, app = _make(tmp_path)

    # Pick an ephemeral port.
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="error"))
    started = threading.Event()
    orig_startup = server.startup

    async def patched_startup(*a, **kw):
        await orig_startup(*a, **kw)
        started.set()

    server.startup = patched_startup

    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    started.wait(timeout=5)

    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as c:
            async with c.stream("GET", "/api/stream") as resp:
                assert resp.status_code == 200
                await asyncio.sleep(0.05)
                bus.publish({"type": "incident_created", "incident": {"id": 7}})
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        assert '"id": 7' in line
                        break
    finally:
        server.should_exit = True
        t.join(timeout=5)


class _FakeSource:
    def list_states(self):
        return []
    def get_logs(self, name, lines=200):
        return ""
    def inspect(self, name):
        return {}


@pytest.mark.anyio
async def test_run_diagnosis_safely_marks_failed(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    bus = EventBus()
    sig = Signal(source="docker", type=SignalType.OOM, target="web")
    inc = store.add_incident(Incident(source="docker", target="web", signal=sig,
                                      dedup_key=sig.dedup_key))
    # FakeLLM([]) raises "FakeLLM exhausted" the moment diagnosis calls the model.
    pipeline = Pipeline(store, bus, Config(), FakeLLM([]),
                        DiagnosticTools(_FakeSource(), None))

    await run_diagnosis_safely(pipeline, inc)

    saved = store.get_incident(inc.id)
    assert saved.state == IncidentState.DIAGNOSIS_FAILED
    assert saved.failure_reason == "Diagnosis failed"


@pytest.mark.anyio
async def test_incident_detail_includes_diagnosis_and_audit(tmp_path):
    store, _, app = _make(tmp_path)
    sig = Signal(source="docker", type=SignalType.OOM, target="web")
    inc = store.add_incident(Incident(source="docker", target="web", signal=sig,
                                      dedup_key=sig.dedup_key))
    store.add_diagnosis(Diagnosis(incident_id=inc.id, summary="OOM", root_cause="mem",
                                  evidence=["exit 137"], confidence=0.8, rounds=1,
                                  critic_supported=True, critic_reason="ok"))
    store.append_audit(inc.id, actor="diagnostician", event="diagnosed", payload={})
    async with _client(app) as c:
        r = await c.get(f"/api/incidents/{inc.id}")
    body = r.json()
    assert body["diagnosis"]["root_cause"] == "mem"
    assert any(a["event"] == "diagnosed" for a in body["audit"])


class _RecStore:
    def __init__(self):
        self.audits = []
    def append_audit(self, incident_id, actor, event, payload):
        self.audits.append((event, payload))


class _Inc:
    id = 1


@pytest.mark.anyio
async def test_run_diagnosis_safely_runs_pipeline(tmp_path):
    ran = []
    class P:
        store = _RecStore()
        def run(self, inc):
            ran.append(inc.id)
    await run_diagnosis_safely(P(), _Inc())
    assert ran == [1]


def _proposed(store, target="web"):
    sig = Signal(source="docker", type=SignalType.CRASH, target=target)
    inc = store.add_incident(Incident(source="docker", target=target, signal=sig,
                                      dedup_key=sig.dedup_key))
    store.update_state(inc.id, IncidentState.PROPOSED)
    store.add_plan(Plan(incident_id=inc.id, summary="restart it", kind="automated",
                        risk=RiskLevel.LOW,
                        actions=[Action(type=ActionType.RESTART, target=target)],
                        critic2_supported=True, critic2_reason="ok"))
    return inc


@pytest.mark.anyio
async def test_approve_transitions_and_records(tmp_path):
    store, _, app = _make(tmp_path)
    inc = _proposed(store)
    async with _client(app) as c:
        r = await c.post(f"/api/incidents/{inc.id}/approve",
                         json={"identity": "ops@x", "note": "go"})
    assert r.status_code == 200
    assert r.json()["state"] == "approved"
    assert store.get_incident(inc.id).state == IncidentState.APPROVED
    appr = store.approvals_for(inc.id)
    assert appr[0].decision == "approve" and appr[0].identity == "ops@x"


@pytest.mark.anyio
async def test_reject_transitions(tmp_path):
    store, _, app = _make(tmp_path)
    inc = _proposed(store)
    async with _client(app) as c:
        r = await c.post(f"/api/incidents/{inc.id}/reject", json={})
    assert r.status_code == 200
    assert store.get_incident(inc.id).state == IncidentState.REJECTED


@pytest.mark.anyio
async def test_approve_requires_proposed_state(tmp_path):
    store, _, app = _make(tmp_path)
    sig = Signal(source="docker", type=SignalType.CRASH, target="web")
    inc = store.add_incident(Incident(source="docker", target="web", signal=sig,
                                      dedup_key=sig.dedup_key))  # state=detected
    async with _client(app) as c:
        r = await c.post(f"/api/incidents/{inc.id}/approve", json={})
    assert r.status_code == 409


@pytest.mark.anyio
async def test_approve_missing_incident_404(tmp_path):
    _, _, app = _make(tmp_path)
    async with _client(app) as c:
        r = await c.post("/api/incidents/9999/approve", json={})
    assert r.status_code == 404


@pytest.mark.anyio
async def test_detail_includes_plan(tmp_path):
    store, _, app = _make(tmp_path)
    inc = _proposed(store)
    async with _client(app) as c:
        r = await c.get(f"/api/incidents/{inc.id}")
    body = r.json()
    assert body["plan"]["actions"][0]["type"] == "restart"
    assert body["plan"]["kind"] == "automated"


@pytest.mark.anyio
async def test_run_execution_safely_runs(tmp_path):
    from digdown.api import run_execution_safely
    ran = []
    class E:
        store = _RecStore()
        def execute(self, inc):
            ran.append(inc.id)
    await run_execution_safely(E(), _Inc())
    assert ran == [1]


@pytest.mark.anyio
async def test_run_execution_safely_swallows_and_audits(tmp_path):
    from digdown.api import run_execution_safely
    class E:
        store = _RecStore()
        def execute(self, inc):
            raise RuntimeError("docker down")
    e = E()
    await run_execution_safely(e, _Inc())
    assert e.store.audits[0][0] == "execute_failed"


@pytest.mark.anyio
async def test_detail_includes_execution(tmp_path):
    store, _, app = _make(tmp_path)
    inc = _proposed(store)
    store.add_execution(Execution(
        incident_id=inc.id, result=ExecResult.RESOLVED, verified=True,
        steps=[StepResult(type="restart", target="web", ok=True)],
        snapshot={"web": {"image": "web:1"}}, detail="stable"))
    async with _client(app) as c:
        r = await c.get(f"/api/incidents/{inc.id}")
    body = r.json()
    assert body["execution"]["result"] == "resolved"
    assert body["execution"]["steps"][0]["type"] == "restart"


def _make_auth(tmp_path, token):
    store = Store(str(tmp_path / "t.db"))
    bus = EventBus()
    app = create_app(store, bus, Config(auth_token=token))
    return store, app


@pytest.mark.anyio
async def test_approve_requires_token_when_configured(tmp_path):
    store, app = _make_auth(tmp_path, "s3cret")
    inc = _proposed(store)
    async with _client(app) as c:
        no_tok = await c.post(f"/api/incidents/{inc.id}/approve", json={})
        bad = await c.post(f"/api/incidents/{inc.id}/approve", json={},
                           headers={"Authorization": "Bearer wrong"})
        good = await c.post(f"/api/incidents/{inc.id}/approve", json={},
                            headers={"Authorization": "Bearer s3cret"})
    assert no_tok.status_code == 401
    assert bad.status_code == 401
    assert good.status_code == 200


@pytest.mark.anyio
async def test_x_digdown_token_header_accepted(tmp_path):
    store, app = _make_auth(tmp_path, "s3cret")
    inc = _proposed(store)
    async with _client(app) as c:
        r = await c.post(f"/api/incidents/{inc.id}/reject", json={},
                         headers={"X-Digdown-Token": "s3cret"})
    assert r.status_code == 200


class _FakeWatcher:
    class _Src:
        def list_states(self):
            return [ContainerState(id="1", name="web", service="web",
                                   status="running", health=HealthStatus.HEALTHY),
                    ContainerState(id="2", name="db", service="db",
                                   status="exited", exit_code=1)]
    source = _Src()


@pytest.mark.anyio
async def test_services_lists_watched_containers(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    app = create_app(store, EventBus(), Config(), watcher=_FakeWatcher())
    async with _client(app) as c:
        r = await c.get("/api/services")
    body = r.json()
    assert body["available"] is True
    assert {s["name"] for s in body["services"]} == {"web", "db"}


@pytest.mark.anyio
async def test_services_unavailable_without_watcher(tmp_path):
    _, _, app = _make(tmp_path)   # no watcher
    async with _client(app) as c:
        r = await c.get("/api/services")
    assert r.json() == {"available": False, "services": []}


class _NoopWatcher:
    source = None
    def poll_once(self):
        return []


@pytest.mark.anyio
async def test_startup_diagnoses_preexisting_detected_incidents(tmp_path):
    # An incident left at DETECTED while the agent was off (e.g. before it was
    # enabled) must be picked up when the agent comes up — the poll loop alone
    # only diagnoses freshly-opened incidents.
    store = Store(str(tmp_path / "t.db"))
    sig = Signal(source="docker", type=SignalType.CRASH, target="web")
    inc = store.add_incident(Incident(source="docker", target="web", signal=sig,
                                      dedup_key=sig.dedup_key,
                                      state=IncidentState.DETECTED))
    diagnosed = []

    class FakePipeline:
        def __init__(self, store):
            self.store = store
        def run(self, incident):
            diagnosed.append(incident.id)

    app = create_app(store, EventBus(), Config(agent_enabled=True),
                     watcher=_NoopWatcher(), pipeline=FakePipeline(store))
    async with app.router.lifespan_context(app):
        for _ in range(100):
            if diagnosed:
                break
            await asyncio.sleep(0.02)
    assert inc.id in diagnosed


# ---- Task L3: redacted logs endpoint ----

class _FakeTools:
    """A minimal stand-in for DiagnosticTools that supports redacted_logs."""
    def redacted_logs(self, target: str, lines: int = 200) -> str:
        return f"log line 1\nlog line 2 (no secrets here)\n"


class _FakePipelineWithTools:
    def __init__(self):
        self.tools = _FakeTools()


def _make_with_pipeline(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    bus = EventBus()
    pipeline = _FakePipelineWithTools()
    app = create_app(store, bus, Config(), pipeline=pipeline)
    return store, app


@pytest.mark.anyio
async def test_logs_endpoint_returns_redacted_logs(tmp_path):
    store, app = _make_with_pipeline(tmp_path)
    sig = Signal(source="docker", type=SignalType.CRASH, target="web")
    inc = store.add_incident(Incident(source="docker", target="web", signal=sig,
                                      dedup_key=sig.dedup_key))
    async with _client(app) as c:
        r = await c.get(f"/api/incidents/{inc.id}/logs?lines=20")
    assert r.status_code == 200
    body = r.json()
    assert body["target"] == "web"
    assert body["lines"] == 20
    assert "SECRET" not in body["logs"]
    assert body["logs"].strip() != ""


@pytest.mark.anyio
async def test_logs_endpoint_404_for_unknown_incident(tmp_path):
    _, app = _make_with_pipeline(tmp_path)
    async with _client(app) as c:
        r = await c.get("/api/incidents/99999/logs")
    assert r.status_code == 404


@pytest.mark.anyio
async def test_logs_endpoint_503_when_no_pipeline(tmp_path):
    store, _, app = _make(tmp_path)  # no pipeline
    sig = Signal(source="docker", type=SignalType.CRASH, target="web")
    inc = store.add_incident(Incident(source="docker", target="web", signal=sig,
                                      dedup_key=sig.dedup_key))
    async with _client(app) as c:
        r = await c.get(f"/api/incidents/{inc.id}/logs")
    assert r.status_code == 503


# ---- Task 2: per-service HTTP status on /api/services ----

class _FakeHttpProbe:
    name = "http"

    def http_status_by_target(self):
        return {"web": {"check": "home", "status": "ok", "code": 200,
                        "latency_ms": 12, "checked_at": "2026-06-19T00:00:00Z"}}


class _FakeWatcherWithHttp:
    class _Src:
        def list_states(self):
            return [ContainerState(id="1", name="web", service="web",
                                   status="running", health=HealthStatus.HEALTHY),
                    ContainerState(id="2", name="db", service="db",
                                   status="running")]
    source = _Src()
    probes = [_FakeHttpProbe()]


@pytest.mark.anyio
async def test_services_attaches_http_status(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    app = create_app(store, EventBus(), Config(), watcher=_FakeWatcherWithHttp())
    async with _client(app) as c:
        r = await c.get("/api/services")
    body = r.json()
    assert body["available"] is True
    by = {s["name"]: s for s in body["services"]}
    assert by["web"]["http"]["status"] == "ok"
    assert by["web"]["http"]["code"] == 200
    assert by["db"]["http"] is None  # no check targets db


# ---- Mutes API ----

@pytest.mark.anyio
async def test_mutes_listing_includes_config_excludes(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    bus = EventBus()
    app = create_app(store, bus, Config(exclude_projects=["blog"]))
    async with _client(app) as c:
        r = await c.get("/api/mutes")
    assert r.status_code == 200
    body = r.json()
    assert body["mutes"] == []
    assert body["excluded_projects"] == ["blog"]  # config excludes are surfaced


@pytest.mark.anyio
async def test_add_mute_silences_open_incidents(tmp_path):
    store, _, app = _make(tmp_path)
    sig = Signal(source="docker", type=SignalType.CRASH, target="blog-web_app-1")
    inc = store.add_incident(Incident(source="docker", target="blog-web_app-1",
                                      signal=sig, dedup_key=sig.dedup_key))
    async with _client(app) as c:
        r = await c.post("/api/mutes", json={"pattern": "blog", "note": "down"})
        listing = await c.get("/api/mutes")
    assert r.status_code == 200
    assert r.json()["muted_incident_ids"] == [inc.id]
    assert store.get_incident(inc.id).state == IncidentState.MUTED
    assert [m["pattern"] for m in listing.json()["mutes"]] == ["blog"]


@pytest.mark.anyio
async def test_add_mute_requires_pattern(tmp_path):
    _, _, app = _make(tmp_path)
    async with _client(app) as c:
        r = await c.post("/api/mutes", json={"pattern": "  "})
    assert r.status_code == 400


@pytest.mark.anyio
async def test_delete_mute(tmp_path):
    store, _, app = _make(tmp_path)
    store.add_mute("blog")
    async with _client(app) as c:
        gone = await c.delete("/api/mutes/blog")
        again = await c.delete("/api/mutes/blog")
        listing = await c.get("/api/mutes")
    assert gone.json() == {"removed": True, "pattern": "blog"}
    assert again.json()["removed"] is False
    assert listing.json()["mutes"] == []


# ---- Task 2 (alert ingestion): POST /api/alerts ----

def _app_alerts(store, config=None):
    return create_app(store=store, bus=EventBus(), config=config or Config(),
                      pipeline=None, watcher=None)


def _firing(fp="fpA", target="shop-api"):
    return {"alerts": [{"status": "firing", "fingerprint": fp,
                        "labels": {"target": target, "alertname": "HighErrorRate"},
                        "annotations": {"summary": "error rate above 5% for 10 minutes"}}]}


@pytest.mark.anyio
async def test_alerts_auth_rejects_bad_token(tmp_path):
    store = Store(str(tmp_path / "c.db"))
    app = _app_alerts(store, Config(auth_token="secret"))
    async with _client(app) as c:
        r = await c.post("/api/alerts", json=_firing(), headers={"X-Digdown-Token": "wrong"})
    assert r.status_code == 401


@pytest.mark.anyio
async def test_alerts_malformed_is_noop(tmp_path):
    store = Store(str(tmp_path / "d.db"))
    app = _app_alerts(store)
    async with _client(app) as c:
        r = await c.post("/api/alerts", json={"not": "grafana"})
    assert r.status_code == 200 and r.json() == {"ingested": 0, "resolved": 0}


# ---- Task 2: DELETE /api/history ----

@pytest.mark.anyio
async def test_clear_history_deletes_terminal_only(tmp_path):
    store, bus, app = _make(tmp_path)
    active = store.add_incident(Incident(
        source="docker", target="active",
        signal=Signal(source="docker", type=SignalType.CRASH, target="active"),
        dedup_key="a"))
    store.update_state(active.id, IncidentState.INVESTIGATING)
    done = store.add_incident(Incident(
        source="docker", target="done",
        signal=Signal(source="docker", type=SignalType.CRASH, target="done"),
        dedup_key="d"))
    store.update_state(done.id, IncidentState.RESOLVED)

    async with _client(app) as c:
        r = await c.delete("/api/history")
        after = await c.get("/api/incidents")

    assert r.status_code == 200
    assert r.json()["count"] == 1
    ids = [i["id"] for i in after.json()]
    assert ids == [active.id]


@pytest.mark.anyio
async def test_clear_history_requires_token_when_configured(tmp_path):
    store, app = _make_auth(tmp_path, "s3cret")
    async with _client(app) as c:
        no_tok = await c.delete("/api/history")
        good = await c.delete("/api/history",
                              headers={"Authorization": "Bearer s3cret"})
    assert no_tok.status_code == 401
    assert good.status_code == 200


# ---- Task 4: POST /api/incidents/{id}/retry ----

@pytest.mark.anyio
async def test_retry_unknown_incident_404(tmp_path):
    _, _, app = _make(tmp_path)
    async with _client(app) as c:
        r = await c.post("/api/incidents/9999/retry")
    assert r.status_code == 404


@pytest.mark.anyio
async def test_retry_wrong_state_409(tmp_path):
    store, _, app = _make(tmp_path)
    sig = Signal(source="docker", type=SignalType.OOM, target="web")
    inc = store.add_incident(Incident(source="docker", target="web", signal=sig,
                                      dedup_key=sig.dedup_key))  # state=detected
    async with _client(app) as c:
        r = await c.post(f"/api/incidents/{inc.id}/retry")
    assert r.status_code == 409
    assert "not a failed diagnosis" in r.json()["detail"]


@pytest.mark.anyio
async def test_retry_when_diagnosis_disabled_409(tmp_path):
    store, _, app = _make(tmp_path)  # no pipeline -> diagnosis not enabled
    sig = Signal(source="docker", type=SignalType.OOM, target="web")
    inc = store.add_incident(Incident(source="docker", target="web", signal=sig,
                                      dedup_key=sig.dedup_key,
                                      state=IncidentState.DIAGNOSIS_FAILED))
    async with _client(app) as c:
        r = await c.post(f"/api/incidents/{inc.id}/retry")
    assert r.status_code == 409
    assert "not enabled" in r.json()["detail"]


@pytest.mark.anyio
async def test_retry_requeues_failed_incident(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    bus = EventBus()
    sig = Signal(source="docker", type=SignalType.OOM, target="web")
    inc = store.add_incident(Incident(source="docker", target="web", signal=sig,
                                      dedup_key=sig.dedup_key,
                                      state=IncidentState.DIAGNOSIS_FAILED))
    store.set_failure_reason(inc.id, "Rate-limited by the model API")
    pipeline = Pipeline(store, bus, Config(agent_enabled=True), FakeLLM([]),
                        DiagnosticTools(_FakeSource(), None))
    app = create_app(store, bus, Config(agent_enabled=True),
                     watcher=None, pipeline=pipeline)

    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="error"))
    started = threading.Event()
    orig_startup = server.startup

    async def patched_startup(*a, **kw):
        await orig_startup(*a, **kw)
        started.set()

    server.startup = patched_startup
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    started.wait(timeout=5)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as c:
            r = await c.post(f"/api/incidents/{inc.id}/retry")
    finally:
        server.should_exit = True
        t.join(timeout=5)

    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "detected"
    assert body["failure_reason"] == ""
    assert any(a["event"] == "retry_diagnosis" for a in store.audit_for(inc.id))


# ---- POST /api/incidents/{id}/dismiss ----

@pytest.mark.anyio
async def test_dismiss_unknown_incident_404(tmp_path):
    _, _, app = _make(tmp_path)
    async with _client(app) as c:
        r = await c.post("/api/incidents/9999/dismiss")
    assert r.status_code == 404


@pytest.mark.anyio
async def test_dismiss_terminal_state_409(tmp_path):
    store, _, app = _make(tmp_path)
    sig = Signal(source="docker", type=SignalType.CRASH, target="web")
    inc = store.add_incident(Incident(source="docker", target="web", signal=sig,
                                      dedup_key=sig.dedup_key,
                                      state=IncidentState.RESOLVED))
    async with _client(app) as c:
        r = await c.post(f"/api/incidents/{inc.id}/dismiss")
    assert r.status_code == 409
    assert "not a dismissible state" in r.json()["detail"]


@pytest.mark.anyio
async def test_dismiss_executing_state_409(tmp_path):
    store, _, app = _make(tmp_path)
    sig = Signal(source="docker", type=SignalType.CRASH, target="web")
    inc = store.add_incident(Incident(source="docker", target="web", signal=sig,
                                      dedup_key=sig.dedup_key,
                                      state=IncidentState.EXECUTING))
    async with _client(app) as c:
        r = await c.post(f"/api/incidents/{inc.id}/dismiss")
    assert r.status_code == 409


@pytest.mark.anyio
async def test_dismiss_investigating_closes_and_audits(tmp_path):
    store, _, app = _make(tmp_path)
    sig = Signal(source="docker", type=SignalType.CRASH, target="web")
    inc = store.add_incident(Incident(source="docker", target="web", signal=sig,
                                      dedup_key=sig.dedup_key,
                                      state=IncidentState.INVESTIGATING))
    async with _client(app) as c:
        r = await c.post(f"/api/incidents/{inc.id}/dismiss")
        detail = await c.get(f"/api/incidents/{inc.id}")
    assert r.status_code == 200
    assert r.json()["state"] == "closed_no_fix"
    audit = detail.json()["audit"]
    assert any(a["event"] == "dismissed" and a["actor"] == "you" for a in audit)


@pytest.mark.anyio
async def test_dismiss_failed_diagnosis_closes(tmp_path):
    store, _, app = _make(tmp_path)  # no pipeline -> still works (pure transition)
    sig = Signal(source="docker", type=SignalType.CRASH, target="web")
    inc = store.add_incident(Incident(source="docker", target="web", signal=sig,
                                      dedup_key=sig.dedup_key,
                                      state=IncidentState.DIAGNOSIS_FAILED))
    async with _client(app) as c:
        r = await c.post(f"/api/incidents/{inc.id}/dismiss")
    assert r.status_code == 200
    assert r.json()["state"] == "closed_no_fix"


# ---- Task 4: GET /api/config ----

@pytest.mark.anyio
async def test_get_config_annotated_and_redacted(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    bus = EventBus()
    app = create_app(store, bus, Config(auth_token="s3cret"))
    async with _client(app) as c:
        r = await c.get("/api/config", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "observe"
    fields = {f["key"]: f for f in body["fields"]}
    assert fields["auth_token"]["value"] == "set"   # redacted
    assert "s3cret" not in r.text
    assert fields["mode"]["editable"] is True
    assert fields["compose_path"]["editable"] is False


@pytest.mark.anyio
async def test_patch_config_mutates_persists_publishes(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    bus = EventBus()
    config = Config()
    app = create_app(store, bus, config)
    async with _client(app) as c:
        r = await c.patch("/api/config", json={"key": "mode", "value": "execute"})
    assert r.status_code == 200
    assert config.mode == "execute"                       # live mutation
    assert store.list_settings()["mode"] == "execute"     # persisted
    # non-editable -> 409
    async with _client(app) as c:
        r2 = await c.patch("/api/config", json={"key": "compose_path", "value": "/x"})
    assert r2.status_code == 409
    # invalid value -> 422, config unchanged
    async with _client(app) as c:
        r3 = await c.patch("/api/config", json={"key": "verify_polls", "value": "nope"})
    assert r3.status_code == 422
    assert config.verify_polls == 6


@pytest.mark.anyio
async def test_patch_config_requires_auth_when_token_set(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    app = create_app(store, EventBus(), Config(auth_token="tok"))
    async with _client(app) as c:
        r = await c.patch("/api/config", json={"key": "mode", "value": "propose"})
    assert r.status_code == 401
    async with _client(app) as c:
        ok = await c.patch("/api/config", json={"key": "mode", "value": "propose"},
                           headers={"X-Digdown-Token": "tok"})
    assert ok.status_code == 200


@pytest.mark.anyio
async def test_delete_config_resets_to_yaml(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    config = Config()
    base = Config()  # pristine
    app = create_app(store, EventBus(), config, base_config=base)
    async with _client(app) as c:
        await c.patch("/api/config", json={"key": "verify_polls", "value": 12})
        assert config.verify_polls == 12
        r = await c.delete("/api/config/verify_polls")
    assert r.status_code == 200
    assert config.verify_polls == 6                       # reset to base
    assert "verify_polls" not in store.list_settings()


# ---- Cycle 3 Task 3: cpu_series / mem_series on /api/services ----

class _RingWatcher:
    class _Src:
        def list_states(self):
            return [ContainerState(id="1", name="web", status="running",
                                   cpu_percent=12.0, mem_percent=80.0)]

    def __init__(self):
        from digdown.stats_ring import StatsRing
        self.source = _RingWatcher._Src()
        self.stats_ring = StatsRing()
        self.stats_ring.record(self.source.list_states())


@pytest.mark.anyio
async def test_services_includes_resource_series(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    app = create_app(store, EventBus(), Config(), watcher=_RingWatcher())
    async with _client(app) as c:
        r = await c.get("/api/services")
    svc = next(s for s in r.json()["services"] if s["name"] == "web")
    assert svc["cpu_series"] == [12.0]
    assert svc["mem_series"] == [80.0]


@pytest.mark.anyio
async def test_services_series_empty_without_ring(tmp_path):
    # _FakeWatcher (defined earlier in this file) has a source but no stats_ring
    store = Store(str(tmp_path / "t.db"))
    app = create_app(store, EventBus(), Config(), watcher=_FakeWatcher())
    async with _client(app) as c:
        r = await c.get("/api/services")
    for s in r.json()["services"]:
        assert s["cpu_series"] == [] and s["mem_series"] == []


# ---- Task 3: host vitals on /api/services ----

class _HostWatcher:
    class _Src:
        def list_states(self):
            return []

    def __init__(self):
        from digdown.stats_ring import HostRing
        self.source = _HostWatcher._Src()
        self.host_ring = HostRing()
        self.host_ring.record(90.0, 50.0)
        self.host_vitals = lambda: {"disk_percent": 90.0, "load1": 1.1, "mem_percent": 50.0}


@pytest.mark.anyio
async def test_services_includes_host_vitals(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    app = create_app(store, EventBus(), Config(), watcher=_HostWatcher())
    async with _client(app) as c:
        r = await c.get("/api/services")
    h = r.json()["host"]
    assert h["disk_percent"] == 90.0 and h["load1"] == 1.1 and h["mem_percent"] == 50.0
    assert h["disk_series"] == [90.0] and h["mem_series"] == [50.0]


@pytest.mark.anyio
async def test_services_host_defaults_without_support(tmp_path):
    # _FakeWatcher (defined earlier in this file) has a source but no host_ring/host_vitals
    store = Store(str(tmp_path / "t.db"))
    app = create_app(store, EventBus(), Config(), watcher=_FakeWatcher())
    async with _client(app) as c:
        r = await c.get("/api/services")
    h = r.json()["host"]
    assert h["disk_percent"] is None and h["disk_series"] == [] and h["mem_series"] == []


@pytest.mark.anyio
async def test_alerts_creates_incident(tmp_path):
    store = Store(str(tmp_path / "a.db"))
    app = _app_alerts(store)
    async with _client(app) as c:
        r = await c.post("/api/alerts", json=_firing())
    assert r.json() == {"ingested": 1, "resolved": 0}
    opened = store.open_incident_by_dedup("grafana:fpA")
    assert opened is not None and opened.target == "shop-api"
    assert opened.signal.type.value == "alert_firing"


@pytest.mark.anyio
async def test_alerts_dedup_and_resolve(tmp_path):
    store = Store(str(tmp_path / "b.db"))
    app = _app_alerts(store)
    async with _client(app) as c:
        await c.post("/api/alerts", json=_firing())
        r2 = await c.post("/api/alerts", json=_firing())                 # re-fire
        assert r2.json()["ingested"] == 0                                # deduped
        r3 = await c.post("/api/alerts",
                          json={"alerts": [{"status": "resolved", "fingerprint": "fpA",
                                            "labels": {}, "annotations": {}}]})
    assert r3.json()["resolved"] == 1
    assert store.open_incident_by_dedup("grafana:fpA") is None           # closed
