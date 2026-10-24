# Install into an existing Redash

The runner is a Python package (`runner/redis_query_runner/`) with no dependencies of its own. Redash already ships `redis-py`. It was built and tested on Redash **v26.9.0** (Python 3.13, redis-py 4.6.0). Other versions may work but are untested.

## Steps

1. Copy the `runner/redis_query_runner/` folder to a place Redash can read, for example `/opt/redash-extensions/redis_query_runner/`.
2. Make that folder's **parent** importable and tell Redash to load the package. Do this on **every** Redash process that runs queries: `server`, `worker` and `scheduler`.

   ```yaml
   environment:
     REDASH_ADDITIONAL_QUERY_RUNNERS: redis_query_runner
     PYTHONPATH: /opt/redash-extensions
   volumes:
     - /opt/redash-extensions:/opt/redash-extensions:ro
   ```

   To load several extra runners, separate them with commas and **no spaces**.
3. Restart the Redash services. "Redis" now shows in **Settings → Data Sources → New Data Source**.

## Build your own image (no checkout needed)

[`docs/examples/Dockerfile`](examples/Dockerfile) builds stock Redash plus the runner and the Redis icon. BuildKit fetches the extension from GitHub, so the only file you need is that Dockerfile.

```sh
docker build -t redash-redis:26.9.0 --build-arg REDIS_RUNNER_REF=<tag-or-sha> .
```

- `REDIS_RUNNER_REF` is a tag, branch or commit SHA. Pin a tag or SHA: `main` moves, so rebuilds would differ.
- `REDASH_VERSION` picks the base image (default `26.9.0`, the tested version).
- Use the resulting image for every Redash service (`server`, `worker`, `scheduler`, and the one-off `create_db` job). The image sets `PYTHONPATH` and `REDASH_ADDITIONAL_QUERY_RUNNERS` itself, so no volume or extra env var is needed.

## Check it worked

- The `worker` log should show no import error for `redis_query_runner`.
- `GET /api/data_sources/types` (logged in) lists `Redis`.
- Create a data source and press **Test Connection**. It runs `PING` on the worker.

## Common problems

| Symptom | Likely cause |
| --- | --- |
| "Redis" is not in the list | The env var or `PYTHONPATH` is missing on `server`, or the folder is not mounted. |
| Test Connection fails but the list shows Redis | The `worker` is missing the env var or the mount. |
| `Could not connect to Redis at host:port` | Wrong host, or the Redash container can't reach it. Inside Docker, use the service name, not `localhost`. |
| `Redis authentication failed` | Wrong or missing username or password. |
