"""Notifier — pushes the moments a human cares about (needs approval,
resolved, rolled back, needs manual) to external channels, written so they make
sense on a phone at 3am. Channels are plain callables taking a payload dict.
A failing channel never breaks the pipeline.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any, Callable
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

Channel = Callable[[dict[str, Any]], None]

_TITLES = {
    "auto_healed": "{target} was fixed automatically",
    "needs_approval": "{target} needs your decision",
    "needs_manual": "{target} needs a manual fix",
    "resolved": "{target} is fixed",
    "rolled_back": "A fix for {target} didn't work and was undone",
    "failed": "A fix for {target} couldn't be applied",
    "regression": "The problem with {target} came back",
    "test": "Test alert from Digdown",
}
_DISCORD_LIMIT = 2000


def title_for(kind: str, target: str) -> str:
    return _TITLES.get(kind, "Update on {target}").format(target=target)


class Notifier:
    def __init__(self, channels: list[Channel] | None = None):
        self.channels = channels or []

    def notify(self, kind: str, incident, detail: str = "") -> dict[str, Any]:
        state = getattr(incident.state, "value", incident.state)
        payload = {
            "kind": kind,
            "incident_id": incident.id,
            "target": incident.target,
            "signal": getattr(incident.signal.type, "value", None),
            "state": state,
            "detail": detail,
        }
        for channel in self.channels:
            try:
                channel(payload)
            except Exception as e:  # noqa: BLE001 - never break the caller
                log.warning("notify channel failed: %s", e)
        return payload


def _default_post(url: str, body: bytes, headers: dict[str, str]) -> None:
    urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers),
                           timeout=5).close()


class WebhookChannel:
    """Sends each service the format it accepts: Discord and Slack each want their
    own field, and ntfy shows the body as the message, so JSON would reach a phone
    as raw JSON. Anything else gets the full JSON payload."""

    def __init__(self, url: str,
                 post: Callable[[str, bytes, dict[str, str]], None] = _default_post):
        self.url = url
        self._post = post

    def __call__(self, payload: dict[str, Any]) -> None:
        title = title_for(payload.get("kind", ""), payload.get("target", ""))
        message = payload.get("detail") or title
        # Discord refuses urllib's default user agent.
        headers = {"Content-Type": "application/json", "User-Agent": "Digdown"}
        host = (urlsplit(self.url).hostname or "").lower()
        if host in ("discord.com", "discordapp.com"):
            text = f"**{title}**\n{message}"[:_DISCORD_LIMIT]
            body = json.dumps({"content": text}).encode()
        elif host == "hooks.slack.com":
            body = json.dumps({"text": f"*{title}*\n{message}"}).encode()
        elif "ntfy" in host:
            headers["Content-Type"] = "text/plain; charset=utf-8"
            headers["Title"] = title.encode("ascii", "replace").decode()  # headers can't carry UTF-8
            body = message.encode()
        else:
            body = json.dumps({**payload, "title": title, "message": message}).encode()
        self._post(self.url, body, headers)
