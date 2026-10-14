"""Extraction of the monthly notes table (spec FR-4).

The region extracted is exactly what the operator asked for: the table whose
header carries ``Note#``, ``Title``, ``Priority``, ``CVSS``, ending before the
paragraph that begins "To know more about the security researchers".
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence

from .errors import ParseError
from .htmltree import (
    Node,
    clean_text,
    collapse,
    first_href,
    inner_html,
    sanitize_fragment,
    text_with_breaks,
)
from .models import MONTH_NAMES, SOURCE_GITHUB, SOURCE_SAP, PatchDayMonth, SecurityNote
from .note_fields import (
    clean_line,
    extract_cve_ids,
    extract_cvss_score,
    extract_cvss_vector,
    extract_product,
    extract_update_marker,
    extract_versions,
    headline_from_lines,
    is_github_advisory,
    normalize_priority,
    split_lines,
)

HEADER_KEYWORDS: tuple[str, ...] = ("note#", "title", "priority", "cvss")
CELL_TAGS = ("td", "th")
END_MARKER = "to know more about the security researchers"
INTRO_MARKER = "security patch day saw the release of"
TITLE_MARKER = "security patch day"
PAGE_MONTH = re.compile(
    r"(?P<month>" + "|".join(MONTH_NAMES) + r")\s+(?P<year>\d{4})", re.IGNORECASE
)
HEADING_TAGS = ("h1", "h2", "h3", "h4")


def find_notes_table(root: Node) -> Node:
    """The bulletin's notes table, by its header row (spec FR-4.1)."""
    for table in root.find_all(_is_table):
        if _has_notes_header(table):
            return table
    raise ParseError(
        "No notes table found: expected a table header containing Note#, Title, Priority, CVSS"
    )


def extract_notes_table(root: Node) -> tuple[SecurityNote, ...]:
    """Every readable row of the bulletin's notes table (spec FR-4.2, FR-4.3)."""
    notes = tuple(_notes_from_rows(find_notes_table(root)))
    if not notes:
        raise ParseError("Notes table found but no row carried a note reference and a link")
    return notes


def extract_intro_text(root: Node) -> str:
    """SAP's own summary sentence for the month, or an empty string."""
    for node in root.iter_elements():
        text = collapse(node.text_content)
        if INTRO_MARKER not in text.lower():
            continue
        if _is_deepest_match(node, INTRO_MARKER):
            return _summary_lines(node)
    return ""


def _summary_lines(node: Node) -> str:
    """The ``<br>``-separated line(s) from the marker on (addendum 0011).

    The live page shares one paragraph between SAP's standing boilerplate and
    the month's summary, separated only by ``<br />``.
    """
    lines = [clean_text(line) for line in text_with_breaks(node).split("\n")]
    start = next(i for i, line in enumerate(lines) if INTRO_MARKER in line.lower())
    return " ".join(line for line in lines[start:] if line)


def extract_page_title(root: Node) -> str:
    """The page's ``SAP Security Patch Day – <Month> <Year>`` heading."""
    for tag in HEADING_TAGS:
        for node in root.find_all(_tagged(tag)):
            text = collapse(node.text_content)
            if TITLE_MARKER in text.lower() and _is_deepest_match(node, TITLE_MARKER):
                return clean_text(text)
    title_node = root.find_first(_tagged("title"))
    return clean_text(title_node.text_content) if title_node is not None else ""


def _tagged(tag: str) -> Callable[[Node], bool]:
    """A predicate for one tag; a factory so a loop never closes over itself."""
    return lambda element: element.tag == tag


def page_claims_month(page_title: str, target: PatchDayMonth) -> bool | None:
    """Whether the page claims to be ``target``'s month; ``None`` when it is silent."""
    match = PAGE_MONTH.search(page_title)
    if match is None:
        return None
    claimed_month = match.group("month").lower()
    claimed_year = int(match.group("year"))
    return claimed_month == target.month_name.lower() and claimed_year == target.year


def build_note(cells: Sequence[Node]) -> SecurityNote | None:
    """One table row as a :class:`SecurityNote`, or ``None`` for non-note rows."""
    if len(cells) < 2:
        return None
    note_id, note_url = _note_reference(cells[0])
    if not note_id or not note_url:
        return None
    title_html, lines = _title_parts(cells[1])
    is_update, update_period = extract_update_marker(lines)
    return SecurityNote(
        note_id=note_id,
        note_url=note_url,
        source=SOURCE_GITHUB if is_github_advisory(note_id, note_url) else SOURCE_SAP,
        title_html=title_html,
        headline=headline_from_lines(lines),
        priority=_cell_text(cells, 2, normalize_priority),
        cvss_score=_cell_text(cells, 3, extract_cvss_score),
        cve_ids=extract_cve_ids(cell_text(cells[1])),
        product=extract_product(lines),
        versions=extract_versions(lines),
        cvss_vector=extract_cvss_vector(first_href(cells[3]) if len(cells) > 3 else ""),
        cvss_url=first_href(cells[3]) if len(cells) > 3 else "",
        is_update=is_update,
        update_period=update_period,
    )


def cell_text(cell: Node) -> str:
    """Readable text of a cell: ``<br>`` becomes a newline, whitespace collapsed."""
    return "\n".join(clean_line(line) for line in text_with_breaks(cell).splitlines())


def _title_parts(cell: Node) -> tuple[str, tuple[str, ...]]:
    """The title cell as (sanitised markup for the email, readable text lines)."""
    return sanitize_fragment(inner_html(cell)), split_lines(cell_text(cell))


def _note_reference(cell: Node) -> tuple[str, str]:
    return clean_text(cell.text_content), first_href(cell)


def _notes_from_rows(table: Node) -> Iterator[SecurityNote]:
    for cells in _body_rows(table):
        if _is_end_marker(cells):
            return
        note = build_note(cells)
        if note is not None:
            yield note


def _body_rows(table: Node) -> Iterator[Sequence[Node]]:
    for row in table.find_all(_is_row):
        if _nearest_table(row) is not table:
            continue
        cells = _cells(row)
        if cells and not _is_notes_header_row(cells):
            yield cells


def _cells(row: Node) -> list[Node]:
    return [child for child in row.children if child.tag in CELL_TAGS]


def _is_end_marker(cells: Sequence[Node]) -> bool:
    return any(collapse(cell.text_content).lower().startswith(END_MARKER) for cell in cells)


def _is_notes_header_row(cells: Sequence[Node]) -> bool:
    header_text = " ".join(collapse(cell.text_content).lower() for cell in cells)
    return all(keyword in header_text for keyword in HEADER_KEYWORDS)


def _has_notes_header(table: Node) -> bool:
    first_row = table.find_first(_is_row)
    return first_row is not None and _is_notes_header_row(_cells(first_row))


def _cell_text(cells: Sequence[Node], index: int, transform: Callable[[str], str]) -> str:
    if index >= len(cells):
        return ""
    return transform(cell_text(cells[index]))


def _is_table(node: Node) -> bool:
    return node.tag == "table"


def _is_row(node: Node) -> bool:
    return node.tag == "tr"


def _nearest_table(node: Node) -> Node | None:
    for ancestor in node.iter_ancestors():
        if ancestor.tag == "table":
            return ancestor
    return None


def _is_deepest_match(node: Node, marker: str) -> bool:
    return not any(
        marker in collapse(child.text_content).lower()
        for child in node.iter_elements()
        if child is not node
    )
