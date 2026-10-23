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

from redash.query_runner import TYPE_BOOLEAN, TYPE_FLOAT, TYPE_INTEGER, TYPE_STRING

# Strict number grammars. Anything else ("007", "+5", " 12 ", "1_000", "-0",
# "nan", "inf", "1.", ".5") is text. Integers must round-trip exactly.
_INT_RE = re.compile(r"(0|-?[1-9][0-9]*)\Z")
_FLOAT_RE = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?\Z")
_SCALARS = (str, int, float, bool)
_CONTAINERS = (list, tuple, dict, set, frozenset)


def _cell(v):
    """Any value -> a JSON-safe cell: scalars stay, containers -> repr."""
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if isinstance(v, float) and not math.isfinite(v):
        return str(v)  # JSON has no nan/inf
    if v is None or isinstance(v, _SCALARS):
        return v
    return repr(v)


def _parse_number(s):
    """Strictly parse a string to int or finite float; None if it is text."""
    if _INT_RE.match(s):
        return int(s)
    m = _FLOAT_RE.match(s)
    if m and (m.group(2) or m.group(3)):
        f = float(s)
        if math.isfinite(f):
            return f
    return None


def _coerce_column(rows, name):
    """Convert a column of numeric strings, only if every non-null value parses."""
    idx = [i for i, r in enumerate(rows) if r[name] is not None]
    vals = [rows[i][name] for i in idx]
    if not vals or not all(isinstance(v, str) for v in vals):
        return
    parsed = [_parse_number(v) for v in vals]
    if any(p is None for p in parsed):
        return
    if any(isinstance(p, float) for p in parsed):
        parsed = [float(p) for p in parsed]  # int + float column -> all float
    for i, p in zip(idx, parsed):
        rows[i][name] = p


def _type_of(rows, name):
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


def _table(names, raw_rows):
    names = [str(n) for n in names]
    rows = [dict(zip(names, [_cell(c) for c in r])) for r in raw_rows]
    for n in names:
        _coerce_column(rows, n)
        if _type_of(rows, n) == TYPE_STRING:  # mixed column: uniform strings
            for r in rows:
                if r[n] is not None and not isinstance(r[n], str):
                    r[n] = str(r[n])
    cols = [{"name": n, "friendly_name": n, "type": _type_of(rows, n)} for n in names]
    return {"columns": cols, "rows": rows}


def _pairs(flat):
    return list(zip(flat[::2], flat[1::2]))


def _is_flat(reply):
    return isinstance(reply, list) and not any(isinstance(x, _CONTAINERS) for x in reply)


def _fields(f):
    """Stream entry fields: dict, or a flat [k, v, ...] list; None -> {}."""
    if f is None:
        return {}
    if isinstance(f, dict):
        return f
    return dict(_pairs(list(f)))


def shape(reply, args=()):
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
    if cmd in (["SCAN"], ["SSCAN"]) and isinstance(reply, (list, tuple)) and len(reply) == 2 \
            and isinstance(reply[1], (list, tuple)):
        return _table(["value"], [(k,) for k in reply[1]])

    # streams: [(id, {field: value})] -> id + a column per field (first-seen order)
    if cmd in (["XRANGE"], ["XREVRANGE"]) and isinstance(reply, (list, tuple)) \
            and all(isinstance(e, (list, tuple)) and len(e) == 2 for e in reply):
        entries = [(i, _fields(f)) for i, f in reply]
        fields = []
        for _, f in entries:
            fields += [k for k in f if k not in fields]
        names = {k: (f"{k}_1" if k == "id" else k) for k in fields}  # a field named "id" must not clash
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
