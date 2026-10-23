"""Table-driven check of common Redis commands against the real data-redis (logical DB 15).

Each case is a query plus the columns (name, type) and rows the runner returns. `None` for rows
means "only the columns matter" (the data varies per run). Expected values were taken from real
replies, so a change in redis-py or Redis that alters a reply shape fails here.
"""

import json
import os

import pytest
from redash.utils import json_dumps
from redis_query_runner.runner import Redis

pytestmark = pytest.mark.integration

HOST = os.environ.get("DATA_REDIS_HOST", "data-redis")
PORT = int(os.environ.get("DATA_REDIS_PORT", "6379"))

STR, INT, FLOAT = "string", "integer", "float"

SEED = """
SET s hello
SET n 42
EXPIRE s 1000
RPUSH l a b c
SADD st x y z
ZADD z 1 a 2.5 b 3 c
HSET h f1 v1 f2 7
PFADD hl a b c
GEOADD g 13.36 38.11 Palermo 15.08 37.50 Catania
SETBIT bits 7 1
XADD x 1-1 a 1 b two
XADD x 2-1 a 3
XADD y 5-1 q 9
XGROUP CREATE x grp $
XGROUP CREATE x grp2 0
XGROUP CREATE y grp3 0
"""

VALUE = [("value", STR)]
MEMBER_SCORE = [("member", STR), ("score", FLOAT)]
GEO_FULL = [
    ("member", STR),
    ("distance", FLOAT),
    ("hash", INT),
    ("longitude", FLOAT),
    ("latitude", FLOAT),
]

# (query, columns, rows or None)
CASES = [
    # strings and counters
    ("GET s", VALUE, [{"value": "hello"}]),
    ("MGET s n nope", VALUE, [{"value": "hello"}, {"value": "42"}, {"value": None}]),
    ("STRLEN s", [("value", INT)], [{"value": 5}]),
    ("GETRANGE s 0 2", VALUE, [{"value": "hel"}]),
    ("TTL s", [("value", INT)], None),
    ("TYPE s", VALUE, [{"value": "string"}]),
    ("TYPE nope", VALUE, [{"value": "none"}]),
    ("EXISTS s n", [("value", INT)], [{"value": 2}]),
    ("OBJECT ENCODING s", VALUE, [{"value": "embstr"}]),
    ("MEMORY USAGE s", [("value", INT)], None),
    ("ECHO hi", VALUE, [{"value": "hi"}]),
    ("BITCOUNT bits", [("value", INT)], [{"value": 1}]),
    ("GETBIT bits 7", [("value", INT)], [{"value": 1}]),
    ("PFCOUNT hl", [("value", INT)], [{"value": 3}]),
    # keys
    ("KEYS bits", VALUE, [{"value": "bits"}]),
    ("DBSIZE", [("value", INT)], None),
    ("SCAN 0 MATCH n COUNT 100 TYPE string", VALUE, [{"value": "n"}]),
    # hashes
    ("HGET h f1", VALUE, [{"value": "v1"}]),
    ("HMGET h f1 f2 nope", VALUE, [{"value": "v1"}, {"value": "7"}, {"value": None}]),
    ("HKEYS h", VALUE, [{"value": "f1"}, {"value": "f2"}]),
    ("HVALS h", VALUE, [{"value": "v1"}, {"value": "7"}]),
    ("HLEN h", [("value", INT)], [{"value": 2}]),
    (
        "HSCAN h 0",
        [("field", STR), ("value", STR)],
        [
            {"field": "f1", "value": "v1"},
            {"field": "f2", "value": "7"},
        ],
    ),
    # lists
    ("LLEN l", [("value", INT)], [{"value": 3}]),
    ("LINDEX l 0", VALUE, [{"value": "a"}]),
    ("LPOS l b", [("value", INT)], [{"value": 1}]),
    (
        "LMPOP 1 l LEFT COUNT 2",
        [("key", STR), ("value", STR)],
        [
            {"key": "l", "value": "a"},
            {"key": "l", "value": "b"},
        ],
    ),
    # sets
    ("SCARD st", [("value", INT)], [{"value": 3}]),
    ("SINTER st st", VALUE, [{"value": "x"}, {"value": "y"}, {"value": "z"}]),
    ("SSCAN st 0", VALUE, [{"value": "x"}, {"value": "y"}, {"value": "z"}]),
    ("SMISMEMBER st x q", [("value", INT)], [{"value": 1}, {"value": 0}]),
    # sorted sets
    ("ZCARD z", [("value", INT)], [{"value": 3}]),
    ("ZCOUNT z 0 2", [("value", INT)], [{"value": 1}]),
    ("ZRANK z b", [("value", INT)], [{"value": 1}]),
    ("ZSCORE z a", [("value", FLOAT)], [{"value": 1.0}]),
    ("ZMSCORE z a b", [("value", FLOAT)], [{"value": 1.0}, {"value": 2.5}]),
    ("ZRANGE z 0 -1 REV", VALUE, [{"value": "c"}, {"value": "b"}, {"value": "a"}]),
    ("ZRANGEBYSCORE z 0 2", VALUE, [{"value": "a"}]),
    ("ZRANGEBYLEX z - +", VALUE, [{"value": "a"}, {"value": "b"}, {"value": "c"}]),
    (
        "ZREVRANGE z 0 -1 WITHSCORES",
        MEMBER_SCORE,
        [
            {"member": "c", "score": 3.0},
            {"member": "b", "score": 2.5},
            {"member": "a", "score": 1.0},
        ],
    ),
    (
        "ZRANGE z 0 -1 WITHSCORES REV",
        MEMBER_SCORE,
        [
            {"member": "c", "score": 3.0},
            {"member": "b", "score": 2.5},
            {"member": "a", "score": 1.0},
        ],
    ),
    (
        "ZSCAN z 0",
        MEMBER_SCORE,
        [
            {"member": "a", "score": 1.0},
            {"member": "b", "score": 2.5},
            {"member": "c", "score": 3.0},
        ],
    ),
    (
        "ZINTER 1 z WITHSCORES",
        MEMBER_SCORE,
        [
            {"member": "a", "score": 1.0},
            {"member": "b", "score": 2.5},
            {"member": "c", "score": 3.0},
        ],
    ),
    (
        "ZMPOP 1 z MIN",
        [("key", STR), ("member", STR), ("score", INT)],
        [
            {"key": "z", "member": "a", "score": 1},
        ],
    ),
    # geo
    (
        "GEOPOS g Palermo Nope",
        [("longitude", FLOAT), ("latitude", FLOAT)],
        [
            {"longitude": 13.36, "latitude": 38.11},
            {"longitude": None, "latitude": None},
        ],
    ),
    ("GEODIST g Palermo Catania km", [("value", FLOAT)], [{"value": 165.679}]),
    ("GEOHASH g Palermo", VALUE, [{"value": "sqc8b18qyd0"}]),
    (
        "GEOSEARCH g FROMLONLAT 15 37 BYRADIUS 200 km WITHDIST WITHHASH WITHCOORD ASC",
        GEO_FULL,
        [
            {
                "member": "Catania",
                "distance": 56.0623,
                "hash": 3479447184714206,
                "longitude": 15.08,
                "latitude": 37.5,
            },
            {
                "member": "Palermo",
                "distance": 190.1377,
                "hash": 3479099943497504,
                "longitude": 13.36,
                "latitude": 38.11,
            },
        ],
    ),
    # streams
    ("XLEN x", [("value", INT)], [{"value": 2}]),
    (
        "XRANGE x - +",
        [("id", STR), ("a", INT), ("b", STR)],
        [
            {"id": "1-1", "a": 1, "b": "two"},
            {"id": "2-1", "a": 3, "b": None},
        ],
    ),
    ("XREVRANGE x + - COUNT 1", [("id", STR), ("a", INT)], [{"id": "2-1", "a": 3}]),
    (
        "XREAD COUNT 5 STREAMS x y 0 0",
        [("stream", STR), ("id", STR), ("a", INT), ("b", STR), ("q", INT)],
        [
            {"stream": "x", "id": "1-1", "a": 1, "b": "two", "q": None},
            {"stream": "x", "id": "2-1", "a": 3, "b": None, "q": None},
            {"stream": "y", "id": "5-1", "a": None, "b": None, "q": 9},
        ],
    ),
    (
        "XREADGROUP GROUP grp3 c1 STREAMS y >",
        [("stream", STR), ("id", STR), ("q", INT)],
        [
            {"stream": "y", "id": "5-1", "q": 9},
        ],
    ),
    ("XINFO STREAM x", [("key", STR), ("value", STR)], None),
    ("XINFO GROUPS x", [("name", STR), ("consumers", INT), ("pending", INT)], None),
    # server and introspection
    ("INFO keyspace", [("name", STR), ("keys", INT), ("expires", INT)], None),
    ("INFO commandstats", [("name", STR), ("calls", INT)], None),
    ("MEMORY STATS", [("key", STR), ("value", STR)], None),
    ("CLIENT INFO", [("id", INT), ("addr", STR)], None),
    ("COMMAND COUNT", [("value", INT)], None),
    ("TIME", [("value", INT)], None),
    ("LASTSAVE", VALUE, None),
    ("ROLE", VALUE, None),
    ("ACL WHOAMI", VALUE, [{"value": "default"}]),
    ("PUBSUB CHANNELS", VALUE, []),
    ("OBJECT HELP", VALUE, None),
    ("EVAL 'return {1, 2, {3, 4}}' 0", VALUE, None),
]


@pytest.fixture
def seeded(real_redis):
    for line in SEED.strip().splitlines():
        real_redis.execute_command(*line.split())
    return real_redis


def run(query):
    data, err = Redis({"host": HOST, "port": PORT, "db": 15}).run_query(query, None)
    assert err is None, err
    json.loads(json_dumps(data))  # must serialise as Redash will
    return data


def same(actual, expected):
    if isinstance(expected, float):
        return actual == pytest.approx(expected, rel=1e-4)
    return actual == expected


@pytest.mark.parametrize(("query", "columns", "rows"), CASES, ids=[c[0] for c in CASES])
def test_command(seeded, query, columns, rows):
    data = run(query)
    got = [(c["name"], c["type"]) for c in data["columns"]]
    assert got[: len(columns)] == columns
    if rows is None:
        return
    assert len(data["rows"]) == len(rows)
    for actual, expected in zip(data["rows"], rows, strict=True):
        assert actual.keys() == expected.keys()
        assert all(same(actual[k], v) for k, v in expected.items()), (actual, expected)


def test_xinfo_stream_has_length(seeded):
    rows = {r["key"]: r["value"] for r in run("XINFO STREAM x")["rows"]}
    assert rows["length"] == "2"


def test_xinfo_groups_lists_each_group(seeded):
    rows = run("XINFO GROUPS x")["rows"]
    assert sorted(r["name"] for r in rows) == ["grp", "grp2"]


def test_xinfo_consumers(seeded):
    seeded.execute_command("XREADGROUP", "GROUP", "grp2", "c1", "STREAMS", "x", ">")
    rows = run("XINFO CONSUMERS x grp2")["rows"]
    assert [r["name"] for r in rows] == ["c1"]


def test_info_keyspace_row_for_db_15(seeded):
    rows = {r["name"]: r for r in run("INFO keyspace")["rows"]}
    assert rows["db15"]["keys"] >= 10


def test_client_list_has_this_connection(seeded):
    data = run("CLIENT LIST")
    assert {"id", "addr", "db"} <= {c["name"] for c in data["columns"]}
    assert any(r["db"] == 15 for r in data["rows"])


def test_command_stats_names_commands(seeded):
    seeded.get("s")
    names = [r["name"] for r in run("INFO commandstats")["rows"]]
    assert "cmdstat_get" in names


def test_lastsave_is_iso_text(seeded):
    value = run("LASTSAVE")["rows"][0]["value"]
    assert value[:2] == "20" and "T" in value


def test_slowlog_get(seeded):
    seeded.config_set("slowlog-log-slower-than", 0)
    try:
        seeded.get("s")
        data = run("SLOWLOG GET 5")
    finally:
        seeded.config_set("slowlog-log-slower-than", 10000)
    names = [c["name"] for c in data["columns"]]
    assert names == ["id", "start_time", "duration_us", "command", "client_address", "client_name"]
    assert data["rows"]


def test_dump_reports_binary_reply(seeded):
    data, err = Redis({"host": HOST, "port": PORT, "db": 15}).run_query("DUMP s", None)
    assert data is None
    assert "binary" in err
