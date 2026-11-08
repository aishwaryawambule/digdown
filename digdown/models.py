from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class HealthStatus(str, Enum):
    NONE = "none"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    STARTING = "starting"


class ContainerState(BaseModel):
    id: str
    name: str
    service: Optional[str] = None          # compose service label, if any
    project: str = ""                      # compose project label (groups the fleet)
    status: str                            # running, exited, restarting, created, paused, dead
    health: HealthStatus = HealthStatus.NONE
    exit_code: Optional[int] = None
    restart_count: int = 0
    oom_killed: bool = False
    image: str = ""
    finished_at: Optional[datetime] = None  # when it last stopped running; None if unknown
    depends_on: list[str] = Field(default_factory=list)  # services in its own stack, from Compose's label
    restart_policy: str = ""               # HostConfig RestartPolicy.Name: ""|no|always|unless-stopped|on-failure
    # Live resource sample — None when not running / not sampled / no limit.
    cpu_percent: Optional[float] = None
    mem_bytes: Optional[int] = None
    mem_limit_bytes: Optional[int] = None   # the CONFIGURED limit (HostConfig.Memory); None if unlimited
    mem_percent: Optional[float] = None


class SignalType(str, Enum):
    CRASH = "crash"
    OOM = "oom"
    UNHEALTHY = "unhealthy"
    RESTART_LOOP = "restart_loop"
    STOPPED = "stopped"
    PAUSED = "paused"
    CREATED = "created"
    DISK_FULL = "disk_full"
    MEMORY_PRESSURE = "memory_pressure"
    HTTP_ERROR = "http_error"
    SLOW_RESPONSE = "slow_response"
    LOG_ERROR_SPIKE = "log_error_spike"
    ALERT_FIRING = "alert_firing"


class Signal(BaseModel):
    source: str
    type: SignalType
    target: str
    check: Optional[str] = None             # discriminator for app-level checks
    detail: dict[str, Any] = Field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        base = f"{self.source}:{self.target}:{self.type.value}"
        return f"{base}:{self.check}" if self.check else base


def error_signature(signal: Signal) -> str:
    """A stable identity for *the same problem* across recurrences, derived from
    the signal at detection time (no LLM): the check that raised it, or failing
    that, the kind of failure."""
    kind = signal.check or signal.type.value
    return f"{signal.target}|{kind}".strip().lower()


class IncidentState(str, Enum):
    DETECTED = "detected"
    TRIAGED = "triaged"
    INVESTIGATING = "investigating"
    DIAGNOSED = "diagnosed"             # cause found, and Digdown isn't set to propose a fix
    DIAGNOSIS_FAILED = "diagnosis_failed"
    PROPOSED = "proposed"
    APPROVED = "approved"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    RESOLVED = "resolved"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    CLOSED_NO_FIX = "closed_no_fix"
    MUTED = "muted"


TERMINAL_STATES: set[IncidentState] = {
    IncidentState.RESOLVED,
    IncidentState.REJECTED,
    IncidentState.ROLLED_BACK,
    IncidentState.FAILED,
    IncidentState.CLOSED_NO_FIX,
    IncidentState.MUTED,  # silenced on purpose — done, won't reopen or re-diagnose
}


class Mute(BaseModel):
    """A runtime silence rule: a target prefix Digdown should not act on.

    `pattern` matches a target by exact equality or prefix, so muting `blog`
    silences the whole `blog-*` stack. Set live via the API/dashboard and stored
    in the DB; the watcher drops matching signals before they become incidents.
    """

    pattern: str
    created_at: datetime = Field(default_factory=utcnow)
    identity: str = "you"
    note: str = ""

    def matches(self, target: str) -> bool:
        return bool(target) and (target == self.pattern
                                 or target.startswith(self.pattern))


class Incident(BaseModel):
    id: Optional[int] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    state: IncidentState = IncidentState.DETECTED
    source: str
    target: str
    signal: Signal
    dedup_key: str
    parent_incident_id: Optional[int] = None  # set when this is a cascade dependent
    failure_reason: str = ""  # human-readable cause when state is DIAGNOSIS_FAILED
    error_signature: str = ""        # stable identity of the underlying problem (see error_signature())
    recurred_after_fix: bool = False  # set when a RESOLVED incident's problem recurred (regression)

    @model_validator(mode="after")
    def _fill_error_signature(self):
        if not self.error_signature:
            self.error_signature = error_signature(self.signal)
        return self
