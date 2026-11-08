"""Typed I/O for the remediation stage.

A plan is either *automated* — a list of allow-listed, parameterized Actions to
approve — or *manual* (propose-and-explain): no executable action, just a
recommended human fix. The Planner emits a PlanProposal; the validated, critic-
checked result is persisted as a Plan. Nothing here executes anything.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from .models import utcnow


class ActionType(str, Enum):
    RESTART = "restart"
    RECREATE = "recreate"
    START = "start"
    SET_ENV_VAR = "set_env_var"
    SET_RESOURCE_LIMIT = "set_resource_limit"
    # Never offered to the AI: a fixed rule proposes it, for a full disk only.
    CLEAN_DOCKER = "clean_docker"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Action(BaseModel):
    type: ActionType
    target: str
    params: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class PlanProposal(BaseModel):
    """Planner output — actions to take, or a manual fix when none is safe."""

    summary: str
    actions: list[Action] = Field(default_factory=list)
    manual_fix: str = ""


class Plan(BaseModel):
    """Persisted, validated remediation plan."""

    id: Optional[int] = None
    incident_id: int
    created_at: datetime = Field(default_factory=utcnow)
    summary: str
    kind: str = "automated"  # "automated" (approvable) | "manual" (propose-and-explain)
    risk: RiskLevel = RiskLevel.LOW
    actions: list[Action] = Field(default_factory=list)
    manual_fix: str = ""
    critic2_supported: bool = False
    critic2_reason: str = ""


class Approval(BaseModel):
    """A human decision on a proposed plan."""

    id: Optional[int] = None
    incident_id: int
    created_at: datetime = Field(default_factory=utcnow)
    decision: str  # "approve" | "reject"
    identity: str = "you"
    note: str = ""
