"""The index page's "Dates for <year>" section and its link transition (FR-2.3)."""

from __future__ import annotations

import pytest

from sap_patch_day.bulletin_index import link_transition, parse_dates_sections
from sap_patch_day.models import PatchDayMonth
from tests.support.pages import fixture

TWO_YEARS = """
<h4>Dates for 2027</h4>
<p><b><a href="/january-2027.html">January 12</a></b></p>
<p><b>February 9</b></p>
<h4>Dates for 2026</h4>
<p><b><a href="/october-2026.html">October 13</a></b></p>
<p><b>November 10</b></p>
<p>For previous years bulletins refer to</p>
"""


def test_index_fixture_has_the_published_months_linked() -> None:
    sections = parse_dates_sections(fixture("security_notes_news_index.html"))
    assert [section.year for section in sections] == [2026]
    entries = {entry.month_name: entry for entry in sections[0].entries}
    assert len(entries) == 12
    assert entries["October"].url.endswith("/security-notes-news/october-2026.html")
    assert entries["October"].day == 13
    assert entries["November"].url is None
    assert entries["November"].day == 10
    assert entries["December"].url is None


def test_link_transition_reports_true_for_a_linked_month() -> None:
    target = PatchDayMonth(year=2026, month=10)
    assert link_transition(fixture("security_notes_news_index.html"), target) is True


def test_link_transition_reports_false_for_a_plain_text_month() -> None:
    target = PatchDayMonth(year=2026, month=11)
    assert link_transition(fixture("security_notes_news_index.html"), target) is False


def test_link_transition_is_unknown_for_another_year() -> None:
    target = PatchDayMonth(year=2025, month=10)
    assert link_transition(fixture("security_notes_news_index.html"), target) is None


def test_link_transition_is_unknown_when_the_heading_is_absent() -> None:
    assert (
        link_transition("<h2>Security Notes</h2><p>Nothing to see</p>", PatchDayMonth(2026, 10))
        is None
    )


def test_multiple_year_sections_are_kept_apart() -> None:
    sections = parse_dates_sections(TWO_YEARS)
    assert [section.year for section in sections] == [2027, 2026]
    assert sections[0].entry_for(PatchDayMonth(2027, 1)) is not None
    assert sections[1].entry_for(PatchDayMonth(2026, 10)) is not None
    assert sections[1].entry_for(PatchDayMonth(2026, 12)) is None


def test_section_stops_at_the_previous_years_paragraph() -> None:
    html = """
    <h4>Dates for 2026</h4>
    <p><b>November 10</b></p>
    <p>For previous years bulletins refer to</p>
    <p><b><a href="/archive.html">December 8</a></b></p>
    """
    section = parse_dates_sections(html)[0]
    assert [entry.month_name for entry in section.entries] == ["November"]


def test_entry_may_carry_a_year() -> None:
    html = """
    <h4>Dates for 2026</h4>
    <p><b><a href="/november-2026.html">November 10, 2026</a></b></p>
    """
    section = parse_dates_sections(html)[0]
    assert section.entries[0].day == 10
    assert section.entries[0].is_linked


def test_list_markup_is_supported_too() -> None:
    html = """
    <h4>Dates for 2026</h4>
    <ul>
      <li><a href="/september-2026.html">September 8</a> - Patch Day Security Notes</li>
      <li>October 13</li>
    </ul>
    """
    section = parse_dates_sections(html)[0]
    entries = {entry.month_name: entry for entry in section.entries}
    assert entries["September"].is_linked
    assert entries["October"].is_linked is False


@pytest.mark.parametrize(
    "heading",
    ["Dates for 2026", "<b>Dates for 2026</b>", "DATES FOR 2026"],
)
def test_heading_text_variants(heading: str) -> None:
    html = f"<h4>{heading}</h4><p><b>November 10</b></p>"
    sections = parse_dates_sections(html)
    assert len(sections) == 1
    assert sections[0].year == 2026
