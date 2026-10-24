"""Data source configuration: validation, and the defaults that blank values fall back to."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 6379
DEFAULT_DB = 0
DEFAULT_CONNECT_TIMEOUT = (
    5  # seconds: a bad host fails fast, well inside Test Connection's 30 s job limit
)
DEFAULT_REQUEST_TIMEOUT = 30  # seconds per command
MAX_CONNECT_TIMEOUT = 30  # Test Connection is a 30 s job
MAX_REQUEST_TIMEOUT = 3600


class ConfigError(ValueError):
    """The data source configuration holds a value that cannot be used."""


def _is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _as_int(name: str, value: object, default: int, minimum: int, maximum: int) -> int:
    """Config value -> int in [minimum, maximum].

    Unset/blank -> default; anything else unusable raises.
    """
    if _is_blank(value):
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


def _as_text(name: str, value: object, default: str | None, *, strip: bool = True) -> str | None:
    """Config value -> text. Unset/blank -> default; a non-string raises."""
    if value is not None and not isinstance(value, str):
        raise ConfigError(f"Invalid {name}: not text")
    if strip and value is not None:
        value = value.strip()
    return value or default


def _as_bool(name: str, value: object) -> bool:
    """Config value -> bool. Unset/blank -> False; anything but a boolean raises."""
    if _is_blank(value):
        return False
    if not isinstance(value, bool):
        raise ConfigError(f"Invalid {name}: not a yes/no value")
    return value


@dataclass(frozen=True)
class Settings:
    """The validated connection settings, with defaults applied."""

    host: str
    port: int
    db: int
    username: str | None
    password: str | None
    connect_timeout: int
    request_timeout: int
    ssl: bool
    cluster: bool

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "Settings":
        db = _as_int("database index", config.get("db"), DEFAULT_DB, 0, 2**31 - 1)
        cluster = _as_bool("cluster mode", config.get("cluster"))
        if cluster and db:
            raise ConfigError("Invalid database index: cluster mode only has database 0")
        return cls(
            host=_as_text("host", config.get("host"), DEFAULT_HOST) or DEFAULT_HOST,
            port=_as_int("port", config.get("port"), DEFAULT_PORT, 1, 65535),
            db=db,
            username=_as_text("username", config.get("username"), None),
            password=_as_text("password", config.get("password"), None, strip=False),
            connect_timeout=_as_int(
                "connect timeout",
                config.get("connect_timeout"),
                DEFAULT_CONNECT_TIMEOUT,
                1,
                MAX_CONNECT_TIMEOUT,
            ),
            request_timeout=_as_int(
                "request timeout",
                config.get("request_timeout"),
                DEFAULT_REQUEST_TIMEOUT,
                1,
                MAX_REQUEST_TIMEOUT,
            ),
            ssl=_as_bool("TLS", config.get("ssl")),
            cluster=cluster,
        )
