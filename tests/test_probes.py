from digdown.models import Signal, SignalType
from digdown.probes import HttpCheck, HttpProbe


def fetch_for(status=200, latency=10.0, body="ok", error=None):
    return lambda url, timeout: (status, latency, body, error)


def _check(**kw):
    base = dict(name="home", url="http://web:8000/health", target="web")
    base.update(kw)
    return HttpCheck(**base)


def test_healthy_check_yields_no_signal():
    probe = HttpProbe([_check()], fetch=fetch_for(status=200, latency=10.0))
    assert probe.poll() == []


def test_5xx_yields_http_error():
    probe = HttpProbe([_check()], fetch=fetch_for(status=503))
    sigs = probe.poll()
    assert [s.type for s in sigs] == [SignalType.HTTP_ERROR]
    assert sigs[0].target == "web" and sigs[0].check == "home"
    assert sigs[0].detail["status_code"] == 503


def test_connection_error_yields_http_error():
    probe = HttpProbe([_check()], fetch=fetch_for(status=None, error="timed out"))
    sigs = probe.poll()
    assert sigs[0].type == SignalType.HTTP_ERROR
    assert sigs[0].detail["error"] == "timed out"


def test_slow_success_yields_slow_response():
    probe = HttpProbe([_check(latency_threshold_ms=1000)],
                      fetch=fetch_for(status=200, latency=2500.0))
    sigs = probe.poll()
    assert [s.type for s in sigs] == [SignalType.SLOW_RESPONSE]
    assert sigs[0].detail["latency_ms"] == 2500


def test_verify_passes_when_healthy():
    probe = HttpProbe([_check()], fetch=fetch_for(status=200))
    sig = Signal(source="http", type=SignalType.HTTP_ERROR, target="web", check="home")
    ok, _ = probe.verify(sig)
    assert ok is True


def test_verify_fails_when_still_erroring():
    probe = HttpProbe([_check()], fetch=fetch_for(status=500))
    sig = Signal(source="http", type=SignalType.HTTP_ERROR, target="web", check="home")
    ok, _ = probe.verify(sig)
    assert ok is False


def test_verify_returns_none_for_foreign_signal():
    probe = HttpProbe([_check()], fetch=fetch_for())
    assert probe.verify(Signal(source="docker", type=SignalType.CRASH, target="web")) is None
    assert probe.verify(Signal(source="http", type=SignalType.HTTP_ERROR,
                               target="web", check="unknown")) is None


def test_build_http_probe_from_config():
    from digdown.config import Config, ProbesConfig, HttpCheckConfig
    from digdown.probes import build_http_probe, probe_hosts
    cfg = Config(probes=ProbesConfig(http=[
        HttpCheckConfig(name="h", url="http://api:80/p", target="web")]))
    probe = build_http_probe(cfg)
    assert probe is not None and probe.checks[0].name == "h"
    assert "api" in probe_hosts(cfg)


def test_build_http_probe_none_when_empty():
    from digdown.config import Config
    from digdown.probes import build_http_probe
    assert build_http_probe(Config()) is None


class _FakeLogSource:
    def __init__(self, text=""):
        self._text = text

    def get_logs(self, name, lines=200, since_seconds=None):
        return self._text


def _log_check(**kw):
    from digdown.probes import LogCheck
    base = dict(target="nginx", name="5xx", patterns=[r"\s5\d\d\s"], threshold=2)
    base.update(kw)
    return LogCheck(**base)


def test_log_spike_fires_at_threshold():
    from digdown.probes import LogScanProbe
    from digdown.models import SignalType
    logs = 'GET /a 500\nGET /b 200\nGET /c 503\n'
    probe = LogScanProbe([_log_check()], source=_FakeLogSource(logs))
    sigs = probe.poll()
    assert [s.type for s in sigs] == [SignalType.LOG_ERROR_SPIKE]
    assert sigs[0].target == "nginx" and sigs[0].check == "5xx"
    assert sigs[0].detail["match_count"] == 2
    assert "window_seconds" in sigs[0].detail


def test_log_below_threshold_is_silent():
    from digdown.probes import LogScanProbe
    probe = LogScanProbe([_log_check(threshold=3)],
                         source=_FakeLogSource("GET /a 500\nGET /b 200\n"))
    assert probe.poll() == []


def test_log_detail_has_no_raw_lines():
    from digdown.probes import LogScanProbe
    logs = "GET /login 500 token=sk-secret-leak\nGET /x 503\n"
    sig = LogScanProbe([_log_check()], source=_FakeLogSource(logs)).poll()[0]
    blob = repr(sig.detail)
    assert "sk-secret-leak" not in blob and "/login" not in blob


def test_log_verify_passes_when_clear():
    from digdown.probes import LogScanProbe
    from digdown.models import Signal, SignalType
    probe = LogScanProbe([_log_check()], source=_FakeLogSource("GET /a 200\n"))
    sig = Signal(source="logs", type=SignalType.LOG_ERROR_SPIKE, target="nginx", check="5xx")
    ok, _ = probe.verify(sig)
    assert ok is True


def test_log_verify_fails_when_still_spiking():
    from digdown.probes import LogScanProbe
    from digdown.models import Signal, SignalType
    probe = LogScanProbe([_log_check()], source=_FakeLogSource("500\n503\n500\n"))
    sig = Signal(source="logs", type=SignalType.LOG_ERROR_SPIKE, target="nginx", check="5xx")
    ok, _ = probe.verify(sig)
    assert ok is False


def test_log_verify_returns_none_for_foreign_signal():
    from digdown.probes import LogScanProbe
    from digdown.models import Signal, SignalType
    probe = LogScanProbe([_log_check()], source=_FakeLogSource(""))
    assert probe.verify(Signal(source="docker", type=SignalType.CRASH, target="nginx")) is None
    assert probe.verify(Signal(source="logs", type=SignalType.LOG_ERROR_SPIKE,
                               target="nginx", check="unknown")) is None


def test_default_patterns_match_5xx_and_traceback_not_benign():
    from digdown.probes import LogScanProbe, LogCheck
    text = ('GET /a 200 OK\n'
            'GET /b 500 Internal Server Error\n'
            'Traceback (most recent call last):\n')
    probe = LogScanProbe([LogCheck(target="web", threshold=2)],  # default patterns
                         source=_FakeLogSource(text))
    sigs = probe.poll()
    assert sigs != []   # the 500 line + the traceback line = 2 matches
    assert sigs[0].detail["match_count"] == 2


def test_consecutive_5xx_lines_all_counted():
    from digdown.probes import LogScanProbe
    # threshold 10 → no signal, but the count must be correct (not undercounted)
    probe2 = LogScanProbe([_log_check(threshold=1)],
                          source=_FakeLogSource("500\n503\n500\n502\n500\n"))
    sig = probe2.poll()[0]
    assert sig.detail["match_count"] == 5


def test_build_log_probe_from_config():
    from digdown.config import Config, ProbesConfig, LogCheckConfig
    from digdown.probes import build_log_probe
    cfg = Config(probes=ProbesConfig(logs=[
        LogCheckConfig(target="nginx", name="5xx")]))
    probe = build_log_probe(cfg, source=_FakeLogSource(""))
    assert probe is not None and probe.checks[0].target == "nginx"
    # empty config patterns → probe uses the default set
    assert probe.checks[0].patterns


def test_build_log_probe_none_when_empty():
    from digdown.config import Config
    from digdown.probes import build_log_probe
    assert build_log_probe(Config(), source=_FakeLogSource("")) is None


def test_http_records_ok_status():
    from digdown.probes import HttpProbe
    probe = HttpProbe([_check()], fetch=fetch_for(status=200, latency=12.0))
    probe.poll()
    r = probe.http_status_by_target()["web"]
    assert r["status"] == "ok" and r["code"] == 200 and r["latency_ms"] == 12 and "checked_at" in r


def test_http_records_error_and_slow():
    from digdown.probes import HttpProbe
    p1 = HttpProbe([_check()], fetch=fetch_for(status=503)); p1.poll()
    assert p1.http_status_by_target()["web"]["status"] == "http_error"
    p2 = HttpProbe([_check(latency_threshold_ms=1000)], fetch=fetch_for(status=200, latency=2500.0)); p2.poll()
    assert p2.http_status_by_target()["web"]["status"] == "slow_response"


def test_http_last_results_rebuilt_each_poll_recovers():
    from digdown.probes import HttpProbe
    seq = [(500, 5.0, "", None), (200, 5.0, "ok", None)]
    probe = HttpProbe([_check()], fetch=lambda url, t: seq.pop(0))
    probe.poll()
    assert probe.http_status_by_target()["web"]["status"] == "http_error"
    probe.poll()   # next poll sees a healthy response
    assert probe.http_status_by_target()["web"]["status"] == "ok"  # recovered, not stale


def test_http_worst_status_wins_on_shared_target():
    from digdown.probes import HttpProbe, HttpCheck
    checks = [HttpCheck(name="a", url="http://web/a", target="web"),
              HttpCheck(name="b", url="http://web/b", target="web")]
    fetch = lambda url, t: (500, 5.0, "", None) if url.endswith("/b") else (200, 5.0, "ok", None)
    probe = HttpProbe(checks, fetch=fetch)
    probe.poll()
    assert probe.http_status_by_target()["web"]["status"] == "http_error"
