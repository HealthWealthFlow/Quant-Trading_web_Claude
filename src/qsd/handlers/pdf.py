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


def _column_split(page) -> float | None:
    """x of the vertical gutter between two columns, or None for a single-column page.

    pdfplumber reads a two-column page as alternating lines ("...we evaluate 62 instruments | adaptation creates an
    opposing risk. a fixed strategy pool is | across five asset classes..."), which shuffles the sentences. The facts
    survive, but no contiguous quote exists any more, so grounding correctly rejects every value — measured: 13 of 19
    values on one MM-ARC paper were stripped as QUOTE_NOT_FOUND although the paper states all of them.
    """
    try:
        words = page.extract_words()
        width = float(page.width)
    except Exception:  # noqa: BLE001 — layout probing must never break parsing
        return None
    if len(words) < 40:
        return None  # too little text to judge (title page, figure)
    # A word wider than this cannot be split without breaking it (a long URL, a wide equation). Splitting through such
    # a token garbles it and destroys anything else on the line, so the page is left in pdfplumber's own order.
    widest = max((float(w["x1"]) - float(w["x0"]) for w in words), default=0.0)
    if widest > width * 0.28:
        return None
    # vertical projection: how many words cover each x-interval
    edges = sorted({round(e, 1) for w in words for e in (float(w["x0"]), float(w["x1"])) if 0 <= e <= width})
    edges = [0.0, *edges, width]
    total_words = len(words)
    span: list[float] = []  # index i spans edges[i]..edges[i+1]
    for a, b in zip(edges, edges[1:], strict=False):
        span.append(0.0 if b - a < 2 else float(sum(1 for w in words
                                                     if float(w["x0"]) < b and float(w["x1"]) > a)))
    # scan for a gutter in the middle 40% of the page: nearly empty, with text on both sides.
    # The tolerance has to allow a few cross-gutter words (a long URL or a wide equation), because otherwise a single
    # one vetoes detection on an otherwise clean two-column page — measured: a real paper had its gutter at x=312 on
    # every page, but detection fired on only 3 of 6 pages, leaving the rest interleaved.
    lo, hi = width * 0.28, width * 0.72
    best_gap, best_x = 0.0, None
    for i, (a, b) in enumerate(zip(edges, edges[1:], strict=False)):
        if not (lo <= a <= hi) or b - a < 10:
            continue
        if span[i] > max(3.0, total_words * 0.02):
            continue
        left = sum(1 for w in words if float(w["x1"]) <= a)
        right = sum(1 for w in words if float(w["x0"]) >= b)
        if left >= total_words * 0.25 and right >= total_words * 0.25 and b - a > best_gap:
            best_gap, best_x = b - a, (a + b) / 2
    return best_x


def _text_by_columns(page, gutter: float) -> str:
    """Read the left column fully, then the right — so sentences stay contiguous."""
    words = page.extract_words()
    left = [w for w in words if float(w["x1"]) <= gutter]
    right = [w for w in words if float(w["x0"]) >= gutter]
    out: list[str] = []
    for column in (left, right):
        # pdfplumber's `top` is measured from the top of the page, so ascending order is reading order
        column.sort(key=lambda w: (float(w["top"]), float(w["x0"])))
        line: list[str] = []
        last_top: float | None = None
        for w in column:
            top = float(w["top"])
            if last_top is not None and abs(top - last_top) > 3:
                out.append(" ".join(line))
                line = []
            line.append(str(w["text"]))
            last_top = top
        if line:
            out.append(" ".join(line))
    return "\n".join(out).strip()


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
    # two-column pages: read each column in order instead of alternate lines from both
    gutter = _column_split(page)
    if gutter is not None:
        columned = _text_by_columns(page, gutter)
        if len(columned) >= len(best) * 0.6 and glued_share(columned) <= max(0.02, best_share):
            best = columned
    return best


MAX_LAYOUT_PAGES = 80


def layout_blocks(data: bytes, primary: list[TextBlock]) -> list[TextBlock]:
    """A second reading of each page by pdfminer's layout analysis (text boxes in reading order).

    pdfplumber reads a page line by line across its full width, so on two-column papers it can interleave the columns
    or even merge characters from both columns into one token — measured on a real paper: tokens 242–504pt wide on a
    612pt page, 13 of 19 grounded values lost. pdfminer groups characters into lines and boxes before ordering them, so
    columns come out whole. Both readings are faithful renderings of the same page; grounding accepts a quote found in
    either, which recovers real quotes without accepting anything the document does not contain.
    """
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LAParams, LTTextContainer

    seen = {b.location.page: " ".join(b.text.split()) for b in primary}
    out: list[TextBlock] = []
    try:
        for number, layout in enumerate(extract_pages(io.BytesIO(data), laparams=LAParams(),
                                                      maxpages=MAX_LAYOUT_PAGES), 1):
            text = "\n".join(el.get_text().strip() for el in layout if isinstance(el, LTTextContainer)).strip()
            if text and " ".join(text.split()) != seen.get(number):
                out.append(TextBlock(text, Location(page=number)))
    except Exception:  # noqa: BLE001 — the second reading is optional; the primary text stands on its own
        return out
    return out


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
    version = "3"  # 2: re-read glued-word pages; 3: + pdfminer layout rendering for quote verification
    formats = ("pdf",)

    def extract(self, data: bytes, fmt: str, base_url: str | None = None) -> HandlerResult:
        result = self.new_result(data, fmt)
        try:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                self._read(pdf, result)
            result.alt_blocks = layout_blocks(data, result.blocks)
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
