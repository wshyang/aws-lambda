"""Field-level parsing of one notes-table row (spec FR-4.4 to FR-4.7)."""

from __future__ import annotations

import pytest

from sap_patch_day.note_fields import (
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

TITLE_CELL_TEXT = "\n".join(
    [
        "[ CVE-2026-89107 ] Code Injection vulnerability in SAP S/4HANA "
        "(Manage Substitution/Validation Rules)",
        "Product - SAP S/4HANA (Manage Substitution/Validation Rules)",
        "Version(s) - S4CORE 105, 106, 107, 108, 109",
    ]
)


def test_extract_cve_ids_is_ordered_deduplicated_and_uppercased() -> None:
    text = "cve-2026-89107 and CVE-2026-89107 and CVE-2025-42938"
    assert extract_cve_ids(text) == ("CVE-2026-89107", "CVE-2025-42938")


def test_extract_cve_ids_returns_empty_tuple_without_matches() -> None:
    assert extract_cve_ids("no identifiers here") == ()


def test_is_github_advisory_by_identifier_and_by_host() -> None:
    assert is_github_advisory("GHSA-4mv3-jxxm-9cx2", "https://github.com/x/y")
    assert is_github_advisory("1234567", "https://github.com/SAP/repo/security/advisories/GHSA-1")
    assert not is_github_advisory("3797111", "https://me.sap.com/notes/3797111")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Critical", "Critical"),
        ("critical", "Critical"),
        (" High ", "High"),
        ("MEDIUM", "Medium"),
        ("Informational", "Informational"),
        ("", ""),
    ],
)
def test_normalize_priority(text: str, expected: str) -> None:
    assert normalize_priority(text) == expected


def test_extract_cvss_score() -> None:
    assert extract_cvss_score("9.9") == "9.9"
    assert extract_cvss_score(" CVSS 10.0 ") == "10.0"
    assert extract_cvss_score("n/a") == ""


def test_extract_cvss_vector_from_calculator_url() -> None:
    url = "https://www.first.org/cvss/calculator/3.0#CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H"
    assert extract_cvss_vector(url) == "CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H"
    assert extract_cvss_vector("https://example.test") == ""


def test_extract_product_and_versions_from_labelled_lines() -> None:
    lines = split_lines(TITLE_CELL_TEXT)
    assert extract_product(lines) == "SAP S/4HANA (Manage Substitution/Validation Rules)"
    assert extract_versions(lines) == "S4CORE 105, 106, 107, 108, 109"


def test_extract_product_falls_back_to_library_label_for_github_advisories() -> None:
    lines = split_lines("Library - SAP Cloud SDK for Python\nVersion(s) < 0.45.2")
    assert extract_product(lines) == "SAP Cloud SDK for Python"
    assert extract_versions(lines) == "< 0.45.2"


def test_extract_product_is_empty_when_no_labelled_line_exists() -> None:
    assert extract_product(split_lines("Just a headline")) == ""


def test_split_lines_drops_markdown_noise_and_empty_lines() -> None:
    raw = (
        "\n\n**Update to Security Note released on March 2026 Patch Day:**\n\n"
        "Product **-** SAP ABAP\n\n"
    )
    assert split_lines(raw) == (
        "Update to Security Note released on March 2026 Patch Day",
        "Product - SAP ABAP",
    )


def test_extract_update_marker() -> None:
    lines = split_lines(
        "Update to Security Note released on March 2026 Patch Day:\n[ CVE-2026-24316 ] SSRF"
    )
    assert extract_update_marker(lines) == (True, "March 2026 Patch Day")


def test_extract_update_marker_absent() -> None:
    assert extract_update_marker(split_lines(TITLE_CELL_TEXT)) == (False, "")


def test_headline_skips_the_update_marker_line() -> None:
    lines = split_lines(
        "Update to Security Note released on March 2026 Patch Day:\n"
        "[ CVE-2026-24316 ] Server-Side Request Forgery (SSRF) in SAP NetWeaver "
        "Application Server for ABAP\n"
        "Product - SAP NetWeaver Application Server for ABAP"
    )
    assert headline_from_lines(lines) == (
        "Server-Side Request Forgery (SSRF) in SAP NetWeaver Application Server for ABAP"
    )


def test_headline_removes_cve_brackets_from_the_first_line() -> None:
    lines = split_lines(TITLE_CELL_TEXT)
    assert headline_from_lines(lines) == (
        "Code Injection vulnerability in SAP S/4HANA (Manage Substitution/Validation Rules)"
    )


def test_clean_line_strips_emphasis_and_edge_punctuation() -> None:
    assert clean_line("**Bold** text: ;-") == "Bold text"
