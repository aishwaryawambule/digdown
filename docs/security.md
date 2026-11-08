---
title: Security
description: Who can reach Digdown, what it can change, and what leaves your computer.
---

Digdown can control Docker and sends failure details to an AI, so both are kept on a short leash.

## The AI can only read

The four AI steps (Diagnostician, Critic, Planner and plan critic) have no tool that changes anything. They can only call `get_container_state`, `get_recent_logs`, `inspect_container`, `get_topology` and `http_probe`.

Only one part of Digdown makes changes, the Executor. It:

- runs only when Digdown is set to **Fix after I approve** (`mode: execute`)
- runs only after you click **Approve**, or when auto heal's rules allow it
- knows only six fixes: `start`, `restart`, `set_resource_limit`, `recreate`, `set_env_var` and `clean_docker`, and only on containers that exist
- checks the plan against that list again before doing anything

So without auto heal, the worst a confused or manipulated AI can do is suggest a bad fix, which the plan critic and you then turn down.

### Auto heal, and its limits

Auto heal is off until you turn it on, for every app (`auto_heal_all`) or for chosen ones (`auto_heal_stacks`). For those apps, a fix can run with nobody checking it, so a wrong diagnosis can cause a needless restart. Someone who can write lines into a container's logs could also try to talk the AI into a restart.

The limits are plain code in `digdown/heal.py`. They use facts from Docker, not anything the AI says, so a manipulated AI can't stretch them:

- Only starting or restarting a container, or raising an existing memory limit by up to double.
- Only for apps it's on for, and only when the container really is failing.
- Never on Digdown's own container, even when it's on for every app.
- Never on a container someone stopped on purpose, or one that was already down when Digdown noticed.
- A limit per container and per hour, after which it asks you.
- Clearing unused Docker data only if you've turned that on, and at most once a day.

So the most a manipulated AI could cause is a few restarts of a container that's already failing, and a memory limit up to double. Everything else goes to you. Every automatic fix is recorded with `auto-heal` as the approver.

## Secrets are removed first

Before anything reaches the AI, the database or the dashboard, Digdown removes secrets:

- Values from your stack's `.env` file and Compose `environment` are hidden.
- Things shaped like credentials, such as API keys, JWTs and connection strings, are hidden by pattern.
- `inspect_container` gives the names of environment variables, never their values.

This applies to logs, web pages and everything else a tool returns.

## Logs are treated as data, not instructions

Everything a tool returns is marked as untrusted, so the AI treats it as evidence, not commands. If someone hides an instruction in a container's logs, there's still no tool that changes anything for it to reach, and any fix it leads to still faces the plan critic and you. `tests/test_injection.py` tests this.

## Who can reach Digdown

**Without a token (the default for a direct install),** Digdown listens on `127.0.0.1` and answers only requests addressed to this computer. That also stops a hostile web page from reaching it through a trick called DNS rebinding. Something in another container, such as Grafana, reaches Digdown by a different name, so it needs a token.

**With a token,** every API call needs it, including reads, because logs and diagnoses can be sensitive. Set it in the settings file, or with `DIGDOWN_AUTH_TOKEN`:

```yaml
auth_token: "<a long random string>"
```

Scripts and Grafana send it as `Authorization: Bearer <token>` or `X-Digdown-Token: <token>`. The dashboard asks for it once, then keeps a sign-in cookie for 30 days. The cookie can't be read by scripts or sent from other sites, and it's made from the token, so changing the token signs everyone out.

**The Docker install always asks for a token,** because another container on the same computer may be able to reach its port, and control of Digdown means control of Docker. Digdown prints a sign-in link with the token when it starts. See it with `docker logs digdown`.

**If Digdown listens on any other address without a token,** it makes one, saves it as `digdown.token` beside the database, readable only by your user, and prints it at startup. It won't run open to the network. You can skip this with `DIGDOWN_PORT_IS_LOCAL=1`, if the container's port is published only to `127.0.0.1`. Don't set it if the port is published more widely.

**Use HTTPS if the traffic crosses a network you don't trust,** for example through a reverse proxy or an SSH tunnel. Without it, the token travels unencrypted. A proxy must pass on the original `Host` header, or set `X-Forwarded-Host`.

**Other websites can't act for you.** Digdown refuses changes sent from a page on another site, so a page you visit can't approve a fix in the background. The dashboard also blocks scripts from elsewhere, and being shown inside another site.

**The setup screen can only change** what to watch, the AI, the mode and auto heal. It can't change the network settings or the token, and a saved API key is never sent back to the browser. When you test a connection with the key left blank, the saved key is used only for the service it was saved for.

## The Docker socket

Digdown needs the Docker socket to see containers and fix them. Access to it amounts to full control of the computer, whatever Digdown itself is allowed to do, and mounting it read-only doesn't change that. The container runs as root for the same reason: socket access is already that much power. Don't run Digdown on a computer where that's unacceptable.

## Where secrets are kept

- **An API key entered during setup** is saved in the settings file, which only your user can read. Its `.bak` copy has the same protection. To keep keys out of the file, leave the field blank and set `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` in the environment instead.
- **The token** is in the settings file, the environment, or `digdown.token`.
- **In this repository,** `digdown.yml` is ignored by Git, and `digdown.example.yml` uses placeholders. Don't commit real hostnames or tokens.

## What leaves your computer

Digdown collects no usage data and contacts no one by itself. Only these leave, and only if you set them up:

- **Failure details, to the AI you choose.** With a local model such as Ollama, nothing leaves. Secrets are removed, but personal data in logs isn't, so use a local model if your logs hold any.
- **Alerts, to the address you set.** They include container names and causes.
- **Website checks,** which open the addresses you give them.

## Reporting a security problem

Please don't open a public issue. Use **Report a vulnerability** on the repository's **Security** tab.
