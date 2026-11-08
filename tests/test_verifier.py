from digdown.config import Config
from digdown.models import (ContainerState, HealthStatus, Incident, Signal,
                                  SignalType)
from digdown.verifier import Verifier
from tests.conftest import FakeSource


def _verifier(source, probes=None):
    cfg = Config(verify_polls=2, verify_poll_interval=0)
    return Verifier(source, cfg, sleep=lambda _s: None, probes=probes or [])


def _incident(target, type=SignalType.CRASH, check=None):
    sig = Signal(source="docker", type=type, target=target, check=check)
    return Incident(source="docker", target=target, signal=sig, dedup_key=sig.dedup_key)


def test_healthy_target_verifies():
    src = FakeSource([ContainerState(id="1", name="web", status="running",
                                     health=HealthStatus.HEALTHY)])
    ok, _ = _verifier(src).verify(_incident("web"))
    assert ok is True


def test_running_without_healthcheck_verifies():
    src = FakeSource([ContainerState(id="1", name="web", status="running")])
    ok, _ = _verifier(src).verify(_incident("web"))
    assert ok is True


def test_recrashed_target_fails_verification():
    src = FakeSource([ContainerState(id="1", name="web", status="exited", exit_code=1)])
    ok, detail = _verifier(src).verify(_incident("web"))
    assert ok is False and "exited" in detail


def test_unhealthy_target_fails_verification():
    src = FakeSource([ContainerState(id="1", name="web", status="running",
                                     health=HealthStatus.UNHEALTHY)])
    ok, detail = _verifier(src).verify(_incident("web"))
    assert ok is False and "unhealthy" in detail


def test_missing_target_fails_verification():
    src = FakeSource([ContainerState(id="1", name="other", status="running")])
    ok, detail = _verifier(src).verify(_incident("web"))
    assert ok is False and "missing" in detail.lower()


class _StubHttpProbe:
    def __init__(self, healthy):
        self._healthy = healthy

    def verify(self, signal):
        if signal.type is not SignalType.HTTP_ERROR:
            return None
        return (True, "healthy") if self._healthy else (False, "still 500")


def test_http_incident_verifies_via_probe_when_healthy():
    inc = _incident("web", type=SignalType.HTTP_ERROR, check="home")
    ok, _ = _verifier(FakeSource([]), probes=[_StubHttpProbe(True)]).verify(inc)
    assert ok is True


def test_http_incident_fails_when_probe_still_unhealthy():
    inc = _incident("web", type=SignalType.HTTP_ERROR, check="home")
    ok, detail = _verifier(FakeSource([]), probes=[_StubHttpProbe(False)]).verify(inc)
    assert ok is False and "500" in detail


def test_checks_the_exact_container_even_if_a_service_shares_the_name():
    from digdown.config import Config
    from digdown.models import ContainerState, Signal, SignalType, Incident
    from digdown.verifier import Verifier

    class Source:
        def list_states(self):
            return [ContainerState(id="1", name="other-db-1", service="db", status="exited"),
                    ContainerState(id="2", name="db", service="database", status="running")]

    sig = Signal(source="docker", type=SignalType.CRASH, target="db")
    incident = Incident(source="docker", target="db", signal=sig, dedup_key="k")
    ok, _ = Verifier(Source(), Config(verify_polls=1), sleep=lambda _s: None).verify(incident)
    assert ok
