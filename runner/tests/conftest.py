"""Shared fixtures: a fake Redis client (unit) and a real, flushed DB 15 (integration)."""

import os

import pytest

DATA_REDIS_HOST = os.environ.get("DATA_REDIS_HOST", "data-redis")
DATA_REDIS_PORT = int(os.environ.get("DATA_REDIS_PORT", "6379"))
TEST_DB = 15  # never DB 0, which holds the seed data


class FakeRedis:
    """Minimal in-memory stand-in for redis.Redis. Extend as the runner needs it."""

    def __init__(self):
        self.data = {}
        self.closed = False

    def close(self):
        self.closed = True

    def ping(self):
        return True

    def set(self, key, value):
        self.data[key] = value
        return True

    def get(self, key):
        return self.data.get(key)

    def execute_command(self, *args):
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
