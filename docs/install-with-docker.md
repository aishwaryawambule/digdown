---
title: Install with Docker
description: The recommended way to run Digdown. One container, nothing else to install.
---

This is the recommended install. It's one command, and you don't need Python, Node or Git.

**You'll need:** Docker.

## Steps

1. **Start Digdown.** Paste this on the computer or server where your apps run. It downloads Digdown the first time, which takes about a minute.

   ```bash
   docker run -d --name digdown --restart unless-stopped \
     -p 127.0.0.1:8088:8088 \
     -v /var/run/docker.sock:/var/run/docker.sock \
     -v digdown-data:/data \
     --add-host host.docker.internal:host-gateway \
     ghcr.io/aishwaryawambule/digdown:latest
   ```

   **You should see** a long line of letters and numbers. That's the new container's ID.

2. **Get your sign-in link.**

   ```bash
   docker logs digdown
   ```

   **You should see** `Open this link to sign in:`, followed by a link like `http://localhost:8088/#token=…`.

   Digdown has full control of Docker, so it won't let anything in without this token, including other containers on the same machine. The token stays the same when Digdown restarts.

3. **Open the link.** You're signed in, and setup starts. If the link doesn't work, go to [http://localhost:8088](http://localhost:8088) and paste the token printed under it.

4. **Answer the three setup questions.** You'll be asked what to watch, which AI to use, and what Digdown may do. To have it fix things without asking, choose **Fix things by itself**. See [first-time setup](first-time-setup.md).

### What the command does

| Part | Why |
|---|---|
| `-p 127.0.0.1:8088:8088` | Opens the dashboard to this computer only |
| `-v /var/run/docker.sock:…` | Lets Digdown see and restart your containers |
| `-v digdown-data:/data` | Keeps settings and history when Digdown is updated |
| `--add-host host.docker.internal:host-gateway` | Lets it reach an AI running on this computer, such as Ollama |

### Prefer Compose?

Save this as `docker-compose.yml` in a folder of its own, then run `docker compose up -d`. Get the sign-in link with `docker compose logs digdown`.

```yaml
name: digdown

services:
  digdown:
    image: ghcr.io/aishwaryawambule/digdown:latest
    restart: unless-stopped
    ports:
      - "127.0.0.1:8088:8088"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - digdown-data:/data
    extra_hosts:
      - "host.docker.internal:host-gateway"

volumes:
  digdown-data:
```

### Build it yourself instead

To run your own copy of the code, clone the repository and build it. This takes a few minutes.

```bash
git clone https://github.com/aishwaryawambule/digdown.git
cd digdown
docker compose up -d --build
```

## Check that it's working

```bash
docker exec digdown digdown doctor
```

**You should see** `ok` beside Docker and the settings file, then `No problems found`. A warning that `docker-compose.yml wasn't found` is normal in this install. It only means fixes that recreate a container aren't available.

## What this install can and can't do

It can start or restart a container, and raise its memory limit. That's everything auto heal needs.

It can't recreate a container or change a container's settings, because the image doesn't include the `docker` command or your Compose files. For those failures, Digdown tells you what to do instead of offering a fix it can't run.

## Where things are kept

Settings, history and the token live on a Docker volume called `digdown-data`. To edit the settings file, or just to read it:

```bash
docker exec digdown cat /data/digdown.yml
```

Most settings can be changed in the dashboard, so you may never need this.

## Reach it from another computer

Only this computer can reach it by default. On a server, the safest way in is an SSH tunnel, which needs no changes:

```bash
ssh -N -L 8088:127.0.0.1:8088 you@your-server
```

Then open [http://localhost:8088](http://localhost:8088) on your own computer.

**Never expose port 8088 directly to the internet.** Control of Digdown means control of Docker. See [security](security.md).

## Next

[First-time setup](first-time-setup.md).
