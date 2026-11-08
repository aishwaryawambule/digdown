---
title: Set up alerts
description: Get a message on Discord, Slack or your phone when something needs you.
---

Alerts tell you when something needs a decision, when a fix has worked or been undone, and when auto heal has acted.

**Discord, Slack and ntfy formats are covered by automated tests, but [they haven't been tried with real accounts yet](whats-been-tested.md).** **Send test** will show you whether yours works.

## Discord

1. In your Discord server, go to **Server Settings → Integrations → Webhooks → New Webhook**.
2. Choose a channel, then click **Copy Webhook URL**.
3. In Digdown, go to **Settings → Alerts** and paste the address.
4. Click **Send test**. **You should see** a test message in that channel.
5. Click **Save**.

## Slack

1. Create an [incoming webhook](https://api.slack.com/messaging/webhooks) for your workspace and choose a channel.
2. Copy its address, which starts with `https://hooks.slack.com/`.
3. Paste it into **Settings → Alerts**, click **Send test**, then **Save**.

## Your phone, with ntfy

[ntfy](https://ntfy.sh) sends notifications to a free phone app.

1. Install the ntfy app, and subscribe to a topic with a name that's hard to guess, such as `digdown-7fk2m9qx`.
2. In **Settings → Alerts**, paste `https://ntfy.sh/your-topic-name`.
3. Click **Send test**, then **Save**.

**Anyone who knows a public topic's name can read it,** and alerts include container names and causes. Pick a hard name, or [host your own ntfy](https://docs.ntfy.sh/install/). A self-hosted ntfy works as long as `ntfy` appears in its address.

## Anything else

Any other address receives the details as JSON, including `title` and `message`.

## Turn alerts off

Go to **Settings → Alerts** and click **Turn off alerts**.
