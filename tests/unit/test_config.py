"""Configuration loading and the exact validation messages (spec §8, §9)."""

from __future__ import annotations

from datetime import time

import pytest

from sap_patch_day.config import MappingConfigSource, load_config
from sap_patch_day.errors import ConfigError

VALID = {"sender": "sap-patch-day@example.com", "recipients": "one@example.test,two@example.test"}


def load(values: dict[str, str] | None = None, environ: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    return load_config(MappingConfigSource({**VALID, **(values or {})}).read(), environ or {})


def test_defaults_are_the_documented_ones() -> None:
    config = load()
    assert config.recipients == ("one@example.test", "two@example.test")
    assert config.sender == "sap-patch-day@example.com"
    assert config.reply_to == ""
    assert config.subject_prefix == "[SAP Security Patch Day]"
    assert config.attach_csv is False
    assert config.notify_when_not_published is True
    assert config.retry_window_end == time(20, 0)
    assert config.dry_run is False
    assert config.config_path == "/sap-security-patch-day/"
    assert config.state_path == "/sap-security-patch-day/state/"
    assert config.http.timeout_seconds == 30
    assert config.http.max_attempts == 3


def test_missing_sender_uses_the_documented_message() -> None:
    with pytest.raises(ConfigError) as error:
        load_config(MappingConfigSource({"recipients": "a@example.test"}).read(), {})
    assert str(error.value) == (
        "SES sender address is not configured: set the SSM parameter /sap-security-patch-day/sender"
    )


def test_invalid_sender_uses_the_documented_message() -> None:
    with pytest.raises(ConfigError) as error:
        load({"sender": "not-an-address"})
    assert str(error.value) == ("SES sender address is not a valid email address: 'not-an-address'")


def test_missing_recipients_uses_the_documented_message() -> None:
    with pytest.raises(ConfigError) as error:
        load_config(MappingConfigSource({"sender": "s@example.com"}).read(), {})
    assert str(error.value) == (
        "No recipients configured: set the SSM parameter /sap-security-patch-day/recipients"
    )


def test_whitespace_only_recipients_are_treated_as_missing() -> None:
    with pytest.raises(ConfigError):
        load({"recipients": "   "})


def test_invalid_recipient_uses_the_documented_message() -> None:
    with pytest.raises(ConfigError) as error:
        load({"recipients": "one@example.test,broken"})
    assert str(error.value) == "Recipient is not a valid email address: 'broken'"


def test_invalid_reply_to_uses_the_documented_message() -> None:
    with pytest.raises(ConfigError) as error:
        load({"reply_to": "nope"})
    assert str(error.value) == "Reply-to address is not a valid email address: 'nope'"


def test_boolean_parameter_messages() -> None:
    with pytest.raises(ConfigError) as error:
        load({"attach_csv": "yes please"})
    assert str(error.value) == "attach_csv must be 'true' or 'false', got 'yes please'"
    with pytest.raises(ConfigError) as error:
        load({"notify_when_not_published": "maybe"})
    assert str(error.value) == "notify_when_not_published must be 'true' or 'false', got 'maybe'"


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on"])
def test_boolean_true_spellings(value: str) -> None:
    assert load({"attach_csv": value}).attach_csv is True


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off"])
def test_boolean_false_spellings(value: str) -> None:
    assert load({"notify_when_not_published": value}).notify_when_not_published is False


def test_recipients_accept_newlines_semicolons_and_json() -> None:
    assert load({"recipients": "a@example.test\nb@example.test"}).recipients == (
        "a@example.test",
        "b@example.test",
    )
    assert load({"recipients": "a@example.test;b@example.test"}).recipients == (
        "a@example.test",
        "b@example.test",
    )
    assert load({"recipients": '["a@example.test", "b@example.test"]'}).recipients == (
        "a@example.test",
        "b@example.test",
    )


def test_recipients_json_must_be_an_array() -> None:
    with pytest.raises(ConfigError):
        load({"recipients": '{"a": "b@example.test"}'})


def test_unknown_parameters_are_ignored() -> None:
    config = load({"documentation": "notes", "future_flag": "whatever"})
    assert config.recipients == ("one@example.test", "two@example.test")


def test_retry_window_end_must_be_hh_mm() -> None:
    with pytest.raises(ConfigError) as error:
        load(environ={"RETRY_WINDOW_END_SGT": "8pm"})
    assert str(error.value) == "RETRY_WINDOW_END_SGT must be HH:MM in Singapore time, got '8pm'"


def test_retry_window_end_is_parsed() -> None:
    assert load(environ={"RETRY_WINDOW_END_SGT": "21:30"}).retry_window_end == time(21, 30)


@pytest.mark.parametrize(
    "variable,value,expected",
    [
        (
            "HTTP_TIMEOUT_SECONDS",
            "abc",
            "HTTP_TIMEOUT_SECONDS must be a positive integer, got 'abc'",
        ),
        ("HTTP_TIMEOUT_SECONDS", "0", "HTTP_TIMEOUT_SECONDS must be a positive integer, got '0'"),
        ("HTTP_MAX_ATTEMPTS", "-1", "HTTP_MAX_ATTEMPTS must be a positive integer, got '-1'"),
        (
            "HTTP_BACKOFF_SECONDS",
            "soon",
            "HTTP_BACKOFF_SECONDS must be a positive number, got 'soon'",
        ),
    ],
)
def test_environment_number_messages(variable: str, value: str, expected: str) -> None:
    with pytest.raises(ConfigError) as error:
        load(environ={variable: value})
    assert str(error.value) == expected


def test_dry_run_and_log_level_come_from_the_environment() -> None:
    config = load(environ={"DRY_RUN": "true", "LOG_LEVEL": "debug"})
    assert config.dry_run is True
    assert config.log_level == "DEBUG"


def test_dry_run_message() -> None:
    with pytest.raises(ConfigError) as error:
        load(environ={"DRY_RUN": "sometimes"})
    assert str(error.value) == "DRY_RUN must be 'true' or 'false', got 'sometimes'"


def test_bulkhead_recipients_override_the_stored_list() -> None:
    config = load(environ={"BULKHEAD_RECIPIENTS": "smoke@example.test"})
    assert config.recipients == ("smoke@example.test",)


def test_custom_paths_are_honoured_in_messages() -> None:
    with pytest.raises(ConfigError) as error:
        load_config(
            MappingConfigSource({"recipients": "a@example.test"}).read(),
            {"SSM_CONFIG_PATH": "/custom/"},
        )
    assert "/custom/sender" in str(error.value)


def test_masked_recipients_hide_the_local_part() -> None:
    config = load()
    assert config.masked_recipients == ("o***@example.test", "t***@example.test")


def test_configuration_set_is_passed_through() -> None:
    assert load(environ={"CONFIGURATION_SET": "sap-security-patch-day"}).configuration_set == (
        "sap-security-patch-day"
    )


def test_mapping_source_only_exposes_known_keys() -> None:
    source = MappingConfigSource({**VALID, "state_path": "/other/", "unrelated": "x"})
    assert set(source.read()) == {"sender", "recipients"}
