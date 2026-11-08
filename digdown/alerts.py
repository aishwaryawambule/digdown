"""Parse grafana alerting webhooks into digdown Signals.

Grafana posts {"alerts": [{status, labels, annotations, fingerprint, ...}]} to the
contact-point webhook. We turn each firing alert into a generic ALERT_FIRING signal
(its nature lives in `detail`) and collect resolved alerts' fingerprints so the
endpoint can close their incidents.
"""
from .models import Signal, SignalType

_TARGET_FALLBACKS = ("app", "instance")


def parse_grafana_alerts(payload, target_label: str = "target"):
    alerts = (payload or {}).get("alerts") or []
    firing: list[Signal] = []
    resolved: list[str] = []
    for a in alerts:
        if not isinstance(a, dict):
            continue
        labels = a.get("labels") or {}
        ann = a.get("annotations") or {}
        fp = a.get("fingerprint") or ""
        if (a.get("status") or "firing") == "resolved":
            if fp:
                resolved.append(fp)
            continue
        target = labels.get(target_label)
        if not target:
            for k in _TARGET_FALLBACKS:
                if labels.get(k):
                    target = labels[k]
                    break
        if not target:
            target = labels.get("alertname") or "external"
        alertname = labels.get("alertname") or ann.get("summary") or "grafana-alert"
        detail = {
            "alertname": alertname,
            "summary": ann.get("summary", ""),
            "description": ann.get("description", ""),
            "labels": labels,
            "value": a.get("valueString", ""),
            "fingerprint": fp,
            "startsAt": a.get("startsAt", ""),
        }
        # Enrichment: the quantified symptom (evaluated metric `values`), the
        # severity, and the human-written runbook/dashboard links are what let the
        # Diagnostician reason from real evidence instead of guessing from the
        # alert name. Include a key only when it carries a value, so thin alerts
        # stay clean and don't pad the incident brief with empty noise.
        enrichment = {
            "severity": labels.get("severity"),
            "runbook_url": ann.get("runbook_url") or ann.get("runbook"),
            "dashboard_url": a.get("dashboardURL"),
            "panel_url": a.get("panelURL"),
            "generator_url": a.get("generatorURL"),
            "values": a.get("values"),
        }
        detail.update({k: v for k, v in enrichment.items() if v})
        firing.append(Signal(source="grafana", type=SignalType.ALERT_FIRING,
                             target=target, check=alertname, detail=detail))
    return firing, resolved
