"""Shared fixtures: a fake Redis client (unit) and a real, flushed DB 15 (integration)."""

import os

import pytest

DATA_REDIS_HOST = os.environ.get("DATA_REDIS_HOST", "data-redis")
DATA_REDIS_PORT = int(os.environ.get("DATA_REDIS_PORT", "6379"))
TEST_DB = 15  # never DB 0, which holds the seed data


_STORE = object()


class FakeRedis:
    """Stand-in for redis.Redis. By default an in-memory GET/SET store; given an `outcome`, every
    command returns it (or raises it, if it is an exception). Records calls and close()."""

    def __init__(self, outcome=_STORE, kwargs=None):
        self.outcome = outcome
        self.kwargs = kwargs
        self.data = {}
        self.calls = []
        self.closed = False

    def close(self):
        self.closed = True

    def _scripted(self):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    def ping(self):
        self.calls.append(("PING",))
        if self.outcome is not _STORE:
            self._scripted()
        return True

    def set(self, key, value):
        self.data[key] = value
        return True

    def get(self, key):
        return self.data.get(key)

    def execute_command(self, *args):
        self.calls.append(args)
        if self.outcome is not _STORE:
            return self._scripted()
        name, *rest = args
        return getattr(self, name.lower())(*rest)


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def real_redis():
    import redis

    client = redis.Redis(
        host=DATA_REDIS_HOST, port=DATA_REDIS_PORT, db=TEST_DB, decode_responses=True
    )
    client.flushdb()
    yield client
    client.flushdb()
    client.close()
