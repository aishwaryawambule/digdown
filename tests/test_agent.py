import pytest

from digdown.agent import Agent, AgentError
from digdown.llm import FakeLLM, LLMResponse, ToolCall
from digdown.tools import DiagnosticTools
from digdown.models import ContainerState


class FakeSource:
    def list_states(self):
        return [ContainerState(id="1", name="web", status="exited", exit_code=1)]
    def get_logs(self, name, lines=200):
        return "traceback...\nKeyError: DB_HOST"
    def inspect(self, name):
        return {"image": "web:1"}


def _tools():
    return DiagnosticTools(FakeSource(), None, log_tail_lines=100)


def test_agent_runs_tool_then_returns_structured_output():
    llm = FakeLLM([
        LLMResponse(tool_calls=[ToolCall(id="a", name="get_container_state",
                                         arguments={"target": "web"})]),
        LLMResponse(content='{"summary": "web exited", "confidence": 0.7}'),
    ])
    result = Agent(llm, _tools(), max_tool_calls=8).run("sys", "diagnose web")
    assert result.output["summary"] == "web exited"
    assert result.tool_calls_made == 1
    # transcript carries the tool observation for the trace
    assert any(m.get("role") == "tool" for m in result.transcript)


def test_agent_respects_tool_call_budget_then_forces_answer():
    llm = FakeLLM([
        LLMResponse(tool_calls=[ToolCall(id="1", name="get_topology", arguments={})]),
        LLMResponse(tool_calls=[ToolCall(id="2", name="get_topology", arguments={})]),
        LLMResponse(content='{"summary": "done"}'),  # forced final (tools withheld)
    ])
    result = Agent(llm, _tools(), max_tool_calls=2).run("sys", "go")
    assert result.tool_calls_made == 2
    assert result.output["summary"] == "done"
    # last chat call must have been made WITHOUT tools (budget exhausted)
    assert llm.calls[-1]["tools"] is None


def test_agent_parses_json_inside_code_fence():
    llm = FakeLLM([LLMResponse(content='```json\n{"summary": "x", "confidence": 0.9}\n```')])
    result = Agent(llm, _tools()).run("sys", "go")
    assert result.output["confidence"] == 0.9


def test_agent_salvages_invalid_json_escapes():
    # Small models over-escape: `\'` is illegal JSON and strict json.loads
    # rejects it. The parser should salvage it instead of throwing away an
    # otherwise-usable diagnosis (this is the real diagnosis_failed we hit).
    bad = r'''{"summary": "web exited", "root_cause": "should be 'sh -c \'exit 1\''", "confidence": 0.5}'''
    llm = FakeLLM([LLMResponse(content=bad)])
    result = Agent(llm, _tools()).run("sys", "go")
    assert result.output["summary"] == "web exited"
    assert result.output["confidence"] == 0.5


def test_agent_preserves_valid_json_escapes():
    # Valid escapes must round-trip unchanged — the salvage must not corrupt them.
    good = r'{"summary": "line1\nline2", "path": "C:\\tmp", "confidence": 0.5}'
    llm = FakeLLM([LLMResponse(content=good)])
    result = Agent(llm, _tools()).run("sys", "go")
    assert result.output["summary"] == "line1\nline2"
    assert result.output["path"] == "C:\\tmp"


def test_agent_raises_on_unparseable_final():
    llm = FakeLLM([LLMResponse(content="I could not produce JSON, sorry."), LLMResponse(content="I could not produce JSON, sorry."), LLMResponse(content="I could not produce JSON, sorry.")])
    with pytest.raises(AgentError):
        Agent(llm, _tools()).run("sys", "go")


def test_agent_streams_each_step_via_on_step():
    llm = FakeLLM([
        LLMResponse(tool_calls=[ToolCall(id="a", name="get_container_state",
                                         arguments={"target": "web"})]),
        LLMResponse(content='{"summary": "web exited"}'),
    ])
    steps = []
    result = Agent(llm, _tools(), max_tool_calls=8).run("sys", "go", on_step=steps.append)
    roles = [s.get("role") for s in steps]
    assert "tool" in roles                      # the observation was streamed
    assert roles[-1] == "assistant"             # final answer streamed last
    assert any(s.get("tool_calls") for s in steps)  # the reasoning+call streamed
    # streamed steps are exactly the non-system transcript messages, in order
    assert steps == [m for m in result.transcript if m.get("role") != "system"]


def test_agent_on_step_defaults_off():
    llm = FakeLLM([LLMResponse(content='{"summary": "x"}')])
    # no on_step → no error, behaviour unchanged
    result = Agent(llm, _tools()).run("sys", "go")
    assert result.output["summary"] == "x"


def test_agent_scopes_offered_tools_to_nonlocal_target():
    # When the incident target is not a known local container, the agent must be
    # offered a reduced toolset (no container-inspection tools) so it cannot
    # produce phantom "no container named X" observations.
    llm = FakeLLM([LLMResponse(content='{"summary": "x"}')])
    Agent(llm, _tools(), max_tool_calls=8).run("sys", "go", tool_target="remote-app")
    offered = {f["function"]["name"] for f in (llm.calls[0]["tools"] or [])}
    assert "get_container_state" not in offered
    assert "inspect_container" not in offered
    assert "get_topology" in offered


# ---- found scoring real runs: logs full of quotes make a small model write bad JSON ----


def test_a_badly_formed_answer_gets_a_second_chance():
    agent, llm = _agent_with(['{"summary": "bind() failed (98: "Address in use")"}', '{"summary": "port in use"}'])
    assert agent.run("system", "user").output == {"summary": "port in use"}
    assert "valid JSON" in llm.calls[1]["messages"][3]["content"]


def test_it_keeps_the_bad_answer_in_view_so_it_can_be_corrected():
    agent, llm = _agent_with(['{"summary": broken', '{"summary": "ok"}'])
    agent.run("system", "user")
    assert llm.calls[1]["messages"][2]["content"] == '{"summary": broken'


def test_it_gives_up_with_a_clear_error_after_a_few_tries():
    import pytest
    from digdown.agent import AgentError
    agent, llm = _agent_with(["nope", "still nope", "no", "never"])
    with pytest.raises(AgentError):
        agent.run("system", "user")
    assert len(llm.calls) <= 3


def test_a_good_answer_needs_no_retry():
    agent, llm = _agent_with(['{"summary": "fine"}'])
    assert agent.run("system", "user").output == {"summary": "fine"}
    assert len(llm.calls) == 1


def _agent_with(answers):
    from digdown.agent import Agent
    from digdown.llm import FakeLLM, LLMResponse
    llm = FakeLLM([LLMResponse(content=a) for a in answers])

    class NoTools:
        def schemas(self, target=None):
            return []
    return Agent(llm, NoTools(), max_tool_calls=4), llm
