"""In-memory rolling cpu%/mem% samples per container, for the Fleet sparklines.

Capped and pruned to the live container set so a removed container's history is
dropped. Process memory only — no persistence (a sparkline is a recent-trend
glance; on restart it rebuilds over a few polls)."""
from __future__ import annotations

from collections import deque

from .models import ContainerState

MAX_SAMPLES = 60   # ~15 min at the default 15s poll


class StatsRing:
    def __init__(self, max_samples: int = MAX_SAMPLES):
        self._max = max_samples
        self._rings: dict[str, deque] = {}

    def record(self, states: list[ContainerState]) -> None:
        current = {s.name for s in states}
        for name in list(self._rings):
            if name not in current:
                del self._rings[name]
        for s in states:
            ring = self._rings.get(s.name)
            if ring is None:
                ring = self._rings[s.name] = deque(maxlen=self._max)
            ring.append((s.cpu_percent, s.mem_percent))

    def series(self, name: str) -> dict:
        ring = self._rings.get(name)
        if ring is None:
            return {"cpu": [], "mem": []}
        return {"cpu": [c for c, _ in ring], "mem": [m for _, m in ring]}


class HostRing:
    """Rolling (disk%, mem%) for the single host — for the host-vitals sparklines."""

    def __init__(self, max_samples: int = MAX_SAMPLES):
        self._ring: deque = deque(maxlen=max_samples)

    def record(self, disk_percent, mem_percent) -> None:
        self._ring.append((disk_percent, mem_percent))

    def series(self) -> dict:
        return {"disk": [d for d, _ in self._ring], "mem": [m for _, m in self._ring]}
