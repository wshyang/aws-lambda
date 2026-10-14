"""Unit tests for the handler's own surface: payload parsing, dispatch, the
result contract, and the wiring that turns the environment into ports.

The end-to-end behaviour of the delivery and watchdog flows lives in
`tests/integration/test_handler_delivery.py`; this file stays close to the
handler and asserts the things only it can get wrong — above all that an
unusable payload is rejected before any port is touched (spec §6, §7).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from sap_patch_day.clock import FixedClock
from sap_patch_day.errors import ConfigError, PatchDayError
from sap_patch_day.handler import (
    LOGGER,
    STATUS_INVALID_REQUEST,
    DeliveryRequest,
    InvalidRequest,
    RunContext,
    WatchdogRequest,
    build_run_context,
    execute_request,
    lambda_handler,
    parse_request,
)
from sap_patch_day.mailer import LoggingMailer, SesMailer
from sap_patch_day.models import PatchDayMonth, StateSnapshot
from sap_patch_day.state import InMemoryStateStore, ReadOnlyStateStore, SsmStateStore
from tests.support.fakes import RecordingMailer
from tests.support.harness import DEFAULT_CONFIG_VALUES, build_harness

# The first attempt of the October 2026 retry window, 16:05 Singapore time.
WINDOW_MOMENT = datetime(2026, 10, 13, 8, 5, tzinfo=UTC)
# The second Wednesday at 09:00 Singapore time.
WATCHDOG_MOMENT = datetime(2026, 10, 14, 1, 0, tzinfo=UTC)


class StateSpy(InMemoryStateStore):
    """A store that fails the test if anything touches it."""

    def read(self) -> StateSnapshot:
        raise AssertionError("a rejected request must not read the state store")

    def record_attempt(self, at: datetime) -> None:
        raise AssertionError("a rejected request must not write the state store")


class FakeSsmClient:
    """The two SSM calls the handler makes, honouring the two path rules.

    Configuration is read non-recursively and state recursively (spec §9), so the
    fake filters the same way the real service does: a parameter only appears
    under a path prefix, and a nested name only appears when recursion is asked
    for.
    """

    def __init__(self, parameters: Mapping[str, str]) -> None:
        self.parameters = dict(parameters)
        self.paths: list[str] = []

    def get_parameters_by_path(self, **kwargs: Any) -> Mapping[str, Any]:
        path = str(kwargs["Path"])
        recursive = bool(kwargs.get("Recursive"))
        prefix = path if path.endswith("/") else f"{path}/"
        self.paths.append(path)
        found = []
        for name, value in self.parameters.items():
            if not name.startswith(prefix):
                continue
            if not recursive and "/" in name[len(prefix) :]:
                continue
            found.append({"Name": name, "Value": value})
        return {"Parameters": found}


def clock_at(moment: datetime) -> FixedClock:
    return FixedClock(moment)


@pytest.fixture
def run_environment(monkeypatch: pytest.MonkeyPatch) -> FakeSsmClient:
    """Point the handler at a fake SSM and a fake SES, and record the service calls."""
    ssm = FakeSsmClient(
        {
            "/sap-security-patch-day/sender": DEFAULT_CONFIG_VALUES["sender"],
            "/sap-security-patch-day/recipients": DEFAULT_CONFIG_VALUES["recipients"],
        }
    )
    services: dict[str, Any] = {}
    monkeypatch.setattr(
        "sap_patch_day.handler._aws_client", lambda service: services.setdefault(service, ssm)
    )
    return ssm


# ------------------------------------------------------------------- parse_request


def test_an_empty_payload_delivers_the_current_patch_day_month() -> None:
    request = parse_request({}, clock_at(WINDOW_MOMENT))
    assert request == DeliveryRequest(
        target=PatchDayMonth(year=2026, month=10), ignore_idempotency=False
    )


@pytest.mark.parametrize(
    "payload", [{}, {"run_mode": ""}, {"run_mode": "  "}, {"run_mode": "delivery"}]
)
def test_delivery_is_the_default_when_run_mode_is_absent_or_blank(
    payload: Mapping[str, Any],
) -> None:
    assert isinstance(parse_request(payload, clock_at(WINDOW_MOMENT)), DeliveryRequest)


@pytest.mark.parametrize("event", [[], None, "delivery", 7])
def test_a_payload_that_is_not_a_mapping_falls_back_to_delivery(event: Any) -> None:
    assert isinstance(parse_request(event, clock_at(WINDOW_MOMENT)), DeliveryRequest)


def test_a_watchdog_payload_targets_the_month_of_the_invocation() -> None:
    request = parse_request({"run_mode": "watchdog"}, clock_at(WATCHDOG_MOMENT))
    assert request == WatchdogRequest(target=PatchDayMonth(year=2026, month=10))


def test_the_watchdog_ignores_a_target_month_it_was_not_asked_for() -> None:
    request = parse_request(
        {"run_mode": "watchdog", "target_month": "2020-01"}, clock_at(WATCHDOG_MOMENT)
    )
    assert isinstance(request, WatchdogRequest)
    assert request.target == PatchDayMonth(year=2026, month=10)


@pytest.mark.parametrize("run_mode", ["Watchdog", "WATCHDOG", "both", "dry-run", "deliver"])
def test_an_unknown_run_mode_is_rejected_with_the_exact_message(run_mode: str) -> None:
    request = parse_request({"run_mode": run_mode}, clock_at(WINDOW_MOMENT))
    assert isinstance(request, InvalidRequest)
    assert request.detail == f"Unsupported run_mode: '{run_mode}'"


@pytest.mark.parametrize("run_mode", ["delivery ", " delivery", "\tdelivery"])
def test_surrounding_whitespace_in_run_mode_is_tolerated(run_mode: str) -> None:
    assert isinstance(
        parse_request({"run_mode": run_mode}, clock_at(WINDOW_MOMENT)), DeliveryRequest
    )


def test_a_target_month_override_is_honoured() -> None:
    request = parse_request({"target_month": "2026-09"}, clock_at(WINDOW_MOMENT))
    assert request == DeliveryRequest(
        target=PatchDayMonth(year=2026, month=9), ignore_idempotency=False
    )


@pytest.mark.parametrize(
    "override", ["2026-13", "2026-0", "October 2026", "202610", "2026/10", "-", ""]
)
def test_an_unparsable_target_month_is_rejected_with_the_exact_message(override: str) -> None:
    request = parse_request({"target_month": override}, clock_at(WINDOW_MOMENT))
    assert isinstance(request, InvalidRequest)
    assert request.detail == f"target_month must be YYYY-MM, got '{override}'"


@pytest.mark.parametrize("override", [202610, 10, ["2026-10"], {"month": 10}])
def test_a_non_string_target_month_is_rejected(override: Any) -> None:
    request = parse_request({"target_month": override}, clock_at(WINDOW_MOMENT))
    assert isinstance(request, InvalidRequest)
    assert request.detail.startswith("target_month must be YYYY-MM, got '")


@pytest.mark.parametrize("value", [True, "yes", 1, "true"])
def test_ignore_idempotency_accepts_the_truthy_spellings(value: Any) -> None:
    request = parse_request({"ignore_idempotency": value}, clock_at(WINDOW_MOMENT))
    assert isinstance(request, DeliveryRequest)
    assert request.ignore_idempotency is True


@pytest.mark.parametrize("value", [False, "false", "FALSE", "no", "off", 0, None, ""])
def test_ignore_idempotency_defaults_to_false(value: Any) -> None:
    request = parse_request({"ignore_idempotency": value}, clock_at(WINDOW_MOMENT))
    assert isinstance(request, DeliveryRequest)
    assert request.ignore_idempotency is False


# ----------------------------------------------------------------- execute_request


def test_an_invalid_request_is_reported_and_touches_no_port() -> None:
    run = build_harness({}, WINDOW_MOMENT, state=StateSpy()).run
    result = execute_request({"run_mode": "nonsense"}, run)
    assert result == {
        "status": STATUS_INVALID_REQUEST,
        "detail": "Unsupported run_mode: 'nonsense'",
    }


def test_the_rejection_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    run = build_harness({}, WINDOW_MOMENT).run
    with caplog.at_level(logging.ERROR, logger="sap_patch_day"):
        execute_request({"run_mode": "nonsense"}, run)
    assert "rejected request: Unsupported run_mode: 'nonsense'" in caplog.text


def test_delivery_and_watchdog_are_dispatched_by_run_mode() -> None:
    delivered = execute_request({"run_mode": "delivery"}, build_harness({}, WINDOW_MOMENT).run)
    assert delivered["status"] == "not-published-yet"

    satisfied = build_harness({}, WATCHDOG_MOMENT).run
    satisfied.state.record_delivery(month="2026-10", kind="bulletin", at=WINDOW_MOMENT)
    assert execute_request({"run_mode": "watchdog"}, satisfied)["status"] == "watchdog-satisfied"


def test_every_result_is_json_serialisable_and_carries_the_status_and_month() -> None:
    for payload, moment in (
        ({"run_mode": "delivery"}, WINDOW_MOMENT),
        ({"run_mode": "watchdog"}, WATCHDOG_MOMENT),
    ):
        result = execute_request(payload, build_harness({}, moment).run)
        json.dumps(result)
        assert set(result) >= {"status", "target_month", "detail"}
        assert result["target_month"] == "2026-10"
        assert result["status"] != STATUS_INVALID_REQUEST


# ------------------------------------------------------------------ lambda_handler


def test_lambda_handler_returns_what_the_shared_logic_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = build_harness({}, WINDOW_MOMENT)
    monkeypatch.setattr("sap_patch_day.handler.build_run_context", lambda environ: harness.run)
    assert lambda_handler({"run_mode": "delivery"})["status"] == "not-published-yet"


def test_lambda_handler_never_swallows_a_patch_day_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(environ: Mapping[str, str]) -> RunContext:
        raise ConfigError(
            "No recipients configured: set the SSM parameter /sap-security-patch-day/recipients"
        )

    monkeypatch.setattr("sap_patch_day.handler.build_run_context", explode)
    with pytest.raises(ConfigError):
        lambda_handler({"run_mode": "delivery"})
    assert issubclass(ConfigError, PatchDayError)


# ---------------------------------------------------------------- build_run_context


def test_build_run_context_reads_configuration_from_the_configured_path(
    run_environment: FakeSsmClient,
) -> None:
    run_environment.parameters = {
        "/custom/path/sender": DEFAULT_CONFIG_VALUES["sender"],
        "/custom/path/recipients": DEFAULT_CONFIG_VALUES["recipients"],
    }
    run = build_run_context({"SSM_CONFIG_PATH": "/custom/path/"})
    assert run.config.sender == DEFAULT_CONFIG_VALUES["sender"]
    assert run.config.recipients == tuple(DEFAULT_CONFIG_VALUES["recipients"].split(","))
    assert run.config.config_path == "/custom/path/"
    assert run_environment.paths == ["/custom/path/"]


def test_build_run_context_uses_the_default_paths_when_the_environment_is_empty(
    run_environment: FakeSsmClient,
) -> None:
    run = build_run_context({})
    assert run.config.config_path == "/sap-security-patch-day/"
    assert run.config.state_path == "/sap-security-patch-day/state/"


def test_a_real_run_sends_through_ses_and_writes_real_state(run_environment: FakeSsmClient) -> None:
    run = build_run_context({})
    assert isinstance(run.mailer, SesMailer)
    assert isinstance(run.state, SsmStateStore)
    assert not isinstance(run.state, ReadOnlyStateStore)


def test_a_dry_run_logs_the_mail_and_refuses_to_write_state(run_environment: FakeSsmClient) -> None:
    run = build_run_context({"DRY_RUN": "true"})
    assert isinstance(run.mailer, LoggingMailer)
    assert isinstance(run.state, ReadOnlyStateStore)


def test_the_dry_run_still_reads_the_real_state(run_environment: FakeSsmClient) -> None:
    """A dry run must report "already delivered" truthfully (spec FR-3.9)."""
    run_environment.parameters.update(
        {
            "/sap-security-patch-day/state/last_delivered_month": "2026-10",
            "/sap-security-patch-day/state/last_delivered_kind": "bulletin",
        }
    )
    run = build_run_context({"DRY_RUN": "true"})
    assert run.state.read().last_delivered_month == "2026-10"
    assert run_environment.paths == ["/sap-security-patch-day/", "/sap-security-patch-day/state/"]


def test_the_log_level_from_the_environment_is_applied(run_environment: FakeSsmClient) -> None:
    build_run_context({"LOG_LEVEL": "debug"})
    assert LOGGER.level == logging.DEBUG


def test_addresses_are_masked_in_the_configuration_log_line(run_environment: FakeSsmClient) -> None:
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = Capture()
    LOGGER.addHandler(handler)
    try:
        build_run_context({})
    finally:
        LOGGER.removeHandler(handler)

    text = " ".join(records)
    assert "p***@example.test" in text
    assert "patch-day-team@example.test" not in text
    assert "sap-patch-day@example.com" not in text


def test_the_run_context_carries_a_usable_clock_and_transport(
    run_environment: FakeSsmClient,
) -> None:
    run = build_run_context({})
    assert run.clock.now().tzinfo is not None
    assert isinstance(run, RunContext)
    assert callable(run.http.get)


def test_an_invalid_environment_fails_before_any_request_is_made(
    run_environment: FakeSsmClient,
) -> None:
    with pytest.raises(ConfigError):
        build_run_context({"RETRY_WINDOW_END_SGT": "half past eight"})
    with pytest.raises(ConfigError):
        build_run_context({"HTTP_MAX_ATTEMPTS": "0"})


def test_a_missing_sender_is_a_configuration_error_not_a_crash(
    run_environment: FakeSsmClient,
) -> None:
    run_environment.parameters = {"/sap-security-patch-day/recipients": "one@example.com"}
    with pytest.raises(ConfigError, match="SES sender address is not configured"):
        build_run_context({})


def test_the_mailer_records_nothing_when_a_run_is_rejected() -> None:
    harness = build_harness({}, WINDOW_MOMENT)
    execute_request({"run_mode": "nonsense"}, harness.run)
    assert harness.recording_mailer().count == 0
    assert isinstance(harness.recording_mailer(), RecordingMailer)
