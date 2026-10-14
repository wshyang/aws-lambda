"""The HTML document model and the sanitiser (spec FR-5.6)."""

from __future__ import annotations

import pytest

from sap_patch_day.htmltree import (
    Node,
    clean_text,
    collapse,
    first_href,
    inner_html,
    nearest_href,
    parse,
    sanitize_fragment,
    strip_opens_in_new_tab,
    text_with_breaks,
)


def test_text_content_and_own_text_differ_for_nested_nodes() -> None:
    root = parse("<div><p>outer <b>bold</b> tail</p></div>")
    paragraph = root.find_first(lambda node: node.tag == "p")
    assert paragraph is not None
    assert paragraph.own_text == "outer  tail"
    assert collapse(paragraph.text_content) == "outer bold tail"


def test_document_order_iteration_includes_self_first() -> None:
    root = parse("<div><p>a</p><span>b</span></div>")
    tags = [node.tag for node in root.iter_elements()]
    assert tags == ["#document", "div", "p", "span"]


def test_unclosed_paragraphs_are_closed_implicitly() -> None:
    root = parse("<div><p>first<p>second</div>")
    paragraphs = [node.text_content for node in root.find_all(lambda node: node.tag == "p")]
    assert paragraphs == ["first", "second"]


def test_unclosed_table_cells_are_closed_implicitly() -> None:
    root = parse("<table><tr><td>one<td>two<tr><td>three</table>")
    rows = root.find_all(lambda node: node.tag == "tr")
    assert len(rows) == 2
    cells = [cell.text_content for cell in rows[0].children if cell.tag == "td"]
    assert cells == ["one", "two"]


def test_void_elements_do_not_swallow_following_content() -> None:
    root = parse("<td>headline<br>Product - SAP NetWeaver<br>Version(s) - KERNEL 7.93</td>")
    cell = root.find_first(lambda node: node.tag == "td")
    assert cell is not None
    assert text_with_breaks(cell) == "headline\nProduct - SAP NetWeaver\nVersion(s) - KERNEL 7.93"


def test_comments_are_dropped() -> None:
    root = parse("<p>before<!-- secret -->after</p>")
    assert collapse(root.text_content) == "beforeafter"


def test_entities_are_decoded() -> None:
    root = parse("<p>SAP S/4HANA &ndash; Version(s) &lt; 0.45.2 &amp; more</p>")
    assert collapse(root.text_content) == "SAP S/4HANA – Version(s) < 0.45.2 & more"


def test_collapse_normalises_non_breaking_spaces_and_newlines() -> None:
    assert collapse("a\u00a0b\n\t c   d") == "a b c d"


def test_strip_opens_in_new_tab_variants() -> None:
    assert strip_opens_in_new_tab("GHSA-4mv3-jxxm-9cx2(opens in new tab)") == "GHSA-4mv3-jxxm-9cx2"
    assert strip_opens_in_new_tab("note (OPENS IN NEW TAB)") == "note "
    assert clean_text("3797111 (opens in new tab)") == "3797111"


def test_first_href_and_nearest_href() -> None:
    root = parse('<a href="/outer"><b>January 13</b></a><p><i>x</i></p>')
    bold = root.find_first(lambda node: node.tag == "b")
    paragraph = root.find_first(lambda node: node.tag == "p")
    assert bold is not None and paragraph is not None
    assert nearest_href(bold) == "/outer"
    assert nearest_href(paragraph) == ""
    assert first_href(paragraph) == ""


def test_inner_html_round_trips_markup() -> None:
    root = parse('<div><b>bold</b><br><a href="https://example.test">link</a> &amp; tail</div>')
    div = root.find_first(lambda node: node.tag == "div")
    assert div is not None
    assert inner_html(div) == '<b>bold</b><br><a href="https://example.test">link</a> &amp; tail'


@pytest.mark.parametrize(
    "fragment,expected_text",
    [
        ("<script>alert(1)</script>", ""),
        ("<style>body{background:red}</style>", ""),
        ('<a href="javascript:alert(1)">x</a>', "x"),
        ('<a href="data:text/html;base64,PHNjcmlwdD4=">x</a>', "x"),
        ('<span onclick="steal()">x</span>', "x"),
        ("<iframe src='https://evil.example.test'></iframe>", ""),
        ("<img src='https://evil.example.test/pixel.png'>", ""),
        ("<!-- comment -->", ""),
        ("<svg onload='x()'></svg>", ""),
    ],
)
def test_sanitiser_removes_hostile_markup(fragment: str, expected_text: str) -> None:
    cleaned = sanitize_fragment(fragment)
    forbidden = [
        "script",
        "onclick",
        "onload",
        "javascript:",
        "data:",
        "iframe",
        "img",
        "svg",
        "<!--",
        "style",
    ]
    assert not any(token in cleaned for token in forbidden)
    assert cleaned == expected_text


def test_sanitiser_keeps_allowlisted_markup_and_filters_attributes() -> None:
    cleaned = sanitize_fragment(
        '<a href="https://me.sap.com/notes/1" class="x" target="_blank" onclick="y()">'
        "<b>note</b></a>"
    )
    assert (
        cleaned == '<a href="https://me.sap.com/notes/1" rel="noopener noreferrer"><b>note</b></a>'
    )


def test_sanitiser_escapes_text_that_looks_like_markup() -> None:
    assert sanitize_fragment("<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>") == (
        "&lt;script&gt;alert(1)&lt;/script&gt;"
    )


def test_sanitiser_removes_link_decorations() -> None:
    cleaned = sanitize_fragment('<a href="https://example.test">3797111(opens in new tab)</a>')
    assert cleaned == '<a href="https://example.test" rel="noopener noreferrer">3797111</a>'


def test_node_attr_helpers() -> None:
    node = Node("a", {"href": "https://example.test"})
    assert node.attr("href") == "https://example.test"
    assert node.attr("missing", "fallback") == "fallback"
