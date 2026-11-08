"""The Redactor — strips secrets from text before it reaches the LLM, the store,
or the dashboard. Always on. Two layers:

1. A SecretRegistry of literal values discovered in .env / compose `environment`
   / secrets, so a known secret is masked even when echoed inside a log line.
2. Credential patterns (assignments, JWTs, connection strings, AWS keys) for
   secrets we never saw declared.

Tool output, logs, and app text are untrusted *data*; redaction is value-only and
never interprets them as instructions.
"""
from __future__ import annotations

import re
from typing import Any

MASK = "‹redacted›"

# Values that look like config flags, not secrets — never registered.
_TRIVIAL = {"true", "false", "none", "null", "yes", "no", "0", "1", "localhost",
            "db", "redis", "postgres", "development", "production"}

# Setting names whose values are secrets. Other settings, such as MODE=worker, stay
# readable: masking ordinary words would make the logs hard to diagnose from.
_SECRET_NAME = re.compile(r"(?i)(key|secret|token|pass|pwd|auth|credential|dsn|"
                          r"database_url|connection_string)")
# A long mix of letters and digits is treated as a secret whatever it's called.
_RANDOM_LOOKING = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9_+=-]{16,}$")

# Keys whose values are secrets regardless of length.
_SECRET_KEY = re.compile(
    r"(?i)\b([a-z0-9_]*(?:api[_-]?key|secret|token|password|passwd|pwd|"
    r"access[_-]?key|private[_-]?key|auth)[a-z0-9_]*)\s*[=:]\s*"
    r"(\"?)([^\s\"',]+)\2"
)
# Command-line flags such as --requirepass x or --password=x.
_SECRET_FLAG = re.compile(r"(?i)(--[a-z0-9-]*(?:pass|pwd|secret|token|api-?key)[a-z0-9-]*)(=|\s+)([^\s\"',]+)")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_CONN = re.compile(r"\b([a-z][a-z0-9+.\-]*://[^:\s/@]+:)([^@\s/]+)(@)")
_AWS = re.compile(r"\bAKIA[0-9A-Z]{16}\b")


class SecretRegistry:
    def __init__(self) -> None:
        self._values: set[str] = set()

    def register(self, value: Any) -> None:
        if not isinstance(value, str):
            return
        v = value.strip()
        if len(v) < 5 or v.lower() in _TRIVIAL:
            return
        self._values.add(v)

    def register_env(self, env: dict[str, str]) -> None:
        for v in (env or {}).values():
            self.register(v)

    def register_secret_settings(self, env: dict[str, str]) -> None:
        for key, value in (env or {}).items():
            if _SECRET_NAME.search(key) or _RANDOM_LOOKING.match(str(value or "").strip()):
                self.register(value)

    @property
    def values(self) -> set[str]:
        return self._values


class Redactor:
    def __init__(self, registry: SecretRegistry | None = None) -> None:
        self.registry = registry or SecretRegistry()

    def redact(self, text: Any) -> Any:
        if not isinstance(text, str):
            return text
        return self.redact_counted(text)[0]

    def redact_counted(self, text: str) -> tuple[str, int]:
        count = 0
        # 1) known literals first (longest first, so substrings don't shadow)
        for value in sorted(self.registry.values, key=len, reverse=True):
            if value and value in text:
                count += text.count(value)
                text = text.replace(value, MASK)
        # 2) pattern-based
        text, n = _SECRET_KEY.subn(lambda m: f"{m.group(1)}={MASK}", text)
        count += n
        text, n = _SECRET_FLAG.subn(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)
        count += n
        text, n = _JWT.subn(MASK, text)
        count += n
        text, n = _CONN.subn(lambda m: f"{m.group(1)}{MASK}{m.group(3)}", text)
        count += n
        text, n = _AWS.subn(MASK, text)
        count += n
        return text, count

    def redact_list(self, items: list[str]) -> list[str]:
        return [self.redact(i) for i in items]
