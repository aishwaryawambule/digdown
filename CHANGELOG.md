# Changelog

## 0.2.0 (beta)

First public version.

- Watches Docker containers and explains why one failed, in plain language, with a local or hosted AI.
- Suggests a fix, applies it when you approve, checks that it held, and undoes it if it didn't.
- Auto heal: any fix that needs no decision from you runs without a click, for every app or app by app, with limits. Off by default, and setup offers it as **Fix things by itself**.
- Fixes a stopped dependency or a container that never started in about 20 seconds, without using the AI.
- Optional disk cleanup: deletes unused images and build cache when the disk is full. Off until you confirm it.
- Alerts to Discord, Slack, ntfy, or anything that accepts JSON.
- Website checks, added in Settings, catch an app that's running but returning errors or slow.
- A ready-made image for Intel and ARM, so installing is one `docker run` command. Digdown prints a link that signs you in.
