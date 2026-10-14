"""Notes-table extraction (spec FR-4)."""

from __future__ import annotations

import pytest

from sap_patch_day.errors import ParseError
from sap_patch_day.htmltree import parse
from sap_patch_day.models import SOURCE_GITHUB, SOURCE_SAP, BulletinSummary, PatchDayMonth
from sap_patch_day.notes_table import (
    extract_intro_text,
    extract_notes_table,
    extract_page_title,
    find_notes_table,
    page_claims_month,
)
from tests.support.pages import fixture


@pytest.fixture
def bulletin_root():  # type: ignore[no-untyped-def]
    return parse(fixture("october_2026.html"))


def test_extracts_every_row_of_the_real_bulletin(bulletin_root) -> None:  # type: ignore[no-untyped-def]
    notes = extract_notes_table(bulletin_root)
    assert len(notes) == 21
    assert notes[0].note_id == "GHSA-4mv3-jxxm-9cx2"
    assert notes[-1].note_id == "3767634"
    assert all(note.note_url.startswith("https://") for note in notes)


def test_summary_counts_from_the_real_bulletin(bulletin_root) -> None:  # type: ignore[no-untyped-def]
    summary = BulletinSummary.from_notes(extract_notes_table(bulletin_root))
    assert summary.total == 21
    assert (summary.critical, summary.high, summary.medium, summary.low) == (4, 2, 14, 1)
    assert summary.updates == 2
    assert summary.new_notes == 19
    assert summary.github_advisories == 4
    assert summary.sap_notes == 17
    assert summary.other == 0


def test_github_advisories_are_identified_by_host(bulletin_root) -> None:  # type: ignore[no-untyped-def]
    notes = {note.note_id: note for note in extract_notes_table(bulletin_root)}
    advisory = notes["GHSA-8rcv-xqgf-8743"]
    assert advisory.source == SOURCE_GITHUB
    assert advisory.product == "cla-assistant"
    assert advisory.versions == "<= 2.13.1"
    assert notes["3797111"].source == SOURCE_SAP


def test_update_rows_carry_the_earlier_patch_day(bulletin_root) -> None:  # type: ignore[no-untyped-def]
    updates = [note for note in extract_notes_table(bulletin_root) if note.is_update]
    assert [note.note_id for note in updates] == ["3689080", "3629325"]
    assert [note.update_period for note in updates] == [
        "March 2026 Patch Day",
        "September 2025 Patch Day",
    ]


def test_title_cell_markup_is_preserved_and_anchors_are_safe(bulletin_root) -> None:  # type: ignore[no-untyped-def]
    note = extract_notes_table(bulletin_root)[0]
    assert "<b>" in note.title_html
    assert "<br>" in note.title_html
    assert 'rel="noopener noreferrer"' in note.title_html
    assert "(opens in new tab)" not in note.title_html


def test_cvss_vector_and_score_are_parsed(bulletin_root) -> None:  # type: ignore[no-untyped-def]
    note = extract_notes_table(bulletin_root)[0]
    assert note.cvss_score == "10.0"
    assert note.cvss_vector == "CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H"
    assert note.cvss_url.startswith("https://www.first.org/cvss/calculator/3.0#")


def test_intro_and_page_title_are_captured(bulletin_root) -> None:  # type: ignore[no-untyped-def]
    assert extract_page_title(bulletin_root) == "SAP Security Patch Day – October 2026"
    intro = extract_intro_text(bulletin_root)
    assert intro.startswith("On 13th of October 2026, SAP security patch day saw the release of")
    assert intro.endswith("previously released security notes.")


def test_intro_drops_boilerplate_sharing_a_paragraph_with_the_summary() -> None:
    # The live page puts the boilerplate and the summary in ONE <p>, split by <br />.
    root = parse(
        "<p>This post shares the information on security notes. SAP strongly recommends "
        'that the customer visits the <a href="https://example.test">support portal</a>.<br />\n'
        "On 13<sup>th</sup> of October 2026, SAP security patch day saw the release of 16 new "
        "security notes. There are 2 updates to previously released security notes.</p>"
    )
    assert extract_intro_text(root) == (
        "On 13th of October 2026, SAP security patch day saw the release of 16 new "
        "security notes. There are 2 updates to previously released security notes."
    )


def test_page_claims_month_detects_the_right_and_wrong_month() -> None:
    october = PatchDayMonth(year=2026, month=10)
    assert page_claims_month("SAP Security Patch Day – October 2026", october) is True
    assert page_claims_month("SAP Security Patch Day – September 2026", october) is False
    assert page_claims_month("Security Notes & News", october) is None


def test_extraction_stops_at_the_researchers_paragraph_inside_the_table() -> None:
    html = """
    <table>
      <tr><th>Note#</th><th>Title</th><th>Priority</th><th>CVSS</th></tr>
      <tr><td><a href="https://me.sap.com/notes/1">1</a></td><td>first<b>x</b></td>
          <td>High</td><td>7.5</td></tr>
      <tr><td colspan="4">To know more about the security researchers and research companies who
          have contributed for security patches of this month, visit here.</td></tr>
      <tr><td><a href="https://me.sap.com/notes/2">2</a></td><td>after the paragraph</td>
          <td>Low</td><td>2.0</td></tr>
    </table>
    """
    notes = extract_notes_table(parse(html))
    assert [note.note_id for note in notes] == ["1"]


def test_rows_without_a_note_reference_are_skipped_not_fatal() -> None:
    html = """
    <table>
      <tr><th>Note#</th><th>Title</th><th>Priority</th><th>CVSS</th></tr>
      <tr><td>not a note</td><td>no link</td><td>High</td><td>7.5</td></tr>
      <tr><td><a href="https://me.sap.com/notes/2">2</a></td><td>second</td>
          <td>Low</td><td>2.0</td></tr>
    </table>
    """
    notes = extract_notes_table(parse(html))
    assert [note.note_id for note in notes] == ["2"]


def test_rows_of_a_nested_table_are_not_read_as_note_rows() -> None:
    html = """
    <table>
      <tr><th>Note#</th><th>Title</th><th>Priority</th><th>CVSS</th></tr>
      <tr>
        <td><a href="https://me.sap.com/notes/3">3</a></td>
        <td>outer row<table><tr><td><a href="https://me.sap.com/notes/9">9</a></td>
            <td>inner</td><td>Low</td><td>1.0</td></tr></table></td>
        <td>High</td><td>7.5</td>
      </tr>
    </table>
    """
    notes = extract_notes_table(parse(html))
    assert [note.note_id for note in notes] == ["3"]


def test_missing_table_raises_the_documented_parse_error() -> None:
    with pytest.raises(ParseError) as error:
        extract_notes_table(parse(fixture("no_table.html")))
    assert str(error.value) == (
        "No notes table found: expected a table header containing Note#, Title, Priority, CVSS"
    )


def test_header_without_body_rows_raises_the_documented_parse_error() -> None:
    with pytest.raises(ParseError) as error:
        extract_notes_table(parse(fixture("empty_table.html")))
    assert "no row carried a note reference" in str(error.value)


def test_find_notes_table_ignores_tables_without_the_header() -> None:
    root = parse(
        "<table><tr><td>layout</td></tr></table><table><tr><th>Note#</th>"
        "<th>Title</th><th>Priority</th><th>CVSS</th></tr><tr><td>1</td></tr></table>"
    )
    table = find_notes_table(root)
    assert table.attrs.get("class") is None
    assert len(table.find_all(lambda node: node.tag == "tr")) == 2


def test_non_breaking_spaces_and_line_breaks_are_tolerated() -> None:
    html = """
    <table><tr><th>Note#</th><th>Title</th><th>Priority</th><th>CVSS</th></tr>
    <tr>
      <td><a href="https://me.sap.com/notes/4">4</a></td>
      <td>[ <a href="https://www.cve.org/CVERecord?id=CVE-2026-10000">CVE-2026-10000</a> ]
          <b>Headline&nbsp;with non-breaking space</b><br>Product - SAP&nbsp;NetWeaver<br>
          Version(s) - KERNEL 7.93</td>
      <td>High</td><td>7.5</td>
    </tr></table>
    """
    note = extract_notes_table(parse(html))[0]
    assert note.headline == "Headline with non-breaking space"
    assert note.product == "SAP NetWeaver"
    assert note.cve_ids == ("CVE-2026-10000",)
