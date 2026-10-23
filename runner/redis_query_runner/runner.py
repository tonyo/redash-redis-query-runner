"""The Redash query runner for Redis: one Redis command per query, result as a table."""

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import redis
from redash.query_runner import BaseQueryRunner, register

from .config import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_DB,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_REQUEST_TIMEOUT,
    ConfigError,
    Settings,
)
from .parser import QueryError, parse_query
from .shape import shape

logger = logging.getLogger(__name__)

# redis-py parses these replies with callbacks written for its own OBJECT/COMMAND methods, which
# raise TypeError on a raw execute_command. Dropping them gives the raw reply for shape().
_RAW_REPLY_COMMANDS = ("OBJECT", "COMMAND")


def new_client(**kwargs: str | int | bool | None) -> redis.Redis:
    """Build the redis-py client. A seam: unit tests patch this to return a fake."""
    client = redis.Redis(**kwargs)
    for name in _RAW_REPLY_COMMANDS:
        client.response_callbacks.pop(name, None)
    return client


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
                "username": {"type": "string", "title": "Username (Redis 6+ ACL)"},
                "password": {"type": "string", "title": "Password"},
                "connect_timeout": {
                    "type": "number",
                    "title": "Connect timeout (seconds)",
                    "default": DEFAULT_CONNECT_TIMEOUT,
                },
                "request_timeout": {
                    "type": "number",
                    "title": "Request timeout (seconds)",
                    "default": DEFAULT_REQUEST_TIMEOUT,
                },
            },
            "order": [
                "host",
                "port",
                "db",
                "username",
                "password",
                "connect_timeout",
                "request_timeout",
            ],
            "required": ["host"],
            "secret": ["password"],
        }

    def _settings(self) -> Settings:
        """The validated configuration, with defaults applied (schema defaults are only form
        placeholders)."""
        return Settings.from_config(self.configuration)

    def _connect(self) -> redis.Redis:
        settings = self._settings()
        return new_client(
            host=settings.host,
            port=settings.port,
            db=settings.db,
            username=settings.username,
            password=settings.password,
            decode_responses=True,
            socket_connect_timeout=settings.connect_timeout,
            socket_timeout=settings.request_timeout,
        )

    # -- errors

    def _describe(self, e: Exception) -> str:
        """An exception -> one clear line for the user (never containing the password)."""
        if isinstance(e, (QueryError, ConfigError)):
            return str(e)
        settings = self._settings()
        where = f"{settings.host}:{settings.port}"
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
        if settings.password:
            msg = msg.replace(settings.password, "***")
        return msg

    _HANDLED = (QueryError, ConfigError, redis.RedisError, UnicodeDecodeError)

    @contextmanager
    def _client(self) -> Generator[redis.Redis]:
        client = self._connect()
        try:
            yield client
        finally:
            client.close()

    # -- BaseQueryRunner API

    def test_connection(self) -> None:
        try:
            with self._client() as client:
                client.ping()
        except self._HANDLED as e:
            raise Exception(self._describe(e)) from e

    def run_query(self, query: str, user: object) -> tuple[dict[str, Any] | None, str | None]:
        try:
            args = parse_query(query)
        except QueryError as e:
            return None, self._describe(e)
        try:
            with self._client() as client:
                return shape(client.execute_command(*args), args), None
        except self._HANDLED as e:
            return None, self._describe(e)
        except Exception as e:  # a reply the client library or shaper cannot handle: don't crash
            logger.exception("Redis reply could not be read for %s", args[0])
            return None, f"Could not read the reply to {args[0].upper()} ({type(e).__name__}: {e})"


register(Redis)
