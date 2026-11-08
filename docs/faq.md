---
title: Questions
description: Common questions about Digdown.
---

## Is it free?

Yes. It's open source under the Apache 2.0 licence. A hosted AI costs whatever that service charges, while Ollama is free.

## Does it send my data anywhere?

Digdown has no telemetry and doesn't contact us. Only two things can leave your computer, and only if you set them up: incident details to a hosted AI, and alerts to the address you choose. With a local AI and no alerts, nothing leaves. See [security](security.md).

## Can it break my apps?

On **Explain only** or **Suggest fixes**, it changes nothing. With **Fix after I approve**, it changes things only when you click. With auto heal, it can start or restart containers and raise memory limits by itself. If you turn on the disk cleanup, it can also delete unused Docker images and build cache, which can't be undone. If the AI gets a cause wrong, the worst case is usually a needless restart of something already failing. Try it somewhere safe first.

## How is it different from a restart policy or docker-autoheal?

Those restart a container, which helps with a one-off crash. If a container keeps running out of memory, it just crashes again. Digdown finds out why, can raise the limit, checks that it held, and undoes it if it didn't.

## How is it different from Coolify or Portainer?

Those deploy and manage your apps. Digdown works out why something failed and can fix it. You can run it alongside them, though that hasn't been tested with Coolify yet.

## Do my apps need to move, or need changing?

No. Digdown connects to Docker and reads what it needs. Your apps stay where they are, and you don't need to hand it their Compose files.

## Does it work with Kubernetes?

No, only Docker and Docker Compose.

## Do I need a powerful computer?

Not for Digdown itself. A local AI model does need one: `qwen3:14b` has been tested on a Mac with 24 GB of memory. A hosted AI needs no special hardware, but your logs go to that service.

## Why does it need so much access?

To see containers and restart them, it needs Docker's socket, and that amounts to root on the machine. So it asks for a token, limits what it can change to a short list, and never lets the AI change anything directly. See [security](security.md).

## Can I use it at work?

The licence allows it, but it's a beta. Read [what's been tested](whats-been-tested.md), and start on **Explain only**, somewhere that isn't production.
