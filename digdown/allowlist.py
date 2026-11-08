"""The action allow-list and target validation — the safety boundary for what a
plan may contain. Off-list actions are impossible (the type is an enum); every
target must resolve to a real discovered entity (no hallucinated containers),
checked before a proposal is shown and again before execution.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Iterable

from .plan_models import Action, ActionType, RiskLevel

ALLOWED_ACTIONS: set[ActionType] = set(ActionType)
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Per-action risk and required parameters. These are properties of the action
# type, derived deterministically — never taken from the model.
ACTION_RISK: dict[ActionType, RiskLevel] = {
    ActionType.START: RiskLevel.LOW,
    ActionType.RESTART: RiskLevel.LOW,
    ActionType.RECREATE: RiskLevel.MEDIUM,
    ActionType.SET_ENV_VAR: RiskLevel.HIGH,
    ActionType.SET_RESOURCE_LIMIT: RiskLevel.MEDIUM,
    ActionType.CLEAN_DOCKER: RiskLevel.MEDIUM,
}

REQUIRED_PARAMS: dict[ActionType, set[str]] = {
    ActionType.SET_ENV_VAR: {"key", "value"},
    ActionType.SET_RESOURCE_LIMIT: {"mem_limit_bytes"},
}

# What a disk cleanup is aimed at, since it belongs to no container.
HOST_TARGET = "host"

_RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}


def unavailable_actions(compose_path: str) -> set[ActionType]:
    """Actions that can't work on this install. Recreating a container goes through
    `docker compose`, so it needs that command and the stack's Compose file."""
    if shutil.which("docker") and Path(compose_path).exists():
        return set()
    return {ActionType.RECREATE, ActionType.SET_ENV_VAR}


def validate_action(action: Action, valid_targets: Iterable[str]) -> list[str]:
    errors: list[str] = []
    if action.type not in ALLOWED_ACTIONS:
        errors.append(f"action type {action.type!r} is not allowed")
    if action.type == ActionType.CLEAN_DOCKER:
        if action.target != HOST_TARGET:
            errors.append(f"clean_docker can only be aimed at {HOST_TARGET!r}")
    elif action.target not in set(valid_targets):
        errors.append(
            f"target {action.target!r} is not a known container or service")
    for key in REQUIRED_PARAMS.get(action.type, set()):
        if not action.params.get(key):
            errors.append(f"{action.type.value} requires param {key!r}")
    if action.type == ActionType.SET_ENV_VAR:
        # These go into a .env file line by line, so a line break would add settings
        # nobody saw in the plan.
        if not _ENV_NAME.match(str(action.params.get("key", ""))):
            errors.append("set_env_var key must be a plain setting name")
        if re.search(r"[\r\n\0]", str(action.params.get("value", ""))):
            errors.append("set_env_var value must be a single line")
    if action.type == ActionType.SET_RESOURCE_LIMIT:
        v = action.params.get("mem_limit_bytes")
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v <= 0):
            errors.append("set_resource_limit mem_limit_bytes must be a positive integer")
    return errors


def validate_plan(actions: list[Action], valid_targets: Iterable[str]) -> list[str]:
    targets = set(valid_targets)
    errors: list[str] = []
    for action in actions:
        errors.extend(validate_action(action, targets))
    return errors


def plan_risk(actions: list[Action]) -> RiskLevel:
    if not actions:
        return RiskLevel.LOW
    worst = max(actions, key=lambda a: _RISK_ORDER[ACTION_RISK.get(a.type, RiskLevel.LOW)])
    return ACTION_RISK.get(worst.type, RiskLevel.LOW)
