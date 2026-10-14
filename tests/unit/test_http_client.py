"""The transport port: retries, absence and the policy it obeys (spec NFR-5).

``urllib.request.urlopen`` is replaced at the transport boundary — the only
place a substitute is allowed, because everything above it is the real parser
and the real decision logic (CLAUDE-testing.md §2).
"""

from __future__ import annotations

import io
import urllib.error

import pytest

from sap_patch_day.httpclient import HttpResult, StaticHttpClient, UrllibHttpClient
from sap_patch_day.models import HttpPolicy

URL = "https://support.sap.com/en/my-support/knowledge-base/security-notes-news.html"


class FakeResponse:
    """The parts of an ``http.client.HTTPResponse`` that the client touches."""

    def __init__(self, body: bytes = b"<html>ok</html>", charset: str = "utf-8") -> None:
        self._body = body
        self.headers = _Headers(charset)

    def read(self, _limit: int = -1) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class _Headers:
    def __init__(self, charset: str) -> None:
        self._charset = charset

    def get_content_charset(self) -> str:
        return self._charset


class Recorder:
    """Installs a scripted ``urlopen`` and remembers how it was called."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, outcomes: list[object]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[object] = []
        monkeypatch.setattr("urllib.request.urlopen", self._urlopen)

    def _urlopen(self, request: object, timeout: int = 0) -> FakeResponse:
        self.calls.append(request)
        outcome = self._outcomes.pop(0) if self._outcomes else FakeResponse()
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, FakeResponse)
        return outcome


class RecordingSleeper:
    def __init__(self) -> None:
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


def policy(**overrides: object) -> HttpPolicy:
    base = {"timeout_seconds": 30, "max_attempts": 3, "backoff_seconds": 2.0}
    return HttpPolicy(**{**base, **overrides})  # type: ignore[arg-type]


def test_successful_get_returns_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(monkeypatch, [FakeResponse(b"<html>hello</html>")])
    result = UrllibHttpClient(policy(), RecordingSleeper()).get(URL)
    assert result.is_success
    assert result.body == "<html>hello</html>"
    assert result.attempts == 1
    assert len(recorder.calls) == 1


def test_user_agent_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(monkeypatch, [FakeResponse()])
    UrllibHttpClient(policy(user_agent="test-agent/9"), RecordingSleeper()).get(URL)
    request = recorder.calls[0]
    assert isinstance(request, urllib.request.Request)
    assert request.get_header("User-agent") == "test-agent/9"


def test_server_error_is_retried_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(
        monkeypatch,
        [
            urllib.error.HTTPError(URL, 503, "unavailable", {}, io.BytesIO(b"")),  # type: ignore[arg-type]
            FakeResponse(),
        ],
    )
    sleeper = RecordingSleeper()
    result = UrllibHttpClient(policy(), sleeper).get(URL)
    assert result.is_success
    assert result.attempts == 2
    assert len(recorder.calls) == 2
    assert sleeper.waits == [2.0]


def test_transport_failure_is_retried_up_to_the_attempt_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = urllib.error.URLError("connection reset")
    recorder = Recorder(monkeypatch, [error, error, error])
    sleeper = RecordingSleeper()
    result = UrllibHttpClient(policy(max_attempts=3), sleeper).get(URL)
    assert result.status is None
    assert result.is_transient
    assert result.attempts == 3
    assert len(recorder.calls) == 3
    assert sleeper.waits == [2.0, 4.0]


def test_404_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(
        monkeypatch,
        [urllib.error.HTTPError(URL, 404, "not found", {}, io.BytesIO(b""))],  # type: ignore[arg-type]
    )
    sleeper = RecordingSleeper()
    result = UrllibHttpClient(policy(), sleeper).get(URL)
    assert result.status == 404
    assert result.is_absent
    assert not result.is_transient
    assert len(recorder.calls) == 1
    assert sleeper.waits == []


def test_rate_limit_is_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(
        monkeypatch,
        [
            urllib.error.HTTPError(URL, 429, "slow down", {}, io.BytesIO(b"")),  # type: ignore[arg-type]
            FakeResponse(),
        ],
    )
    assert UrllibHttpClient(policy(), RecordingSleeper()).get(URL).is_success
    assert len(recorder.calls) == 2


def test_timeout_is_reported_as_unverified(monkeypatch: pytest.MonkeyPatch) -> None:
    Recorder(monkeypatch, [TimeoutError("timed out")] * 3)
    result = UrllibHttpClient(policy(), RecordingSleeper()).get(URL)
    assert result.status is None
    assert "TimeoutError" in result.error


def test_static_client_answers_404_for_unknown_urls() -> None:
    client = StaticHttpClient({URL: "body"})
    assert client.get(URL).body == "body"
    missing = client.get(f"{URL}?other")
    assert missing.is_absent


def test_http_result_predicates() -> None:
    assert HttpResult(url=URL, status=200).is_success
    assert HttpResult(url=URL, status=410).is_absent
    assert HttpResult(url=URL, status=500).is_transient
    assert not HttpResult(url=URL, status=404).is_transient
