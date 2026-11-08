---
title: First-time setup
description: The three questions Digdown asks the first time you open it.
---

The first time you open the dashboard, Digdown asks three questions. Until you finish, it doesn't watch or record anything. You can change every answer later.

## Step 1: Choose what to watch

- **Everything on this computer.** The usual choice. New apps are picked up by themselves.
- **Only the stacks you choose.** Good for a shared machine, or to keep one app's logs away from the AI.

Digdown reads the logs and details of whatever you choose, and sends the relevant parts to the AI you pick next.

## Step 2: Connect an AI

| Option | Good for | Where failure details go |
|---|---|---|
| **Ollama, on this computer** | Privacy, no running cost | Nowhere |
| **Claude** | Speed, no hardware needed | Anthropic |
| **Another service** | OpenAI, OpenRouter, LiteLLM and others | That service |
| **No AI for now** | Knowing what failed and when, but not why | Nowhere |

Pick one, then click **Test connection**. It makes a real call to check that the model works and can call tools. **You should see** a message saying the model is ready.

For step-by-step help with each option, see [choose an AI](choose-an-ai.md).

## Step 3: Decide what happens next

- **Explain only.** Shows what broke and why, and changes nothing. Start here.
- **Suggest fixes.** Also suggests a fix for you to carry out yourself.
- **Fix after I approve.** Applies a fix when you click Approve, checks it, and undoes it if it didn't hold.
- **Fix things by itself.** Turns on [auto heal](auto-heal.md) for every app, including ones you add later. Fixes that need no decision from you run without a click. A wrong setting, a missing secret or a bug still comes to you.

Not sure? Start with **Explain only** and watch what Digdown says for a while. You can turn on auto heal later, for one app or all of them, on the **Auto heal** page.

Click **Start watching**. Digdown restarts itself to apply your choices, which takes a few seconds and doesn't touch your containers.

## What you should see next

The **Incidents** page. If everything is fine, it says **Nothing needs you**. If something is already broken, an incident appears within a minute. A diagnosis with a local AI can take several minutes.

The bar along the bottom shows that Docker is connected, which AI is in use, and how far Digdown may go.

## Change your answers later

- **What it may do:** Settings → Fixes
- **Which apps it fixes by itself:** Auto heal
- **What it watches:** Settings → Apps
- **Which AI it uses:** Settings → AI → Change setup

## Next

- [See it handle real failures](practice-with-the-sandbox.md), safely, with a deliberately broken stack.
- [Set up alerts](alerts.md), so you hear about failures when you aren't looking.
