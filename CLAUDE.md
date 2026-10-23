## Project

A Redis query runner for stock Redash v26.9.0 (official image, no fork), loaded through `REDASH_ADDITIONAL_QUERY_RUNNERS`. Use "Data Redis" and "Internal Redis" as defined in `CONTEXT.md`; the Data Redis is the only query target.

- `runner/redis_query_runner/`: the package (`parser.py` splits query text, `shape.py` turns replies into tables, `runner.py` is the `Redis` runner class). It is bind-mounted read-only at `/extensions` with `PYTHONPATH=/extensions`.
- `runner/tests/`, `tests/Dockerfile`: tests. `seed/seed.redis`: demo data. `demo/demo.py`: builds the data source, queries, chart and dashboard through the Redash API.
- `.env` holds the admin password and Redash secrets. It is gitignored: never print or commit it.

## Commands

Everything goes through mise, which also pins the tools (Python, ruff, ty, pre-commit).

- `docker compose up -d`: Redash at http://127.0.0.1:5000 (admin `admin@example.com`, password in `.env`), data Redis at `data-redis:6379` (host port 6380), seeded by the one-shot `seed` service.
- After editing the runner: `docker compose restart server worker scheduler` (no rebuild).
- `mise run test` (or `test:unit`, `test:integration`): runs in a container made from the Redash image, because `redash` is not pip-installable. Integration tests use data Redis DB 15 and flush it.
- `mise run check`: `ruff format --check`, `ruff check` and `ty check`, on the host. `mise run format` fixes formatting.
- `mise run demo`: idempotent demo setup; prints the dashboard URL.
- Pre-commit runs the fast checks (not the docker tests). Install once with `mise exec -- pre-commit install`.

## Conventions

- Type hints are required on every function in `runner/redis_query_runner/` (ruff `ANN`); tests and `demo/` are exempt. Avoid `type: ignore` and per-file ignores there.
- Invalid data source config raises a config error; blank values fall back to defaults in code.
- `.scratch/` (wayfinder map, tickets, research) and `prototypes/` are gitignored. Verify Redash facts against the v26.9.0 tag, not `main`.

## Agent skills

### Issue tracker

Issues and specs live as local markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` plus `docs/adr/` at the repo root. See `docs/agents/domain.md`.
