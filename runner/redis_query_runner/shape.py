"""Reply shaping: a Redis reply -> a Redash result dict.

`shape(reply, args)` is pure (no I/O). `reply` is what redis-py's
`execute_command` returned with `decode_responses=True` (some replies arrive
pre-shaped by redis-py callbacks as dict/tuple/set/bool, others flat), and
`args` are the command arguments (`("ZRANGE", "k", 0, -1, "WITHSCORES")`),
needed because a few replies can only be interpreted by command name.

Returns `{"columns": [{name, friendly_name, type}], "rows": [{name: value}]}`.
"""

import math
import re
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from redash.query_runner import TYPE_BOOLEAN, TYPE_FLOAT, TYPE_INTEGER, TYPE_STRING

# Strict number grammars. Anything else ("007", "+5", " 12 ", "1_000", "-0",
# "nan", "inf", "1.", ".5") is text. Integers must round-trip exactly.
_INT_RE = re.compile(r"(0|-?[1-9][0-9]*)\Z")
_FLOAT_RE = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?\Z")
_SCALARS = (str, int, float, bool)
_CONTAINERS = (list, tuple, dict, set, frozenset)
_GEO_SEARCH = {
    "GEOSEARCH",
    "GEORADIUS",
    "GEORADIUS_RO",
    "GEORADIUSBYMEMBER",
    "GEORADIUSBYMEMBER_RO",
}

Row = dict[str, Any]
Result = dict[str, Any]
# redis-py replies are untyped: str, int, float, bool, None, list, tuple, dict, set.
Reply = Any


def _cell(v: Reply) -> Reply:
    """Any value -> a JSON-safe cell: scalars stay, containers -> repr."""
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if isinstance(v, float) and not math.isfinite(v):
        return str(v)  # JSON has no nan/inf
    if isinstance(v, datetime):  # redis-py turns LASTSAVE into a datetime
        return v.isoformat()
    if v is None or isinstance(v, _SCALARS):
        return v
    return repr(v)


def _parse_number(s: str) -> int | float | None:
    """Strictly parse a string to int or finite float; None if it is text."""
    if _INT_RE.match(s):
        return int(s)
    m = _FLOAT_RE.match(s)
    if m and (m.group(2) or m.group(3)):
        f = float(s)
        if math.isfinite(f):
            return f
    return None


def _coerce_column(rows: list[Row], name: str) -> None:
    """Convert a column of numeric strings, only if every non-null value parses."""
    idx = [i for i, r in enumerate(rows) if r[name] is not None]
    vals = [rows[i][name] for i in idx]
    if not vals or not all(isinstance(v, str) for v in vals):
        return
    parsed: list[int | float] = []
    for v in vals:
        number = _parse_number(v)
        if number is None:
            return
        parsed.append(number)
    if any(isinstance(p, float) for p in parsed):
        parsed = [float(p) for p in parsed]  # int + float column -> all float
    for i, p in zip(idx, parsed, strict=True):
        rows[i][name] = p


def _type_of(rows: list[Row], name: str) -> str:
    vals = [r[name] for r in rows if r[name] is not None]
    if not vals:
        return TYPE_STRING
    if all(isinstance(v, bool) for v in vals):
        return TYPE_BOOLEAN
    if all(isinstance(v, int) and not isinstance(v, bool) for v in vals):
        return TYPE_INTEGER
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        return TYPE_FLOAT
    return TYPE_STRING


def _table(names: Iterable[Any], raw_rows: Iterable[Iterable[Any]]) -> Result:
    names = [str(n) for n in names]
    rows = [dict(zip(names, [_cell(c) for c in r], strict=False)) for r in raw_rows]
    for n in names:
        _coerce_column(rows, n)
        if _type_of(rows, n) == TYPE_STRING:  # mixed column: uniform strings
            for r in rows:
                if r[n] is not None and not isinstance(r[n], str):
                    r[n] = str(r[n])
    cols = [{"name": n, "friendly_name": n, "type": _type_of(rows, n)} for n in names]
    return {"columns": cols, "rows": rows}


def _pairs(flat: Sequence[Any]) -> list[tuple[Any, Any]]:
    return list(zip(flat[::2], flat[1::2], strict=False))


def _is_flat(reply: Reply) -> bool:
    return isinstance(reply, list) and not any(isinstance(x, _CONTAINERS) for x in reply)


def _fields(f: Reply) -> dict[Any, Any]:
    """Stream entry fields: dict, or a flat [k, v, ...] list; None -> {}."""
    if f is None:
        return {}
    if isinstance(f, dict):
        return f
    return dict(_pairs(list(f)))


def _is_pair(x: Reply) -> bool:
    return isinstance(x, (list, tuple)) and len(x) == 2


def _union_table(first: str | None, entries: Iterable[tuple[Any, dict[Any, Any]]]) -> Result:
    """(label, {field: value}) entries -> optional label column + a column per field."""
    entries = list(entries)
    fields: list[Any] = []
    for _, f in entries:
        fields += [k for k in f if k not in fields]
    names = [first] if first else []
    names += [f"{k}_1" if k == first else str(k) for k in fields]
    rows = [([lbl] if first else []) + [f.get(k) for k in fields] for lbl, f in entries]
    return _table(names, rows)


def _geo_table(reply: Reply, words: list[str]) -> Result | None:
    """GEOSEARCH / GEORADIUS with WITH* flags: [member, dist?, hash?, [lon, lat]?] per item."""
    names = ["member"]
    if "WITHDIST" in words:
        names.append("distance")
    if "WITHHASH" in words:
        names.append("hash")
    if "WITHCOORD" in words:
        names += ["longitude", "latitude"]
    if len(names) == 1 or not isinstance(reply, list):
        return None
    per_item = 1 + sum(w in words for w in ("WITHDIST", "WITHHASH", "WITHCOORD"))
    if not all(isinstance(x, (list, tuple)) and len(x) == per_item for x in reply):
        return None
    rows = []
    for item in reply:
        row: list[Any] = []
        for cell in item:
            row += list(cell) if isinstance(cell, (list, tuple)) else [cell]
        rows.append(row)
    return _table(names, rows)


def _client_list_table(reply: str) -> Result:
    """CLIENT LIST / CLIENT INFO text: one line per client, space-separated key=value."""
    entries = []
    for line in reply.splitlines():
        if line.strip():
            entries.append((None, dict(p.partition("=")[::2] for p in line.split())))
    return _union_table(None, entries)


def shape(reply: Reply, args: Sequence[Any] = ()) -> Result:
    words = [str(a).upper() for a in args]
    cmd = words[:1]
    cmd2 = words[:2]

    # ZRANGE-family WITHSCORES: flat [m, s, m, s] or [(m, s), ...] -> member/score
    zscored = bool(cmd) and cmd[0].startswith("Z") and "WITHSCORES" in words
    zpop = cmd in (["ZPOPMIN"], ["ZPOPMAX"])
    if zscored or zpop:
        if _is_flat(reply) and len(reply) % 2 == 0:
            return _table(["member", "score"], _pairs(reply))
        if isinstance(reply, (list, tuple)) and all(
            isinstance(x, (list, tuple)) and len(x) == 2 for x in reply
        ):
            return _table(["member", "score"], [tuple(x) for x in reply])

    # CONFIG GET as a flat list (redis-py usually hands back a dict)
    if cmd2 == ["CONFIG", "GET"] and _is_flat(reply) and len(reply) % 2 == 0:
        return _table(["key", "value"], _pairs(reply))

    # SCAN / SSCAN: (cursor, [keys]) -> keys as rows, cursor dropped
    if (
        cmd in (["SCAN"], ["SSCAN"])
        and isinstance(reply, (list, tuple))
        and len(reply) == 2
        and isinstance(reply[1], (list, tuple))
    ):
        return _table(["value"], [(k,) for k in reply[1]])

    # streams: [(id, {field: value})] -> id + a column per field (first-seen order)
    if (
        cmd in (["XRANGE"], ["XREVRANGE"])
        and isinstance(reply, (list, tuple))
        and all(isinstance(e, (list, tuple)) and len(e) == 2 for e in reply)
    ):
        entries = [(i, _fields(f)) for i, f in reply]
        fields: list[Any] = []
        for _, f in entries:
            fields += [k for k in f if k not in fields]
        names = {
            k: (f"{k}_1" if k == "id" else k) for k in fields
        }  # a field named "id" must not clash
        return _table(
            ["id"] + [names[k] for k in fields],
            [(i, *[f.get(k) for k in fields]) for i, f in entries],
        )

    # HGETALL: one row, a typed column per field. Empty hash falls through.
    if cmd == ["HGETALL"]:
        if _is_flat(reply) and reply and len(reply) % 2 == 0:
            reply = dict(_pairs(reply))  # duplicate fields: last wins
        if isinstance(reply, dict) and reply:
            return _table(list(reply), [tuple(reply.values())])

    # HSCAN / ZSCAN: (cursor, {field: value}) / (cursor, [(member, score)]), cursor dropped
    if cmd in (["HSCAN"], ["ZSCAN"]) and _is_pair(reply):
        body = reply[1]
        if cmd == ["HSCAN"] and isinstance(body, dict):
            return _table(["field", "value"], list(body.items()))
        if cmd == ["HSCAN"] and _is_flat(body) and len(body) % 2 == 0:
            return _table(["field", "value"], _pairs(body))
        if cmd == ["ZSCAN"] and isinstance(body, list) and all(_is_pair(x) for x in body):
            return _table(["member", "score"], [tuple(x) for x in body])

    # XREAD / XREADGROUP: [[stream, [(id, fields)]]] -> stream, id + a column per field
    if (
        cmd in (["XREAD"], ["XREADGROUP"])
        and isinstance(reply, list)
        and reply
        and all(_is_pair(x) and isinstance(x[1], (list, tuple)) for x in reply)
    ):
        return _union_table(
            "stream", [(st, {"id": i, **_fields(f)}) for st, es in reply for i, f in es]
        )

    # XINFO STREAM and MEMORY STATS: flat [key, value, ...]
    if (
        cmd2 in (["XINFO", "STREAM"], ["MEMORY", "STATS"])
        and isinstance(reply, list)
        and len(reply) % 2 == 0
    ):
        return _table(["key", "value"], _pairs(reply))

    # XINFO GROUPS / CONSUMERS: a list of flat [key, value, ...] lists -> a row each
    if (
        cmd2 in (["XINFO", "GROUPS"], ["XINFO", "CONSUMERS"])
        and isinstance(reply, list)
        and reply
        and all(isinstance(x, list) and len(x) % 2 == 0 for x in reply)
    ):
        return _union_table(None, [(None, dict(_pairs(x))) for x in reply])

    # GEOPOS: [(lon, lat) | None, ...]
    if (
        cmd == ["GEOPOS"]
        and isinstance(reply, list)
        and all(x is None or _is_pair(x) for x in reply)
    ):
        return _table(["longitude", "latitude"], [x or (None, None) for x in reply])

    # GEOSEARCH / GEORADIUS with WITH* flags
    if cmd and cmd[0] in _GEO_SEARCH and (geo := _geo_table(reply, words)) is not None:
        return geo

    # LMPOP / ZMPOP: [key, [elements]] ; nil when nothing was popped
    if cmd in (["LMPOP"], ["BLMPOP"]) and _is_pair(reply) and isinstance(reply[1], list):
        return _table(["key", "value"], [(reply[0], x) for x in reply[1]])
    if (
        cmd in (["ZMPOP"], ["BZMPOP"])
        and _is_pair(reply)
        and isinstance(reply[1], list)
        and all(_is_pair(x) for x in reply[1])
    ):
        return _table(["key", "member", "score"], [(reply[0], *x) for x in reply[1]])

    # SLOWLOG GET: [[id, start_time, duration_us, [command...], client_address, client_name], ...]
    if (
        cmd2 == ["SLOWLOG", "GET"]
        and isinstance(reply, list)
        and reply
        and all(isinstance(x, list) and len(x) >= 6 for x in reply)
    ):
        names = ["id", "start_time", "duration_us", "command", "client_address", "client_name"]
        return _table(names, [(*x[:3], " ".join(map(str, x[3])), *x[4:6]) for x in reply])

    # CLIENT LIST / CLIENT INFO: one text blob -> a row per client
    if cmd2 in (["CLIENT", "LIST"], ["CLIENT", "INFO"]) and isinstance(reply, str):
        return _client_list_table(reply)

    # INFO keyspace / commandstats and the like: {name: {field: value}} -> name + a column per field
    if isinstance(reply, dict) and reply and all(isinstance(v, dict) for v in reply.values()):
        return _union_table("name", [(k, v) for k, v in reply.items()])

    # status replies: redis-py turns +PONG / +OK into True
    if reply is True:
        return _table(["value"], [("PONG" if cmd == ["PING"] else "OK",)])

    if isinstance(reply, (set, frozenset)):
        try:
            reply = sorted(reply)
        except TypeError:  # mixed types
            reply = sorted(reply, key=repr)

    # dict (INFO, CONFIG GET, anything else) -> key/value rows
    if isinstance(reply, dict):
        return _table(["key", "value"], list(reply.items()))

    # list / tuple: one `value` column; nested elements become reprs via _cell
    if isinstance(reply, (list, tuple)):
        return _table(["value"], [(x,) for x in reply])

    # scalar / nil
    return _table(["value"], [(reply,)])
