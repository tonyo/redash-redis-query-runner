# Redash Redis query runner

A [query runner](CONTEXT.md) that lets [Redash](https://redash.io) query a Redis instance. You write one Redis command per query and get a table back, so results work in tables, charts and dashboards.

It is built for **stock Redash v26.9.0** (the official image, no fork). The runner is plain Python that Redash loads through its `REDASH_ADDITIONAL_QUERY_RUNNERS` setting.

> **Status: proof of concept.** It runs any Redis command you send. Read [Security](#security) before pointing it at data you care about.

## Quick start

You need Docker with Compose, and [mise](https://mise.jdx.dev) for the helper tasks.

```sh
mise run setup          # creates .env with random local secrets (never overwrites)
docker compose up -d    # Redash, its Postgres and Redis, a seeded data Redis
mise run demo           # creates the data source, queries, a chart and a dashboard
```

Then open http://127.0.0.1:5000 and log in as `admin@example.com`. The password is the `ADMIN_PASSWORD` line in `.env`. The `demo` task prints the dashboard URL.

Without mise, run `sh scripts/setup-env.sh` instead of the first command.

The stack has two Redis instances. The **Internal Redis** is Redash's own job queue and cache. The **Data Redis** (`data-redis`, also on host port 6380) is the one you query. Don't confuse them.

## Add the data source yourself

1. Go to **Settings → Data Sources → New Data Source** and pick **Redis**.
2. Fill in the fields and press **Test Connection**. For the bundled data Redis use host `data-redis` and port `6379`.

| Field | Default | Notes |
| --- | --- | --- |
| Host | `localhost` | Required. |
| Port | `6379` | 1–65535. |
| Database index | `0` | Redis logical database. |
| Password | none | Stored as a Redash secret. |

A bad value (for example port `abc`) gives an error message. A blank value uses the default. There are fixed timeouts of 5 s to connect and 30 s per command.

## Writing queries

One Redis command per query, written as you would in `redis-cli`:

```
ZRANGE sales:revenue_by_region 0 -1 WITHSCORES
HGETALL product:1001
GET "my key with spaces"
```

See [docs/queries.md](docs/queries.md) for quoting rules and for exactly which table each command returns. Redash `{{ parameters }}` work in the query text.

## Use it in your own Redash

See [docs/install.md](docs/install.md). In short: mount the `runner/` folder into every Redash service, add it to `PYTHONPATH`, and set `REDASH_ADDITIONAL_QUERY_RUNNERS=redis_query_runner`.

## Security

- **There is no command filter.** Anyone who can use the data source can run any command, including `FLUSHALL`, `CONFIG SET` and `EVAL`. Redash re-runs saved and scheduled queries, so a write command in a saved query repeats on every refresh.
- **Use a least-privilege account.** Point the data source at a Redis user that can only read what is needed. The runner has no username field yet, so today that means a password-protected instance with the `default` user, or a separate read-only replica.
- **No TLS.** The connection is plain text. Keep it on a trusted network.
- The compose file is for **local development**. Its secrets are random per checkout, but the Data Redis and Internal Redis have no password and the Redash port is open on all interfaces.

## Develop

Everything goes through mise, which pins Python, ruff, ty and pre-commit.

| Command | What it does |
| --- | --- |
| `mise run test` | All tests, in a container made from the Redash image. |
| `mise run test:unit` / `test:integration` | Only unit tests, or only tests against the data Redis (DB 15, flushed). |
| `mise run check` | `ruff format --check`, `ruff check` and `ty check`. |
| `mise run format` | Format the code. |
| `mise run demo` | Rebuild the demo objects (safe to repeat). |

After you edit the runner, run `docker compose restart server worker scheduler`. No rebuild is needed.

Run `mise exec -- pre-commit install` once to get the fast checks on every commit.

Tests run in a container because the `redash` package can't be installed from PyPI.

## Layout

```
runner/redis_query_runner/   the runner package
  parser.py                  query text -> command arguments
  shape.py                   Redis reply -> table (columns, types, rows)
  runner.py                  the Redash query runner class
runner/tests/                unit and integration tests
seed/seed.redis              demo data for the Data Redis
demo/demo.py                 builds the demo through the Redash API
scripts/setup-env.sh         creates .env
docker-compose.yml           Redash v26.9.0 + data Redis (+ `test` profile)
```

`CLAUDE.md` holds the same project notes in a form for coding agents.
