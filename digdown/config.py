from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator


VALID_MODES = ("observe", "propose", "execute")
# Stands for containers started without Compose, wherever a list of stacks is kept.
# Compose project names can't contain brackets, so it can't clash with a real stack.
NO_STACK = "(no stack)"
VALID_PROVIDERS = ("openai", "claude", "anthropic")

# Deployment settings that can also come from the environment, so a container
# can be configured without a file. These win over digdown.yml.
_ENV_OVERRIDES = {
    "DIGDOWN_HOST": "host",
    "DIGDOWN_PORT": "port",
    "DIGDOWN_DB_PATH": "db_path",
    "DIGDOWN_AUTH_TOKEN": "auth_token",
}


class ConfigError(Exception):
    """digdown.yml can't be used. The message says what to fix, in plain words."""


class HttpCheckConfig(BaseModel):
    name: str
    url: str
    target: str
    expect_status: str = "2xx-3xx"
    timeout_ms: int = 5000
    latency_threshold_ms: int = 2000


class LogCheckConfig(BaseModel):
    target: str
    name: str = "errors"
    patterns: list[str] = []
    threshold: int = 5
    window_seconds: int = 60
    max_lines: int = 1000


class ProbesConfig(BaseModel):
    http: list[HttpCheckConfig] = []
    logs: list[LogCheckConfig] = []


class Config(BaseModel):
    mode: str = "observe"                 # observe | propose | execute
    db_path: str = "digdown.db"
    compose_path: str = "docker-compose.yml"
    project_name: list[str] = []          # "" / [] = all; a name or list of names scopes to those compose projects (see validator)
    exclude_projects: list[str] = []      # compose project(s) to never watch (e.g. a stack intentionally taken down); wins over project_name
    poll_interval_seconds: int = 15
    collect_resource_stats: bool = True   # sample container.stats() for running containers
    host_introspection: bool = False   # opt-in: agent's read-only system_inspect host tool
    debounce_polls: int = 1               # consecutive polls a signal must persist
    restart_loop_threshold: int = 3       # cumulative restart increments → restart_loop
    correlation_window_seconds: int = 120  # link dependents to a parent within this window
    disk_percent_threshold: float = 90.0
    mem_percent_threshold: float = 90.0   # % of a container's configured mem limit → memory_pressure
    disk_path: str = "/"
    host: str = "127.0.0.1"               # this computer only; see docs/SECURITY.md before changing
    port: int = 8088
    auth_token: str = ""                  # if set, required for the dashboard and API (else this computer only)
    notify_webhook_url: str = ""          # if set, push needs-approval/resolved/etc. here

    # --- Agent core ---
    agent_enabled: bool = True            # run diagnosis on new incidents
    llm_provider: str = "openai"          # "openai" (OpenAI-compatible, e.g. Ollama) | "claude"
    llm_base_url: str = "http://localhost:11434/v1"  # OpenAI-compatible endpoint (openai provider)
    llm_api_key: str = "not-needed"       # many local backends ignore this; Anthropic key if you have one
    llm_auth_token: str = ""              # claude: setup-token from `claude setup-token` (else use $ANTHROPIC_AUTH_TOKEN)
    llm_model: str = "qwen3:14b"         # claude: set to e.g. claude-sonnet-5-5
    llm_temperature: float = 0.1          # openai provider only (Opus 4.x rejects sampling params)
    llm_max_tokens: int = 4096            # claude provider: response cap
    log_tail_lines: int = 200             # how many log lines tools expose
    max_tool_calls: int = 8               # per-agent ReAct step cap
    max_critic_rounds: int = 2            # diagnose↔critic retries before CLOSED_NO_FIX
    reuse_recurring_diagnosis: bool = True  # when the same issue (dedup_key) recurs
                                          # AND was RESOLVED before, replay that prior
                                          # diagnosis + plan instead of re-running the
                                          # LLM. Unresolved recurrences diagnose fresh.
                                          # Saves tokens/compute; set false to always
                                          # diagnose fresh.
    reuse_recurrence_window_hours: int = 168  # how far back a prior incident counts
                                              # as the same recurrence (default 7 days)

    # --- Executor: only active in `mode: execute` ---
    verify_polls: int = 6                 # health checks after a fix
    verify_poll_interval: int = 10        # seconds between checks (~60s window)
    env_path: str = ""                    # env file for set_env_var (default: <compose dir>/.env)

    # --- Auto heal: off until a stack is listed. Needs `mode: execute`. ---
    auto_heal_stacks: list[str] = []      # compose project(s) whose low-risk fixes run without a click
    auto_heal_all: bool = False           # every app, including ones started later
    auto_heal_disk_cleanup: bool = False  # clear unused Docker images and build cache when the disk fills
    auto_heal_max_attempts: int = 3       # per container in 24 hours, then it stops and asks

    alert_target_label: str = "target"    # grafana alert label that names the app/target

    # --- App-level probes ---
    probes: ProbesConfig = Field(default_factory=ProbesConfig)


    def auto_heals(self, stack: str) -> bool:
        """Whether auto heal looks after this app."""
        return self.auto_heal_all or stack in self.auto_heal_stacks

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, v):
        if v not in VALID_MODES:
            raise ValueError(f"must be one of {', '.join(VALID_MODES)}")
        return v

    @field_validator("llm_provider")
    @classmethod
    def _check_provider(cls, v):
        if (v or "").lower() not in VALID_PROVIDERS:
            raise ValueError(f"must be one of {', '.join(VALID_PROVIDERS)}")
        return v

    @field_validator("auto_heal_max_attempts")
    @classmethod
    def _check_attempts(cls, v):
        if v < 1:
            raise ValueError("must be at least 1")
        return v

    @field_validator("project_name", "exclude_projects", "auto_heal_stacks", mode="before")
    @classmethod
    def _coerce_projects(cls, v):
        # Accept a single name ("shop") or a list (["shop", "grafana"]).
        # Empty string / None / [] means watch every container on the host.
        if not v:
            return []
        return [v] if isinstance(v, str) else [n for n in v if n]


def config_path() -> str:
    """Where the config file lives: $DIGDOWN_CONFIG, else ./digdown.yml."""
    return os.environ.get("DIGDOWN_CONFIG") or "digdown.yml"


def _read_yaml(p: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{p} isn't valid YAML. {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{p} should be a list of `key: value` settings.")
    return data


def _env_overrides() -> dict[str, str]:
    return {key: os.environ[var] for var, key in _ENV_OVERRIDES.items()
            if os.environ.get(var)}


def _build(data: dict[str, Any], where: str) -> Config:
    try:
        return Config(**data)
    except ValidationError as e:
        lines = [f"  {'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
                 for err in e.errors()]
        raise ConfigError(f"{where} has settings Digdown can't use:\n"
                          + "\n".join(lines)) from e


def load_config(path: Optional[str] = None) -> Config:
    """Read the config file (defaults if it doesn't exist), then apply environment
    overrides. Raises ConfigError with a readable message when it can't be used."""
    p = Path(path or config_path())
    data = _read_yaml(p) if p.exists() else {}
    return _build({**data, **_env_overrides()}, str(p))


def unknown_keys(path: Optional[str] = None) -> list[str]:
    """Top-level keys Digdown doesn't recognise, usually typos it would otherwise
    ignore without saying anything."""
    p = Path(path or config_path())
    if not p.exists():
        return []
    return sorted(set(_read_yaml(p)) - set(Config.model_fields))


def save_config(path: str, updates: dict[str, Any]) -> None:
    """Merge `updates` into the config file. The file can hold API keys, so it's
    written owner-only. An existing file is copied to <name>.bak first because
    rewriting YAML drops its comments."""
    p = Path(path)
    data = _read_yaml(p) if p.exists() else {}
    merged = {**data, **updates}
    _build(merged, str(p))  # refuse to write something that wouldn't load
    if p.exists():
        backup = p.with_name(p.name + ".bak")
        shutil.copy2(p, backup)
        os.chmod(backup, 0o600)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        yaml.safe_dump(merged, f, sort_keys=False)
    os.replace(tmp, p)


# Keys editable at runtime — each VERIFIED to be read at use-time (so a live
# mutation of Config takes effect without a restart). Keys captured at
# construction (exclude_projects, max_tool_calls, log_tail_lines, agent_enabled)
# are intentionally excluded and shown read-only.
RUNTIME_SAFE_KEYS = frozenset({
    "mode", "poll_interval_seconds", "debounce_polls", "restart_loop_threshold",
    "correlation_window_seconds", "disk_percent_threshold", "mem_percent_threshold",
    "max_critic_rounds", "reuse_recurring_diagnosis", "verify_polls", "verify_poll_interval",
    "auto_heal_stacks", "auto_heal_max_attempts", "auto_heal_disk_cleanup", "auto_heal_all",
})

# Read at startup, so changing one means a restart. Network, access, secrets and paths
# stay out, so a browser form can't open Digdown up or point it at other files.
FILE_EDITABLE_KEYS = frozenset({
    "log_tail_lines", "max_tool_calls", "llm_temperature", "llm_max_tokens",
    "collect_resource_stats", "reuse_recurrence_window_hours", "disk_path",
})

# Never returned by GET, never accepted by PATCH — shown only as set/unset.
# notify_webhook_url is a credential (the URL itself authorizes posting).
SECRET_KEYS = frozenset({
    "auth_token", "llm_api_key", "llm_auth_token", "notify_webhook_url",
})

_NESTED_KEYS = frozenset({"probes"})


def apply_overrides(config: Config, overrides: dict) -> list[str]:
    """Apply runtime overrides onto `config` in place. Only RUNTIME_SAFE_KEYS are
    applied; unknown/non-whitelisted keys and values that fail validation are
    skipped. Returns the keys actually applied."""
    applied: list[str] = []
    base = config.model_dump()
    for key, value in overrides.items():
        if key not in RUNTIME_SAFE_KEYS:
            continue
        try:
            validated = Config(**{**base, key: value})
        except Exception:  # noqa: BLE001 - invalid override is skipped, not fatal
            continue
        setattr(config, key, getattr(validated, key))
        applied.append(key)
    return applied


def config_view(config: Config, overrides_meta: dict) -> dict:
    """Annotated, secret-redacted view of the effective config for GET /api/config."""
    fields = []
    for key, value in config.model_dump().items():
        secret = key in SECRET_KEYS
        editable = key in RUNTIME_SAFE_KEYS
        if key in _NESTED_KEYS:
            fields.append({"key": key, "value": "configured" if value else "—",
                           "source": "config", "editable": False, "file_editable": False,
                           "requires_restart": True, "secret": False})
            continue
        fields.append({
            "key": key,
            "value": ("set" if value else "unset") if secret else value,
            "source": "override" if key in overrides_meta else "config",
            "editable": editable,
            "requires_restart": not editable and not secret,
            "file_editable": key in FILE_EDITABLE_KEYS,
            "secret": secret,
            "identity": overrides_meta.get(key, {}).get("identity"),
        })
    return {"mode": config.mode, "fields": fields}
