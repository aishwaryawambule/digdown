from digdown.models import Signal, SignalType, Incident, IncidentState
from digdown.agent_models import Diagnosis
from digdown.store import Store


def _mk(store, state=IncidentState.DETECTED):
    sig = Signal(source="docker", type=SignalType.OOM, target="web")
    return store.add_incident(Incident(source="docker", target="web", signal=sig,
                                       dedup_key=sig.dedup_key, state=state))


def test_failure_reason_persists_and_updates(store):
    inc = _mk(store)
    assert store.get_incident(inc.id).failure_reason == ""

    store.set_failure_reason(inc.id, "Rate-limited by the model API")
    assert store.get_incident(inc.id).failure_reason == "Rate-limited by the model API"

    store.set_failure_reason(inc.id, "")
    assert store.get_incident(inc.id).failure_reason == ""


def _incident(target="web", typ=SignalType.CRASH):
    sig = Signal(source="docker", type=typ, target=target)
    return Incident(source="docker", target=target, signal=sig, dedup_key=sig.dedup_key)


def test_store_migrates_existing_db_lacking_new_columns(tmp_path):
    """Regression: a digdown.db created before the error_signature columns must
    migrate cleanly on open. The error_signature index must be created AFTER the
    column-adding migration, not inside the initial executescript — otherwise an
    existing DB (where CREATE TABLE IF NOT EXISTS is a no-op) raises
    'no such column: error_signature' on startup."""
    import sqlite3
    p = str(tmp_path / "old.db")
    con = sqlite3.connect(p)
    con.executescript(
        """CREATE TABLE incidents (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
             state TEXT NOT NULL, source TEXT NOT NULL, target TEXT NOT NULL,
             dedup_key TEXT NOT NULL, signal_json TEXT NOT NULL);""")
    con.commit()
    con.close()
    # Opening the Store triggers _init_schema; it must migrate without raising.
    store = Store(p)
    inc = _mk(store)
    assert store.get_incident(inc.id).error_signature == "web|oom"


def test_add_and_latest_diagnosis(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    inc = store.add_incident(_incident())
    assert store.latest_diagnosis_for(inc.id) is None
    d1 = store.add_diagnosis(Diagnosis(
        incident_id=inc.id, summary="s1", root_cause="r1",
        evidence=["e"], confidence=0.5, rounds=1, critic_supported=False,
        critic_reason="weak"))
    assert d1.id is not None
    store.add_diagnosis(Diagnosis(
        incident_id=inc.id, summary="s2", root_cause="r2",
        evidence=["e1", "e2"], confidence=0.9, rounds=2, critic_supported=True,
        critic_reason="solid"))
    latest = store.latest_diagnosis_for(inc.id)
    assert latest.root_cause == "r2"
    assert latest.evidence == ["e1", "e2"]
    assert latest.critic_supported is True


def test_add_and_get(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    saved = store.add_incident(_incident())
    assert saved.id is not None
    fetched = store.get_incident(saved.id)
    assert fetched.target == "web"
    assert fetched.signal.type == SignalType.CRASH


def test_list_orders_newest_first(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    a = store.add_incident(_incident(target="a"))
    b = store.add_incident(_incident(target="b"))
    ids = [i.id for i in store.list_incidents()]
    assert ids == [b.id, a.id]


def test_incidents_in_state_filters_by_state(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    a = store.add_incident(_incident(target="a"))
    b = store.add_incident(_incident(target="b"))
    c = store.add_incident(_incident(target="c"))
    store.update_state(a.id, IncidentState.DETECTED)
    store.update_state(b.id, IncidentState.DETECTED)
    store.update_state(c.id, IncidentState.INVESTIGATING)
    got = store.incidents_in_state(IncidentState.DETECTED)
    assert sorted(i.target for i in got) == ["a", "b"]
    assert all(i.state == IncidentState.DETECTED for i in got)


def test_open_incident_by_dedup_ignores_terminal(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    inc = store.add_incident(_incident())
    assert store.open_incident_by_dedup(inc.dedup_key).id == inc.id
    store.update_state(inc.id, IncidentState.RESOLVED)
    assert store.open_incident_by_dedup(inc.dedup_key) is None


def test_append_audit(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    inc = store.add_incident(_incident())
    store.append_audit(inc.id, actor="watcher", event="detected", payload={"k": "v"})
    rows = store.audit_for(inc.id)
    assert rows[0]["actor"] == "watcher"
    assert rows[0]["payload"] == {"k": "v"}


def test_add_and_latest_plan(tmp_path):
    from digdown.plan_models import Plan, Action, ActionType, RiskLevel
    store = Store(str(tmp_path / "t.db"))
    inc = store.add_incident(_incident())
    assert store.latest_plan_for(inc.id) is None
    plan = store.add_plan(Plan(
        incident_id=inc.id, summary="restart it", kind="automated",
        risk=RiskLevel.LOW,
        actions=[Action(type=ActionType.RESTART, target="web", rationale="x")],
        critic2_supported=True, critic2_reason="ok"))
    assert plan.id is not None
    got = store.latest_plan_for(inc.id)
    assert got.actions[0].type == ActionType.RESTART
    assert got.kind == "automated"
    assert got.critic2_supported is True


def test_add_and_list_approvals(tmp_path):
    from digdown.plan_models import Approval
    store = Store(str(tmp_path / "t.db"))
    inc = store.add_incident(_incident())
    store.add_approval(Approval(incident_id=inc.id, decision="approve",
                                identity="ops", note="go"))
    rows = store.approvals_for(inc.id)
    assert rows[0].decision == "approve"
    assert rows[0].identity == "ops"


def test_add_and_latest_execution(tmp_path):
    from digdown.executor_models import Execution, ExecResult, StepResult
    store = Store(str(tmp_path / "t.db"))
    inc = store.add_incident(_incident())
    assert store.latest_execution_for(inc.id) is None
    store.add_execution(Execution(
        incident_id=inc.id, result=ExecResult.RESOLVED, verified=True,
        steps=[StepResult(type="restart", target="web", ok=True)],
        snapshot={"web": {"image": "web:1"}}, detail="stable"))
    got = store.latest_execution_for(inc.id)
    assert got.result == ExecResult.RESOLVED
    assert got.verified is True
    assert got.steps[0].type == "restart"
    assert got.snapshot["web"]["image"] == "web:1"


# ---- Mutes (runtime silence by target prefix) ----

def test_add_list_remove_mute(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    assert store.list_mutes() == []
    m = store.add_mute("blog", identity="alice", note="manual down")
    assert m.pattern == "blog" and m.identity == "alice" and m.note == "manual down"
    assert [x.pattern for x in store.list_mutes()] == ["blog"]
    # prefix match covers the whole stack
    assert store.is_target_muted("blog-api_app-1") is True
    assert store.is_target_muted("grafana") is False
    assert store.remove_mute("blog") is True
    assert store.remove_mute("blog") is False  # already gone
    assert store.list_mutes() == []
    assert store.is_target_muted("blog-api_app-1") is False


def test_add_mute_is_idempotent(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.add_mute("blog")
    store.add_mute("blog", note="again")
    assert [x.pattern for x in store.list_mutes()] == ["blog"]


def test_mute_open_incidents_flips_only_matching_nonterminal(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    a = store.add_incident(_incident(target="blog-web_app-1"))
    b = store.add_incident(_incident(target="grafana"))
    c = store.add_incident(_incident(target="blog-api_app-1"))
    store.update_state(c.id, IncidentState.RESOLVED)  # terminal — must be left alone
    affected = store.mute_open_incidents("blog")
    assert sorted(i.id for i in affected) == [a.id]
    assert store.get_incident(a.id).state == IncidentState.MUTED
    assert store.get_incident(b.id).state != IncidentState.MUTED   # different target
    assert store.get_incident(c.id).state == IncidentState.RESOLVED  # stayed terminal


def test_clear_terminal_incidents_removes_terminal_and_children(tmp_path):
    store = Store(str(tmp_path / "t.db"))

    # Active incident — must survive, with its diagnosis child.
    active = store.add_incident(_incident(target="active"))
    store.update_state(active.id, IncidentState.INVESTIGATING)
    store.add_diagnosis(Diagnosis(
        incident_id=active.id, summary="s", root_cause="r", evidence=["e"],
        confidence=0.5, rounds=1, critic_supported=True, critic_reason="ok"))

    # Terminal incidents (one resolved, one muted) — must be deleted with children.
    resolved = store.add_incident(_incident(target="resolved"))
    store.update_state(resolved.id, IncidentState.RESOLVED)
    store.add_diagnosis(Diagnosis(
        incident_id=resolved.id, summary="s", root_cause="r", evidence=["e"],
        confidence=0.9, rounds=1, critic_supported=True, critic_reason="ok"))
    store.append_audit(resolved.id, actor="op", event="resolved", payload={})

    muted = store.add_incident(_incident(target="muted"))
    store.update_state(muted.id, IncidentState.MUTED)

    # A mute RULE — must be untouched by clearing a muted incident.
    store.add_mute("muted", identity="op", note="silence")

    result = store.clear_terminal_incidents()

    assert sorted(result["deleted_ids"]) == sorted([resolved.id, muted.id])
    assert result["count"] == 2
    # Terminal incidents gone, active survives.
    remaining = [i.id for i in store.list_incidents()]
    assert remaining == [active.id]
    # Children of deleted incidents are gone; active's diagnosis survives.
    assert store.latest_diagnosis_for(resolved.id) is None
    assert store.latest_diagnosis_for(active.id) is not None
    assert store.audit_for(resolved.id) == []
    # Mute RULE persists.
    assert [m.pattern for m in store.list_mutes()] == ["muted"]


def test_clear_terminal_incidents_nulls_dangling_parent(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    parent = store.add_incident(_incident(target="parent"))
    store.update_state(parent.id, IncidentState.RESOLVED)
    child = store.add_incident(Incident(
        source="docker", target="child",
        signal=Signal(source="docker", type=SignalType.CRASH, target="child"),
        dedup_key="child-key", parent_incident_id=parent.id))
    store.update_state(child.id, IncidentState.INVESTIGATING)

    store.clear_terminal_incidents()

    refreshed = store.get_incident(child.id)
    assert refreshed is not None
    assert refreshed.parent_incident_id is None


def test_clear_terminal_incidents_empty_is_noop(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    assert store.clear_terminal_incidents() == {"deleted_ids": [], "count": 0}


def test_settings_roundtrip_and_reset(store):
    assert store.list_settings() == {}
    store.set_setting("mode", "execute", identity="alice")
    store.set_setting("verify_polls", 8)
    store.set_setting("reuse_recurring_diagnosis", False)
    assert store.list_settings() == {"mode": "execute", "verify_polls": 8,
                                     "reuse_recurring_diagnosis": False}
    meta = store.settings_meta()
    assert meta["mode"]["identity"] == "alice"
    assert meta["mode"]["updated_at"]
    # upsert overwrites
    store.set_setting("mode", "propose")
    assert store.list_settings()["mode"] == "propose"
    # delete resets
    assert store.delete_setting("mode") is True
    assert store.delete_setting("mode") is False
    assert "mode" not in store.list_settings()


def _diagnosed(store, signature, created_at_iso=None):
    sig = Signal(source="grafana", type=SignalType.ALERT_FIRING, target="t",
                 detail={"labels": {}})
    kwargs = dict(source="grafana", target="t", signal=sig,
                  dedup_key="k", error_signature=signature)
    if created_at_iso:
        kwargs["created_at"] = created_at_iso  # parsed to datetime at construction
    inc = store.add_incident(Incident(**kwargs))
    store.add_diagnosis(Diagnosis(incident_id=inc.id, summary="s", root_cause="r",
                                  evidence=[], confidence=0.9, rounds=1,
                                  critic_supported=True, critic_reason="ok"))
    return inc


def test_latest_diagnosed_for_signature_matches_within_window(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    older = _diagnosed(store, "sig-x")
    newer = _diagnosed(store, "sig-x")
    found = store.latest_diagnosed_incident_for_signature(
        "sig-x", newer.id, "2000-01-01T00:00:00+00:00")
    assert found == older.id  # the most recent OTHER one


def test_latest_diagnosed_for_signature_respects_window(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    _diagnosed(store, "sig-y", created_at_iso="2020-01-01T00:00:00+00:00")
    cur = _diagnosed(store, "sig-y")
    found = store.latest_diagnosed_incident_for_signature(
        "sig-y", cur.id, "2026-01-01T00:00:00+00:00")
    assert found is None  # the only prior is outside the window


def test_latest_diagnosed_for_signature_ignores_undiagnosed_and_empty(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    sig = Signal(source="grafana", type=SignalType.ALERT_FIRING, target="t",
                 detail={"labels": {}})
    bare = store.add_incident(Incident(source="grafana", target="t", signal=sig,
                                       dedup_key="k", error_signature="sig-z"))
    cur = store.add_incident(Incident(source="grafana", target="t", signal=sig,
                                      dedup_key="k", error_signature="sig-z"))
    assert store.latest_diagnosed_incident_for_signature(
        "sig-z", cur.id, "2000-01-01T00:00:00+00:00") is None  # bare has no diagnosis
    assert store.latest_diagnosed_incident_for_signature(
        "", cur.id, "2000-01-01T00:00:00+00:00") is None       # empty signature


def test_set_parent_and_recurred_after_fix(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    sig = Signal(source="docker", type=SignalType.OOM, target="web")
    inc = store.add_incident(Incident(source="docker", target="web", signal=sig,
                                      dedup_key=sig.dedup_key))
    store.set_parent_incident(inc.id, 42)
    store.set_recurred_after_fix(inc.id, True)
    got = store.get_incident(inc.id)
    assert got.parent_incident_id == 42
    assert got.recurred_after_fix is True


def test_incident_persists_error_signature_and_regression_flag(tmp_path):
    db_path = str(tmp_path / "t.db")
    store = Store(db_path)
    sig = Signal(source="grafana", type=SignalType.ALERT_FIRING,
                 target="shop-api", check="HighErrorRate")
    inc = store.add_incident(Incident(source="grafana", target="shop-api",
                                      signal=sig, dedup_key="grafana:abc"))
    # Verify persistence by reloading from DB
    store2 = Store(db_path)
    got = store2.get_incident(inc.id)
    assert got.error_signature == "shop-api|higherrorrate"
    assert got.recurred_after_fix is False
