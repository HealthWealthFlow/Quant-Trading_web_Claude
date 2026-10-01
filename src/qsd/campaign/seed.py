"""Seed a campaign from `sources.txt` instead of searching: videos, channels, links, files and folders.

Already-read sources are skipped (like the Idea Extractor's processed cache): a link or YouTube video that was read
before, or a local file whose content has not changed. Changed files are read again.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Engine, select

from ..db import session_scope
from ..db.models import Source, SourceFact
from ..discovery import BudgetExhausted, CampaignBudget, Candidate, ConnectorError, YouTubeConnector, store_candidate
from ..discovery.runner import store_video_links
from ..handlers import EXTENSION_FORMATS, SUPPORTED_FORMATS, sha256_bytes
from ..sources_file import SourceItem
from ..taxonomy import AccessStatus, ExtractionMethod

READABLE_EXTENSIONS = {ext for ext, fmt in EXTENSION_FORMATS.items() if fmt in SUPPORTED_FORMATS}
MAX_FOLDER_FILES = 200
TITLE_OVERRIDE = "TITLE_OVERRIDE"


@dataclass
class SeedReport:
    added: int = 0
    already_read: int = 0
    problems: list[str] = field(default_factory=list)


def _set_title(s, src: Source, title: str | None) -> None:
    if not title:
        return
    src.title = title
    fact = s.scalars(select(SourceFact).where(SourceFact.source_id == src.id,
                                              SourceFact.fact_type == TITLE_OVERRIDE)).first()
    if fact is None:
        s.add(SourceFact(source_id=src.id, fact_type=TITLE_OVERRIDE, value=title, location="sources.txt",
                         extraction_method=ExtractionMethod.MANUAL, confidence=1.0))
    else:
        fact.value = title


def _queue(src: Source, cid: int) -> None:
    src.campaign_id, src.access_status = cid, AccessStatus.NOT_FETCHED


def read_video_ids(engine: Engine) -> set[str]:
    with session_scope(engine) as s:
        return set(s.scalars(select(SourceFact.value).join(Source, Source.id == SourceFact.source_id).where(
            SourceFact.fact_type == "ID_YOUTUBE", Source.access_status != AccessStatus.NOT_FETCHED)))


def _seed_file(s, path: Path, cid: int, title: str | None, rep: SeedReport) -> None:
    key = str(path.resolve())
    src = s.scalars(select(Source).where(Source.local_path == key)).first()
    if src is not None and src.access_status is not AccessStatus.NOT_FETCHED and src.content_hash:
        try:
            unchanged = sha256_bytes(path.read_bytes()) == src.content_hash
        except OSError as e:
            rep.problems.append(f"{path}: {e}")
            return
        if unchanged:
            rep.already_read += 1
            return
    if src is not None and src.campaign_id == cid and src.access_status is AccessStatus.NOT_FETCHED:
        return  # listed twice in this run (e.g. a file and its folder)
    if src is None:
        src = Source(local_path=key, title=path.stem[:2000], content_type="local-file")
        s.add(src)
        s.flush()
    _queue(src, cid)
    _set_title(s, src, title)
    rep.added += 1


def _seed_candidate(s, c: Candidate, cid: int, title: str | None, rep: SeedReport, video_links: int,
                    budget: CampaignBudget) -> None:
    src, created = store_candidate(s, c, cid)
    if src is None:
        rep.problems.append(f"not a valid link: {c.url}")
        return
    if not created and src.access_status is not AccessStatus.NOT_FETCHED:
        rep.already_read += 1
        return
    if not created and src.campaign_id == cid:
        return  # listed twice in this run (e.g. a video and its channel)
    _queue(src, cid)
    _set_title(s, src, title)
    rep.added += 1
    if c.connector == "youtube" and video_links:
        rep.added += store_video_links(s, src, c, cid, video_links, budget)


def seed_sources(engine: Engine, cid: int, items: list[SourceItem], youtube: YouTubeConnector | None,
                 budget: CampaignBudget, video_links: int = 5, note: Callable[[str], None] = lambda _m: None
                 ) -> SeedReport:
    rep = SeedReport()
    for it in items:
        before = rep.added
        try:
            if it.kind == "path":
                p = Path(it.target).expanduser()
                if p.is_dir():
                    files = sorted(f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in READABLE_EXTENSIONS)
                    if len(files) > MAX_FOLDER_FILES:
                        rep.problems.append(f"line {it.line}: {len(files)} files in {p}; only the first "
                                            f"{MAX_FOLDER_FILES} are read")
                    with session_scope(engine) as s:
                        for f in files[:MAX_FOLDER_FILES]:
                            _seed_file(s, f, cid, None, rep)
                elif p.is_file():
                    if p.suffix.lower() not in READABLE_EXTENSIONS:
                        rep.problems.append(f"line {it.line}: {p.suffix} files can't be read yet: {p.name}")
                        continue
                    with session_scope(engine) as s:
                        _seed_file(s, p, cid, it.title, rep)
                else:
                    rep.problems.append(f"line {it.line}: not found: {it.target}")
                    continue
            elif it.kind == "url":
                pdf = it.target if it.target.lower().split("?")[0].endswith(".pdf") else None
                with session_scope(engine) as s:
                    _seed_candidate(s, Candidate("user_urls", url=it.target, pdf_url=pdf, work_type="web-page"),
                                    cid, it.title, rep, 0, budget)
            elif youtube is None:
                rep.problems.append(f"line {it.line}: YouTube needs the YOUTUBE_API_KEY environment variable")
                continue
            elif it.kind == "youtube_video":
                if it.target in read_video_ids(engine):
                    rep.already_read += 1
                    continue
                for c in youtube.videos([it.target]):
                    with session_scope(engine) as s:
                        _seed_candidate(s, c, cid, it.title, rep, video_links, budget)
            elif it.kind == "youtube_channel":
                skip = read_video_ids(engine)
                channel, ids = youtube.channel_video_ids(it.target, it.n or 10, skip)
                for c in youtube.videos(ids):
                    with session_scope(engine) as s:
                        _seed_candidate(s, c, cid, None, rep, video_links, budget)
                note(f"channel {channel}: {len(ids)} new video(s)")
        except (ConnectorError, BudgetExhausted) as e:
            rep.problems.append(f"line {it.line}: {e}")
            continue
        if rep.added == before and it.kind != "youtube_channel":
            note(f"line {it.line}: already read before, skipped ({it.target[:80]})")
    return rep
