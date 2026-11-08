"""Who may talk to the API.

Two modes, chosen by whether a token is configured:

* Token set: every /api call needs it, as a header or the dashboard's session
  cookie. Reads are covered too, because logs and diagnoses can be sensitive.
* No token: Digdown answers only requests addressed to this computer. That also
  stops DNS-rebinding pages, which reach localhost under a hostile hostname.

In both modes a browser page from another site can't change anything. Approve
takes no meaningful body, so without this check any website you visit could
approve a fix on localhost with a plain cross-site POST.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

SESSION_COOKIE = "digdown_session"
SESSION_MAX_AGE = 30 * 24 * 3600
OPEN_PATHS = {"/api/health", "/api/session"}
LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1"}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def hostname_of(host_header: str) -> str:
    """'localhost:8088' -> 'localhost', '[::1]:8088' -> '::1'."""
    return (urlsplit(f"//{host_header}").hostname or "").lower()


def is_loopback_bind(host: str) -> bool:
    return host.lower() in {"127.0.0.1", "localhost", "::1"}


def session_value(token: str) -> str:
    """What the cookie holds: derived from the token, so a leaked cookie doesn't
    reveal it, and changing the token ends every session."""
    return hmac.new(token.encode(), b"digdown-session", hashlib.sha256).hexdigest()


def token_matches(provided: str | None, token: str) -> bool:
    return bool(provided) and hmac.compare_digest(provided.encode(), token.encode())


def _cookie(headers: dict[str, str], name: str) -> str | None:
    for part in headers.get("cookie", "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return None


def _is_cross_site(headers: dict[str, str]) -> bool:
    if headers.get("sec-fetch-site") == "cross-site":
        return True
    origin = headers.get("origin")
    if not origin:
        return False  # not a browser form or fetch: curl, Grafana, the CLI
    origin_host = urlsplit(origin).netloc.lower()
    allowed = {headers.get("host", "").lower(),
               headers.get("x-forwarded-host", "").lower()}
    return origin_host not in allowed


class AccessGuard:
    """Pure ASGI so it never buffers the event stream."""

    def __init__(self, app, config, local_only: bool = False):
        self.app = app
        self.config = config
        self.local_only = local_only

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith("/api/"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope["headers"]}

        if scope["method"] not in _SAFE_METHODS and _is_cross_site(headers):
            return await _deny(send, 403, "Blocked a request from another website.")

        token = self.config.auth_token
        if token:
            if path not in OPEN_PATHS:
                cookie = _cookie(headers, SESSION_COOKIE)
                if token_matches(cookie, session_value(token)):
                    # Let the per-route token check treat this as authenticated.
                    scope["headers"] = [*scope["headers"],
                                        (b"x-digdown-token", token.encode())]
                elif not _header_token_ok(headers, token):
                    return await _deny(send, 401, "invalid or missing token")
        elif self.local_only and path not in OPEN_PATHS:
            if hostname_of(headers.get("host", "")) not in LOCAL_HOSTNAMES:
                return await _deny(
                    send, 403,
                    "Digdown only accepts connections from this computer until "
                    "you set auth_token. See docs/security.md.")
        await self.app(scope, receive, send)


def _header_token_ok(headers: dict[str, str], token: str) -> bool:
    bearer = headers.get("authorization", "")
    provided = (bearer[len("Bearer "):] if bearer.startswith("Bearer ")
                else headers.get("x-digdown-token"))
    return token_matches(provided, token)


async def _deny(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


def ensure_token(config, *, port_is_local: bool = False) -> str | None:
    """A process reachable from other machines must not run open. When it would,
    create a token, keep it beside the database so it survives restarts, and return
    it so startup can print it. `port_is_local` is for a container whose published
    port is bound to loopback, so listening on 0.0.0.0 inside it stays private."""
    if config.auth_token or is_loopback_bind(config.host) or port_is_local:
        return None
    path = Path(config.db_path).resolve().parent / "digdown.token"
    if path.exists():
        token = path.read_text().strip()
    else:
        token = secrets.token_urlsafe(24)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(token)
    config.auth_token = token
    return token
