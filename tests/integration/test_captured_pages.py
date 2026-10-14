"""Regression tests for captured pages: raw SAP HTML, saved by
`scripts/capture-fixture.sh` (docs/addenda/0010).

The parametrisation reads the directory, so the first real capture becomes a
test without editing this file — and the file is skipped, not silently empty,
until one exists. The curated fixtures in `tests/fixtures/` cover the branch
matrix; these cover "SAP changed its markup and we saved the evidence".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sap_patch_day.bulletin_index import parse_dates_sections
from sap_patch_day.htmltree import parse
from sap_patch_day.notes_table import extract_intro_text, extract_notes_table
from tests.support.pages import FIXTURE_DIR

CAPTURE_DIR = FIXTURE_DIR / "captured"


def captured_pages() -> list[Path]:
    return sorted(CAPTURE_DIR.glob("**/*.html"))


def page_id(path: Path) -> str:
    return str(path.relative_to(CAPTURE_DIR))


def test_the_capture_directory_is_documented() -> None:
    assert (CAPTURE_DIR / "README.md").exists(), "captures must say where they came from"


def test_there_is_a_capture_or_a_clear_note_that_there_is_not() -> None:
    """A green build must never look like 'the live pages were verified'."""
    if not captured_pages():
        pytest.skip("no captures yet: run scripts/capture-fixture.sh from a host with egress")


@pytest.mark.parametrize("page", captured_pages(), ids=page_id)
def test_a_captured_bulletin_page_still_parses(page: Path) -> None:
    body = page.read_text(encoding="utf-8")
    if "dates for" in body.lower() and "note#" not in body.lower():
        sections = parse_dates_sections(body)
        assert sections, f"{page_id(page)} has a 'Dates for' heading but no section parsed from it"
        return
    notes = extract_notes_table(parse(body))
    assert notes, f"{page_id(page)} no longer yields any notes"
    for note in notes:
        assert note.note_id, f"{page_id(page)} has a row without a note reference"
        assert note.priority, f"{page_id(page)} has a row without a priority ({note.note_id})"


@pytest.mark.parametrize("page", captured_pages(), ids=page_id)
def test_a_captured_page_is_never_empty_or_an_error_page(page: Path) -> None:
    body = page.read_text(encoding="utf-8")
    assert len(body) > 2000, f"{page_id(page)} is suspiciously small for a SAP page"
    lowered = body.lower()
    for marker in ("access denied", "page not found", "502 bad gateway"):
        assert marker not in lowered, f"{page_id(page)} looks like an error page"
    if page.name.startswith("security_notes_news_index"):
        return  # the index has no summary sentence; its 'Dates for' parse is asserted above
    intro = extract_intro_text(parse(body))
    assert intro or "note#" in lowered, (
        f"{page_id(page)} has neither a summary sentence nor a notes table"
    )
