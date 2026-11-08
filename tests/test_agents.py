from digdown.agents import Diagnostician, Critic
from digdown.agent_models import RootCause, CriticVerdict
from digdown.llm import FakeLLM, LLMResponse
from digdown.tools import DiagnosticTools
from digdown.models import ContainerState, Incident, Signal, SignalType


class FakeSource:
    def list_states(self):
        return [ContainerState(id="1", name="web", status="exited", exit_code=137,
                               oom_killed=True)]
    def get_logs(self, name, lines=200):
        return "Killed\n"
    def inspect(self, name):
        return {"image": "web:1"}


def _incident():
    sig = Signal(source="docker", type=SignalType.OOM, target="web",
                 detail={"exit_code": 137})
    return Incident(id=1, source="docker", target="web", signal=sig,
                    dedup_key=sig.dedup_key)


def _tools():
    return DiagnosticTools(FakeSource(), None, log_tail_lines=100)


def test_diagnostician_returns_rootcause():
    llm = FakeLLM([LLMResponse(content=(
        '{"summary": "OOM kill", "root_cause": "memory limit too low", '
        '"evidence": ["exit 137", "OOMKilled"], "confidence": 0.8}'
    ))])
    rc = Diagnostician(llm, _tools()).diagnose(_incident())
    assert isinstance(rc, RootCause)
    assert rc.root_cause == "memory limit too low"
    assert rc.confidence == 0.8


def test_diagnostician_coerces_missing_fields():
    llm = FakeLLM([LLMResponse(content='{"root_cause": "x"}')])
    rc = Diagnostician(llm, _tools()).diagnose(_incident())
    assert rc.evidence == []
    assert rc.summary  # filled with a fallback rather than crashing


def test_critic_returns_verdict():
    llm = FakeLLM([LLMResponse(content=(
        '{"supported": false, "reason": "no evidence of memory pressure", '
        '"issues": ["no metrics cited"]}'
    ))])
    rc = RootCause(summary="s", root_cause="r", evidence=[], confidence=0.6)
    v = Critic(llm, _tools()).review(_incident(), rc)
    assert isinstance(v, CriticVerdict)
    assert v.supported is False
    assert "no metrics cited" in v.issues


def test_diagnostician_passes_feedback_into_prompt():
    llm = FakeLLM([LLMResponse(content='{"summary": "s", "root_cause": "r"}')])
    Diagnostician(llm, _tools()).diagnose(_incident(), feedback="cite the logs")
    user_msg = next(m for m in llm.calls[0]["messages"] if m["role"] == "user")
    assert "cite the logs" in user_msg["content"]


def test_diagnostician_prompt_is_source_agnostic():
    # The prompt must not frame every incident as a local container: it must tell
    # the agent to reason from the signal detail, and that empty tool output for a
    # non-local target (e.g. a grafana alert on a remote app) is EXPECTED and
    # is NOT evidence the target was deleted/missing.
    from digdown.agents import DIAGNOSTICIAN_SYSTEM
    p = DIAGNOSTICIAN_SYSTEM.lower()
    assert "detail" in p                                   # reason from the signal detail
    assert "not" in p and ("deleted" in p or "missing" in p)  # don't conclude deleted
    assert "grafana" in p or "external" in p or "remote" in p  # non-local targets exist


def test_critic_prompt_accepts_payload_evidence():
    # The critic must accept a payload-based diagnosis for non-local incidents
    # rather than demanding container evidence.
    from digdown.agents import CRITIC_SYSTEM
    p = CRITIC_SYSTEM.lower()
    assert "detail" in p or "payload" in p


def _grafana_incident():
    sig = Signal(source="grafana", type=SignalType.ALERT_FIRING,
                 target="wiki-web", check="WikiUnreachable",
                 detail={"summary": "the wiki is not answering"})
    return Incident(id=2, source="grafana", target="wiki-web",
                    signal=sig, dedup_key=sig.dedup_key)


def test_diagnostician_scopes_tools_for_nonlocal_grafana_target():
    # End-to-end: a grafana incident whose target is not a local container must
    # reach the LLM WITHOUT the container-inspection tools — so the agent can't
    # report "no container named X" / "no inspect data" as evidence.
    llm = FakeLLM([LLMResponse(content='{"summary": "s", "root_cause": "r"}')])
    Diagnostician(llm, _tools()).diagnose(_grafana_incident())
    offered = {f["function"]["name"] for f in (llm.calls[0]["tools"] or [])}
    assert "get_container_state" not in offered
    assert "inspect_container" not in offered


def test_critic_scopes_tools_for_nonlocal_grafana_target():
    llm = FakeLLM([LLMResponse(content='{"supported": true, "reason": "ok"}')])
    rc = RootCause(summary="s", root_cause="r", evidence=[], confidence=0.6)
    Critic(llm, _tools()).review(_grafana_incident(), rc)
    offered = {f["function"]["name"] for f in (llm.calls[0]["tools"] or [])}
    assert "get_container_state" not in offered
    assert "inspect_container" not in offered


def test_planner_prompt_advertises_set_resource_limit():
    from digdown.agents import PLANNER_SYSTEM
    assert "set_resource_limit" in PLANNER_SYSTEM
    assert "mem_limit_bytes" in PLANNER_SYSTEM
