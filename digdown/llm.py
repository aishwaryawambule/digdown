"""Thin LLM providers behind one tiny `LLMClient` surface the agent loop drives.

Two backends, same `chat()` contract:

* `OpenAIClient` — any OpenAI-compatible endpoint (Ollama / LiteLLM / OpenRouter
  / vLLM / Claude-via-proxy). This is the original, unchanged path.
* `ClaudeClient` — Anthropic's first-party Messages API. It translates the
  OpenAI-shaped transcript the loop produces into Anthropic's format and maps
  the reply back, so the rest of the system stays provider-agnostic.

No LangChain — a transparent, minimal surface. Tests use FakeLLM and the pure
translation staticmethods; neither `openai` nor `anthropic` is imported until
its client is actually constructed.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: Optional[str] = None
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMClient(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> LLMResponse: ...


class FakeLLM:
    """Scripted client for tests: returns `responses` in order, records calls."""

    def __init__(self, responses: list[LLMResponse]):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, tools=None) -> LLMResponse:
        self.calls.append({"messages": messages, "tools": tools})
        if not self._responses:
            raise RuntimeError("FakeLLM exhausted: no scripted response left")
        return self._responses.pop(0)


class OpenAIClient:
    """Adapter over the `openai` SDK pointed at any compatible base URL."""

    def __init__(self, base_url: str, api_key: str, model: str,
                 temperature: float = 0.1):
        from openai import OpenAI  # lazy: keeps `openai` optional for tests

        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.temperature = temperature

    @staticmethod
    def _to_response(message: Any) -> LLMResponse:
        """Map an openai chat message object → our provider-agnostic response."""
        tool_calls: list[ToolCall] = []
        for tc in (getattr(message, "tool_calls", None) or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except (ValueError, TypeError):
                args = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        return LLMResponse(content=getattr(message, "content", None),
                           tool_calls=tool_calls)

    def chat(self, messages, tools=None) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        completion = self._client.chat.completions.create(**kwargs)
        return self._to_response(completion.choices[0].message)


_OAUTH_BETA = "oauth-2025-04-20"  # /v1/messages requires this when auth is a Bearer token


class ClaudeClient:
    """Adapter over the `anthropic` SDK implementing the `LLMClient` protocol.

    Auth (no API key required): pass a `auth_token` — the OAuth token from
    `claude setup-token` (`sk-ant-oat01-...`) — and the SDK sends it as
    `Authorization: Bearer` plus the `oauth-2025-04-20` beta header that
    `/v1/messages` needs. An `api_key` works too (for when you have one). With
    neither, the SDK resolves a credential from the environment
    (`ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_API_KEY`), so nothing secret has to live
    in digdown.yml.

    Note: Opus 4.x rejects sampling params (`temperature` 400s), so this client
    deliberately does not send one — it relies on the strict-JSON system prompts
    the agents already use.
    """

    def __init__(self, model: str, api_key: str = "", auth_token: str = "",
                 max_tokens: int = 4096, base_url: str = ""):
        from anthropic import Anthropic  # lazy: keeps `anthropic` optional for tests

        key = api_key if api_key and api_key != "not-needed" else ""
        # Decide whether we're authenticating with a Bearer token (setup-token /
        # ANTHROPIC_AUTH_TOKEN) — that's the case that needs the oauth beta header.
        bearer = bool(auth_token) or (
            not key
            and bool(os.environ.get("ANTHROPIC_AUTH_TOKEN"))
            and not os.environ.get("ANTHROPIC_API_KEY")
        )
        opts: dict[str, Any] = {}
        if auth_token:
            opts["auth_token"] = auth_token
        elif key:
            opts["api_key"] = key
        if base_url:
            opts["base_url"] = base_url

        self._client = Anthropic(**opts)
        self.model = model
        self.max_tokens = max_tokens
        self.extra_headers: dict[str, str] = (
            {"anthropic-beta": _OAUTH_BETA} if bearer else {})

    @staticmethod
    def _split_system(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        """Lift `system` turns to Anthropic's top-level system string and convert
        the rest of the OpenAI-shaped transcript into Anthropic message dicts.

        Consecutive `tool` results are coalesced into one user turn — Anthropic
        requires every `tool_use` in an assistant turn to be answered by
        `tool_result` blocks in the single following user turn.
        """
        system_parts: list[str] = []
        conv: list[dict[str, Any]] = []
        for m in messages:
            role = m.get("role")
            if role == "system":
                if m.get("content"):
                    system_parts.append(m["content"])
            elif role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id"),
                    "content": m.get("content") or "",
                }
                if conv and conv[-1]["role"] == "user" and isinstance(
                        conv[-1]["content"], list):
                    conv[-1]["content"].append(block)
                else:
                    conv.append({"role": "user", "content": [block]})
            elif role == "assistant" and m.get("tool_calls"):
                blocks: list[dict[str, Any]] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m["tool_calls"]:
                    fn = tc["function"]
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except (ValueError, TypeError):
                        args = {}
                    blocks.append({"type": "tool_use", "id": tc["id"],
                                   "name": fn["name"], "input": args})
                conv.append({"role": "assistant", "content": blocks})
            elif role == "assistant":
                conv.append({"role": "assistant", "content": m.get("content") or ""})
            else:  # user
                conv.append({"role": "user", "content": m.get("content") or ""})
        return "\n\n".join(system_parts), conv

    @staticmethod
    def _to_anthropic_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """OpenAI function schemas → Anthropic tool schemas."""
        out: list[dict[str, Any]] = []
        for t in tools:
            fn = t.get("function", t)
            out.append({
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters",
                                       {"type": "object", "properties": {}}),
            })
        return out

    @staticmethod
    def _to_response(message: Any) -> LLMResponse:
        """Map an Anthropic message's content blocks → our provider-agnostic
        response. Text blocks concatenate into `content`; `tool_use` blocks
        become ToolCalls (their `input` is already a parsed dict)."""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in (getattr(message, "content", None) or []):
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(getattr(block, "text", "") or "")
            elif btype == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id, name=block.name,
                    arguments=dict(getattr(block, "input", None) or {})))
        return LLMResponse(content="".join(text_parts) if text_parts else None,
                           tool_calls=tool_calls)

    def chat(self, messages, tools=None) -> LLMResponse:
        system, conv = self._split_system(messages)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": conv,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = self._to_anthropic_tools(tools)
            kwargs["tool_choice"] = {"type": "auto"}
        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers
        message = self._client.messages.create(**kwargs)
        return self._to_response(message)


def friendly_diagnosis_error(exc: Exception) -> str:
    """Map a diagnosis exception to a short, human-readable cause for the
    dashboard. The raw exception is still recorded in the audit log."""
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    if status == 429 or "rate_limit" in text or "429" in text:
        return "Rate-limited by the model API"
    if status in (401, 403) or "authentication" in text or "401" in text or "403" in text:
        return "Model API authentication failed"
    if "timeout" in text or "timed out" in text or "connection" in text:
        return "Couldn't reach the model API"
    return "Diagnosis failed"


def make_llm(provider: str, *, base_url: str, api_key: str, model: str,
             temperature: float = 0.1, max_tokens: int = 4096,
             auth_token: str = "") -> LLMClient:
    """Pick the backend from config. `openai` (the default) keeps the original
    OpenAI-compatible path (Ollama, etc.); `claude`/`anthropic` uses the
    first-party Messages API. `base_url` is intentionally not forwarded to the
    Claude client — point it elsewhere with the `ANTHROPIC_BASE_URL` env var."""
    if (provider or "openai").lower() in ("claude", "anthropic"):
        return ClaudeClient(model=model, api_key=api_key, auth_token=auth_token,
                            max_tokens=max_tokens)
    return OpenAIClient(base_url, api_key, model, temperature)
