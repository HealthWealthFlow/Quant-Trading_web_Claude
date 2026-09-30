"""EPUB (a ZIP of XHTML). Parsed with the safe ZIP reader + HTML parser; DRM-protected books are not processed.

Only sections are indexed; downstream code must search relevant sections instead of sending whole books to AI
(spec §21), and must not store large copied passages (spec §108).
"""

from __future__ import annotations

import posixpath

from lxml import etree

from ..taxonomy import AccessStatus
from .archive import UnsafeArchiveError, assert_safe_zip
from .base import DRM_PROTECTED, PARSE_ERROR, UNSAFE_ARCHIVE, HandlerResult, SourceHandler
from .html import parse_html_into
from .text import decode_text

_NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}
_SAFE_XML = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)


class EPUBHandler(SourceHandler):
    name = "EPUBHandler"
    version = "1"
    formats = ("epub",)

    def extract(self, data: bytes, fmt: str, base_url: str | None = None) -> HandlerResult:
        result = self.new_result(data, fmt)
        try:
            z = assert_safe_zip(data)
        except UnsafeArchiveError as e:
            result.access_status = AccessStatus.ERROR
            result.add_limitation(UNSAFE_ARCHIVE, str(e))
            return result.finalize()
        names = set(z.names())
        if "META-INF/encryption.xml" in names or "META-INF/rights.xml" in names:
            result.access_status = AccessStatus.ACCESS_RESTRICTED
            result.add_limitation(DRM_PROTECTED, "encrypted/DRM EPUB; not processed")
            return result.finalize()
        try:
            container = etree.fromstring(z.read("META-INF/container.xml"), _SAFE_XML)
            opf_path = container.find(".//c:rootfile", _NS).get("full-path")
            opf = etree.fromstring(z.read(opf_path), _SAFE_XML)
        except Exception as e:  # noqa: BLE001
            result.access_status = AccessStatus.ERROR
            result.add_limitation(PARSE_ERROR, f"invalid EPUB package: {str(e)[:150]}")
            return result.finalize()

        for key, tag in (("title", "title"), ("author", "creator"), ("publication_date", "date")):
            values = [el.text.strip() for el in opf.iterfind(f".//dc:{tag}", _NS) if el.text and el.text.strip()]
            if values:
                result.set_meta(key, "; ".join(values) if key == "author" else values[0])

        base = posixpath.dirname(opf_path)
        manifest = {i.get("id"): i.get("href") for i in opf.iterfind(".//opf:manifest/opf:item", _NS)}
        for ref in opf.iterfind(".//opf:spine/opf:itemref", _NS):
            href = manifest.get(ref.get("idref"))
            if not href:
                continue
            path = posixpath.normpath(posixpath.join(base, href))
            if path not in names:
                continue
            parse_html_into(result, decode_text(z.read(path), result), base_url=None, item=href)
        return result.finalize()
