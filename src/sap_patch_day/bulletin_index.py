"""Parsing of the "Dates for <year>" sections on the Security Notes & News index.

The index lists one entry per patch day. Early in a month the date is plain
text; on patch day it becomes a hyperlink to that month's bulletin. This module
reports that transition — it does **not** decide whether the bulletin exists
(see ``probe.py`` and addendum 0005).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from .htmltree import TEXT_NODE, Node, collapse, nearest_href, parse
from .models import MONTH_NAMES, IndexEntry, PatchDayMonth

DATES_HEADING = re.compile(r"^Dates for\s+(\d{4})$", re.IGNORECASE)
MONTH_DAY = re.compile(
    r"^(?P<month>" + "|".join(MONTH_NAMES) + r")\s+(?P<day>\d{1,2})(?:,\s*(?P<year>\d{4}))?$",
    re.IGNORECASE,
)
SECTION_STOP = re.compile(r"previous years", re.IGNORECASE)
MAX_SECTION_TEXT_LENGTH = 200


@dataclass(frozen=True)
class DatesSection:
    """One ``Dates for <year>`` section and its patch-day entries."""

    year: int
    entries: tuple[IndexEntry, ...]

    def entry_for(self, target: PatchDayMonth) -> IndexEntry | None:
        """The entry for ``target``'s month, or ``None`` when the section has none."""
        for entry in self.entries:
            if entry.month_name.lower() != target.month_name.lower():
                continue
            return entry
        return None


def parse_dates_sections(html_text: str) -> tuple[DatesSection, ...]:
    """Every ``Dates for <year>`` section on the page, in document order."""
    root = parse(html_text)
    nodes = tuple(root.iter_elements())
    return tuple(_sections(nodes))


def link_transition(html_text: str, target: PatchDayMonth) -> bool | None:
    """Whether ``target``'s date is a hyperlink on the index page.

    Returns ``None`` when the page has no matching section or entry, which
    callers must treat as "unknown", never as "not published".
    """
    for section in parse_dates_sections(html_text):
        if section.year != target.year:
            continue
        entry = section.entry_for(target)
        if entry is not None:
            return entry.is_linked
    return None


def _sections(nodes: Sequence[Node]) -> Iterator[DatesSection]:
    matched: list[Node] = []
    for index, node in enumerate(nodes):
        year = _heading_year(node)
        if year is None or any(ancestor in matched for ancestor in node.iter_ancestors()):
            continue
        matched.append(node)
        yield _section_from(nodes, index, year)


def _section_from(nodes: Sequence[Node], start: int, year: int) -> DatesSection:
    heading_level = _heading_level(nodes[start])
    entries: list[IndexEntry] = []
    for node in nodes[start + 1 :]:
        if _ends_section(node, heading_level):
            break
        entry = _entry_from(node)
        if entry is not None:
            entries.append(entry)
    return DatesSection(year=year, entries=tuple(entries))


def _heading_year(node: Node) -> int | None:
    match = DATES_HEADING.match(collapse(node.text_content))
    if match is None:
        return None
    return int(match.group(1))


def _heading_level(node: Node) -> int | None:
    if len(node.tag) == 2 and node.tag[0] == "h" and node.tag[1].isdigit():
        return int(node.tag[1])
    return None


def _ends_section(node: Node, heading_level: int | None) -> bool:
    level = _heading_level(node)
    if level is not None and heading_level is not None and level <= heading_level:
        return True
    return _is_shallow_stop(node)


def _is_shallow_stop(node: Node) -> bool:
    if _heading_level(node) is not None:
        return False
    text = collapse(node.text_content)
    if len(text) > MAX_SECTION_TEXT_LENGTH or not SECTION_STOP.search(text):
        return False
    return not any(
        SECTION_STOP.search(collapse(child.text_content)) for child in _element_children(node)
    )


def _element_children(node: Node) -> tuple[Node, ...]:
    """Element children only: text nodes are never a "deeper match"."""
    return tuple(child for child in node.children if child.tag != TEXT_NODE)


def _entry_from(node: Node) -> IndexEntry | None:
    """The patch-day entry this node represents, if it is the deepest such node."""
    match = MONTH_DAY.match(collapse(node.text_content))
    if match is None:
        return None
    if any(MONTH_DAY.match(collapse(child.text_content)) for child in _element_children(node)):
        return None
    return IndexEntry(
        month_name=match.group("month").capitalize(),
        day=int(match.group("day")),
        url=nearest_href(node) or None,
    )
