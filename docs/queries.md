# Writing queries

## Query text

- A query is **one** Redis command, written like `redis-cli`: `GET mykey`.
- Empty text, more than one line, and unbalanced quotes are rejected with a clear error.
- Quote arguments with spaces: `GET "my key"`. Inside double quotes, `\"` and `\\` are escapes. Single quotes are literal.
- `;` and `#` are normal characters. They do not split commands or start comments.
- The command name is passed as you typed it. Redis decides whether it exists.
- Redash `{{ parameters }}` work: `GET {{ key }}`.

## What you get back

Every result is a table. A column is typed **integer**, **float**, **boolean** or **string**, so charts and sorting work. A text value is turned into a number only when every value in the column is a plain number (`42`, `-3`, `1250.5`, `1e3`). Values like `007`, `+5`, `1_000` and `nan` stay text. A column mixing integers and floats becomes float.

| Command | Columns | Rows |
| --- | --- | --- |
| `GET`, `INCR`, `TTL`, `TYPE` and other single values | `value` | one |
| `LRANGE`, `SMEMBERS`, `KEYS`, `MGET` and other lists | `value` | one per element |
| `ZRANGE … WITHSCORES`, `ZPOPMIN`, `ZPOPMAX` | `member`, `score` | one per member |
| `HGETALL` | one column per hash field | one |
| `INFO`, `CONFIG GET` | `key`, `value` | one per entry |
| `XRANGE`, `XREVRANGE` | `id` plus one column per field | one per entry |
| `SCAN`, `SSCAN` | `value` | one per key (the cursor is **not** returned yet) |
| `HSCAN` | `field`, `value` | one per field (cursor not returned) |
| `ZSCAN` | `member`, `score` | one per member (cursor not returned) |
| `XREAD`, `XREADGROUP` | `stream`, `id`, plus one column per field | one per entry |
| `XINFO STREAM`, `MEMORY STATS` | `key`, `value` | one per entry |
| `XINFO GROUPS`, `XINFO CONSUMERS` | one column per property | one per group or consumer |
| `GEOPOS` | `longitude`, `latitude` | one per member (empty for a missing member) |
| `GEOSEARCH`, `GEORADIUS*` with `WITHDIST` / `WITHHASH` / `WITHCOORD` | `member`, then `distance`, `hash`, `longitude`, `latitude` as requested | one per match |
| `LMPOP` | `key`, `value` | one per popped element |
| `ZMPOP` | `key`, `member`, `score` | one per popped member |
| `SLOWLOG GET` | `id`, `start_time`, `duration_us`, `command`, `client_address`, `client_name` | one per entry |
| `CLIENT LIST`, `CLIENT INFO` | one column per field (`id`, `addr`, `db`, …) | one per client |
| `INFO keyspace`, `INFO commandstats` | `name` plus one column per field | one per database or command |
| `PING`, `SET` and other status replies | `value` (`PONG` or `OK`) | one |
| nil (missing key) | `value` | one row with an empty value |

Notes:

- Sets are sorted so output is stable.
- A nested reply with no rule above (for example `EVAL` returning a list in a list) shows each inner element as text.
- An empty hash returns a table with `key` and `value` columns and no rows.
- A stream field named `id` becomes `id_1`.
- `LASTSAVE` shows an ISO date-time as text.
- Values that aren't valid UTF-8 make the query fail with an error that says so.

## Errors

Errors are shown in Redash as one line and never include the password:

- `Redis error: …` — Redis rejected the command (unknown command, wrong type, wrong arguments).
- `Could not connect to Redis at host:port: …`
- `Redis authentication failed at host:port: …`
- `Redis at host:port timed out: …` — a command ran longer than 30 s.
- A query-text error, for example `Query is empty` or `Unbalanced quotes in query`.

## Examples (from the bundled seed data)

```
ZRANGE sales:revenue_by_region 0 -1 WITHSCORES   -> bar chart of revenue by region
HGETALL product:1001                              -> name, stock, price as typed columns
LRANGE recent:orders 0 -1
XRANGE events - +                                 -> id, user, action, amount
INFO server
```
