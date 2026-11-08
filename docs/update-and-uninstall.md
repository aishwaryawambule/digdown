---
title: Update, back up, or remove
description: Keep Digdown up to date, save your data, or remove it completely.
---

## Update

**Docker install, with the one command:** get the new version, then remove the old container and start it again with [the same command](install-with-docker.md#steps). Your settings and history are kept, because they're in the `digdown-data` volume.

```bash
docker pull ghcr.io/aishwaryawambule/digdown:latest
docker rm -f digdown
```

**Docker install, with Compose:** from the folder with your `docker-compose.yml`:

```bash
docker compose pull
docker compose up -d
```

If you built it yourself, run `git pull`, then `docker compose up -d --build` instead.

**Direct install:**

```bash
git pull
scripts/build-dashboard.sh
pip install .
```

Then restart Digdown. Your settings and history are kept.

## Back up

Everything lives in a Docker volume in the Docker install, or in `digdown.yml` and `digdown.db` in a direct install. The volume is called `digdown-data` with the one command, and `digdown_digdown-data` with Compose. To copy it to a file:

```bash
docker run --rm -v digdown-data:/data -v "$PWD":/backup alpine tar czf /backup/digdown-backup.tar.gz -C /data .
```

With Compose, use `digdown_digdown-data:/data` instead.

**Keep this file safe.** It contains your settings, any API keys, and your sign-in token.

## Remove it

These also delete Digdown's settings and history.

**Docker install, with the one command:**

```bash
docker rm -f digdown
docker volume rm digdown-data
docker rmi ghcr.io/aishwaryawambule/digdown:latest
```

**Docker install, with Compose,** from its folder:

```bash
docker compose down -v --rmi all
```

**Direct install:** stop Digdown, then delete the `digdown` folder.

None of these touch your other containers. A memory limit Digdown raised stays on that container until the container is recreated.
