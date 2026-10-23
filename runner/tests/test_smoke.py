import pytest


def test_unit_fake_redis(fake_redis):
    fake_redis.set("k", "v")
    assert fake_redis.get("k") == "v"


def test_runner_package_imports():
    import redash.query_runner  # noqa: F401
    import redis_query_runner  # noqa: F401


@pytest.mark.integration
def test_integration_real_redis(real_redis):
    assert real_redis.ping()
    assert real_redis.dbsize() == 0
    real_redis.set("k", "v")
    assert real_redis.get("k") == "v"
