"""The HTTP port and its standard-library implementation (spec NFR-5).

Reasons this is a port rather than a bare ``urlopen`` call: tests supply
recorded responses instead of hitting SAP, the local dry run can read pages
from disk, and every retry policy lives in exactly one place.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from .models import HttpPolicy

MAX_BODY_BYTES = 5_000_000
RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class HttpResult:
    """One logical GET, after any retries the policy allowed."""

    url: str
    status: int | None
    body: str = ""
    error: str = ""
    attempts: int = 1

    @property
    def is_success(self) -> bool:
        return self.status == 200

    @property
    def is_absent(self) -> bool:
        """The server says this page does not exist (404/410)."""
        return self.status in (404, 410)

    @property
    def is_transient(self) -> bool:
        """Worth another attempt: transport failure, rate limit or server error."""
        if self.status is None:
            return True
        return self.status in RETRYABLE_STATUSES


class HttpClient(Protocol):
    """Anything that can answer ``get(url)`` with an :class:`HttpResult`."""

    def get(self, url: str) -> HttpResult: ...


class UrllibHttpClient:
    """Real transport: ``urllib`` with a policy-driven retry loop.

    One instance serves one invocation, so ``deadline_seconds`` is a budget for
    the whole run (both URLs), measured with a monotonic clock. When it runs
    out, the client stops starting attempts and reports why — a clean
    ``attempt-failed``/notice beats being killed by the Lambda timeout.
    """

    def __init__(
        self,
        policy: HttpPolicy,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._policy = policy
        self._sleeper = sleeper
        self._monotonic = monotonic
        # One budget per invocation, fixed when the client is built: the second
        # URL a run fetches must not get a fresh allowance (NFR-4).
        self._deadline = monotonic() + policy.deadline_seconds

    def get(self, url: str) -> HttpResult:
        attempts = 0
        result: HttpResult | None = None
        while attempts < self._policy.max_attempts and not self._out_of_time():
            attempts += 1
            result = self._attempt(url, attempts)
            if not result.is_transient:
                return result
            if attempts < self._policy.max_attempts:
                self._sleeper(self._policy.backoff_seconds * attempts)
        if result is None:
            return HttpResult(url=url, status=None, error=self._deadline_error(), attempts=0)
        return result

    def _out_of_time(self) -> bool:
        return self._monotonic() >= self._deadline

    def _deadline_error(self) -> str:
        return (
            f"HTTP_DEADLINE_SECONDS ({self._policy.deadline_seconds}s) exceeded "
            "before this request was started"
        )

    def _attempt(self, url: str, attempt: int) -> HttpResult:
        request = urllib.request.Request(url, headers={"User-Agent": self._policy.user_agent})
        try:
            with urllib.request.urlopen(request, timeout=self._policy.timeout_seconds) as response:
                body = response.read(MAX_BODY_BYTES).decode(_charset(response), errors="replace")
            return HttpResult(url=url, status=200, body=body, attempts=attempt)
        except urllib.error.HTTPError as error:
            return HttpResult(
                url=url, status=error.code, error=f"HTTP {error.code}", attempts=attempt
            )
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            return HttpResult(url=url, status=None, error=_describe(error), attempts=attempt)


class StaticHttpClient:
    """Transport backed by a fixed mapping of URL to body.

    Used by the offline dry run (``python -m sap_patch_day.local_cli``) and by
    tests that replay recorded pages; unknown URLs answer 404, like SAP would
    for a bulletin that does not exist yet.
    """

    def __init__(self, pages: Mapping[str, str]) -> None:
        self._pages = dict(pages)

    def get(self, url: str) -> HttpResult:
        body = self._pages.get(url)
        if body is None:
            return HttpResult(url=url, status=404, error="HTTP 404")
        if isinstance(body, HttpResult):  # pragma: no cover - defensive
            return body
        return HttpResult(url=url, status=200, body=body)


def _charset(response: object) -> str:
    headers = getattr(response, "headers", None)
    if headers is None:
        return "utf-8"
    return headers.get_content_charset() or "utf-8"


def _describe(error: BaseException) -> str:
    if isinstance(error, urllib.error.URLError) and error.reason:
        return f"{type(error).__name__}: {error.reason}"
    return f"{type(error).__name__}: {error}"
