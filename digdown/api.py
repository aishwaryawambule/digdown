from __future__ import annotations

import asyncio
import contextlib
import heapq
import itertools
import json
from pathlib import Path
from urllib.parse import urlsplit

from typing import Callable, Optional

from fastapi import BackgroundTasks, Body, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse

from . import __version__, access, checks
from .alerts import parse_grafana_alerts
from .config import (FILE_EDITABLE_KEYS, NO_STACK, Config, ConfigError, RUNTIME_SAFE_KEYS, config_path,
                     config_view, save_config)
from .events import EventBus
from .executor_models import without_env_file
from .models import Incident, IncidentState, SignalType
from .notifier import WebhookChannel
from .plan_models import Approval
from .store import Store

_POLL_TIMEOUT = 1.0  # seconds between disconnect checks

# States a person can manually dismiss (close to closed_no_fix). These are the
# stuck / pre-decision non-terminal states; mid-decision (proposed) and
# mid-execution (approved/executing/verifying) states have their own controls.
_DISMISSIBLE_STATES = {
    IncidentState.DETECTED,
    IncidentState.TRIAGED,
    IncidentState.INVESTIGATING,
    IncidentState.DIAGNOSIS_FAILED,
    IncidentState.DIAGNOSED,
}


# Lower goes first. A container that's actually broken leads, then one that's merely
# stopped and may be off on purpose, then warnings about something that hasn't failed.
_DOWN = {SignalType.CRASH, SignalType.OOM, SignalType.RESTART_LOOP, SignalType.UNHEALTHY}
_STOPPED = {SignalType.STOPPED, SignalType.PAUSED, SignalType.CREATED}
_APP_FAILING = {SignalType.HTTP_ERROR, SignalType.ALERT_FIRING}


def diagnosis_priority(incident: Incident) -> int:
    kind = incident.signal.type
    if kind in _DOWN:
        return 0
    if kind in _APP_FAILING:
        return 1
    return 2 if kind in _STOPPED else 3


class PriorityGate:
    """Lets one job run at a time, choosing the most urgent of those waiting rather
    than the first to arrive. The job already running isn't interrupted."""

    def __init__(self) -> None:
        self._waiting: list[tuple[int, int, asyncio.Future]] = []
        self._order = itertools.count()
        self._busy = False

    def _start_next(self) -> None:
        while self._waiting:
            _, _, turn = heapq.heappop(self._waiting)
            if not turn.done():  # one that was cancelled while waiting is skipped
                turn.set_result(None)
                return
        self._busy = False

    @contextlib.asynccontextmanager
    async def slot(self, priority: int):
        if self._busy:
            turn = asyncio.get_running_loop().create_future()
            heapq.heappush(self._waiting, (priority, next(self._order), turn))
            try:
                await turn
            except asyncio.CancelledError:
                if turn.done() and not turn.cancelled():
                    self._start_next()  # it was given the slot just as it was cancelled
                raise
        else:
            self._busy = True
        try:
            yield
        finally:
            self._start_next()


async def run_diagnosis_safely(pipeline, incident) -> None:
    """Diagnose one incident on a worker thread (LLM calls block), never letting
    a failure crash the poll loop. A failure moves the incident to
    DIAGNOSIS_FAILED (with a human-readable reason) and is recorded to the audit
    log, so it stops masquerading as 'Diagnosing…' and can be retried."""
    try:
        await asyncio.to_thread(pipeline.run, incident)
    except Exception as e:  # noqa: BLE001
        with contextlib.suppress(Exception):
            pipeline.mark_diagnosis_failed(incident, e)


async def run_execution_safely(executor, incident) -> None:
    """Execute an approved plan on a worker thread (Docker writes block); never
    crash the loop. Failures are recorded; the Executor itself rolls back."""
    try:
        await asyncio.to_thread(executor.execute, incident)
    except Exception as e:  # noqa: BLE001
        with contextlib.suppress(Exception):
            executor.store.append_audit(
                incident.id, actor="executor", event="execute_failed",
                payload={"error": str(e)})


# The only settings the setup screen may write. Network and access settings stay
# out so a browser form can't open Digdown up to other machines.
_SETUP_KEYS = {"project_name", "agent_enabled", "llm_provider", "llm_base_url",
               "llm_api_key", "llm_auth_token", "llm_model", "mode", "auto_heal_all"}

_CSP = ("default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "font-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'")

_NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>Digdown</title>
<body style="font:16px system-ui;max-width:36rem;margin:4rem auto;padding:0 1rem">
<h1>Digdown is running</h1>
<p>This copy doesn't include the dashboard. To add it, run
<code>scripts/build-dashboard.sh</code> and restart, or use the Docker image.</p>
<p>The API is at <a href="/docs">/docs</a>.</p>"""


def _mount_dashboard(app: FastAPI, static_dir: Path) -> None:
    root = Path(static_dir).resolve()
    index = root / "index.html"

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def dashboard(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        if not index.exists():
            return HTMLResponse(_NOT_BUILT)
        wanted = (root / path).resolve()
        if path and wanted.is_file() and wanted.is_relative_to(root):
            cache = ("public, max-age=31536000, immutable"
                     if path.startswith("assets/") else "no-cache")
            headers = {"Cache-Control": cache}
            if wanted.suffix == ".html":
                headers["Content-Security-Policy"] = _CSP
            return FileResponse(wanted, headers=headers)
        # Anything else is a page inside the dashboard.
        return FileResponse(index, headers={"Cache-Control": "no-cache",
                                            "Content-Security-Policy": _CSP})


def _website_checks(wanted: list, existing: list) -> list[dict]:
    """The checks as they'll be saved. Each needs an address and a container, and a
    name of its own, which is how Digdown tells two checks on one container apart."""
    kept = {(h.url, h.target): h for h in existing}
    saved: list[dict] = []
    names: set[str] = set()
    for item in wanted:
        item = item if isinstance(item, dict) else {}
        url = str(item.get("url") or "").strip()
        target = str(item.get("target") or "").strip()
        if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc:
            raise HTTPException(status_code=422, detail=(
                f"\"{url}\" isn't a web address. It has to start with http:// or https://."))
        if not target:
            raise HTTPException(status_code=422,
                                detail="Choose the container this address belongs to.")
        old = kept.get((url, target))
        check = old.model_dump() if old else {"url": url, "target": target}
        name = check.get("name") or target
        n = 2
        while name in names:
            name, n = f"{check.get('name') or target}-{n}", n + 1
        check["name"] = name
        names.add(name)
        saved.append(check)
    return saved


# Settings that can let a fix run which couldn't before.
_WIDENS_WHAT_RUNS = {"mode", "auto_heal_stacks", "auto_heal_disk_cleanup", "auto_heal_all"}


def create_app(store: Store, bus: EventBus, config: Config,
               watcher=None, pipeline=None, executor=None,
               base_config: Optional[Config] = None,
               *,
               local_only: bool = False,
               status_info: Optional[dict] = None,
               config_file: Optional[str] = None,
               restart: Optional[Callable[[], None]] = None,
               docker_factory: Callable = checks.docker_client,
               docker_ready: Optional[Callable[[], bool]] = None,
               static_dir: Optional[Path] = None,
               demo: bool = False) -> FastAPI:
    base_config = base_config or Config()
    runtime: dict = {"loop": None, "exec_gate": None, "poll_error": None}
    status_info = status_info or {}

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        loop = asyncio.get_running_loop()
        bus.bind_loop(loop)
        runtime["loop"] = loop
        runtime["exec_gate"] = asyncio.Semaphore(1)
        if pipeline is not None and executor is not None:
            pipeline.start_fix = _start_fix  # auto heal can now run fixes
        # One diagnosis at a time: bounds LLM cost and serialises agent work.
        # Hoisted so the /api/alerts endpoint can reuse diagnose regardless of watcher.
        diag_gate = PriorityGate()
        run_agents = pipeline is not None and config.agent_enabled

        async def diagnose(incident):
            # The gate limits AI use, so what Docker can answer alone doesn't wait for it.
            check = getattr(pipeline, "needs_no_ai", None)
            with contextlib.suppress(Exception):
                if check is not None and await asyncio.to_thread(check, incident):
                    await run_diagnosis_safely(pipeline, incident)
                    return
            async with diag_gate.slot(diagnosis_priority(incident)):
                await run_diagnosis_safely(pipeline, incident)

        runtime["diagnose"] = diagnose if run_agents else None

        if watcher is not None:
            async def loop_body():
                while True:
                    try:
                        # Run the scan off the event loop: against a remote
                        # (SSH-tunnelled) Docker a poll can take seconds, and we
                        # must not freeze API requests or the SSE stream meanwhile.
                        opened = (await asyncio.to_thread(watcher.poll_once)) or []
                        runtime["poll_error"] = None
                    except Exception as e:  # noqa: BLE001 - never let the loop die
                        opened = []
                        # Keep it: the dashboard must not read "all good" while
                        # the watcher is actually failing.
                        runtime["poll_error"] = str(e)
                    if run_agents:
                        for inc in opened:
                            # Suppress noise: a cascade dependent is not diagnosed
                            # on its own — its parent root-incident is.
                            if inc.parent_incident_id is None:
                                asyncio.create_task(diagnose(inc))
                    await asyncio.sleep(config.poll_interval_seconds)

            if run_agents:
                # Catch up on incidents detected while the agent was off (e.g.
                # before it was enabled): the poll loop only diagnoses freshly-
                # opened incidents, so without this sweep they sit at DETECTED
                # forever. Same filter as the loop — skip cascade dependents.
                for inc in store.incidents_in_state(IncidentState.DETECTED):
                    if inc.parent_incident_id is None:
                        asyncio.create_task(diagnose(inc))

            task = asyncio.create_task(loop_body())
            app.state._poll_task = task
        elif docker_ready is not None and restart is not None:
            # Started before Docker was up. Pick it up once it appears, so nobody
            # has to know to restart Digdown.
            async def wait_for_docker():
                while not await asyncio.to_thread(docker_ready):
                    await asyncio.sleep(10)
                restart()

            task = asyncio.create_task(wait_for_docker())
        try:
            yield
        finally:
            if task is not None:
                task.cancel()

    app = FastAPI(title="Digdown", version=__version__, lifespan=lifespan)
    app.add_middleware(access.AccessGuard, config=config, local_only=local_only)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "mode": config.mode}

    @app.get("/api/session")
    def get_session(request: Request):
        required = bool(config.auth_token)
        cookie = request.cookies.get(access.SESSION_COOKIE)
        authed = not required or access.token_matches(
            cookie, access.session_value(config.auth_token))
        return {"auth_required": required, "authenticated": authed}

    @app.post("/api/session")
    def login(request: Request, body: dict = Body(default={})):
        if not config.auth_token:
            return {"authenticated": True}
        if not access.token_matches(str((body or {}).get("token") or ""), config.auth_token):
            raise HTTPException(status_code=401, detail="That token isn't right.")
        secure = (request.url.scheme == "https"
                  or request.headers.get("x-forwarded-proto") == "https")
        response = JSONResponse({"authenticated": True})
        response.set_cookie(access.SESSION_COOKIE, access.session_value(config.auth_token),
                            max_age=access.SESSION_MAX_AGE, httponly=True,
                            samesite="strict", secure=secure)
        return response

    @app.delete("/api/session")
    def logout():
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(access.SESSION_COOKIE)
        return response

    def _status() -> dict:
        startup_error = status_info.get("docker_error")
        error = startup_error or runtime["poll_error"]
        docker_ok = not error and (watcher is not None or "docker_error" not in status_info)
        watching = watcher is not None and not error
        return {
            "version": __version__,
            "demo": demo,
            "first_run": bool(status_info.get("first_run")),
            "mode": config.mode,
            "watching": watching,
            "docker": {"ok": docker_ok, "error": error,
                       "fix": "" if docker_ok else checks.docker_fix()},
            "ai": {"enabled": config.agent_enabled and (pipeline is not None or demo),
                   "configured": config.agent_enabled,
                   "provider": config.llm_provider, "model": config.llm_model},
            "config_file": config_file or config_path(),
            "auth_required": bool(config.auth_token),
            "auto_heal": {"stacks": config.auto_heal_stacks, "all": config.auto_heal_all,
                          "max_attempts": config.auto_heal_max_attempts},
        }

    @app.get("/api/status")
    def status():
        return _status()

    def _no_setup_in_demo():
        if demo:
            raise HTTPException(
                status_code=409,
                detail="The demo has no settings to save. Run `digdown start` "
                       "to set up Digdown for your own containers.")

    @app.get("/api/setup")
    def setup_options():
        # One client for both, so a hung Docker costs one timeout rather than two.
        try:
            client = docker_factory()
        except Exception as e:  # noqa: BLE001 - reported by the check below
            error = e

            def client_factory():
                raise error
            stacks = []
        else:
            client_factory = lambda: client  # noqa: E731
            try:
                stacks = checks.list_stacks(client)
            except Exception:  # noqa: BLE001 - reported by the check below
                stacks = []
        docker_check = checks.check_docker(client_factory)
        suggested = checks.default_ollama_url()
        return {
            "docker": docker_check.to_dict(),
            "stacks": stacks,
            "in_container": checks.in_container(),
            "ollama": {"base_url": suggested, "models": checks.ollama_models(suggested)},
            "current": {"project_name": config.project_name, "mode": config.mode,
                        "agent_enabled": config.agent_enabled,
                        "llm_provider": config.llm_provider,
                        "llm_base_url": config.llm_base_url,
                        "llm_model": config.llm_model,
                        "auto_heal_all": config.auto_heal_all,
                        "has_key": config.llm_api_key not in ("", "not-needed")
                        or bool(config.llm_auth_token)},
        }

    @app.post("/api/setup/test-ai")
    def setup_test_ai(body: dict = Body(default={}),
                      authorization: Optional[str] = Header(default=None),
                      x_digdown_token: Optional[str] = Header(default=None)):
        _check_auth(authorization, x_digdown_token)
        _no_setup_in_demo()
        b = body or {}
        provider = str(b.get("llm_provider") or "openai")
        base_url = str(b.get("llm_base_url") or "")
        api_key = str(b.get("llm_api_key") or "")
        auth_token = str(b.get("llm_auth_token") or "")
        # A blank key means "keep the saved one", but a saved key only ever goes to
        # the service it was saved for.
        same_service = (provider == config.llm_provider
                        and (provider != "openai" or base_url == config.llm_base_url))
        if not api_key and not auth_token and same_service:
            api_key, auth_token = config.llm_api_key, config.llm_auth_token
        return checks.test_ai(provider, base_url=base_url, api_key=api_key,
                              auth_token=auth_token,
                              model=str(b.get("llm_model") or "")).to_dict()

    @app.post("/api/setup")
    def save_setup(background: BackgroundTasks, body: dict = Body(default={}),
                   authorization: Optional[str] = Header(default=None),
                   x_digdown_token: Optional[str] = Header(default=None)):
        _check_auth(authorization, x_digdown_token)
        _no_setup_in_demo()
        updates = {k: v for k, v in (body or {}).items() if k in _SETUP_KEYS}
        try:
            save_config(config_file or config_path(), updates)
        except ConfigError as e:
            raise HTTPException(status_code=422, detail=str(e))
        # A mode set earlier in Settings is stored separately and wins at startup,
        # so clear it or this choice would be silently ignored.
        for key in ("mode", "auto_heal_all"):
            if key in updates:
                store.delete_setting(key)
        # Agents and the Docker connection are built at startup, so new settings
        # take effect by restarting, after this response has gone out.
        if restart is not None:
            background.add_task(restart)
        return {"saved": True, "restarting": restart is not None}

    def _restart_soon(background: BackgroundTasks) -> bool:
        # After the response has gone out, as Docker connections are made at startup.
        if restart is not None:
            background.add_task(restart)
        return restart is not None

    @app.get("/api/auto-heal/activity")
    def auto_heal_activity():
        return store.auto_heal_activity()

    def _stacks_on_this_machine() -> list[dict]:
        """Every app Docker knows about, watched or not. The demo has only its own."""
        counts: dict[str, int] = {}
        if not demo:
            try:
                return [{"name": s["name"] or NO_STACK, "containers": s["containers"]}
                        for s in checks.list_stacks(docker_factory())]
            except Exception:  # noqa: BLE001 - fall back to what's being watched
                pass
        for state in (watcher.last_states if watcher is not None else None) or []:
            name = state.project or NO_STACK
            counts[name] = counts.get(name, 0) + 1
        return [{"name": n, "containers": c} for n, c in sorted(counts.items())]

    @app.get("/api/apps")
    def list_apps():
        watch_all = not config.project_name
        stacks = _stacks_on_this_machine()
        return {
            "all": watch_all,
            "stacks": [{**s, "watched": watch_all or s["name"] in config.project_name,
                        "auto_heal": config.auto_heals(s["name"])} for s in stacks],
        }

    @app.put("/api/apps")
    def choose_apps(background: BackgroundTasks, body: dict = Body(default={}),
                    authorization: Optional[str] = Header(default=None),
                    x_digdown_token: Optional[str] = Header(default=None)):
        _check_auth(authorization, x_digdown_token)
        _no_setup_in_demo()
        b = body or {}
        chosen = [] if b.get("all") else [str(n) for n in b.get("stacks") or [] if n]
        if not b.get("all") and not chosen:
            raise HTTPException(status_code=422, detail="Choose at least one app to watch.")
        try:
            save_config(config_file or config_path(), {"project_name": chosen})
        except ConfigError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return {"saved": True, "restarting": _restart_soon(background)}

    @app.put("/api/config/file")
    def save_file_setting(background: BackgroundTasks, body: dict = Body(default={}),
                          authorization: Optional[str] = Header(default=None),
                          x_digdown_token: Optional[str] = Header(default=None)):
        _check_auth(authorization, x_digdown_token)
        _no_setup_in_demo()
        key = (body or {}).get("key")
        if key not in FILE_EDITABLE_KEYS:
            raise HTTPException(status_code=409,
                                detail=f"'{key}' can only be changed in the settings file.")
        try:
            save_config(config_file or config_path(), {key: (body or {}).get("value")})
        except ConfigError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return {"saved": True, "restarting": _restart_soon(background)}

    @app.get("/api/checks")
    def list_checks():
        return [{"name": h.name, "url": h.url, "target": h.target} for h in config.probes.http]

    @app.put("/api/checks")
    def save_checks(background: BackgroundTasks, body: list = Body(default=[]),
                    authorization: Optional[str] = Header(default=None),
                    x_digdown_token: Optional[str] = Header(default=None)):
        _check_auth(authorization, x_digdown_token)
        _no_setup_in_demo()
        checks_wanted = _website_checks(body or [], config.probes.http)
        probes = {"http": checks_wanted,
                  "logs": [c.model_dump() for c in config.probes.logs]}
        try:
            save_config(config_file or config_path(), {"probes": probes})
        except ConfigError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return {"saved": True, "restarting": _restart_soon(background)}

    @app.get("/api/notifications")
    def get_notifications():
        return {"set": bool(config.notify_webhook_url)}

    def _valid_alert_url(url: str) -> str:
        if url and urlsplit(url).scheme not in ("http", "https"):
            raise HTTPException(status_code=422,
                                detail="The alert address has to start with http:// or https://.")
        return url

    @app.put("/api/notifications")
    def save_notifications(body: dict = Body(default={}),
                           authorization: Optional[str] = Header(default=None),
                           x_digdown_token: Optional[str] = Header(default=None)):
        _check_auth(authorization, x_digdown_token)
        _no_setup_in_demo()
        url = _valid_alert_url(str((body or {}).get("url") or "").strip())
        try:
            save_config(config_file or config_path(), {"notify_webhook_url": url})
        except ConfigError as e:
            raise HTTPException(status_code=422, detail=str(e))
        config.notify_webhook_url = url
        channels = [WebhookChannel(url)] if url else []
        for owner in (pipeline, executor):
            if owner is not None:
                owner.notifier.channels = channels
        return {"set": bool(url)}

    @app.post("/api/notifications/test")
    def test_notifications(body: dict = Body(default={}),
                           authorization: Optional[str] = Header(default=None),
                           x_digdown_token: Optional[str] = Header(default=None)):
        _check_auth(authorization, x_digdown_token)
        _no_setup_in_demo()
        url = _valid_alert_url(str((body or {}).get("url") or "").strip()) or config.notify_webhook_url
        if not url:
            raise HTTPException(status_code=422, detail="Enter an alert address first.")
        try:
            WebhookChannel(url)({"kind": "test", "target": "",
                                 "detail": "Alerts from Digdown will reach you here."})
        except Exception as e:  # noqa: BLE001 - reported to the person trying it
            return {"ok": False, "detail": f"That didn't work: {e}"}
        return {"ok": True, "detail": "Sent. Check that it arrived."}

    @app.get("/api/config")
    def get_config():
        return config_view(config, store.settings_meta())

    _VALID_MODES = {"observe", "propose", "execute"}

    @app.patch("/api/config")
    def patch_config(body: dict = Body(default={}),
                     authorization: Optional[str] = Header(default=None),
                     x_digdown_token: Optional[str] = Header(default=None)):
        _check_auth(authorization, x_digdown_token)
        key = (body or {}).get("key")
        if key not in RUNTIME_SAFE_KEYS:
            raise HTTPException(
                status_code=409,
                detail=f"'{key}' is not runtime-editable — set it in digdown.yml and restart")
        value = (body or {}).get("value")
        # Config types `mode` as a bare str; constrain it here (defense-in-depth).
        if key == "mode" and value not in _VALID_MODES:
            raise HTTPException(status_code=422,
                                detail=f"mode must be one of {sorted(_VALID_MODES)}")
        try:
            validated = Config(**{**config.model_dump(), key: value})
        except Exception as e:  # noqa: BLE001 - surface as 422, leave state untouched
            raise HTTPException(status_code=422, detail=f"invalid value for {key}: {e}")
        coerced = getattr(validated, key)
        if key in ("auto_heal_stacks", "auto_heal_all") and coerced and config.mode != "execute":
            raise HTTPException(status_code=409,
                                detail='Auto heal needs "Fix after I approve" turned on first.')
        identity = (body or {}).get("identity", "you")
        store.set_setting(key, coerced, identity=identity)
        setattr(config, key, coerced)
        bus.publish({"type": "config_changed", "key": key, "value": coerced,
                     "source": "override"})
        _reconsider_after(key)
        return config_view(config, store.settings_meta())

    @app.delete("/api/config/{key}")
    def reset_config(key: str,
                     authorization: Optional[str] = Header(default=None),
                     x_digdown_token: Optional[str] = Header(default=None)):
        _check_auth(authorization, x_digdown_token)
        if key not in RUNTIME_SAFE_KEYS:
            raise HTTPException(
                status_code=409,
                detail=f"'{key}' is not runtime-editable")
        store.delete_setting(key)
        base_value = getattr(base_config, key)
        setattr(config, key, base_value)
        bus.publish({"type": "config_changed", "key": key, "value": base_value,
                     "source": "config"})
        _reconsider_after(key)
        return config_view(config, store.settings_meta())

    @app.get("/api/incidents")
    def list_incidents():
        verified = store.verified_incident_ids()
        healed = store.auto_healed_incident_ids()
        rows = []
        for incident in store.list_incidents():
            row = incident.model_dump(mode="json")
            # Lets the list say "fixed" rather than "cleared" without a fetch per row.
            if incident.id in verified:
                row["execution"] = {"verified": True}
            if incident.id in healed:
                row["auto_healed"] = True
                plan = store.latest_plan_for(incident.id)
                row["heal_summary"] = plan.summary if plan else ""
            rows.append(row)
        return rows

    @app.get("/api/services")
    def services():
        """The containers Digdown is currently watching — so you can confirm
        your stack is loaded even when nothing is failing."""
        source = getattr(watcher, "source", None)
        if source is None:
            return {"available": False, "services": []}
        # The watcher has just collected this. Asking Docker again per request means
        # fresh stats for every container, which took 15 seconds under load, and more
        # with each dashboard open.
        states = None if runtime["poll_error"] else getattr(watcher, "last_states", None)
        try:
            states = states if states is not None else source.list_states()
        except Exception:  # noqa: BLE001 - Docker may be unreachable
            return {"available": False, "services": []}
        http_status = {}
        for p in getattr(watcher, "probes", []) or []:
            if getattr(p, "name", None) == "http" and hasattr(p, "http_status_by_target"):
                http_status = p.http_status_by_target()
        ring = getattr(watcher, "stats_ring", None)
        out = []
        for s in states:
            d = s.model_dump(mode="json")
            d["http"] = http_status.get(s.name) or http_status.get(getattr(s, "service", None))
            series = ring.series(s.name) if ring is not None else {"cpu": [], "mem": []}
            d["cpu_series"] = series["cpu"]
            d["mem_series"] = series["mem"]
            out.append(d)
        host_ring = getattr(watcher, "host_ring", None)
        host_vitals = getattr(watcher, "host_vitals", None)
        host = (host_vitals() if host_vitals
                else {"disk_percent": None, "load1": None, "mem_percent": None})
        hs = host_ring.series() if host_ring is not None else {"disk": [], "mem": []}
        host["disk_series"], host["mem_series"] = hs["disk"], hs["mem"]
        return {"available": True, "services": out, "host": host}

    @app.get("/api/incidents/{incident_id}")
    def get_incident(incident_id: int):
        inc = store.get_incident(incident_id)
        if inc is None:
            raise HTTPException(status_code=404, detail="incident not found")
        payload = inc.model_dump(mode="json")
        diagnosis = store.latest_diagnosis_for(incident_id)
        payload["diagnosis"] = diagnosis.model_dump(mode="json") if diagnosis else None
        plan = store.latest_plan_for(incident_id)
        payload["plan"] = plan.model_dump(mode="json") if plan else None
        payload["approvals"] = [a.model_dump(mode="json")
                                for a in store.approvals_for(incident_id)]
        execution = store.latest_execution_for(incident_id)
        payload["execution"] = execution.model_dump(mode="json") if execution else None
        if execution:  # a database from an older version may still hold this
            payload["execution"]["snapshot"] = without_env_file(payload["execution"]["snapshot"])
        payload["audit"] = store.audit_for(incident_id)
        return payload

    def _reconsider_after(key: str) -> None:
        """Allowing more shouldn't only apply to the next failure. What's already broken
        gets another look: anything only explained until now goes round again, and a
        fix already waiting runs if auto heal may now do it."""
        loop = runtime["loop"]
        if key not in _WIDENS_WHAT_RUNS or pipeline is None or loop is None:
            return
        if key == "mode" and config.mode in ("propose", "execute") and runtime.get("diagnose"):
            for inc in store.incidents_in_state(IncidentState.DIAGNOSED):
                store.update_state(inc.id, IncidentState.DETECTED)
                asyncio.run_coroutine_threadsafe(runtime["diagnose"](inc), loop)
        if config.mode == "execute":
            waiting = store.incidents_in_state(IncidentState.PROPOSED)
            for inc in waiting:
                # Reads Docker, so it stays off the request and the event loop.
                loop.call_soon_threadsafe(asyncio.create_task,
                                          _retry_auto_heal(inc))

    async def _retry_auto_heal(incident) -> None:
        with contextlib.suppress(Exception):  # it stays waiting for a person
            await asyncio.to_thread(pipeline.retry_auto_heal, incident)

    def _start_fix(incident) -> None:
        """Run an approved fix, one at a time. Called from worker threads as well as
        the event loop, so a person's click and auto heal take the same path."""
        if executor is None or runtime["loop"] is None:
            return

        async def job():
            async with runtime["exec_gate"]:
                await run_execution_safely(executor, incident)
        asyncio.run_coroutine_threadsafe(job(), runtime["loop"])

    def _decide(incident_id: int, decision: str, body: dict):
        inc = store.get_incident(incident_id)
        if inc is None:
            raise HTTPException(status_code=404, detail="incident not found")
        if inc.state != IncidentState.PROPOSED:
            raise HTTPException(
                status_code=409,
                detail=f"incident is {inc.state.value}, not awaiting approval")
        identity = (body or {}).get("identity", "you")
        note = (body or {}).get("note", "")
        store.add_approval(Approval(incident_id=incident_id, decision=decision,
                                    identity=identity, note=note))
        new_state = (IncidentState.APPROVED if decision == "approve"
                     else IncidentState.REJECTED)
        store.update_state(incident_id, new_state)
        inc.state = new_state
        store.append_audit(incident_id, actor=identity,
                           event="approved" if decision == "approve" else "rejected",
                           payload={"note": note})
        bus.publish({"type": "incident_updated", "incident": inc.model_dump(mode="json")})

        # In execute mode, an approval kicks off the Executor off the loop thread.
        if decision == "approve" and config.mode == "execute":
            _start_fix(inc)

        return inc.model_dump(mode="json")

    def _check_auth(authorization: Optional[str], token_header: Optional[str]):
        if not config.auth_token:
            return  # no token: AccessGuard limits requests to this computer
        provided = token_header
        if authorization and authorization.startswith("Bearer "):
            provided = authorization[len("Bearer "):]
        if not access.token_matches(provided, config.auth_token):
            raise HTTPException(status_code=401, detail="invalid or missing token")

    @app.post("/api/alerts")
    def ingest_alerts(body: dict = Body(default={}),
                      authorization: Optional[str] = Header(default=None),
                      x_digdown_token: Optional[str] = Header(default=None)):
        _check_auth(authorization, x_digdown_token)
        firing, resolved = parse_grafana_alerts(body, config.alert_target_label)
        ingested = 0
        for sig in firing:
            key = "grafana:" + (sig.detail.get("fingerprint") or sig.dedup_key)
            if store.open_incident_by_dedup(key) is not None:
                continue
            inc = Incident(source="grafana", target=sig.target, signal=sig,
                           dedup_key=key, state=IncidentState.DETECTED)
            inc = store.add_incident(inc)
            store.append_audit(inc.id, actor="grafana", event="detected",
                               payload=sig.model_dump(mode="json"))
            bus.publish({"type": "incident_created",
                         "incident": inc.model_dump(mode="json")})
            ingested += 1
            if runtime.get("loop") is not None and runtime.get("diagnose") is not None:
                asyncio.run_coroutine_threadsafe(runtime["diagnose"](inc),
                                                 runtime["loop"])
        n_resolved = 0
        for fp in resolved:
            inc = store.open_incident_by_dedup("grafana:" + fp)
            if inc is None:
                continue
            store.update_state(inc.id, IncidentState.RESOLVED)
            store.append_audit(inc.id, actor="grafana", event="resolved", payload={})
            inc.state = IncidentState.RESOLVED
            bus.publish({"type": "incident_updated",
                         "incident": inc.model_dump(mode="json")})
            n_resolved += 1
        return {"ingested": ingested, "resolved": n_resolved}

    @app.post("/api/incidents/{incident_id}/approve")
    def approve(incident_id: int, body: dict = Body(default={}),
                authorization: Optional[str] = Header(default=None),
                x_digdown_token: Optional[str] = Header(default=None)):
        _check_auth(authorization, x_digdown_token)
        return _decide(incident_id, "approve", body)

    @app.post("/api/incidents/{incident_id}/reject")
    def reject(incident_id: int, body: dict = Body(default={}),
               authorization: Optional[str] = Header(default=None),
               x_digdown_token: Optional[str] = Header(default=None)):
        _check_auth(authorization, x_digdown_token)
        return _decide(incident_id, "reject", body)

    @app.post("/api/incidents/{incident_id}/retry")
    def retry_diagnosis(incident_id: int,
                        authorization: Optional[str] = Header(default=None),
                        x_digdown_token: Optional[str] = Header(default=None)):
        _check_auth(authorization, x_digdown_token)
        inc = store.get_incident(incident_id)
        if inc is None:
            raise HTTPException(status_code=404, detail="incident not found")
        if inc.state != IncidentState.DIAGNOSIS_FAILED:
            raise HTTPException(
                status_code=409,
                detail=f"incident is {inc.state.value}, not a failed diagnosis")
        if runtime.get("loop") is None or runtime.get("diagnose") is None:
            raise HTTPException(status_code=409, detail="diagnosis is not enabled")
        store.set_failure_reason(incident_id, "")
        store.update_state(incident_id, IncidentState.DETECTED)
        inc.state = IncidentState.DETECTED
        inc.failure_reason = ""
        store.append_audit(incident_id, actor="you", event="retry_diagnosis",
                           payload={})
        bus.publish({"type": "incident_updated",
                     "incident": inc.model_dump(mode="json")})
        asyncio.run_coroutine_threadsafe(runtime["diagnose"](inc), runtime["loop"])
        return inc.model_dump(mode="json")

    @app.post("/api/incidents/{incident_id}/dismiss")
    def dismiss(incident_id: int,
                authorization: Optional[str] = Header(default=None),
                x_digdown_token: Optional[str] = Header(default=None)):
        _check_auth(authorization, x_digdown_token)
        inc = store.get_incident(incident_id)
        if inc is None:
            raise HTTPException(status_code=404, detail="incident not found")
        # An approval waits for a fix only in execute mode. Otherwise it would sit
        # in APPROVED forever with no way to close it.
        idle_approval = inc.state == IncidentState.APPROVED and config.mode != "execute"
        if inc.state not in _DISMISSIBLE_STATES and not idle_approval:
            raise HTTPException(
                status_code=409,
                detail=f"incident is {inc.state.value}, not a dismissible state")
        store.update_state(incident_id, IncidentState.CLOSED_NO_FIX)
        inc.state = IncidentState.CLOSED_NO_FIX
        store.append_audit(incident_id, actor="you", event="dismissed",
                           payload={})
        bus.publish({"type": "incident_updated",
                     "incident": inc.model_dump(mode="json")})
        return inc.model_dump(mode="json")

    @app.get("/api/incidents/{incident_id}/logs")
    def incident_logs(incident_id: int, lines: int = 200,
                      authorization: Optional[str] = Header(default=None),
                      x_digdown_token: Optional[str] = Header(default=None)):
        _check_auth(authorization, x_digdown_token)
        inc = store.get_incident(incident_id)
        if inc is None:
            raise HTTPException(status_code=404, detail="incident not found")
        if pipeline is None or getattr(pipeline, "tools", None) is None:
            raise HTTPException(status_code=503, detail="logs unavailable (agent disabled)")
        eff = min(max(1, lines), 1000)
        logs = pipeline.tools.redacted_logs(inc.target, eff)
        return {"target": inc.target, "lines": eff, "logs": logs}

    @app.get("/api/mutes")
    def list_mutes():
        """Active runtime mutes plus the config `exclude_projects` — so the
        dashboard can show everything that's been silenced, by either mechanism."""
        return {"mutes": [m.model_dump(mode="json") for m in store.list_mutes()],
                "excluded_projects": config.exclude_projects}

    @app.post("/api/mutes")
    def add_mute(body: dict = Body(default={}),
                 authorization: Optional[str] = Header(default=None),
                 x_digdown_token: Optional[str] = Header(default=None)):
        _check_auth(authorization, x_digdown_token)
        pattern = (body or {}).get("pattern", "").strip()
        if not pattern:
            raise HTTPException(status_code=400, detail="pattern is required")
        identity = (body or {}).get("identity", "you")
        note = (body or {}).get("note", "")
        mute = store.add_mute(pattern, identity=identity, note=note)
        # Silence any incidents already open for matching targets, and broadcast
        # each so the dashboard moves them out of the active queue live.
        affected = store.mute_open_incidents(pattern)
        for inc in affected:
            store.append_audit(inc.id, actor=identity, event="muted",
                               payload={"pattern": pattern})
            bus.publish({"type": "incident_updated",
                         "incident": inc.model_dump(mode="json")})
        bus.publish({"type": "mute_added", "mute": mute.model_dump(mode="json")})
        return {"mute": mute.model_dump(mode="json"),
                "muted_incident_ids": [inc.id for inc in affected]}

    @app.delete("/api/mutes/{pattern}")
    def delete_mute(pattern: str,
                    authorization: Optional[str] = Header(default=None),
                    x_digdown_token: Optional[str] = Header(default=None)):
        _check_auth(authorization, x_digdown_token)
        removed = store.remove_mute(pattern)
        if removed:
            bus.publish({"type": "mute_removed", "pattern": pattern})
        return {"removed": removed, "pattern": pattern}

    @app.delete("/api/history")
    def clear_history(authorization: Optional[str] = Header(default=None),
                      x_digdown_token: Optional[str] = Header(default=None)):
        _check_auth(authorization, x_digdown_token)
        result = store.clear_terminal_incidents()
        if result["count"]:
            bus.publish({"type": "history_cleared",
                         "incident_ids": result["deleted_ids"],
                         "count": result["count"]})
        return result

    @app.get("/api/stream")
    async def stream(request: Request):
        queue = bus.subscribe()

        async def gen():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(
                            queue.get(), timeout=_POLL_TIMEOUT
                        )
                    except asyncio.TimeoutError:
                        continue
                    yield {"data": json.dumps(event)}
            finally:
                bus.unsubscribe(queue)

        return EventSourceResponse(gen())

    # Last, so it only answers paths the API doesn't.
    _mount_dashboard(app, static_dir if static_dir is not None else checks.STATIC_DIR)
    return app
