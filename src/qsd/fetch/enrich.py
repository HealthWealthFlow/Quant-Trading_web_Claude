"""Additive source enrichment (spec §24, §56).

Connector metadata is only attached when a source row is first created by discovery. A source read straight from a
cache entry, a direct URL or a local file never passes through discovery, so it keeps whatever title/date it had —
for a local read that can be `UNKNOWN`, which silently forfeits source quality and the root-evidence identity.

Everything here is **additive**: a known value is never overwritten, and the caller decides when to run it.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Source, SourceFact
from ..handlers import HandlerResult
from ..taxonomy import UNKNOWN, ExtractionMethod
from .identifiers import identifiers_from_url


def _add_fact_once(s: Session, source_id: int, fact_type: str, value: str, location: str) -> bool:
    """Add the fact unless one of that type already exists. Returns True only when a row was really added.

    The pre-check is deliberately optimistic: it uses the caller's session, which may not see a fact written by an
    outer, still-open transaction. Callers must therefore believe this return value, not the check alone — reporting
    a fact as "added" when it was not would be a silent lie about what enrichment did.
    """
    exists = s.scalars(select(SourceFact.id).where(SourceFact.source_id == source_id,
                                                   SourceFact.fact_type == fact_type)).first()
    if exists is not None:
        return False
    s.add(SourceFact(source_id=source_id, fact_type=fact_type, value=value, location=location,
                     extraction_method=ExtractionMethod.DETERMINISTIC, confidence=1.0))
    return True


def _title_from_url(url: str | None) -> str | None:
    """`.../1304.6846v2.pdf` -> `arXiv:1304.6846`; a readable last path segment is better than nothing."""
    ids = identifiers_from_url(url)
    if ids.get("arxiv"):
        return f"arXiv:{ids['arxiv']}"
    if ids.get("doi"):
        return f"DOI:{ids['doi']}"
    if not url:
        return None
    stem = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    stem = stem.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").strip()
    return stem if len(stem) >= 8 else None


def enrich_source(s: Session, source: Source, result: HandlerResult | None = None,
                  local_path: str | Path | None = None) -> list[str]:
    """Fill in what is missing on `source`. Returns the names of the fields that were added."""
    added: list[str] = []

    # Identifiers: a paper is one study however it was reached (abstract page, PDF link, publisher page).
    pdf_url = s.scalars(select(SourceFact.value).where(SourceFact.source_id == source.id,
                                                       SourceFact.fact_type == "PDF_URL")).first()
    urls = [source.canonical_url, source.url, pdf_url]
    if result is not None:
        urls += [result.metadata.get("final_url"), result.metadata.get("url")]
    ids: dict[str, str] = {}
    for url in urls:
        for key, value in identifiers_from_url(url).items():
            ids.setdefault(key, value)
    for key, value in ids.items():
        if _add_fact_once(s, source.id, f"ID_{key.upper()}", value, "derived:url"):
            added.append(f"ID_{key.upper()}")

    # Publication metadata: prefer what the handler read out of the document.
    metadata = (result.metadata if result is not None else {}) or {}
    for field in ("title", "author", "publication_date"):
        current = getattr(source, field, UNKNOWN)
        value = metadata.get(field)
        if (not current or current == UNKNOWN) and value and value != UNKNOWN:
            setattr(source, field, str(value)[:2000])
            added.append(field)

    # Title fallback: never leave a read source titled UNKNOWN, it hides the row in the dashboard.
    if not source.title or source.title == UNKNOWN:
        stem = Path(local_path).stem.replace("_", " ").replace("-", " ").strip() if local_path else None
        title = _title_from_url(pdf_url) or _title_from_url(source.canonical_url) or \
            (stem if stem and len(stem) >= 8 else None)
        if title:
            source.title = title[:2000]
            added.append("title")

    return added
