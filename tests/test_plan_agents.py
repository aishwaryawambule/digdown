from digdown.agents import Planner, PlanCritic
from digdown.agent_models import CriticVerdict, RootCause
from digdown.plan_models import ActionType, PlanProposal
from digdown.llm import FakeLLM, LLMResponse
from digdown.tools import DiagnosticTools
from digdown.models import ContainerState, Incident, Signal, SignalType


class FakeSource:
    def list_states(self):
        return [ContainerState(id="1", name="worker", status="exited", exit_code=1)]
    def get_logs(self, name, lines=200):
        return "KeyError: STRIPE_KEY\n"
    def inspect(self, name):
        return {"image": "worker:1"}


def _incident():
    sig = Signal(source="docker", type=SignalType.CRASH, target="worker",
                 detail={"exit_code": 1})
    return Incident(id=5, source="docker", target="worker", signal=sig,
                    dedup_key=sig.dedup_key)


def _rc():
    return RootCause(summary="missing env", root_cause="STRIPE_KEY unset",
                     evidence=["KeyError"], confidence=0.8)


def _tools():
    return DiagnosticTools(FakeSource(), None)


def test_planner_returns_automated_plan():
    llm = FakeLLM([LLMResponse(content=(
        '{"summary": "restart worker", "actions": '
        '[{"type": "restart", "target": "worker", "rationale": "clear state"}], '
        '"manual_fix": ""}'
    ))])
    p = Planner(llm, _tools()).plan(_incident(), _rc(), {"worker"})
    assert isinstance(p, PlanProposal)
    assert p.actions[0].type == ActionType.RESTART
    assert p.actions[0].target == "worker"


def test_planner_can_propose_and_explain():
    llm = FakeLLM([LLMResponse(content=(
        '{"summary": "no safe automated fix", "actions": [], '
        '"manual_fix": "Set STRIPE_KEY in the compose env and redeploy."}'
    ))])
    p = Planner(llm, _tools()).plan(_incident(), _rc(), {"worker"})
    assert p.actions == []
    assert "STRIPE_KEY" in p.manual_fix


def test_planner_prompt_lists_targets_and_feedback():
    llm = FakeLLM([LLMResponse(content='{"summary": "s", "actions": []}')])
    Planner(llm, _tools()).plan(_incident(), _rc(), {"worker", "db"},
                                feedback="target was invalid last time")
    user = next(m for m in llm.calls[0]["messages"] if m["role"] == "user")
    assert "worker" in user["content"]
    assert "invalid last time" in user["content"]


def test_plan_critic_returns_verdict():
    llm = FakeLLM([LLMResponse(content=(
        '{"supported": true, "reason": "reversible and in scope"}'
    ))])
    proposal = PlanProposal(summary="restart", actions=[])
    v = PlanCritic(llm, _tools()).review(_incident(), _rc(), proposal)
    assert isinstance(v, CriticVerdict)
    assert v.supported is True
