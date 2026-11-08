"""Auto heal's rule: if a fix needs nobody's knowledge or judgement, it runs by itself.
These cover both sides: what it must now fix, and what still has to reach a person."""
from datetime import timedelta
from types import SimpleNamespace

import pytest

from digdown.agent_models import Diagnosis
from digdown.allowlist import validate_action
from digdown.config import Config
from digdown.heal import decide
from digdown.models import (ContainerState, Incident, IncidentState, Signal, SignalType, utcnow)
from digdown.plan_models import Action, ActionType, Plan
from digdown.verifier import Verifier

_MB = 1024 * 1024


def _state(name="app-web-1", status="running", service="web", **kw):
    return ContainerState(id=kw.pop("id", name), name=name, project="app", service=service,
                          status=status, **kw)


def _incident(store, kind, target="app-web-1", state=IncidentState.PROPOSED):
    sig = Signal(source="docker", type=kind, target=target)
    return store.add_incident(Incident(source="docker", target=target, signal=sig,
                                       dedup_key=f"{sig.dedup_key}:{utcnow().timestamp()}",
                                       state=state))


def _decide(store, incident, states, *actions, stopped=False, **config):
    plan = Plan(incident_id=incident.id, summary="Fix it", kind="automated",
                critic2_supported=True, actions=list(actions))
    diagnosis = Diagnosis(incident_id=incident.id, summary="s", root_cause="r", confidence=0.9,
                          critic_supported=True)
    settings = {"mode": "execute", "auto_heal_stacks": ["app"], **config}
    return decide(incident, plan, diagnosis, config=Config(**settings), states=states,
                  store=store, stopped_on_purpose=lambda _t: stopped, own_id="zzz")


def _restart(target="app-web-1"):
    return Action(type=ActionType.RESTART, target=target)


def _start(target="app-web-1"):
    return Action(type=ActionType.START, target=target)


# ---- fixes that need nobody ----

@pytest.mark.parametrize("policy", ["always", "unless-stopped"])
def test_starts_a_container_meant_to_keep_running_that_exited_by_itself(store, policy):
    state = _state(status="exited", exit_code=0, finished_at=utcnow(), restart_policy=policy)
    assert _decide(store, _incident(store, SignalType.STOPPED), [state], _start()).allowed


def test_starts_its_own_long_stopped_database_when_a_failing_app_needs_it(store):
    # Found on real Docker: the database's own incident comes first, and the app
    # waits on it, so allowing this only for the app's incident never ran the fix.
    db = _state("app-db-1", "exited", "db", exit_code=0, finished_at=utcnow() - timedelta(days=9))
    web = _state(status="restarting", restart_count=9, depends_on=["db"])
    assert _decide(store, _incident(store, SignalType.STOPPED, target="app-db-1"), [db, web],
                   _start("app-db-1")).allowed


def test_starts_a_container_stuck_in_created(store):
    state = _state(status="created")
    assert _decide(store, _incident(store, SignalType.CREATED), [state], _start()).allowed


@pytest.mark.parametrize("kind", [SignalType.HTTP_ERROR, SignalType.SLOW_RESPONSE,
                                  SignalType.LOG_ERROR_SPIKE])
def test_restarts_an_app_that_looks_fine_to_docker_but_is_failing(store, kind):
    # Docker says it's running, which is why these have their own check afterwards.
    assert _decide(store, _incident(store, kind), [_state()], _restart()).allowed


def test_raises_the_limit_of_a_container_near_it_without_restarting(store):
    state = _state(mem_limit_bytes=256 * _MB, mem_percent=95.0)
    action = Action(type=ActionType.SET_RESOURCE_LIMIT, target="app-web-1",
                    params={"mem_limit_bytes": 512 * _MB})
    assert _decide(store, _incident(store, SignalType.MEMORY_PRESSURE), [state], action).allowed


def test_starts_a_dependency_stopped_long_ago_that_an_app_needs(store):
    web = _state(status="restarting", restart_count=9, depends_on=["db"])
    db = _state("app-db-1", "exited", "db", exit_code=0, finished_at=utcnow() - timedelta(days=9))
    assert _decide(store, _incident(store, SignalType.RESTART_LOOP), [web, db],
                   _start("app-db-1")).allowed


# ---- what still needs a person ----

def test_a_job_that_finished_is_not_run_again(store):
    # Exiting cleanly with no restart policy is what a migration or backup does.
    state = _state(status="exited", exit_code=0, finished_at=utcnow(), restart_policy="no")
    decision = _decide(store, _incident(store, SignalType.STOPPED), [state], _start())
    assert not decision.allowed and "job" in decision.reason


def test_a_database_someone_stopped_stays_stopped_even_if_an_app_needs_it(store):
    db = _state("app-db-1", "exited", "db", exit_code=0, finished_at=utcnow() - timedelta(days=9))
    web = _state(status="restarting", restart_count=9, depends_on=["db"])
    decision = _decide(store, _incident(store, SignalType.STOPPED, target="app-db-1"), [db, web],
                       _start("app-db-1"), stopped=True)
    assert not decision.allowed and "on purpose" in decision.reason


def test_a_healthy_app_depending_on_it_doesnt_show_it_is_wanted(store):
    db = _state("app-db-1", "exited", "db", exit_code=0, finished_at=utcnow() - timedelta(days=9))
    web = _state(depends_on=["db"])
    assert not _decide(store, _incident(store, SignalType.STOPPED, target="app-db-1"), [db, web],
                       _start("app-db-1")).allowed


def test_another_stacks_failing_app_doesnt_show_it_is_wanted(store):
    db = _state("app-db-1", "exited", "db", exit_code=0, finished_at=utcnow() - timedelta(days=9))
    other = ContainerState(id="o", name="other-web-1", project="other", service="web",
                           status="restarting", depends_on=["db"])
    assert not _decide(store, _incident(store, SignalType.STOPPED, target="app-db-1"), [db, other],
                       _start("app-db-1")).allowed


def test_a_dependency_someone_just_stopped_is_left_alone(store):
    web = _state(status="restarting", restart_count=9, depends_on=["db"])
    db = _state("app-db-1", "exited", "db", exit_code=0, finished_at=utcnow() - timedelta(days=9))
    decision = _decide(store, _incident(store, SignalType.RESTART_LOOP), [web, db],
                       _start("app-db-1"), stopped=True)
    assert not decision.allowed and "on purpose" in decision.reason


def test_something_nothing_depends_on_stopped_long_ago_is_left_alone(store):
    web = _state(status="restarting", restart_count=9)
    other = _state("app-old-1", "exited", "old", exit_code=0,
                   finished_at=utcnow() - timedelta(days=9))
    assert not _decide(store, _incident(store, SignalType.RESTART_LOOP), [web, other],
                       _start("app-old-1")).allowed


def test_its_own_long_stopped_container_is_left_alone(store):
    state = _state(status="exited", exit_code=0, finished_at=utcnow() - timedelta(days=9))
    assert not _decide(store, _incident(store, SignalType.STOPPED), [state], _start()).allowed


def test_a_paused_container_is_left_alone(store):
    # Pausing only ever happens because someone or something asked for it.
    decision = _decide(store, _incident(store, SignalType.PAUSED), [_state(status="paused")],
                       _restart())
    assert not decision.allowed and decision.reason


def test_an_outside_alert_is_left_alone(store):
    # Nothing here could confirm the fix afterwards.
    assert not _decide(store, _incident(store, SignalType.ALERT_FIRING), [_state()],
                       _restart()).allowed


def test_memory_pressure_never_restarts_a_running_app(store):
    state = _state(mem_limit_bytes=256 * _MB, mem_percent=95.0)
    raised = Action(type=ActionType.SET_RESOURCE_LIMIT, target="app-web-1",
                    params={"mem_limit_bytes": 512 * _MB})
    assert not _decide(store, _incident(store, SignalType.MEMORY_PRESSURE), [state], raised,
                       _restart()).allowed


def test_memory_pressure_that_passed_is_dropped_quietly(store):
    state = _state(mem_limit_bytes=256 * _MB, mem_percent=40.0)
    raised = Action(type=ActionType.SET_RESOURCE_LIMIT, target="app-web-1",
                    params={"mem_limit_bytes": 512 * _MB})
    decision = _decide(store, _incident(store, SignalType.MEMORY_PRESSURE), [state], raised)
    assert not decision.allowed and decision.quiet


# ---- a full disk: an opt-in of its own, because deleting can't be undone ----

def _clean():
    return Action(type=ActionType.CLEAN_DOCKER, target="host")


def test_disk_cleanup_runs_when_turned_on(store):
    incident = _incident(store, SignalType.DISK_FULL, target="host")
    assert _decide(store, incident, [], _clean(), auto_heal_disk_cleanup=True).allowed


def test_disk_cleanup_is_off_by_default_even_with_auto_heal_on(store):
    assert not Config().auto_heal_disk_cleanup
    incident = _incident(store, SignalType.DISK_FULL, target="host")
    assert not _decide(store, incident, [], _clean()).allowed


def test_disk_cleanup_needs_fixes_allowed(store):
    incident = _incident(store, SignalType.DISK_FULL, target="host")
    assert not _decide(store, incident, [], _clean(), mode="observe",
                       auto_heal_disk_cleanup=True).allowed


def test_disk_cleanup_runs_once_a_day_at_most(store):
    from digdown.heal import AUTO_HEAL_IDENTITY
    from digdown.plan_models import Approval
    first = _incident(store, SignalType.DISK_FULL, target="host")
    store.add_approval(Approval(incident_id=first.id, decision="approve",
                                identity=AUTO_HEAL_IDENTITY))
    second = _incident(store, SignalType.DISK_FULL, target="host")
    decision = _decide(store, second, [], _clean(), auto_heal_disk_cleanup=True)
    assert not decision.allowed and decision.reason


def test_cleanup_can_only_be_aimed_at_the_host():
    assert validate_action(_clean(), ["app-web-1"]) == []
    assert validate_action(Action(type=ActionType.CLEAN_DOCKER, target="app-web-1"), ["app-web-1"])


def test_nothing_else_can_be_aimed_at_the_host():
    assert validate_action(_restart("host"), ["app-web-1"])


def test_the_ai_is_never_offered_the_cleanup():
    from digdown.agents import PLANNER_SYSTEM
    assert "clean_docker" not in PLANNER_SYSTEM


def test_cleanup_removes_only_unused_images_and_build_cache():
    from digdown.write_adapter import DockerWriter
    calls = []
    client = SimpleNamespace(
        images=SimpleNamespace(prune=lambda filters=None: calls.append(("images", filters))),
        api=SimpleNamespace(prune_builds=lambda: calls.append(("builds", None))),
        volumes=SimpleNamespace(prune=lambda **_k: calls.append(("volumes", None))),
        containers=SimpleNamespace(prune=lambda **_k: calls.append(("containers", None))))
    DockerWriter(client, "docker-compose.yml").apply(_clean())
    assert calls == [("images", {"dangling": True}), ("builds", None)]


# ---- checking the fix afterwards ----

def _verifier(states, **kw):
    source = SimpleNamespace(list_states=lambda: states)
    return Verifier(source, Config(verify_polls=2, verify_poll_interval=0), sleep=lambda _s: None, **kw)


class _Probe:
    """Answers a list of (ok, detail) in turn, as an app coming up would. The verifier
    asks once first to see whether the probe applies, which uses the first answer."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked = 0

    def verify(self, _signal):
        answer = self.answers[min(self.asked, len(self.answers) - 1)]
        self.asked += 1
        return answer


_HTTP = SimpleNamespace(target="app-web-1", signal=Signal(source="http", type=SignalType.HTTP_ERROR,
                                                          target="app-web-1"))
_DOWN, _UP = (False, "connection closed"), (True, "ok")


def test_an_app_still_starting_up_after_a_restart_isnt_a_failed_fix():
    # Found on real Docker: the first check ran while the app was booting, so a
    # restart that worked was recorded as undone.
    probe = _Probe(_DOWN, _DOWN, _UP)
    assert _verifier([_state()], probes=[probe]).verify(_HTTP)[0]


def test_it_still_has_to_pass_every_check_once_it_is_up():
    probe = _Probe(_UP, _DOWN, _UP)
    assert _verifier([_state()], probes=[probe]).verify(_HTTP)[0] and probe.asked == 4


def test_an_app_that_never_comes_back_is_a_failed_fix():
    assert not _verifier([_state()], probes=[_Probe(_DOWN)]).verify(_HTTP)[0]


def test_an_app_that_comes_up_then_fails_again_is_a_failed_fix():
    assert not _verifier([_state()], probes=[_Probe(_UP, _UP, _DOWN, _UP)]).verify(_HTTP)[0]


def test_an_app_in_dockers_restart_delay_gets_time_after_its_database_is_started():
    # Found on real Docker: the database came back and so did the app, but the check
    # ran during Docker's delay of up to a minute, so it was recorded as undone.
    source = SimpleNamespace()
    verifier = Verifier(source, Config(verify_polls=6, verify_poll_interval=0), sleep=lambda _s: None)
    loop = SimpleNamespace(target="app-web-1", signal=Signal(source="docker", type=SignalType.RESTART_LOOP,
                                                             target="app-web-1"))
    frames = iter([[_state(status="restarting")]] * 9 + [[_state()]] * 6)
    source.list_states = lambda: next(frames)
    assert verifier.verify(loop)[0]


def test_an_app_that_keeps_looping_is_still_a_failed_fix():
    source = SimpleNamespace(list_states=lambda: [_state(status="restarting")])
    verifier = Verifier(source, Config(verify_polls=2, verify_poll_interval=0), sleep=lambda _s: None)
    loop = SimpleNamespace(target="app-web-1", signal=Signal(source="docker", type=SignalType.RESTART_LOOP,
                                                             target="app-web-1"))
    assert not verifier.verify(loop)[0]


def test_an_app_that_looks_fine_for_a_moment_then_crashes_again_is_a_failed_fix():
    frames = iter([[_state(status="restarting")], [_state()], [_state(status="restarting")]])
    source = SimpleNamespace(list_states=lambda: next(frames))
    verifier = Verifier(source, Config(verify_polls=2, verify_poll_interval=0), sleep=lambda _s: None)
    loop = SimpleNamespace(target="app-web-1", signal=Signal(source="docker", type=SignalType.RESTART_LOOP,
                                                             target="app-web-1"))
    assert not verifier.verify(loop)[0]


def test_a_still_unhealthy_container_fails_straight_away():
    verifier = _verifier([_state(health="unhealthy")])
    unhealthy = SimpleNamespace(target="app-web-1", signal=Signal(source="docker", type=SignalType.UNHEALTHY,
                                                                  target="app-web-1"))
    assert not verifier.verify(unhealthy)[0]


def test_a_raised_limit_only_counts_if_the_pressure_is_gone():
    incident = SimpleNamespace(target="app-web-1",
                               signal=Signal(source="docker", type=SignalType.MEMORY_PRESSURE,
                                             target="app-web-1"))
    still = _state(mem_limit_bytes=256 * _MB, mem_percent=97.0)
    assert not _verifier([still]).verify(incident)[0]
    assert _verifier([_state(mem_limit_bytes=512 * _MB, mem_percent=48.0)]).verify(incident)[0]


def test_a_cleanup_only_counts_if_the_disk_has_room():
    incident = SimpleNamespace(target="host", signal=Signal(source="host", type=SignalType.DISK_FULL,
                                                            target="host"))
    assert not _verifier([], disk_percent=lambda: 93.0).verify(incident)[0]
    assert _verifier([], disk_percent=lambda: 60.0).verify(incident)[0]


# ---- the whole way through ----

def test_a_full_disk_gets_the_fixed_rule_when_turned_on(store, bus):
    from digdown.llm import FakeLLM, LLMResponse
    from digdown.pipeline import Pipeline
    from digdown.tools import DiagnosticTools
    answers = ['{"summary": "Disk", "root_cause": "disk is 93% full", "evidence": ["93%"], "confidence": 0.9}',
               '{"supported": true, "reason": "fits"}']
    source = SimpleNamespace(list_states=lambda: [], get_logs=lambda *_a, **_k: "", inspect=lambda _t: {})
    started = []
    config = Config(mode="execute", auto_heal_disk_cleanup=True)
    pipeline = Pipeline(store, bus, config, FakeLLM([LLMResponse(content=a) for a in answers]),
                        DiagnosticTools(source, None))
    pipeline.start_fix = started.append
    incident = _incident(store, SignalType.DISK_FULL, target="host", state=IncidentState.DETECTED)
    pipeline.run(incident)
    plan = store.latest_plan_for(incident.id)
    assert [a.type for a in plan.actions] == [ActionType.CLEAN_DOCKER]
    assert len(started) == 1


def test_the_ai_cannot_slip_the_cleanup_into_a_plan(store, bus):
    from digdown.llm import FakeLLM, LLMResponse
    from digdown.pipeline import Pipeline
    from digdown.tools import DiagnosticTools
    bad = '{"summary": "Clean", "actions": [{"type": "clean_docker", "target": "host"}]}'
    answers = ['{"summary": "Crash", "root_cause": "r", "evidence": ["e"], "confidence": 0.9}',
               '{"supported": true, "reason": "fits"}', bad, bad, bad, bad]
    state = _state(status="exited", exit_code=1, finished_at=utcnow())
    source = SimpleNamespace(list_states=lambda: [state], get_logs=lambda *_a, **_k: "", inspect=lambda _t: {})
    config = Config(mode="execute")
    pipeline = Pipeline(store, bus, config, FakeLLM([LLMResponse(content=a) for a in answers]),
                        DiagnosticTools(source, None))
    incident = _incident(store, SignalType.CRASH, state=IncidentState.DETECTED)
    pipeline.run(incident)
    assert store.latest_plan_for(incident.id).kind == "manual"


def test_a_cleanup_that_did_not_free_enough_is_not_called_undone(store, bus):
    from digdown.executor import Executor
    from digdown.executor_models import ExecResult
    incident = _incident(store, SignalType.DISK_FULL, target="host")
    store.add_plan(Plan(incident_id=incident.id, summary="Clean", kind="automated",
                        critic2_supported=True, actions=[_clean()]))
    store.update_state(incident.id, IncidentState.APPROVED)
    writer = SimpleNamespace(capture=lambda _t: {}, apply=lambda _a: None,
                             rollback=lambda _a, _s: pytest.fail("nothing to undo"))
    verifier = SimpleNamespace(verify=lambda _i: (False, "the disk is still 93% full"))
    result = Executor(store, bus, Config(mode="execute"), writer, verifier, set).execute(
        store.get_incident(incident.id))
    assert result.result == ExecResult.FAILED and not result.rolled_back
    assert "undone" not in result.detail and "93%" in result.detail


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---- allowing fixes after something has already been explained ----
# Found in real use: the docs say to start on Explain only, but problems explained
# there were never looked at again once fixes were allowed, so they just sat there.

class _FakePipeline:
    def __init__(self, store):
        self.store = store
        self.start_fix = None
        self.diagnosed, self.retried = [], []

    def run(self, incident):
        self.diagnosed.append(incident.id)

    def retry_auto_heal(self, incident):
        self.retried.append(incident.id)


async def _patch_setting(store, config, key, value):
    import asyncio
    import httpx
    from digdown.api import create_app
    from digdown.events import EventBus

    class _Watcher:
        def poll_once(self):
            return []

    pipeline = _FakePipeline(store)
    app = create_app(store, EventBus(), config, watcher=_Watcher(), pipeline=pipeline)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://localhost") as client:
            response = await client.patch("/api/config", json={"key": key, "value": value})
            assert response.status_code == 200
        for _ in range(50):
            await asyncio.sleep(0.01)
    return pipeline


def _config_on(**kw):
    return Config(agent_enabled=True, poll_interval_seconds=1, **kw)


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["propose", "execute"])
async def test_allowing_fixes_looks_again_at_what_was_only_explained(store, mode):
    incident = _incident(store, SignalType.RESTART_LOOP, state=IncidentState.DIAGNOSED)
    pipeline = await _patch_setting(store, _config_on(mode="observe"), "mode", mode)
    assert incident.id in pipeline.diagnosed
    assert store.get_incident(incident.id).state == IncidentState.DETECTED


@pytest.mark.anyio
async def test_staying_on_explain_only_leaves_it_alone(store):
    incident = _incident(store, SignalType.RESTART_LOOP, state=IncidentState.DIAGNOSED)
    pipeline = await _patch_setting(store, _config_on(mode="observe"), "poll_interval_seconds", 2)
    assert pipeline.diagnosed == []
    assert store.get_incident(incident.id).state == IncidentState.DIAGNOSED


@pytest.mark.anyio
async def test_turning_on_auto_heal_picks_up_fixes_already_waiting(store):
    incident = _incident(store, SignalType.RESTART_LOOP)
    pipeline = await _patch_setting(store, _config_on(mode="execute"), "auto_heal_stacks", ["app"])
    assert pipeline.retried == [incident.id]


@pytest.mark.anyio
async def test_only_fixes_allowed_mode_retries_waiting_fixes(store):
    _incident(store, SignalType.RESTART_LOOP)
    pipeline = await _patch_setting(store, _config_on(mode="execute"), "mode", "propose")
    assert pipeline.retried == []


def test_a_restart_looks_again_too(store):
    from digdown.pipeline import recover_interrupted
    incident = _incident(store, SignalType.RESTART_LOOP, state=IncidentState.DIAGNOSED)
    recover_interrupted(store, Config(mode="execute"))
    assert store.get_incident(incident.id).state == IncidentState.DETECTED


def test_a_restart_on_explain_only_leaves_it_alone(store):
    from digdown.pipeline import recover_interrupted
    incident = _incident(store, SignalType.RESTART_LOOP, state=IncidentState.DIAGNOSED)
    recover_interrupted(store, Config(mode="observe"))
    assert store.get_incident(incident.id).state == IncidentState.DIAGNOSED


def _waiting_pipeline(store, bus, state, **config):
    from digdown.llm import FakeLLM
    from digdown.pipeline import Pipeline
    from digdown.tools import DiagnosticTools
    source = SimpleNamespace(list_states=lambda: [state], get_logs=lambda *_a, **_k: "",
                             inspect=lambda _t: {}, stopped_on_purpose=lambda _t: False)
    pipeline = Pipeline(store, bus, Config(mode="execute", **config), FakeLLM([]),
                        DiagnosticTools(source, None))
    pipeline.started = []
    pipeline.start_fix = pipeline.started.append
    incident = _incident(store, SignalType.CRASH)
    store.add_diagnosis(Diagnosis(incident_id=incident.id, summary="s", root_cause="r",
                                  confidence=0.9, critic_supported=True))
    store.add_plan(Plan(incident_id=incident.id, summary="Fix", kind="automated",
                        critic2_supported=True, actions=[_start()]))
    return pipeline, incident


def test_a_waiting_fix_runs_once_auto_heal_allows_it(store, bus):
    state = _state(status="exited", exit_code=1, finished_at=utcnow())
    pipeline, incident = _waiting_pipeline(store, bus, state, auto_heal_stacks=["app"])
    pipeline.retry_auto_heal(incident)
    assert len(pipeline.started) == 1
    assert store.get_incident(incident.id).state == IncidentState.APPROVED


def test_a_waiting_fix_auto_heal_refuses_keeps_waiting(store, bus):
    state = _state(status="exited", exit_code=1, finished_at=utcnow() - timedelta(days=2))
    pipeline, incident = _waiting_pipeline(store, bus, state, auto_heal_stacks=["app"])
    pipeline.retry_auto_heal(incident)
    assert pipeline.started == []
    assert store.get_incident(incident.id).state == IncidentState.PROPOSED


# ---- what a fix says it does comes from what it does, not from the AI ----

def _describe(*actions, states=(), now=None):
    from digdown.pipeline import describe_fix
    return describe_fix(list(actions), list(states), now=now)


def test_the_summary_says_start_when_the_action_is_start():
    assert _describe(_start("app-db-1")) == "Start app-db-1."


def test_the_summary_says_how_long_something_had_been_down():
    now = utcnow()
    db = _state("app-db-1", "exited", "db", finished_at=now - timedelta(days=9))
    assert _describe(_start("app-db-1"), states=[db], now=now) == \
        "Start app-db-1, which had been down for 9 days."


def test_a_recent_stop_isnt_worth_mentioning():
    now = utcnow()
    db = _state("app-db-1", "exited", "db", finished_at=now - timedelta(minutes=2))
    assert _describe(_start("app-db-1"), states=[db], now=now) == "Start app-db-1."


def test_raising_a_limit_and_restarting_reads_as_one_fix():
    raised = Action(type=ActionType.SET_RESOURCE_LIMIT, target="shop-cache",
                    params={"mem_limit_bytes": 512 * _MB})
    assert _describe(raised, _restart("shop-cache")) == \
        "Raise the shop-cache memory limit to 512 MB and restart it."


def test_a_setting_change_never_repeats_its_value():
    action = Action(type=ActionType.SET_ENV_VAR, target="app-web-1",
                    params={"key": "API_TOKEN", "value": "hunter2hunter2"})
    text = _describe(action)
    assert "API_TOKEN" in text and "hunter2" not in text


# ---- a stopped dependency is a fact, so it needs no AI ----

def _rule_pipeline(store, bus, states, mode="execute", **config):
    from digdown.llm import FakeLLM
    from digdown.pipeline import Pipeline
    from digdown.redactor import Redactor
    from digdown.tools import DiagnosticTools
    source = SimpleNamespace(list_states=lambda: states, get_logs=lambda *_a, **_k: "",
                             inspect=lambda _t: {}, stopped_on_purpose=lambda _t: False)
    llm = FakeLLM([])  # raises if anything asks the AI
    pipeline = Pipeline(store, bus, Config(mode=mode, auto_heal_stacks=["app"], **config), llm,
                        DiagnosticTools(source, None), redactor=Redactor())
    pipeline.started = []
    pipeline.start_fix = pipeline.started.append
    return pipeline


def _looping_app_and_stopped_db(exit_code=0, oom=False):
    web = _state(status="restarting", restart_count=9, depends_on=["db"])
    db = _state("app-db-1", "exited", "db", exit_code=exit_code, oom_killed=oom,
                finished_at=utcnow() - timedelta(days=9))
    return [web, db]


def test_an_app_failing_on_a_stopped_database_is_fixed_with_no_ai(store, bus):
    pipeline = _rule_pipeline(store, bus, _looping_app_and_stopped_db())
    incident = _incident(store, SignalType.RESTART_LOOP, state=IncidentState.DETECTED)
    pipeline.run(incident)
    plan = store.latest_plan_for(incident.id)
    assert [(a.type, a.target) for a in plan.actions] == [(ActionType.START, "app-db-1")]
    assert len(pipeline.started) == 1
    assert "9 days" in plan.summary


def test_the_stopped_databases_own_incident_is_fixed_with_no_ai(store, bus):
    pipeline = _rule_pipeline(store, bus, _looping_app_and_stopped_db())
    incident = _incident(store, SignalType.STOPPED, target="app-db-1", state=IncidentState.DETECTED)
    pipeline.run(incident)
    plan = store.latest_plan_for(incident.id)
    assert [(a.type, a.target) for a in plan.actions] == [(ActionType.START, "app-db-1")]
    assert len(pipeline.started) == 1


def test_the_rule_says_what_it_knows_and_doesnt_guess_why(store, bus):
    pipeline = _rule_pipeline(store, bus, _looping_app_and_stopped_db())
    incident = _incident(store, SignalType.RESTART_LOOP, state=IncidentState.DETECTED)
    pipeline.run(incident)
    diagnosis = store.latest_diagnosis_for(incident.id)
    assert "app-db-1" in diagnosis.root_cause and "doesn't show why" in diagnosis.root_cause
    assert diagnosis.critic_supported


def test_explain_only_gets_the_answer_and_changes_nothing(store, bus):
    pipeline = _rule_pipeline(store, bus, _looping_app_and_stopped_db(), mode="observe")
    incident = _incident(store, SignalType.RESTART_LOOP, state=IncidentState.DETECTED)
    pipeline.run(incident)
    assert store.get_incident(incident.id).state == IncidentState.DIAGNOSED
    assert pipeline.started == [] and store.latest_plan_for(incident.id) is None


@pytest.mark.parametrize("exit_code,oom", [(1, False), (137, True)])
def test_a_database_that_crashed_gets_a_real_diagnosis(store, bus, exit_code, oom):
    # Starting something that crashed may just crash again, so the cause matters.
    from digdown.pipeline import stopped_dependency
    states = _looping_app_and_stopped_db(exit_code, oom)
    incident = _incident(store, SignalType.RESTART_LOOP)
    assert stopped_dependency(incident, states) is None


def test_another_stacks_stopped_database_doesnt_count(store):
    from digdown.pipeline import stopped_dependency
    web = _state(status="restarting", depends_on=["db"])
    other = ContainerState(id="o", name="other-db-1", project="other", service="db",
                           status="exited", exit_code=0)
    assert stopped_dependency(_incident(store, SignalType.RESTART_LOOP), [web, other]) is None


def test_a_job_that_finished_with_nothing_needing_it_gets_no_rule(store):
    from digdown.pipeline import stopped_dependency
    job = _state("app-job-1", "exited", "job", exit_code=0)
    assert stopped_dependency(_incident(store, SignalType.STOPPED, target="app-job-1"), [job]) is None


# ---- the AI is told what it needs, and told not to guess ----

def test_the_diagnosis_is_told_it_may_say_it_cant_tell():
    from digdown.agents import CRITIC_SYSTEM, DIAGNOSTICIAN_SYSTEM
    assert "can't tell" in DIAGNOSTICIAN_SYSTEM
    assert "invent" in CRITIC_SYSTEM


def test_the_plan_is_told_which_failing_apps_need_this(store, bus):
    from digdown.llm import FakeLLM
    from digdown.pipeline import Pipeline
    from digdown.redactor import Redactor
    from digdown.tools import DiagnosticTools
    states = _looping_app_and_stopped_db()
    source = SimpleNamespace(list_states=lambda: states, get_logs=lambda *_a, **_k: "",
                             inspect=lambda _t: {})
    pipeline = Pipeline(store, bus, Config(), FakeLLM([]), DiagnosticTools(source, None),
                        redactor=Redactor())
    facts = pipeline._container_facts(_incident(store, SignalType.STOPPED, target="app-db-1"))
    assert "app-web-1" in facts and "depends on" in facts


# ---- what needs no AI doesn't wait behind what does ----

@pytest.mark.anyio
async def test_a_fix_that_needs_no_ai_doesnt_queue_behind_a_diagnosis(store):
    import asyncio
    import threading
    from digdown.api import create_app
    from digdown.events import EventBus

    release = threading.Event()
    done = []

    class SlowAndFast:
        start_fix = None

        def needs_no_ai(self, incident):
            return incident.target == "app-fast-1"

        def run(self, incident):
            if incident.target == "app-slow-1":
                release.wait(5)
            done.append(incident.target)

    class OneEach:
        calls = 0

        def poll_once(self):
            self.calls += 1
            if self.calls > 1:
                return []
            slow = _incident(store, SignalType.CRASH, target="app-slow-1", state=IncidentState.DETECTED)
            fast = _incident(store, SignalType.STOPPED, target="app-fast-1", state=IncidentState.DETECTED)
            return [slow, fast]

    app = create_app(store, EventBus(), Config(agent_enabled=True, poll_interval_seconds=1),
                     watcher=OneEach(), pipeline=SlowAndFast())
    try:
        async with app.router.lifespan_context(app):
            for _ in range(100):
                if done:
                    break
                await asyncio.sleep(0.02)
            assert done == ["app-fast-1"]  # the slow one is still going
            release.set()
            for _ in range(100):
                if len(done) == 2:
                    break
                await asyncio.sleep(0.02)
    finally:
        release.set()
    assert done == ["app-fast-1", "app-slow-1"]


def test_the_pipeline_knows_what_needs_no_ai(store, bus):
    pipeline = _rule_pipeline(store, bus, _looping_app_and_stopped_db())
    assert pipeline.needs_no_ai(_incident(store, SignalType.RESTART_LOOP))
    assert not pipeline.needs_no_ai(_incident(store, SignalType.UNHEALTHY, target="app-other-1"))


# ---- a container stuck in created is a fact too ----
# Found on real Docker: told not to guess, the AI said it couldn't tell why, which
# lowered its confidence, so auto heal asked instead of trying a start.

def test_a_stuck_container_is_started_with_no_ai(store, bus):
    state = _state(status="created")
    pipeline = _rule_pipeline(store, bus, [state])
    incident = _incident(store, SignalType.CREATED, state=IncidentState.DETECTED)
    assert pipeline.needs_no_ai(incident)
    pipeline.run(incident)
    plan = store.latest_plan_for(incident.id)
    assert [(a.type, a.target) for a in plan.actions] == [(ActionType.START, "app-web-1")]
    assert len(pipeline.started) == 1
    assert "never started" in store.latest_diagnosis_for(incident.id).summary


@pytest.mark.anyio
async def test_turning_on_auto_heal_for_all_apps_picks_up_fixes_already_waiting(store):
    incident = _incident(store, SignalType.RESTART_LOOP)
    pipeline = await _patch_setting(store, _config_on(mode="execute"), "auto_heal_all", True)
    assert pipeline.retried == [incident.id]
