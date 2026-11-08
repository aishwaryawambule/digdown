"""App-level detection behind a Probe seam.

A Probe is polled each tick and returns Signals into the same pipeline the
Docker watcher feeds. HTTP probing is GET-only against user-configured URLs and
adds no runtime dependency (stdlib urllib). The default fetch is injectable so
unit tests never touch the network.
"""
from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

from .models import Signal, SignalType, utcnow


class Probe(Protocol):
    name: str

    def poll(self) -> list[Signal]: ...

    # Re-check a signal's target after a fix. None => "not my signal".
    def verify(self, signal: Signal) -> Optional[tuple[bool, str]]: ...


@dataclass
class HttpCheck:
    name: str
    url: str
    target: str
    expect_status: str = "2xx-3xx"
    timeout_ms: int = 5000
    latency_threshold_ms: int = 2000


# fetch(url, timeout_ms) -> (status|None, latency_ms, body, error|None)
Fetch = Callable[[str, int], "tuple[Optional[int], float, str, Optional[str]]"]


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Override redirect handler to stop urllib from following 3xx responses.

    An allowed host must not be able to 302 the agent to an internal/metadata
    endpoint that is NOT on the allowlist.  We return the 3xx as-is instead of
    transparently following it.  The status is still an acceptable value for the
    default ``expect_status="2xx-3xx"`` HttpCheck band, so synthetic health
    checks are unaffected.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Returning None tells urllib to raise HTTPError for the redirect,
        # which we catch below and surface as a normal (code, latency, body)
        # tuple — no redirect is followed.
        return None


_no_redirect_opener = urllib.request.build_opener(_NoRedirectHandler)


def urllib_fetch(url: str, timeout_ms: int):
    start = time.monotonic()
    try:
        with _no_redirect_opener.open(  # noqa: S310 - GET only, user-configured URL
                url, timeout=timeout_ms / 1000.0) as resp:
            body = resp.read(64_000).decode("utf-8", "replace")
            return resp.status, (time.monotonic() - start) * 1000.0, body, None
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read(64_000).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        return e.code, (time.monotonic() - start) * 1000.0, body, None
    except Exception as e:  # noqa: BLE001 - timeout / connection refused
        return None, (time.monotonic() - start) * 1000.0, "", str(e)


_HTTP_RANK = {"ok": 0, "slow_response": 1, "http_error": 2}


def _worse_http(prev, new):
    if prev is None:
        return new
    return new if _HTTP_RANK[new["status"]] >= _HTTP_RANK[prev["status"]] else prev


def _status_ok(status: Optional[int], expect: str) -> bool:
    if status is None:
        return False
    for band in (expect or "2xx-3xx").lower().split("-"):
        band = band.strip()
        if band.endswith("xx"):
            lo = int(band[0]) * 100
            if lo <= status < lo + 100:
                return True
        elif band.isdigit() and status == int(band):
            return True
    return False


class HttpProbe:
    name = "http"

    def __init__(self, checks: list[HttpCheck], fetch: Fetch = urllib_fetch):
        self.checks = checks
        self._fetch = fetch
        self._by_name = {c.name: c for c in checks}
        self.last_results: dict = {}

    def _run(self, c: HttpCheck):
        status, latency_ms, _body, error = self._fetch(c.url, c.timeout_ms)
        lat = round(latency_ms)
        result = {"check": c.name, "status": "ok", "code": status, "latency_ms": lat}
        sig = None
        if error is not None or not _status_ok(status, c.expect_status):
            result["status"] = "http_error"
            sig = Signal(source="http", type=SignalType.HTTP_ERROR, target=c.target,
                         check=c.name, detail={"url": c.url, "status_code": status,
                                               "latency_ms": lat, "error": error})
        elif latency_ms > c.latency_threshold_ms:
            result["status"] = "slow_response"
            sig = Signal(source="http", type=SignalType.SLOW_RESPONSE, target=c.target,
                         check=c.name, detail={"url": c.url, "status_code": status,
                                               "latency_ms": lat,
                                               "threshold_ms": c.latency_threshold_ms})
        return result, sig

    def _evaluate(self, c: HttpCheck) -> Optional[Signal]:
        return self._run(c)[1]

    def poll(self) -> list[Signal]:
        sigs = []
        fresh: dict = {}
        ts = utcnow().isoformat()
        for c in self.checks:
            result, sig = self._run(c)
            result["checked_at"] = ts
            fresh[c.target] = _worse_http(fresh.get(c.target), result)
            if sig is not None:
                sigs.append(sig)
        self.last_results = fresh
        return sigs

    def http_status_by_target(self) -> dict:
        return dict(self.last_results)

    def verify(self, signal: Signal) -> Optional[tuple[bool, str]]:
        if signal.type not in (SignalType.HTTP_ERROR, SignalType.SLOW_RESPONSE):
            return None
        c = self._by_name.get(signal.check)
        if c is None:
            return None
        sig = self._evaluate(c)
        if sig is None:
            return True, f"{c.name} healthy ({c.url})"
        return False, f"{c.name} still failing: {sig.detail}"


def build_http_probe(config, fetch: Fetch = urllib_fetch) -> Optional[HttpProbe]:
    checks = [HttpCheck(name=c.name, url=c.url, target=c.target,
                        expect_status=c.expect_status, timeout_ms=c.timeout_ms,
                        latency_threshold_ms=c.latency_threshold_ms)
              for c in config.probes.http]
    return HttpProbe(checks, fetch=fetch) if checks else None


def probe_hosts(config) -> set[str]:
    from urllib.parse import urlparse
    hosts = {urlparse(c.url).hostname for c in config.probes.http}
    return {h for h in hosts if h}


def build_log_probe(config, source) -> Optional[LogScanProbe]:
    checks = [LogCheck(target=c.target, name=c.name,
                       patterns=c.patterns or list(DEFAULT_LOG_PATTERNS),
                       threshold=c.threshold, window_seconds=c.window_seconds,
                       max_lines=c.max_lines)
              for c in config.probes.logs]
    return LogScanProbe(checks, source) if checks else None


DEFAULT_LOG_PATTERNS = [
    r"\s5\d\d\s",                            # HTTP 5xx in proxy/access logs
    r"Traceback \(most recent call last\)",  # python traceback
    r"\bERROR\b",                            # generic error level
]


@dataclass
class LogCheck:
    target: str
    name: str = "errors"
    patterns: list = field(default_factory=lambda: list(DEFAULT_LOG_PATTERNS))
    threshold: int = 5
    window_seconds: int = 60
    max_lines: int = 1000


class LogScanProbe:
    name = "logs"

    def __init__(self, checks: list, source):
        self.checks = checks
        self.source = source
        self._by_name = {c.name: c for c in checks}
        self._compiled = {c.name: [re.compile(p) for p in c.patterns] for c in checks}

    def poll(self) -> list:
        return [s for c in self.checks if (s := self._evaluate(c)) is not None]

    def _count(self, c: LogCheck) -> int:
        text = self.source.get_logs(c.target, lines=c.max_lines,
                                    since_seconds=c.window_seconds)
        regexes = self._compiled[c.name]
        count = 0
        for line in text.splitlines():
            padded = "\n" + line + "\n"      # both boundaries are whitespace
            if any(r.search(padded) for r in regexes):
                count += 1
        return count

    def _evaluate(self, c: LogCheck) -> Optional[Signal]:
        count = self._count(c)
        if count >= c.threshold:
            return Signal(source="logs", type=SignalType.LOG_ERROR_SPIKE,
                          target=c.target, check=c.name,
                          detail={"match_count": count, "threshold": c.threshold,
                                  "window_seconds": c.window_seconds})
        return None

    def verify(self, signal: Signal) -> Optional[tuple[bool, str]]:
        if signal.source != self.name:        # self.name == "logs"
            return None
        if signal.type is not SignalType.LOG_ERROR_SPIKE:
            return None
        c = self._by_name.get(signal.check)
        if c is None:
            return None
        count = self._count(c)
        if count < c.threshold:
            return True, f"{c.name}: {count} matches (< {c.threshold})"
        return False, f"{c.name}: still {count} matches (>= {c.threshold})"
