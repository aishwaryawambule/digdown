---
title: How it works
description: How the pieces fit together, for the curious and for contributors.
---

Digdown watches your containers, works out why one failed, and, if you let it, fixes it. The part that thinks about a failure and the part that changes things are kept apart:

- **The AI can only read.** It looks at logs, container state and how your services connect, and it has no tool that changes anything.
- **One part makes changes, the Executor.** It runs only a short list of fixes, only on containers that exist, and only after you click Approve or auto heal's rules allow it.

```
notice ──▶ explain ──▶ plan a fix ──▶ you approve, or auto heal ──▶ fix ──▶ check it held ──▶ done, or undo
```

## Noticing a failure

**The Watcher** asks Docker about every container every 15 seconds (`poll_interval_seconds`). It raises a signal when a container:

- crashed, or ran out of memory
- is failing its health check
- keeps restarting (`restart_loop_threshold` restarts in a row)
- stopped by itself, is paused, or never started
- is close to its memory limit

It also raises one when the disk is nearly full (`disk_percent_threshold`).

Each signal has a key made of where it came from, the container, and what went wrong, so the same failure doesn't open a second incident. A signal has to last for `debounce_polls` checks before it counts.

**Website and log checks** catch an app that's running but broken, which Docker can't see. A website check opens an address and reports errors, no answer, or a slow answer. A log check counts lines that look like errors, and stores only the counts, never the log text. See [configuration](configuration.md#website-and-log-checks).

**Grafana alerts** can arrive at `POST /api/alerts`. Each one opens an incident for the app its `alert_target_label` label names, even if that app runs somewhere Digdown can't see.

**Related failures are grouped.** If a service fails while something it depends on (Compose `depends_on`) already has an open incident, within `correlation_window_seconds`, the new incident is linked to that one. It isn't diagnosed separately, because the cause is the same.

## Working out the cause

Incidents are diagnosed one at a time, by four AI steps. Each step's full record, every tool it called and what came back, is saved with the incident.

| Step | What it does |
|---|---|
| **Diagnostician** | Investigates with the read-only tools below, up to `max_tool_calls` times, and gives a cause, the evidence and how sure it is. |
| **Critic** | A second check: does the evidence support that cause? If not, the Diagnostician tries again, up to `max_critic_rounds` times. |
| **Planner** | Suggests a fix from the short list below, or, if none fits, says what you should do. |
| **Plan critic** | Checks that the fix is safe, can be undone, and touches only what it should. |

If the Critic still disagrees after its last round, or there's no safe fix, the incident closes with advice for you (`closed_no_fix`).

Some failures need no AI at all. When a service that an app depends on has simply stopped, or a container never started, Docker already says why, so Digdown proposes starting it straight away.

### What the AI can look at

| Tool | What it returns |
|---|---|
| `get_container_state` | Status, health, exit code, restart count, whether it ran out of memory, image |
| `get_recent_logs` | The latest log lines, with secrets removed and marked as untrusted |
| `inspect_container` | Image, command, restart policy, and the **names** of environment variables, never their values |
| `get_topology` | Service names and what depends on what |
| `http_probe` | An address's status, how long it took, and the start of the page, with secrets removed. Only addresses you've allowed. |
| `system_inspect` | A summary of the host. Only if you turn on `host_introspection`. |

For an incident about something that isn't a local container, such as a Grafana alert, the container tools aren't offered. See [security](security.md).

## Making a fix

The Executor only runs when Digdown is set to **Fix after I approve** (`mode: execute`). These are the only fixes it knows:

| Fix | What it does | Risk |
|---|---|---|
| `start` | Starts a stopped container | Low |
| `restart` | Restarts a container | Low |
| `set_resource_limit` | Raises a container's memory limit | Medium |
| `recreate` | Recreates one container with `docker compose up -d --force-recreate --no-deps` | Medium |
| `set_env_var` | Changes a value in your env file, then recreates the container | High |
| `clean_docker` | Deletes unused images and the build cache when the disk is full. Never offered to the AI, and off until you turn it on. | Medium |

Every fix goes through the same steps:

1. **Check the plan again** against the list of fixes and the containers that exist.
2. **Save the current state** of each container it will touch.
3. **Make the change,** one container at a time.
4. **Watch the container** for `verify_polls` checks, `verify_poll_interval` seconds apart.
5. **Undo the change** if a step failed or the container didn't stay healthy.

It ends as **fixed** (the check passed), **undone** (the check failed and the change was reversed) or **failed** (a step, or undoing it, went wrong).

## Auto heal

Just before a fix would wait for your approval, the rules in `digdown/heal.py` decide whether it may run by itself. They use facts from Docker, not anything the AI says. If they allow it, the fix runs through the same steps as a click. If not, it waits for you, and the reason is recorded. See [auto heal](auto-heal.md).

## An incident's life

```
detected → triaged → investigating → proposed → approved → executing → verifying → resolved
                          │              │                                   │
                          │              └──▶ resolved (problem went away)   ├──▶ rolled_back
                          ├──▶ diagnosed (explain-only mode)                 └──▶ failed
                          ├──▶ diagnosis_failed ──(retry)──▶ detected
                          └──▶ closed_no_fix
```

It ends in one of: `resolved`, `rejected`, `rolled_back`, `failed`, `closed_no_fix` or `muted`.

- **In explain-only mode,** it stops at `diagnosed`, and waits for the problem to go away or for someone to dismiss it.
- **It closes by itself** when the problem has been gone for 4 checks in a row, about a minute, with no restarts in that time. This applies to `detected`, `diagnosed`, `diagnosis_failed` and `proposed`. Work already under way is left to finish. A Grafana incident closes when Grafana says the alert has cleared.
- **It stays closed.** After `closed_no_fix`, `rejected`, `rolled_back` or `failed`, a problem that never went away doesn't open a new incident. It does once it clears and comes back. A problem still there after `resolved` opens a new one, because the fix didn't hold.
- **After Digdown restarts,** anything that was in progress was interrupted. `triaged` and `investigating` go back to `detected`, or to `diagnosed` in explain-only mode if the cause was already found. In execute mode, `approved` goes back to `proposed`, because nothing had changed yet. `executing` and `verifying` become `failed`, saying the fix may be only partly applied.
- **A failed diagnosis** can be retried, or dismissed to `closed_no_fix`.

## Where things are kept

One SQLite file (`db_path`) holds everything:

| Table | What's in it |
|---|---|
| `incidents` | Each incident: its state, container, signal, and the incident it's linked to |
| `diagnoses` | The cause, evidence, confidence and the Critic's verdict |
| `plans` | The proposed fix, its risk, the advice for you, and the plan critic's verdict |
| `approvals` | Each approve or reject, and who made it (`auto-heal` for auto heal) |
| `executions` | What each fix did, what was saved first, and whether it was undone |
| `audit_log` | Every step, by every part of Digdown, never edited afterwards |
| `mutes` | Containers you've muted |
| `settings` | Settings changed in the dashboard |

The audit log is the record of what happened and why. The incident page and the query in [troubleshooting](troubleshooting.md) both read from it.

## The API

The dashboard uses the same API you can call yourself, on port 8088. When `auth_token` is set, every call except `/api/health` and signing in needs it.

| Address | Method | What it does |
|---|---|---|
| `/api/health` | GET | Whether Digdown is up, and its mode |
| `/api/status` | GET | Docker, the AI, the mode and auto heal, as the dashboard shows them |
| `/api/incidents` | GET | Incidents, newest first |
| `/api/incidents/{id}` | GET | One incident: diagnosis, fix, approvals, what ran, and its audit log |
| `/api/incidents/{id}/logs` | GET | The container's logs, with secrets removed |
| `/api/incidents/{id}/approve` | POST | Approve the proposed fix |
| `/api/incidents/{id}/reject` | POST | Reject it |
| `/api/incidents/{id}/retry` | POST | Diagnose again after a failure |
| `/api/incidents/{id}/dismiss` | POST | Close an incident that's stuck |
| `/api/services` | GET | The containers being watched, and their website checks |
| `/api/alerts` | POST | Receive a Grafana alert |
| `/api/mutes` | GET, POST | List or add mutes |
| `/api/mutes/{pattern}` | DELETE | Remove a mute |
| `/api/history` | DELETE | Clear finished incidents |
| `/api/config` | GET, PATCH | Read settings, or change one that applies straight away |
| `/api/checks` | GET, PUT | Read or replace the website checks |
| `/api/stream` | GET | Live updates, as Server-Sent Events |

## What it's built with

- **Backend:** Python, FastAPI, SQLite and the Docker SDK. It talks to any OpenAI-compatible AI service or to Claude directly, with no framework in between.
- **AI steps:** plain loops of "call a tool, read the answer", with the whole exchange saved.
- **Dashboard:** React, built into the Python package and served by Digdown itself. See [the dashboard](using-the-dashboard.md).
