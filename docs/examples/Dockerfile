# syntax=docker/dockerfile:1
# Stock Redash plus the Redis query runner, fetched from GitHub at build time.
# Needs only this file: no local checkout of the extension. Requires BuildKit (default in current Docker).
#
#   docker build -t redash-redis:26.9.0 --build-arg REDIS_RUNNER_REF=<tag-or-sha> .

ARG REDASH_VERSION=26.9.0
# A tag or commit SHA of the extension. Pin one for reproducible builds; `main` moves.
ARG REDIS_RUNNER_REF=main
ARG REDIS_RUNNER_REPO=https://github.com/tonyo/redash-redis-query-runner.git

FROM scratch AS redis-extension
ARG REDIS_RUNNER_REPO
ARG REDIS_RUNNER_REF
ADD ${REDIS_RUNNER_REPO}#${REDIS_RUNNER_REF} /

FROM redash/redash:${REDASH_VERSION}
COPY --from=redis-extension /runner/redis_query_runner /extensions/redis_query_runner
COPY --from=redis-extension /assets/redis.png /app/client/dist/images/db-logos/redis.png
ENV PYTHONPATH=/extensions \
    REDASH_ADDITIONAL_QUERY_RUNNERS=redis_query_runner
