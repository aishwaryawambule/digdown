"""A thin, transparent ReAct loop: reason → call tool → observe → repeat, until
the model returns a final JSON answer or the tool-call budget is spent.

Provider-agnostic (drives any `LLMClient`). The full message transcript is
returned so the pipeline can persist an inspectable reasoning trace.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .llm import LLMClient, LLMResponse


class AgentError(Exception):
    pass


@dataclass
class AgentResult:
    output: dict[str, Any]
    transcript: list[dict[str, Any]] = field(default_factory=list)
    tool_calls_made: int = 0


_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
# A backslash that does NOT begin a valid JSON escape (\" \\ \/ \b \f \n \r \t
# \uXXXX). Small models routinely over-escape (e.g. \' ), which strict json
# rejects; doubling these backslashes is lossless and always yields valid JSON.
_BAD_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')


def _extract_json(content: str | None) -> dict[str, Any]:
    if not content:
        raise AgentError("model returned no content for the final answer")
    text = content.strip()
    m = _FENCE.search(text)
    if m:
        text = m.group(1).strip()
    # Try the whole string, then the outermost {...} (drops surrounding prose);
    # for each, attempt strict parse first, then a salvage pass that repairs
    # invalid escape sequences a small model may have emitted.
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for cand in candidates:
        for attempt in (cand, _BAD_ESCAPE.sub(r"\\\\", cand)):
            try:
                return json.loads(attempt)
            except ValueError:
                pass
    raise AgentError(f"could not parse JSON from model output: {content[:200]!r}")


def _assistant_tool_call_msg(resp: LLMResponse) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": resp.content,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in resp.tool_calls
        ],
    }


_MAX_REPAIRS = 2
_REPAIR_REQUEST = ("That wasn't valid JSON. Reply again with ONLY the JSON object. Write any "
                   "double quote inside a string as \\\".")


class Agent:
    def __init__(self, llm: LLMClient, tools, max_tool_calls: int = 8):
        self.llm = llm
        self.tools = tools
        self.max_tool_calls = max_tool_calls

    def run(self, system: str, user: str, on_step=None,
            tool_target: Any = None) -> AgentResult:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        def _emit():
            if on_step is not None:
                on_step(messages[-1])

        # Emit the user message once it's added to the transcript
        if on_step is not None:
            on_step(messages[1])  # messages[1] is the user message

        calls = 0
        repairs = 0
        # +2 guards against a model that keeps emitting empty turns.
        for _ in range(self.max_tool_calls + 2 + _MAX_REPAIRS):
            offer_tools = calls < self.max_tool_calls
            schemas = self.tools.schemas(tool_target) if offer_tools else None
            resp = self.llm.chat(messages, tools=schemas)

            if resp.tool_calls and offer_tools:
                messages.append(_assistant_tool_call_msg(resp))
                _emit()
                for tc in resp.tool_calls:
                    calls += 1
                    observation = self.tools.call(tc.name, tc.arguments)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "content": observation,
                    })
                    _emit()
                continue

            messages.append({"role": "assistant", "content": resp.content})
            _emit()
            try:
                output = _extract_json(resp.content)
            except AgentError:
                # Logs are full of quotes, and small models copy them into strings
                # unescaped. Asking again costs far less than losing the diagnosis.
                if repairs >= _MAX_REPAIRS:
                    raise
                repairs += 1
                messages.append({"role": "user", "content": _REPAIR_REQUEST})
                continue
            return AgentResult(output=output, transcript=messages, tool_calls_made=calls)

        raise AgentError("agent did not converge within its step budget")
