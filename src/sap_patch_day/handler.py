"""Lambda entry point and the delivery/watchdog flows (spec FR-3, §6, §7).

Everything external arrives as an injected port on :class:`RunContext`, so the
whole decision path is testable without AWS, without SAP and without mocking the
parsers (see CLAUDE-testing.md).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .clock import Clock, SystemClock, attempt_label, is_final_attempt, patch_day_month
from .config import DEFAULT_CONFIG_PATH, SsmConfigSource, load_config
from .email_render import render_bulletin_email, render_notice_email
from .errors import ParseError, PatchDayError
from .httpclient import HttpClient, UrllibHttpClient
from .mailer import LoggingMailer, Mailer, SesMailer
from .models import (
    KIND_BULLETIN,
    KIND_PENDING,
    NOTICE_PENDING,
    NOTICE_UNVERIFIED,
    NOTICE_WATCHDOG,
    Bulletin,
    EmailContent,
    Notice,
    PatchDayMonth,
    PublicationCheck,
    RuntimeConfig,
    SendRequest,
    StateSnapshot,
    mask_address,
)
from .probe import ProbeRequest, check_publication
from .state import ReadOnlyStateStore, SsmStateStore, StateStore, bulletin_delivered_for

LOGGER = logging.getLogger("sap_patch_day")
STATUS_INVALID_REQUEST = "invalid-request"


@dataclass(frozen=True)
class RunContext:
    """Everything a run needs, as one parameter object."""

    config: RuntimeConfig
    http: HttpClient
    mailer: Mailer
    state: StateStore
    clock: Clock


@dataclass(frozen=True)
class DeliveryRequest:
    """A delivery run for one patch-day month."""

    target: PatchDayMonth
    ignore_idempotency: bool = False


@dataclass(frozen=True)
class WatchdogRequest:
    """The second-Wednesday check that something was actually delivered."""

    target: PatchDayMonth


@dataclass(frozen=True)
class InvalidRequest:
    """A payload that cannot be honoured (spec §6)."""

    detail: str


Request = DeliveryRequest | WatchdogRequest | InvalidRequest


def lambda_handler(event: Mapping[str, Any], _context: object = None) -> dict[str, Any]:
    """AWS entry point: build the real ports, then run the shared logic."""
    try:
        return execute_request(event, build_run_context(os.environ))
    except PatchDayError as error:
        LOGGER.error("run failed: %s", error)
        raise


def build_run_context(environ: Mapping[str, str]) -> RunContext:
    """Assemble the production ports from the environment and SSM."""
    config_path = environ.get("SSM_CONFIG_PATH", DEFAULT_CONFIG_PATH)
    values = SsmConfigSource(_aws_client("ssm"), config_path).read()
    config = load_config(values, environ)
    _configure_logging(config)
    LOGGER.info(
        "configuration loaded: %d recipient(s) %s, sender %s, retry window ends %s SGT",
        len(config.recipients),
        ", ".join(config.masked_recipients),
        mask_address(config.sender),
        config.retry_window_end.strftime("%H:%M"),
    )
    state: StateStore = SsmStateStore(_aws_client("ssm"), config.state_path)
    return RunContext(
        config=config,
        http=UrllibHttpClient(config.http),
        mailer=LoggingMailer(LOGGER)
        if config.dry_run
        else SesMailer(_aws_client("sesv2"), config.configuration_set),
        state=ReadOnlyStateStore(state, LOGGER) if config.dry_run else state,
        clock=SystemClock(),
    )


def execute_request(event: Mapping[str, Any], run: RunContext) -> dict[str, Any]:
    """Validate the payload and dispatch (spec §6)."""
    request = parse_request(event, run.clock)
    if isinstance(request, InvalidRequest):
        LOGGER.error("rejected request: %s", request.detail)
        return {"status": STATUS_INVALID_REQUEST, "detail": request.detail}
    if isinstance(request, WatchdogRequest):
        return run_watchdog(request, run)
    return run_delivery(request, run)


def parse_request(event: Mapping[str, Any], clock: Clock) -> Request:
    """Turn an EventBridge payload into a request object."""
    payload = event if isinstance(event, Mapping) else {}
    mode = str(payload.get("run_mode", "delivery")).strip() or "delivery"
    if mode == "watchdog":
        return WatchdogRequest(target=patch_day_month(clock.now()))
    if mode != "delivery":
        return InvalidRequest(f"Unsupported run_mode: '{mode}'")
    return _delivery_request(payload, clock)


def _delivery_request(payload: Mapping[str, Any], clock: Clock) -> Request:
    override = payload.get("target_month")
    if override is None:
        target = patch_day_month(clock.now())
    else:
        try:
            target = PatchDayMonth.parse(str(override))
        except ValueError:
            return InvalidRequest(f"target_month must be YYYY-MM, got '{override}'")
    return DeliveryRequest(
        target=target,
        ignore_idempotency=_payload_flag(payload.get("ignore_idempotency", False)),
    )


def _payload_flag(value: Any) -> bool:
    """A payload boolean, tolerating the strings a CLI-invoked payload carries.

    `aws lambda invoke --payload '{"ignore_idempotency":"false"}'` arrives as the
    *string* "false", and `bool("false")` is true — which would silently resend.
    """
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "0", "no", "off", ""}
    return bool(value)


def run_delivery(request: DeliveryRequest, run: RunContext) -> dict[str, Any]:
    """One attempt at delivering the month's bulletin (spec §5)."""
    run.state.record_attempt(run.clock.now())
    snapshot = run.state.read()
    if _already_delivered(request, snapshot):
        return _result(
            "already-delivered", request.target, "this month's notes have already been emailed"
        )
    check = check_publication(_probe_request(request, run), run.http)
    LOGGER.info(
        "probe %s: outcome=%s http=%s link_transitioned=%s detail=%s",
        request.target.label,
        check.outcome,
        check.http_status,
        check.link_transitioned,
        check.detail or "-",
    )
    if check.bulletin is not None:
        return _deliver(check, request, run)
    if check.is_unverified:
        return _handle_unverified(request, check, run)
    return _handle_unpublished(request, check, run)


def run_watchdog(request: WatchdogRequest, run: RunContext) -> dict[str, Any]:
    """Second-Wednesday check that the month's notes were actually delivered (FR-3.8)."""
    run.state.record_attempt(run.clock.now())
    snapshot = run.state.read()
    if bulletin_delivered_for(request.target.label, snapshot):
        return _result(
            "watchdog-satisfied", request.target, "the bulletin for this month was delivered"
        )
    if snapshot.last_watchdog_alert_month == request.target.label:
        return _result(
            "watchdog-already-alerted", request.target, "an alert for this month was already sent"
        )
    notice = Notice(
        kind=NOTICE_WATCHDOG,
        target=request.target,
        observed_at=run.clock.now(),
        detail=_watchdog_detail(snapshot),
    )
    return _send_notice(notice, "watchdog-alert-sent", run)


def _deliver(check: PublicationCheck, request: DeliveryRequest, run: RunContext) -> dict[str, Any]:
    bulletin = _published_bulletin(check)
    message_id = _send_bulletin(bulletin, run)
    run.state.record_delivery(month=request.target.label, kind=KIND_BULLETIN, at=run.clock.now())
    LOGGER.info(
        "delivered %s: %d notes, message id %s",
        request.target.label,
        len(bulletin.notes),
        message_id,
    )
    return _result("delivered", request.target, _bypass_detail(request)) | {
        "canonical_url": check.canonical_url,
        "http_status": check.http_status,
        "link_transitioned": check.link_transitioned,
        "note_count": len(bulletin.notes),
        "message_id": message_id,
        "attempt": _attempt(run),
    }


def _published_bulletin(check: PublicationCheck) -> Bulletin:
    if check.bulletin is None:
        raise ParseError("publication reported as published without a parsed bulletin")
    return check.bulletin


def _handle_unpublished(
    request: DeliveryRequest, check: PublicationCheck, run: RunContext
) -> dict[str, Any]:
    if not _is_final(run):
        return _result(
            "not-published-yet",
            request.target,
            check.detail or "SAP has not published this month's notes",
        ) | _probe_fields(check)
    if not run.config.notify_when_not_published:
        return _result(
            "not-published-yet", request.target, "final attempt; notices are disabled"
        ) | _probe_fields(check)
    notice = Notice(
        kind=NOTICE_PENDING,
        target=request.target,
        observed_at=run.clock.now(),
        detail=(
            "The bulletin was still not published when the retry window closed "
            f"({check.detail or 'SAP answered 404'})."
        ),
    )
    return _send_notice(notice, "pending-notice-sent", run) | _probe_fields(check)


def _handle_unverified(
    request: DeliveryRequest, check: PublicationCheck, run: RunContext
) -> dict[str, Any]:
    if not _is_final(run):
        return _result(
            "attempt-failed", request.target, check.detail or "SAP could not be reached"
        ) | _probe_fields(check)
    if not run.config.notify_when_not_published:
        return _result(
            "attempt-failed", request.target, "final attempt failed; notices are disabled"
        ) | _probe_fields(check)
    notice = Notice(
        kind=NOTICE_UNVERIFIED,
        target=request.target,
        observed_at=run.clock.now(),
        detail=f"Publication could not be verified ({check.detail or 'SAP could not be reached'}).",
    )
    return _send_notice(notice, "unverified-notice-sent", run) | _probe_fields(check)


def _send_bulletin(bulletin: Bulletin, run: RunContext) -> str:
    content = render_bulletin_email(bulletin, run.config)
    return run.mailer.send(_send_request(content, run.config))


def _send_notice(notice: Notice, status: str, run: RunContext) -> dict[str, Any]:
    content = render_notice_email(notice, run.config)
    message_id = run.mailer.send(_send_request(content, run.config))
    _record_notice(notice, run)
    LOGGER.info(
        "sent %s notice for %s, message id %s", notice.kind, notice.target.label, message_id
    )
    return _result(status, notice.target, notice.detail) | {
        "message_id": message_id,
        "attempt": _attempt(run),
    }


def _record_notice(notice: Notice, run: RunContext) -> None:
    if notice.kind == NOTICE_WATCHDOG:
        run.state.record_watchdog_alert(month=notice.target.label, at=run.clock.now())
        return
    run.state.record_delivery(month=notice.target.label, kind=KIND_PENDING, at=run.clock.now())


def _send_request(content: EmailContent, config: RuntimeConfig) -> SendRequest:
    return SendRequest(
        recipients=config.recipients,
        sender=config.sender,
        reply_to=config.reply_to,
        content=content,
    )


def _probe_request(request: DeliveryRequest, run: RunContext) -> ProbeRequest:
    return ProbeRequest(target=request.target, observed_at=run.clock.now())


def _already_delivered(request: DeliveryRequest, snapshot: StateSnapshot) -> bool:
    if request.ignore_idempotency:
        return False
    return bulletin_delivered_for(request.target.label, snapshot)


def _is_final(run: RunContext) -> bool:
    return is_final_attempt(run.clock.now(), run.config.retry_window_end)


def _attempt(run: RunContext) -> str:
    return attempt_label(run.clock.now(), run.config.retry_window_end)


def _bypass_detail(request: DeliveryRequest) -> str:
    if request.ignore_idempotency:
        return "idempotency_bypassed"
    return ""


def _watchdog_detail(snapshot: StateSnapshot) -> str:
    if snapshot.last_delivered_month in {"", "-"}:
        return "No security notes have been delivered by this pipeline yet."
    return (
        f"The most recent delivery was {snapshot.last_delivered_month} "
        f"({snapshot.last_delivered_kind})."
    )


def _probe_fields(check: PublicationCheck) -> dict[str, Any]:
    return {
        "canonical_url": check.canonical_url,
        "http_status": check.http_status,
        "link_transitioned": check.link_transitioned,
    }


def _result(status: str, target: PatchDayMonth, detail: str = "") -> dict[str, Any]:
    return {"status": status, "target_month": target.label, "detail": detail}


def _configure_logging(config: RuntimeConfig) -> None:
    logging.getLogger("sap_patch_day").setLevel(getattr(logging, config.log_level, logging.INFO))


def _aws_client(service: str) -> Any:
    import boto3  # noqa: PLC0415 - imported lazily: the runtime provides it, tests need not

    return boto3.client(service)
