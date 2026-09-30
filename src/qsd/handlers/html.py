"""HTML pages. Scripts are removed and never run (spec §7). Academic `citation_*` meta tags are read when present."""

from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from .base import PARSE_ERROR, HandlerResult, Location, SourceHandler, Table, TextBlock
from .text import decode_text

_DROP_TAGS = ("script", "style", "noscript", "iframe", "object", "embed", "form", "svg", "template", "button")
_BOILERPLATE = ("nav", "footer", "aside", "header")
_BLOCK_TAGS = ("p", "li", "pre", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "td", "dd", "dt", "figcaption")

_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_INLINE_TAGS = ("a", "span", "b", "i", "em", "strong", "code", "sub", "sup", "small", "abbr", "cite", "q", "mark",
                "br", "time", "u", "s")

_META_KEYS = {
    "title": ("citation_title", "dc.title", "og:title", "twitter:title"),
    "author": ("citation_author", "author", "dc.creator", "article:author"),
    "publication_date": ("citation_publication_date", "citation_date", "article:published_time", "dc.date",
                         "date", "pubdate"),
}


def _meta(soup: BeautifulSoup, names: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for name in names:
        for tag in soup.find_all("meta", attrs={"name": name}) + soup.find_all("meta", attrs={"property": name}):
            content = (tag.get("content") or "").strip()
            if content:
                values.append(content)
        if values:
            break
    return values


def parse_html_into(result: HandlerResult, html: str, base_url: str | None, item: str | None = None) -> None:
    """Shared by the HTML and EPUB handlers."""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:  # noqa: BLE001 — any parser failure becomes a recorded limitation
        result.add_limitation(PARSE_ERROR, str(e)[:200])
        return

    for key, names in _META_KEYS.items():
        values = _meta(soup, names)
        if values and result.metadata.get(key) == "UNKNOWN":
            result.set_meta(key, "; ".join(dict.fromkeys(values)) if key == "author" else values[0])
    if result.metadata["title"] == "UNKNOWN" and soup.title and soup.title.string:
        result.set_meta("title", soup.title.string)
    for pdf in _meta(soup, ("citation_pdf_url",)):
        result.links.append(urljoin(base_url or "", pdf))

    for tag in soup(_DROP_TAGS):
        tag.decompose()
    body = soup.find("article") or soup.find("main") or soup.body or soup
    for tag in body.find_all(_BOILERPLATE):
        tag.decompose()

    for a in body.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("javascript:", "mailto:", "#", "data:")):
            continue
        result.links.append(urljoin(base_url or "", href))

    for i, tbl in enumerate(body.find_all("table"), 1):
        rows = [[c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])] for tr in tbl.find_all("tr")]
        rows = [r for r in rows if any(r)]
        if rows:
            result.tables.append(Table(rows, Location(item=f"{item + ' ' if item else ''}table {i}")))

    state = {"section": None}
    _walk(body, result, state, item)


def _emit(text: str, result: HandlerResult, state: dict, item: str | None, heading: bool = False) -> None:
    text = " ".join(text.split())
    if not text:
        return
    if heading:
        state["section"] = text[:200]
    result.blocks.append(TextBlock(text, Location(section=state["section"], item=item)))


def _walk(el: Tag, result: HandlerResult, state: dict, item: str | None) -> None:
    """Document-order walk: leaf block tags give their full text; loose text in containers (div, section...) is
    kept too, so content outside <p> tags is not silently lost."""
    loose: list[str] = []
    for child in el.children:
        if isinstance(child, NavigableString):
            if not isinstance(child, Comment):
                loose.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        if child.name in _BLOCK_TAGS and not child.find(_BLOCK_TAGS):
            _emit("".join(loose), result, state, item)
            loose.clear()
            _emit(child.get_text(" ", strip=True), result, state, item, heading=child.name in _HEADINGS)
        elif child.name in _INLINE_TAGS:
            loose.append(child.get_text(" ", strip=True))
        else:
            _emit("".join(loose), result, state, item)
            loose.clear()
            _walk(child, result, state, item)
    _emit("".join(loose), result, state, item)


class HTMLHandler(SourceHandler):
    name = "HTMLHandler"
    version = "1"
    formats = ("html",)

    def extract(self, data: bytes, fmt: str, base_url: str | None = None) -> HandlerResult:
        result = self.new_result(data, fmt)
        parse_html_into(result, decode_text(data, result), base_url)
        return result.finalize()
