# Contributing

Bug reports and pull requests are welcome. For anything large, open an issue first so we can agree on the approach.

## Set up

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest

cd web && npm ci && npm test && npm run lint
```

`digdown demo` runs the real pipeline with simulated containers and a scripted AI, so you can try most changes without Docker or a model. Add `?mock` to the dashboard's address to see it with sample data and no backend.

To see real failures, start the deliberately broken stack in [`sandbox/`](sandbox/) and have Digdown watch only that stack.

## Docs

The guides are plain Markdown in `docs/`, and the website builds from them. To preview: `cd website && npm ci && npm run dev`. Link between pages as `alerts.md`, so links work on GitHub too.

## What we look for

- **A test with every fix or feature.** A bug fix should include a test that fails without it.
- **Plain language in the dashboard.** Name things by what people understand, and say what to do when something goes wrong.
- **No new write paths.** The AI agents only read. Only the Executor changes anything, only from the allow-list in `digdown/allowlist.py`, and only after someone approves. Changes to this boundary need extra care and a clear reason.
- **Be honest on screen.** The dashboard shouldn't claim more than Digdown knows. For example, it can't say "all good" if it can't reach Docker.
