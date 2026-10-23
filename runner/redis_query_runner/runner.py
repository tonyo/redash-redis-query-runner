"""The Redash query runner for Redis: one Redis command per query, result as a table."""
import logging

import redis

from redash.query_runner import BaseQueryRunner, register

from .parser import QueryError, parse_query
from .shape import shape

logger = logging.getLogger(__name__)

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 6379
DEFAULT_DB = 0
CONNECT_TIMEOUT = 5  # seconds: a bad host fails fast, well inside Test Connection's 30 s job limit
COMMAND_TIMEOUT = 30  # seconds per command


def new_client(**kwargs):
    """Build the redis-py client. A seam: unit tests patch this to return a fake."""
    return redis.Redis(**kwargs)


class ConfigError(ValueError):
    """The data source configuration holds a value that cannot be used."""


def _as_int(name, value, default, minimum, maximum):
    """Config value -> int in [minimum, maximum]. Unset/blank -> default; anything else unusable raises."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ConfigError(f"Invalid {name}: {value!r} is not a whole number")
    try:
        n = int(value.strip() if isinstance(value, str) else value)
        if isinstance(value, float) and value != n:
            raise ValueError
    except (ValueError, OverflowError):
        raise ConfigError(f"Invalid {name}: {value!r} is not a whole number") from None
    if not minimum <= n <= maximum:
        raise ConfigError(f"Invalid {name}: {n} is outside {minimum}-{maximum}")
    return n


class Redis(BaseQueryRunner):
    should_annotate_query = False  # a /* ... */ prefix would be sent to Redis as the command
    noop_query = "PING"

    def __init__(self, configuration):
        super().__init__(configuration)
        self.syntax = "text"

    @classmethod
    def name(cls):
        return "Redis"

    @classmethod
    def type(cls):
        return "redis"

    @classmethod
    def configuration_schema(cls):
        return {
            "type": "object",
            "properties": {
                "host": {"type": "string", "title": "Host", "default": DEFAULT_HOST},
                "port": {"type": "number", "title": "Port", "default": DEFAULT_PORT},
                "db": {"type": "number", "title": "Database index", "default": DEFAULT_DB},
                "password": {"type": "string", "title": "Password"},
            },
            "order": ["host", "port", "db", "password"],
            "required": ["host"],
            "secret": ["password"],
        }

    # -- configuration, with defaults applied here (schema defaults are only form placeholders)

    def _host(self):
        host = self.configuration.get("host")
        if host is not None and not isinstance(host, str):
            raise ConfigError(f"Invalid host: {host!r} is not text")
        return (host or "").strip() or DEFAULT_HOST

    def _port(self):
        return _as_int("port", self.configuration.get("port"), DEFAULT_PORT, 1, 65535)

    def _db(self):
        return _as_int("database index", self.configuration.get("db"), DEFAULT_DB, 0, 2**31 - 1)

    def _password(self):
        password = self.configuration.get("password")
        if password is not None and not isinstance(password, str):
            raise ConfigError("Invalid password: not text")
        return password or None

    def _connect(self):
        return new_client(
            host=self._host(),
            port=self._port(),
            db=self._db(),
            password=self._password(),
            decode_responses=True,
            socket_connect_timeout=CONNECT_TIMEOUT,
            socket_timeout=COMMAND_TIMEOUT,
        )

    # -- errors

    def _describe(self, e):
        """An exception -> one clear line for the user (never containing the password)."""
        if isinstance(e, (QueryError, ConfigError)):
            return str(e)
        where = f"{self._host()}:{self._port()}"
        if isinstance(e, redis.AuthenticationError):  # subclass of ConnectionError: check first
            msg = f"Redis authentication failed at {where}: {e}"
        elif isinstance(e, redis.TimeoutError):
            msg = f"Redis at {where} timed out: {e}"
        elif isinstance(e, redis.ConnectionError):
            msg = f"Could not connect to Redis at {where}: {e}"
        elif isinstance(e, redis.ResponseError):
            msg = f"Redis error: {e}"
        elif isinstance(e, redis.RedisError):
            msg = f"Redis client error ({type(e).__name__}): {e}"
        elif isinstance(e, UnicodeDecodeError):
            msg = "Reply contains binary data that is not valid UTF-8 and cannot be shown"
        else:
            raise e
        password = self._password()
        if password:
            msg = msg.replace(password, "***")
        return msg

    _HANDLED = (QueryError, ConfigError, redis.RedisError, UnicodeDecodeError)

    # -- BaseQueryRunner API

    def test_connection(self):
        client = None
        try:
            client = self._connect()
            client.ping()
        except self._HANDLED as e:
            raise Exception(self._describe(e)) from e
        finally:
            if client is not None:
                client.close()

    def run_query(self, query, user):
        try:
            args = parse_query(query)
        except QueryError as e:
            return None, self._describe(e)
        client = None
        try:
            client = self._connect()
            reply = client.execute_command(*args)
            return shape(reply, args), None
        except self._HANDLED as e:
            return None, self._describe(e)
        finally:
            if client is not None:
                client.close()


register(Redis)
