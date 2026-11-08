from digdown.allowlist import (ALLOWED_ACTIONS, plan_risk, validate_action,
                                    validate_plan)
from digdown.plan_models import Action, ActionType, RiskLevel


def test_all_action_types_are_allowed():
    assert ALLOWED_ACTIONS == set(ActionType)


def test_valid_restart_has_no_errors():
    a = Action(type=ActionType.RESTART, target="web")
    assert validate_action(a, {"web", "db"}) == []


def test_unknown_target_is_rejected():
    a = Action(type=ActionType.RESTART, target="ghost")
    errs = validate_action(a, {"web"})
    assert errs and "ghost" in errs[0]


def test_set_env_var_requires_key_and_value():
    a = Action(type=ActionType.SET_ENV_VAR, target="web", params={"key": "X"})
    errs = validate_action(a, {"web"})
    assert any("value" in e for e in errs)
    ok = Action(type=ActionType.SET_ENV_VAR, target="web",
                params={"key": "X", "value": "1"})
    assert validate_action(ok, {"web"}) == []


def test_validate_plan_aggregates_and_reports_per_action():
    actions = [
        Action(type=ActionType.RESTART, target="web"),       # ok
        Action(type=ActionType.START, target="ghost"),       # bad target
    ]
    errs = validate_plan(actions, {"web"})
    assert len(errs) == 1
    assert "ghost" in errs[0]


def test_plan_risk_is_the_max():
    assert plan_risk([Action(type=ActionType.RESTART, target="w")]) == RiskLevel.LOW
    assert plan_risk([
        Action(type=ActionType.RESTART, target="w"),
        Action(type=ActionType.SET_ENV_VAR, target="w", params={"key": "k", "value": "v"}),
    ]) == RiskLevel.HIGH
    assert plan_risk([]) == RiskLevel.LOW


def test_set_resource_limit_is_allowed_and_medium_risk():
    a = Action(type=ActionType.SET_RESOURCE_LIMIT, target="web",
               params={"mem_limit_bytes": 512_000_000})
    assert validate_action(a, {"web"}) == []
    assert plan_risk([a]) == RiskLevel.MEDIUM


def test_set_resource_limit_rejects_bad_mem_limit():
    base = lambda p: Action(type=ActionType.SET_RESOURCE_LIMIT, target="web", params=p)
    assert validate_action(base({}), {"web"}) != []                       # missing
    assert validate_action(base({"mem_limit_bytes": "512"}), {"web"}) != []  # non-int
    assert validate_action(base({"mem_limit_bytes": True}), {"web"}) != []   # bool
    assert validate_action(base({"mem_limit_bytes": -1}), {"web"}) != []     # negative
    assert validate_action(base({"mem_limit_bytes": 0}), {"web"}) != []      # zero
