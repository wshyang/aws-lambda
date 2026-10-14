"""Email rendering (spec FR-5, addendum 0007).

One parsed model, three renderers — HTML, plain text and CSV — so the three
parts can never disagree about what the month contained. The HTML is built from
small functions rather than one template string: each piece is unit-tested, and
nothing about the design leaks into the copy (spec FR-5.9).
"""

from __future__ import annotations

import csv
import html
import io
from collections.abc import Iterable
from urllib.parse import urlparse

from .clock import singapore_stamp
from .models import (
    NOTICE_PENDING,
    NOTICE_UNVERIFIED,
    NOTICE_WATCHDOG,
    Attachment,
    Bulletin,
    BulletinSummary,
    EmailContent,
    Notice,
    RuntimeConfig,
    SecurityNote,
)

CSV_COLUMNS: tuple[str, ...] = (
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
)

PRIORITY_COLOURS = {
    "critical": "#a50021",
    "high": "#b45309",
    "medium": "#1d4ed8",
    "low": "#475569",
}

_TABLE_STYLE = (
    "width:100%;border-collapse:collapse;font-family:Arial,Helvetica,sans-serif;font-size:13px"
)
_CELL_STYLE = "padding:6px 8px;border-bottom:1px solid #e2e8f0;vertical-align:top"
_NUMERIC_CELL_STYLE = _CELL_STYLE + ";white-space:nowrap"


def render_bulletin_email(bulletin: Bulletin, config: RuntimeConfig) -> EmailContent:
    """The month's bulletin as a ready-to-send message."""
    summary = BulletinSummary.from_notes(bulletin.notes)
    return EmailContent(
        subject=bulletin_subject(bulletin, config, summary),
        html_body=_bulletin_html(bulletin, summary),
        text_body=_bulletin_text(bulletin, summary),
        attachment=_csv_attachment(bulletin, config),
    )


def bulletin_subject(bulletin: Bulletin, config: RuntimeConfig, summary: BulletinSummary) -> str:
    """Fixed, countable subject line (spec FR-5.2)."""
    counts = (
        f"{summary.critical} critical, {summary.high} high, "
        f"{summary.medium} medium, {summary.low} low"
    )
    return f"{config.subject_prefix} {bulletin.target.display} — {summary.total} notes ({counts})"


def render_notice_email(notice: Notice, config: RuntimeConfig) -> EmailContent:
    """A pending, unverified or watchdog message (spec FR-5.8)."""
    subject = f"{config.subject_prefix} {_notice_subject_tail(notice)}"
    return EmailContent(
        subject=subject,
        html_body=_notice_html(notice),
        text_body=_notice_text(notice, config),
    )


def render_csv(bulletin: Bulletin) -> str:
    """The bulletin as CSV, one row per note (spec FR-5.5)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for note in bulletin.notes:
        writer.writerow(_csv_row(note))
    return buffer.getvalue()


def csv_filename(bulletin: Bulletin) -> str:
    """Stable attachment name, e.g. ``sap-security-notes-2026-10.csv``."""
    return f"sap-security-notes-{bulletin.target.label}.csv"


def _csv_row(note: SecurityNote) -> tuple[str, ...]:
    return (
        note.note_id,
        note.source,
        note.priority,
        note.cvss_score,
        note.cvss_vector,
        " ".join(note.cve_ids),
        note.product,
        note.versions,
        "true" if note.is_update else "false",
        note.headline,
        note.note_url,
    )


def _csv_attachment(bulletin: Bulletin, config: RuntimeConfig) -> Attachment | None:
    if not config.attach_csv:
        return None
    return Attachment(
        filename=csv_filename(bulletin),
        content_type="text/csv; charset=utf-8",
        data=render_csv(bulletin).encode("utf-8"),
    )


def _notice_subject_tail(notice: Notice) -> str:
    if notice.kind == NOTICE_WATCHDOG:
        return f"Delivery alert — no {notice.target.display} notes were sent"
    if notice.kind == NOTICE_UNVERIFIED:
        return f"{notice.target.display} — publication could not be verified"
    if notice.kind == NOTICE_PENDING:
        return f"{notice.target.display} — notes not published yet"
    return f"{notice.target.display} — status update"


def _bulletin_html(bulletin: Bulletin, summary: BulletinSummary) -> str:
    parts = [
        _banner("SAP Security Patch Day", bulletin.target.display),
        _summary_html(bulletin, summary),
        _notes_table_html(bulletin.notes),
        _footer_html(bulletin),
    ]
    return _wrap("".join(parts))


def _banner(heading: str, subheading: str) -> str:
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif">'
        f'<h1 style="font-size:18px;margin:0 0 4px 0">{html.escape(heading)}</h1>'
        f'<p style="margin:0 0 14px 0;color:#475569;font-size:13px">{html.escape(subheading)}</p>'
        "</div>"
    )


def _summary_html(bulletin: Bulletin, summary: BulletinSummary) -> str:
    sentences = [html.escape(bulletin.intro_text)] if bulletin.intro_text else []
    sentences.append(
        html.escape(
            f"This email lists {summary.total} entries: "
            f"{summary.new_notes} new notes and {summary.updates} updates "
            f"({summary.sap_notes} SAP notes, {summary.github_advisories} GitHub advisories)."
        )
    )
    body = "".join(f'<p style="margin:0 0 8px 0">{sentence}</p>' for sentence in sentences)
    return f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:13px">{body}</div>'


def _notes_table_html(notes: Iterable[SecurityNote]) -> str:
    rows = "".join(_row_html(note) for note in notes)
    return (
        f'<table style="{_TABLE_STYLE}" cellspacing="0" cellpadding="0">'
        "<thead><tr>"
        f'<th style="{_HEADER_STYLE}">Note#</th>'
        f'<th style="{_HEADER_STYLE}">Title</th>'
        f'<th style="{_HEADER_STYLE}">Priority</th>'
        f'<th style="{_HEADER_STYLE}">CVSS</th>'
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )


_TITLE_STYLE = "font-family:Arial,Helvetica,sans-serif;font-size:13px"

_HEADER_STYLE = (
    "padding:6px 8px;text-align:left;border-bottom:2px solid #cbd5e1;"
    "font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#334155"
)


def _row_html(note: SecurityNote) -> str:
    update_note = (
        f'<div style="color:#64748b;font-size:12px">{note.update_period}</div>'
        if note.is_update
        else ""
    )
    title = f'<div style="{_TITLE_STYLE}">{note.title_html}</div>{update_note}'
    return (
        "<tr>"
        f'<td style="{_NUMERIC_CELL_STYLE}">{_note_link_html(note)}</td>'
        f'<td style="{_CELL_STYLE}">{title}</td>'
        f'<td style="{_NUMERIC_CELL_STYLE}">{_priority_html(note.priority)}</td>'
        f'<td style="{_NUMERIC_CELL_STYLE}">{_cvss_html(note)}</td>'
        "</tr>"
    )


def _note_link_html(note: SecurityNote) -> str:
    url = _safe_url(note.note_url)
    identifier = html.escape(note.display_id)
    if not url:
        return identifier
    return f'<a href="{html.escape(url, quote=True)}" style="color:#1d4ed8">{identifier}</a>'


def _priority_html(priority: str) -> str:
    colour = PRIORITY_COLOURS.get(priority.lower(), "#475569")
    return (
        f'<span style="color:{colour};font-weight:bold">{html.escape(priority or "unknown")}</span>'
    )


def _cvss_html(note: SecurityNote) -> str:
    score = html.escape(note.cvss_score or "n/a")
    url = _safe_url(note.cvss_url)
    if not url:
        return score
    return f'<a href="{html.escape(url, quote=True)}" style="color:#1d4ed8">{score}</a>'


def _footer_html(bulletin: Bulletin) -> str:
    href = html.escape(bulletin.source_url, quote=True)
    label = html.escape(bulletin.source_url)
    lines = [
        f'Source: <a href="{href}">{label}</a>',
        f"Retrieved {html.escape(singapore_stamp(bulletin.fetched_at))}",
        _link_transition_sentence(bulletin),
    ]
    body = "".join(f'<div style="margin:0 0 4px 0">{line}</div>' for line in lines)
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#475569;'
        f'margin-top:14px;padding-top:10px;border-top:1px solid #e2e8f0">{body}</div>'
    )


def _link_transition_sentence(bulletin: Bulletin) -> str:
    if bulletin.link_transitioned is True:
        return "The monthly index page links to this bulletin."
    if bulletin.link_transitioned is False:
        return (
            "The monthly index page does not link to this bulletin yet; it was retrieved directly."
        )
    return "The state of the monthly index page could not be determined."


def _wrap(body: str) -> str:
    return (
        '<!doctype html><html><head><meta charset="utf-8"></head>'
        f'<body style="margin:0;padding:16px;background:#ffffff">{body}</body></html>'
    )


def _notice_html(notice: Notice) -> str:
    paragraphs = [html.escape(notice.detail)] if notice.detail else []
    paragraphs.extend(_notice_paragraphs(notice))
    body = "".join(f'<p style="margin:0 0 10px 0">{text}</p>' for text in paragraphs)
    footer = (
        '<div style="font-size:12px;color:#475569;margin-top:14px">'
        f"{html.escape(singapore_stamp(notice.observed_at))}</div>"
    )
    return _wrap(
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px">{body}{footer}</div>'
    )


def _notice_paragraphs(notice: Notice) -> tuple[str, ...]:
    if notice.kind == NOTICE_WATCHDOG:
        return (
            f"No {html.escape(notice.target.display)} security notes have been delivered by email.",
            "This alert is sent once per month when a delivery has not been recorded, "
            "so that the patch day is not missed silently.",
        )
    if notice.kind == NOTICE_UNVERIFIED:
        return (
            "SAP's support portal could not be reached, so this month's security notes "
            "could not be checked. This does not mean that no notes were published.",
            "The check runs again automatically, and the notes will be emailed if they appear.",
        )
    return (
        f"The {html.escape(notice.target.display)} security notes have not appeared on SAP's "
        "site yet.",
        "The check runs again automatically, and the notes will be emailed as soon as they are "
        "published.",
    )


def _bulletin_text(bulletin: Bulletin, summary: BulletinSummary) -> str:
    lines = [
        f"SAP Security Patch Day — {bulletin.target.display}",
        f"Source: {bulletin.source_url}",
        f"Retrieved: {singapore_stamp(bulletin.fetched_at)}",
        "",
    ]
    if bulletin.intro_text:
        lines.extend([bulletin.intro_text, ""])
    lines.append(
        f"{summary.total} entries: {summary.new_notes} new notes, {summary.updates} updates, "
        f"{summary.critical} critical, {summary.high} high, {summary.medium} medium, "
        f"{summary.low} low."
    )
    lines.append("")
    lines.extend(_text_row(index, note) for index, note in enumerate(bulletin.notes, start=1))
    lines.extend(["", _link_transition_sentence(bulletin)])
    return "\n".join(lines)


def _text_row(index: int, note: SecurityNote) -> str:
    details = [f"{index:>3}. {note.note_id} [{note.priority}, CVSS {note.cvss_score or 'n/a'}]"]
    details.append(f"     {note.headline}")
    if note.cve_ids:
        details.append(f"     {' '.join(note.cve_ids)}")
    if note.product:
        details.append(f"     Product: {note.product}")
    if note.versions:
        details.append(f"     Versions: {note.versions}")
    details.append(f"     {note.note_url}")
    return "\n".join(details)


def _notice_text(notice: Notice, config: RuntimeConfig) -> str:
    headline = _notice_subject_tail(notice)
    lines = [f"{config.subject_prefix} {headline}".strip()]
    if notice.detail:
        lines.extend(["", notice.detail])
    lines.extend(["", *(html.unescape(p) for p in _notice_paragraphs(notice))])
    lines.extend(["", singapore_stamp(notice.observed_at)])
    return "\n".join(lines)


def _safe_url(url: str) -> str:
    if not url:
        return ""
    scheme = urlparse(url).scheme.lower()
    return url if scheme in {"http", "https"} else ""
