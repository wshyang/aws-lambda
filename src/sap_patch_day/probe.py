"""Publication detection (spec FR-2, addendum 0005).

The canonical bulletin URL is constructed and probed; the index page's link
transition is corroborating evidence only. The rule that must never regress:
**a live canonical URL is enough to deliver**, even when the index page has not
turned this month's date into a link.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .bulletin_index import link_transition
from .errors import ParseError, PatchDayError
from .htmltree import parse
from .httpclient import HttpClient, HttpResult
from .models import (
    OUTCOME_NOT_PUBLISHED,
    OUTCOME_PUBLISHED,
    OUTCOME_UNVERIFIED,
    OUTCOME_UNVERIFIED_TABLE,
    Bulletin,
    PatchDayMonth,
    PublicationCheck,
)
from .notes_table import (
    extract_intro_text,
    extract_notes_table,
    extract_page_title,
    page_claims_month,
)

BASE_URL = "https://support.sap.com/en/my-support/knowledge-base/security-notes-news/"
# The index lives one level above the bulletin directory; the sibling
# ``security-notes-news/security-notes-news.html`` answers 404 (addendum 0011).
INDEX_URL = "https://support.sap.com/en/my-support/knowledge-base/security-notes-news.html"


@dataclass(frozen=True)
class ProbeRequest:
    """What to probe, and when the probe is considered to have happened."""

    target: PatchDayMonth
    observed_at: datetime
    index_url: str = INDEX_URL


def canonical_url(target: PatchDayMonth) -> str:
    """The month's bulletin URL, constructed rather than scraped (FR-2.1)."""
    return f"{BASE_URL}{target.bulletin_slug}.html"


def check_publication(probe: ProbeRequest, http: HttpClient) -> PublicationCheck:
    """Probe the canonical URL and classify the outcome (FR-2.2 to FR-2.7)."""
    response = http.get(canonical_url(probe.target))
    facts = _facts(probe, response, link=_index_link_state(probe, http))
    if response.is_absent:
        return _outcome(
            facts, OUTCOME_NOT_PUBLISHED, "SAP answered 404 for the canonical bulletin URL"
        )
    if not response.is_success:
        return _outcome(facts, OUTCOME_UNVERIFIED, response.error or f"HTTP {response.status}")
    return _classify_page(probe, response, facts)


@dataclass(frozen=True)
class _Facts:
    """What is known about the response before its body is interpreted."""

    target: PatchDayMonth
    canonical_url: str
    http_status: int | None
    link_transitioned: bool | None


def _facts(probe: ProbeRequest, response: HttpResult, *, link: bool | None) -> _Facts:
    return _Facts(
        target=probe.target,
        canonical_url=canonical_url(probe.target),
        http_status=response.status,
        link_transitioned=link,
    )


def _outcome(facts: _Facts, outcome: str, detail: str = "") -> PublicationCheck:
    return PublicationCheck(
        target=facts.target,
        canonical_url=facts.canonical_url,
        outcome=outcome,
        http_status=facts.http_status,
        link_transitioned=facts.link_transitioned,
        detail=detail,
    )


def _classify_page(probe: ProbeRequest, response: HttpResult, facts: _Facts) -> PublicationCheck:
    """A 200 response: published only if the page parses into this month's table."""
    try:
        bulletin = _bulletin(probe, response, facts)
    except ParseError as error:
        return _outcome(facts, OUTCOME_UNVERIFIED_TABLE, f"table_not_parsed: {error}")
    if page_claims_month(bulletin.page_title, probe.target) is False:
        return _outcome(
            facts, OUTCOME_UNVERIFIED_TABLE, f"stale_page: title is '{bulletin.page_title}'"
        )
    return PublicationCheck(
        target=probe.target,
        canonical_url=response.url,
        outcome=OUTCOME_PUBLISHED,
        http_status=response.status,
        link_transitioned=facts.link_transitioned,
        bulletin=bulletin,
    )


def _bulletin(probe: ProbeRequest, response: HttpResult, facts: _Facts) -> Bulletin:
    root = parse(response.body)
    return Bulletin(
        target=probe.target,
        source_url=response.url,
        page_title=extract_page_title(root),
        intro_text=extract_intro_text(root),
        notes=extract_notes_table(root),
        fetched_at=probe.observed_at,
        link_transitioned=facts.link_transitioned,
    )


def _index_link_state(probe: ProbeRequest, http: HttpClient) -> bool | None:
    """Whether the index shows this month as a link; ``None`` if we cannot tell.

    Never raises: the index page is evidence, not a gate (FR-2.4).
    """
    response = http.get(probe.index_url)
    if not response.is_success:
        return None
    try:
        return link_transition(response.body, probe.target)
    except (PatchDayError, ValueError):
        return None
