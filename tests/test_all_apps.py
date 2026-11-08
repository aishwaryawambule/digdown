"""Watching every self-hosted app on a machine, using what Docker already knows,
rather than needing one Compose file per stack."""
from datetime import timedelta

import pytest

from digdown.config import Config
from digdown.docker_adapter import DockerAdapter, parse_depends_on
from digdown.heal import decide
from digdown.llm import FakeLLM, LLMResponse
from digdown.models import ContainerState, Incident, IncidentState, Signal, SignalType, utcnow
from digdown.pipeline import Pipeline
from digdown.plan_models import Action, ActionType, Plan
from digdown.agent_models import Diagnosis
from digdown.tools import DiagnosticTools
from digdown.watcher import Watcher

_ANSWERS = [
    '{"summary": "Crash", "root_cause": "cannot reach the database", "evidence": ["log"], "confidence": 0.9}',
    '{"supported": true, "reason": "fits"}',
    '{"summary": "Start the database", "actions": [{"type": "start", "target": "app-db-1"}]}',
    '{"supported": true, "reason": "safe"}',
]


# ---- what Docker's labels say ----

def test_compose_depends_on_label_is_read():
    assert parse_depends_on("db:service_healthy:false,redis:service_started:true") == ["db", "redis"]


@pytest.mark.parametrize("label", ["", None])
def test_no_dependencies_is_fine(label):
    assert parse_depends_on(label) == []


def test_a_container_reports_its_dependencies_from_docker():
    class Api:
        def containers(self, all=True):
            return [{"Id": "a", "Names": ["/web"], "State": "running",
                     "Labels": {"com.docker.compose.project": "shop"}}]

        def inspect_container(self, cid):
            return {"Name": "/web", "State": {"Status": "running"}, "HostConfig": {},
                    "Config": {"Labels": {"com.docker.compose.project": "shop",
                                          "com.docker.compose.depends_on": "db:service_healthy:false"}}}

    class Client:
        api = Api()

    adapter = DockerAdapter.__new__(DockerAdapter)
    adapter._client = Client()
    adapter.project_names, adapter.exclude_names = [], []
    adapter.collect_resource_stats = False
    assert adapter.list_states()[0].depends_on == ["db"]


def _adapter_with(*containers):
    class C:
        def __init__(self, name, service):
            self.name, self.labels, self.attrs = name, {"com.docker.compose.service": service}, {}

    class Containers:
        def list(self, all=True):
            return [C(*c) for c in containers]

    class Client:
        pass
    client = Client()
    client.containers = Containers()
    adapter = DockerAdapter.__new__(DockerAdapter)
    adapter._client = client
    return adapter


def test_reading_a_container_prefers_the_exact_name_over_a_shared_service_name():
    adapter = _adapter_with(("other-db-1", "db"), ("db", "database"))
    assert adapter._find("db").name == "db"


# ---- linking failures in any stack, with no Compose file ----

def _state(name, service, status="running", project="app", **kw):
    return ContainerState(id=name, name=name, service=service, project=project, status=status,
                          exit_code=kw.pop("exit_code", 1 if status == "exited" else None), **kw)


def test_failures_link_with_no_compose_file_at_all(store, bus, config):
    class Source:
        frames = [[_state("app-db-1", "db", "exited"),
                   _state("app-web-1", "web", depends_on=["db"])],
                  [_state("app-db-1", "db", "exited"),
                   _state("app-web-1", "web", "exited", depends_on=["db"])]]

        def list_states(self):
            return self.frames.pop(0)

    watcher = Watcher(Source(), store, bus, config)  # no project given
    db = watcher.poll_once()[0]
    web = watcher.poll_once()[0]
    assert web.parent_incident_id == db.id


# ---- telling the AI about what a container depends on ----

class _RecordingLLM:
    def __init__(self, answers):
        self.answers, self.seen = list(answers), []

    def chat(self, messages, tools=None):
        self.seen.append(messages)
        return LLMResponse(content=self.answers.pop(0))


class _Source:
    def __init__(self, states):
        self.states = states

    def list_states(self):
        return self.states

    def get_logs(self, name, lines=200):
        return "name or service not known\n"

    def inspect(self, name):
        return {"image": "x", "env": {"DB_PASSWORD": "hunter2hunter2", "MODE": "server"}}

    def stopped_on_purpose(self, target):
        return False


def _run(store, bus, states, llm=None, config=None):
    llm = llm or _RecordingLLM(_ANSWERS)
    from digdown.redactor import Redactor
    pipeline = Pipeline(store, bus, config or Config(mode="execute"), llm,
                        DiagnosticTools(_Source(states), None), redactor=Redactor())
    sig = Signal(source="docker", type=SignalType.RESTART_LOOP, target="app-web-1")
    incident = store.add_incident(Incident(source="docker", target="app-web-1", signal=sig,
                                           dedup_key=sig.dedup_key))
    pipeline.run(incident)
    return llm, pipeline, incident


def test_the_diagnosis_is_told_a_dependency_is_stopped(store, bus):
    # Found in real use: the AI said "can't find the database" without knowing
    # the database container was stopped.
    # One that stopped cleanly needs no AI, so this one crashed.
    states = [_state("app-web-1", "web", "restarting", depends_on=["db"]),
              _state("app-db-1", "db", "exited", exit_code=1)]
    llm, _, _ = _run(store, bus, states)
    first = llm.seen[0][1]["content"]
    assert "app-db-1" in first and "exited" in first


def test_a_healthy_dependency_is_mentioned_as_healthy(store, bus):
    states = [_state("app-web-1", "web", "restarting", depends_on=["db"]),
              _state("app-db-1", "db", "running")]
    llm, _, _ = _run(store, bus, states)
    assert "app-db-1" in llm.seen[0][1]["content"] and "running" in llm.seen[0][1]["content"]


def test_another_stacks_db_is_not_mentioned(store, bus):
    states = [_state("app-web-1", "web", "restarting", depends_on=["db"]),
              _state("notes-db-1", "db", "exited", project="notes")]
    llm, _, _ = _run(store, bus, states)
    assert "notes-db-1" not in llm.seen[0][1]["content"]


def test_the_planner_may_start_a_stopped_dependency():
    from digdown.agents import PLAN_CRITIC_SYSTEM, PLANNER_SYSTEM
    assert "depends on" in PLANNER_SYSTEM and "depends on" in PLAN_CRITIC_SYSTEM


# ---- secrets in every watched stack ----

def test_a_secret_from_any_watched_container_is_masked(store, bus):
    states = [_state("app-web-1", "web", "restarting"), _state("notes-db-1", "db", project="notes")]
    _, pipeline, _ = _run(store, bus, states)
    assert "hunter2hunter2" not in pipeline.redactor.redact("login failed for hunter2hunter2")


def test_ordinary_settings_stay_readable_so_diagnoses_make_sense(store, bus):
    # An earlier run masked the word "worker" in a Postgres log for this reason.
    states = [_state("app-web-1", "web", "restarting")]
    _, pipeline, _ = _run(store, bus, states)
    assert "server" in pipeline.redactor.redact("running in server mode")


# ---- starting something else unattended ----

def _decide_start(store, db_state, stopped=False):
    sig = Signal(source="docker", type=SignalType.RESTART_LOOP, target="app-web-1",
                 detail={"restart_count": 1})
    incident = store.add_incident(Incident(source="docker", target="app-web-1", signal=sig,
                                           dedup_key="x", state=IncidentState.PROPOSED))
    plan = Plan(incident_id=incident.id, summary="Start db", kind="automated", critic2_supported=True,
                actions=[Action(type=ActionType.START, target="app-db-1")])
    diagnosis = Diagnosis(incident_id=incident.id, summary="s", root_cause="r", confidence=0.9,
                          critic_supported=True)
    states = [_state("app-web-1", "web", "restarting", restart_count=5, depends_on=["db"]), db_state]
    return decide(incident, plan, diagnosis, config=Config(mode="execute", auto_heal_stacks=["app"]),
                  states=states, store=store, stopped_on_purpose=lambda _t: stopped, own_id="zzz")


def test_auto_heal_starts_a_dependency_that_just_went_down(store):
    db = _state("app-db-1", "db", "exited", finished_at=utcnow())
    assert _decide_start(store, db).allowed


def test_auto_heal_starts_a_dependency_stopped_long_ago_that_the_app_needs(store):
    # The app stuck failing without it shows it's wanted, and Docker's log doesn't
    # say anyone stopped it.
    db = _state("app-db-1", "db", "exited", finished_at=utcnow() - timedelta(days=9))
    assert _decide_start(store, db).allowed


def test_auto_heal_will_not_start_something_stopped_on_purpose(store):
    db = _state("app-db-1", "db", "exited", finished_at=utcnow())
    decision = _decide_start(store, db, stopped=True)
    assert not decision.allowed and "on purpose" in decision.reason


# ---- containers with no stack ----

def test_a_plain_container_can_be_opted_in():
    from digdown.config import NO_STACK
    assert NO_STACK


def test_a_plain_container_heals_once_opted_in(store):
    from digdown.config import NO_STACK
    sig = Signal(source="docker", type=SignalType.CRASH, target="solo")
    incident = store.add_incident(Incident(source="docker", target="solo", signal=sig, dedup_key="s",
                                           state=IncidentState.PROPOSED))
    plan = Plan(incident_id=incident.id, summary="Restart", kind="automated", critic2_supported=True,
                actions=[Action(type=ActionType.RESTART, target="solo")])
    diagnosis = Diagnosis(incident_id=incident.id, summary="s", root_cause="r", confidence=0.9,
                          critic_supported=True)
    state = ContainerState(id="s", name="solo", status="exited", exit_code=1, finished_at=utcnow())
    allowed = decide(incident, plan, diagnosis, config=Config(mode="execute", auto_heal_stacks=[NO_STACK]),
                     states=[state], store=store, stopped_on_purpose=lambda _t: False, own_id="zzz")
    assert allowed.allowed


def test_watching_a_chosen_list_can_include_plain_containers():
    from digdown.config import NO_STACK
    adapter = DockerAdapter.__new__(DockerAdapter)
    adapter.project_names, adapter.exclude_names = [NO_STACK], []
    assert adapter._in_scope({}) is True
    assert adapter._in_scope({"com.docker.compose.project": "shop"}) is False


# ---- the activity log ----

def _heal_history(store):
    from digdown.plan_models import Approval
    sig = Signal(source="docker", type=SignalType.CRASH, target="web")
    fixed = store.add_incident(Incident(source="docker", target="web", signal=sig, dedup_key="a",
                                        state=IncidentState.RESOLVED))
    store.add_plan(Plan(incident_id=fixed.id, summary="Restarted web"))
    store.add_approval(Approval(incident_id=fixed.id, decision="approve", identity="auto-heal"))
    store.append_audit(fixed.id, actor="auto-heal", event="auto_approved", payload={"summary": "Restarted web"})
    undone = store.add_incident(Incident(source="docker", target="api", signal=sig, dedup_key="b",
                                         state=IncidentState.ROLLED_BACK))
    store.append_audit(undone.id, actor="auto-heal", event="auto_approved", payload={"summary": "Restarted api"})
    skipped = store.add_incident(Incident(source="docker", target="db", signal=sig, dedup_key="c",
                                          state=IncidentState.PROPOSED))
    store.append_audit(skipped.id, actor="auto-heal", event="auto_heal_skipped", payload={"reason": "It came back."})
    return fixed, undone, skipped


def test_activity_lists_what_auto_heal_did_and_held_back_newest_first(store):
    fixed, undone, skipped = _heal_history(store)
    rows = store.auto_heal_activity()
    assert [(r["incident_id"], r["outcome"]) for r in rows] == [
        (skipped.id, "left_for_you"), (undone.id, "undone"), (fixed.id, "fixed")]
    assert rows[0]["reason"] == "It came back." and rows[2]["summary"] == "Restarted web"


# ---- the failing container's own log, up front ----

def test_every_step_sees_the_failing_containers_own_log(store, bus):
    # Left to find logs itself, a real model blamed an event in another container.
    states = [_state("app-web-1", "web", "restarting")]
    llm, _, _ = _run(store, bus, states)
    for call in llm.seen:
        assert "name or service not known" in call[1]["content"]


def test_the_log_is_labelled_as_untrusted(store, bus):
    states = [_state("app-web-1", "web", "restarting")]
    llm, _, _ = _run(store, bus, states)
    assert "<untrusted-logs" in llm.seen[0][1]["content"]


def test_a_long_log_is_cut_down_to_its_end(store, bus):
    from digdown.pipeline import _FACT_LOG_CHARS
    states = [_state("app-web-1", "web", "restarting")]

    class Noisy(_Source):
        def get_logs(self, name, lines=200):
            return "x" * 20000 + "\nthe real error\n"

    from digdown.redactor import Redactor
    llm = _RecordingLLM(_ANSWERS)
    pipeline = Pipeline(store, bus, Config(mode="execute"), llm,
                        DiagnosticTools(Noisy(states), None), redactor=Redactor())
    sig = Signal(source="docker", type=SignalType.CRASH, target="app-web-1")
    incident = store.add_incident(Incident(source="docker", target="app-web-1", signal=sig, dedup_key="n"))
    facts = pipeline._container_facts(incident)
    assert "the real error" in facts and len(facts) < _FACT_LOG_CHARS + 600


def test_a_secret_in_the_log_is_masked_before_the_ai_sees_it(store, bus):
    class Leaky(_Source):
        def get_logs(self, name, lines=200):
            return "connecting with hunter2hunter2\n"

    from digdown.redactor import Redactor
    llm = _RecordingLLM(_ANSWERS)
    pipeline = Pipeline(store, bus, Config(mode="execute"), llm,
                        DiagnosticTools(Leaky([_state("app-web-1", "web", "restarting")]), None,
                                        redactor=Redactor()), redactor=Redactor())
    sig = Signal(source="docker", type=SignalType.CRASH, target="app-web-1")
    incident = store.add_incident(Incident(source="docker", target="app-web-1", signal=sig, dedup_key="l"))
    pipeline.run(incident)
    assert all("hunter2hunter2" not in m.get("content", "") for call in llm.seen for m in call)


def test_the_instructions_say_how_to_judge_evidence():
    from digdown.agents import CRITIC_SYSTEM, DIAGNOSTICIAN_SYSTEM, PLANNER_SYSTEM
    assert "own log" in DIAGNOSTICIAN_SYSTEM and "depends on" in DIAGNOSTICIAN_SYSTEM
    assert "contradict" in CRITIC_SYSTEM and "own log" in CRITIC_SYSTEM
    assert "wrong setting" in PLANNER_SYSTEM and "manual" in PLANNER_SYSTEM
