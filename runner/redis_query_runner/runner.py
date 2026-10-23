"""The Redash query runner for Redis: one Redis command per query, result as a table."""

import logging
from typing import Any

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


def new_client(**kwargs: str | int | bool | None) -> redis.Redis:
    """Build the redis-py client. A seam: unit tests patch this to return a fake."""
    return redis.Redis(**kwargs)


# redis-py parses these replies with callbacks written for its own OBJECT/COMMAND methods, which
# raise TypeError on a raw execute_command. Dropping them gives the raw reply for shape().
_RAW_REPLY_COMMANDS = ("OBJECT", "COMMAND")


def _execute(client: redis.Redis, args: list[str]) -> object:
    callbacks = getattr(client, "response_callbacks", None)
    if callbacks is not None:
        for name in _RAW_REPLY_COMMANDS:
            callbacks.pop(name, None)
    return client.execute_command(*args)


class ConfigError(ValueError):
    """The data source configuration holds a value that cannot be used."""


def _as_int(name: str, value: object, default: int, minimum: int, maximum: int) -> int:
    """Config value -> int in [minimum, maximum].

    Unset/blank -> default; anything else unusable raises.
    """
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

    def __init__(self, configuration: dict[str, Any]) -> None:
        super().__init__(configuration)
        self.syntax = "text"

    @classmethod
    def name(cls) -> str:
        return "Redis"

    @classmethod
    def type(cls) -> str:
        return "redis"

    @classmethod
    def configuration_schema(cls) -> dict[str, Any]:
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

    def _host(self) -> str:
        host = self.configuration.get("host")
        if host is not None and not isinstance(host, str):
            raise ConfigError(f"Invalid host: {host!r} is not text")
        return (host or "").strip() or DEFAULT_HOST

    def _port(self) -> int:
        return _as_int("port", self.configuration.get("port"), DEFAULT_PORT, 1, 65535)

    def _db(self) -> int:
        return _as_int("database index", self.configuration.get("db"), DEFAULT_DB, 0, 2**31 - 1)

    def _password(self) -> str | None:
        password = self.configuration.get("password")
        if password is not None and not isinstance(password, str):
            raise ConfigError("Invalid password: not text")
        return password or None

    def _connect(self) -> redis.Redis:
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

    def _describe(self, e: Exception) -> str:
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

    def test_connection(self) -> None:
        client = None
        try:
            client = self._connect()
            client.ping()
        except self._HANDLED as e:
            raise Exception(self._describe(e)) from e
        finally:
            if client is not None:
                client.close()

    def run_query(self, query: str, user: object) -> tuple[dict[str, Any] | None, str | None]:
        try:
            args = parse_query(query)
        except QueryError as e:
            return None, self._describe(e)
        client = None
        try:
            client = self._connect()
            reply = _execute(client, args)
            return shape(reply, args), None
        except self._HANDLED as e:
            return None, self._describe(e)
        except (
            Exception
        ) as e:  # a reply the client library or shaper cannot handle: report, don't crash
            logger.exception("Redis reply could not be read for %s", args[0])
            return None, f"Could not read the reply to {args[0].upper()} ({type(e).__name__}: {e})"
        finally:
            if client is not None:
                client.close()


register(Redis)
