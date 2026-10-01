"""Run searches with search memory and budgets; store candidates as metadata-only Source rows (spec §45, §93).

Candidates are de-duplicated across connectors by DOI / arXiv id / URL. Abstracts are stored as provenance facts
(fact_type ABSTRACT) because the cheap filters in later milestones read them before any full download (§84).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, select

from ..db import session_scope
from ..db.models import ErrorRecord, SearchQuery, Source, SourceFact, SourceLink
from ..fetch.urls import InvalidURLError, canonicalize_url
from ..taxonomy import UNKNOWN, AccessStatus, ExtractionMethod, SourceRelation
from .budget import BudgetExhausted, CampaignBudget
from .connectors import Candidate, Connector, ConnectorError, research_links
from .queries import query_hash
from .tiering import tier_for_candidate

MAX_ABSTRACT_CHARS = 4000


@dataclass
class DiscoveryReport:
    queries_run: int = 0
    queries_skipped_memory: int = 0
    candidates: int = 0
    new_sources: int = 0
    existing_sources: int = 0
    errors: list[str] = field(default_factory=list)
    stopped_reason: str | None = None
    source_ids: list[int] = field(default_factory=list)


def recently_searched(engine: Engine, connector: str, query: str, within_days: int) -> bool:
    if within_days <= 0:
        return False
    cutoff = datetime.now(UTC) - timedelta(days=within_days)
    with session_scope(engine) as s:
        rows = s.scalars(select(SearchQuery).where(SearchQuery.query_hash == query_hash(connector, query))).all()
        return any((r.ran_at if r.ran_at.tzinfo else r.ran_at.replace(tzinfo=UTC)) >= cutoff for r in rows)


def store_candidate(s, c: Candidate, campaign_id: int | None) -> tuple[Source | None, bool]:
    link = c.canonical_link
    if not link:
        return None, False
    try:
        canonical = canonicalize_url(link)
    except InvalidURLError:
        return None, False
    src = s.scalars(select(Source).where(Source.canonical_url == canonical)).one_or_none()
    created = src is None
    if created:
        tier, basis = tier_for_candidate(c.url or c.pdf_url or link, c.connector, c.work_type)
        src = Source(url=c.url or c.pdf_url or link, canonical_url=canonical, campaign_id=campaign_id, tier=tier,
                     access_status=AccessStatus.NOT_FETCHED, content_type="metadata")
        s.add(src)
        s.flush()
        s.add(SourceFact(source_id=src.id, fact_type="TIER_BASIS", value=basis,
                         extraction_method=ExtractionMethod.DETERMINISTIC, confidence=1.0))
    # Fill UNKNOWN fields only; never overwrite known values with another connector's version.
    if src.title == UNKNOWN and c.title != UNKNOWN:
        src.title = c.title[:2000]
    if src.author == UNKNOWN and c.authors:
        src.author = "; ".join(c.authors)[:2000]
    if src.publication_date == UNKNOWN and c.publication_date != UNKNOWN:
        src.publication_date = c.publication_date
    if src.organization == UNKNOWN and c.venue != UNKNOWN:
        src.organization = c.venue[:500]
    for key, value in c.ids.items():
        _add_fact_once(s, src.id, f"ID_{key.upper()}", value, c.connector)
    if c.pdf_url:
        _add_fact_once(s, src.id, "PDF_URL", c.pdf_url, c.connector)
    if c.abstract:
        _add_fact_once(s, src.id, "ABSTRACT", c.abstract[:MAX_ABSTRACT_CHARS], c.connector)
    return src, created


def store_video_links(s, video: Source, c: Candidate, campaign_id: int | None, limit: int,
                      budget: CampaignBudget | None = None) -> int:
    """Follow a video description's research links: each becomes a candidate source (read later like any paper)
    linked to the video with a CITES edge. Returns the number of new sources."""
    new = 0
    for url in research_links(c.abstract, limit):
        if budget is not None and budget.remaining("urls") < 1:
            break
        link = Candidate("youtube_link", url=url, pdf_url=url if url.lower().split("?")[0].endswith(".pdf") else None,
                         work_type="linked-from-video")
        src, created = store_candidate(s, link, campaign_id)
        if src is None or src.id == video.id:
            continue
        if created:
            new += 1
            if budget is not None:
                budget.spend("urls")
        exists = s.scalars(select(SourceLink.id).where(SourceLink.from_source_id == video.id,
                                                       SourceLink.to_source_id == src.id)).first()
        if exists is None:
            s.add(SourceLink(from_source_id=video.id, to_source_id=src.id, relation=SourceRelation.CITES))
    return new


def _add_fact_once(s, source_id: int, fact_type: str, value: str, connector: str) -> None:
    exists = s.scalars(select(SourceFact.id).where(SourceFact.source_id == source_id,
                                                   SourceFact.fact_type == fact_type)).first()
    if exists is None:
        s.add(SourceFact(source_id=source_id, fact_type=fact_type, value=value, location=f"api:{connector}",
                         extraction_method=ExtractionMethod.DETERMINISTIC, confidence=1.0))


def run_discovery(engine: Engine, connectors: list[Connector], queries: list[str], budget: CampaignBudget,
                  limit_per_query: int = 10, memory_days: int = 30, campaign_id: int | None = None,
                  purpose: str = "DISCOVERY", on_search=None, video_links: int = 5) -> DiscoveryReport:
    """`on_search(connector, query, found, new, done, total)` is called after each search (progress display)."""
    report = DiscoveryReport()
    total, done = len(queries) * len(connectors), 0
    try:
        for query in queries:
            for conn in connectors:
                done += 1
                if recently_searched(engine, conn.name, query, memory_days):
                    report.queries_skipped_memory += 1
                    if on_search:
                        on_search(conn.name, query, None, 0, done, total)
                    continue
                budget.spend("search_requests")
                try:
                    found = conn.search(query, limit=limit_per_query)
                except ConnectorError as e:
                    report.errors.append(str(e))
                    if on_search:
                        on_search(conn.name, query, -1, 0, done, total)
                    with session_scope(engine) as s:
                        s.add(ErrorRecord(campaign_id=campaign_id, stage="discovery", source_ref=query,
                                          handler=conn.name, error=str(e)[:2000]))
                    continue
                report.queries_run += 1
                report.candidates += len(found)
                with session_scope(engine) as s:
                    new_here = 0
                    for c in found:
                        if budget.remaining("urls") < 1:
                            raise BudgetExhausted("urls", budget.limit("urls"))
                        src, created = store_candidate(s, c, campaign_id)
                        if src is None:
                            continue
                        if created:
                            budget.spend("urls")
                            new_here += 1
                            report.new_sources += 1
                        else:
                            report.existing_sources += 1
                            if (campaign_id is not None and src.campaign_id != campaign_id
                                    and src.access_status is AccessStatus.NOT_FETCHED):
                                src.campaign_id = campaign_id  # found before but never read: read it here
                        report.source_ids.append(src.id)
                        if c.connector == "youtube" and video_links:
                            linked = store_video_links(s, src, c, campaign_id, video_links, budget)
                            new_here += linked
                            report.new_sources += linked
                    s.add(SearchQuery(campaign_id=campaign_id, connector=conn.name, query_text=query,
                                      query_hash=query_hash(conn.name, query), purpose=purpose,
                                      results_count=len(found), useful_count=new_here))
                if on_search:
                    on_search(conn.name, query, len(found), new_here, done, total)
    except BudgetExhausted as e:
        report.stopped_reason = str(e)
    report.source_ids = list(dict.fromkeys(report.source_ids))
    return report
