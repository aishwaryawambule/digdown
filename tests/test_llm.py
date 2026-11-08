import pytest

from digdown.llm import (
    ClaudeClient,
    FakeLLM,
    LLMResponse,
    OpenAIClient,
    ToolCall,
    friendly_diagnosis_error,
    make_llm,
)


def test_fake_llm_returns_scripted_and_records_calls():
    fake = FakeLLM([
        LLMResponse(tool_calls=[ToolCall(id="1", name="get_logs", arguments={"target": "web"})]),
        LLMResponse(content='{"ok": true}'),
    ])
    r1 = fake.chat([{"role": "user", "content": "hi"}], tools=[{"x": 1}])
    assert r1.tool_calls[0].name == "get_logs"
    r2 = fake.chat([{"role": "user", "content": "more"}])
    assert r2.content == '{"ok": true}'
    assert len(fake.calls) == 2
    assert fake.calls[0]["tools"] == [{"x": 1}]


def test_fake_llm_raises_when_exhausted():
    fake = FakeLLM([LLMResponse(content="only one")])
    fake.chat([])
    with pytest.raises(RuntimeError):
        fake.chat([])


def test_openai_client_maps_message_with_tool_calls():
    # a minimal stand-in for the openai SDK message object
    class _Fn:
        name = "get_logs"
        arguments = '{"target": "db", "lines": 50}'

    class _TC:
        id = "call_1"
        function = _Fn()

    class _Msg:
        content = None
        tool_calls = [_TC()]

    resp = OpenAIClient._to_response(_Msg())
    assert resp.content is None
    assert resp.tool_calls[0].name == "get_logs"
    assert resp.tool_calls[0].arguments == {"target": "db", "lines": 50}


def test_openai_client_maps_plain_content():
    class _Msg:
        content = '{"summary": "x"}'
        tool_calls = None

    resp = OpenAIClient._to_response(_Msg())
    assert resp.content == '{"summary": "x"}'
    assert resp.tool_calls == []


# ---- ClaudeClient (Anthropic adapter) ----
#
# These exercise the pure translation surface only — no `anthropic` import and
# no network — exactly as the OpenAIClient tests above stay off the wire.


def test_claude_client_splits_system_and_converts_messages():
    system, conv = ClaudeClient._split_system([
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "diagnose"},
        {"role": "assistant", "content": "thinking",
         "tool_calls": [{"id": "call_1", "type": "function",
                         "function": {"name": "get_recent_logs",
                                      "arguments": '{"target": "web"}'}}]},
        {"role": "tool", "tool_call_id": "call_1", "name": "get_recent_logs",
         "content": "boom"},
    ])

    assert system == "be terse"
    # system message is lifted out; the rest become Anthropic-shaped turns
    assert conv[0] == {"role": "user", "content": "diagnose"}
    # assistant turn carries a text block then a tool_use block
    assert conv[1]["role"] == "assistant"
    assert conv[1]["content"][0] == {"type": "text", "text": "thinking"}
    assert conv[1]["content"][1] == {
        "type": "tool_use", "id": "call_1",
        "name": "get_recent_logs", "input": {"target": "web"}}
    # the tool message becomes a user turn with a tool_result block
    assert conv[2] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "boom"}]}


def test_claude_client_merges_consecutive_tool_results():
    # Two parallel tool calls produce two `role: tool` messages; Anthropic needs
    # both tool_result blocks in a SINGLE following user turn.
    _, conv = ClaudeClient._split_system([
        {"role": "assistant", "content": None,
         "tool_calls": [
             {"id": "a", "type": "function",
              "function": {"name": "t", "arguments": "{}"}},
             {"id": "b", "type": "function",
              "function": {"name": "t", "arguments": "{}"}},
         ]},
        {"role": "tool", "tool_call_id": "a", "name": "t", "content": "ra"},
        {"role": "tool", "tool_call_id": "b", "name": "t", "content": "rb"},
    ])

    # assistant turn has no text block (content was None), just two tool_use
    assert [b["type"] for b in conv[0]["content"]] == ["tool_use", "tool_use"]
    # both results coalesce into one user turn
    assert conv[1] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "a", "content": "ra"},
        {"type": "tool_result", "tool_use_id": "b", "content": "rb"},
    ]}


def test_claude_client_converts_openai_tools_to_anthropic():
    openai_tools = [{
        "type": "function",
        "function": {
            "name": "get_recent_logs",
            "description": "Recent log lines.",
            "parameters": {"type": "object",
                           "properties": {"target": {"type": "string"}},
                           "required": ["target"]},
        },
    }]
    tools = ClaudeClient._to_anthropic_tools(openai_tools)
    assert tools == [{
        "name": "get_recent_logs",
        "description": "Recent log lines.",
        "input_schema": {"type": "object",
                         "properties": {"target": {"type": "string"}},
                         "required": ["target"]},
    }]


def test_claude_client_to_response_maps_text_blocks():
    class _Block:
        type = "text"
        text = '{"summary": "x"}'

    class _Msg:
        content = [_Block()]

    resp = ClaudeClient._to_response(_Msg())
    assert resp.content == '{"summary": "x"}'
    assert resp.tool_calls == []


def test_claude_client_to_response_maps_tool_use_blocks():
    class _Use:
        type = "tool_use"
        id = "call_1"
        name = "get_recent_logs"
        input = {"target": "db", "lines": 50}

    class _Msg:
        content = [_Use()]

    resp = ClaudeClient._to_response(_Msg())
    assert resp.content is None
    assert resp.tool_calls[0] == ToolCall(
        id="call_1", name="get_recent_logs",
        arguments={"target": "db", "lines": 50})


def test_make_llm_defaults_to_openai_compatible_client():
    llm = make_llm("openai", base_url="http://localhost:11434/v1",
                   api_key="not-needed", model="qwen2.5:7b")
    assert isinstance(llm, OpenAIClient)
    assert llm.model == "qwen2.5:7b"


def test_make_llm_selects_claude_provider():
    # setup-token (OAuth) auth — Digdown's `rn no api key` path
    llm = make_llm("claude", base_url="", api_key="", model="claude-opus-4-8",
                   auth_token="sk-ant-oat01-test")
    assert isinstance(llm, ClaudeClient)
    assert llm.model == "claude-opus-4-8"


def test_claude_client_sends_oauth_beta_header_for_setup_token():
    # A setup-token is a Bearer credential; /v1/messages needs the oauth beta.
    llm = ClaudeClient(model="claude-opus-4-8", auth_token="sk-ant-oat01-test")
    assert llm.extra_headers == {"anthropic-beta": "oauth-2025-04-20"}


def test_claude_client_omits_oauth_beta_header_for_api_key():
    llm = ClaudeClient(model="claude-opus-4-8", api_key="sk-ant-api-test")
    assert llm.extra_headers == {}


class _FakeAPIError(Exception):
    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


def test_friendly_diagnosis_error_maps_known_causes():
    assert friendly_diagnosis_error(_FakeAPIError("Error", 429)) == "Rate-limited by the model API"
    assert friendly_diagnosis_error(Exception("Error code: 429 rate_limit_error")) == "Rate-limited by the model API"
    assert friendly_diagnosis_error(_FakeAPIError("nope", 401)) == "Model API authentication failed"
    assert friendly_diagnosis_error(_FakeAPIError("nope", 403)) == "Model API authentication failed"
    assert friendly_diagnosis_error(Exception("Request timed out")) == "Couldn't reach the model API"
    assert friendly_diagnosis_error(Exception("Connection error")) == "Couldn't reach the model API"
    assert friendly_diagnosis_error(Exception("something weird")) == "Diagnosis failed"
