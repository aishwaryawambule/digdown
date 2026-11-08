---
title: Practise with a broken stack
description: See Digdown handle real failures safely, using containers that are broken on purpose.
---

The `sandbox` folder is a small stack that's broken on purpose. It's the safest way to see what Digdown does with real containers before you trust it with your own apps.

**You'll need:** Digdown [installed and set up](install-with-docker.md), with **Everything** chosen in setup, or the `digdown-sandbox` stack added under Settings → Apps.

## Steps

1. **Start the broken stack,** from the `digdown` folder.

   ```bash
   docker compose -f sandbox/docker-compose.yml up -d
   ```

2. **Open the Incidents page.** **You should see** incidents appear within a minute or so:

   | Container | What's wrong with it | What Digdown should notice |
   |---|---|---|
   | `crasher` | Exits as soon as it starts | Crash |
   | `flapper` | Crashes every few seconds | Restart loop |
   | `hog` | Uses more memory than its limit | Out of memory |
   | `sicko` | Its health check always fails | Unhealthy |
   | `db` | Dies as soon as it starts | Crash |
   | `config` | Crashes because a setting is missing | Crash |

3. **Read a diagnosis.** With an AI connected, each incident gets a cause. A local AI can take several minutes for each one. Open one and read what it found.

4. **Try a fix.** Go to **Settings → Fixes** and choose **Fix after I approve**. For new incidents with a proposed fix, you can then click **Approve fix** and watch Digdown apply it and check it.

5. **Try auto heal.** See [auto heal](auto-heal.md). Turn it on for `digdown-sandbox`, and new failures that qualify are fixed without a click.

## Clean up

```bash
docker compose -f sandbox/docker-compose.yml down -v
```

Its incidents stay in your history. To remove them, switch the incident list to **Cards** and click **Clear** beside Finished. This clears all finished incidents, not just these.
