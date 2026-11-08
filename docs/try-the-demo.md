---
title: Try the demo
description: See Digdown handle four failures in about a minute, with simulated containers and a scripted AI.
---

The demo needs no Docker and no AI model. The containers and the AI are simulated, so nothing on your computer changes and no data leaves it.

**You'll need:** Python 3.11 or newer, Node 22 or newer, and Git.

## Steps

1. **Get the code.**

   ```bash
   git clone https://github.com/aishwaryawambule/digdown.git
   cd digdown
   ```

2. **Create a Python environment.** This keeps Digdown separate from the rest of your computer.

   ```bash
   python3 -m venv .venv
   . .venv/bin/activate
   ```

3. **Build the dashboard and install Digdown.** This takes a minute or two the first time.

   ```bash
   scripts/build-dashboard.sh
   pip install .
   ```

4. **Start the demo.**

   ```bash
   digdown demo
   ```

   **You should see** `Digdown demo is running at http://localhost:8088`, and your browser opens the dashboard. If port 8088 is taken, run `digdown demo --port 8089`.

## What to look for

Four failures play out over about a minute, one for each way an incident can end:

| Container | What happens | What to do |
|---|---|---|
| `shop-cache` | Runs out of memory | Click **Approve fix**. Digdown raises the limit and confirms it worked. |
| `photos-server` | Runs out of memory | Nothing. Auto heal is on for its app, so it's fixed without a click. |
| `shop-worker` | Crashes on a missing secret | Open it. Digdown can't safely fix this, so it tells you what to do. It waits until `shop-cache` is dealt with, because it may be related. |
| `shop-web` | Fails its health check | Click **Approve fix**. The restart doesn't help, so Digdown undoes it and says so. |

Then look at **Auto heal** in the menu, to see what it fixed and when it held back.

## Stop the demo

Press `Ctrl+C`. The demo's data is deleted.

## Next

[Install it for your own containers](install-with-docker.md).
