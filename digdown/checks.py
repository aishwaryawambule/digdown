"""Health checks behind `digdown doctor` and the setup screen. Each result says
what's wrong and what to do about it, so nobody has to read a stack trace."""
from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import sys
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from . import access
from .config import Config, ConfigError, config_path, load_config, unknown_keys
from .llm import make_llm

OK, WARN, FAIL = "ok", "warn", "fail"
STATIC_DIR = Path(__file__).parent / "static"
_AI_TEST_TIMEOUT = 240  # the first call may have to load a large model into memory

_PING_TOOL = {"type": "function", "function": {
    "name": "ping",
    "description": "Confirms the connection works.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}}


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""
    technical: str = ""  # the raw error, for anyone who wants it

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def in_container() -> bool:
    return bool(os.environ.get("DIGDOWN_IN_CONTAINER"))


def default_ollama_url() -> str:
    host = "host.docker.internal" if in_container() else "localhost"
    return f"http://{host}:11434/v1"


def docker_client(timeout: int = 5):
    import docker

    return docker.from_env(timeout=timeout)


def list_stacks(client) -> list[dict[str, Any]]:
    """Compose projects on this machine with their container counts, for the
    'what should I watch?' question during setup."""
    counts: dict[str, int] = {}
    for c in client.containers.list(all=True):
        name = (c.labels or {}).get("com.docker.compose.project") or ""
        counts[name] = counts.get(name, 0) + 1
    return [{"name": name, "containers": n} for name, n in sorted(counts.items())]


def docker_fix() -> str:
    if in_container():
        return ("Mount the Docker socket into this container: "
                "-v /var/run/docker.sock:/var/run/docker.sock")
    return "Start Docker."


def check_docker(client_factory: Callable[[], Any] = docker_client) -> Check:
    try:
        client = client_factory()
        n = len(client.containers.list(all=True))
    except Exception as e:  # noqa: BLE001 - any failure means the same thing here
        return Check("Docker", FAIL, "Docker isn't answering.", docker_fix(), _short(e))
    if n == 0:
        return Check("Docker", WARN, "Connected, but there are no containers yet.",
                     "Start a stack, or run `digdown demo` to see Digdown work.")
    return Check("Docker", OK, f"Connected. {n} container{'s' if n != 1 else ''} found.")


def test_ai(provider: str, *, base_url: str = "", api_key: str = "", model: str = "",
            auth_token: str = "", llm_factory=make_llm,
            timeout: float = _AI_TEST_TIMEOUT) -> Check:
    """Ask the model to call a tool, because diagnosis fails on a model that can
    chat but can't do that, and only a real call reveals it."""
    name = "AI model"
    if not model:
        return Check(name, FAIL, "No model name is set.", "Choose a model.")

    def ask():
        llm = llm_factory(provider, base_url=base_url, api_key=api_key or "not-needed",
                          model=model, auth_token=auth_token)
        return llm.chat(
            [{"role": "system", "content": "Call the ping tool now. Do not write text."},
             {"role": "user", "content": "Check the connection."}],
            tools=[_PING_TOOL])

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        resp = pool.submit(ask).result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        return Check(name, FAIL, f"{model} didn't answer within {int(timeout)} seconds.",
                     "Check the address, or try a smaller model.")
    except Exception as e:  # noqa: BLE001
        return _ai_failure(model, base_url, e)
    finally:
        pool.shutdown(wait=False)
    if not resp.tool_calls:
        return Check(name, FAIL, f"{model} answered but didn't call the test tool.",
                     "Digdown needs a model with tool calling. On Ollama, "
                     "`ollama show <model>` should list \"tools\". qwen3:14b is known to work.")
    return Check(name, OK, f"{model} is ready and can call tools.")


def _ai_failure(model: str, base_url: str, e: Exception) -> Check:
    status = getattr(e, "status_code", None)
    text = str(e).lower()
    if status in (401, 403) or "authentication" in text or "api_key" in text:
        return Check("AI model", FAIL, "The model service rejected the key.",
                     "Check the API key.")
    if status == 404 or "not found" in text:
        return Check("AI model", FAIL, f"The service doesn't have a model called {model}.",
                     f"Check the name. On Ollama, run `ollama pull {model}`.")
    if "connect" in text or "timed out" in text or "timeout" in text:
        where = base_url or "the model service"
        return Check("AI model", FAIL, f"Can't reach {where}.",
                     "Start the model service, or check the address.")
    return Check("AI model", FAIL, f"The test failed: {_short(e)}", "")


def ollama_models(base_url: str, fetch: Optional[Callable[[str], bytes]] = None) -> list[str]:
    """Models installed on an Ollama server, or [] if it isn't one or isn't up."""
    root = base_url.rstrip("/").removesuffix("/v1")
    url = f"{root}/api/tags"

    def default_fetch(u: str) -> bytes:
        with urllib.request.urlopen(u, timeout=3) as r:  # noqa: S310 - local setup
            return r.read()

    try:
        data = json.loads((fetch or default_fetch)(url))
        return sorted(m["name"] for m in data.get("models", []))
    except Exception:  # noqa: BLE001 - absent or not Ollama: just no suggestions
        return []


def check_config(path: Optional[str] = None) -> Check:
    p = path or config_path()
    if not Path(p).exists():
        return Check("Settings file", WARN, f"{p} doesn't exist yet.",
                     "Run `digdown start` and finish setup in the browser.")
    try:
        load_config(p)
    except ConfigError as e:
        return Check("Settings file", FAIL, str(e), f"Fix {p}, then run this again.")
    extras = unknown_keys(p)
    if extras:
        return Check("Settings file", WARN,
                     f"{p} has settings Digdown ignores: {', '.join(extras)}.",
                     "These may be typos. See docs/CONFIGURATION.md.")
    return Check("Settings file", OK, f"{p} loads correctly.")


def check_compose(config: Config) -> Check:
    if Path(config.compose_path).exists():
        if not shutil.which("docker"):
            return Check("Compose file", WARN, f"Using {config.compose_path}, but the docker "
                         "command isn't installed here.",
                         "Restarting and starting containers works. Fixes that recreate a "
                         "container or change its settings need the docker command.")
        return Check("Compose file", OK, f"Using {config.compose_path}.")
    return Check("Compose file", WARN, f"{config.compose_path} wasn't found.",
                 "Watching still works. Linking related failures and fixes that "
                 "recreate a container need this file, so set compose_path.")


def check_network(config: Config) -> Check:
    if access.is_loopback_bind(config.host) or os.environ.get("DIGDOWN_PORT_IS_LOCAL"):
        return Check("Network", OK, "Only this computer can reach Digdown.")
    if config.auth_token:
        if len(config.auth_token) < 16:
            return Check("Network", WARN, "Open to other machines with a short token.",
                         "Use a token of at least 16 characters.")
        return Check("Network", OK, "Open to other machines, protected by a token.")
    return Check("Network", WARN, f"Listening on {config.host} with no token.",
                 "Digdown will create a token at startup and print it.")


def check_dashboard(static_dir: Path = STATIC_DIR) -> Check:
    if (static_dir / "index.html").exists():
        return Check("Dashboard", OK, "Included.")
    return Check("Dashboard", WARN, "This copy doesn't include the dashboard.",
                 "Run scripts/build-dashboard.sh, or use the Docker image.")


def check_python() -> Check:
    v = sys.version_info
    if v < (3, 11):
        return Check("Python", FAIL, f"Python {v.major}.{v.minor} is too old.",
                     "Install Python 3.11 or newer.")
    return Check("Python", OK, f"Python {v.major}.{v.minor}.")


def run_all(path: Optional[str] = None, *,
            client_factory: Callable[[], Any] = docker_client) -> list[Check]:
    config_check = check_config(path)
    results = [check_python(), config_check]
    try:
        config = load_config(path)
    except ConfigError:
        return results + [check_docker(client_factory), check_dashboard()]
    results += [check_docker(client_factory), check_compose(config),
                check_network(config), check_dashboard()]
    if Path(path or config_path()).exists() and config.agent_enabled:
        results.append(test_ai(config.llm_provider, base_url=config.llm_base_url,
                               api_key=config.llm_api_key, model=config.llm_model,
                               auth_token=config.llm_auth_token))
    return results


def _short(e: Exception, limit: int = 160) -> str:
    text = " ".join(str(e).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"
