"""Sending the message, behind a port (spec FR-5).

Two implementations: the real Amazon SES one, and a logging one used by dry runs
so the delivery logic is exercised without sending mail. The dry-run switch is a
*port*, not a flag argument (CLAUDE-function-design.md §3).
"""

from __future__ import annotations

import email.policy
import logging
from collections.abc import Mapping
from email.message import EmailMessage
from typing import Any, Protocol

from .errors import DeliveryError
from .models import Attachment, SendRequest, mask_address

LOGGER = logging.getLogger("sap_patch_day.mailer")
DRY_RUN_MESSAGE_ID = "dry-run:no-message-id"


class Mailer(Protocol):
    """Anything that can deliver a :class:`SendRequest` and return a message id."""

    def send(self, request: SendRequest) -> str: ...


class SesMailer:
    """Amazon SES v2 ``SendEmail``."""

    def __init__(self, client: Any, configuration_set: str = "") -> None:
        self._client = client
        self._configuration_set = configuration_set

    def send(self, request: SendRequest) -> str:
        payload = build_payload(request, self._configuration_set)
        try:
            response = self._client.send_email(**payload)
        except Exception as error:  # botocore ClientError, without importing botocore
            raise DeliveryError(f"Amazon SES rejected the message: {error}") from error
        message_id = str(response.get("MessageId", ""))
        LOGGER.info(
            "delivered '%s' to %d recipient(s), message id %s",
            request.content.subject,
            len(request.recipients),
            message_id or "<none>",
        )
        return message_id


class LoggingMailer:
    """Dry-run mailer: logs the message instead of sending it."""

    def __init__(self, logger: logging.Logger = LOGGER) -> None:
        self._logger = logger

    def send(self, request: SendRequest) -> str:
        self._logger.info(
            "dry run: would send '%s' to %s (%d characters of HTML, attachment: %s)",
            request.content.subject,
            ", ".join(mask_address(address) for address in request.recipients),
            len(request.content.html_body),
            request.content.attachment.filename if request.content.attachment else "none",
        )
        return DRY_RUN_MESSAGE_ID


def build_payload(request: SendRequest, configuration_set: str) -> dict[str, Any]:
    attachment = request.content.attachment
    if attachment is None:
        content: dict[str, Any] = {"Simple": _simple_content(request)}
    else:
        content = {"Raw": {"Data": _mime_bytes(request, attachment)}}
    payload: dict[str, Any] = {
        "FromEmailAddress": request.sender,
        "Destination": {"ToAddresses": list(request.recipients)},
        "Content": content,
    }
    if request.reply_to:
        payload["ReplyToAddresses"] = [request.reply_to]
    if configuration_set:
        payload["ConfigurationSetName"] = configuration_set
    return payload


def _simple_content(request: SendRequest) -> Mapping[str, Any]:
    return {
        "Subject": {"Data": request.content.subject, "Charset": "UTF-8"},
        "Body": {
            "Html": {"Data": request.content.html_body, "Charset": "UTF-8"},
            "Text": {"Data": request.content.text_body, "Charset": "UTF-8"},
        },
    }


def _mime_bytes(request: SendRequest, attachment: Attachment) -> bytes:
    message = EmailMessage()
    # A bare address: config validates the sender as one, and SES resolves the
    # display name from the identity. `formataddr((sender, sender))` would print
    # the address twice in the reader's mail client.
    message["From"] = request.sender
    message["To"] = ", ".join(request.recipients)
    message["Subject"] = request.content.subject
    if request.reply_to:
        message["Reply-To"] = request.reply_to
    message.set_content(request.content.text_body)
    message.add_alternative(request.content.html_body, subtype="html")
    maintype, _, subtype = attachment.content_type.partition("/")
    message.add_attachment(
        attachment.data,
        maintype=maintype or "application",
        subtype=subtype or "octet-stream",
        filename=attachment.filename,
    )
    return message.as_bytes(policy=email.policy.SMTP)
