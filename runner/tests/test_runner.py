"""Unit tests for the Redis runner: a fake client against the real BaseQueryRunner."""

import json

import pytest
import redis
import redis_query_runner
from redash.query_runner import (
    TYPE_FLOAT,
    TYPE_INTEGER,
    TYPE_STRING,
    BaseQueryRunner,
    query_runners,
)
from redash.utils import json_dumps
from redis_query_runner import runner as runner_mod
from redis_query_runner.runner import Redis


class ScriptedClient:
    """Fake client: returns or raises a scripted outcome, records calls and close()."""

    def __init__(self, outcome=None, kwargs=None):
        self.outcome = outcome
        self.kwargs = kwargs
        self.calls = []
        self.closed = False

    def execute_command(self, *args):
        self.calls.append(args)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    def ping(self):
        self.calls.append(("PING",))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return True

    def close(self):
        self.closed = True


@pytest.fixture
def patched(monkeypatch):
    """patched(client) -> installs it as the client; returns the list of constructor kwargs."""
    made = []

    def install(client):
        def factory(**kwargs):
            made.append(kwargs)
            client.kwargs = kwargs
            return client

        monkeypatch.setattr(runner_mod, "new_client", factory)
        return made

    return install


def make(config=None):
    return Redis({"host": "h"} if config is None else config)


# ---- identity, schema, registration


def test_type_and_name():
    assert Redis.type() == "redis"
    assert Redis.name() == "Redis"
    assert Redis.enabled()


def test_is_a_real_base_query_runner():
    assert issubclass(Redis, BaseQueryRunner)
    assert Redis.should_annotate_query is False


def test_registered_on_import():
    assert query_runners["redis"] is Redis
    assert redis_query_runner.Redis is Redis


def test_annotate_query_is_a_noop():
    assert make().annotate_query("GET k", {"Username": "u", "query_id": 1}) == "GET k"


def test_schema_contents():
    s = Redis.configuration_schema()
    assert s["type"] == "object"
    assert set(s["properties"]) == {"host", "port", "db", "password"}
    assert s["required"] == ["host"]
    assert s["secret"] == ["password"]
    assert s["order"] == ["host", "port", "db", "password"]
    assert s["properties"]["port"]["type"] == "number"
    assert s["properties"]["db"]["type"] == "number"
    json.dumps(s)


def test_schema_has_no_timeout_or_url_field():
    props = Redis.configuration_schema()["properties"]
    assert not {"url", "timeout", "decode_responses"} & set(props)


# ---- config defaults


@pytest.mark.parametrize(
    "cfg, expected",
    [
        ({"host": "h"}, ("h", 6379, 0, None)),
        ({}, ("localhost", 6379, 0, None)),
        ({"host": "", "port": "", "db": "", "password": ""}, ("localhost", 6379, 0, None)),
        ({"host": "   "}, ("localhost", 6379, 0, None)),
        ({"host": None, "port": None, "db": None, "password": None}, ("localhost", 6379, 0, None)),
        ({"host": " h "}, ("h", 6379, 0, None)),
        ({"host": "h", "port": 6380, "db": 3, "password": "pw"}, ("h", 6380, 3, "pw")),
        ({"host": "h", "port": "6380", "db": "3"}, ("h", 6380, 3, None)),
        ({"host": "h", "port": " 6380 ", "db": " 3 "}, ("h", 6380, 3, None)),
        ({"host": "h", "port": 6380.0, "db": 3.0}, ("h", 6380, 3, None)),
        ({"host": "h", "port": 65535}, ("h", 65535, 0, None)),
        ({"host": "h", "db": 99}, ("h", 6379, 99, None)),  # out of range is the server's call
    ],
)
def test_config_defaults(patched, cfg, expected):
    client = ScriptedClient("x")
    patched(client)
    Redis(cfg).run_query("GET k", None)
    kw = client.kwargs
    assert (kw["host"], kw["port"], kw["db"], kw["password"]) == expected


@pytest.mark.parametrize(
    "cfg, fragment",
    [
        ({"host": "h", "port": "abc"}, "Invalid port"),
        ({"host": "h", "port": 6380.5}, "Invalid port"),
        ({"host": "h", "port": 0}, "Invalid port"),
        ({"host": "h", "port": -5}, "Invalid port"),
        ({"host": "h", "port": 65536}, "Invalid port"),
        ({"host": "h", "port": True}, "Invalid port"),
        ({"host": "h", "port": [1]}, "Invalid port"),
        ({"host": "h", "port": "1e3"}, "Invalid port"),
        ({"host": "h", "db": "xyz"}, "Invalid database index"),
        ({"host": "h", "db": 1.5}, "Invalid database index"),
        ({"host": "h", "db": -1}, "Invalid database index"),
        ({"host": "h", "db": True}, "Invalid database index"),
        ({"host": "h", "db": {"a": 1}}, "Invalid database index"),
        ({"host": 123}, "Invalid host"),
        ({"host": "h", "password": 123}, "Invalid password"),
    ],
)
def test_invalid_config_is_an_error(patched, cfg, fragment):
    client = ScriptedClient("x")
    patched(client)
    data, error = Redis(cfg).run_query("GET k", None)
    assert data is None and fragment in error
    assert client.kwargs is None  # never connected
    with pytest.raises(Exception, match=fragment):
        Redis(cfg).test_connection()


def test_invalid_config_reported_even_for_a_bad_query(patched):
    patched(ScriptedClient("x"))
    _, error = Redis({"host": "h", "port": "abc"}).run_query("", None)
    assert error  # a parse error or a config error, never a crash


def test_client_options_are_fixed(patched):
    client = ScriptedClient("x")
    patched(client)
    Redis({"host": "h", "decode_responses": False, "socket_timeout": 1}).run_query("GET k", None)
    kw = client.kwargs
    assert kw["decode_responses"] is True
    assert kw["socket_connect_timeout"] == 5
    assert kw["socket_timeout"] == 30


# ---- run_query: replies through the real shaper


def test_get_with_fake_redis(monkeypatch, fake_redis):
    fake_redis.data["k"] = "v"
    monkeypatch.setattr(runner_mod, "new_client", lambda **kw: fake_redis)
    data, err = make().run_query("GET k", None)
    assert err is None
    assert data["rows"] == [{"value": "v"}]
    assert data["columns"] == [{"name": "value", "friendly_name": "value", "type": TYPE_STRING}]
    assert fake_redis.closed


def test_set_then_get_with_fake_redis(monkeypatch, fake_redis):
    monkeypatch.setattr(runner_mod, "new_client", lambda **kw: fake_redis)
    r = make()
    assert r.run_query('SET "my key" "a b"', None)[0]["rows"] == [{"value": "OK"}]
    assert fake_redis.data == {"my key": "a b"}
    assert r.run_query('GET "my key"', None)[0]["rows"] == [{"value": "a b"}]


def test_command_args_reach_client_as_parsed(patched):
    client = ScriptedClient("x")
    patched(client)
    make().run_query('  set   \'a b\' "c\\"d"  \n', None)
    assert client.calls == [("set", "a b", 'c"d')]


def test_multiline_query_rejected_without_connecting(patched):
    client = ScriptedClient("x")
    made = patched(client)
    data, err = make().run_query("GET a\nGET b", None)
    assert data is None
    assert "2 lines" in err
    assert made == []


@pytest.mark.parametrize("q", ["", "   ", "\n\n", "\t \n "])
def test_empty_query(patched, q):
    made = patched(ScriptedClient("x"))
    data, err = make().run_query(q, None)
    assert data is None
    assert "empty" in err.lower()
    assert made == []


def test_unbalanced_quote(patched):
    patched(ScriptedClient("x"))
    data, err = make().run_query('GET "abc', None)
    assert data is None and "quote" in err.lower()


def test_trailing_blank_lines_ok(patched):
    client = ScriptedClient("v")
    patched(client)
    assert make().run_query("GET k\n\n\n", None)[1] is None


@pytest.mark.parametrize(
    "query, reply, columns, rows",
    [
        ("GET k", None, ["value"], [{"value": None}]),
        ("GET k", "hello", ["value"], [{"value": "hello"}]),
        ("INCR k", 5, ["value"], [{"value": 5}]),
        ("PING", True, ["value"], [{"value": "PONG"}]),
        ("SET k v", True, ["value"], [{"value": "OK"}]),
        ("LRANGE l 0 -1", ["a", "b"], ["value"], [{"value": "a"}, {"value": "b"}]),
        ("LRANGE l 0 -1", [], ["value"], []),
        ("SMEMBERS s", {"b", "a"}, ["value"], [{"value": "a"}, {"value": "b"}]),
        ("HGETALL h", {"n": "1", "s": "x"}, ["n", "s"], [{"n": 1, "s": "x"}]),
        (
            "ZRANGE z 0 -1 WITHSCORES",
            [("a", 1.0), ("b", 2.5)],
            ["member", "score"],
            [{"member": "a", "score": 1.0}, {"member": "b", "score": 2.5}],
        ),
        ("SCAN 0", ("0", ["k1", "k2"]), ["value"], [{"value": "k1"}, {"value": "k2"}]),
        ("XRANGE s - +", [("1-0", {"f": "v"})], ["id", "f"], [{"id": "1-0", "f": "v"}]),
        (
            "INFO",
            {"redis_mode": "standalone"},
            ["key", "value"],
            [{"key": "redis_mode", "value": "standalone"}],
        ),
        (
            "CONFIG GET maxmemory",
            {"maxmemory": "0"},
            ["key", "value"],
            [{"key": "maxmemory", "value": 0}],
        ),
        ("EXISTS k", 0, ["value"], [{"value": 0}]),
        ("ZSCORE z m", 1.5, ["value"], [{"value": 1.5}]),
        ("GET k", "", ["value"], [{"value": ""}]),
        ("GET k", "héllo ☃", ["value"], [{"value": "héllo ☃"}]),
    ],
)
def test_reply_kinds(patched, query, reply, columns, rows):
    client = ScriptedClient(reply)
    patched(client)
    data, err = make().run_query(query, None)
    assert err is None
    assert [c["name"] for c in data["columns"]] == columns
    assert data["rows"] == rows
    assert client.closed


def test_column_types(patched):
    patched(ScriptedClient({"i": "1", "f": "1.5", "s": "x"}))
    data, _ = make().run_query("HGETALL h", None)
    assert {c["name"]: c["type"] for c in data["columns"]} == {
        "i": TYPE_INTEGER,
        "f": TYPE_FLOAT,
        "s": TYPE_STRING,
    }


@pytest.mark.parametrize(
    "reply",
    [
        None,
        "x",
        1,
        1.5,
        True,
        ["a", 1],
        {"a": "1"},
        {"a", "b"},
        ("0", ["k"]),
        [("a", float("inf"))],
        [["nested", ["x"]]],
        b"raw",
        {"big": 10**30},
        [float("nan")],
    ],
)
def test_result_is_json_serializable(patched, reply):
    patched(ScriptedClient(reply))
    data, err = make().run_query("FOO x", None)
    assert err is None
    assert isinstance(data, dict)
    assert set(data) == {"columns", "rows"}
    json.loads(json_dumps(data))


# ---- errors


@pytest.mark.parametrize(
    "exc, needle",
    [
        (redis.ResponseError("ERR unknown command 'FOO'"), "Redis error: ERR unknown command"),
        (redis.ResponseError("WRONGTYPE Operation against a key"), "WRONGTYPE"),
        (redis.ResponseError("ERR wrong number of arguments for 'get' command"), "wrong number"),
        (redis.ConnectionError("Connection refused"), "Could not connect to Redis at h:6379"),
        (redis.TimeoutError("Timeout reading from socket"), "timed out"),
        (redis.AuthenticationError("invalid password"), "authentication failed"),
        (redis.BusyLoadingError("loading"), "Could not connect"),  # a ConnectionError subclass
        (redis.DataError("bad arg"), "Redis client error (DataError)"),
        (redis.RedisError("generic"), "Redis client error (RedisError)"),
        (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"), "UTF-8"),
    ],
)
def test_error_paths(patched, exc, needle):
    client = ScriptedClient(exc)
    patched(client)
    data, err = make().run_query("FOO x", None)
    assert data is None
    assert needle.lower() in err.lower()
    assert client.closed


def test_error_mentions_configured_host_and_port(patched):
    patched(ScriptedClient(redis.ConnectionError("nope")))
    _, err = Redis({"host": "db.internal", "port": 7000}).run_query("GET k", None)
    assert "db.internal:7000" in err


def test_password_never_leaks_in_errors(patched):
    patched(ScriptedClient(redis.AuthenticationError("bad password hunter2 given")))
    _, err = Redis({"host": "h", "password": "hunter2"}).run_query("GET k", None)
    assert "hunter2" not in err and "***" in err


def test_unexpected_exception_propagates_but_closes(patched):
    client = ScriptedClient(RuntimeError("bug"))
    patched(client)
    with pytest.raises(RuntimeError):
        make().run_query("GET k", None)
    assert client.closed


def test_connect_failure_in_constructor_is_an_error_tuple(monkeypatch):
    def boom(**kw):
        raise redis.ConnectionError("cannot")

    monkeypatch.setattr(runner_mod, "new_client", boom)
    # Constructing is lazy in redis-py, but guard anyway: must not raise out of run_query
    try:
        data, err = make().run_query("GET k", None)
    except redis.ConnectionError:
        pytest.fail("constructor error escaped run_query")
    assert data is None and "Could not connect" in err


def test_each_query_uses_a_fresh_client(patched):
    c = ScriptedClient("x")
    made = patched(c)
    r = make()
    r.run_query("GET a", None)
    r.run_query("GET b", None)
    assert len(made) == 2


# ---- test_connection


def test_test_connection_success(patched):
    client = ScriptedClient()
    patched(client)
    assert make().test_connection() is None
    assert client.calls == [("PING",)]
    assert client.closed


@pytest.mark.parametrize(
    "exc, needle",
    [
        (redis.ConnectionError("refused"), "Could not connect"),
        (redis.TimeoutError("t"), "timed out"),
        (redis.AuthenticationError("x"), "authentication"),
        (redis.ResponseError("NOAUTH Authentication required."), "NOAUTH"),
    ],
)
def test_test_connection_failure_raises(patched, exc, needle):
    client = ScriptedClient(exc)
    patched(client)
    with pytest.raises(Exception, match="(?i)" + needle):
        make().test_connection()
    assert client.closed


def test_test_connection_failure_hides_password(patched):
    patched(ScriptedClient(redis.AuthenticationError("pw=s3cret")))
    with pytest.raises(Exception) as ei:
        Redis({"host": "h", "password": "s3cret"}).test_connection()
    assert "s3cret" not in str(ei.value)


def test_noop_query_is_ping():
    assert Redis.noop_query == "PING"
