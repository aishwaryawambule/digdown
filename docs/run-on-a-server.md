---
title: Run on a server
description: Keep Digdown running on the server it watches, and reach it safely.
---

For round-the-clock watching, run Digdown on the server itself.

## Steps

1. **On the server, follow [install with Docker](install-with-docker.md).** It keeps running after a reboot, and only the server itself can reach the port.

2. **From your own computer, open an SSH tunnel.**

   ```bash
   ssh -N -L 8088:127.0.0.1:8088 you@your-server
   ```

3. **Open [http://localhost:8088](http://localhost:8088)** on your computer, and sign in with the token.

## Choosing an AI on a server

- **A small server probably can't run a local model.** A hosted service is the easy option, but your logs go to it. See [choose an AI](choose-an-ai.md).
- **A model on another machine** works if Digdown can reach its address.

## Please don't

- **Don't publish port 8088 to the internet,** even with the token. Control of Digdown means control of Docker.
- **Don't run it on production first.** Try [the broken stack](practice-with-the-sandbox.md) somewhere safe, and start on **Explain only**.

## Running on Linux

Digdown's automated tests and image build pass on Linux, but it hasn't watched real containers on a Linux server yet. See [what's been tested](whats-been-tested.md). If you try it, please [report what you find](https://github.com/aishwaryawambule/digdown/issues).
