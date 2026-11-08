---
title: Watch another server
description: Run Digdown on your computer and watch a server's containers over SSH.
---

You can watch a server from your own computer without installing anything on the server. Digdown connects to the server's Docker over SSH.

**Connecting gives this access to everything on the server.** Access to Docker amounts to root, so use an SSH key, and stay on **Explain only** while you try this.

**This hasn't been tested against a real remote server yet.** See [what's been tested](whats-been-tested.md).

**You'll need:** Digdown [installed directly](install-directly.md) on your computer, and SSH access to the server with a user that can use Docker.

## Steps

1. **Check that you can reach the server's Docker.**

   ```bash
   ssh you@your-server docker ps
   ```

   **You should see** the server's containers.

2. **Forward the server's Docker to your computer.** Leave this running.

   ```bash
   ssh -nNT -L /tmp/server-docker.sock:/var/run/docker.sock you@your-server
   ```

3. **In another terminal, start Digdown pointing at it.** Use a separate settings file, so it doesn't mix with anything local.

   ```bash
   export DOCKER_HOST=unix:///tmp/server-docker.sock
   digdown --config ~/.config/digdown/my-server.yml start
   ```

4. **Finish setup** at [http://localhost:8088](http://localhost:8088). Choose **Explain only**.

## Good to know

- **Secrets:** Digdown masks secret-looking settings it reads from Docker. It can't see the server's `.env` files, so a secret under an unusual name could slip through. Keep that in mind if you use a hosted AI.
- **Speed:** every check goes over SSH. If it feels slow, turn off **Show CPU and memory use** under Settings → Fine-tuning → Advanced settings.
- **Fixes that recreate a container** need a Compose file on the machine running Digdown, so they aren't offered here.

## Prefer it on the server?

See [run on a server](run-on-a-server.md).
