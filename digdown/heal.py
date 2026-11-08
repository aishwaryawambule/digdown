"""Auto heal: decides whether a fix may run with nobody there to approve it.

The AI proposes the fix, but this code decides whether it runs, using only facts
it can check itself. Whenever it can't be sure, the answer is no and a person gets
the normal approval request with the reason attached.
"""
from __future__ import annotations

import math
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from .agent_models import Diagnosis
from .allowlist import HOST_TARGET
from .config import NO_STACK, Config
from .models import ContainerState, HealthStatus, Incident, SignalType, utcnow
from .plan_models import Action, ActionType, Plan

# Recorded as who approved the fix, so the audit log and dashboard can say so.
AUTO_HEAL_IDENTITY = "auto-heal"

# The rule: a fix runs by itself unless it needs someone's knowledge or judgement.
# Left out: PAUSED, because pausing only happens when someone asks for it, and
# ALERT_FIRING, because nothing here could confirm the fix afterwards.
_HEALABLE_SIGNALS = {SignalType.CRASH, SignalType.OOM, SignalType.UNHEALTHY,
                     SignalType.RESTART_LOOP, SignalType.STOPPED, SignalType.CREATED,
                     SignalType.MEMORY_PRESSURE, SignalType.HTTP_ERROR,
                     SignalType.SLOW_RESPONSE, SignalType.LOG_ERROR_SPIKE}
_KEEPS_RUNNING = {"always", "unless-stopped"}
# Docker sees these containers as fine, so only the check that raised them can tell,
# and that same check runs again after the fix.
_APP_SIGNALS = {SignalType.HTTP_ERROR, SignalType.SLOW_RESPONSE, SignalType.LOG_ERROR_SPIKE}
# Fixes that don't change how a container is set up, apart from more memory.
_HEALABLE_ACTIONS = {ActionType.RESTART, ActionType.START, ActionType.SET_RESOURCE_LIMIT}
_MIN_CONFIDENCE = 0.7
_MAX_RAISE_FACTOR = 2
_ATTEMPT_WINDOW = timedelta(hours=24)
# A container already down this long before Digdown noticed may have been stopped
# on purpose before Digdown started, when nothing was watching to tell the difference.
_NOTICED_WITHIN = timedelta(minutes=5)
# Clearing Docker's unused data twice in a day won't free any more.
_MAX_CLEANUPS_PER_DAY = 1
# Across all containers. Many failures at once usually share a cause on the machine,
# which fixing them one by one would hide.
_MAX_HEALS_PER_HOUR = 5


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""  # why a person is being asked; empty when auto heal doesn't apply
    quiet: bool = False  # nothing to tell anyone: the problem has gone away


_NOT_APPLICABLE = Decision(False)


def _no(reason: str) -> Decision:
    return Decision(False, reason)


def _find(target: str, states: list[ContainerState]) -> Optional[ContainerState]:
    """The one container an action means. A service name shared by several isn't
    good enough: many stacks have a service called `db`."""
    for state in states:
        if state.name == target:
            return state
    matches = [s for s in states if s.service == target]
    return matches[0] if len(matches) == 1 else None


def _still_failing(state: ContainerState, incident: Incident, config: Config) -> bool:
    if incident.signal.type in _APP_SIGNALS:
        return True
    if incident.signal.type == SignalType.MEMORY_PRESSURE:
        return state.mem_percent is None or state.mem_percent >= config.mem_percent_threshold
    if state.status != "running" or state.health == HealthStatus.UNHEALTHY:
        return True
    # With a restart policy, a crash loop can look fine between crashes. Restarts
    # since it was noticed show it's still going.
    if incident.signal.type == SignalType.RESTART_LOOP:
        return state.restart_count > incident.signal.detail.get("restart_count", state.restart_count)
    return False


def _is_stopped(state: ContainerState) -> bool:
    """Off, rather than crashing and being restarted by Docker."""
    return state.status not in ("running", "restarting")


def needed_by_a_failing_app(state: ContainerState, states: list[ContainerState]) -> bool:
    """An app in the same stack is failing and depends on this, which shows it's wanted
    however long it has been down. Another stack's `db` doesn't count."""
    return any(other.name != state.name and other.project == state.project
               and state.service in other.depends_on
               and (other.status == "restarting" or other.health == HealthStatus.UNHEALTHY
                    or (other.status in ("exited", "dead") and (other.exit_code or 0) != 0))
               for other in states)


def _stack(state: ContainerState) -> str:
    return state.project or NO_STACK


def _action_problem(action: Action, incident: Incident, states: list[ContainerState],
                    config: Config, stopped_on_purpose: Callable[[str], bool]) -> str:
    if action.type not in _HEALABLE_ACTIONS:
        return "This fix changes how the container is set up, which always needs your approval."
    if incident.signal.type == SignalType.MEMORY_PRESSURE and action.type != ActionType.SET_RESOURCE_LIMIT:
        return "This container is still running, so auto heal only raises its limit."
    state = _find(action.target, states)
    if state is None and sum(s.service == action.target for s in states) > 1:
        return f"\"{action.target}\" matches more than one container, so this needs you to check it."
    if state is None or not config.auto_heals(_stack(state)):
        return f"This fix touches {action.target}, outside the apps you turned auto heal on for."
    if action.type == ActionType.SET_RESOURCE_LIMIT:
        current = state.mem_limit_bytes or 0
        new = action.params.get("mem_limit_bytes") or 0
        if current <= 0 or new <= current:
            return "Auto heal only raises an existing memory limit, and this fix doesn't."
        if new > current * _MAX_RAISE_FACTOR:
            return "This fix more than doubles the memory limit, which needs your approval."
    # Bringing back something else that's down. If the failing app depends on it,
    # that app shows it's wanted, however long it's been down, unless Docker's event
    # log says someone stopped it. Anything else may be off on purpose.
    if state.name != incident.target and _is_stopped(state):
        failing = _find(incident.target, states)
        needed = failing is not None and state.service in failing.depends_on
        if not needed and (state.finished_at is None
                           or incident.created_at - state.finished_at > _NOTICED_WITHIN):
            return f"{state.name} was already down, so it may be off on purpose."
        try:
            if stopped_on_purpose(state.name):
                return f"Someone stopped {state.name} on purpose."
        except Exception:  # noqa: BLE001 - not knowing is the same as a possible yes
            return f"Couldn't confirm that {state.name} wasn't stopped on purpose."
    return ""


def _decide_cleanup(plan: Plan, config: Config, store, now: Optional[datetime]) -> Decision:
    """A full disk has its own switch: deleting can't be undone, and it isn't about
    any one app. Nothing here depends on the AI, because a fixed rule made the plan."""
    if not config.auto_heal_disk_cleanup:
        return _NOT_APPLICABLE
    if config.mode != "execute":
        return _no("Auto heal only runs when Digdown is set to \"Fix after I approve\".")
    if not plan.actions or any(a.type != ActionType.CLEAN_DOCKER for a in plan.actions):
        return _no("This fix isn't the disk cleanup you turned on.")
    since = (now or utcnow()) - _ATTEMPT_WINDOW
    if store.auto_heals_since(since, target=HOST_TARGET) >= _MAX_CLEANUPS_PER_DAY:
        return _no("Docker's unused data was already cleared in the last 24 hours, so "
                   "something else is filling the disk.")
    return Decision(True)


def _own_container_id() -> str:
    return socket.gethostname()  # Docker sets this to the short container id


def decide(incident: Incident, plan: Plan, diagnosis: Optional[Diagnosis], *, config: Config,
           states: list[ContainerState], store, stopped_on_purpose: Callable[[str], bool],
           own_id: Optional[str] = None, now: Optional[datetime] = None) -> Decision:
    """May this plan run by itself? The cheap facts come first, and the question for
    Docker's event log only gets asked when it could change the answer."""
    if incident.signal.type == SignalType.DISK_FULL:
        return _decide_cleanup(plan, config, store, now)
    state = _find(incident.target, states)
    if state is None or not config.auto_heals(_stack(state)):
        return _NOT_APPLICABLE
    if config.mode != "execute":
        return _no("Auto heal only runs when Digdown is set to \"Fix after I approve\".")
    if state.id == (own_id if own_id is not None else _own_container_id()):
        return _no("Digdown won't restart itself.")
    if incident.signal.type not in _HEALABLE_SIGNALS:
        return _no("Someone paused this or an outside alert raised it, so it needs you to "
                   "decide. Digdown couldn't confirm a fix for an outside alert anyway.")
    if not _still_failing(state, incident, config):
        return Decision(False, "The container recovered while the cause was being worked out.",
                        quiet=True)
    if incident.recurred_after_fix:
        return _no("This problem came back after an earlier fix, so that fix isn't enough.")
    if diagnosis is None or not diagnosis.critic_supported or diagnosis.confidence < _MIN_CONFIDENCE:
        return _no("The AI wasn't sure enough of the cause.")
    if plan.kind != "automated" or not plan.critic2_supported or not plan.actions:
        return _no("There's no checked fix to run.")
    for action in plan.actions:
        problem = _action_problem(action, incident, states, config, stopped_on_purpose)
        if problem:
            return _no(problem)

    moment = now or utcnow()
    tried = store.auto_heals_since(moment - _ATTEMPT_WINDOW, target=incident.target)
    if tried >= config.auto_heal_max_attempts:
        return _no(f"Auto heal already tried {tried} times in the last 24 hours, so it stopped.")
    if store.auto_heals_since(moment - timedelta(hours=1)) >= _MAX_HEALS_PER_HOUR:
        return _no(f"Auto heal made {_MAX_HEALS_PER_HOUR} fixes in the last hour, which points "
                   "to a bigger problem on this machine.")

    if _is_stopped(state):
        wanted = needed_by_a_failing_app(state, states)
        # Exiting cleanly with no restart policy is what a migration or a backup does,
        # and running one of those again could do harm.
        if (incident.signal.type == SignalType.STOPPED and not wanted
                and state.restart_policy not in _KEEPS_RUNNING):
            return _no("It exited cleanly and isn't set to keep running, so it may be a "
                       "job that finished. Running it again could repeat its work.")
        # A container stuck in created has never run, so it has no stop time to check.
        if (incident.signal.type != SignalType.CREATED and not wanted
                and (state.finished_at is None
                     or incident.created_at - state.finished_at > _NOTICED_WITHIN)):
            return _no("It was already down when Digdown noticed, so it may be down on purpose.")
        try:
            if stopped_on_purpose(incident.target):
                return _no("Someone stopped this container on purpose.")
        except Exception:  # noqa: BLE001 - not knowing is the same as a possible yes
            return _no("Couldn't confirm that the container wasn't stopped on purpose.")
    return Decision(True)


def keep_after_update_hint(plan: Plan) -> str:
    """A new memory limit lives on the container, not in the Compose file, so the
    next update quietly loses it unless the person copies it across."""
    for action in plan.actions:
        if action.type == ActionType.SET_RESOURCE_LIMIT:
            # Compose's `m` means 1024 x 1024. Round up so the kept limit isn't lower.
            megabytes = math.ceil(action.params.get("mem_limit_bytes", 0) / (1024 * 1024))
            return (f"This limit is lost when the container is recreated, for example by an "
                    f"update. To keep it, add `mem_limit: {megabytes}m` to this service in "
                    f"your Compose file.")
    return ""
