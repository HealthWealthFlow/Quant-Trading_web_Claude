"""Source handler interface (spec §6).

A handler turns raw bytes into a `HandlerResult`. It parses and never executes (spec §7): no macros, no scripts,
no formulas are evaluated. The spec's interface methods map onto this design as follows:

    can_handle()                -> SourceHandler.can_handle
    inspect() / extract_*()     -> SourceHandler.extract returns everything in one pass (cheaper than re-parsing):
                                   metadata, blocks (text + structure with locations), links, references, tables
    extract_media_transcript()  -> Phase 2 (audio/video handlers)
    calculate_hash()            -> sha256_bytes / HandlerResult.sha256
    report_limitations()        -> HandlerResult.limitations
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import ClassVar

from ..security import INJECTION_FLAG, detect_injection
from ..taxonomy import UNKNOWN, AccessStatus

# Limitation codes (stable strings; shown in the dashboard and stored with the source)
UNREADABLE_CHART_DATA = "UNREADABLE_CHART_DATA"
ENCRYPTED = "ENCRYPTED"
DRM_PROTECTED = "DRM_PROTECTED"
NO_TEXT_LAYER = "NO_TEXT_LAYER_OCR_REQUIRED"
TRUNCATED = "TRUNCATED"
PARSE_ERROR = "PARSE_ERROR"
ENCODING_FALLBACK = "ENCODING_FALLBACK"
UNSAFE_ARCHIVE = "UNSAFE_ARCHIVE"
MACRO_CONTAINER = "MACRO_CONTAINER_NOT_EXECUTED"

_DOI = re.compile(r"\b10\.\d{4,9}/[^\s\"<>)\]]+", re.I)
_ARXIV = re.compile(r"\barxiv(?:\.org)?[:\s./]*(?:abs/|pdf/)?(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
_SSRN = re.compile(r"ssrn\.com/(?:abstract=|abstract_id=|sol3/papers\.cfm\?abstract_id=)(\d+)", re.I)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Location:
    """Where a piece of content sits in its source (feeds fact provenance, spec §28)."""

    page: int | None = None
    slide: int | None = None
    section: str | None = None
    sheet: str | None = None
    item: str | None = None  # e.g. "notes", "table 2", EPUB chapter file

    def label(self) -> str:
        parts = []
        if self.page is not None:
            parts.append(f"p.{self.page}")
        if self.slide is not None:
            parts.append(f"slide {self.slide}")
        if self.sheet:
            parts.append(f"sheet {self.sheet}")
        if self.section:
            parts.append(f"§ {self.section}")
        if self.item:
            parts.append(self.item)
        return ", ".join(parts) or "document"


@dataclass
class TextBlock:
    text: str
    location: Location = field(default_factory=Location)


@dataclass
class Table:
    rows: list[list[str]]
    location: Location = field(default_factory=Location)


@dataclass
class HandlerResult:
    handler: str
    handler_version: str
    format: str
    sha256: str
    size: int
    access_status: AccessStatus = AccessStatus.OK
    metadata: dict[str, str] = field(
        default_factory=lambda: {"title": UNKNOWN, "author": UNKNOWN, "publication_date": UNKNOWN}
    )
    blocks: list[TextBlock] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    injection_phrases: list[str] = field(default_factory=list)
    # Other faithful renderings of the same pages (e.g. PDF layout analysis that reads two-column pages column by
    # column). Used only to verify quotes during grounding; never sent to the AI and never shown as the document.
    alt_blocks: list[TextBlock] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(b.text for b in self.blocks if b.text.strip())

    def add_limitation(self, code: str, detail: str | None = None) -> None:
        entry = f"{code}: {detail}" if detail else code
        if entry not in self.limitations:
            self.limitations.append(entry)

    def set_meta(self, key: str, value: object) -> None:
        """Only overwrite UNKNOWN with a real, non-empty value; never invent."""
        text = str(value).strip() if value is not None else ""
        if text:
            self.metadata[key] = text

    def finalize(self) -> HandlerResult:
        """Common post-processing: dedupe links, find academic references, flag injection text."""
        self.links = list(dict.fromkeys(link for link in self.links if link))
        refs = set(self.references)
        haystack = self.text + "\n" + "\n".join(self.links)
        refs.update(f"doi:{m.group(0).rstrip('.,;')}" for m in _DOI.finditer(haystack))
        refs.update(f"arxiv:{m.group(1)}" for m in _ARXIV.finditer(haystack))
        refs.update(f"ssrn:{m.group(1)}" for m in _SSRN.finditer(haystack))
        self.references = sorted(refs)
        self.injection_phrases = detect_injection(self.text)
        if self.injection_phrases:
            self.add_limitation(INJECTION_FLAG, "treated as data; review before trusting this source")
        return self


class SourceHandler:
    name: ClassVar[str] = "base"
    version: ClassVar[str] = "1"
    extensions: ClassVar[tuple[str, ...]] = ()
    formats: ClassVar[tuple[str, ...]] = ()

    def can_handle(self, fmt: str) -> bool:
        return fmt in self.formats

    def new_result(self, data: bytes, fmt: str) -> HandlerResult:
        return HandlerResult(handler=self.name, handler_version=self.version, format=fmt,
                             sha256=sha256_bytes(data), size=len(data))

    def extract(self, data: bytes, fmt: str, base_url: str | None = None) -> HandlerResult:
        raise NotImplementedError
