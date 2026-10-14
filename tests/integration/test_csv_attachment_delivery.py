"""The CSV attachment, end to end: parsed page -> rendered bytes -> SES call.

`attach_csv` is an SSM flag (spec FR-5.5), so switching it on must change what
Amazon SES receives and nothing else: the same subject and recipients, the same
bodies, the same state written afterwards. The unit tests check the MIME builder
and the CSV renderer separately; this file runs the whole handler, so a break in
the wiring between them (config -> renderer -> mailer -> SES payload) fails here
rather than in production.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from email import message_from_bytes, policy as email_policy
from email.message import EmailMessage, Message

import pytest

from sap_patch_day.errors import DeliveryError
from sap_patch_day.handler import execute_request
from sap_patch_day.mailer import SesMailer
from sap_patch_day.models import KIND_BULLETIN
from tests.support.harness import build_harness, delivered_state
from tests.support.pages import page_map
from tests.unit.test_mailer import RecordingSesClient

ATTACHMENT_CONFIG = {"attach_csv": "true"}
EXPECTED_SUBJECT = (
    "[SAP Security Patch Day] October 2026 — 21 notes (4 critical, 2 high, 14 medium, 1 low)"
)
EXPECTED_FILENAME = "sap-security-notes-2026-10.csv"
EXPECTED_COLUMNS = [
    "note_id",
    "source",
    "priority",
    "cvss_score",
    "cvss_vector",
    "cve_ids",
    "product",
    "versions",
    "is_update",
    "headline",
    "note_url",
]
COLUMN = {name: index for index, name in enumerate(EXPECTED_COLUMNS)}


def attached_run(moment: datetime) -> tuple[dict[str, object], RecordingSesClient]:
    client = RecordingSesClient()
    harness = build_harness(
        page_map(),
        moment,
        config_values=ATTACHMENT_CONFIG,
        mailer=SesMailer(client),
    )
    result = execute_request({"run_mode": "delivery"}, harness.run)
    return result, client


def raw_message(call: dict[str, object]) -> Message:
    content = call["Content"]
    assert isinstance(content, dict) and set(content) == {"Raw"}, "an attachment must be raw MIME"
    data = content["Raw"]["Data"]
    assert isinstance(data, bytes)
    # `policy.default` decodes the RFC 2047 encoded words the em dash forces.
    return message_from_bytes(data, policy=email_policy.default)


def csv_rows(message: Message) -> list[list[str]]:
    parts = [part for part in message.walk() if part.get_filename() == EXPECTED_FILENAME]
    assert len(parts) == 1, f"expected exactly one {EXPECTED_FILENAME} part"
    payload = parts[0].get_payload(decode=True)
    assert isinstance(payload, bytes)
    return list(csv.reader(io.StringIO(payload.decode("utf-8"))))


def test_a_csv_attachment_rides_along_with_the_same_message(window_moment: datetime) -> None:
    result, client = attached_run(window_moment)

    assert result["status"] == "delivered"
    assert result["note_count"] == 21
    message = raw_message(client.calls[0])

    # The reader-facing message is unchanged. The subject is the same *string* as
    # the simple path sends; only the raw MIME transport encodes the em dash.
    assert str(message["Subject"]) == EXPECTED_SUBJECT
    assert message["From"] == "sap-patch-day@example.com"
    assert "\n" not in str(message["Subject"]) and "<" not in str(message["From"])
    assert message["To"] == "patch-day-team@example.test, security-ops@example.test"

    # Both bodies are still there, so the attachment is an addition, not a swap.
    assert {"text/plain", "text/html"} <= {part.get_content_type() for part in message.walk()}
    html = [part for part in message.walk() if part.get_content_type() == "text/html"]
    assert len(html) == 1
    assert "Note#" in html[0].get_content() and "3767634" in html[0].get_content()


def test_the_attachment_holds_every_parsed_note_and_nothing_else(
    window_moment: datetime,
) -> None:
    _, client = attached_run(window_moment)
    rows = csv_rows(raw_message(client.calls[0]))

    assert rows[0] == EXPECTED_COLUMNS
    data = rows[1:]
    assert len(data) == 21, "one row per note, and no summary row"
    ids = [row[COLUMN["note_id"]] for row in data]
    assert len(set(ids)) == len(ids), "no duplicated note"
    assert ids[0] == "GHSA-4mv3-jxxm-9cx2"
    assert ids[-1] == "3767634"

    assert len([row for row in data if row[COLUMN["priority"]].lower() == "critical"]) == 4
    updates = [row for row in data if row[COLUMN["is_update"]] == "true"]
    assert {row[COLUMN["note_id"]] for row in updates} == {"3689080", "3629325"}

    # The first row is the GitHub advisory from the live page: its score, CVE
    # reference and link have to survive the parser -> CSV trip intact.
    first = data[0]
    assert first[COLUMN["source"]] == "github"
    assert first[COLUMN["cvss_score"]] == "10.0"
    assert first[COLUMN["headline"]].startswith("OAuth2 SSRF vulnerability")
    assert first[COLUMN["note_url"]].startswith("https://")
    assert all(row[COLUMN["headline"]] for row in data), "no note without a headline"


def test_the_attachment_is_utf8_and_spreadsheet_safe(window_moment: datetime) -> None:
    _, client = attached_run(window_moment)
    part = next(
        part
        for part in raw_message(client.calls[0]).walk()
        if part.get_filename() == EXPECTED_FILENAME
    )
    assert part.get_content_type() == "text/csv"
    payload = part.get_payload(decode=True)
    assert isinstance(payload, bytes)
    # Decodes as UTF-8 (the pages carry SAP's typographic dashes), and no cell
    # starts with a character a spreadsheet would treat as a formula.
    for row in csv.reader(io.StringIO(payload.decode("utf-8"))):
        for cell in row:
            assert not cell.startswith(("=", "+", "-@")), f"formula-looking cell: {cell!r}"


def test_a_delivery_with_an_attachment_writes_the_same_state(window_moment: datetime) -> None:
    client = RecordingSesClient()
    harness = build_harness(
        page_map(),
        window_moment,
        config_values=ATTACHMENT_CONFIG,
        mailer=SesMailer(client),
    )
    execute_request({"run_mode": "delivery"}, harness.run)

    snapshot = harness.in_memory_state().snapshot
    assert (snapshot.last_delivered_month, snapshot.last_delivered_kind) == (
        "2026-10",
        KIND_BULLETIN,
    )

    # And the month is now idempotent, attachment or not.
    again = execute_request({"run_mode": "delivery"}, harness.run)
    assert again["status"] == "already-delivered"
    assert len(client.calls) == 1


def test_the_result_never_carries_the_csv_or_the_addresses(window_moment: datetime) -> None:
    result, _ = attached_run(window_moment)
    blob = json.dumps(result)
    assert "patch-day-team@example.test" not in blob
    assert "note_id" not in blob
    assert result["message_id"].startswith("raw-id-")


def test_a_send_without_a_message_id_is_still_a_delivery(window_moment: datetime) -> None:
    """SES accepted the message but named it nothing.

    The email has left the building, so reporting a failure would make the next
    attempt send the whole bulletin a second time. The empty id is reported as
    it is, and the month is recorded as delivered.
    """

    class AnonymousSesClient(RecordingSesClient):
        def send_email(self, **kwargs: object) -> dict[str, object]:
            super().send_email(**kwargs)
            return {}

    harness = build_harness(
        page_map(),
        window_moment,
        config_values=ATTACHMENT_CONFIG,
        mailer=SesMailer(AnonymousSesClient()),
        state=delivered_state("2026-09"),
    )
    result = execute_request({"run_mode": "delivery"}, harness.run)

    assert result["status"] == "delivered"
    assert result["message_id"] == ""
    assert harness.in_memory_state().snapshot.last_delivered_month == "2026-10"

    again = execute_request({"run_mode": "delivery"}, harness.run)
    assert again["status"] == "already-delivered", "an unnamed message must not be resent"


def test_an_ses_rejection_raises_and_owes_the_month(window_moment: datetime) -> None:
    """An SES refusal is an error, not a quiet result (spec FR-5.7).

    It propagates so the Lambda invocation is recorded as failed and the alarm
    fires; no state is written, so the month is still owed an email.
    """

    class RejectingSesClient(RecordingSesClient):
        def send_email(self, **kwargs: object) -> dict[str, object]:
            raise RuntimeError("Email address is not verified")

    state = delivered_state("2026-09")
    harness = build_harness(
        page_map(),
        window_moment,
        config_values=ATTACHMENT_CONFIG,
        mailer=SesMailer(RejectingSesClient()),
        state=state,
    )
    with pytest.raises(DeliveryError) as raised:
        execute_request({"run_mode": "delivery"}, harness.run)

    assert "not verified" in str(raised.value)
    assert state.snapshot.last_delivered_month == "2026-09"
    assert state.snapshot.last_attempt_at != "-"


def test_the_raw_message_is_a_well_formed_email() -> None:
    """A cheap structural check on the builder the attachment path depends on."""
    message = EmailMessage(policy=email_policy.SMTP)
    message["From"] = "a@example.com"
    message["To"] = "b@example.test"
    message["Subject"] = EXPECTED_SUBJECT
    message.set_content("text")
    message.add_alternative("<p>html</p>", subtype="html")
    round_tripped = message_from_bytes(message.as_bytes(), policy=email_policy.default)
    assert str(round_tripped["Subject"]) == EXPECTED_SUBJECT
    assert round_tripped.get_content_type() == "multipart/alternative"
