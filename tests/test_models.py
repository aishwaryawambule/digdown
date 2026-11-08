from digdown.models import (
    ContainerState, HealthStatus, Signal, SignalType,
    Incident, IncidentState, TERMINAL_STATES, error_signature,
)


def test_container_state_defaults():
    s = ContainerState(id="abc", name="web", status="running")
    assert s.health == HealthStatus.NONE
    assert s.restart_count == 0
    assert s.oom_killed is False
    assert s.service is None


def test_signal_dedup_key():
    sig = Signal(source="docker", type=SignalType.CRASH, target="web")
    assert sig.dedup_key == "docker:web:crash"


def test_incident_defaults_to_detected():
    sig = Signal(source="docker", type=SignalType.UNHEALTHY, target="db")
    inc = Incident(source="docker", target="db", signal=sig, dedup_key=sig.dedup_key)
    assert inc.state == IncidentState.DETECTED
    assert inc.id is None
    assert IncidentState.RESOLVED in TERMINAL_STATES
    assert IncidentState.DETECTED not in TERMINAL_STATES


def test_dedup_key_without_check_is_unchanged():
    s = Signal(source="docker", type=SignalType.CRASH, target="web")
    assert s.dedup_key == "docker:web:crash"


def test_dedup_key_includes_check_discriminator():
    s = Signal(source="http", type=SignalType.HTTP_ERROR, target="web", check="home")
    assert s.dedup_key == "http:web:http_error:home"


def test_app_signal_types_exist():
    assert SignalType.HTTP_ERROR.value == "http_error"
    assert SignalType.SLOW_RESPONSE.value == "slow_response"


def test_log_error_spike_signal_type_exists():
    from digdown.models import SignalType
    assert SignalType.LOG_ERROR_SPIKE.value == "log_error_spike"


def test_log_signal_dedup_key_with_check():
    from digdown.models import Signal, SignalType
    s = Signal(source="logs", type=SignalType.LOG_ERROR_SPIKE, target="nginx", check="5xx")
    assert s.dedup_key == "logs:nginx:log_error_spike:5xx"


def test_muted_is_terminal():
    # A muted incident is done — it must not reopen via dedup or be re-diagnosed.
    assert IncidentState.MUTED in TERMINAL_STATES


def test_mute_matches_exact_and_prefix():
    from digdown.models import Mute
    m = Mute(pattern="blog")
    assert m.matches("blog") is True                 # exact
    assert m.matches("blog-api_app-1") is True     # whole stack via prefix
    assert m.matches("grafana") is False
    assert m.matches("") is False


def _grafana_signal(labels, check=None):
    return Signal(source="grafana", type=SignalType.ALERT_FIRING,
                  target="shop-api", check=check or labels.get("alertname"),
                  detail={"labels": labels})


def test_error_signature_falls_back_to_alertname():
    sig = Signal(source="grafana", type=SignalType.ALERT_FIRING,
                 target="shop-api", check="HighErrorRate",
                 detail={"labels": {"severity": "critical"}})
    assert error_signature(sig) == "shop-api|higherrorrate"


def test_error_signature_falls_back_to_signal_type():
    sig = Signal(source="docker", type=SignalType.OOM, target="web")
    assert error_signature(sig) == "web|oom"


def test_incident_populates_error_signature_on_construction():
    sig = Signal(source="docker", type=SignalType.OOM, target="web")
    inc = Incident(source="docker", target="web", signal=sig, dedup_key=sig.dedup_key)
    assert inc.error_signature == "web|oom"
    assert inc.recurred_after_fix is False


def test_incident_preserves_explicit_error_signature():
    sig = Signal(source="docker", type=SignalType.OOM, target="web")
    inc = Incident(source="docker", target="web", signal=sig,
                   dedup_key=sig.dedup_key, error_signature="preset")
    assert inc.error_signature == "preset"
