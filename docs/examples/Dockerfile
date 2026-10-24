# syntax=docker/dockerfile:1
# Stock Redash plus the Redis query runner, fetched from GitHub at build time.
# Needs only this file: no local checkout of the extension. Requires BuildKit (default in current Docker).
#
#   docker build -t my-redash:26.9.0 --build-arg EXTENSION_REF=<tag-or-sha> .
#
# Private repo: add  --secret id=GIT_AUTH_TOKEN,env=GH_TOKEN  (a token that can read the repo).

ARG REDASH_VERSION=26.9.0
# A tag or commit SHA of the extension. Pin one for reproducible builds; `main` moves.
ARG EXTENSION_REF=main
ARG EXTENSION_REPO=https://github.com/tonyo/redash-redis-query-runner.git

FROM scratch AS extension
ARG EXTENSION_REPO
ARG EXTENSION_REF
ADD ${EXTENSION_REPO}#${EXTENSION_REF} /

FROM redash/redash:${REDASH_VERSION}
COPY --from=extension /runner/redis_query_runner /extensions/redis_query_runner
COPY --from=extension /assets/redis.png /app/client/dist/images/db-logos/redis.png
ENV PYTHONPATH=/extensions \
    REDASH_ADDITIONAL_QUERY_RUNNERS=redis_query_runner
