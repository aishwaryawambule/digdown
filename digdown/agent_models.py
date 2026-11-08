"""Typed I/O for the agent core.

Each agent has a structured input/output so the pipeline is deterministic around
the LLM. RootCause is the Diagnostician's output; CriticVerdict is gate 1's
output; Diagnosis is the persisted record that combines them.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from .models import utcnow


class RootCause(BaseModel):
    """Diagnostician output — the cause, not a fix."""

    summary: str  # one-line, human-facing
    root_cause: str  # the underlying cause, in plain terms
    evidence: list[str] = Field(default_factory=list)  # concrete observations cited
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class CriticVerdict(BaseModel):
    """Critic gate-1 output — is the diagnosis supported by evidence?"""

    supported: bool
    reason: str
    issues: list[str] = Field(default_factory=list)  # what's weak/missing, if any


class Diagnosis(BaseModel):
    """Persisted diagnosis for an incident (Diagnostician + Critic combined)."""

    id: Optional[int] = None
    incident_id: int
    created_at: datetime = Field(default_factory=utcnow)
    summary: str
    root_cause: str
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    rounds: int = 1  # how many diagnose↔critic rounds it took
    critic_supported: bool = False
    critic_reason: str = ""
