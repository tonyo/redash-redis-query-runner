# Redash extensions

Out-of-tree extensions for Redash; currently a Redis query runner.

## Language

**Query runner**:
A Redash plugin class that turns a data source's query text into a result table.
_Avoid_: connector, integration, driver

**Data Redis**:
The Redis instance that users query through the Redis query runner.
_Avoid_: Redis (unqualified, when the internal Redis is also in play)

**Internal Redis**:
The Redis that Redash itself uses for its job queue and caching; never a query target.
_Avoid_: Redash Redis
