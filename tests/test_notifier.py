from digdown.models import Incident, IncidentState, Signal, SignalType
from digdown.notifier import Notifier, WebhookChannel


def _incident(state=IncidentState.PROPOSED):
    sig = Signal(source="docker", type=SignalType.CRASH, target="web")
    return Incident(id=3, source="docker", target="web", signal=sig,
                    dedup_key=sig.dedup_key, state=state)


def test_notify_dispatches_payload_to_channels():
    seen = []
    n = Notifier(channels=[seen.append])
    payload = n.notify("needs_approval", _incident(), "please review")
    assert payload["kind"] == "needs_approval"
    assert payload["incident_id"] == 3
    assert payload["target"] == "web"
    assert payload["state"] == "proposed"
    assert seen[0]["detail"] == "please review"


def test_failing_channel_does_not_raise():
    def boom(_p):
        raise RuntimeError("channel down")
    ok = []
    n = Notifier(channels=[boom, ok.append])   # second still runs
    n.notify("resolved", _incident(IncidentState.RESOLVED))
    assert ok and ok[0]["kind"] == "resolved"


def test_no_channels_is_a_noop():
    assert Notifier().notify("detected", _incident())["kind"] == "detected"


def test_webhook_channel_posts_json():
    import json
    sent = {}
    def fake_post(url, body, headers):
        sent["url"] = url
        sent["payload"] = json.loads(body)
    ch = WebhookChannel("https://hooks.example/x", post=fake_post)
    ch({"kind": "resolved", "incident_id": 1, "target": "web"})
    assert sent["url"] == "https://hooks.example/x"
    assert sent["payload"]["kind"] == "resolved"


def _sent(url, payload=None):
    calls = []
    channel = WebhookChannel(url, post=lambda *a: calls.append(a))
    channel(payload or {"kind": "auto_healed", "incident_id": 1, "target": "web",
                        "detail": "Restarted it."})
    url_sent, body, headers = calls[0]
    return url_sent, body, headers


def test_discord_gets_a_message_it_accepts():
    import json
    _, body, headers = _sent("https://discord.com/api/webhooks/1/abc")
    assert "web was fixed automatically" in json.loads(body)["content"]
    assert "Restarted it." in json.loads(body)["content"]
    # Discord turns away urllib's default user agent.
    assert "Python" not in headers["User-Agent"]


def test_slack_gets_a_message_it_accepts():
    import json
    _, body, _ = _sent("https://hooks.slack.com/services/a/b/c")
    assert "web was fixed automatically" in json.loads(body)["text"]


def test_ntfy_gets_plain_text_with_a_title():
    _, body, headers = _sent("https://ntfy.sh/my-topic")
    assert body == b"Restarted it."
    assert headers["Title"] == "web was fixed automatically"


def test_self_hosted_ntfy_is_recognised():
    _, body, _ = _sent("https://ntfy.example.com/alerts")
    assert body == b"Restarted it."


def test_anything_else_gets_json_with_readable_text_added():
    import json
    _, body, _ = _sent("https://hooks.example/x")
    data = json.loads(body)
    assert data["title"] == "web was fixed automatically" and data["kind"] == "auto_healed"
    assert data["message"] == "Restarted it."


def test_every_kind_has_readable_wording():
    from digdown.notifier import title_for
    for kind in ["needs_approval", "needs_manual", "resolved", "rolled_back", "failed",
                 "regression", "auto_healed"]:
        assert "web" in title_for(kind, "web") and kind not in title_for(kind, "web")
