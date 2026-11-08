from digdown.alerts import parse_grafana_alerts
from digdown.models import SignalType


def _payload(status="firing", labels=None, ann=None, fp="fp1"):
    return {"alerts": [{"status": status, "labels": labels or {},
                        "annotations": ann or {}, "fingerprint": fp,
                        "valueString": "value=2500", "startsAt": "2026-01-01T00:00:00Z"}]}


def test_firing_alert_becomes_signal_with_target_from_label():
    firing, resolved = parse_grafana_alerts(
        _payload(labels={"target": "shop-api", "alertname": "HighErrorRate"},
                 ann={"summary": "error rate above 5% for 10 minutes"}))
    assert resolved == []
    assert len(firing) == 1
    s = firing[0]
    assert s.source == "grafana" and s.type == SignalType.ALERT_FIRING
    assert s.target == "shop-api" and s.check == "HighErrorRate"
    assert s.detail["summary"].startswith("error rate")
    assert s.detail["fingerprint"] == "fp1"


def test_target_fallbacks_then_alertname_then_external():
    f1, _ = parse_grafana_alerts(_payload(labels={"app": "a1", "alertname": "X"}))
    assert f1[0].target == "a1"                       # falls back to 'app'
    f2, _ = parse_grafana_alerts(_payload(labels={"alertname": "OnlyName"}))
    assert f2[0].target == "OnlyName"                 # falls back to alertname
    f3, _ = parse_grafana_alerts(_payload(labels={}))
    assert f3[0].target == "external"                 # final fallback


def test_resolved_alert_returns_fingerprint():
    firing, resolved = parse_grafana_alerts(_payload(status="resolved", fp="fpZ"))
    assert firing == [] and resolved == ["fpZ"]


def test_custom_target_label():
    firing, _ = parse_grafana_alerts(_payload(labels={"service": "svcA"}),
                                     target_label="service")
    assert firing[0].target == "svcA"


def test_empty_and_malformed_payloads_are_safe():
    assert parse_grafana_alerts({}) == ([], [])
    assert parse_grafana_alerts({"alerts": []}) == ([], [])
    assert parse_grafana_alerts({"alerts": ["not-a-dict"]}) == ([], [])


def test_detail_carries_metric_values_severity_and_runbook_links():
    # A rich grafana alert must surface the QUANTIFIED symptom (the evaluated
    # metric values + valueString), the severity, and the human-written
    # runbook/dashboard links — that is what lets the Diagnostician reason from
    # real evidence instead of hallucinating a cause from the alert name.
    payload = {"alerts": [{
        "status": "firing", "fingerprint": "fp1",
        "labels": {"target": "shop-api", "alertname": "High5xx",
                   "severity": "critical"},
        "annotations": {"summary": "5xx rate high",
                        "runbook_url": "https://wiki/runbooks/5xx"},
        "dashboardURL": "https://g/d/abc",
        "panelURL": "https://g/d/abc?panelId=3",
        "generatorURL": "https://g/alerting/grafana/rule1/view",
        "values": {"A": 12.5},
        "valueString": "[ var='A' metric='http_5xx_rate' value=12.5 ]",
    }]}
    firing, _ = parse_grafana_alerts(payload)
    d = firing[0].detail
    assert d["severity"] == "critical"
    assert d["runbook_url"] == "https://wiki/runbooks/5xx"
    assert d["dashboard_url"] == "https://g/d/abc"
    assert d["panel_url"].endswith("panelId=3")
    assert d["generator_url"].endswith("/view")
    assert d["values"] == {"A": 12.5}
    assert "http_5xx_rate" in d["value"]


def test_detail_omits_absent_enrichment_keys():
    # Thin alerts must not carry empty enrichment keys — empty noise in the brief
    # is exactly what nudges the model toward a fabricated diagnosis.
    firing, _ = parse_grafana_alerts(
        {"alerts": [{"status": "firing", "labels": {"alertname": "X"}}]})
    d = firing[0].detail
    for k in ("severity", "runbook_url", "dashboard_url", "panel_url",
              "generator_url", "values"):
        assert k not in d
