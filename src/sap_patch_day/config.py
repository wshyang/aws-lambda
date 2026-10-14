"""Configuration loading and validation (spec §8, §9, addendum 0004).

People live in SSM Parameter Store; paths and policies live in environment
variables. Every failure raises :class:`ConfigError` with the exact message
documented in ``docs/spec.md`` §8, before any network request is made.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import time
from typing import Any, Protocol

from .errors import ConfigError
from .models import HttpPolicy, RuntimeConfig

DEFAULT_CONFIG_PATH = "/sap-security-patch-day/"
DEFAULT_STATE_PATH = "/sap-security-patch-day/state/"
DEFAULT_SUBJECT_PREFIX = "[SAP Security Patch Day]"
DEFAULT_USER_AGENT = "sap-patch-day-notifier/1 (aws-lambda; +https://support.sap.com)"

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
TIME_PATTERN = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
CONFIG_KEYS = (
    "sender",
    "recipients",
    "reply_to",
    "subject_prefix",
    "attach_csv",
    "notify_when_not_published",
)


class ConfigSource(Protocol):
    """Anything that can supply the raw configuration values."""

    def read(self) -> Mapping[str, str]: ...


class SsmConfigSource:
    """Reads the configuration parameters (non-recursive: state lives deeper)."""

    def __init__(self, client: Any, path_prefix: str) -> None:
        self._client = client
        self._prefix = path_prefix if path_prefix.endswith("/") else f"{path_prefix}/"

    def read(self) -> Mapping[str, str]:
        response = self._client.get_parameters_by_path(
            Path=self._prefix, Recursive=False, WithDecryption=False
        )
        return {
            parameter.get("Name", "")[len(self._prefix) :]: parameter.get("Value", "")
            for parameter in response.get("Parameters", [])
        }


class MappingConfigSource:
    """Configuration from a mapping: the offline dry run, and tests."""

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = {key: value for key, value in values.items() if key in CONFIG_KEYS}

    def read(self) -> Mapping[str, str]:
        return dict(self._values)


def load_config(values: Mapping[str, str], environ: Mapping[str, str]) -> RuntimeConfig:
    """Validate raw values into a :class:`RuntimeConfig` (spec §9.3)."""
    config_path = _text(environ.get("SSM_CONFIG_PATH"), DEFAULT_CONFIG_PATH)
    return RuntimeConfig(
        recipients=_recipients(values, environ, config_path),
        sender=_sender(values, config_path),
        reply_to=_reply_to(values, config_path),
        subject_prefix=_text(values.get("subject_prefix"), DEFAULT_SUBJECT_PREFIX),
        attach_csv=_config_boolean(values, "attach_csv", default=False),
        notify_when_not_published=_config_boolean(
            values, "notify_when_not_published", default=True
        ),
        retry_window_end=_time_of_day(environ.get("RETRY_WINDOW_END_SGT"), "20:00"),
        http=_http_policy(environ),
        configuration_set=_text(environ.get("CONFIGURATION_SET"), ""),
        dry_run=_environment_boolean(environ, "DRY_RUN", default=False),
        log_level=_text(environ.get("LOG_LEVEL"), "INFO").upper(),
        config_path=config_path,
        state_path=_text(environ.get("SSM_STATE_PATH"), DEFAULT_STATE_PATH),
    )


def _sender(values: Mapping[str, str], config_path: str) -> str:
    raw = _text(values.get("sender"), "")
    if not raw:
        raise ConfigError(
            f"SES sender address is not configured: set the SSM parameter {config_path}sender"
        )
    return _require_email(raw, f"SES sender address is not a valid email address: '{raw}'")


def _reply_to(values: Mapping[str, str], _config_path: str) -> str:
    raw = _text(values.get("reply_to"), "")
    if not raw:
        return ""
    return _require_email(raw, f"Reply-to address is not a valid email address: '{raw}'")


def _recipients(
    values: Mapping[str, str], environ: Mapping[str, str], config_path: str
) -> tuple[str, ...]:
    override = _text(environ.get("BULKHEAD_RECIPIENTS"), "")
    raw = override or _text(values.get("recipients"), "")
    if not raw:
        raise ConfigError(
            f"No recipients configured: set the SSM parameter {config_path}recipients"
        )
    addresses = tuple(
        _require_email(address, f"Recipient is not a valid email address: '{address}'")
        for address in _split(raw)
    )
    if not addresses:
        raise ConfigError(
            f"No recipients configured: set the SSM parameter {config_path}recipients"
        )
    return addresses


def _split(raw: str) -> tuple[str, ...]:
    """Accept a StringList, a comma list, a newline list or a JSON array."""
    stripped = raw.strip()
    if stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as error:
            raise ConfigError(f"recipients is not valid JSON: {error}") from error
        if not isinstance(parsed, list):
            raise ConfigError("recipients JSON must be an array of addresses")
        return tuple(str(item).strip() for item in parsed if str(item).strip())
    normalised = stripped.replace("\n", ",").replace(";", ",")
    return tuple(part.strip() for part in normalised.split(",") if part.strip())


def _config_boolean(values: Mapping[str, str], key: str, *, default: bool) -> bool:
    raw = _text(values.get(key), "")
    if not raw:
        return default
    return _boolean(raw, f"{key} must be 'true' or 'false', got '{raw}'")


def _environment_boolean(environ: Mapping[str, str], key: str, *, default: bool) -> bool:
    raw = _text(environ.get(key), "")
    if not raw:
        return default
    return _boolean(raw, f"{key} must be 'true' or 'false', got '{raw}'")


def _boolean(raw: str, message: str) -> bool:
    lowered = raw.strip().lower()
    if lowered in {"true", "1", "yes", "on"}:
        return True
    if lowered in {"false", "0", "no", "off"}:
        return False
    raise ConfigError(message)


def _http_policy(environ: Mapping[str, str]) -> HttpPolicy:
    return HttpPolicy(
        timeout_seconds=_positive_int(
            environ.get("HTTP_TIMEOUT_SECONDS"), 30, "HTTP_TIMEOUT_SECONDS"
        ),
        max_attempts=_positive_int(environ.get("HTTP_MAX_ATTEMPTS"), 3, "HTTP_MAX_ATTEMPTS"),
        backoff_seconds=_positive_float(
            environ.get("HTTP_BACKOFF_SECONDS"), 2.0, "HTTP_BACKOFF_SECONDS"
        ),
        user_agent=_text(environ.get("USER_AGENT"), DEFAULT_USER_AGENT),
    )


def _time_of_day(raw: str | None, default: str) -> time:
    candidate = _text(raw, default)
    match = TIME_PATTERN.match(candidate)
    if match is None:
        raise ConfigError(
            f"RETRY_WINDOW_END_SGT must be HH:MM in Singapore time, got '{candidate}'"
        )
    return time(hour=int(match.group(1)), minute=int(match.group(2)))


def _positive_int(raw: str | None, default: int, key: str) -> int:
    candidate = _text(raw, str(default))
    if not candidate.isdigit() or int(candidate) < 1:
        raise ConfigError(f"{key} must be a positive integer, got '{candidate}'")
    return int(candidate)


def _positive_float(raw: str | None, default: float, key: str) -> float:
    candidate = _text(raw, str(default))
    try:
        value = float(candidate)
    except ValueError as error:
        raise ConfigError(f"{key} must be a positive number, got '{candidate}'") from error
    if value < 0:
        raise ConfigError(f"{key} must be a positive number, got '{candidate}'")
    return value


def _require_email(value: str, message: str) -> str:
    if not EMAIL_PATTERN.match(value):
        raise ConfigError(message)
    return value


def _text(raw: str | None, default: str) -> str:
    if raw is None:
        return default
    return raw.strip() or default
