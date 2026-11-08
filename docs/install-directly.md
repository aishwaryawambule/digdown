---
title: Install without Docker
description: Run Digdown directly on your computer with Python.
---

Run Digdown directly if you're trying it on your own computer, or if you want fixes that recreate a container and change its settings, which the Docker install can't do.

**You'll need:** Python 3.11 or newer, Node 22 or newer, Git, and Docker running on the same computer.

## Steps

1. **Get the code and create a Python environment.**

   ```bash
   git clone https://github.com/aishwaryawambule/digdown.git
   cd digdown
   python3 -m venv .venv
   . .venv/bin/activate
   ```

2. **Build the dashboard and install Digdown.**

   ```bash
   scripts/build-dashboard.sh
   pip install .
   ```

3. **Check your setup.**

   ```bash
   digdown doctor
   ```

   **You should see** `ok` beside Python, Docker and the dashboard. A warning that `digdown.yml doesn't exist yet` is expected, as setup creates it.

4. **Start it.**

   ```bash
   digdown start
   ```

   **You should see** `Digdown is running at http://localhost:8088`.

5. **Finish setup.** Open [http://localhost:8088](http://localhost:8088) and follow [first-time setup](first-time-setup.md). No token is needed, because only this computer can reach it.

## Keep your settings somewhere else

Settings are saved to `digdown.yml` in the current folder. To keep them elsewhere:

```bash
digdown --config ~/.config/digdown/digdown.yml start
```

`--config` goes before `start`.

## Next

[First-time setup](first-time-setup.md).
