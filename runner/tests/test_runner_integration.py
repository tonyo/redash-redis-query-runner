"""Integration tests: the runner against the real data-redis (logical DB 15)."""

import json
import os

import pytest
from redash.utils import json_dumps
from redis_query_runner.runner import Redis

pytestmark = pytest.mark.integration

HOST = os.environ.get("DATA_REDIS_HOST", "data-redis")
PORT = int(os.environ.get("DATA_REDIS_PORT", "6379"))


@pytest.fixture
def run(real_redis):
    cfg = {"host": HOST, "port": PORT, "db": 15}

    def _run(query, **overrides):
        return Redis({**cfg, **overrides}).run_query(query, None)

    return _run


def ok(result):
    data, err = result
    assert err is None, err
    json.loads(json_dumps(data))
    return data


def err_of(result):
    data, err = result
    assert data is None
    assert err
    return err


def test_set_get(run):
    assert ok(run("SET k hello"))["rows"] == [{"value": "OK"}]
    assert ok(run("GET k"))["rows"] == [{"value": "hello"}]


def test_get_nil(run):
    data = ok(run("GET missing"))
    assert data["rows"] == [{"value": None}]


def test_quoted_and_unicode(run):
    ok(run('SET "my key" "a b ☃"'))
    assert ok(run('GET "my key"'))["rows"] == [{"value": "a b ☃"}]


def test_lowercase_command(run):
    ok(run("set k 1"))
    assert ok(run("get k"))["rows"] == [{"value": 1}]


def test_hset_hgetall(run):
    assert ok(run("HSET h name bob age 30 score 1.5"))["rows"] == [{"value": 3}]
    data = ok(run("HGETALL h"))
    assert data["rows"] == [{"name": "bob", "age": 30, "score": 1.5}]
    assert [c["name"] for c in data["columns"]] == ["name", "age", "score"]


def test_hgetall_missing(run):
    assert ok(run("HGETALL nothere"))["rows"] == []


def test_zadd_zrange_withscores(run):
    ok(run("ZADD z 1 a 2.5 b"))
    data = ok(run("ZRANGE z 0 -1 WITHSCORES"))
    assert data["rows"] == [{"member": "a", "score": 1.0}, {"member": "b", "score": 2.5}]


def test_zrange_without_scores(run):
    ok(run("ZADD z 1 a 2 b"))
    assert ok(run("ZRANGE z 0 -1"))["rows"] == [{"value": "a"}, {"value": "b"}]


def test_lrange(run):
    ok(run("RPUSH l a b 3"))
    data = ok(run("LRANGE l 0 -1"))
    assert [r["value"] for r in data["rows"]] == ["a", "b", "3"]


def test_lrange_empty(run):
    assert ok(run("LRANGE nolist 0 -1"))["rows"] == []


def test_smembers(run):
    ok(run("SADD s c a b"))
    assert [r["value"] for r in ok(run("SMEMBERS s"))["rows"]] == ["a", "b", "c"]


def test_xadd_xrange(run):
    ok(run("XADD st 1-1 temp 20 city oslo"))
    ok(run("XADD st 2-1 temp 21"))
    data = ok(run("XRANGE st - +"))
    assert [c["name"] for c in data["columns"]] == ["id", "temp", "city"]
    assert data["rows"] == [
        {"id": "1-1", "temp": 20, "city": "oslo"},
        {"id": "2-1", "temp": 21, "city": None},
    ]


def test_scan(run):
    for k in ("a1", "a2", "a3"):
        ok(run(f"SET {k} x"))
    data = ok(run("SCAN 0 MATCH a* COUNT 100"))
    assert sorted(r["value"] for r in data["rows"]) == ["a1", "a2", "a3"]


def test_info(run):
    data = ok(run("INFO"))
    assert [c["name"] for c in data["columns"]] == ["key", "value"]
    assert any(r["key"] == "redis_version" for r in data["rows"])


def test_info_section(run):
    data = ok(run("INFO server"))
    assert any(r["key"] == "redis_version" for r in data["rows"])


def test_config_get(run):
    data = ok(run("CONFIG GET maxmemory"))
    assert [c["name"] for c in data["columns"]] == ["key", "value"]
    assert data["rows"][0]["key"] == "maxmemory"


def test_ping(run):
    assert ok(run("PING"))["rows"] == [{"value": "PONG"}]


def test_incr_and_exists(run):
    assert ok(run("INCR n"))["rows"] == [{"value": 1}]
    assert ok(run("EXISTS n"))["rows"] == [{"value": 1}]


def test_dbsize_is_isolated_to_db_15(run):
    ok(run("SET only-here 1"))
    assert ok(run("DBSIZE"))["rows"] == [{"value": 1}]


def test_wrong_arity(run):
    assert "wrong number of arguments" in err_of(run("GET"))
    assert "wrong number of arguments" in err_of(run("GET a b"))


def test_unknown_command(run):
    assert "unknown command" in err_of(run("FOOBAR x")).lower()


def test_wrongtype(run):
    ok(run("SET k v"))
    assert "WRONGTYPE" in err_of(run("LRANGE k 0 -1"))


def test_not_an_integer(run):
    ok(run("SET k abc"))
    assert "integer" in err_of(run("INCR k"))


def test_empty_and_multiline(run):
    assert "empty" in err_of(run("")).lower()
    assert "lines" in err_of(run("SET a 1\nSET b 2"))
    assert ok(run("GET a"))["rows"] == [{"value": None}]  # nothing ran


def test_unbalanced_quote(run):
    assert "quote" in err_of(run('GET "x')).lower()


def test_binary_reply_is_an_error_not_a_crash(run, real_redis):
    real_redis.execute_command("SET", "bin", b"\xff\xfe")
    assert "UTF-8" in err_of(run("GET bin"))


def test_connection_refused_bad_port(run):
    err = err_of(run("GET k", port=1))
    assert "Could not connect" in err and f"{HOST}:1" in err


def test_connection_unknown_host(run):
    assert "Could not connect" in err_of(run("GET k", host="no-such-host.invalid"))


def test_wrong_password(run):
    # data-redis has no password configured, so AUTH is rejected by the server
    err = err_of(run("GET k", password="wrong-password"))
    assert "wrong-password" not in err
    assert "auth" in err.lower() or "password" in err.lower()


def test_db_index_out_of_range(run):
    err = err_of(run("GET k", db=9999))
    assert "invalid" in err.lower() or "out of range" in err.lower()


def test_blank_config_values_fall_back_to_defaults(run):
    # blank port/db -> 6379/0 on host data-redis; DB 0 holds seed data, so only read
    data = ok(run("PING", port="", db=""))
    assert data["rows"] == [{"value": "PONG"}]


def test_test_connection_ok():
    Redis({"host": HOST, "port": PORT}).test_connection()


def test_test_connection_refused():
    with pytest.raises(Exception, match="Could not connect"):
        Redis({"host": HOST, "port": 1}).test_connection()


def test_test_connection_wrong_password():
    with pytest.raises(Exception, match=r"(?i)auth|password"):
        Redis({"host": HOST, "port": PORT, "password": "nope"}).test_connection()
