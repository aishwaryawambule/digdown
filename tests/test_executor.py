from digdown.config import Config
from digdown.executor import Executor
from digdown.executor_models import ExecResult
from digdown.models import (Incident, IncidentState, Signal, SignalType)
from digdown.plan_models import Action, ActionType, Plan, RiskLevel


class FakeWriter:
    def __init__(self, fail_on=None):
        self.applied = []
        self.rolled_back = []
        self.fail_on = fail_on  # (type, target) to raise on

    def capture(self, target):
        return {"image": f"{target}:snap", "status": "running", "env_backup": ""}

    def apply(self, action):
        if self.fail_on == (action.type.value, action.target):
            raise RuntimeError("apply boom")
        self.applied.append((action.type.value, action.target))

    def rollback(self, action, snapshot):
        self.rolled_back.append((action.type.value, action.target))


class FakeVerifier:
    def __init__(self, ok):
        self.ok = ok

    def verify(self, incident):
        return (self.ok, "stable" if self.ok else "still failing")


def _approved(store, actions, target="web", kind="automated"):
    sig = Signal(source="docker", type=SignalType.CRASH, target=target)
    inc = store.add_incident(Incident(source="docker", target=target, signal=sig,
                                      dedup_key=sig.dedup_key))
    store.update_state(inc.id, IncidentState.APPROVED)
    inc.state = IncidentState.APPROVED
    store.add_plan(Plan(incident_id=inc.id, summary="fix", kind=kind,
                        risk=RiskLevel.LOW, actions=actions, critic2_supported=True))
    return inc


def _executor(store, bus, writer, verifier, targets=("web",)):
    return Executor(store, bus, Config(mode="execute"), writer, verifier,
                    valid_targets=lambda: set(targets))


def test_successful_execution_resolves(store, bus):
    inc = _approved(store, [Action(type=ActionType.RESTART, target="web")])
    writer = FakeWriter()
    ex = _executor(store, bus, writer, FakeVerifier(True)).execute(inc)
    assert ex.result == ExecResult.RESOLVED
    assert ex.verified is True
    assert writer.applied == [("restart", "web")]
    assert writer.rolled_back == []
    assert store.get_incident(inc.id).state == IncidentState.RESOLVED
    events = [e["event"] for e in store.audit_for(inc.id)]
    assert "executing" in events and "verifying" in events and "executed" in events


def test_failed_verification_rolls_back(store, bus):
    inc = _approved(store, [Action(type=ActionType.RESTART, target="web")])
    writer = FakeWriter()
    ex = _executor(store, bus, writer, FakeVerifier(False)).execute(inc)
    assert ex.result == ExecResult.ROLLED_BACK
    assert ex.rolled_back is True
    assert writer.rolled_back == [("restart", "web")]
    assert store.get_incident(inc.id).state == IncidentState.ROLLED_BACK


def test_action_failure_aborts_and_rolls_back_ran(store, bus):
    inc = _approved(store, [
        Action(type=ActionType.SET_ENV_VAR, target="web",
               params={"key": "K", "value": "v"}),
        Action(type=ActionType.RESTART, target="web"),
    ])
    writer = FakeWriter(fail_on=("restart", "web"))
    ex = _executor(store, bus, writer, FakeVerifier(True)).execute(inc)
    assert ex.result == ExecResult.FAILED
    # the set_env_var that ran was rolled back; restart never succeeded
    assert ("set_env_var", "web") in writer.rolled_back
    assert store.get_incident(inc.id).state == IncidentState.FAILED


def test_snapshot_captured_before_running(store, bus):
    inc = _approved(store, [Action(type=ActionType.RESTART, target="web")])
    ex = _executor(store, bus, FakeWriter(), FakeVerifier(True)).execute(inc)
    assert ex.snapshot["web"]["image"] == "web:snap"


def test_revalidation_rejects_unknown_target(store, bus):
    inc = _approved(store, [Action(type=ActionType.RESTART, target="web")])
    writer = FakeWriter()
    # target no longer present in the live system
    ex = _executor(store, bus, writer, FakeVerifier(True), targets=()).execute(inc)
    assert ex.result == ExecResult.FAILED
    assert writer.applied == []
    assert store.get_incident(inc.id).state == IncidentState.FAILED


def test_manual_plan_is_not_executed(store, bus):
    inc = _approved(store, [], kind="manual")
    writer = FakeWriter()
    ex = _executor(store, bus, writer, FakeVerifier(True)).execute(inc)
    assert ex.result == ExecResult.FAILED
    assert writer.applied == []


def test_resolution_sends_notification(store, bus):
    from digdown.notifier import Notifier
    sent = []
    inc = _approved(store, [Action(type=ActionType.RESTART, target="web")])
    Executor(store, bus, Config(mode="execute"), FakeWriter(), FakeVerifier(True),
             valid_targets=lambda: {"web"},
             notifier=Notifier(channels=[sent.append])).execute(inc)
    assert sent[-1]["kind"] == "resolved"
