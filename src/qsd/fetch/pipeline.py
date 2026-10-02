"""Fetch → parse → store: one Source row per canonical URL, a fetch_log row per retrieval, and an errors row for
every failure (spec §27, §133). Only redacted metadata is logged; headers and bodies are never stored in the DB.
"""

from __future__ import annotations

from sqlalchemy import Engine, select

from ..db import session_scope
from ..db.models import ErrorRecord, FetchLog, Source
from ..handlers import HandlerResult, parse_bytes
from ..taxonomy import AccessStatus, ErrorState
from .client import FetchResponse, PoliteFetcher
from .enrich import enrich_source

_STOP_STATUSES = {AccessStatus.ROBOTS_DISALLOWED, AccessStatus.ACCESS_RESTRICTED,
                  AccessStatus.MANUAL_ACCESS_REQUIRED, AccessStatus.PRIVATE_ACCESS_REQUIRED}


def apply_result(source: Source, result: HandlerResult) -> None:
    source.content_hash = result.sha256
    source.file_size = result.size
    source.format = result.format
    source.handler_version = f"{result.handler}/{result.handler_version}"
    for key in ("title", "author", "publication_date"):
        value = result.metadata.get(key)
        if value and value != "UNKNOWN":
            setattr(source, key, value[:2000])


def fetch_and_store(url: str, fetcher: PoliteFetcher, engine: Engine, campaign_id: int | None = None,
                    retrieval_method: str = "HTTP") -> tuple[int | None, FetchResponse, HandlerResult | None]:
    """Returns (source_id, fetch response, parse result or None)."""
    resp = fetcher.fetch(url)
    result: HandlerResult | None = None
    with session_scope(engine) as s:
        source = s.scalars(select(Source).where(Source.canonical_url == resp.url)).one_or_none()
        if source is None and not resp.invalid_url:
            source = Source(url=url, canonical_url=resp.url, campaign_id=campaign_id)
            s.add(source)
            s.flush()
        s.add(FetchLog(source_id=source.id if source else None, page_url=url, request_url=resp.url,
                       status_code=resp.status_code, content_type=(resp.content_type or "")[:200] or None,
                       retrieval_method="CACHE" if resp.from_cache else retrieval_method,
                       request_class=resp.request_class, error=resp.error))
        if source is not None:
            source.retrieved_at = resp.fetched_at
            source.access_status = resp.access_status
            if resp.content_type:
                source.content_type = resp.content_type.split(";")[0].strip()[:200]

        if resp.ok:
            result = parse_bytes(resp.content, name=resp.final_url, content_type=resp.content_type,
                                 base_url=resp.final_url)
            apply_result(source, result)
            enrich_source(s, source, result)
            if result.access_status is not AccessStatus.OK and resp.access_status is AccessStatus.OK:
                source.access_status = result.access_status
            if result.access_status is AccessStatus.ERROR:
                s.add(ErrorRecord(campaign_id=campaign_id, stage="parse", source_ref=resp.url,
                                  handler=result.handler, error="; ".join(result.limitations)[:2000]))
        elif resp.access_status not in _STOP_STATUSES and resp.access_status is not AccessStatus.PAYWALLED:
            s.add(ErrorRecord(campaign_id=campaign_id, stage="fetch", source_ref=resp.url, handler="PoliteFetcher",
                              error=resp.error or resp.access_status.value, retry_count=max(0, resp.attempts - 1),
                              state=ErrorState.GAVE_UP if resp.attempts > 1 else ErrorState.UNRESOLVED))
        source_id = source.id if source else None
    return source_id, resp, result
