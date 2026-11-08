"""Read-only introspection of the host the Digdown BACKEND runs on, for the
Diagnostician. Every command is a fixed allow-list run as an argv list — no
shell, no caller input beyond the configured disk_path. Any failure (missing
binary, timeout, non-zero exit) degrades to '(unavailable)' rather than raising.
Sorting/trimming is done in Python, never via shell pipes."""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Callable, Optional

Runner = Callable[[list], str]

_UNITS = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4, "P": 1024 ** 5}

_MEMINFO = "/proc/meminfo"


def _mem_percent() -> Optional[float]:
    total = avail = None
    with open(_MEMINFO, "r") as f:
        for line in f:
            if line.startswith("MemTotal:"):
                total = float(line.split()[1])
            elif line.startswith("MemAvailable:"):
                avail = float(line.split()[1])
            if total is not None and avail is not None:
                break
    if total and avail is not None:
        return round((1 - avail / total) * 100, 1)
    return None


def host_vitals(disk_path: str) -> dict:
    """Light, always-available host metrics for the UI host card — stdlib only,
    no shell, ungated. Each field degrades to None when unavailable; never raises."""
    out = {"disk_percent": None, "load1": None, "mem_percent": None}
    try:
        u = shutil.disk_usage(disk_path)
        out["disk_percent"] = round(u.used / u.total * 100, 1) if u.total else None
    except Exception:  # noqa: BLE001 - degrade, never break the card
        pass
    try:
        out["load1"] = round(os.getloadavg()[0], 2)
    except Exception:  # noqa: BLE001 - getloadavg absent on some platforms
        pass
    try:
        out["mem_percent"] = _mem_percent()
    except Exception:  # noqa: BLE001 - /proc/meminfo absent on non-Linux
        pass
    return out


def _default_runner(timeout: float = 5.0) -> Runner:
    def run(argv: list) -> str:
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            return p.stdout if p.returncode == 0 else ""
        except Exception:  # noqa: BLE001 - missing binary / timeout / OS error → degrade
            return ""
    return run


def _top_lines(text: str, n: int) -> str:
    return "\n".join([ln for ln in (text or "").splitlines() if ln.strip()][:n])


def _section(title: str, body: str) -> str:
    body = (body or "").rstrip()
    return f"[{title}]\n{body if body else '(unavailable)'}"


def _to_bytes(h: str) -> float:
    h = h.strip()
    if not h:
        return 0.0
    unit = h[-1].upper()
    try:
        return float(h[:-1]) * _UNITS[unit] if unit in _UNITS else float(h)
    except ValueError:
        return 0.0


def _du_top(du_raw: str, n: int) -> str:
    rows = []
    for ln in du_raw.splitlines():
        parts = ln.split("\t", 1) if "\t" in ln else ln.split(None, 1)
        if len(parts) == 2:
            rows.append((parts[0].strip(), parts[1].strip()))
    rows.sort(key=lambda r: _to_bytes(r[0]), reverse=True)
    return "\n".join(f"{size}\t{path}" for size, path in rows[:n])


def _container_table(source) -> str:
    try:
        states = source.list_states()
    except Exception:  # noqa: BLE001 - a degraded source must not break the report
        return ""
    if not states:
        return "(no containers in scope)"
    rows = sorted(states,
                  key=lambda s: s.mem_percent if s.mem_percent is not None else -1.0,
                  reverse=True)
    out = [f"{'NAME':<20} {'STATUS':<10} {'CPU%':>6} {'MEM%':>6}"]
    for s in rows:
        cpu = s.cpu_percent if s.cpu_percent is not None else "-"
        mem = s.mem_percent if s.mem_percent is not None else "-"
        out.append(f"{s.name:<20} {s.status:<10} {str(cpu):>6} {str(mem):>6}")
    return "\n".join(out)


def system_report(source, disk_path: str, runner: Optional[Runner] = None,
                  max_section_lines: int = 15) -> str:
    run = runner or _default_runner()
    n = max_section_lines

    df = _top_lines(run(["df", "-h", disk_path]), 2)
    du = _du_top(run(["du", "-xh", "--max-depth=2", disk_path]), n)
    docker_df = _top_lines(run(["docker", "system", "df"]), n)
    disk_body = "\n".join(b for b in (df, du, docker_df) if b)

    parts = [
        _section("LOAD", _top_lines(run(["uptime"]), 1)),
        _section("MEMORY", _top_lines(run(["free", "-h"]), 3)),
        _section("DISK", disk_body),
        _section("TOP PROCESSES",
                 _top_lines(run(["ps", "-eo", "pid,pcpu,pmem,rss,comm", "--sort=-pcpu"]), n)),
        _section("CONTAINERS", _container_table(source)),
    ]
    return "\n\n".join(parts)
