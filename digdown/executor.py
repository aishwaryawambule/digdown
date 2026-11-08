"""The Executor — the only path that mutates the system, and only after the
plan was approved by a person or by the auto heal rules (heal.py). Sequence: re-validate against the allow-list → snapshot
→ run each action → verify a healthy window → roll back on failure. Everything is
journaled to an Execution record and the audit log.

Safety: actions are re-validated here (defense in depth, not just at propose
time); any action error aborts the rest and rolls back what ran; verification
failure rolls everything back. A per-target lock serialises writes to a service.
"""
from __future__ import annotations

import threading
from typing import Callable

from .allowlist import validate_plan
from .config import Config
from .executor_models import Execution, ExecResult, StepResult, without_env_file
from .heal import AUTO_HEAL_IDENTITY, keep_after_update_hint
from .models import Incident, IncidentState
from .plan_models import ActionType
from .notifier import Notifier


class Executor:
    def __init__(self, store, bus, config: Config, writer, verifier,
                 valid_targets: Callable[[], set[str]], notifier=None):
        self.store = store
        self.bus = bus
        self.config = config
        self.writer = writer
        self.verifier = verifier
        self.valid_targets = valid_targets
        self.notifier = notifier or Notifier()
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # ---- orchestration ----

    def execute(self, incident: Incident) -> Execution:
        plan = self.store.latest_plan_for(incident.id)
        if plan is None or plan.kind != "automated" or not plan.actions:
            return self._abort(incident, "no automated plan to execute")

        errors = validate_plan(plan.actions, self.valid_targets())
        if errors:
            return self._abort(incident, "plan failed re-validation: " +
                               "; ".join(errors))

        self._transition(incident, IncidentState.EXECUTING, "executing")

        snapshot = {}
        for target in {a.target for a in plan.actions}:
            try:
                snapshot[target] = self.writer.capture(target)
            except Exception as e:  # noqa: BLE001
                snapshot[target] = {"capture_error": str(e)}

        steps: list[StepResult] = []
        ran = []
        for action in plan.actions:
            try:
                with self._lock_for(action.target):
                    self.writer.apply(action)
                steps.append(StepResult(type=action.type.value, target=action.target,
                                        ok=True))
                ran.append((action, len(steps) - 1))
            except Exception as e:  # noqa: BLE001
                steps.append(StepResult(type=action.type.value, target=action.target,
                                        ok=False, detail=str(e)))
                self._rollback(ran, snapshot, steps)
                return self._finish(incident, ExecResult.FAILED, IncidentState.FAILED,
                                    verified=False, rolled_back=bool(ran),
                                    steps=steps, snapshot=snapshot,
                                    detail=f"action {action.type.value} failed: {e}")

        self._transition(incident, IncidentState.VERIFYING, "verifying")
        ok, detail = self.verifier.verify(incident)
        if ok:
            return self._finish(incident, ExecResult.RESOLVED, IncidentState.RESOLVED,
                                verified=True, rolled_back=False, steps=steps,
                                snapshot=snapshot, detail=detail)
        if all(action.type == ActionType.CLEAN_DOCKER for action, _ in ran):
            # What was deleted is gone, so saying it was undone would be untrue.
            return self._finish(incident, ExecResult.FAILED, IncidentState.FAILED,
                                verified=False, rolled_back=False, steps=steps,
                                snapshot=snapshot,
                                detail=f"Cleared unused Docker data, but {detail}. "
                                       "Nothing else was deleted, so this needs you.")
        self._rollback(ran, snapshot, steps)
        return self._finish(incident, ExecResult.ROLLED_BACK,
                            IncidentState.ROLLED_BACK, verified=False,
                            rolled_back=True, steps=steps, snapshot=snapshot,
                            detail=f"The fix didn't hold: {detail}")

    # ---- helpers ----

    def _rollback(self, ran, snapshot, steps) -> None:
        # Reverse order; best-effort (a rollback error is recorded, not raised).
        for action, idx in reversed(ran):
            try:
                with self._lock_for(action.target):
                    self.writer.rollback(action, snapshot.get(action.target, {}))
                steps[idx].rolled_back = True
            except Exception as e:  # noqa: BLE001
                steps[idx].detail = (steps[idx].detail + f" | rollback failed: {e}").strip()

    def _lock_for(self, target: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(target, threading.Lock())

    def _abort(self, incident: Incident, detail: str) -> Execution:
        execution = self.store.add_execution(Execution(
            incident_id=incident.id, result=ExecResult.FAILED, verified=False,
            rolled_back=False, steps=[], snapshot={}, detail=detail))
        self._transition(incident, IncidentState.FAILED, "execute_failed",
                         payload={"detail": detail})
        return execution

    def _finish(self, incident, result, final_state, *, verified, rolled_back,
                steps, snapshot, detail) -> Execution:
        # The snapshot was only needed to undo the fix. The .env copy in it holds real
        # secrets, so it must not reach the database, the audit log or live updates.
        kept = without_env_file(snapshot)
        execution = self.store.add_execution(Execution(
            incident_id=incident.id, result=result, verified=verified,
            rolled_back=rolled_back, steps=steps, snapshot=kept, detail=detail))
        self._transition(incident, final_state, "executed",
                         payload=execution.model_dump(mode="json"),
                         verified=verified)
        self.notifier.notify(*self._note(incident, result, detail))
        return execution

    def _auto_healed(self, incident: Incident) -> bool:
        return any(a.identity == AUTO_HEAL_IDENTITY and a.decision == "approve"
                   for a in self.store.approvals_for(incident.id))

    def _note(self, incident, result, detail) -> tuple[str, Incident, str]:
        """What the alert says. Auto heal's has to stand alone: nobody watched it
        happen, so it covers what went wrong and what was done, as well as the result."""
        auto = self._auto_healed(incident)
        if result != ExecResult.RESOLVED and not auto:
            return result.value, incident, detail
        plan = self.store.latest_plan_for(incident.id)
        parts = [detail]
        if auto:
            diagnosis = self.store.latest_diagnosis_for(incident.id)
            parts = [diagnosis.summary if diagnosis else "", plan.summary, detail]
        if result == ExecResult.RESOLVED:
            parts.append(keep_after_update_hint(plan))
        text = " ".join(f"{p.rstrip('.')}." if p[-1] not in ".!?`" else p for p in parts if p)
        kind = "auto_healed" if auto and result == ExecResult.RESOLVED else result.value
        return kind, incident, text

    def _transition(self, incident, state, event, payload=None, verified=None):
        self.store.update_state(incident.id, state)
        incident.state = state
        self.store.append_audit(incident.id, actor="executor", event=event,
                                payload=payload or {})
        record = incident.model_dump(mode="json")
        if verified is not None:
            record["execution"] = {"verified": verified}
        if self._auto_healed(incident):
            record["auto_healed"] = True
            plan = self.store.latest_plan_for(incident.id)
            record["heal_summary"] = plan.summary if plan else ""
        self.bus.publish({"type": "incident_updated", "incident": record})
