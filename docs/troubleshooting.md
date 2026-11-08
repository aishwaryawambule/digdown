---
title: Troubleshooting
description: What to do when something isn't working.
---

## Start here

Run the built-in check. It lists what's wrong and how to fix it.

```bash
digdown doctor                                # direct install
docker compose exec digdown digdown doctor    # Docker install
```

## Common problems

| What you see | What to do |
|---|---|
| **Sign-in asks for a token** | Open the sign-in link from `docker logs digdown` (with Compose, `docker compose logs digdown`). The token is also in `/data/digdown.token` in the container. |
| **"Nothing is being watched"** | Digdown can't reach Docker. Start Docker, and Digdown reconnects by itself. |
| **"Diagnosis failed"** | The AI couldn't be reached, or the model can't call tools. Check the reason shown, make sure your AI is running, then click **Retry diagnosis**. |
| **Connection error, or the AI stops responding** | A local model may have run out of memory, especially if the computer is busy. Close other heavy programs, restart Ollama, or try a smaller model. |
| **Diagnoses are slow** | Normal for local models. Each one makes several AI calls, and incidents are handled one at a time. Use a faster model if this matters. |
| **"A second AI check disagreed"** | The cause shown is a best guess. Check the evidence on the Details tab. |
| **Port 8088 is in use** | Direct install: set `DIGDOWN_PORT`. Demo: use `--port`. Docker install: change the port in `docker-compose.yml`. |
| **Edited `digdown.yml` and nothing changed** | That file is read at startup, so restart Digdown. Changes in the dashboard apply straight away, or tell you if they need a restart. |
| **`pip install .` says the environment is externally managed** | Create a Python environment first: `python3 -m venv .venv && . .venv/bin/activate` |
| **A container I stopped shows as crashed** | Digdown can tell a deliberate stop from Docker's event log, which is short. It can miss one made while Digdown wasn't running. Auto heal asks first in that case. |
| **An app isn't listed** | Check **Settings → Apps**, as it may not be on your list. |
| **A raised memory limit disappeared** | Recreating a container, as an update does, loses it. Add `mem_limit` to your Compose file. |

## Ollama from the Docker install

Use `http://host.docker.internal:11434/v1` as the address, not `localhost`, because `localhost` inside a container means the container itself. On Linux, see [choose an AI](choose-an-ai.md).

## Still stuck?

[Open an issue](https://github.com/aishwaryawambule/digdown/issues), with the output of `digdown doctor`. Remove anything private from logs first. For a security problem, see [how to report it](security.md#reporting-a-problem).
