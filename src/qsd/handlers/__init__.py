"""Format detection and handler registry. Entry points: `parse_bytes`, `parse_file`."""

from __future__ import annotations

from pathlib import Path

from ..taxonomy import AccessStatus
from .archive import UnsafeArchiveError, assert_safe_zip
from .base import (
    PARSE_ERROR,
    TRUNCATED,
    UNSAFE_ARCHIVE,
    HandlerResult,
    Location,
    SourceHandler,
    Table,
    TextBlock,
    sha256_bytes,
)
from .epub import EPUBHandler
from .html import HTMLHandler
from .office import DOCXHandler, PPTXHandler
from .pdf import PDFHandler
from .spreadsheet import SpreadsheetHandler
from .text import PlainTextHandler

__all__ = ["HandlerResult", "Location", "Table", "TextBlock", "detect_format", "parse_bytes", "parse_file",
           "SUPPORTED_FORMATS", "sha256_bytes"]

EXTENSION_FORMATS = {
    ".txt": "txt", ".text": "txt", ".log": "txt", ".rst": "txt",
    ".md": "md", ".markdown": "md",
    ".json": "json", ".xml": "xml", ".rss": "xml", ".atom": "xml",
    ".py": "code", ".ipynb": "json", ".r": "code", ".pine": "code", ".mq4": "code", ".mq5": "code",
    ".mqh": "code", ".cpp": "code", ".h": "code", ".js": "code", ".ts": "code",
    ".html": "html", ".htm": "html", ".xhtml": "html",
    ".pdf": "pdf", ".docx": "docx", ".docm": "docx", ".pptx": "pptx", ".pptm": "pptx",
    ".csv": "csv", ".xlsx": "xlsx", ".xlsm": "xlsx", ".epub": "epub", ".zip": "zip",
    # Recognised but not parsed in Phase 1 (reported as UNSUPPORTED_FORMAT, never guessed at):
    ".doc": "doc", ".rtf": "rtf", ".odt": "odt", ".ppt": "ppt", ".xls": "xls", ".mobi": "mobi", ".azw": "azw",
    ".azw3": "azw3", ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image", ".tif": "image",
    ".tiff": "image", ".mp3": "audio", ".wav": "audio", ".m4a": "audio", ".aac": "audio", ".flac": "audio",
    ".ogg": "audio", ".mp4": "video", ".mov": "video", ".mkv": "video", ".webm": "video", ".avi": "video",
    ".m4v": "video",
}

_HANDLERS: list[SourceHandler] = [
    PlainTextHandler(), HTMLHandler(), PDFHandler(), DOCXHandler(), PPTXHandler(), SpreadsheetHandler(),
    EPUBHandler(),
]
SUPPORTED_FORMATS = sorted({f for h in _HANDLERS for f in h.formats} | {"zip"})


def _zip_kind(data: bytes) -> str:
    try:
        names = set(assert_safe_zip(data).names())
    except UnsafeArchiveError:
        return "zip"
    if "mimetype" in names and "META-INF/container.xml" in names:
        return "epub"
    if "word/document.xml" in names:
        return "docx"
    if "ppt/presentation.xml" in names:
        return "pptx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    return "zip"


def detect_format(name: str | None, data: bytes, content_type: str | None = None) -> str:
    """Magic bytes first (content can lie about its name), then content type, then extension."""
    head = data[:1024].lstrip()
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:4] == b"PK\x03\x04":
        return _zip_kind(data)
    ext = Path(name).suffix.lower() if name else ""
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in ("text/html", "application/xhtml+xml") or head[:15].lower().startswith((b"<!doctype html", b"<html")):
        return "html"
    if ct == "application/pdf":
        return "pdf"
    if ext in EXTENSION_FORMATS:
        return EXTENSION_FORMATS[ext]
    if ct in ("application/json",):
        return "json"
    if ct in ("application/xml", "text/xml", "application/rss+xml", "application/atom+xml"):
        return "xml"
    if ct.startswith("text/"):
        return "txt"
    return "unknown"


def _unsupported(data: bytes, fmt: str) -> HandlerResult:
    result = HandlerResult(handler="UnsupportedHandler", handler_version="1", format=fmt,
                           sha256=sha256_bytes(data), size=len(data), access_status=AccessStatus.UNSUPPORTED_FORMAT)
    result.add_limitation("UNSUPPORTED_FORMAT", f"no Phase 1 handler for '{fmt}'")
    return result.finalize()


MAX_ARCHIVE_MEMBERS_PARSED = 200


def _parse_zip(data: bytes) -> HandlerResult:
    """Parse supported members of a ZIP (one level deep; nested archives are not opened)."""
    result = HandlerResult(handler="ArchiveHandler", handler_version="1", format="zip",
                           sha256=sha256_bytes(data), size=len(data))
    try:
        z = assert_safe_zip(data)
    except UnsafeArchiveError as e:
        result.access_status = AccessStatus.ERROR
        result.add_limitation(UNSAFE_ARCHIVE, str(e))
        return result.finalize()
    parsed = 0
    for name in z.names():
        member = z.read(name)
        fmt = detect_format(name, member)
        if fmt in ("zip", "unknown") or fmt not in SUPPORTED_FORMATS:
            result.add_limitation("SKIPPED_MEMBER", f"{name} ({fmt})")
            continue
        if parsed >= MAX_ARCHIVE_MEMBERS_PARSED:
            result.add_limitation(TRUNCATED, f"only first {MAX_ARCHIVE_MEMBERS_PARSED} members parsed")
            break
        sub = parse_bytes(member, name=name)
        parsed += 1
        for b in sub.blocks:
            loc = b.location
            item = f"{name} {loc.item}" if loc.item else name
            result.blocks.append(TextBlock(b.text, Location(page=loc.page, slide=loc.slide, section=loc.section,
                                                            sheet=loc.sheet, item=item)))
        result.tables.extend(sub.tables)
        result.links.extend(sub.links)
        result.limitations.extend(f"{name}: {lim}" for lim in sub.limitations)
    return result.finalize()


def parse_bytes(data: bytes, name: str | None = None, content_type: str | None = None,
                base_url: str | None = None) -> HandlerResult:
    fmt = detect_format(name, data, content_type)
    if fmt == "zip":
        return _parse_zip(data)
    for handler in _HANDLERS:
        if handler.can_handle(fmt):
            try:
                return handler.extract(data, fmt, base_url=base_url)
            except Exception as e:  # noqa: BLE001 — last-resort guard; handlers already catch known errors
                result = handler.new_result(data, fmt)
                result.access_status = AccessStatus.ERROR
                result.add_limitation(PARSE_ERROR, f"{type(e).__name__}: {str(e)[:200]}")
                return result.finalize()
    return _unsupported(data, fmt)


def parse_file(path: str | Path, max_bytes: int = 200 * 1024 * 1024) -> HandlerResult:
    """Read a local file (read-only; the file is never modified, spec §111)."""
    p = Path(path)
    size = p.stat().st_size
    if size > max_bytes:
        result = HandlerResult(handler="none", handler_version="1", format=detect_format(p.name, b""),
                               sha256="", size=size, access_status=AccessStatus.ERROR)
        result.add_limitation("FILE_TOO_LARGE", f"{size} bytes > {max_bytes}")
        return result
    with p.open("rb") as fh:
        data = fh.read()
    return parse_bytes(data, name=p.name)
