"""Typed records for execution. An Execution journals what the Executor
did: the pre-change snapshot, each step, whether verification passed, and the
outcome. It is persisted so a Digdown restart can reconcile in-flight work.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from .models import utcnow


class ExecResult(str, Enum):
    RESOLVED = "resolved"        # ran + verified healthy
    ROLLED_BACK = "rolled_back"  # ran, verification failed, reverted
    FAILED = "failed"            # an action errored; reverted what ran


class StepResult(BaseModel):
    type: str
    target: str
    ok: bool
    detail: str = ""
    rolled_back: bool = False


class Execution(BaseModel):
    id: Optional[int] = None
    incident_id: int
    created_at: datetime = Field(default_factory=utcnow)
    result: ExecResult
    verified: bool = False
    rolled_back: bool = False
    steps: list[StepResult] = Field(default_factory=list)
    snapshot: dict[str, Any] = Field(default_factory=dict)
    detail: str = ""


def without_env_file(snapshot: dict) -> dict:
    """A snapshot holds a copy of the .env file for each container, needed only to undo
    a fix. It has real secrets in it, so this is the version that's safe to keep or show."""
    return {target: {k: v for k, v in details.items() if k != "env_backup"}
            if isinstance(details, dict) else details
            for target, details in (snapshot or {}).items() if target != "env_backup"}
