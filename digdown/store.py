from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from typing import Iterable, Any, Optional

from .agent_models import Diagnosis
from .executor_models import Execution, ExecResult, StepResult
from .models import (Incident, IncidentState, Mute, Signal, TERMINAL_STATES,
                     utcnow)
from .plan_models import Action, Approval, Plan, RiskLevel


class Store:
    def __init__(self, path: str):
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        # Diagnosis runs on a worker thread while the poll loop and API handlers
        # also touch the store; a lock makes the single connection safe.
        self._lock = threading.RLock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    dedup_key TEXT NOT NULL,
                    signal_json TEXT NOT NULL,
                    parent_incident_id INTEGER,
                    failure_reason TEXT NOT NULL DEFAULT '',
                    error_signature TEXT NOT NULL DEFAULT '',
                    recurred_after_fix INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_incidents_dedup ON incidents(dedup_key);
                CREATE INDEX IF NOT EXISTS idx_incidents_target ON incidents(target);
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id INTEGER NOT NULL,
                    ts TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    event TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS diagnoses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    root_cause TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    rounds INTEGER NOT NULL,
                    critic_supported INTEGER NOT NULL,
                    critic_reason TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_diagnoses_incident
                    ON diagnoses(incident_id);
                CREATE TABLE IF NOT EXISTS plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    actions_json TEXT NOT NULL,
                    manual_fix TEXT NOT NULL,
                    critic2_supported INTEGER NOT NULL,
                    critic2_reason TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_plans_incident ON plans(incident_id);
                CREATE TABLE IF NOT EXISTS approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    identity TEXT NOT NULL,
                    note TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_approvals_incident
                    ON approvals(incident_id);
                CREATE TABLE IF NOT EXISTS executions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    result TEXT NOT NULL,
                    verified INTEGER NOT NULL,
                    rolled_back INTEGER NOT NULL,
                    steps_json TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    detail TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_executions_incident
                    ON executions(incident_id);
                CREATE TABLE IF NOT EXISTS mutes (
                    pattern TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    identity TEXT NOT NULL,
                    note TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    identity TEXT NOT NULL DEFAULT 'you'
                );
                """
            )
            # Migration for DBs created before correlation landed.
            try:
                self._conn.execute(
                    "ALTER TABLE incidents ADD COLUMN parent_incident_id INTEGER")
            except sqlite3.OperationalError:
                pass  # column already exists
            try:
                self._conn.execute(
                    "ALTER TABLE incidents ADD COLUMN failure_reason TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass  # column already exists
            for ddl in (
                "ALTER TABLE incidents ADD COLUMN error_signature TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE incidents ADD COLUMN recurred_after_fix INTEGER NOT NULL DEFAULT 0",
            ):
                try:
                    self._conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass  # column already exists
            # Index the error_signature column only AFTER it is guaranteed to exist
            # (fresh DB: via CREATE TABLE; existing DB: via the ALTER above). Creating
            # it inside the executescript would run before the migration and raise
            # "no such column: error_signature" on a pre-existing DB.
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_incidents_error_sig "
                "ON incidents(error_signature)")
            self._conn.commit()

    def _row_to_incident(self, row: sqlite3.Row) -> Incident:
        return Incident(
            id=row["id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            state=IncidentState(row["state"]),
            source=row["source"],
            target=row["target"],
            dedup_key=row["dedup_key"],
            signal=Signal.model_validate_json(row["signal_json"]),
            parent_incident_id=row["parent_incident_id"],
            failure_reason=row["failure_reason"],
            error_signature=row["error_signature"],
            recurred_after_fix=bool(row["recurred_after_fix"]),
        )

    def add_incident(self, incident: Incident) -> Incident:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO incidents
                   (created_at, updated_at, state, source, target, dedup_key,
                    signal_json, parent_incident_id, failure_reason,
                    error_signature, recurred_after_fix)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    incident.created_at.isoformat(),
                    incident.updated_at.isoformat(),
                    incident.state.value,
                    incident.source,
                    incident.target,
                    incident.dedup_key,
                    incident.signal.model_dump_json(),
                    incident.parent_incident_id,
                    incident.failure_reason,
                    incident.error_signature,
                    int(incident.recurred_after_fix),
                ),
            )
            self._conn.commit()
            incident.id = cur.lastrowid
            return incident

    def get_incident(self, incident_id: int) -> Optional[Incident]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone()
        return self._row_to_incident(row) if row else None

    def list_incidents(self) -> list[Incident]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM incidents ORDER BY id DESC"
            ).fetchall()
        return [self._row_to_incident(r) for r in rows]

    def incidents_in_state(self, state: IncidentState) -> list[Incident]:
        """Incidents currently in `state`, oldest first — used at startup to
        re-enqueue diagnosis for incidents detected while the agent was off."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM incidents WHERE state = ? ORDER BY id ASC",
                (state.value,),
            ).fetchall()
        return [self._row_to_incident(r) for r in rows]

    def incidents_in_states(self, states: Iterable[IncidentState]) -> list[Incident]:
        values = [s.value for s in states]
        placeholders = ",".join("?" for _ in values)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM incidents WHERE state IN ({placeholders}) ORDER BY id ASC",
                values,
            ).fetchall()
        return [self._row_to_incident(r) for r in rows]

    def open_incident_by_dedup(self, dedup_key: str) -> Optional[Incident]:
        placeholders = ",".join("?" for _ in TERMINAL_STATES)
        params = [dedup_key] + [s.value for s in TERMINAL_STATES]
        with self._lock:
            row = self._conn.execute(
                f"""SELECT * FROM incidents
                    WHERE dedup_key = ? AND state NOT IN ({placeholders})
                    ORDER BY id DESC LIMIT 1""",
                params,
            ).fetchone()
        return self._row_to_incident(row) if row else None

    def latest_incident_by_dedup(self, dedup_key: str) -> Optional[Incident]:
        """The most recent incident for this problem, whatever state it's in."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM incidents WHERE dedup_key = ? ORDER BY id DESC LIMIT 1",
                (dedup_key,),
            ).fetchone()
        return self._row_to_incident(row) if row else None

    def verified_incident_ids(self) -> set[int]:
        """Incidents whose latest fix was confirmed healthy afterwards."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT incident_id FROM executions WHERE id IN
                   (SELECT MAX(id) FROM executions GROUP BY incident_id)
                   AND verified = 1""",
            ).fetchall()
        return {r["incident_id"] for r in rows}

    def latest_diagnosed_incident_for_dedup(
            self, dedup_key: str, exclude_incident_id: int) -> Optional[int]:
        """The most recent OTHER incident with the same dedup_key that already
        carries a diagnosis — the source to replay when this exact issue recurs.
        Returns its id, or None when there's nothing to reuse."""
        with self._lock:
            row = self._conn.execute(
                """SELECT i.id FROM incidents i
                   JOIN diagnoses d ON d.incident_id = i.id
                   WHERE i.dedup_key = ? AND i.id != ?
                   ORDER BY i.id DESC LIMIT 1""",
                (dedup_key, exclude_incident_id),
            ).fetchone()
        return row["id"] if row else None

    def latest_diagnosed_incident_for_signature(
            self, signature: str, exclude_incident_id: int,
            since_iso: str) -> Optional[int]:
        """The most recent OTHER incident with the same error_signature, created
        on/after `since_iso`, that already carries a diagnosis — the source to
        reuse when this problem recurs. Returns its id, or None."""
        if not signature:
            return None
        with self._lock:
            row = self._conn.execute(
                """SELECT i.id FROM incidents i
                   JOIN diagnoses d ON d.incident_id = i.id
                   WHERE i.error_signature = ? AND i.id != ? AND i.created_at >= ?
                   ORDER BY i.id DESC LIMIT 1""",
                (signature, exclude_incident_id, since_iso),
            ).fetchone()
        return row["id"] if row else None

    def recent_open_incident_for_target(self, target: str,
                                        since_iso: str) -> Optional[Incident]:
        """Most recent non-terminal incident for a target since `since_iso` —
        used to link a cascade dependent to its root incident."""
        placeholders = ",".join("?" for _ in TERMINAL_STATES)
        params = [target, since_iso] + [s.value for s in TERMINAL_STATES]
        with self._lock:
            row = self._conn.execute(
                f"""SELECT * FROM incidents
                    WHERE target = ? AND created_at >= ?
                    AND state NOT IN ({placeholders})
                    ORDER BY id DESC LIMIT 1""",
                params,
            ).fetchone()
        return self._row_to_incident(row) if row else None

    def update_state(self, incident_id: int, state: IncidentState) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE incidents SET state = ?, updated_at = ? WHERE id = ?",
                (state.value, utcnow().isoformat(), incident_id),
            )
            self._conn.commit()

    def set_failure_reason(self, incident_id: int, reason: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE incidents SET failure_reason = ?, updated_at = ? WHERE id = ?",
                (reason, utcnow().isoformat(), incident_id))
            self._conn.commit()

    def set_parent_incident(self, incident_id: int, parent_id: Optional[int]) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE incidents SET parent_incident_id = ?, updated_at = ? WHERE id = ?",
                (parent_id, utcnow().isoformat(), incident_id))
            self._conn.commit()

    def set_recurred_after_fix(self, incident_id: int, value: bool = True) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE incidents SET recurred_after_fix = ?, updated_at = ? WHERE id = ?",
                (int(value), utcnow().isoformat(), incident_id))
            self._conn.commit()

    def add_diagnosis(self, diagnosis: Diagnosis) -> Diagnosis:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO diagnoses
                   (incident_id, created_at, summary, root_cause, evidence_json,
                    confidence, rounds, critic_supported, critic_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    diagnosis.incident_id,
                    diagnosis.created_at.isoformat(),
                    diagnosis.summary,
                    diagnosis.root_cause,
                    json.dumps(diagnosis.evidence),
                    diagnosis.confidence,
                    diagnosis.rounds,
                    int(diagnosis.critic_supported),
                    diagnosis.critic_reason,
                ),
            )
            self._conn.commit()
            diagnosis.id = cur.lastrowid
            return diagnosis

    def latest_diagnosis_for(self, incident_id: int) -> Optional[Diagnosis]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM diagnoses WHERE incident_id = ? ORDER BY id DESC LIMIT 1",
                (incident_id,),
            ).fetchone()
        if row is None:
            return None
        return Diagnosis(
            id=row["id"],
            incident_id=row["incident_id"],
            created_at=row["created_at"],
            summary=row["summary"],
            root_cause=row["root_cause"],
            evidence=json.loads(row["evidence_json"]),
            confidence=row["confidence"],
            rounds=row["rounds"],
            critic_supported=bool(row["critic_supported"]),
            critic_reason=row["critic_reason"],
        )

    def add_plan(self, plan: Plan) -> Plan:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO plans
                   (incident_id, created_at, summary, kind, risk, actions_json,
                    manual_fix, critic2_supported, critic2_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    plan.incident_id,
                    plan.created_at.isoformat(),
                    plan.summary,
                    plan.kind,
                    plan.risk.value,
                    json.dumps([a.model_dump(mode="json") for a in plan.actions]),
                    plan.manual_fix,
                    int(plan.critic2_supported),
                    plan.critic2_reason,
                ),
            )
            self._conn.commit()
            plan.id = cur.lastrowid
            return plan

    def latest_plan_for(self, incident_id: int) -> Optional[Plan]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM plans WHERE incident_id = ? ORDER BY id DESC LIMIT 1",
                (incident_id,),
            ).fetchone()
        if row is None:
            return None
        return Plan(
            id=row["id"],
            incident_id=row["incident_id"],
            created_at=row["created_at"],
            summary=row["summary"],
            kind=row["kind"],
            risk=RiskLevel(row["risk"]),
            actions=[Action.model_validate(a) for a in json.loads(row["actions_json"])],
            manual_fix=row["manual_fix"],
            critic2_supported=bool(row["critic2_supported"]),
            critic2_reason=row["critic2_reason"],
        )

    def add_approval(self, approval: Approval) -> Approval:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO approvals
                   (incident_id, created_at, decision, identity, note)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    approval.incident_id,
                    approval.created_at.isoformat(),
                    approval.decision,
                    approval.identity,
                    approval.note,
                ),
            )
            self._conn.commit()
            approval.id = cur.lastrowid
            return approval

    def auto_heals_since(self, since: datetime, target: Optional[str] = None) -> int:
        """How many fixes auto heal has started since then, optionally for one target."""
        sql = """SELECT COUNT(*) AS n FROM approvals a JOIN incidents i ON i.id = a.incident_id
                 WHERE a.identity = 'auto-heal' AND a.decision = 'approve' AND a.created_at >= ?"""
        params: list[Any] = [since.isoformat()]
        if target is not None:
            sql += " AND i.target = ?"
            params.append(target)
        with self._lock:
            return self._conn.execute(sql, params).fetchone()["n"]

    def auto_heal_activity(self, limit: int = 100) -> list[dict[str, Any]]:
        """Each time auto heal acted or held back, newest first, with how it turned out."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT a.id, a.ts, a.event, a.payload_json, i.id AS incident_id, i.target, i.state
                   FROM audit_log a JOIN incidents i ON i.id = a.incident_id
                   WHERE a.event IN ('auto_approved', 'auto_heal_skipped')
                   ORDER BY a.id DESC LIMIT ?""", (limit,)).fetchall()
        outcomes = {"resolved": "fixed", "rolled_back": "undone", "failed": "failed"}
        activity = []
        for r in rows:
            payload = json.loads(r["payload_json"])
            acted = r["event"] == "auto_approved"
            activity.append({
                "incident_id": r["incident_id"], "target": r["target"], "ts": r["ts"],
                "summary": payload.get("summary", ""), "reason": payload.get("reason", ""),
                "outcome": outcomes.get(r["state"], "working") if acted else "left_for_you",
            })
        return activity

    def auto_healed_incident_ids(self) -> set[int]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT DISTINCT incident_id FROM approvals
                   WHERE identity = 'auto-heal' AND decision = 'approve'""").fetchall()
        return {r["incident_id"] for r in rows}

    def approvals_for(self, incident_id: int) -> list[Approval]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM approvals WHERE incident_id = ? ORDER BY id",
                (incident_id,),
            ).fetchall()
        return [
            Approval(id=r["id"], incident_id=r["incident_id"],
                     created_at=r["created_at"], decision=r["decision"],
                     identity=r["identity"], note=r["note"])
            for r in rows
        ]

    def add_execution(self, execution: Execution) -> Execution:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO executions
                   (incident_id, created_at, result, verified, rolled_back,
                    steps_json, snapshot_json, detail)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    execution.incident_id,
                    execution.created_at.isoformat(),
                    execution.result.value,
                    int(execution.verified),
                    int(execution.rolled_back),
                    json.dumps([s.model_dump(mode="json") for s in execution.steps]),
                    json.dumps(execution.snapshot),
                    execution.detail,
                ),
            )
            self._conn.commit()
            execution.id = cur.lastrowid
            return execution

    def latest_execution_for(self, incident_id: int) -> Optional[Execution]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM executions WHERE incident_id = ? ORDER BY id DESC LIMIT 1",
                (incident_id,),
            ).fetchone()
        if row is None:
            return None
        return Execution(
            id=row["id"],
            incident_id=row["incident_id"],
            created_at=row["created_at"],
            result=ExecResult(row["result"]),
            verified=bool(row["verified"]),
            rolled_back=bool(row["rolled_back"]),
            steps=[StepResult.model_validate(s) for s in json.loads(row["steps_json"])],
            snapshot=json.loads(row["snapshot_json"]),
            detail=row["detail"],
        )

    def append_audit(self, incident_id: int, actor: str, event: str,
                     payload: dict[str, Any], ts: Optional[datetime] = None) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO audit_log (incident_id, ts, actor, event, payload_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (incident_id, (ts or utcnow()).isoformat(), actor, event, json.dumps(payload)),
            )
            self._conn.commit()

    def audit_for(self, incident_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit_log WHERE incident_id = ? ORDER BY id",
                (incident_id,),
            ).fetchall()
        return [
            {"actor": r["actor"], "event": r["event"],
             "payload": json.loads(r["payload_json"]), "ts": r["ts"]}
            for r in rows
        ]

    # ---- mutes (runtime silence by target prefix) ----

    def add_mute(self, pattern: str, identity: str = "you",
                 note: str = "") -> Mute:
        """Create (or refresh) a mute rule. Idempotent on `pattern`."""
        mute = Mute(pattern=pattern, identity=identity, note=note)
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO mutes (pattern, created_at, identity, note)
                   VALUES (?, ?, ?, ?)""",
                (mute.pattern, mute.created_at.isoformat(), mute.identity, mute.note),
            )
            self._conn.commit()
        return mute

    def remove_mute(self, pattern: str) -> bool:
        """Delete a mute rule; returns True if one existed."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM mutes WHERE pattern = ?", (pattern,))
            self._conn.commit()
            return cur.rowcount > 0

    def clear_terminal_incidents(self) -> dict[str, Any]:
        """Delete every incident in a terminal state (resolved/rejected/
        rolled_back/failed/closed_no_fix/muted) plus its child records. Mute
        RULES (the `mutes` table) are untouched. Returns deleted ids and count."""
        states = tuple(s.value for s in TERMINAL_STATES)
        placeholders = ",".join("?" for _ in states)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT id FROM incidents WHERE state IN ({placeholders})",
                states).fetchall()
            ids = [r["id"] for r in rows]
            if not ids:
                return {"deleted_ids": [], "count": 0}
            id_marks = ",".join("?" for _ in ids)
            for table in ("audit_log", "diagnoses", "plans", "approvals",
                          "executions"):
                self._conn.execute(
                    f"DELETE FROM {table} WHERE incident_id IN ({id_marks})", ids)
            # Drop dangling parent references on surviving incidents.
            self._conn.execute(
                f"UPDATE incidents SET parent_incident_id = NULL "
                f"WHERE parent_incident_id IN ({id_marks})", ids)
            self._conn.execute(
                f"DELETE FROM incidents WHERE id IN ({id_marks})", ids)
            self._conn.commit()
            return {"deleted_ids": ids, "count": len(ids)}

    def list_mutes(self) -> list[Mute]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM mutes ORDER BY created_at, pattern"
            ).fetchall()
        return [
            Mute(pattern=r["pattern"], created_at=r["created_at"],
                 identity=r["identity"], note=r["note"])
            for r in rows
        ]

    def is_target_muted(self, target: str) -> bool:
        return any(m.matches(target) for m in self.list_mutes())

    def mute_open_incidents(self, pattern: str) -> list[Incident]:
        """Flip every non-terminal incident whose target matches `pattern` to
        MUTED. Returns the affected incidents (already updated) so the caller can
        audit and broadcast them."""
        probe = Mute(pattern=pattern)
        affected = [
            inc for inc in self.list_incidents()
            if inc.state not in TERMINAL_STATES and probe.matches(inc.target)
        ]
        for inc in affected:
            self.update_state(inc.id, IncidentState.MUTED)
            inc.state = IncidentState.MUTED
        return affected

    # ---- runtime settings overrides (layered over digdown.yml) ----

    def list_settings(self) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value_json FROM settings").fetchall()
        return {r["key"]: json.loads(r["value_json"]) for r in rows}

    def set_setting(self, key: str, value: Any, identity: str = "you") -> None:
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO settings (key, value_json, updated_at, identity)
                   VALUES (?, ?, ?, ?)""",
                (key, json.dumps(value), utcnow().isoformat(), identity))
            self._conn.commit()

    def delete_setting(self, key: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            self._conn.commit()
            return cur.rowcount > 0

    def settings_meta(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, updated_at, identity FROM settings").fetchall()
        return {r["key"]: {"updated_at": r["updated_at"], "identity": r["identity"]}
                for r in rows}
