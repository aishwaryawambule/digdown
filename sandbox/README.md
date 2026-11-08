# Chaos sandbox

A deliberately-broken Docker Compose stack for demoing Digdown end-to-end. It is
a neutral demo — not anyone's real services — and each container fails in a way
that triggers a specific detector.

| Service | Breaks as | Detector |
|---|---|---|
| `crasher` | exits 1 on boot | crash |
| `flapper` | crashes every few seconds, `restart: always` | restart_loop |
| `hog` | allocates past a 48 MB limit | oom |
| `sicko` | healthcheck always fails (stays up) | unhealthy |
| `db` | dies on boot | crash (cascade root) |
| `api` | `depends_on: db` | correlated to `db`'s incident |
| `config` | crashes: `APP_API_KEY` unset | crash → **fixable** in execute mode (`set_env_var` + recreate) |

Most services above are deterministic failures (detection/diagnosis demos). `config`
is the one that genuinely **recovers** after a Digdown action, so it's the one to
use for an execute-mode auto-fix demo.

## Run

Follow the step-by-step guide in [practise with a broken stack](../docs/practice-with-the-sandbox.md).

## Reset

```bash
docker compose -f sandbox/docker-compose.yml down -v
```
