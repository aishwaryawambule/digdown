---
title: Commands
description: Everything you can do from the command line.
---

## Commands

| Command | What it does |
|---|---|
| `digdown start` | Watches your containers and serves the dashboard. This is the default. |
| `digdown demo` | Runs with simulated containers and a scripted AI. Nothing real changes. |
| `digdown demo --port 8089` | Runs the demo on another port. |
| `digdown demo --no-browser` | Doesn't open a browser. |
| `digdown doctor` | Checks that everything is set up correctly, and says how to fix what isn't. |
| `digdown --config FILE start` | Uses a different settings file. `--config` goes before the command. |
| `digdown --version` | Shows the version. |
| `python -m digdown.evaluate --model NAME` | Scores an AI model on [failures with known causes](whats-been-tested.md). |

In the Docker install, put `docker compose exec digdown` in front, as in `docker compose exec digdown digdown doctor`.

## Environment variables

These override the settings file.

| Variable | What it sets |
|---|---|
| `DIGDOWN_CONFIG` | Path to the settings file. The default is `./digdown.yml`. |
| `DIGDOWN_HOST` | Address to listen on |
| `DIGDOWN_PORT` | Port to listen on |
| `DIGDOWN_DB_PATH` | Where the database is kept |
| `DIGDOWN_AUTH_TOKEN` | The sign-in token |
| `DOCKER_HOST` | Which Docker to connect to, used to [watch another server](watch-another-server.md) |
| `ANTHROPIC_API_KEY` | Your Claude API key |

## Keyboard shortcuts

Press `?` in the dashboard to see these at any time.

| Key | What it does |
|---|---|
| `1` to `5` | Go to Incidents, Auto heal, Containers, Trends, Settings |
| `j` or `↓` | Next incident |
| `k` or `↑` | Previous incident |
| `Enter` or `o` | Open the selected incident |
| `esc` | Go back |
| `←` `→` | Previous or next, while an incident is open |
| `a` | Approve the fix, while an incident is open |
| `r` | Reject the fix, while an incident is open |
| `/` | Filter by name |
