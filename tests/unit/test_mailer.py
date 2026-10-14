"""The Amazon SES adapter: request shape validated by botocore's service model.

``botocore.stub.Stubber`` rejects a request whose parameter names or types do not
match the SES v2 model, so these tests catch exactly the class of bug that would
otherwise surface at 16:00 on patch day (spec FR-5, addendum 0007).
"""

from __future__ import annotations

import logging
from typing import Any

import boto3
import pytest
from botocore.client import BaseClient
from botocore.config import Config
from botocore.stub import Stubber

from sap_patch_day.errors import DeliveryError
from sap_patch_day.mailer import LoggingMailer, SesMailer, build_payload
from sap_patch_day.models import Attachment, EmailContent, SendRequest

REGION = "ap-southeast-1"
CONFIGURATION_SET = "sap-security-patch-day"
CSV_ATTACHMENT = Attachment(
    filename="sap-security-notes-2026-10.csv",
    content_type="text/csv; charset=utf-8",
    data=b"note_id\n3797111\n",
)


def ses_client() -> BaseClient:
    return boto3.client(
        "sesv2",
        region_name=REGION,
        aws_access_key_id="test-key",
        aws_secret_access_key="test-secret",
        config=Config(retries={"max_attempts": 0}),
    )


def request_with(
    *,
    reply_to: str = "reply@example.com",
    attachment: Attachment | None = None,
) -> SendRequest:
    return SendRequest(
        recipients=("one@example.test", "two@example.test"),
        sender="sap-patch-day@example.com",
        reply_to=reply_to,
        content=EmailContent(
            subject="subject line",
            html_body="<p>html</p>",
            text_body="text",
            attachment=attachment,
        ),
    )


def simple_params(*, with_reply_to: bool, with_configuration_set: bool) -> dict[str, Any]:
    params: dict[str, Any] = {
        "FromEmailAddress": "sap-patch-day@example.com",
        "Destination": {"ToAddresses": ["one@example.test", "two@example.test"]},
        "Content": {
            "Simple": {
                "Subject": {"Data": "subject line", "Charset": "UTF-8"},
                "Body": {
                    "Html": {"Data": "<p>html</p>", "Charset": "UTF-8"},
                    "Text": {"Data": "text", "Charset": "UTF-8"},
                },
            }
        },
    }
    if with_reply_to:
        params["ReplyToAddresses"] = ["reply@example.com"]
    if with_configuration_set:
        params["ConfigurationSetName"] = CONFIGURATION_SET
    return params


def test_simple_message_matches_the_ses_model() -> None:
    client = ses_client()
    stubber = Stubber(client)
    stubber.add_response(
        "send_email",
        {"MessageId": "0100018f-abc"},
        simple_params(with_reply_to=True, with_configuration_set=True),
    )
    with stubber:
        message_id = SesMailer(client, CONFIGURATION_SET).send(request_with())
    assert message_id == "0100018f-abc"


def test_empty_reply_to_and_configuration_set_are_omitted() -> None:
    client = ses_client()
    stubber = Stubber(client)
    stubber.add_response(
        "send_email",
        {"MessageId": "id"},
        simple_params(with_reply_to=False, with_configuration_set=False),
    )
    with stubber:
        assert SesMailer(client, "").send(request_with(reply_to="")) == "id"


class RecordingSesClient:
    """A real implementation of the one SES call the mailer makes.

    Used where the message body is not byte-identical between calls (MIME
    boundaries are random), so ``Stubber``'s exact parameter match cannot apply.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send_email(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"MessageId": f"raw-id-{len(self.calls)}"}


def test_raw_payload_with_attachment_is_accepted_by_the_ses_model() -> None:
    payload = build_payload(request_with(attachment=CSV_ATTACHMENT), CONFIGURATION_SET)
    raw = payload["Content"]["Raw"]["Data"]
    assert isinstance(raw, bytes)
    assert b'Content-Type: text/csv; charset="utf-8"' in raw
    assert b'Content-Disposition: attachment; filename="sap-security-notes-2026-10.csv"' in raw
    assert b"Content-Type: text/html" in raw
    assert payload["ConfigurationSetName"] == CONFIGURATION_SET

    client = ses_client()
    stubber = Stubber(client)
    stubber.add_response("send_email", {"MessageId": "raw-id"}, payload)
    with stubber:
        assert client.send_email(**payload)["MessageId"] == "raw-id"


def test_attachment_request_is_sent_as_raw_and_returns_the_message_id() -> None:
    client = RecordingSesClient()
    message_id = SesMailer(client, CONFIGURATION_SET).send(request_with(attachment=CSV_ATTACHMENT))
    assert message_id == "raw-id-1"
    assert set(client.calls[0]["Content"]) == {"Raw"}
    assert "Simple" not in client.calls[0]["Content"]


def test_missing_message_id_is_reported_as_an_empty_string() -> None:
    client = ses_client()
    stubber = Stubber(client)
    stubber.add_response(
        "send_email", {}, simple_params(with_reply_to=False, with_configuration_set=False)
    )
    with stubber:
        assert SesMailer(client, "").send(request_with(reply_to="")) == ""


def test_ses_failure_becomes_a_delivery_error() -> None:
    client = ses_client()
    stubber = Stubber(client)
    stubber.add_client_error("send_email", service_error_code="Throttling", http_status_code=429)
    with stubber, pytest.raises(DeliveryError) as error:
        SesMailer(client, "").send(request_with())
    assert "Amazon SES rejected the message" in str(error.value)


def test_logging_mailer_masks_recipients_and_sends_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="sap_patch_day.mailer")
    message_id = LoggingMailer(logging.getLogger("sap_patch_day.mailer")).send(
        request_with(attachment=CSV_ATTACHMENT)
    )
    assert message_id == "dry-run:no-message-id"
    assert "o***@example.test" in caplog.text
    assert "one@example.test" not in caplog.text
    assert "sap-security-notes-2026-10.csv" in caplog.text


def test_logging_mailer_reports_no_attachment(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="sap_patch_day.mailer")
    LoggingMailer(logging.getLogger("sap_patch_day.mailer")).send(request_with())
    assert "attachment: none" in caplog.text
