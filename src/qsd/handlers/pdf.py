"""PDF via pdfplumber (MIT). Text per page keeps page numbers for provenance (spec §21, §28).

Encrypted PDFs are not cracked (spec §10): they are reported as ENCRYPTED. Pages without a text layer are
reported as needing OCR (Phase 2) instead of being guessed at.
"""

from __future__ import annotations

import io
import re

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect

from ..taxonomy import AccessStatus
from .base import (
    ENCRYPTED,
    NO_TEXT_LAYER,
    PARSE_ERROR,
    TRUNCATED,
    HandlerResult,
    Location,
    SourceHandler,
    Table,
    TextBlock,
)

MAX_PAGES = 600
MAX_TABLE_PAGES = 60  # table detection is slow; only the first pages
# Some PDFs place words with tiny gaps, so the default spacing rule glues them together
# ("Thissuggeststhatforthestock..."). Such pages are re-read with tighter word-gap tolerances.
_GLUED = re.compile(r"[A-Za-z]{25,}")
_TOLERANCES = (1.5, 1.0)


def glued_share(text: str) -> float:
    """Share of letters that sit in implausibly long 'words' (≥ 25 letters)."""
    letters = sum(c.isalpha() for c in text)
    return sum(len(m) for m in _GLUED.findall(text)) / letters if letters else 0.0


def page_text(page) -> str:
    text = (page.extract_text() or "").strip()
    best, best_share = text, glued_share(text)
    for tol in _TOLERANCES:
        if best_share < 0.02:
            break
        alt = (page.extract_text(x_tolerance=tol) or "").strip()
        share = glued_share(alt)
        if alt and share < best_share:
            best, best_share = alt, share
    return best


def _is_password_error(e: BaseException) -> bool:
    """pdfplumber wraps pdfminer errors; look through args and the exception chain."""
    seen: set[int] = set()
    stack: list[object] = [e]
    while stack:
        cur = stack.pop()
        if id(cur) in seen or not isinstance(cur, BaseException):
            continue
        seen.add(id(cur))
        if isinstance(cur, PDFPasswordIncorrect):
            return True
        stack.extend([*cur.args, cur.__cause__, cur.__context__])
    return False


class PDFHandler(SourceHandler):
    name = "PDFHandler"
    version = "2"  # 2: re-read pages with glued words using tighter word-gap tolerances
    formats = ("pdf",)

    def extract(self, data: bytes, fmt: str, base_url: str | None = None) -> HandlerResult:
        result = self.new_result(data, fmt)
        try:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                self._read(pdf, result)
        except Exception as e:  # noqa: BLE001 — malformed PDFs must not crash the pipeline
            msg = str(e) or type(e).__name__
            if _is_password_error(e) or "password" in msg.lower() or "encrypt" in msg.lower():
                result.access_status = AccessStatus.ACCESS_RESTRICTED
                result.add_limitation(ENCRYPTED, "password-protected PDF; not decrypted")
            else:
                result.access_status = AccessStatus.ERROR
                result.add_limitation(PARSE_ERROR, msg[:200])
        return result.finalize()

    @staticmethod
    def _read(pdf: pdfplumber.PDF, result: HandlerResult) -> None:
        meta = pdf.metadata or {}
        result.set_meta("title", meta.get("Title"))
        result.set_meta("author", meta.get("Author"))
        created = str(meta.get("CreationDate") or "")
        if created.startswith("D:") and len(created) >= 10:
            # File creation date, not the publication date (spec §1); kept separately.
            result.set_meta("file_created_date", f"{created[2:6]}-{created[6:8]}-{created[8:10]}")

        pages = pdf.pages
        if len(pages) > MAX_PAGES:
            result.add_limitation(TRUNCATED, f"only first {MAX_PAGES} of {len(pages)} pages read")
        empty = 0
        for number, page in enumerate(pages[:MAX_PAGES], 1):
            text = page_text(page)
            if text:
                result.blocks.append(TextBlock(text, Location(page=number)))
            else:
                empty += 1
            for link in page.hyperlinks or []:
                uri = link.get("uri")
                if uri:
                    result.links.append(uri)
            if number <= MAX_TABLE_PAGES:
                for i, rows in enumerate(page.extract_tables() or [], 1):
                    clean = [[(c or "").strip() for c in row] for row in rows if row and any(row)]
                    if clean:
                        result.tables.append(Table(clean, Location(page=number, item=f"table {i}")))
        if pages and empty == min(len(pages), MAX_PAGES):
            result.add_limitation(NO_TEXT_LAYER, "scanned/image-only PDF")
        elif empty:
            result.add_limitation(NO_TEXT_LAYER, f"{empty} page(s) without a text layer")
