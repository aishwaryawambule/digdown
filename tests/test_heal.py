"""Auto heal acts with nobody watching, so each way it must hold back has a test."""
from datetime import timedelta

import pytest

from digdown.agent_models import Diagnosis
from digdown.config import Config
from digdown.heal import AUTO_HEAL_IDENTITY, decide, keep_after_update_hint
from digdown.models import (ContainerState, HealthStatus, Incident, IncidentState,
                                 Signal, SignalType, utcnow)
from digdown.plan_models import Action, ActionType, Approval, Plan

_MB = 1024 * 1024


def _state(name="app-web-1", project="app", status="exited", **kw):
    kw.setdefault("finished_at", utcnow())
    return ContainerState(id=kw.pop("id", "abc"), name=name, project=project,
                          service=kw.pop("service", "web"), status=status, **kw)


def _incident(store, target="app-web-1", kind=SignalType.CRASH, **kw):
    sig = Signal(source="docker", type=kind, target=target)
    return store.add_incident(Incident(source="docker", target=target, signal=sig,
                                       dedup_key=f"{sig.dedup_key}:{utcnow().timestamp()}",
                                       state=IncidentState.PROPOSED, **kw))


def _plan(incident, *actions):
    return Plan(incident_id=incident.id, summary="Fix it", kind="automated",
                critic2_supported=True,
                actions=list(actions) or [Action(type=ActionType.RESTART, target=incident.target)])


def _diagnosis(incident, confidence=0.9):
    return Diagnosis(incident_id=incident.id, summary="s", root_cause="r",
                     confidence=confidence, critic_supported=True)


def _config(**kw):
    return Config(**{"mode": "execute", "auto_heal_stacks": ["app"], **kw})


def _decide(store, incident, plan=None, *, config=None, states=None, stopped=False, **kw):
    return decide(incident, plan or _plan(incident), _diagnosis(incident),
                  config=config or _config(), states=[_state()] if states is None else states,
                  store=store, stopped_on_purpose=lambda _t: stopped, **kw)


def _past_heal(store, target, ago=timedelta(hours=1)):
    incident = _incident(store, target=target)
    store.add_approval(Approval(incident_id=incident.id, decision="approve",
                                identity=AUTO_HEAL_IDENTITY, created_at=utcnow() - ago))
    return incident


def test_heals_a_crashed_container_in_an_opted_in_stack(store):
    assert _decide(store, _incident(store)).allowed


@pytest.mark.parametrize("kind", [SignalType.CRASH, SignalType.OOM, SignalType.UNHEALTHY,
                                  SignalType.RESTART_LOOP])
def test_real_failures_qualify(store, kind):
    state = _state(status="running", health=HealthStatus.UNHEALTHY)
    assert _decide(store, _incident(store, kind=kind), states=[state]).allowed


def test_off_by_default(store):
    decision = _decide(store, _incident(store), config=Config(mode="execute"))
    assert not decision.allowed and decision.reason == ""


def test_a_stack_nobody_opted_in_stays_quiet(store):
    decision = _decide(store, _incident(store), config=_config(auto_heal_stacks=["other"]))
    assert not decision.allowed and decision.reason == ""


def test_needs_the_mode_that_allows_fixes(store):
    decision = _decide(store, _incident(store), config=_config(mode="propose"))
    assert not decision.allowed and "Fix after I approve" in decision.reason


def test_a_plain_container_with_no_stack_is_left_alone(store):
    state = _state(project="")
    assert not _decide(store, _incident(store), states=[state]).allowed


@pytest.mark.parametrize("kind", [SignalType.PAUSED, SignalType.ALERT_FIRING])
def test_what_needs_a_person_never_qualifies(store, kind):
    decision = _decide(store, _incident(store, kind=kind))
    assert not decision.allowed and "needs you" in decision.reason


def test_never_restarts_what_someone_stopped_on_purpose(store):
    decision = _decide(store, _incident(store), stopped=True)
    assert not decision.allowed and "on purpose" in decision.reason


def test_unsure_whether_it_was_stopped_on_purpose_means_ask(store):
    def unknown(_t):
        raise RuntimeError("no events")
    incident = _incident(store)
    decision = decide(incident, _plan(incident), _diagnosis(incident), config=_config(),
                      states=[_state()], store=store, stopped_on_purpose=unknown)
    assert not decision.allowed and "on purpose" in decision.reason


def test_a_container_already_down_before_digdown_noticed_asks(store):
    # It could have been stopped on purpose before Digdown was running.
    state = _state(finished_at=utcnow() - timedelta(minutes=6))
    decision = _decide(store, _incident(store), states=[state])
    assert not decision.allowed and "on purpose" in decision.reason


def test_a_container_with_no_stop_time_asks(store):
    state = ContainerState(id="abc", name="app-web-1", project="app", status="exited")
    assert not _decide(store, _incident(store), states=[state]).allowed


def test_a_container_that_recovered_during_diagnosis_is_left_alone(store):
    state = _state(status="running", health=HealthStatus.HEALTHY)
    decision = _decide(store, _incident(store), states=[state])
    assert not decision.allowed and "recovered" in decision.reason


def test_a_problem_that_came_back_after_a_fix_goes_to_a_person(store):
    decision = _decide(store, _incident(store, recurred_after_fix=True))
    assert not decision.allowed and "came back" in decision.reason


def test_a_shaky_diagnosis_goes_to_a_person(store):
    incident = _incident(store)
    decision = decide(incident, _plan(incident), _diagnosis(incident, confidence=0.5),
                      config=_config(), states=[_state()], store=store,
                      stopped_on_purpose=lambda _t: False)
    assert not decision.allowed and "sure" in decision.reason


@pytest.mark.parametrize("action", [
    Action(type=ActionType.SET_ENV_VAR, target="app-web-1", params={"key": "A", "value": "b"}),
    Action(type=ActionType.RECREATE, target="app-web-1"),
])
def test_setting_changes_always_ask(store, action):
    incident = _incident(store)
    decision = _decide(store, incident, _plan(incident, action))
    assert not decision.allowed and "approval" in decision.reason


def test_a_fix_reaching_into_another_stack_asks(store):
    incident = _incident(store)
    other = _state(name="notes-db-1", project="notes", service="db", id="def")
    plan = _plan(incident, Action(type=ActionType.RESTART, target="notes-db-1"))
    decision = _decide(store, incident, plan, states=[_state(), other])
    assert not decision.allowed and "outside" in decision.reason


def test_a_service_name_shared_by_two_stacks_asks(store):
    incident = _incident(store)
    one = _state(name="app-db-1", service="db", id="d1")
    two = _state(name="notes-db-1", project="notes", service="db", id="d2")
    plan = _plan(incident, Action(type=ActionType.RESTART, target="db"))
    assert not _decide(store, incident, plan, states=[_state(), one, two]).allowed


def _raise_to(incident, megabytes):
    return _plan(incident, Action(type=ActionType.SET_RESOURCE_LIMIT, target=incident.target,
                                  params={"mem_limit_bytes": megabytes * _MB}))


def test_may_raise_a_memory_limit_up_to_double(store):
    incident = _incident(store, kind=SignalType.OOM)
    state = _state(mem_limit_bytes=256 * _MB)
    assert _decide(store, incident, _raise_to(incident, 512), states=[state]).allowed


def test_a_bigger_memory_jump_asks(store):
    incident = _incident(store, kind=SignalType.OOM)
    state = _state(mem_limit_bytes=256 * _MB)
    decision = _decide(store, incident, _raise_to(incident, 513), states=[state])
    assert not decision.allowed and "double" in decision.reason


def test_lowering_a_memory_limit_asks(store):
    incident = _incident(store, kind=SignalType.OOM)
    state = _state(mem_limit_bytes=256 * _MB)
    assert not _decide(store, incident, _raise_to(incident, 128), states=[state]).allowed


def test_a_container_with_no_memory_limit_asks(store):
    incident = _incident(store, kind=SignalType.OOM)
    assert not _decide(store, incident, _raise_to(incident, 512), states=[_state()]).allowed


def test_stops_after_the_attempt_limit(store):
    for _ in range(3):
        _past_heal(store, "app-web-1")
    decision = _decide(store, _incident(store))
    assert not decision.allowed and "3 times" in decision.reason


def test_attempt_limit_can_be_changed(store):
    _past_heal(store, "app-web-1")
    assert not _decide(store, _incident(store), config=_config(auto_heal_max_attempts=1)).allowed


def test_old_attempts_do_not_count(store):
    for _ in range(3):
        _past_heal(store, "app-web-1", ago=timedelta(hours=25))
    assert _decide(store, _incident(store)).allowed


def test_another_containers_attempts_do_not_count(store):
    for _ in range(3):
        _past_heal(store, "app-other-1")
    assert _decide(store, _incident(store)).allowed


def test_many_fixes_in_an_hour_points_to_a_bigger_problem(store):
    for n in range(5):
        _past_heal(store, f"app-{n}-1", ago=timedelta(minutes=10))
    decision = _decide(store, _incident(store))
    assert not decision.allowed and "bigger problem" in decision.reason


def test_it_will_not_restart_itself(store):
    decision = _decide(store, _incident(store), own_id="abc")
    assert not decision.allowed and "itself" in decision.reason


def test_hint_says_a_new_limit_is_lost_on_update(store):
    incident = _incident(store)
    hint = keep_after_update_hint(_raise_to(incident, 512))
    assert "mem_limit: 512m" in hint and "update" in hint


def test_no_hint_for_a_restart(store):
    assert keep_after_update_hint(_plan(_incident(store))) == ""


def test_store_lists_auto_healed_incidents_only(store):
    healed = _past_heal(store, "app-web-1")
    by_hand = _incident(store, target="app-other-1")
    store.add_approval(Approval(incident_id=by_hand.id, decision="approve"))
    assert store.auto_healed_incident_ids() == {healed.id}


# ---- the pipeline running it ----

class _Source:
    def __init__(self, status="exited", stopped=False):
        self.status, self.stopped = status, stopped

    def list_states(self):
        return [_state(status=self.status, exit_code=1)]

    def get_logs(self, name, lines=200):
        return "boom\n"

    def inspect(self, name):
        return {"image": "web:1"}

    def stopped_on_purpose(self, target):
        return self.stopped


_RESTART_ANSWERS = [
    '{"summary": "Crash", "root_cause": "bad state", "evidence": ["exit 1"], "confidence": 0.9}',
    '{"supported": true, "reason": "fits"}',
    '{"summary": "Restart it", "actions": [{"type": "restart", "target": "app-web-1"}]}',
    '{"supported": true, "reason": "safe"}',
]


def _run(store, bus, config, source=None, wired=True):
    from digdown.llm import FakeLLM, LLMResponse
    from digdown.notifier import Notifier
    from digdown.pipeline import Pipeline
    from digdown.tools import DiagnosticTools

    seen, started = [], []
    pipeline = Pipeline(store, bus, config,
                        FakeLLM([LLMResponse(content=c) for c in _RESTART_ANSWERS]),
                        DiagnosticTools(source or _Source(), None),
                        notifier=Notifier(channels=[seen.append]))
    if wired:
        pipeline.start_fix = started.append
    sig = Signal(source="docker", type=SignalType.CRASH, target="app-web-1")
    incident = store.add_incident(Incident(source="docker", target="app-web-1", signal=sig,
                                           dedup_key=sig.dedup_key))
    pipeline.run(incident)
    return store.get_incident(incident.id), seen, started


def test_an_allowed_fix_starts_without_a_click(store, bus):
    incident, seen, started = _run(store, bus, _config())
    assert incident.state == IncidentState.APPROVED
    assert [i.id for i in started] == [incident.id]
    assert store.approvals_for(incident.id)[0].identity == AUTO_HEAL_IDENTITY
    assert "auto_approved" in [r["event"] for r in store.audit_for(incident.id)]
    assert [p["kind"] for p in seen] == []  # the note comes when the fix has finished


def test_a_stack_not_opted_in_waits_for_approval_as_before(store, bus):
    incident, seen, started = _run(store, bus, Config(mode="execute"))
    assert incident.state == IncidentState.PROPOSED and started == []
    assert seen[0]["kind"] == "needs_approval"
    assert "Auto heal" not in seen[0]["detail"]


def test_a_refusal_tells_the_person_why(store, bus):
    incident, seen, started = _run(store, bus, _config(), source=_Source(stopped=True))
    assert incident.state == IncidentState.PROPOSED and started == []
    assert "on purpose" in seen[0]["detail"]
    assert "auto_heal_skipped" in [r["event"] for r in store.audit_for(incident.id)]


def test_a_container_that_cannot_be_read_waits_for_approval(store, bus):
    class Broken(_Source):
        def list_states(self):
            # Docker goes away after the fix has been planned.
            if store.latest_plan_for(store.list_incidents()[0].id):
                raise RuntimeError("docker went away")
            return super().list_states()

    incident, seen, started = _run(store, bus, _config(), source=Broken())
    assert incident.state == IncidentState.PROPOSED and started == []


def test_nothing_runs_when_no_fixer_is_attached(store, bus):
    incident, _, started = _run(store, bus, _config(), wired=False)
    assert incident.state == IncidentState.PROPOSED and started == []


# ---- the executor reporting it ----

def _execute(store, bus, identity, verify_ok=True, plan=None):
    from digdown.executor import Executor
    from digdown.notifier import Notifier

    class Writer:
        def capture(self, target):
            return {}

        def apply(self, action):
            pass

        def rollback(self, action, snapshot):
            pass

    class Verifier:
        def verify(self, incident):
            return verify_ok, "stable across 6 checks" if verify_ok else "still down"

    seen = []
    executor = Executor(store, bus, _config(), Writer(), Verifier(),
                        lambda: {"app-web-1"}, notifier=Notifier(channels=[seen.append]))
    incident = _incident(store)
    store.add_plan(plan(incident) if plan else _plan(incident))
    store.add_approval(Approval(incident_id=incident.id, decision="approve", identity=identity))
    executor.execute(incident)
    return seen[-1]


def test_a_fixed_incident_reports_what_auto_heal_did(store, bus):
    note = _execute(store, bus, AUTO_HEAL_IDENTITY)
    assert note["kind"] == "auto_healed"
    assert "Fix it" in note["detail"] and "stable across 6 checks" in note["detail"]


def test_a_fix_you_approved_reports_as_before(store, bus):
    assert _execute(store, bus, "digdown")["kind"] == "resolved"


def test_an_undone_auto_heal_says_it_needs_you(store, bus):
    note = _execute(store, bus, AUTO_HEAL_IDENTITY, verify_ok=False)
    assert note["kind"] == "rolled_back" and "still down" in note["detail"]


def test_the_note_warns_when_a_limit_will_not_survive_an_update(store, bus):
    note = _execute(store, bus, AUTO_HEAL_IDENTITY, plan=lambda i: _raise_to(i, 512))
    assert "mem_limit: 512m" in note["detail"]


def test_live_updates_mark_the_incident_as_auto_healed(store, bus):
    _execute(store, bus, AUTO_HEAL_IDENTITY)
    last = [e for e in bus.events if e["type"] == "incident_updated"][-1]
    assert last["incident"]["auto_healed"] is True


# ---- found by running against real Docker ----

def test_a_crash_loop_caught_between_crashes_still_counts_as_failing(store):
    # With restart: unless-stopped it can be "running" for a moment at any check.
    sig = Signal(source="docker", type=SignalType.RESTART_LOOP, target="app-web-1",
                 detail={"restart_count": 5})
    incident = store.add_incident(Incident(source="docker", target="app-web-1", signal=sig,
                                           dedup_key="loop", state=IncidentState.PROPOSED))
    state = _state(status="running", restart_count=7)
    assert _decide(store, incident, states=[state]).allowed


def test_a_crash_loop_that_stopped_during_diagnosis_counts_as_recovered(store):
    sig = Signal(source="docker", type=SignalType.RESTART_LOOP, target="app-web-1",
                 detail={"restart_count": 5})
    incident = store.add_incident(Incident(source="docker", target="app-web-1", signal=sig,
                                           dedup_key="loop", state=IncidentState.PROPOSED))
    state = _state(status="running", restart_count=5)
    decision = _decide(store, incident, states=[state])
    assert not decision.allowed and "recovered" in decision.reason


def test_a_shared_service_name_is_explained_as_that(store):
    incident = _incident(store)
    one = _state(name="app-db-1", service="db", id="d1")
    two = _state(name="notes-db-1", project="notes", service="db", id="d2")
    plan = _plan(incident, Action(type=ActionType.RESTART, target="db"))
    decision = _decide(store, incident, plan, states=[_state(), one, two])
    assert "more than one container" in decision.reason


def test_the_note_says_what_went_wrong_as_well_as_what_was_done(store, bus):
    from digdown.executor import Executor
    from digdown.notifier import Notifier

    class Writer:
        def capture(self, target):
            return {}

        def apply(self, action):
            pass

        def rollback(self, action, snapshot):
            pass

    class Verifier:
        def verify(self, incident):
            return True, "app-web-1 stable across 6 checks"

    seen = []
    executor = Executor(store, bus, _config(), Writer(), Verifier(), lambda: {"app-web-1"},
                        notifier=Notifier(channels=[seen.append]))
    incident = _incident(store)
    store.add_diagnosis(Diagnosis(incident_id=incident.id, summary="Ran out of memory",
                                  root_cause="r", confidence=0.9, critic_supported=True))
    store.add_plan(_plan(incident))
    store.add_approval(Approval(incident_id=incident.id, decision="approve",
                                identity=AUTO_HEAL_IDENTITY))
    executor.execute(incident)
    assert seen[-1]["detail"] == "Ran out of memory. Fix it. app-web-1 stable across 6 checks."


# ---- found on the second real run ----

def test_the_decision_is_made_before_anyone_can_click_approve(store, bus):
    # Reading Docker takes seconds. If the incident already showed as waiting, a click
    # in that gap and auto heal would both run the fix.
    seen_states = []

    class Watching(_Source):
        def list_states(self):
            incidents = store.list_incidents()
            if incidents:
                seen_states.append(incidents[0].state)
            return super().list_states()

    _run(store, bus, _config(), source=Watching())
    assert IncidentState.PROPOSED not in seen_states


def test_a_container_that_recovered_sends_no_alert(store, bus):
    state = _state(status="running")

    class Recovered(_Source):
        def list_states(self):
            return [state]

    incident, seen, started = _run(store, bus, _config(), source=Recovered())
    assert incident.state == IncidentState.PROPOSED and started == [] and seen == []


def test_a_problem_coming_back_is_handed_to_a_person_with_the_reason(store, bus):
    from digdown.notifier import Notifier

    class Stable(_Source):
        pass

    prior, seen_first, _ = _run(store, bus, _config())
    store.update_state(prior.id, IncidentState.RESOLVED)

    from digdown.llm import FakeLLM
    from digdown.pipeline import Pipeline
    from digdown.tools import DiagnosticTools
    seen, started = [], []
    pipeline = Pipeline(store, bus, _config(), FakeLLM([]), DiagnosticTools(Stable(), None),
                        notifier=Notifier(channels=[seen.append]))
    pipeline.start_fix = started.append
    sig = Signal(source="docker", type=SignalType.CRASH, target="app-web-1")
    again = store.add_incident(Incident(source="docker", target="app-web-1", signal=sig,
                                        dedup_key=f"{sig.dedup_key}:again"))
    pipeline.run(again)
    assert started == [] and store.get_incident(again.id).state == IncidentState.PROPOSED
    assert "came back" in seen[-1]["detail"] and seen[-1]["kind"] == "needs_approval"


def test_live_updates_carry_the_summary_the_dashboard_lists(store, bus):
    _execute(store, bus, AUTO_HEAL_IDENTITY)
    last = [e for e in bus.events if e["type"] == "incident_updated"][-1]
    assert last["incident"]["heal_summary"] == "Fix it"


# ---- found when a fix named `db` met another stack's `db` on a real machine ----

class _TwoDbs(_Source):
    def list_states(self):
        return [_state(name="app-web-1", service="web", id="w"),
                _state(name="app-db-1", service="db", status="running", id="d"),
                _state(name="notes-db-1", service="db", project="notes", status="running", id="b")]


def test_a_service_name_is_pinned_to_the_container_in_the_failing_stack(store, bus):
    from digdown.llm import FakeLLM, LLMResponse
    from digdown.pipeline import Pipeline
    from digdown.tools import DiagnosticTools

    answers = list(_RESTART_ANSWERS)
    answers[2] = '{"summary": "Restart the database", "actions": [{"type": "restart", "target": "db"}]}'
    pipeline = Pipeline(store, bus, Config(mode="execute"),
                        FakeLLM([LLMResponse(content=c) for c in answers]),
                        DiagnosticTools(_TwoDbs(), None))
    sig = Signal(source="docker", type=SignalType.CRASH, target="app-web-1")
    incident = store.add_incident(Incident(source="docker", target="app-web-1", signal=sig,
                                           dedup_key=sig.dedup_key))
    pipeline.run(incident)
    assert store.latest_plan_for(incident.id).actions[0].target == "app-db-1"


def test_recreate_keeps_the_service_name_compose_needs(store, bus):
    from digdown.pipeline import Pipeline
    from digdown.tools import DiagnosticTools

    pipeline = Pipeline(store, bus, Config(mode="execute"), None, DiagnosticTools(_TwoDbs(), None))
    sig = Signal(source="docker", type=SignalType.CRASH, target="app-web-1")
    incident = store.add_incident(Incident(source="docker", target="app-web-1", signal=sig,
                                           dedup_key=sig.dedup_key))
    action = Action(type=ActionType.RECREATE, target="db")
    assert pipeline._pin_to_container(incident, [action])[0].target == "db"


# ---- found with a real model, which offered a manual fix for a plain out-of-memory ----

class _RecordingLLM:
    def __init__(self, answers):
        self.answers, self.seen = list(answers), []

    def chat(self, messages, tools=None):
        from digdown.llm import LLMResponse
        self.seen.append(messages)
        return LLMResponse(content=self.answers.pop(0))


def _planned_with(store, bus, state):
    from digdown.pipeline import Pipeline
    from digdown.tools import DiagnosticTools

    class Source(_Source):
        def list_states(self):
            return [state]

    llm = _RecordingLLM(_RESTART_ANSWERS)
    sig = Signal(source="docker", type=SignalType.OOM, target="app-web-1")
    incident = store.add_incident(Incident(source="docker", target="app-web-1", signal=sig,
                                           dedup_key=sig.dedup_key))
    Pipeline(store, bus, Config(mode="execute"), llm, DiagnosticTools(Source(), None)).run(incident)
    return " ".join(m["content"] for call in llm.seen[2:] for m in call if m.get("content"))


def test_the_planner_and_its_critic_are_told_the_real_memory_limit(store, bus):
    text = _planned_with(store, bus, _state(mem_limit_bytes=32 * _MB, oom_killed=True))
    assert str(32 * _MB) in text and str(64 * _MB) in text


def test_the_planner_is_told_when_there_is_no_limit_to_raise(store, bus):
    assert "no memory limit" in _planned_with(store, bus, _state(oom_killed=True))


def test_the_planner_is_told_a_limited_container_that_ran_out_needs_it_raised():
    from digdown.agents import PLANNER_SYSTEM
    assert "out of memory" in PLANNER_SYSTEM and "not a manual fix" in PLANNER_SYSTEM


# ---- every app at once ----

def test_all_apps_covers_a_stack_nobody_listed(store):
    config = Config(mode="execute", auto_heal_all=True)
    state = _state(project="brand-new")
    incident = _incident(store)
    assert _decide(store, incident, config=config, states=[state]).allowed


def test_all_apps_covers_containers_with_no_stack(store):
    config = Config(mode="execute", auto_heal_all=True)
    assert _decide(store, _incident(store), config=config, states=[_state(project="")]).allowed


def test_all_apps_still_needs_the_mode_that_allows_fixes(store):
    config = Config(mode="propose", auto_heal_all=True)
    decision = _decide(store, _incident(store), config=config)
    assert not decision.allowed and "Fix after I approve" in decision.reason


def test_all_apps_lets_a_fix_reach_a_dependency_in_another_stack(store):
    config = Config(mode="execute", auto_heal_all=True)
    incident = _incident(store)
    db = _state(name="other-db-1", project="other", status="running")
    plan = _plan(incident, Action(type=ActionType.RESTART, target="other-db-1"))
    decision = _decide(store, incident, plan, config=config, states=[_state(), db])
    assert decision.allowed, decision.reason
