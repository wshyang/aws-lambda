"""Email rendering: one model, three renderers (spec FR-5, addendum 0007)."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from sap_patch_day.config import MappingConfigSource, load_config
from sap_patch_day.email_render import (
    csv_filename,
    render_bulletin_email,
    render_csv,
    render_notice_email,
)
from sap_patch_day.htmltree import parse
from sap_patch_day.models import (
    NOTICE_PENDING,
    NOTICE_UNVERIFIED,
    NOTICE_WATCHDOG,
    Bulletin,
    Notice,
    PatchDayMonth,
    RuntimeConfig,
)
from sap_patch_day.notes_table import extract_intro_text, extract_notes_table, extract_page_title
from tests.support.pages import OCTOBER_2026, fixture

MOMENT = datetime(2026, 10, 13, 8, 5, tzinfo=UTC)


@pytest.fixture
def bulletin() -> Bulletin:
    root = parse(fixture("october_2026.html"))
    return Bulletin(
        target=OCTOBER_2026,
        source_url="https://support.sap.com/en/my-support/knowledge-base/security-notes-news/october-2026.html",
        page_title=extract_page_title(root),
        intro_text=extract_intro_text(root),
        notes=extract_notes_table(root),
        fetched_at=MOMENT,
        link_transitioned=True,
    )


@pytest.fixture
def config() -> RuntimeConfig:
    values = {
        "sender": "sap-patch-day@example.com",
        "recipients": "a@example.test, b@example.test",
    }
    return load_config(MappingConfigSource(values).read(), {})


def test_subject_counts_notes_and_priorities(bulletin: Bulletin, config: RuntimeConfig) -> None:
    content = render_bulletin_email(bulletin, config)
    assert content.subject == (
        "[SAP Security Patch Day] October 2026 — 21 notes (4 critical, 2 high, 14 medium, 1 low)"
    )


def test_subject_prefix_comes_from_configuration(bulletin: Bulletin) -> None:
    values = {
        "sender": "s@example.com",
        "recipients": "a@example.test",
        "subject_prefix": "[PATCH-DAY]",
    }
    custom = load_config(MappingConfigSource(values).read(), {})
    assert render_bulletin_email(bulletin, custom).subject.startswith("[PATCH-DAY] October 2026")


def test_html_and_text_contain_every_note(bulletin: Bulletin, config: RuntimeConfig) -> None:
    content = render_bulletin_email(bulletin, config)
    for note in bulletin.notes:
        assert note.note_id in content.html_body
        assert note.note_id in content.text_body


def test_html_keeps_the_four_source_columns(bulletin: Bulletin, config: RuntimeConfig) -> None:
    html_body = render_bulletin_email(bulletin, config).html_body
    for column in ("Note#", "Title", "Priority", "CVSS"):
        assert f">{column}</th>" in html_body


def test_html_carries_priority_colour_and_cvss_links(
    bulletin: Bulletin, config: RuntimeConfig
) -> None:
    html_body = render_bulletin_email(bulletin, config).html_body
    assert "#a50021" in html_body  # critical
    assert "#b45309" in html_body  # high
    assert (
        "https://www.first.org/cvss/calculator/3.0#CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H"
        in html_body
    )


def test_html_has_no_scripts_styles_or_images(bulletin: Bulletin, config: RuntimeConfig) -> None:
    html_body = render_bulletin_email(bulletin, config).html_body
    for forbidden in ("<script", "<iframe", "<img", "onclick=", "javascript:", "<link "):
        assert forbidden not in html_body


def test_html_does_not_leak_internals(bulletin: Bulletin, config: RuntimeConfig) -> None:
    html_body = render_bulletin_email(bulletin, config).html_body
    for internal in (
        "sap-security-patch-day",
        "/sap-security-patch-day/",
        "run_mode",
        "SSM",
        "CloudFormation",
        "lambda",
        "addendum",
    ):
        assert internal.lower() not in html_body.lower()


def test_footer_provenance_reports_the_link_transition(
    bulletin: Bulletin, config: RuntimeConfig
) -> None:
    html_body = render_bulletin_email(bulletin, config).html_body
    assert "The monthly index page links to this bulletin." in html_body
    assert "13 Oct 2026, 16:05 SGT" in html_body
    unlinked = replace(bulletin, link_transitioned=False)
    body = render_bulletin_email(unlinked, config).html_body
    assert "does not link to this bulletin yet" in body
    unknown = replace(bulletin, link_transitioned=None)
    assert "could not be determined" in render_bulletin_email(unknown, config).html_body


def test_no_attachment_by_default(bulletin: Bulletin, config: RuntimeConfig) -> None:
    assert render_bulletin_email(bulletin, config).attachment is None


def test_csv_attachment_when_enabled(bulletin: Bulletin) -> None:
    values = {"sender": "s@example.com", "recipients": "a@example.test", "attach_csv": "true"}
    config = load_config(MappingConfigSource(values).read(), {})
    attachment = render_bulletin_email(bulletin, config).attachment
    assert attachment is not None
    assert attachment.filename == "sap-security-notes-2026-10.csv"
    assert attachment.content_type == "text/csv; charset=utf-8"
    assert attachment.data.decode("utf-8").startswith("note_id,source,priority,cvss_score,")


def test_csv_filename_is_stable(bulletin: Bulletin) -> None:
    assert csv_filename(bulletin) == "sap-security-notes-2026-10.csv"


def test_csv_has_one_row_per_note_with_the_documented_columns(bulletin: Bulletin) -> None:
    rows = list(csv.reader(io.StringIO(render_csv(bulletin))))
    assert rows[0] == [
        "note_id",
        "source",
        "priority",
        "cvss_score",
        "cvss_vector",
        "cve_ids",
        "product",
        "versions",
        "is_update",
        "headline",
        "note_url",
    ]
    assert len(rows) == len(bulletin.notes) + 1
    first = dict(zip(rows[0], rows[1], strict=True))
    assert first["note_id"] == "GHSA-4mv3-jxxm-9cx2"
    assert first["priority"] == "Critical"
    assert first["is_update"] == "false"
    assert first["cve_ids"] == "CVE-2026-89111"


def test_labelled_value_style_notation_is_matched_without_a_dash(bulletin: Bulletin) -> None:
    advisory = bulletin.notes[0]
    assert advisory.versions == "< 0.45.2"


@pytest.mark.parametrize(
    "kind,expected_subject_tail",
    [
        (NOTICE_PENDING, "October 2026 — notes not published yet"),
        (NOTICE_UNVERIFIED, "October 2026 — publication could not be verified"),
        (NOTICE_WATCHDOG, "Delivery alert — no October 2026 notes were sent"),
    ],
)
def test_notice_subjects(kind: str, expected_subject_tail: str, config: RuntimeConfig) -> None:
    notice = Notice(
        kind=kind, target=PatchDayMonth(2026, 10), observed_at=MOMENT, detail="detail text"
    )
    content = render_notice_email(notice, config)
    assert content.subject == f"[SAP Security Patch Day] {expected_subject_tail}"
    assert "detail text" in content.text_body
    assert "detail text" in content.html_body
    assert "<table" not in content.html_body  # never an empty table


def test_unverified_notice_never_claims_absence(config: RuntimeConfig) -> None:
    notice = Notice(kind=NOTICE_UNVERIFIED, target=PatchDayMonth(2026, 10), observed_at=MOMENT)
    content = render_notice_email(notice, config)
    assert "could not be reached" in content.text_body
    assert "does not mean that no notes were published" in content.text_body


def test_watchdog_notice_explains_itself(config: RuntimeConfig) -> None:
    notice = Notice(kind=NOTICE_WATCHDOG, target=PatchDayMonth(2026, 10), observed_at=MOMENT)
    content = render_notice_email(notice, config)
    assert "This alert is sent once per month" in content.text_body


def test_text_renderer_is_readable(bulletin: Bulletin, config: RuntimeConfig) -> None:
    text_body = render_bulletin_email(bulletin, config).text_body
    assert text_body.startswith("SAP Security Patch Day — October 2026")
    assert re.search(r"^\s+1\. GHSA-4mv3-jxxm-9cx2 \[Critical, CVSS 10\.0\]$", text_body, re.M)
    assert f"{bulletin.notes[0].note_url}" in text_body
    assert "21 entries: 19 new notes, 2 updates" in text_body
