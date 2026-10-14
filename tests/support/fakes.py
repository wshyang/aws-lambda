"""Test doubles for the ports.

These are real implementations of the ports, not mocks: the mailer records
what it was asked to send, the transport replays recorded pages, and the state
store is a genuine in-memory store. Nothing here substitutes the parsers,
renderers or decision logic (see CLAUDE-testing.md §2).
"""

from __future__ import annotations

from sap_patch_day.errors import DeliveryError
from sap_patch_day.httpclient import HttpResult
from sap_patch_day.models import SendRequest


class RecordingMailer:
    """A mailer that keeps every request it received."""

    def __init__(self) -> None:
        self.requests: list[SendRequest] = []

    @property
    def count(self) -> int:
        return len(self.requests)

    @property
    def last(self) -> SendRequest:
        if not self.requests:
            raise AssertionError("no message was sent")
        return self.requests[-1]

    @property
    def last_or_none(self) -> SendRequest | None:
        return self.requests[-1] if self.requests else None

    def send(self, request: SendRequest) -> str:
        self.requests.append(request)
        return f"test-message-{len(self.requests)}"


class ExplodingMailer:
    """A mailer that always fails, for the "SES is down" paths."""

    def __init__(self, message: str = "Amazon SES rejected the message: throttled") -> None:
        self._message = message

    def send(self, request: SendRequest) -> str:
        raise DeliveryError(self._message)


class UnreachableHttpClient:
    """Transport that always fails, for the "unverified" branches (spec FR-2.5)."""

    def __init__(self, error: str = "URLError: connection reset by peer") -> None:
        self._error = error

    def get(self, url: str) -> HttpResult:
        return HttpResult(url=url, status=None, error=self._error)
