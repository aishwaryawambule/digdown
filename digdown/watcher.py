from __future__ import annotations

import re
from collections import deque
from datetime import timedelta
from typing import Callable, Optional

from .config import Config
from .models import (ContainerState, HealthStatus, Incident, IncidentState,
                     Signal, SignalType, TERMINAL_STATES, utcnow)

# Names of one-shot/init jobs that are MEANT to run once and exit — exempt from
# the STOPPED detector even when they exit cleanly.
_ONESHOT_NAME = re.compile(r"init|migrat|setup|seed|bootstrap|provision", re.I)


def _is_oneshot(s: ContainerState) -> bool:
    return bool(_ONESHOT_NAME.search(s.name)) or s.restart_policy == "on-failure"


# Endings where the problem may well still be there, on purpose: nothing fixed it.
# It stays closed until the signal clears and returns. RESOLVED isn't here because
# a problem still present after a "resolved" means the fix didn't hold.
_STAYS_CLOSED = {IncidentState.CLOSED_NO_FIX, IncidentState.REJECTED,
                 IncidentState.ROLLED_BACK, IncidentState.FAILED}


# Waiting on a person, or on nobody. If the problem goes away these close themselves,
# so a recovered container doesn't stay "open" forever. Anything being worked on is
# left to finish.
_CLOSES_BY_ITSELF = {IncidentState.DETECTED, IncidentState.DIAGNOSED,
                     IncidentState.DIAGNOSIS_FAILED, IncidentState.PROPOSED}
# A linked failure can stop waiting at these. Explain-only mode ends at DIAGNOSED.
_NOTHING_MORE_COMING = TERMINAL_STATES | {IncidentState.DIAGNOSED}
# Checks in a row without the problem before it counts as gone (about a minute at
# the default interval), so a flapping container doesn't close and reopen each time.
_CLEAR_AFTER_POLLS = 4


class Watcher:
    def __init__(self, source, store, bus, config: Config,
                 disk_percent: Optional[Callable[[], float]] = None,
                 project=None, probes=None, stats_ring=None,
                 host_ring=None, host_vitals=None):
        self.source = source
        self.store = store
        self.bus = bus
        self.config = config
        self.disk_percent = disk_percent
        self.project = project  # compose topology for cascade correlation
        self.probes = probes or []
        self.stats_ring = stats_ring
        self.host_ring = host_ring
        self.host_vitals = host_vitals
        self._restart_baseline: dict[str, int] = {}
        self._restart_window: dict[str, deque] = {}
        self._debounce: dict[str, int] = {}
        self._cleared: set[str] = set()  # problems seen to go away since last closed
        self._absent: dict[str, int] = {}  # checks in a row a problem has been gone
        self._key_target: dict[str, str] = {}
        self.last_states: Optional[list[ContainerState]] = None  # from the latest check
        self._restarted_this_poll: set[str] = set()
        self._created_seen: set[str] = set()  # names seen stuck in 'created'
        self._restarting_checks: dict[str, int] = {}  # checks in a row found restarting
        self._stop_checked: dict[str, tuple] = {}  # name -> (when it stopped, on purpose?)

    def detect(self, states: list[ContainerState]) -> list[Signal]:
        signals: list[Signal] = []
        for s in states:
            signals.extend(self._detect_container(s))
        if self.disk_percent is not None:
            pct = self.disk_percent()
            if pct >= self.config.disk_percent_threshold:
                signals.append(Signal(source="host", type=SignalType.DISK_FULL,
                                      target="host", detail={"percent": pct}))
        return signals

    def _stopped_on_purpose(self, s: ContainerState) -> bool:
        """Asked once per stop, soon after it happens, while Docker still remembers."""
        check = getattr(self.source, "stopped_on_purpose", None)
        if check is None or s.oom_killed or s.status not in ("exited", "dead"):
            self._stop_checked.pop(s.name, None)
            return False
        seen = self._stop_checked.get(s.name)
        if seen is not None and seen[0] == s.finished_at:
            return seen[1]
        try:
            on_purpose = bool(check(s.name))
        except Exception:  # noqa: BLE001 - not knowing means treating it as a failure
            on_purpose = False
        self._stop_checked[s.name] = (s.finished_at, on_purpose)
        return on_purpose

    def _detect_container(self, s: ContainerState) -> list[Signal]:
        # Someone stopped it. That isn't a failure, so no incident and no AI call.
        if self._stopped_on_purpose(s):
            return []
        out: list[Signal] = []
        crashed = s.status == "exited" and (s.exit_code or 0) != 0
        if crashed and s.oom_killed:
            out.append(Signal(source="docker", type=SignalType.OOM, target=s.name,
                              detail={"exit_code": s.exit_code}))
        elif crashed:
            out.append(Signal(source="docker", type=SignalType.CRASH, target=s.name,
                              detail={"exit_code": s.exit_code}))
        elif s.oom_killed:
            out.append(Signal(source="docker", type=SignalType.OOM, target=s.name,
                              detail={}))
        elif (s.status in ("exited", "dead") and (s.exit_code or 0) == 0
              and not _is_oneshot(s)):
            # Down but not crashed: a long-running service has exited cleanly (e.g.
            # stopped, host reboot, killed). One-shot/init jobs — recognised by name
            # or an on-failure policy — are MEANT to exit and are exempt.
            out.append(Signal(source="docker", type=SignalType.STOPPED,
                              target=s.name, detail={"restart_policy": s.restart_policy}))
        elif s.status == "paused":
            # A paused container is frozen — effectively down.
            out.append(Signal(source="docker", type=SignalType.PAUSED, target=s.name,
                              detail={}))

        # created-but-never-started: a fresh container is briefly 'created' before
        # the daemon starts it, so only flag one STILL created on a later poll
        # (a genuine failed/blocked start, e.g. bad mount or port conflict).
        if s.status == "created":
            if s.name in self._created_seen:
                out.append(Signal(source="docker", type=SignalType.CREATED,
                                  target=s.name, detail={}))
            self._created_seen.add(s.name)
        else:
            self._created_seen.discard(s.name)

        # Docker keeps the last health result after a container stops, and being
        # stopped is already reported above.
        if s.health == HealthStatus.UNHEALTHY and s.status not in ("exited", "dead"):
            out.append(Signal(source="docker", type=SignalType.UNHEALTHY,
                              target=s.name, detail={}))

        if (s.status == "running" and s.mem_limit_bytes and s.mem_percent is not None
                and s.mem_percent >= self.config.mem_percent_threshold):
            out.append(Signal(source="docker", type=SignalType.MEMORY_PRESSURE,
                              target=s.name,
                              detail={"mem_bytes": s.mem_bytes,
                                      "mem_limit_bytes": s.mem_limit_bytes,
                                      "percent": s.mem_percent}))

        prev = self._restart_baseline.get(s.name)
        if prev is not None:
            window = self._restart_window.setdefault(
                s.name, deque(maxlen=self.config.restart_loop_threshold))
            window.append(max(0, s.restart_count - prev))
        self._restart_baseline[s.name] = s.restart_count
        # Restart-loop = enough restarts within a recent sliding window (not a
        # monotonic total), so a container that stabilizes stops being flagged and
        # a resolved incident does not immediately reopen.
        window = self._restart_window.get(s.name)
        # Docker backs off to a minute between restarts, so a slow loop never adds up
        # within the window, but it's found waiting to restart check after check.
        restarting_checks = self._restarting_checks.get(s.name, 0) + 1 if s.status == "restarting" else 0
        self._restarting_checks[s.name] = restarting_checks
        slow_loop = (restarting_checks >= 2
                     and s.restart_count >= self.config.restart_loop_threshold)
        if slow_loop or (window is not None and sum(window) >= self.config.restart_loop_threshold):
            out.append(Signal(source="docker", type=SignalType.RESTART_LOOP,
                              target=s.name, detail={"restart_count": s.restart_count}))
        return out

    def _correlate(self, target: str) -> Optional[int]:
        """If `target` depends on a service with a recent open incident, this
        failure is likely a cascade dependent — link it to that root incident.
        Docker's labels say this for every stack, and the Compose file, if there is
        one, covers older containers. Dependencies name services while incidents name
        containers such as shop-db-1, and another stack's `db` doesn't count."""
        states = self.last_states or []
        me = next((s for s in states if s.name == target), None)
        deps = list(me.depends_on) if me is not None else []
        if not deps and self.project is not None:
            deps = (self.project.depends_on or {}).get((me.service if me else None) or target, [])
        if not deps:
            return None
        since = (utcnow() - timedelta(
            seconds=self.config.correlation_window_seconds)).isoformat()
        for dep in deps:
            names = [s.name for s in states if s.service == dep
                     and me is not None and s.project == me.project] or [dep]
            for name in names:
                parent = self.store.recent_open_incident_for_target(name, since)
                if parent is not None:
                    return parent.id
        return None

    def _valid_targets(self, states) -> set[str]:
        names: set[str] = set(self.project.services if self.project else [])
        for s in states:
            names.add(s.name)
            if s.service:
                names.add(s.service)
        return names

    def _probe_signals(self, states) -> list[Signal]:
        if not self.probes:
            return []
        valid = self._valid_targets(states)
        out: list[Signal] = []
        for probe in self.probes:
            for sig in probe.poll():
                if sig.target not in valid:
                    self.bus.publish({"type": "signal_dropped",
                                      "reason": "unbound_target",
                                      "signal": sig.model_dump(mode="json")})
                    continue
                out.append(sig)
        return out

    def poll_once(self) -> list[Incident]:
        states = self.source.list_states()
        self.last_states = states
        self._restarted_this_poll = {
            s.name for s in states
            if s.restart_count > self._restart_baseline.get(s.name, s.restart_count)}
        if self.stats_ring is not None:
            self.stats_ring.record(states)
        if self.host_ring is not None and self.host_vitals is not None:
            v = self.host_vitals()
            self.host_ring.record(v.get("disk_percent"), v.get("mem_percent"))
        signals = self.detect(states)
        signals.extend(self._probe_signals(states))
        opened: list[Incident] = []
        seen_keys: set[str] = set()
        # Muted targets are silenced on purpose (e.g. a stack taken down): drop
        # their signals before they ever become incidents — no diagnosis, no
        # alerts. Reported as a dropped signal so the choice stays observable.
        mutes = self.store.list_mutes()
        for sig in signals:
            if any(m.matches(sig.target) for m in mutes):
                self.bus.publish({"type": "signal_dropped", "reason": "muted",
                                  "signal": sig.model_dump(mode="json")})
                continue
            key = sig.dedup_key
            seen_keys.add(key)
            self._key_target[key] = sig.target
            self._debounce[key] = self._debounce.get(key, 0) + 1
            if self._debounce[key] < self.config.debounce_polls:
                continue
            if self.store.open_incident_by_dedup(key) is not None:
                continue
            if self._closed_and_never_cleared(key):
                continue
            self._cleared.discard(key)
            parent_id = self._correlate(sig.target)
            inc = Incident(source=sig.source, target=sig.target, signal=sig,
                           dedup_key=key, state=IncidentState.DETECTED,
                           parent_incident_id=parent_id)
            inc = self.store.add_incident(inc)
            self.store.append_audit(inc.id, actor="watcher", event="detected",
                                    payload=sig.model_dump(mode="json"))
            self.bus.publish({"type": "incident_created",
                              "incident": inc.model_dump(mode="json")})
            opened.append(inc)
        for key in list(self._debounce):
            if key not in seen_keys:
                self._debounce[key] = 0
        self._note_recoveries(seen_keys, states)
        self._close_recovered()
        opened.extend(self._release_linked())
        return opened

    def _release_linked(self) -> list[Incident]:
        """A failure linked to another waits for that one to be dealt with, since
        fixing it often fixes both. Once that's over, one still down has a problem of
        its own and needs its own diagnosis. It gets the time a recovery takes to
        register first, because the usual case is an app reconnecting to its database."""
        grace = timedelta(seconds=_CLEAR_AFTER_POLLS * self.config.poll_interval_seconds)
        released: list[Incident] = []
        for incident in self.store.incidents_in_state(IncidentState.DETECTED):
            if incident.parent_incident_id is None:
                continue
            parent = self.store.get_incident(incident.parent_incident_id)
            over = parent is None or parent.state in _NOTHING_MORE_COMING
            if not over or (parent is not None and utcnow() - parent.updated_at < grace):
                continue
            self.store.set_parent_incident(incident.id, None)
            incident.parent_incident_id = None
            released.append(incident)
        return released

    def _note_recoveries(self, seen_keys: set[str], states) -> None:
        """Count, per problem, the checks in a row it has been gone. A container that
        is restarting, or restarted since the last check, hasn't recovered even if no
        signal fired: Docker backs off to a minute between restarts, so a crash loop
        spends most of its time looking quiet."""
        unsettled = {s.name for s in states
                     if s.status == "restarting" or s.name in self._restarted_this_poll}
        known = set(self._debounce) | {i.dedup_key for i in
                                       self.store.incidents_in_states(_CLOSES_BY_ITSELF)}
        for key in known:
            target = self._key_target.get(key)
            if key in seen_keys or target in unsettled:
                self._absent[key] = 0
                continue
            self._absent[key] = self._absent.get(key, 0) + 1
            if self._absent[key] >= _CLEAR_AFTER_POLLS:
                self._cleared.add(key)

    def _close_recovered(self) -> None:
        for incident in self.store.incidents_in_states(_CLOSES_BY_ITSELF):
            # Grafana says when its own alerts resolve.
            if incident.source == "grafana":
                continue
            if self._absent.get(incident.dedup_key, 0) < _CLEAR_AFTER_POLLS:
                continue
            self.store.update_state(incident.id, IncidentState.RESOLVED)
            incident.state = IncidentState.RESOLVED
            self.store.append_audit(incident.id, actor="watcher", event="cleared", payload={})
            self.bus.publish({"type": "incident_updated",
                              "incident": incident.model_dump(mode="json")})

    def _closed_and_never_cleared(self, key: str) -> bool:
        latest = self.store.latest_incident_by_dedup(key)
        return (latest is not None and latest.state in _STAYS_CLOSED
                and key not in self._cleared)
