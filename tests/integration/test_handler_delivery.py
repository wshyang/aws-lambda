"""End-to-end delivery flows over injected ports (spec §5, §6, §7).

Every test here drives the real ``execute_request`` path: real parser, real
renderer, real decision logic. Only the transport, the mailer and the state
store are substituted.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

import pytest

from sap_patch_day.errors import DeliveryError
from sap_patch_day.handler import execute_request
from sap_patch_day.models import KIND_BULLETIN, KIND_PENDING
from sap_patch_day.probe import INDEX_URL, canonical_url
from sap_patch_day.state import InMemoryStateStore
from tests.support.fakes import UnreachableHttpClient
from tests.support.harness import Harness, build_harness, delivered_state
from tests.support.pages import OCTOBER_2026, fixture, page_map

TARGET = OCTOBER_2026
CANONICAL = canonical_url(TARGET)


def index_page(linked: bool) -> str:
    entry = (
        '<p><b><a href="/en/my-support/knowledge-base/security-notes-news/october-2026.html">'
        "October 13</a></b></p>"
        if linked
        else "<p><b>October 13</b></p>"
    )
    return f"<h2>Security Notes</h2><h4>Dates for 2026</h4><div>{entry}</div>"


def published_pages(index: str | None = None) -> Mapping[str, str]:
    pages = {CANONICAL: fixture("october_2026.html")}
    if index is not None:
        pages[INDEX_URL] = index
    return pages


def delivery_event(**extra: object) -> Mapping[str, object]:
    return {"run_mode": "delivery", **extra}


# --------------------------------------------------------------------------
# The happy path, and the rule that must never regress
# --------------------------------------------------------------------------


def test_publishes_the_bulletin_with_all_notes_and_recipients(
    window_moment: datetime,
) -> None:
    harness = build_harness(published_pages(index_page(linked=True)), window_moment)
    result = execute_request(delivery_event(), harness.run)

    assert result["status"] == "delivered"
    assert result["target_month"] == "2026-10"
    assert result["note_count"] == 21
    assert result["http_status"] == 200
    assert result["link_transitioned"] is True
    assert result["attempt"] == "window"
    assert result["message_id"] == "test-message-1"

    sent = harness.recording_mailer().last
    assert sent.recipients == ("patch-day-team@example.test", "security-ops@example.test")
    assert sent.sender == "sap-patch-day@example.com"
    assert sent.content.subject.startswith("[SAP Security Patch Day] October 2026")
    for note_id in ("GHSA-4mv3-jxxm-9cx2", "3797111", "3767634"):
        assert note_id in sent.content.html_body
        assert note_id in sent.content.text_body


def test_index_not_updated_but_live_url_is_delivered(window_moment: datetime) -> None:
    """The operator's scenario: the index says plain text, the page is live."""
    harness = build_harness(published_pages(index_page(linked=False)), window_moment)
    result = execute_request(delivery_event(), harness.run)
    assert result["status"] == "delivered"
    assert result["link_transitioned"] is False
    assert harness.recording_mailer().count == 1


def test_unreadable_index_page_does_not_block_delivery(window_moment: datetime) -> None:
    harness = build_harness({CANONICAL: fixture("october_2026.html")}, window_moment)
    result = execute_request(delivery_event(), harness.run)
    assert result["status"] == "delivered"
    assert result["link_transitioned"] is None


def test_delivery_records_state_and_attempt_timestamps(window_moment: datetime) -> None:
    harness = build_harness(published_pages(index_page(linked=True)), window_moment)
    execute_request(delivery_event(), harness.run)
    snapshot = harness.in_memory_state().read()
    assert snapshot.last_delivered_month == "2026-10"
    assert snapshot.last_delivered_kind == KIND_BULLETIN
    assert snapshot.last_delivered_at == "2026-10-13T08:05:00+00:00"
    assert snapshot.last_attempt_at == "2026-10-13T08:05:00+00:00"


# --------------------------------------------------------------------------
# Not published, and unknown
# --------------------------------------------------------------------------


def test_not_published_during_the_window_sends_nothing(window_moment: datetime) -> None:
    harness = build_harness({INDEX_URL: index_page(linked=False)}, window_moment)
    result = execute_request(delivery_event(), harness.run)
    assert result["status"] == "not-published-yet"
    assert result["http_status"] == 404
    assert harness.recording_mailer().count == 0
    assert harness.in_memory_state().read().last_attempt_at == "2026-10-13T08:05:00+00:00"


def test_not_published_at_the_final_attempt_sends_one_notice(final_moment: datetime) -> None:
    harness = build_harness({INDEX_URL: index_page(linked=False)}, final_moment)
    result = execute_request(delivery_event(), harness.run)
    assert result["status"] == "pending-notice-sent"
    assert result["attempt"] == "final"
    sent = harness.recording_mailer().last
    assert "notes not published yet" in sent.content.subject
    assert harness.in_memory_state().read().last_delivered_kind == KIND_PENDING


def test_final_attempt_notice_can_be_disabled(final_moment: datetime) -> None:
    harness = build_harness(
        {INDEX_URL: index_page(linked=False)},
        final_moment,
        config_values={"notify_when_not_published": "false"},
    )
    result = execute_request(delivery_event(), harness.run)
    assert result["status"] == "not-published-yet"
    assert "notices are disabled" in result["detail"]
    assert harness.recording_mailer().count == 0


def test_unreachable_sap_during_the_window_does_not_raise(window_moment: datetime) -> None:
    harness = build_harness({}, window_moment, http=UnreachableHttpClient())
    result = execute_request(delivery_event(), harness.run)
    assert result["status"] == "attempt-failed"
    assert result["http_status"] is None
    assert harness.recording_mailer().count == 0
    assert harness.in_memory_state().read().last_attempt_at != "-"


def test_unreachable_sap_at_the_final_attempt_warns_that_it_is_unknown(
    final_moment: datetime,
) -> None:
    harness = build_harness({CANONICAL: fixture("stale_page.html")}, final_moment)
    result = execute_request(delivery_event(), harness.run)
    assert result["status"] == "unverified-notice-sent"
    sent = harness.recording_mailer().last
    assert "could not be verified" in sent.content.subject
    assert "does not mean that no notes were published" in sent.content.text_body


def test_markup_change_is_reported_as_unverified_not_as_absence(
    final_moment: datetime,
) -> None:
    harness = build_harness({CANONICAL: fixture("no_table.html")}, final_moment)
    result = execute_request(delivery_event(), harness.run)
    assert result["status"] == "unverified-notice-sent"
    assert "could not be verified" in harness.recording_mailer().last.content.subject


# --------------------------------------------------------------------------
# Idempotency (spec FR-3.1, FR-3.6, §5)
# --------------------------------------------------------------------------


def test_a_second_run_in_the_same_month_does_not_resend(window_moment: datetime) -> None:
    harness = build_harness(published_pages(index_page(linked=True)), window_moment)
    execute_request(delivery_event(), harness.run)
    second = execute_request(delivery_event(), harness.run)
    assert second["status"] == "already-delivered"
    assert harness.recording_mailer().count == 1


def test_idempotency_can_be_bypassed_on_purpose(window_moment: datetime) -> None:
    state = delivered_state("2026-10")
    harness = build_harness(published_pages(index_page(linked=True)), window_moment, state=state)
    result = execute_request(delivery_event(ignore_idempotency=True), harness.run)
    assert result["status"] == "delivered"
    assert result["detail"] == "idempotency_bypassed"
    assert harness.recording_mailer().count == 1


def test_a_pending_notice_does_not_suppress_the_later_bulletin(final_moment: datetime) -> None:
    """FR-3.6: "not published yet" and "here are the notes" are different states."""
    state = InMemoryStateStore()
    state.record_delivery(month="2026-10", kind=KIND_PENDING, at=final_moment)
    harness = build_harness(published_pages(index_page(linked=True)), final_moment, state=state)
    result = execute_request(delivery_event(), harness.run)
    assert result["status"] == "delivered"
    assert harness.in_memory_state().read().last_delivered_kind == KIND_BULLETIN


# --------------------------------------------------------------------------
# Failure handling
# --------------------------------------------------------------------------


def test_ses_failure_propagates_and_writes_no_state(window_moment: datetime) -> None:
    from tests.support.fakes import ExplodingMailer

    harness = build_harness(
        published_pages(index_page(linked=True)), window_moment, mailer=ExplodingMailer()
    )
    with pytest.raises(DeliveryError):
        execute_request(delivery_event(), harness.run)
    snapshot = harness.in_memory_state().read()
    assert snapshot.last_delivered_month == "-"
    assert snapshot.last_attempt_at != "-"


# --------------------------------------------------------------------------
# Payload contract (spec §6, §8)
# --------------------------------------------------------------------------


def test_unknown_run_mode_is_rejected(window_moment: datetime) -> None:
    harness = build_harness(published_pages(index_page(linked=True)), window_moment)
    result = execute_request({"run_mode": "sabbatical"}, harness.run)
    assert result == {"status": "invalid-request", "detail": "Unsupported run_mode: 'sabbatical'"}
    assert harness.recording_mailer().count == 0


def test_bad_target_month_is_rejected_with_the_documented_message(window_moment: datetime) -> None:
    harness = build_harness(published_pages(index_page(linked=True)), window_moment)
    result = execute_request(delivery_event(target_month="October"), harness.run)
    assert result == {
        "status": "invalid-request",
        "detail": "target_month must be YYYY-MM, got 'October'",
    }


def test_empty_payload_defaults_to_the_current_patch_day_month(window_moment: datetime) -> None:
    harness = build_harness(published_pages(index_page(linked=True)), window_moment)
    result = execute_request({}, harness.run)
    assert result["status"] == "delivered"
    assert result["target_month"] == "2026-10"


def test_target_month_override_probes_that_month(window_moment: datetime) -> None:
    harness = build_harness(published_pages(index_page(linked=True)), window_moment)
    result = execute_request(delivery_event(target_month="2026-09"), harness.run)
    assert result["status"] == "not-published-yet"
    assert result["target_month"] == "2026-09"
    assert "september-2026.html" in result["canonical_url"]


def test_result_contract_is_json_serialisable(window_moment: datetime) -> None:
    import json

    harness = build_harness(published_pages(index_page(linked=True)), window_moment)
    result = execute_request(delivery_event(), harness.run)
    assert json.loads(json.dumps(result))["status"] == "delivered"


def test_dry_run_sends_nothing_and_writes_no_state(window_moment: datetime) -> None:
    """``DRY_RUN`` is exercised through the ports the handler assembles."""
    from sap_patch_day.clock import FixedClock
    from sap_patch_day.config import MappingConfigSource, load_config
    from sap_patch_day.handler import RunContext
    from sap_patch_day.httpclient import StaticHttpClient
    from sap_patch_day.mailer import LoggingMailer
    from sap_patch_day.state import ReadOnlyStateStore

    config = load_config(
        MappingConfigSource(
            {"sender": "sap-patch-day@example.com", "recipients": "one@example.test"}
        ).read(),
        {"DRY_RUN": "true"},
    )
    assert config.dry_run is True
    inner = InMemoryStateStore()
    run = RunContext(
        config=config,
        http=StaticHttpClient(published_pages(index_page(linked=True))),
        mailer=LoggingMailer(),
        state=ReadOnlyStateStore(inner),
        clock=FixedClock(window_moment),
    )
    result = execute_request(delivery_event(), run)
    assert result["status"] == "delivered"
    assert result["message_id"] == "dry-run:no-message-id"
    assert inner.read().last_delivered_month == "-"


def test_harness_helper_builds_the_documented_defaults(harness: Harness) -> None:
    assert harness.run.config.sender == "sap-patch-day@example.com"
    assert harness.run.config.recipients == (
        "patch-day-team@example.test",
        "security-ops@example.test",
    )
    assert harness.run.config.masked_recipients == (
        "p***@example.test",
        "s***@example.test",
    )


def test_page_map_covers_both_urls() -> None:
    assert set(page_map()) == {CANONICAL, INDEX_URL}
