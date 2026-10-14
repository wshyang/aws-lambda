"""A small, tolerant HTML document model built on the standard library.

Why not ``html.parser`` directly: every scraper needs to ask questions like
"what text is inside this node, but not its descendants?" and "in what document
order do these nodes appear?". This module answers those questions once, so the
page-specific modules stay small and testable.

Deliberate limits: no CSS selector engine, no namespace handling, no scripting
model. Anything a page-specific module needs beyond this belongs in that module.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from html.parser import HTMLParser

TEXT_NODE = "#text"
DOCUMENT_NODE = "#document"

VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

#: Elements whose subtree is removed outright by the sanitiser.
DROPPED_SUBTREES = frozenset(
    {
        "script",
        "style",
        "noscript",
        "iframe",
        "svg",
        "template",
        "object",
        "embed",
        "form",
        "input",
        "button",
    }
)

#: Elements the sanitiser keeps (spec FR-5.6).
ALLOWED_TAGS = frozenset({"a", "strong", "b", "em", "i", "br"})

_ALLOWED_HREF_PREFIXES = ("http://", "https://", "mailto:")
_DECORATION = re.compile(r"\(\s*opens in new tab\s*\)", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")

_IMPLICIT_CLOSE: Mapping[str, frozenset[str]] = {
    "p": frozenset({"p"}),
    "li": frozenset({"li"}),
    "td": frozenset({"td", "th"}),
    "th": frozenset({"td", "th"}),
    "tr": frozenset({"tr", "td", "th", "thead", "tbody", "tfoot"}),
    "thead": frozenset({"thead", "tbody", "tfoot", "tr", "td", "th"}),
    "tbody": frozenset({"thead", "tbody", "tfoot", "tr", "td", "th"}),
    "tfoot": frozenset({"thead", "tbody", "tfoot", "tr", "td", "th"}),
    "option": frozenset({"option"}),
    "dt": frozenset({"dt", "dd"}),
    "dd": frozenset({"dt", "dd"}),
}


class Node:
    """One element or text node. Read-only once a document has been parsed."""

    __slots__ = ("attrs", "children", "parent", "tag")

    def __init__(
        self,
        tag: str,
        attrs: Mapping[str, str] | None = None,
        parent: Node | None = None,
    ) -> None:
        self.tag = tag
        self.attrs = dict(attrs or {})
        self.children: list[Node] = []
        self.parent = parent

    # -- construction (used only by the builder) -------------------------

    def add_child(self, node: Node) -> None:
        node.parent = self
        self.children.append(node)

    def add_text(self, text: str) -> None:
        if self.children and self.children[-1].tag == TEXT_NODE:
            self.children[-1].attrs["data"] += text
            return
        text_node = Node(TEXT_NODE, {"data": text}, self)
        self.children.append(text_node)

    # -- queries ---------------------------------------------------------

    def attr(self, name: str, default: str = "") -> str:
        """Attribute value, or ``default`` when absent."""
        return self.attrs.get(name, default)

    @property
    def own_text(self) -> str:
        """Text directly inside this node, excluding descendants."""
        if self.tag == TEXT_NODE:
            return self.attrs.get("data", "")
        return "".join(child.own_text for child in self.children if child.tag == TEXT_NODE)

    @property
    def text_content(self) -> str:
        """Text of this node and every descendant, in document order."""
        if self.tag == TEXT_NODE:
            return self.own_text
        return "".join(child.text_content for child in self.children)

    def iter_elements(self) -> Iterator[Node]:
        """Every element in this subtree (pre-order), excluding text nodes."""
        if self.tag != TEXT_NODE:
            yield self
        for child in self.children:
            if child.tag != TEXT_NODE:
                yield from child.iter_elements()

    def iter_ancestors(self) -> Iterator[Node]:
        """Ancestors, nearest first."""
        current = self.parent
        while current is not None:
            yield current
            current = current.parent

    def find_all(self, predicate: Callable[[Node], bool]) -> list[Node]:
        """Every element in this subtree matching ``predicate``."""
        return [element for element in self.iter_elements() if predicate(element)]

    def find_first(self, predicate: Callable[[Node], bool]) -> Node | None:
        """First element in document order matching ``predicate``, else ``None``."""
        for element in self.iter_elements():
            if predicate(element):
                return element
        return None


class _TreeBuilder(HTMLParser):
    """Builds a :class:`Node` tree, closing the tags real pages forget to close."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node(DOCUMENT_NODE)
        self._stack: list[Node] = [self.root]

    def handle_starttag(self, tag: str, attrs: Sequence[tuple[str, str | None]]) -> None:
        self._close_implicitly(tag)
        node = Node(tag, {key: value or "" for key, value in attrs}, self._stack[-1])
        self._stack[-1].add_child(node)
        if tag not in VOID_ELEMENTS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: Sequence[tuple[str, str | None]]) -> None:
        self._close_implicitly(tag)
        node = Node(tag, {key: value or "" for key, value in attrs}, self._stack[-1])
        self._stack[-1].add_child(node)

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_ELEMENTS:
            return
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].add_text(data)

    def handle_comment(self, data: str) -> None:
        """Comments are dropped: they are never rendered and only add risk."""

    def _close_implicitly(self, tag: str) -> None:
        closable = _IMPLICIT_CLOSE.get(tag, frozenset())
        while len(self._stack) > 1 and self._stack[-1].tag in closable:
            self._stack.pop()


def parse(html_text: str) -> Node:
    """Parse a document (or fragment) into a tree rooted at a ``#document`` node."""
    builder = _TreeBuilder()
    builder.feed(html_text)
    builder.close()
    return builder.root


def collapse(text: str) -> str:
    """Collapse whitespace runs (including non-breaking space) and trim."""
    return _WHITESPACE.sub(" ", text.replace("\u00a0", " ")).strip()


def strip_opens_in_new_tab(text: str) -> str:
    """Remove the ``(opens in new tab)`` decoration from a link's visible text."""
    return _DECORATION.sub("", text)


def clean_text(text: str) -> str:
    """Text as a human reads it: decoration removed, whitespace collapsed."""
    return collapse(strip_opens_in_new_tab(text))


def first_href(node: Node) -> str:
    """Href of the first anchor in this subtree, or an empty string."""
    for element in node.iter_elements():
        if element.tag == "a" and element.attr("href"):
            return element.attr("href")
    return ""


def nearest_href(node: Node, max_levels: int = 2) -> str:
    """Href on this node, inside it, or on one of its ``max_levels`` nearest ancestors."""
    href = first_href(node)
    if href:
        return href
    for level, ancestor in enumerate(node.iter_ancestors(), start=1):
        if level > max_levels:
            break
        if ancestor.tag == "a" and ancestor.attr("href"):
            return ancestor.attr("href")
    return ""


def inner_html(node: Node) -> str:
    """Serialise a subtree back to markup.

    Used to hand a table cell to the sanitiser as markup (structure intact)
    rather than as text, so anchors and line breaks survive.
    """
    return "".join(_render_markup(child) for child in node.children)


def _render_markup(node: Node) -> str:
    if node.tag == TEXT_NODE:
        return html.escape(node.attrs.get("data", ""), quote=False)
    if node.tag == DOCUMENT_NODE:
        return inner_html(node)
    attrs = "".join(
        f' {name}="{html.escape(value, quote=True)}"' for name, value in node.attrs.items()
    )
    if node.tag in VOID_ELEMENTS:
        return f"<{node.tag}{attrs}>"
    return f"<{node.tag}{attrs}>{inner_html(node)}</{node.tag}>"


def text_with_breaks(node: Node) -> str:
    """Plain text of a subtree, with ``<br>`` rendered as newlines.

    The notes table uses ``<br>`` to separate the headline, the product and the
    version range inside a single cell, so callers need those boundaries.
    """
    return "".join(_render_plain(child) for child in node.children)


def _render_plain(node: Node) -> str:
    if node.tag == TEXT_NODE:
        return node.attrs.get("data", "")
    if node.tag in DROPPED_SUBTREES:
        return ""
    if node.tag == "br":
        return "\n"
    return "".join(_render_plain(child) for child in node.children)


def sanitize_fragment(html_text: str) -> str:
    """Return markup restricted to the allowlist in ``ALLOWED_TAGS`` (spec FR-5.6).

    Everything else is dropped; text is kept except inside
    :data:`DROPPED_SUBTREES`, and anchors keep only a safe ``href``.
    """
    root = parse(html_text)
    return "".join(_render_allowed(child) for child in root.children).strip()


def _render_allowed(node: Node) -> str:
    if node.tag == TEXT_NODE:
        return html.escape(node.own_text, quote=False)
    if node.tag in DROPPED_SUBTREES:
        return ""
    if node.tag not in ALLOWED_TAGS:
        return "".join(_render_allowed(child) for child in node.children)
    return _render_allowed_element(node)


def _render_allowed_element(node: Node) -> str:
    if node.tag == "br":
        return "<br>"
    inner = "".join(_render_allowed(child) for child in node.children)
    if node.tag != "a":
        return f"<{node.tag}>{inner}</{node.tag}>"
    href = _safe_href(node.attr("href"))
    if not href:
        return inner
    return (
        f'<a href="{html.escape(href, quote=True)}" rel="noopener noreferrer">'
        f"{strip_opens_in_new_tab(inner)}</a>"
    )


def _safe_href(href: str) -> str:
    candidate = collapse(href)
    if candidate.lower().startswith(_ALLOWED_HREF_PREFIXES):
        return candidate
    return ""
