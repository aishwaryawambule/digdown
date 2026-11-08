---
title: Settings file
description: Every setting in digdown.yml, with its default.
---

Most people never need to edit this file: the setup screen writes it, and the dashboard's Settings page changes the common options. It's for everything else.

Digdown reads a single YAML file, **`digdown.yml`**, **at startup**, so restart Digdown after editing it. Every key is optional and falls back to the default shown below. [`digdown.example.yml`](https://github.com/aishwaryawambule/digdown/blob/main/digdown.example.yml) is an annotated template:

```bash
cp digdown.example.yml digdown.yml
```

Run `digdown doctor` to check the file. It reports settings Digdown doesn't recognise, which are usually typos.

To keep keys out of this file, set them in the environment instead, such as `ANTHROPIC_API_KEY`. See [run on a server](run-on-a-server.md).

## What to watch

| Key | Type | Default | What it does |
|---|---|---|---|
| `mode` | string | `observe` | What Digdown may do: `observe` (explain only), `propose` (suggest fixes) or `execute` (fix after you approve). See [what Digdown may do](index.md). |
| `compose_path` | string | `docker-compose.yml` | Your stack's Compose file. Digdown uses it to see how services depend on each other, and to recreate a container. |
| `project_name` | string or list | not set | `""` or `[]` watches every container. A name, or a list such as `[web, db]`, watches only those Compose apps. If it isn't set at all, Digdown watches the app in `compose_path`, or everything if there's no such file. |
| `exclude_projects` | list | `[]` | Compose apps never to watch. This wins over `project_name`, so use it for an app you've taken down on purpose. |
| `collect_resource_stats` | bool | `true` | Collect CPU and memory use. Turn it off to make each check faster, especially over SSH. |
| `poll_interval_seconds` | int | `15` | How often Digdown checks your containers. |
| `debounce_polls` | int | `1` | How many checks in a row a problem must last before it becomes an incident. Raise it if brief blips open incidents. |
| `restart_loop_threshold` | int | `3` | How many restarts in a row count as a restart loop. |
| `correlation_window_seconds` | int | `120` | If a service fails within this many seconds of something it depends on, the two are grouped, and only the first is diagnosed. |
| `mem_percent_threshold` | float | `90.0` | Warn when a container uses this much of its memory limit. |
| `disk_percent_threshold` | float | `90.0` | Open an incident when the disk is this full. |
| `disk_path` | string | `/` | The folder whose disk is checked. |

## Server

| Key | Type | Default | What it does |
|---|---|---|---|
| `host` | string | `127.0.0.1` | The address Digdown listens on. The default means only this computer can reach it. Any other address needs a token, and Digdown makes one if you haven't. See [security](security.md). |
| `port` | int | `8088` | The port for the dashboard and API. |
| `db_path` | string | `digdown.db` | The SQLite file that holds incidents and their history. |

## The AI

The AI works by calling Digdown's tools, so the model must be able to call tools reliably, or every diagnosis fails. See [choose an AI](choose-an-ai.md).

| Key | Type | Default | What it does |
|---|---|---|---|
| `agent_enabled` | bool | `true` | `false` turns the AI off. Digdown still says what failed and when. |
| `llm_provider` | string | `openai` | `openai` for any OpenAI-compatible service, including Ollama, or `claude` to use Claude directly. |
| `llm_base_url` | string | `http://localhost:11434/v1` | The service's address, for `openai`: Ollama, LiteLLM, vLLM, OpenRouter and others. Not used with `claude`. |
| `llm_api_key` | string | `not-needed` | The API key, for `openai`. Most local services ignore it. |
| `llm_model` | string | `qwen3:14b` | The model, such as `qwen3:14b` or `claude-sonnet-5-5`. |
| `reuse_recurring_diagnosis` | bool | `true` | When the same problem comes back on the same container, reuse the earlier diagnosis and fix instead of starting again. |
| `reuse_recurrence_window_hours` | int | `168` | How long an earlier diagnosis can be reused. |
| `host_introspection` | bool | `false` | Lets the AI read a summary of the host (`system_inspect`). |
| `llm_temperature` | float | `0.1` | Lower gives steadier answers. Only for `openai`. |
| `llm_max_tokens` | int | `4096` | The longest answer, for `claude`. |
| `llm_auth_token` | string | `""` | A Claude token from `claude setup-token`, used instead of an API key. Leave it empty to read `$ANTHROPIC_AUTH_TOKEN`. |
| `log_tail_lines` | int | `200` | The most log lines the AI can read. |
| `max_tool_calls` | int | `8` | How many tools the AI can call in each step. Lower it to speed up diagnosis. |
| `max_critic_rounds` | int | `2` | How many times to diagnose again when the second check disagrees, before giving up. |

### Signing in to Claude

Use an Anthropic API key, either as `ANTHROPIC_API_KEY` in the environment or as `llm_api_key`. Digdown looks for `llm_auth_token`, then `$ANTHROPIC_AUTH_TOKEN`, then `llm_api_key`, then `$ANTHROPIC_API_KEY`, and uses the first it finds. If you use a token from `claude setup-token` instead, check that Anthropic's terms allow it for this kind of tool.

## Fixes

These apply only when `mode` is `execute`.

| Key | Type | Default | What it does |
|---|---|---|---|
| `verify_polls` | int | `6` | How many times Digdown checks a container after fixing it. |
| `verify_poll_interval` | int | `10` | Seconds between those checks. Together they're how long a fix must hold, about a minute by default. |
| `env_path` | string | `""` | The env file `set_env_var` changes. By default, `.env` beside your Compose file. |

Digdown knows only six fixes: `start`, `restart`, `set_resource_limit`, `recreate`, `set_env_var` and `clean_docker`, and only for containers that exist. Each needs your approval, unless auto heal is on for that app, and auto heal only starts and restarts containers and raises memory limits. See [how it works](how-it-works.md#making-a-fix).

## Auto heal

Off until you turn it on, and it needs `mode: execute`. You can also change these on the **Auto heal** page. See [auto heal](auto-heal.md) for what it will and won't do.

| Key | Type | Default | What it does |
|---|---|---|---|
| `auto_heal_all` | bool | `false` | Every app, including ones started later. Setup's **Fix things by itself** turns this on. |
| `auto_heal_stacks` | list | `[]` | Or only these Compose apps. Use `(no stack)` for containers started with a plain `docker run`. |
| `auto_heal_max_attempts` | int | `3` | Fixes per container in 24 hours, before it stops and asks you. |
| `auto_heal_disk_cleanup` | bool | `false` | When the disk is full, delete images nothing uses and the build cache, at most once a day. Never containers or volumes. This can't be undone. |

## Access and alerts

| Key | Type | Default | What it does |
|---|---|---|---|
| `auth_token` | string | `""` | If set, the dashboard and every API call need it, sent as `Authorization: Bearer …` or `X-Digdown-Token`. See [security](security.md). |
| `notify_webhook_url` | string | `""` | Where alerts go: a Discord or Slack webhook, an ntfy topic (the address must contain `ntfy`), or any address that accepts JSON. You can also set it under **Settings → Alerts**. |

## Website and log checks

Optional. Docker can tell when a container crashes, but not when the app inside is
running and returning errors. These checks catch that. A failed check becomes an
incident like any other, and auto heal restarts the container if it's on for that app.

**Website checks can be added in the dashboard,** under **Settings → Apps → Website
checks**, so you only need this section for the extra options below. Log checks are
only in the settings file for now.

Each check names a container (`target`) Digdown is watching. A check naming a
container it can't find is ignored.

### Website checks: `probes.http`

Digdown opens the address on every check (a plain `GET`, which changes nothing). It
opens an incident if the address returns an error, doesn't answer, or answers slowly.

| Key | Type | Default | What it does |
|---|---|---|---|
| `name` | string | none | A name for the check, different for each one. The dashboard makes one up. Required. |
| `url` | string | none | The address, starting with `http://` or `https://`. Required. |
| `target` | string | none | The container this address belongs to. Required. |
| `expect_status` | string | `2xx-3xx` | Which answers count as working: `2xx-3xx`, `200`, or a range like `200-204`. |
| `timeout_ms` | int | `5000` | How long to wait for an answer, in milliseconds. |
| `latency_threshold_ms` | int | `2000` | Slower than this counts as a problem, in milliseconds. |

### Log checks: `probes.logs`

Reads a container's recent logs, and opens an incident when too many lines look like
errors in a short time. It stores only the counts, never the log text.

| Key | Type | Default | What it does |
|---|---|---|---|
| `target` | string | none | The container whose logs to read, such as a reverse proxy. Required. |
| `name` | string | `errors` | A name for the check. Give each check on the same container its own. |
| `patterns` | list | HTTP 5xx errors, tracebacks, `ERROR` | The patterns (regular expressions) that count as errors. Leave it out to use the defaults. |
| `threshold` | int | `5` | How many matching lines open an incident. |
| `window_seconds` | int | `60` | Within how many seconds. |
| `max_lines` | int | `1000` | The most lines read on each check. |

## Grafana alerts

| Key | Type | Default | What it does |
|---|---|---|---|
| `alert_target_label` | string | `target` | The alert label that names the app. Digdown reads it from alerts sent to `POST /api/alerts`. |

## A short example

```yaml
mode: observe
project_name: ""
agent_enabled: true
llm_base_url: http://localhost:11434/v1
llm_model: qwen3:14b
```

[`digdown.example.yml`](https://github.com/aishwaryawambule/digdown/blob/main/digdown.example.yml) has every setting, with notes, including website and log checks.

## Environment variables

These override the settings file, so a container can be set up without one.

| Variable | Setting |
|---|---|
| `DIGDOWN_CONFIG` | Where the settings file is (default `./digdown.yml`) |
| `DIGDOWN_HOST` | `host` |
| `DIGDOWN_PORT` | `port` |
| `DIGDOWN_DB_PATH` | `db_path` |
| `DIGDOWN_AUTH_TOKEN` | `auth_token` |
| `DIGDOWN_PORT_IS_LOCAL` | Set to `1` to run without a token in a container whose port is published only to `127.0.0.1`. Not recommended: other containers on the machine may still reach it. |
