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
| Username | none | Redis 6+ ACL user. Blank logs in as `default`. |
| Password | none | Stored as a Redash secret. |
| Connect timeout (seconds) | `5` | 1–30. A bad host fails after this long. |
| Request timeout (seconds) | `30` | 1–3600. Per command. |
| Use TLS | off | Turn on for servers with in-transit encryption. The certificate is verified against the system CAs. |
| Cluster mode | off | Turn on for Redis Cluster. Only database 0 exists. |

A bad value (for example port `abc`) gives an error message. A blank value uses the default.

## Amazon ElastiCache

- **In-transit encryption on:** tick **Use TLS**.
- **AUTH token:** put it in **Password** and leave **Username** blank. **RBAC user:** fill both.
- **Cluster mode enabled** (and Serverless): tick **Cluster mode** and use the *configuration endpoint* as host. Redash must be able to reach every node in the VPC.
- **Serverless:** it needs both **Use TLS** and **Cluster mode**.
- **Cluster mode disabled:** use the *primary endpoint*, with Cluster mode off.
- In cluster mode a command runs on the node that owns its key. Multi-key commands (`MGET a b`) need all keys in one slot, for example `{user}:a` and `{user}:b`. Commands without a key (`DBSIZE`, `SCAN`, `INFO`) run on one node only.
- **Valkey** 8 and 9 work like Redis, including ElastiCache for Valkey. Cluster mode only reaches database 0, even on Valkey 9 (redis-py's cluster client has no database option).
- ElastiCache blocks some commands (`CONFIG` and `DEBUG` among them). You get Redis's own error for those.

## Writing queries

One Redis command per query, written as you would in `redis-cli`:

```
ZRANGE sales:revenue_by_region 0 -1 WITHSCORES
HGETALL product:1001
GET "my key with spaces"
```

See [docs/queries.md](docs/queries.md) for quoting rules and for exactly which table each command returns. Redash `{{ parameters }}` work in the query text.

## Use it in your own Redash

See [docs/install.md](docs/install.md). In short: mount the `runner/` folder into every Redash service, add it to `PYTHONPATH`, and set `REDASH_ADDITIONAL_QUERY_RUNNERS=redis_query_runner`. To bake it into your own image without cloning this repo, use the [example Dockerfile](docs/examples/Dockerfile).

## Security

- **There is no command filter in the runner.** Anyone who can use the data source can run any command, including `FLUSHALL`, `CONFIG SET` and `EVAL`. Redash re-runs saved and scheduled queries, so a write command in a saved query repeats on every refresh.
- **Make a data source read-only with a Redis ACL user.** Redash groups only decide who may run queries (a "view only" group can't run new ones), not which commands run. Redis enforces commands server-side:

  ```
  ACL SETUSER readonly on >CHANGE-ME ~* +@read +ping +select +info +object +memory|usage +xinfo +slowlog|get +command +client|list
  ```

  Put that user in the **Username** and **Password** fields, and give the people who should only read the read-only data source. Keep a second data source with a write-capable login for the people who may write. `+@read` leaves out `INFO` and `CLIENT` (they are `@dangerous`), so those are added by name. `+select` is needed whenever the database index is not 0. Module commands (`JSON.*`, `TS.*`) are never in `+@read`: add them by name. The compose stack seeds a user `readonly` with password `readonly` and the demo adds a "(read-only)" data source for it.
- **TLS is optional and off by default.** With **Use TLS** off, the connection, including the password and every reply, is plain text: keep it on a trusted network. With it on, the server certificate is verified against the system CAs. There is no option for a private CA, a client certificate or skipping verification, so a server with a self-signed certificate can't be reached. TLS is covered by unit tests only, not against a live TLS server.
- The compose file is for **local development**. Its secrets are random per checkout, but the Data Redis and Internal Redis have no password, the Data Redis is published on `127.0.0.1:6380` only, and the Redash port (5000) is open on all interfaces.

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
  config.py                  data source settings and their validation
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

## License

[MIT](LICENSE). Unofficial community project: not affiliated with or endorsed by Redash or Redis.
