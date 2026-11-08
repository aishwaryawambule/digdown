"""`digdown demo`: the real watcher, agents, approval gate, executor and verifier,
running against simulated containers and a scripted AI. It needs no Docker and no
model, and touches nothing, so anyone can see the whole product in a minute.

Four failures play out, one for each way an incident can end:
  shop-cache     ran out of memory; you approve the fix, and it's verified
  photos-server  ran out of memory; auto heal is on for its stack, so nobody clicks
  shop-worker    crashed on a missing secret; no safe automatic fix, so it explains
  shop-web       failing its health check; the fix doesn't help and is undone
"""
from __future__ import annotations

import json
import math
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from .agent_models import Diagnosis
from .api import create_app
from .config import Config
from .discovery import Project
from .events import EventBus
from .executor import Executor
from .executor_models import ExecResult, Execution
from .heal import AUTO_HEAL_IDENTITY
from .llm import LLMResponse, ToolCall
from .models import ContainerState, HealthStatus, Incident, IncidentState, Signal, SignalType, utcnow
from .pipeline import Pipeline
from .plan_models import Action, ActionType, Approval, Plan
from .redactor import Redactor
from .stats_ring import HostRing, StatsRing
from .store import Store
from .tools import DiagnosticTools
from .verifier import Verifier
from .watcher import Watcher

_MB = 1024 * 1024
_PROJECT = "shop"
_HEALED_PROJECT = "photos"  # auto heal is on for this stack only
_IMAGES = {"shop-web": "shop/web:1.42", "shop-api": "shop/api:1.42", "shop-db": "postgres:16",
           "shop-cache": "redis:7", "shop-worker": "shop/worker:1.42",
           "photos-server": "photos/server:2.1"}
_LIMITS = {"shop-web": 512, "shop-api": 1024, "shop-db": 2048, "shop-cache": 256,
           "shop-worker": 512, "photos-server": 1024}
_DEPENDS = {"shop-web": ["shop-api"], "shop-api": ["shop-db", "shop-cache"],
            "shop-worker": ["shop-db", "shop-cache"], "shop-db": [], "shop-cache": [],
            "photos-server": []}

# When each failure starts, in seconds, spaced so each can be watched on its own.
_FAULT_TIMES = {"shop-cache": 4, "photos-server": 18, "shop-worker": 34, "shop-web": 60}

_LOGS = {
    "shop-cache": (
        "1:M 09:41:02 * Background saving started by pid 214\n"
        "1:M 09:41:09 # Memory usage 251.2M of maxmemory 256M\n"
        "1:M 09:41:14 # WARNING: bulk import from shop-worker added 41,288 keys\n"
        "Killed"),
    "photos-server": (
        "indexer: found 18,204 new photos, generating thumbnails\n"
        "indexer: memory 1012M of 1024M\n"
        "Killed"),
    "shop-worker": (
        "worker: starting, 4 queues\n"
        "worker: loading payment settings\n"
        "Traceback (most recent call last):\n"
        '  File "/app/payments.py", line 18, in load\n'
        '    secret = os.environ["STRIPE_WEBHOOK_SECRET"]\n'
        "KeyError: 'STRIPE_WEBHOOK_SECRET'"),
    "shop-web": (
        "nginx: GET /healthz 504 upstream timed out after 5.0s\n"
        "nginx: GET /healthz 504 upstream timed out after 5.0s\n"
        "nginx: upstream shop-api:8000 responded slowly, 41 requests waiting"),
}


class DemoSource:
    """Stands in for Docker: five containers whose failures start on a timer."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._start = clock()
        self._lock = threading.Lock()
        self._fixed: set[str] = set()
        self._limits = dict(_LIMITS)
        self._down_at: dict[str, Any] = {}

    def _broken(self, name: str) -> bool:
        started = self._clock() - self._start >= _FAULT_TIMES.get(name, float("inf"))
        return started and name not in self._fixed

    def _state(self, name: str, index: int) -> ContainerState:
        limit = self._limits[name] * _MB
        wobble = math.sin((self._clock() - self._start) / 7 + index)
        common: dict[str, Any] = dict(
            id=f"demo{index:08x}", name=name, service=name,
            project=_HEALED_PROJECT if name == "photos-server" else _PROJECT,
            image=_IMAGES[name], restart_policy="unless-stopped")
        if name in ("shop-cache", "photos-server") and self._broken(name):
            return ContainerState(**common, status="exited", exit_code=137, oom_killed=True,
                                  mem_limit_bytes=limit,
                                  finished_at=self._down_at.setdefault(name, utcnow()))
        if name == "shop-worker" and self._broken(name):
            return ContainerState(**common, status="exited", exit_code=1, mem_limit_bytes=limit,
                                  finished_at=self._down_at.setdefault(name, utcnow()))
        health = HealthStatus.HEALTHY
        if name == "shop-web" and self._broken(name):
            health = HealthStatus.UNHEALTHY
        mem_percent = round(42 + 9 * (index % 4) + 6 * wobble, 1)
        return ContainerState(**common, status="running", health=health,
                              cpu_percent=round(max(1.0, 11 + 5 * index + 8 * wobble), 1),
                              mem_bytes=int(limit * mem_percent / 100),
                              mem_limit_bytes=limit, mem_percent=mem_percent)

    def list_states(self) -> list[ContainerState]:
        with self._lock:
            return [self._state(n, i) for i, n in enumerate(_IMAGES)]

    def get_logs(self, target: str, lines: int = 200) -> str:
        return _LOGS.get(target, "") if self._broken(target) else f"{target}: serving normally"

    def stopped_on_purpose(self, target: str) -> bool:
        return False

    def inspect(self, target: str) -> dict[str, Any]:
        return {"image": _IMAGES.get(target, "?"), "command": "/entrypoint.sh",
                "restart_policy": "unless-stopped", "env": {"LOG_LEVEL": "info"}}

    # Used by DemoWriter.
    def repair(self, target: str) -> None:
        with self._lock:
            self._fixed.add(target)

    def unrepair(self, target: str) -> None:
        with self._lock:
            self._fixed.discard(target)

    def set_limit(self, target: str, megabytes: int) -> None:
        with self._lock:
            self._limits[target] = megabytes

    def limit_of(self, target: str) -> int:
        with self._lock:
            return self._limits[target]


class DemoWriter:
    """Stands in for the code that changes containers, so approving a fix here
    can't touch anything real. shop-web's restart deliberately doesn't help."""

    def __init__(self, source: DemoSource):
        self.source = source

    def capture(self, target: str) -> dict[str, Any]:
        return {"limit_mb": self.source.limit_of(target)}

    def apply(self, action: Action) -> None:
        if action.type == ActionType.SET_RESOURCE_LIMIT:
            self.source.set_limit(action.target, action.params["mem_limit_bytes"] // _MB)
        elif action.target != "shop-web":
            self.source.repair(action.target)

    def rollback(self, action: Action, snapshot: dict[str, Any]) -> None:
        self.source.unrepair(action.target)
        if "limit_mb" in snapshot:
            self.source.set_limit(action.target, snapshot["limit_mb"])


_DIAGNOSES = {
    "shop-cache": {
        "summary": "Redis ran out of memory during a bulk import.",
        "root_cause": "A bulk import from shop-worker added 41,288 keys and pushed Redis "
                      "past its 256 MB limit, so the kernel killed it (exit code 137).",
        "evidence": ["Exit code 137 with the out-of-memory flag set",
                     "Log: memory usage 251.2M of maxmemory 256M",
                     "Log: bulk import added 41,288 keys just before it was killed"],
        "confidence": 0.93},
    "photos-server": {
        "summary": "The photo indexer ran out of memory on a large import.",
        "root_cause": "The indexer was making thumbnails for 18,204 new photos and passed "
                      "its 1 GB limit, so the kernel killed it (exit code 137).",
        "evidence": ["Exit code 137 with the out-of-memory flag set",
                     "Log: memory 1012M of 1024M just before it was killed"],
        "confidence": 0.91},
    "shop-worker": {
        "summary": "The worker can't start because a secret is missing.",
        "root_cause": "shop-worker reads STRIPE_WEBHOOK_SECRET at startup, and that setting "
                      "isn't defined, so it exits with a KeyError every time.",
        "evidence": ["Exit code 1, immediately after startup",
                     "Log: KeyError: 'STRIPE_WEBHOOK_SECRET' in /app/payments.py line 18"],
        "confidence": 0.97},
    "shop-web": {
        "summary": "Health checks are timing out while waiting on shop-api.",
        "root_cause": "nginx in shop-web is waiting on a slow shop-api, so /healthz times out "
                      "after 5 seconds. A stuck connection pool in nginx is the likeliest cause.",
        "evidence": ["Log: GET /healthz 504, upstream timed out after 5.0s",
                     "Log: 41 requests waiting on shop-api:8000"],
        "confidence": 0.62},
}

_PLANS = {
    "shop-cache": {
        "summary": "Raise the shop-cache memory limit to 512 MB and restart it.",
        "actions": [
            {"type": "set_resource_limit", "target": "shop-cache",
             "params": {"mem_limit_bytes": 512 * _MB},
             "rationale": "Gives the import room to finish."},
            {"type": "restart", "target": "shop-cache", "params": {},
             "rationale": "Brings Redis back up with the new limit."}],
        "manual_fix": ""},
    "photos-server": {
        "summary": "Raise the photos-server memory limit to 2 GB and restart it.",
        "actions": [
            {"type": "set_resource_limit", "target": "photos-server",
             "params": {"mem_limit_bytes": 2048 * _MB},
             "rationale": "Gives the indexer room to finish."},
            {"type": "restart", "target": "photos-server", "params": {},
             "rationale": "Brings it back up with the new limit."}],
        "manual_fix": ""},
    "shop-worker": {
        "summary": "Add the missing secret by hand.",
        "actions": [],
        "manual_fix": "Add STRIPE_WEBHOOK_SECRET to the .env file for shop-worker, then "
                      "restart it. Digdown can't do this because it doesn't know the secret."},
    "shop-web": {
        "summary": "Restart shop-web to clear its connections.",
        "actions": [{"type": "restart", "target": "shop-web", "params": {},
                     "rationale": "Clears a stuck connection pool, if that's the cause."}],
        "manual_fix": ""},
}

_VERDICTS = {
    "You are the Critic": {"supported": True, "reason": "The logs and exit code support this.",
                           "issues": []},
    "You are the fix Critic": {"supported": True, "reason": "Limited to this service and "
                               "can be undone.", "issues": []},
}


class DemoLLM:
    """Replies the way a model would, including looking at evidence first, from the
    script above. The pause lets you watch the investigation happen."""

    def __init__(self, delay: float = 1.2):
        self.delay = delay

    def chat(self, messages, tools=None) -> LLMResponse:
        time.sleep(self.delay)
        system = messages[0]["content"]
        target = re.search(r"target: (\S+)", messages[1]["content"]).group(1)
        if system.startswith("You are the Diagnostician"):
            if not any(m["role"] == "tool" for m in messages):
                return LLMResponse(tool_calls=[
                    ToolCall(id="c1", name="get_container_state", arguments={"target": target}),
                    ToolCall(id="c2", name="get_recent_logs", arguments={"target": target})])
            return LLMResponse(content=json.dumps(_DIAGNOSES[target]))
        if system.startswith("You are the Planner"):
            return LLMResponse(content=json.dumps(_PLANS[target]))
        for prefix, verdict in _VERDICTS.items():
            if system.startswith(prefix):
                return LLMResponse(content=json.dumps(verdict))
        raise ValueError(f"unexpected agent: {system[:40]}")


# Past incidents so Trends and history have something to show.
_HISTORY = [
    (6.2, "shop-cache", SignalType.OOM, IncidentState.RESOLVED, 3,
     "Redis ran out of memory during the nightly import."),
    (5.1, "shop-api", SignalType.CRASH, IncidentState.RESOLVED, 2,
     "shop-api crashed on a database connection that timed out."),
    (4.4, "shop-web", SignalType.UNHEALTHY, IncidentState.REJECTED, 9,
     "Health checks failed during a slow deploy. It recovered without a fix."),
    (3.0, "shop-worker", SignalType.RESTART_LOOP, IncidentState.CLOSED_NO_FIX, 6,
     "A bad migration made the worker restart repeatedly. It needed a code change."),
    (1.8, "shop-api", SignalType.MEMORY_PRESSURE, IncidentState.RESOLVED, 4,
     "shop-api neared its memory limit because of a request backlog."),
    (0.7, "shop-db", SignalType.UNHEALTHY, IncidentState.RESOLVED, 5,
     "Postgres failed health checks while a large index was building."),
    (0.3, "photos-server", SignalType.CRASH, IncidentState.RESOLVED, 2,
     "The photo server crashed on a corrupt thumbnail cache."),
]


def _seed_history(store: Store) -> None:
    now = utcnow()
    for n, (days, target, kind, state, minutes, cause) in enumerate(_HISTORY):
        created = now - timedelta(days=days)
        signal = Signal(source="docker", type=kind, target=target)
        incident = store.add_incident(Incident(
            source="docker", target=target, signal=signal, state=state,
            # A key of its own, so these never affect the live failures.
            dedup_key=f"demo-history:{n}", created_at=created,
            updated_at=created + timedelta(minutes=minutes)))
        store.add_diagnosis(Diagnosis(
            incident_id=incident.id, summary=cause, root_cause=cause, confidence=0.9,
            critic_supported=True, critic_reason="Example from the demo history."))
        if state == IncidentState.RESOLVED:
            store.add_execution(Execution(
                incident_id=incident.id, result=ExecResult.RESOLVED, verified=True,
                rolled_back=False, steps=[], snapshot={}, detail="Example from the demo history."))
        if target == "photos-server":
            store.add_plan(Plan(incident_id=incident.id, kind="automated", critic2_supported=True,
                                summary="Restarted photos-server, which rebuilt its cache.",
                                actions=[Action(type=ActionType.RESTART, target=target)]))
            store.add_approval(Approval(incident_id=incident.id, decision="approve",
                                        identity=AUTO_HEAL_IDENTITY, created_at=created))
            store.append_audit(incident.id, actor=AUTO_HEAL_IDENTITY, event="auto_approved",
                               payload={"summary": "Restarted photos-server, which rebuilt its cache."},
                               ts=created)
    # An example of holding back, so the Auto heal page shows both sides.
    signal = Signal(source="docker", type=SignalType.CRASH, target="photos-db")
    held_back = store.add_incident(Incident(
        source="docker", target="photos-db", signal=signal, state=IncidentState.REJECTED,
        dedup_key="demo-history:held-back", created_at=now - timedelta(days=1.4),
        updated_at=now - timedelta(days=1.4)))
    store.append_audit(held_back.id, actor=AUTO_HEAL_IDENTITY, event="auto_heal_skipped",
                       payload={"reason": "This fix changes how the container is set up, "
                                          "which always needs your approval."},
                       ts=now - timedelta(days=1.4))


@dataclass
class Demo:
    app: Any
    store: Store
    source: DemoSource
    watcher: Watcher
    pipeline: Pipeline
    executor: Executor
    data_dir: str

    def cleanup(self) -> None:
        shutil.rmtree(self.data_dir, ignore_errors=True)


def build_demo(clock: Callable[[], float] = time.monotonic, llm_delay: float = 1.2,
               verify_interval: int = 2) -> Demo:
    data_dir = tempfile.mkdtemp(prefix="digdown-demo-")
    config = Config(
        mode="execute", db_path=str(Path(data_dir) / "demo.db"), poll_interval_seconds=3,
        llm_model="scripted demo", reuse_recurring_diagnosis=False,
        verify_polls=3, verify_poll_interval=verify_interval,
        auto_heal_stacks=[_HEALED_PROJECT])
    store = Store(config.db_path)
    _seed_history(store)
    bus = EventBus()
    source = DemoSource(clock)
    project = Project(name=_PROJECT, services=list(_IMAGES), depends_on=_DEPENDS)
    redactor = Redactor()
    tools = DiagnosticTools(source, project, config.log_tail_lines, redactor=redactor)
    pipeline = Pipeline(store, bus, config, DemoLLM(llm_delay), tools, redactor=redactor)
    executor = Executor(store, bus, config, DemoWriter(source), Verifier(source, config),
                        tools.valid_targets)
    vitals = lambda: {"disk_percent": 41.0, "load1": 0.8, "mem_percent": 58.0}  # noqa: E731
    watcher = Watcher(source=source, store=store, bus=bus, config=config,
                      disk_percent=lambda: 41.0, project=project, stats_ring=StatsRing(),
                      host_ring=HostRing(), host_vitals=vitals)
    app = create_app(store, bus, config, watcher=watcher, pipeline=pipeline,
                     executor=executor, local_only=True, demo=True)
    return Demo(app, store, source, watcher, pipeline, executor, data_dir)
