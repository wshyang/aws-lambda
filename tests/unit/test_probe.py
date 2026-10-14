"""Publication detection: the four-row decision table plus the stale-page guard.

This file pins addendum 0005: the canonical URL is authoritative, the index link
transition is evidence, and an unverified result is never reported as "nothing
published".
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sap_patch_day.httpclient import HttpResult, StaticHttpClient
from sap_patch_day.models import (
    OUTCOME_NOT_PUBLISHED,
    OUTCOME_PUBLISHED,
    OUTCOME_UNVERIFIED,
    OUTCOME_UNVERIFIED_TABLE,
    PatchDayMonth,
)
from sap_patch_day.probe import INDEX_URL, ProbeRequest, canonical_url, check_publication
from tests.support.pages import fixture

TARGET = PatchDayMonth(year=2026, month=10)
OBSERVED_AT = datetime(2026, 10, 13, 8, 5, tzinfo=UTC)


class FailingHttpClient:
    """Transport that always fails, for the unverified branch."""

    def __init__(self, error: str = "URLError: connection reset") -> None:
        self._error = error

    def get(self, url: str) -> HttpResult:
        return HttpResult(url=url, status=None, error=self._error)


class FlakyIndexClient:
    """Bulletin page is fine; the index page is broken."""

    def __init__(self, pages, index_error: str = "timed out") -> None:
        self._pages = pages
        self._index_error = index_error

    def get(self, url: str) -> HttpResult:
        if url == INDEX_URL:
            return HttpResult(url=url, status=None, error=self._index_error)
        return StaticHttpClient(self._pages).get(url)


def probe(target: PatchDayMonth = TARGET) -> ProbeRequest:
    return ProbeRequest(target=target, observed_at=OBSERVED_AT)


def test_canonical_url_is_constructed_from_the_month_pattern() -> None:
    assert canonical_url(TARGET) == (
        "https://support.sap.com/en/my-support/knowledge-base/security-notes-news/october-2026.html"
    )


def test_200_with_a_table_is_published() -> None:
    pages = {canonical_url(TARGET): fixture("october_2026.html")}
    check = check_publication(probe(), StaticHttpClient(pages))
    assert check.outcome == OUTCOME_PUBLISHED
    assert check.http_status == 200
    assert check.bulletin is not None
    assert len(check.bulletin.notes) == 21
    assert check.bulletin.fetched_at == OBSERVED_AT


def index_page(linked: bool) -> str:
    """A minimal index page whose October entry is either a link or plain text."""
    entry = (
        '<p><b><a href="/en/my-support/knowledge-base/security-notes-news/october-2026.html">'
        "October 13</a></b></p>"
        if linked
        else "<p><b>October 13</b></p>"
    )
    return f"<h2>Security Notes</h2><h4>Dates for 2026</h4><div>{entry}</div>"


@pytest.mark.parametrize(
    "link_state,index_body",
    [(True, index_page(True)), (False, index_page(False)), (None, None)],
)
def test_link_transition_never_gates_publication(
    link_state: bool | None, index_body: str | None
) -> None:
    """The acceptance rule of addendum 0005: the live URL wins over a stale index."""
    pages = {canonical_url(TARGET): fixture("october_2026.html")}
    if index_body is not None:
        pages[INDEX_URL] = index_body
    check = check_publication(probe(), StaticHttpClient(pages))
    assert check.outcome == OUTCOME_PUBLISHED
    assert check.link_transitioned is link_state


def test_index_showing_plain_text_while_the_url_is_live_is_still_published() -> None:
    """The exact scenario the operator described: index not updated, page live."""
    pages = {
        canonical_url(TARGET): fixture("october_2026.html"),
        INDEX_URL: index_page(linked=False),
    }
    check = check_publication(probe(), StaticHttpClient(pages))
    assert check.outcome == OUTCOME_PUBLISHED
    assert check.link_transitioned is False
    assert check.bulletin is not None
    assert check.bulletin.link_transitioned is False


def test_404_is_definitively_not_published() -> None:
    check = check_publication(probe(), StaticHttpClient({}))
    assert check.outcome == OUTCOME_NOT_PUBLISHED
    assert check.http_status == 404
    assert check.bulletin is None


def test_transport_failure_is_unverified() -> None:
    check = check_publication(probe(), FailingHttpClient())
    assert check.outcome == OUTCOME_UNVERIFIED
    assert check.http_status is None
    assert "connection reset" in check.detail


def test_200_without_a_table_is_unverified_table() -> None:
    pages = {canonical_url(TARGET): fixture("no_table.html")}
    check = check_publication(probe(), StaticHttpClient(pages))
    assert check.outcome == OUTCOME_UNVERIFIED_TABLE
    assert check.detail.startswith("table_not_parsed:")


def test_200_with_an_empty_table_is_unverified_table() -> None:
    pages = {canonical_url(TARGET): fixture("empty_table.html")}
    check = check_publication(probe(), StaticHttpClient(pages))
    assert check.outcome == OUTCOME_UNVERIFIED_TABLE


def test_200_with_a_previous_month_page_is_stale_not_published() -> None:
    pages = {canonical_url(TARGET): fixture("stale_page.html")}
    check = check_publication(probe(), StaticHttpClient(pages))
    assert check.outcome == OUTCOME_UNVERIFIED_TABLE
    assert check.detail.startswith("stale_page:")
    assert check.bulletin is None


def test_broken_index_page_does_not_break_the_probe() -> None:
    pages = {canonical_url(TARGET): fixture("october_2026.html")}
    check = check_publication(probe(), FlakyIndexClient(pages))
    assert check.outcome == OUTCOME_PUBLISHED
    assert check.link_transitioned is None
