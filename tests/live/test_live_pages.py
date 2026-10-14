"""Live tests against the real SAP support portal.

These are the only tests that touch the network, so they are opt-in
(`pytest --live`, `SAP_PATCH_DAY_LIVE=1`, or `scripts/live-check.sh`) and they
make no assertion about a specific month's contents — SAP's site changes every
patch day, and a test that pins today's table would fail tomorrow for the wrong
reason. What is asserted is the *shape* the parser depends on:

* the index still publishes a "Dates for <year>" section with a linked entry;
* the newest linked month parses into a table with the four expected columns;
* a probe of that month reaches the same conclusion through the production code
  path, including the sanitiser's view of the link cells.

Everything that pins exact values lives in `tests/fixtures/` with recorded
provenance (docs/addenda/0010).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from sap_patch_day.bulletin_index import parse_dates_sections
from sap_patch_day.htmltree import parse
from sap_patch_day.httpclient import UrllibHttpClient
from sap_patch_day.models import MONTH_NAMES, HttpPolicy, PatchDayMonth, PublicationCheck
from sap_patch_day.notes_table import extract_notes_table, extract_page_title, page_claims_month
from sap_patch_day.probe import INDEX_URL, ProbeRequest, canonical_url, check_publication

pytestmark = pytest.mark.live

SLUG = re.compile(r"/(?P<month>[a-z]+)-(?P<year>\d{4})\.html$")
MONTH_NUMBERS = {name.lower(): number for number, name in enumerate(MONTH_NAMES, start=1)}


def transport() -> UrllibHttpClient:
    return UrllibHttpClient(HttpPolicy(timeout_seconds=30, max_attempts=2, backoff_seconds=3.0))


def fetch(url: str) -> str:
    result = transport().get(url)
    if not result.is_success:
        pytest.skip(f"{url} answered {result.status} {result.error}".strip())
    return result.body


def index_sections():
    sections = parse_dates_sections(fetch(INDEX_URL))
    assert sections, "the index page no longer publishes a 'Dates for <year>' section"
    return sections


def newest_linked_month() -> PatchDayMonth:
    """The most recent month the index links to, read from its URL slug."""
    months = []
    for section in index_sections():
        for entry in section.entries:
            if not entry.url:
                continue
            match = SLUG.search(entry.url)
            if match and match.group("month").lower() in MONTH_NUMBERS:
                months.append(
                    PatchDayMonth(
                        year=int(match.group("year")),
                        month=MONTH_NUMBERS[match.group("month").lower()],
                    )
                )
    assert months, "no entry on the index page links to a bulletin"
    return max(months, key=lambda month: (month.year, month.month))


def bulletin_page() -> tuple[PatchDayMonth, str]:
    target = newest_linked_month()
    body = fetch(canonical_url(target))
    if page_claims_month(extract_page_title(parse(body)), target) is False:
        pytest.skip(f"the {target.display} page still shows another month")
    return target, body


def test_the_index_page_lists_the_newest_year_with_entries() -> None:
    sections = index_sections()
    assert max(section.year for section in sections) >= datetime.now(UTC).year
    assert any(section.entries for section in sections), "the newest section has no entries"


def test_the_newest_linked_month_is_still_a_link() -> None:
    newest = newest_linked_month()
    section = next(section for section in index_sections() if section.year == newest.year)
    entry = section.entry_for(newest)
    assert entry is not None, f"{newest.display} disappeared from the index"
    assert entry.is_linked


def test_the_newest_month_page_parses_into_the_notes_table() -> None:
    _, body = bulletin_page()
    notes = extract_notes_table(parse(body))
    assert notes, "the notes table parsed but carried no rows"
    first = notes[0]
    assert first.note_id
    assert first.note_url.startswith("http")
    assert first.title_html
    assert first.priority, "a note row without a priority"
    assert first.cvss_score or first.is_update


def test_a_probe_of_the_newest_month_reports_it_as_published() -> None:
    target, _ = bulletin_page()
    probe = ProbeRequest(target=target, observed_at=datetime.now(UTC))
    check: PublicationCheck = check_publication(probe, transport())
    assert check.outcome == "published", check.detail or f"outcome={check.outcome}"
    assert check.http_status == 200
    assert check.canonical_url == canonical_url(target)
    assert check.bulletin is not None
    assert check.bulletin.notes
    assert check.bulletin.intro_text, "SAP's summary sentence is no longer on the page"


def test_the_sanitised_title_cells_keep_their_note_links() -> None:
    _, body = bulletin_page()
    notes = extract_notes_table(parse(body))
    linked = [note for note in notes if f'href="{note.note_url}"' in note.title_html]
    assert linked, "no note row kept its own link after sanitising"
    for note in notes:
        assert "<script" not in note.title_html.lower()
        assert "onerror" not in note.title_html.lower()
