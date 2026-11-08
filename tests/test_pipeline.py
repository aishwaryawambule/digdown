from digdown.config import Config
from digdown.llm import FakeLLM, LLMResponse
from digdown.models import (ContainerState, Incident, IncidentState, Signal,
                                  SignalType)
from digdown.notifier import Notifier
from digdown.pipeline import Pipeline
from digdown.tools import DiagnosticTools


class FakeSource:
    def list_states(self):
        return [ContainerState(id="1", name="web", status="exited", exit_code=137)]
    def get_logs(self, name, lines=200):
        return "Killed\n"
    def inspect(self, name):
        return {"image": "web:1"}


def _incident(store):
    sig = Signal(source="docker", type=SignalType.OOM, target="web",
                 detail={"exit_code": 137})
    return store.add_incident(Incident(source="docker", target="web", signal=sig,
                                       dedup_key=sig.dedup_key))


def _pipeline(store, bus, llm, mode="observe"):
    return Pipeline(store, bus, Config(mode=mode, max_critic_rounds=2),
                    llm, DiagnosticTools(FakeSource(), None))


_DIAG_OK = LLMResponse(content='{"summary": "OOM", "root_cause": "mem limit", '
                               '"evidence": ["exit 137"], "confidence": 0.8}')
_CRITIC1_OK = LLMResponse(content='{"supported": true, "reason": "fits"}')


def test_supported_diagnosis_records_and_finishes_as_diagnosed(store, bus):
    llm = FakeLLM([
        LLMResponse(content='{"summary": "OOM", "root_cause": "mem limit", '
                            '"evidence": ["exit 137"], "confidence": 0.8}'),
        LLMResponse(content='{"supported": true, "reason": "evidence fits"}'),
    ])
    inc = _incident(store)
    diag = _pipeline(store, bus, llm).run(inc)

    assert diag.critic_supported is True
    assert diag.rounds == 1
    assert store.get_incident(inc.id).state == IncidentState.DIAGNOSED
    assert store.latest_diagnosis_for(inc.id).root_cause == "mem limit"
    # state transitions were broadcast
    assert any(e["type"] == "incident_updated" for e in bus.events)
    events = [r["event"] for r in store.audit_for(inc.id)]
    assert "triaged" in events and "investigating" in events and "diagnosed" in events


def test_unsupported_after_max_rounds_closes_no_fix(store, bus):
    llm = FakeLLM([
        LLMResponse(content='{"summary": "guess1", "root_cause": "r1"}'),
        LLMResponse(content='{"supported": false, "reason": "thin", "issues": ["no logs"]}'),
        LLMResponse(content='{"summary": "guess2", "root_cause": "r2"}'),
        LLMResponse(content='{"supported": false, "reason": "still thin"}'),
    ])
    inc = _incident(store)
    diag = _pipeline(store, bus, llm).run(inc)

    assert diag.critic_supported is False
    assert diag.rounds == 2
    assert store.get_incident(inc.id).state == IncidentState.CLOSED_NO_FIX


def test_second_round_gets_critic_feedback(store, bus):
    llm = FakeLLM([
        LLMResponse(content='{"summary": "g1", "root_cause": "r1"}'),
        LLMResponse(content='{"supported": false, "reason": "need log evidence"}'),
        LLMResponse(content='{"summary": "g2", "root_cause": "r2"}'),
        LLMResponse(content='{"supported": true, "reason": "now supported"}'),
    ])
    inc = _incident(store)
    diag = _pipeline(store, bus, llm).run(inc)
    assert diag.critic_supported is True and diag.rounds == 2
    # the 3rd chat (second diagnose) must include the critic feedback
    third_user = next(m for m in llm.calls[2]["messages"] if m["role"] == "user")
    assert "need log evidence" in third_user["content"]


def test_observe_mode_does_not_propose(store, bus):
    llm = FakeLLM([_DIAG_OK, _CRITIC1_OK])
    inc = _incident(store)
    _pipeline(store, bus, llm, mode="observe").run(inc)
    assert store.get_incident(inc.id).state == IncidentState.DIAGNOSED
    assert store.latest_plan_for(inc.id) is None


def test_propose_mode_creates_approvable_plan(store, bus):
    llm = FakeLLM([
        _DIAG_OK, _CRITIC1_OK,
        LLMResponse(content='{"summary": "restart web", "actions": '
                            '[{"type": "restart", "target": "web", "rationale": "clear"}], '
                            '"manual_fix": ""}'),
        LLMResponse(content='{"supported": true, "reason": "reversible, in scope"}'),
    ])
    inc = _incident(store)
    _pipeline(store, bus, llm, mode="propose").run(inc)
    assert store.get_incident(inc.id).state == IncidentState.PROPOSED
    plan = store.latest_plan_for(inc.id)
    assert plan.kind == "automated"
    assert plan.actions[0].target == "web"
    assert plan.critic2_supported is True
    assert any(e["event"] == "proposed" for e in store.audit_for(inc.id))


def test_recurring_resolved_issue_replays_diagnosis_without_llm(store, bus):
    # First occurrence: diagnosed via the LLM, and it ended up RESOLVED.
    first = FakeLLM([_DIAG_OK, _CRITIC1_OK])
    inc1 = _incident(store)
    _pipeline(store, bus, first).run(inc1)
    store.update_state(inc1.id, IncidentState.RESOLVED)

    # Same issue (same dedup_key) recurs — replay inc1's diagnosis, no LLM call.
    inc2 = _incident(store)
    assert inc2.dedup_key == inc1.dedup_key
    second = FakeLLM([])   # any chat() would raise "FakeLLM exhausted"
    diag = _pipeline(store, bus, second).run(inc2)

    assert second.calls == []                          # LLM never called
    assert diag.root_cause == "mem limit"              # replayed from inc1
    assert diag.rounds == 0                            # no diagnose↔critic rounds
    events = [e["event"] for e in store.audit_for(inc2.id)]
    assert "diagnosed_from_cache" in events
    assert "diagnosed" not in events                   # the LLM path did not run


def test_recurring_unresolved_issue_diagnoses_fresh(store, bus):
    # First occurrence diagnosed but NOT resolved (left investigating in observe).
    inc1 = _incident(store)
    _pipeline(store, bus, FakeLLM([_DIAG_OK, _CRITIC1_OK])).run(inc1)
    assert store.get_incident(inc1.id).state != IncidentState.RESOLVED

    # Recurrence must re-run the LLM, since the prior attempt didn't hold.
    inc2 = _incident(store)
    fresh = FakeLLM([_DIAG_OK, _CRITIC1_OK])
    _pipeline(store, bus, fresh).run(inc2)

    assert len(fresh.calls) == 2                        # diagnose + critic ran fresh
    events = [e["event"] for e in store.audit_for(inc2.id)]
    assert "diagnosed" in events and "diagnosed_from_cache" not in events


def test_recurring_resolved_issue_replays_prior_fix_without_llm(store, bus):
    plan_responses = [
        _DIAG_OK, _CRITIC1_OK,
        LLMResponse(content='{"summary": "restart web", "actions": '
                            '[{"type": "restart", "target": "web", "rationale": "clear"}], '
                            '"manual_fix": ""}'),
        LLMResponse(content='{"supported": true, "reason": "reversible, in scope"}'),
    ]
    inc1 = _incident(store)
    _pipeline(store, bus, FakeLLM(plan_responses), mode="propose").run(inc1)
    assert store.get_incident(inc1.id).state == IncidentState.PROPOSED
    store.update_state(inc1.id, IncidentState.RESOLVED)   # the fix worked

    # Recurrence in propose mode replays the diagnosis AND the fix — still zero LLM.
    inc2 = _incident(store)
    second = FakeLLM([])
    _pipeline(store, bus, second, mode="propose").run(inc2)

    assert second.calls == []
    assert store.get_incident(inc2.id).state == IncidentState.PROPOSED
    plan2 = store.latest_plan_for(inc2.id)
    assert plan2.incident_id == inc2.id                # copied onto this incident
    assert plan2.kind == "automated" and plan2.actions[0].target == "web"


def test_reuse_disabled_diagnoses_fresh(store, bus):
    inc1 = _incident(store)
    _pipeline(store, bus, FakeLLM([_DIAG_OK, _CRITIC1_OK])).run(inc1)
    store.update_state(inc1.id, IncidentState.RESOLVED)   # resolved, but reuse is off

    inc2 = _incident(store)
    fresh = FakeLLM([_DIAG_OK, _CRITIC1_OK])
    Pipeline(store, bus,
             Config(mode="observe", max_critic_rounds=2,
                    reuse_recurring_diagnosis=False),
             fresh, DiagnosticTools(FakeSource(), None)).run(inc2)

    assert len(fresh.calls) == 2                        # diagnose + critic ran again
    assert "diagnosed" in [e["event"] for e in store.audit_for(inc2.id)]


def test_propose_and_explain_closes_no_fix(store, bus):
    llm = FakeLLM([
        _DIAG_OK, _CRITIC1_OK,
        LLMResponse(content='{"summary": "manual", "actions": [], '
                            '"manual_fix": "Raise mem_limit in compose and redeploy."}'),
    ])
    inc = _incident(store)
    _pipeline(store, bus, llm, mode="propose").run(inc)
    assert store.get_incident(inc.id).state == IncidentState.CLOSED_NO_FIX
    plan = store.latest_plan_for(inc.id)
    assert plan.kind == "manual"
    assert "mem_limit" in plan.manual_fix


def test_invalid_target_retries_then_proposes(store, bus):
    llm = FakeLLM([
        _DIAG_OK, _CRITIC1_OK,
        # round 1: hallucinated target → rejected by validation
        LLMResponse(content='{"summary": "p1", "actions": '
                            '[{"type": "restart", "target": "ghost"}], "manual_fix": ""}'),
        # round 2: valid target after feedback
        LLMResponse(content='{"summary": "p2", "actions": '
                            '[{"type": "restart", "target": "web"}], "manual_fix": ""}'),
        LLMResponse(content='{"supported": true, "reason": "ok"}'),
    ])
    inc = _incident(store)
    _pipeline(store, bus, llm, mode="propose").run(inc)
    assert store.get_incident(inc.id).state == IncidentState.PROPOSED
    assert store.latest_plan_for(inc.id).actions[0].target == "web"


def test_diagnosis_prose_is_redacted_before_storage(store, bus):
    from digdown.redactor import Redactor, SecretRegistry, MASK
    reg = SecretRegistry(); reg.register("leakedsecretvalue42")
    llm = FakeLLM([
        LLMResponse(content='{"summary": "leak", "root_cause": "saw leakedsecretvalue42 in logs", '
                            '"evidence": ["token leakedsecretvalue42"], "confidence": 0.6}'),
        _CRITIC1_OK,
    ])
    inc = _incident(store)
    Pipeline(store, bus, Config(max_critic_rounds=2),
             llm, DiagnosticTools(FakeSource(), None),
             redactor=Redactor(reg)).run(inc)
    d = store.latest_diagnosis_for(inc.id)
    assert "leakedsecretvalue42" not in d.root_cause
    assert MASK in d.root_cause
    assert all("leakedsecretvalue42" not in e for e in d.evidence)


def test_pipeline_streams_redacted_reasoning_steps(store, bus):
    from digdown.llm import ToolCall
    from digdown.redactor import Redactor, SecretRegistry

    secret = "SUPERSECRET42XYZ"
    reg = SecretRegistry()
    reg.register(secret)
    redactor = Redactor(reg)

    # Diagnostician makes one tool call (logs contain the secret) then answers.
    llm = FakeLLM([
        LLMResponse(tool_calls=[ToolCall(id="t1", name="get_container_state",
                                         arguments={"target": "web"})]),
        LLMResponse(content=f'{{"summary": "OOM due to {secret}", '
                             f'"root_cause": "mem limit", '
                             f'"evidence": ["exit 137"], "confidence": 0.8}}'),
        _CRITIC1_OK,
    ])
    inc = _incident(store)
    Pipeline(store, bus, Config(max_critic_rounds=2),
             llm, DiagnosticTools(FakeSource(), None),
             redactor=redactor).run(inc)

    events = [e for e in bus.events if e["type"] == "reasoning_step"]
    assert events, "expected reasoning_step events during diagnosis"
    assert all(e["incident_id"] == inc.id for e in events)
    assert all(secret not in (e["step"].get("content") or "") for e in events)


def test_tool_call_arguments_are_redacted_in_streamed_steps(store, bus):
    """Regression: tool-call function.arguments must not leak registered secrets."""
    import json as _json
    from digdown.llm import ToolCall
    from digdown.redactor import Redactor, SecretRegistry, MASK

    secret = "TOOLARG_SECRET_XYZ99"
    reg = SecretRegistry()
    reg.register(secret)
    redactor = Redactor(reg)

    # The FakeLLM emits a tool call whose arguments embed the secret string.
    # agent.py serialises tc.arguments via json.dumps, so the secret appears
    # in the "arguments" JSON string that _step_publisher must redact.
    llm = FakeLLM([
        LLMResponse(tool_calls=[ToolCall(id="t1", name="get_container_state",
                                         arguments={"target": "web",
                                                    "secret_field": secret})]),
        LLMResponse(content='{"summary": "OOM", "root_cause": "mem limit", '
                             '"evidence": ["exit 137"], "confidence": 0.8}'),
        _CRITIC1_OK,
    ])
    inc = _incident(store)
    Pipeline(store, bus, Config(max_critic_rounds=2),
             llm, DiagnosticTools(FakeSource(), None),
             redactor=redactor).run(inc)

    events = [e for e in bus.events if e["type"] == "reasoning_step"]
    assert events, "expected reasoning_step events"
    for e in events:
        step = e["step"]
        # content must not leak
        assert secret not in (step.get("content") or "")
        # tool_call arguments must not leak
        for tc in (step.get("tool_calls") or []):
            args_str = tc.get("function", {}).get("arguments", "")
            assert secret not in args_str, (
                f"secret leaked in tool_call arguments: {args_str!r}"
            )


def test_mark_diagnosis_failed_transitions_and_records(store, bus):
    llm = FakeLLM([])  # unused; we drive the failure path directly
    inc = _incident(store)
    pipe = _pipeline(store, bus, llm)

    pipe.mark_diagnosis_failed(inc, Exception("Error code: 429 rate_limit_error"))

    saved = store.get_incident(inc.id)
    assert saved.state == IncidentState.DIAGNOSIS_FAILED
    assert saved.failure_reason == "Rate-limited by the model API"
    events = [r["event"] for r in store.audit_for(inc.id)]
    assert "diagnosis_failed" in events
    failed = [r for r in store.audit_for(inc.id) if r["event"] == "diagnosis_failed"][0]
    assert "429" in failed["payload"]["error"]
    assert failed["payload"]["reason"] == "Rate-limited by the model API"
    assert any(e["type"] == "incident_updated" for e in bus.events)


def test_proposed_triggers_needs_approval_notification(store, bus):
    sent = []
    llm = FakeLLM([
        _DIAG_OK, _CRITIC1_OK,
        LLMResponse(content='{"summary": "restart web", "actions": '
                            '[{"type": "restart", "target": "web"}], "manual_fix": ""}'),
        LLMResponse(content='{"supported": true, "reason": "ok"}'),
    ])
    inc = _incident(store)
    Pipeline(store, bus, Config(mode="propose", max_critic_rounds=2), llm,
             DiagnosticTools(FakeSource(), None),
             notifier=Notifier(channels=[sent.append])).run(inc)
    assert any(p["kind"] == "needs_approval" for p in sent)


def test_confirm_no_backend_reuses_only_resolved_prior(store, bus):
    pipe = _pipeline(store, bus, FakeLLM([]))   # DiagnosticTools(..., None) → grafana is None
    inc = _incident(store)                      # docker OOM, no exception labels
    resolved = _incident(store); resolved.state = IncidentState.RESOLVED
    investigating = _incident(store); investigating.state = IncidentState.INVESTIGATING
    assert pipe._confirm_recurrence(inc, resolved) is True
    assert pipe._confirm_recurrence(inc, investigating) is False


# ---- every path through the pipeline has to finish somewhere ----

def _scripted_pipeline(store, bus, mode, unavailable=frozenset(), plan_actions=None):
    import json as _json

    from digdown.config import Config as _Config
    from digdown.llm import FakeLLM, LLMResponse
    from digdown.tools import DiagnosticTools
    diag = {"summary": "s", "root_cause": "r", "evidence": ["e"], "confidence": 0.9}
    ok = {"supported": True, "reason": "fine", "issues": []}
    responses = [LLMResponse(content=_json.dumps(diag)), LLMResponse(content=_json.dumps(ok))]
    if mode != "observe":
        plan = {"summary": "p", "actions": plan_actions or [], "manual_fix": "do it by hand"}
        responses += [LLMResponse(content=_json.dumps(plan)), LLMResponse(content=_json.dumps(ok))] * 2
    src = FakeSource()
    return Pipeline(store, bus, _Config(mode=mode, reuse_recurring_diagnosis=False),
                    FakeLLM(responses), DiagnosticTools(src), unavailable_actions=unavailable)


def test_explain_only_finishes_as_diagnosed_rather_than_diagnosing_forever(store, bus):
    inc = _incident(store)
    _scripted_pipeline(store, bus, "observe").run(inc)
    assert store.get_incident(inc.id).state == IncidentState.DIAGNOSED


def test_an_incident_closed_while_queued_is_not_diagnosed(store, bus):
    inc = _incident(store)
    store.update_state(inc.id, IncidentState.RESOLVED)
    pipeline = _scripted_pipeline(store, bus, "observe")
    assert pipeline.run(inc) is None
    assert store.get_incident(inc.id).state == IncidentState.RESOLVED
    assert store.latest_diagnosis_for(inc.id) is None


def test_a_fix_that_cannot_run_here_becomes_instructions(store, bus):
    from digdown.plan_models import ActionType
    inc = _incident(store)
    action = {"type": "recreate", "target": inc.target, "params": {}, "rationale": "r"}
    _scripted_pipeline(store, bus, "execute", unavailable={ActionType.RECREATE},
                       plan_actions=[action]).run(inc)
    assert store.get_incident(inc.id).state == IncidentState.CLOSED_NO_FIX
    assert store.latest_plan_for(inc.id).kind == "manual"


def test_recreate_is_only_offered_for_the_stack_the_compose_file_describes(store, bus):
    """It runs `docker compose -f <file> up <service>`. A same-named service in another
    stack would otherwise get the wrong container recreated."""
    from digdown.discovery import Project
    from digdown.models import ContainerState
    from digdown.plan_models import Action, ActionType

    class Source:
        def list_states(self):
            return [ContainerState(id="1", name="shop-db-1", service="db", project="shop", status="exited"),
                    ContainerState(id="2", name="other-db-1", service="db", project="other", status="exited")]

    from digdown.config import Config as _Config
    from digdown.llm import FakeLLM
    from digdown.tools import DiagnosticTools
    tools = DiagnosticTools(Source(), Project(name="shop", services=["db"]))
    pipeline = Pipeline(store, bus, _Config(), FakeLLM([]), tools, compose_project="shop")
    recreate = lambda t: [Action(type=ActionType.RECREATE, target=t)]  # noqa: E731
    assert pipeline._cannot_run(recreate("shop-db-1")) == []
    assert pipeline._cannot_run(recreate("other-db-1"))
    assert pipeline._cannot_run([Action(type=ActionType.RESTART, target="other-db-1")]) == []
    # A bare service name is ambiguous across stacks, so it isn't offered either.
    assert pipeline._cannot_run(recreate("db"))


def test_a_diagnosis_nobody_could_confirm_still_alerts_a_person(store, bus):
    # The container is still down and nothing is going to fix it, so someone must hear.
    seen = []
    llm = FakeLLM([
        LLMResponse(content='{"summary": "guess", "root_cause": "maybe memory"}'),
        LLMResponse(content='{"supported": false, "reason": "no evidence for memory"}'),
        LLMResponse(content='{"summary": "guess", "root_cause": "maybe memory"}'),
        LLMResponse(content='{"supported": false, "reason": "still no evidence"}'),
    ])
    pipeline = Pipeline(store, bus, Config(mode="execute", max_critic_rounds=2), llm,
                        DiagnosticTools(FakeSource(), None), notifier=Notifier(channels=[seen.append]))
    pipeline.run(_incident(store))
    assert [p["kind"] for p in seen] == ["needs_manual"]
    assert "couldn't confirm" in seen[0]["detail"] and "still no evidence" in seen[0]["detail"]


def test_a_problem_that_returns_after_a_fix_is_flagged_and_reported(store, bus):
    sent = []

    def incident():
        sig = Signal(source="grafana", type=SignalType.ALERT_FIRING,
                     target="shop-api", check="HighErrorRate")
        return store.add_incident(Incident(source="grafana", target="shop-api",
                                           signal=sig, dedup_key="grafana:fp1"))

    def pipeline(llm, notifier=None):
        return Pipeline(store, bus, Config(mode="observe", max_critic_rounds=2), llm,
                        DiagnosticTools(FakeSource()), notifier=notifier)

    first = incident()
    pipeline(FakeLLM([_DIAG_OK, _CRITIC1_OK])).run(first)
    store.update_state(first.id, IncidentState.RESOLVED)

    second = incident()
    pipeline(FakeLLM([]), Notifier(channels=[sent.append])).run(second)

    assert store.get_incident(second.id).recurred_after_fix is True
    assert any(p["kind"] == "regression" for p in sent)
