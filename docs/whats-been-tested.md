---
title: What's been tested
description: An honest list of what has and hasn't been tried, and how accurate the AI has been so far.
---

Digdown is a beta. This page says what has actually been tried, so you can decide how far to trust it.

## Tested with real containers

All on one Mac with Docker Desktop:

- The Docker install from a clean copy, including sign-in and first-time setup
- Noticing crashes, running out of memory, crash loops, failed health checks and deliberate stops
- Fixes that restart a container or raise a memory limit, checking them, and undoing them
- Auto heal fixing failures with no click, stopping after repeated tries, and asking when a problem comes back
- Linking a failing app to the database it depends on
- Another container trying to take control of Digdown, which was refused
- `qwen3:14b` on Ollama, diagnosing real failures
- Allowing fixes after starting on **Explain only**, so problems already explained get a fix

### Auto heal on failures caused on purpose

We made each of these happen for real, with auto heal on and nobody clicking. All passed on the Docker install, except the HTTP check, which passed on the install without Docker.

| What went wrong | What happened |
|---|---|
| A database stopped, with an app stuck restarting because of it | Database started, and the app recovered by itself |
| A container that never started | Started |
| A container close to its memory limit | Limit doubled, with no restart |
| An app returning HTTP 500, with a check set up | Restarted, and the check passed |
| An app missing a secret | Left for a person, with what to change |
| A job that finished by itself | Left alone |

The disk cleanup has run once on real Docker: it cleared the build cache and deleted no images. The disk only went from 93% to 92% full, on Docker Desktop for Mac.


## Not tested yet

- **Most apps by name.** Digdown works from what Docker reports, so nothing is specific to one app, but it has only been tried on a few stacks. Some apps will fail in ways these tests don't cover.
- **Linux servers.** The automated tests and image build pass on Linux, but Digdown hasn't watched real containers on a Linux server.
- **Hosted AI,** such as Claude or OpenAI, with a real key.
- **Coolify** and other deployment tools. Their apps are Docker containers, so they should work.
- **Real Discord, Slack or ntfy accounts.** Only the message formats are tested.
- **Watching another server over SSH.**
- **Fixes that recreate a container or change its settings,** on real Docker.

If you try any of these, please [tell us how it went](https://github.com/aishwaryawambule/digdown/issues).

## How accurate is the AI?

We score diagnosis on made-up failures with known causes, using `qwen3:14b` on a Mac with 24 GB of memory. Each case is run through the real code.

| Measure | Result (six cases, each run twice) |
|---|---|
| Found the right cause | 12 of 12 |
| Second AI check accepted a right answer | 12 of 12 |
| Proposed the right kind of fix | 10 of 12 |
| Time per failure | About 2 to 5 minutes, and one run took 14 on a busy computer |

A stopped dependency and a container that never started don't use the AI, so they're fixed in about 20 seconds. One of the six cases is a stopped database, so it counts as right without any AI work.

**This is a small test, so treat it as a rough guide:**

- **The AI doesn't reliably say when it can't tell why.** We told it to, then gave it a log showing an app shut down with no reason. In 3 runs out of 3, it called the shutdown deliberate. It didn't invent a person or a command, but it did overstate what the log shows, so read a confident cause as a best guess. Auto heal's rules don't rely on this: they still wouldn't run that container again.
- Both wrong fixes came from the same case, a crash with a clear signal, where it proposed no automatic fix instead of a restart.
- In an earlier round, three runs failed with a connection error, because the AI service stopped responding while the computer was under heavy load.
- The cases are simple, and scoring only checks for key phrases.
- On a real stack, results were less consistent. In some runs, the second check rejected a correct cause, or the diagnosis was wrong and the second check caught it.
- Other models will behave differently.

To run the test yourself:

```bash
python -m digdown.evaluate --model qwen3:14b
```

## Known limits

- A container stopped while Digdown wasn't running can show up as a crash, because Docker's event log is short. Auto heal asks first in that case.
- A raised memory limit is lost when the container is recreated. Digdown tells you what to add to your Compose file.
- One copy of Digdown watches one machine.
- People have to build it themselves for now, as there's no ready-made image yet.
