from digdown.plan_models import (Action, ActionType, Approval, Plan,
                                       PlanProposal, RiskLevel)


def test_action_defaults():
    a = Action(type=ActionType.RESTART, target="web")
    assert a.params == {}
    assert a.rationale == ""


def test_plan_proposal_defaults_to_no_actions():
    p = PlanProposal(summary="nothing safe")
    assert p.actions == []
    assert p.manual_fix == ""


def test_plan_roundtrips_with_actions():
    plan = Plan(
        incident_id=2,
        summary="restart the worker",
        kind="automated",
        risk=RiskLevel.LOW,
        actions=[Action(type=ActionType.RESTART, target="worker", rationale="clear stuck state")],
        critic2_supported=True,
        critic2_reason="reversible and in scope",
    )
    dumped = plan.model_dump(mode="json")
    assert dumped["actions"][0]["type"] == "restart"
    back = Plan.model_validate(dumped)
    assert back.kind == "automated"
    assert back.risk == RiskLevel.LOW


def test_manual_plan_carries_fix_text():
    plan = Plan(incident_id=3, summary="needs human", kind="manual",
                manual_fix="Increase the mem_limit in compose and redeploy.",
                risk=RiskLevel.LOW)
    assert plan.actions == []
    assert "mem_limit" in plan.manual_fix


def test_approval_decision():
    a = Approval(incident_id=1, decision="approve", identity="ops@x", note="lgtm")
    assert a.decision == "approve"
