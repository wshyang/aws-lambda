"""Pure field parsers for one notes-table row.

Everything here takes strings and returns strings (or tuples of strings) so the
messy reality of SAP's markup can be tested exhaustively without a browser, a
network or a mock (spec FR-4.4 to FR-4.7).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

PRIORITIES: tuple[str, ...] = ("Critical", "High", "Medium", "Low")

CVE_PATTERN = re.compile(r"CVE-\d{4}-\d{4,7}", re.IGNORECASE)
GHSA_PATTERN = re.compile(r"GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}", re.IGNORECASE)
CVSS_VECTOR_PATTERN = re.compile(r"CVSS:\d\.\d(?:/[A-Za-z]+:[A-Za-z])+")
CVSS_SCORE_PATTERN = re.compile(r"\b(\d{1,2}\.\d)\b")
UPDATE_MARKER = re.compile(r"Update to Security Note released on\s+(?P<period>.+)", re.IGNORECASE)
BRACKET_NOISE = re.compile(r"[\[\]]")
PRODUCT_LABELS: tuple[str, ...] = ("Product", "Library", "Component")
VERSION_LABELS: tuple[str, ...] = ("Version(s)", "Versions", "Version")

SEPARATORS = "-–—:"


def split_lines(text: str) -> tuple[str, ...]:
    """Split a cell's text on line breaks, dropping empties and markdown-ish noise."""
    lines: list[str] = []
    for raw_line in text.replace("\u00a0", " ").splitlines():
        cleaned = clean_line(raw_line)
        if cleaned:
            lines.append(cleaned)
    return tuple(lines)


def clean_line(text: str) -> str:
    """Collapse whitespace, drop the ``**`` emphasis markers SAP's CMS emits."""
    return re.sub(r"\s+", " ", text.replace("**", " ")).strip(" :;-")


def extract_cve_ids(text: str) -> tuple[str, ...]:
    """Every distinct CVE identifier in ``text``, uppercased, in order of appearance."""
    seen: list[str] = []
    for match in CVE_PATTERN.finditer(text):
        identifier = match.group(0).upper()
        if identifier not in seen:
            seen.append(identifier)
    return tuple(seen)


def is_github_advisory(note_id: str, note_url: str) -> bool:
    """Whether a row comes from the GitHub advisory feed rather than me.sap.com."""
    if GHSA_PATTERN.match(note_id):
        return True
    return "github.com" in note_url.lower()


def normalize_priority(text: str) -> str:
    """Canonical ``Critical``/``High``/``Medium``/``Low``, else the raw text."""
    cleaned = clean_line(text)
    for priority in PRIORITIES:
        if cleaned.lower() == priority.lower():
            return priority
    return cleaned


def extract_cvss_score(text: str) -> str:
    """The displayed CVSS score, e.g. ``9.9``, or an empty string."""
    match = CVSS_SCORE_PATTERN.search(text)
    return match.group(1) if match else ""


def extract_cvss_vector(url: str) -> str:
    """The vector embedded in a FIRST calculator URL fragment, e.g. ``CVSS:3.0/AV:N/…``."""
    match = CVSS_VECTOR_PATTERN.search(url)
    return match.group(0) if match else ""


def extract_update_marker(lines: Sequence[str]) -> tuple[bool, str]:
    """Whether the row is an update to an earlier note, and which patch day it was."""
    for line in lines:
        match = UPDATE_MARKER.search(line)
        if match:
            return True, clean_line(match.group("period"))
    return False, ""


def labelled_value(lines: Iterable[str], labels: Sequence[str]) -> str:
    """Value of the first line labelled with one of ``labels``.

    Two shapes occur on SAP's page: ``Product - SAP NetWeaver`` (separator) and
    ``Version(s) < 0.45.2`` (no separator, the value starts with an operator).
    Both are matched explicitly, so a shorter label can never swallow a longer one.
    """
    for line in lines:
        for label in labels:
            value = _value_after_label(line, label)
            if value:
                return value
    return ""


def _value_after_label(line: str, label: str) -> str:
    escaped = re.escape(label)
    separated = re.match(rf"^{escaped}\s*[{SEPARATORS}]\s*(?P<value>.+)$", line, re.IGNORECASE)
    if separated:
        return separated.group("value").strip()
    operator_led = re.match(rf"^{escaped}\s*(?P<value>[<>].+)$", line, re.IGNORECASE)
    if operator_led:
        return operator_led.group("value").strip()
    return ""


def extract_product(lines: Iterable[str]) -> str:
    """Product (or library) a note applies to."""
    return labelled_value(lines, PRODUCT_LABELS)


def extract_versions(lines: Iterable[str]) -> str:
    """Version range a note applies to."""
    return labelled_value(lines, VERSION_LABELS)


def headline_from_lines(lines: Sequence[str]) -> str:
    """The vulnerability headline.

    The first line that is neither the update marker nor empty once the CVE
    bracket has been removed: the page wraps these cells across lines, so an
    emphasised CVE reference can occupy a line of its own.
    """
    for line in lines:
        if UPDATE_MARKER.search(line):
            continue
        headline = _strip_brackets_and_cves(line)
        if headline:
            return headline
    return ""


def _strip_brackets_and_cves(line: str) -> str:
    without_cves = CVE_PATTERN.sub("", line)
    without_noise = BRACKET_NOISE.sub(" ", without_cves)
    return re.sub(r"\s+", " ", without_noise).strip(" :;-")
