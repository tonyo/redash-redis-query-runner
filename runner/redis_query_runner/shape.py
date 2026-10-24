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
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from typing import Any

from redash.query_runner import TYPE_BOOLEAN, TYPE_FLOAT, TYPE_INTEGER, TYPE_STRING

# Strict number grammars. Anything else ("007", "+5", " 12 ", "1_000", "-0",
# "nan", "inf", "1.", ".5") is text. Integers must round-trip exactly.
_INT_RE = re.compile(r"(0|-?[1-9][0-9]*)\Z")
_FLOAT_RE = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?\Z")
_SCALARS = (str, int, float, bool)
_CONTAINERS = (list, tuple, dict, set, frozenset)

Row = dict[str, Any]
Result = dict[str, Any]
# redis-py replies are untyped: str, int, float, bool, None, list, tuple, dict, set.
Reply = Any
Shaper = Callable[[Reply, Sequence[Any]], Result | None]


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


def _upper(args: Sequence[Any]) -> list[str]:
    return [str(a).upper() for a in args]


def _is_pairs(x: Reply) -> bool:
    return isinstance(x, (list, tuple)) and all(_is_pair(e) for e in x)


# -- shapers: one per command (or command family). Each returns None when the reply does not have
# the expected form, and shape() then falls back to the generic rendering.


def _zscores(reply: Reply, args: Sequence[Any]) -> Result | None:
    """ZRANGE-family WITHSCORES and ZPOPMIN/MAX: flat [m, s, m, s] or [(m, s), ...]."""
    if _is_flat(reply) and len(reply) % 2 == 0:
        return _table(["member", "score"], _pairs(reply))
    if _is_pairs(reply):
        return _table(["member", "score"], [tuple(x) for x in reply])
    return None


def _flat_key_value(reply: Reply, args: Sequence[Any]) -> Result | None:
    """CONFIG GET as a flat list (redis-py usually hands back a dict)."""
    if _is_flat(reply) and len(reply) % 2 == 0:
        return _table(["key", "value"], _pairs(reply))
    return None


def _key_value_list(reply: Reply, args: Sequence[Any]) -> Result | None:
    """XINFO STREAM and MEMORY STATS: [key, value, ...], values may be nested."""
    if isinstance(reply, list) and len(reply) % 2 == 0:
        return _table(["key", "value"], _pairs(reply))
    return None


def _scan_keys(reply: Reply, args: Sequence[Any]) -> Result | None:
    """SCAN / SSCAN: (cursor, [keys]) -> keys as rows, cursor dropped."""
    if _is_pair(reply) and isinstance(reply[1], (list, tuple)):
        return _table(["value"], [(k,) for k in reply[1]])
    return None


def _stream_range(reply: Reply, args: Sequence[Any]) -> Result | None:
    """XRANGE / XREVRANGE: [(id, {field: value})] -> id + a column per field."""
    if isinstance(reply, (list, tuple)) and all(_is_pair(e) for e in reply):
        return _union_table("id", [(i, _fields(f)) for i, f in reply])
    return None


def _hgetall(reply: Reply, args: Sequence[Any]) -> Result | None:
    """One row, a typed column per field. An empty hash falls through."""
    if _is_flat(reply) and reply and len(reply) % 2 == 0:
        reply = dict(_pairs(reply))  # duplicate fields: last wins
    if isinstance(reply, dict) and reply:
        return _table(list(reply), [tuple(reply.values())])
    return None


def _hscan(reply: Reply, args: Sequence[Any]) -> Result | None:
    """(cursor, {field: value}), cursor dropped."""
    if not _is_pair(reply):
        return None
    body = reply[1]
    if isinstance(body, dict):
        return _table(["field", "value"], list(body.items()))
    if _is_flat(body) and len(body) % 2 == 0:
        return _table(["field", "value"], _pairs(body))
    return None


def _zscan(reply: Reply, args: Sequence[Any]) -> Result | None:
    """(cursor, [(member, score)]), cursor dropped."""
    if _is_pair(reply) and isinstance(reply[1], list) and all(_is_pair(x) for x in reply[1]):
        return _table(["member", "score"], [tuple(x) for x in reply[1]])
    return None


def _xread(reply: Reply, args: Sequence[Any]) -> Result | None:
    """XREAD / XREADGROUP: [[stream, [(id, fields)]]] -> stream, id + a column per field."""
    if (
        isinstance(reply, list)
        and reply
        and all(_is_pair(x) and isinstance(x[1], (list, tuple)) for x in reply)
    ):
        return _union_table(
            "stream", [(st, {"id": i, **_fields(f)}) for st, es in reply for i, f in es]
        )
    return None


def _xinfo_rows(reply: Reply, args: Sequence[Any]) -> Result | None:
    """XINFO GROUPS / CONSUMERS: a list of flat [key, value, ...] lists -> a row each."""
    if (
        isinstance(reply, list)
        and reply
        and all(isinstance(x, list) and len(x) % 2 == 0 for x in reply)
    ):
        return _union_table(None, [(None, dict(_pairs(x))) for x in reply])
    return None


def _geopos(reply: Reply, args: Sequence[Any]) -> Result | None:
    """[(lon, lat) | None, ...]"""
    if isinstance(reply, list) and all(x is None or _is_pair(x) for x in reply):
        return _table(["longitude", "latitude"], [x or (None, None) for x in reply])
    return None


def _hash_fields(args: Sequence[Any]) -> list[str] | None:
    """The field names of ``... FIELDS n f1 .. fn`` (the hash field-expiration commands)."""
    upper = _upper(args)
    if "FIELDS" not in upper[2:]:
        return None
    at = upper.index("FIELDS", 2)
    try:
        count = int(args[at + 1])
    except (IndexError, ValueError):
        return None
    names = [str(a) for a in args[at + 2 : at + 2 + count]]
    return names if len(names) == count else None


def _per_field(column: str) -> Shaper:
    """A shaper for a command that answers one value per requested field, in order."""

    def shaper(reply: Reply, args: Sequence[Any]) -> Result | None:
        names = _hash_fields(args)
        if names is None or not isinstance(reply, list) or len(reply) != len(names):
            return None
        return _table(["field", column], zip(names, reply, strict=True))

    return shaper


def _geo_search(reply: Reply, args: Sequence[Any]) -> Result | None:
    """GEOSEARCH / GEORADIUS with WITH* flags: [member, dist?, hash?, [lon, lat]?] per item."""
    flags = set(_upper(args))
    names = ["member"]
    if "WITHDIST" in flags:
        names.append("distance")
    if "WITHHASH" in flags:
        names.append("hash")
    if "WITHCOORD" in flags:
        names += ["longitude", "latitude"]
    if len(names) == 1 or not isinstance(reply, list):
        return None
    per_item = 1 + sum(w in flags for w in ("WITHDIST", "WITHHASH", "WITHCOORD"))
    if not all(isinstance(x, (list, tuple)) and len(x) == per_item for x in reply):
        return None
    rows = []
    for item in reply:
        row: list[Any] = []
        for cell in item:
            row += list(cell) if isinstance(cell, (list, tuple)) else [cell]
        rows.append(row)
    return _table(names, rows)


def _lmpop(reply: Reply, args: Sequence[Any]) -> Result | None:
    """LMPOP / BLMPOP: [key, [elements]]; nil when nothing was popped."""
    if _is_pair(reply) and isinstance(reply[1], list):
        return _table(["key", "value"], [(reply[0], x) for x in reply[1]])
    return None


def _zmpop(reply: Reply, args: Sequence[Any]) -> Result | None:
    """ZMPOP / BZMPOP: [key, [(member, score)]]; nil when nothing was popped."""
    if _is_pair(reply) and isinstance(reply[1], list) and all(_is_pair(x) for x in reply[1]):
        return _table(["key", "member", "score"], [(reply[0], *x) for x in reply[1]])
    return None


def _slowlog(reply: Reply, args: Sequence[Any]) -> Result | None:
    """SLOWLOG GET: [[id, start_time, duration_us, [command...], client_address, client_name]]."""
    if (
        reply
        and isinstance(reply, list)
        and all(isinstance(x, list) and len(x) >= 6 for x in reply)
    ):
        names = ["id", "start_time", "duration_us", "command", "client_address", "client_name"]
        return _table(names, [(*x[:3], " ".join(map(str, x[3])), *x[4:6]) for x in reply])
    return None


def _client_list(reply: Reply, args: Sequence[Any]) -> Result | None:
    """CLIENT LIST / CLIENT INFO: one text blob, a line per client of space-separated key=value."""
    if not isinstance(reply, str):
        return None
    entries = [
        (None, dict(p.partition("=")[::2] for p in line.split()))
        for line in reply.splitlines()
        if line.strip()
    ]
    return _union_table(None, entries)


# Keyed by the leading words of the command, upper-cased. A two-word key wins over a one-word one.
_SHAPERS: dict[tuple[str, ...], Shaper] = {
    ("ZPOPMIN",): _zscores,
    ("ZPOPMAX",): _zscores,
    ("CONFIG", "GET"): _flat_key_value,
    ("MEMORY", "STATS"): _key_value_list,
    ("XINFO", "STREAM"): _key_value_list,
    ("XINFO", "GROUPS"): _xinfo_rows,
    ("XINFO", "CONSUMERS"): _xinfo_rows,
    ("SCAN",): _scan_keys,
    ("SSCAN",): _scan_keys,
    ("XRANGE",): _stream_range,
    ("XREVRANGE",): _stream_range,
    ("HGETALL",): _hgetall,
    ("HSCAN",): _hscan,
    ("ZSCAN",): _zscan,
    ("XREAD",): _xread,
    ("XREADGROUP",): _xread,
    ("HGETEX",): _per_field("value"),
    ("HTTL",): _per_field("ttl"),
    ("HPTTL",): _per_field("ttl"),
    ("HEXPIRETIME",): _per_field("expires_at"),
    ("HPEXPIRETIME",): _per_field("expires_at"),
    ("GEOPOS",): _geopos,
    ("GEOSEARCH",): _geo_search,
    ("GEORADIUS",): _geo_search,
    ("GEORADIUS_RO",): _geo_search,
    ("GEORADIUSBYMEMBER",): _geo_search,
    ("GEORADIUSBYMEMBER_RO",): _geo_search,
    ("LMPOP",): _lmpop,
    ("BLMPOP",): _lmpop,
    ("ZMPOP",): _zmpop,
    ("BZMPOP",): _zmpop,
    ("SLOWLOG", "GET"): _slowlog,
    ("CLIENT", "LIST"): _client_list,
    ("CLIENT", "INFO"): _client_list,
}


def _command_shapers(args: Sequence[Any]) -> list[Shaper]:
    """The shapers to try for this command, in order."""
    leading = tuple(_upper(args[:2]))
    found: list[Shaper] = []
    if leading[:1] and leading[0].startswith("Z") and "WITHSCORES" in _upper(args):
        found.append(_zscores)
    for key in (leading, leading[:1]):
        if key in _SHAPERS:
            found.append(_SHAPERS[key])
    return found


def shape(reply: Reply, args: Sequence[Any] = ()) -> Result:
    for shaper in _command_shapers(args):
        if (result := shaper(reply, args)) is not None:
            return result

    # INFO keyspace / commandstats and the like: {name: {field: value}} -> name + a column per field
    if isinstance(reply, dict) and reply and all(isinstance(v, dict) for v in reply.values()):
        return _union_table("name", [(k, v) for k, v in reply.items()])

    # status replies: redis-py turns +PONG / +OK into True
    if reply is True:
        is_ping = bool(args) and str(args[0]).upper() == "PING"
        return _table(["value"], [("PONG" if is_ping else "OK",)])

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
