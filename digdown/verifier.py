"""Post-execution verification: poll for a healthy, stable window. For Docker
signals that means the container is running and not unhealthy; for app-level
signals it means the probe that raised the signal re-checks clean.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from .config import Config
from .models import HealthStatus, SignalType


class Verifier:
    def __init__(self, source, config: Config,
                 sleep: Callable[[float], None] = time.sleep, probes=None,
                 disk_percent: Optional[Callable[[], float]] = None):
        self.source = source
        self.config = config
        self._sleep = sleep
        self.probes = probes or []
        self.disk_percent = disk_percent

    def _find(self, target: str):
        states = self.source.list_states()
        for s in states:
            if s.name == target:
                return s
        for s in states:
            if s.service == target:
                return s
        return None

    def _probe_for(self, signal):
        for p in self.probes:
            if p.verify(signal) is not None:
                return p
        return None

    def _docker_check(self, target: str) -> tuple[bool, str]:
        s = self._find(target)
        if s is None:
            return False, f"{target} is missing"
        if s.status != "running":
            return False, f"{target} is {s.status} (exit_code={s.exit_code})"
        if s.health == HealthStatus.UNHEALTHY:
            return False, f"{target} is running but unhealthy"
        return True, "ok"

    def _disk_check(self) -> tuple[bool, str]:
        if self.disk_percent is None:
            return False, "the disk can't be checked here"
        percent = self.disk_percent()
        if percent >= self.config.disk_percent_threshold:
            return False, f"the disk is still {percent:.0f}% full"
        return True, "ok"

    def _memory_check(self, target: str) -> tuple[bool, str]:
        ok, detail = self._docker_check(target)
        if not ok:
            return ok, detail
        percent = self._find(target).mem_percent
        if percent is not None and percent >= self.config.mem_percent_threshold:
            return False, f"{target} is still using {percent:.0f}% of its memory limit"
        return True, "ok"

    def _check(self, incident, probe) -> tuple[bool, str]:
        if probe is not None:
            return probe.verify(incident.signal)
        if incident.signal.type == SignalType.DISK_FULL:
            return self._disk_check()
        if incident.signal.type == SignalType.MEMORY_PRESSURE:
            return self._memory_check(incident.target)
        return self._docker_check(incident.target)

    def verify(self, incident) -> tuple[bool, str]:
        polls = max(1, self.config.verify_polls)
        probe = self._probe_for(incident.signal)
        # An app needs time to start answering after a restart, though Docker reports
        # it as running straight away, and Docker can hold a crash-looping one back for
        # up to a minute even once its cause is fixed. Only checks made before its first
        # good answer get this allowance, and it must pass every check after that.
        allowed_misses = 0
        if probe is not None:
            allowed_misses = polls
        elif incident.signal.type == SignalType.RESTART_LOOP:
            allowed_misses = polls * 2
        passes = misses = 0
        while passes < polls:
            if passes or misses:
                self._sleep(self.config.verify_poll_interval)
            ok, detail = self._check(incident, probe)
            if ok:
                passes += 1
            elif passes or misses >= allowed_misses:
                return False, detail
            else:
                misses += 1
        kind = "healthy" if probe is not None else "stable"
        return True, f"{incident.target} {kind} across {polls} checks"
