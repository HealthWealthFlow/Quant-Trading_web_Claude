import json

import httpx
import pytest
from fixtures import make_pdf
from sqlalchemy import select
from test_campaign import PAPER_PAGES, Clock, ScriptedAI

from qsd.ai import AIGateway
from qsd.campaign import CampaignLimits, CampaignRunner
from qsd.config import DEFAULT_CONFIG, load_settings
from qsd.db import init_db, make_engine, session_scope
from qsd.db.models import FetchLog, Source, SourceFact, SourceLink
from qsd.discovery import (
    CampaignBudget,
    ConnectorError,
    YouTubeConnector,
    build_connectors,
    research_links,
    run_discovery,
)
from qsd.fetch import PoliteFetcher
from qsd.logging_setup import redact
from qsd.taxonomy import SourceRelation

KEY = "AIza" + "x" * 35
DESCRIPTION = ("How the 10-month moving average rule protects against crashes.\n"
               "Paper: https://papers.example/trend.pdf\n"
               "Code: https://github.com/someone/trend-timing\n"
               "Join my course: https://academy.example/course?ref=yt\n"
               "Follow me https://twitter.com/someone https://www.youtube.com/@channel\n")
VIDEO = {"id": "abc123XYZ00", "snippet": {"title": "Trend following for crash protection",
                                          "channelTitle": "Quant Channel", "publishedAt": "2024-05-01T10:00:00Z",
                                          "description": DESCRIPTION},
         "contentDetails": {"duration": "PT14M2S"}}


def youtube_site(seen):
    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.host == "www.googleapis.com":
            if req.url.path == "/youtube/v3/search":
                return httpx.Response(200, json={"items": [{"id": {"kind": "youtube#video",
                                                                   "videoId": VIDEO["id"]}}]})
            if req.url.path == "/youtube/v3/videos":
                return httpx.Response(200, json={"items": [VIDEO]})
        if req.url.path == "/robots.txt":
            return httpx.Response(200, content=b"User-agent: *\nAllow: /\n")
        if req.url.path == "/trend.pdf":
            return httpx.Response(200, content=make_pdf(PAPER_PAGES, title="Trend following"),
                                  headers={"content-type": "application/pdf"})
        return httpx.Response(404)
    return handler


def settings(**env):
    s = load_settings(DEFAULT_CONFIG, None, environ={"QSD_AI__DEFAULT_PROVIDER": "fake", **env})
    s.ai.prices["deepseek-chat"] = type(s.ai.prices["claude-opus-5-5"])(input_per_mtok=1, output_per_mtok=2)
    return s


def fetcher_for(s, seen, tmp_path=None):
    clock = Clock()
    return PoliteFetcher(s, cache_dir=(tmp_path / "cache") if tmp_path else None,
                         transport=httpx.MockTransport(youtube_site(seen)), clock=clock, sleep=clock.sleep)


def test_youtube_search_reads_metadata_with_key_in_header_only(tmp_path):
    seen = []
    yt = YouTubeConnector(fetcher_for(settings(), seen, tmp_path), api_key=KEY)
    [c] = yt.search("trend following crash", limit=5)
    assert c.title == "Trend following for crash protection" and c.authors == ["Quant Channel"]
    assert c.url == "https://www.youtube.com/watch?v=abc123XYZ00" and c.publication_date == "2024-05-01"
    assert "https://papers.example/trend.pdf" in c.abstract and c.ids == {"youtube": "abc123XYZ00",
                                                                           "duration": "PT14M2S"}
    api = [r for r in seen if r.url.host == "www.googleapis.com"]
    assert [r.url.path for r in api] == ["/youtube/v3/search", "/youtube/v3/videos"]
    assert all(r.headers["x-goog-api-key"] == KEY and KEY not in str(r.url) for r in api)
    assert not any(r.url.path == "/robots.txt" for r in seen)  # official API endpoint
    cache = tmp_path / "cache"
    assert not cache.exists() or not any(KEY in p.read_text(errors="ignore") for p in cache.iterdir())
    assert not cache.exists() or not list(cache.glob("*.bin"))  # credentialed responses are never cached


def test_youtube_without_key_is_skipped_or_refused(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    s, seen = settings(), []
    f = fetcher_for(s, seen)
    with pytest.raises(ConnectorError, match="YOUTUBE_API_KEY"):
        YouTubeConnector(f).search("x")
    assert [c.name for c in build_connectors(f, s)] == ["arxiv", "openalex", "crossref"]  # quietly left out
    with pytest.raises(ConnectorError, match="YOUTUBE_API_KEY"):
        build_connectors(f, s, ["youtube"])  # explicitly asked for: say why it can't run
    assert seen == []


def test_build_connectors_with_key(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", KEY)
    s = settings()
    names = [c.name for c in build_connectors(fetcher_for(s, []), s)]
    assert names == ["arxiv", "openalex", "crossref", "youtube"]
    s.discovery.youtube_enabled = False
    assert "youtube" not in [c.name for c in build_connectors(fetcher_for(s, []), s)]


def test_research_links_skip_social_shop_and_affiliate():
    assert research_links(DESCRIPTION) == ["https://papers.example/trend.pdf",
                                           "https://github.com/someone/trend-timing"]
    assert research_links("see https://arxiv.org/abs/1234.5678.", limit=1) == ["https://arxiv.org/abs/1234.5678"]
    assert research_links(None) == []


def test_google_api_keys_are_redacted():
    assert KEY not in redact(f"error calling api with {KEY}")


def test_video_description_links_become_linked_candidates(tmp_path):
    s, seen = settings(), []
    e = make_engine(tmp_path / "db.sqlite")
    init_db(e)
    yt = YouTubeConnector(fetcher_for(s, seen), api_key=KEY)
    rep = run_discovery(e, [yt], ["trend crash"], CampaignBudget(s.budgets), video_links=5)
    assert rep.new_sources == 3  # the video + its paper + its code link
    with session_scope(e) as sess:
        video = sess.scalars(select(Source).where(Source.url.like("%youtube.com/watch%"))).one()
        assert video.tier == 3 and video.author == "Quant Channel"
        links = sess.scalars(select(SourceLink).where(SourceLink.from_source_id == video.id)).all()
        targets = {sess.get(Source, x.to_source_id).url for x in links}
        assert targets == {"https://papers.example/trend.pdf", "https://github.com/someone/trend-timing"}
        assert all(x.relation is SourceRelation.CITES for x in links)
        pdf = sess.scalars(select(Source).where(Source.url == "https://papers.example/trend.pdf")).one()
        assert sess.scalars(select(SourceFact.value).where(SourceFact.source_id == pdf.id,
                                                           SourceFact.fact_type == "PDF_URL")).one()


def test_campaign_reads_video_description_and_linked_paper_never_the_video(tmp_path):
    s, seen = settings(), []
    e = make_engine(tmp_path / "db.sqlite")
    init_db(e)
    f = fetcher_for(s, seen)
    ai = ScriptedAI()
    events = []
    runner = CampaignRunner(e, s, f, AIGateway(e, s, {"fake": ai}), [YouTubeConnector(f, api_key=KEY)],
                            CampaignLimits(max_queries=1, docs_per_round=5, deepen_top_ideas=2),
                            on_event=lambda p, m: events.append(m))
    cid = runner.create("trend following crash protection")
    rep = runner.run(cid)
    text = "\n".join(events)
    assert "(YouTube video)" in text and rep.documents_processed >= 2
    assert not any(r.url.host in ("www.youtube.com", "youtube.com") for r in seen)  # never downloaded/scraped
    searches = [r for r in seen if r.url.path == "/youtube/v3/search"]
    assert len(searches) == 1  # follow-up (evidence) searches use paper sources only
    with session_scope(e) as sess:
        log = sess.scalars(select(FetchLog).where(FetchLog.retrieval_method == "API_METADATA")).one()
        assert "youtube.com/watch" in log.request_url and KEY not in json.dumps(
            [r.request_url for r in sess.scalars(select(FetchLog))])
    assert any("Task: extract" in c for c in ai.calls)


def test_video_is_read_through_its_transcript_when_enabled(tmp_path, monkeypatch):
    """The spoken content, not the description, is what carries a strategy (DECISIONS D29: campaign 3 read 150
    video descriptions and produced zero strategies)."""
    from qsd.fetch import Transcript

    spied: dict = {}

    def fake_transcript(video_id, languages=None, timeout=None):
        spied["video_id"], spied["languages"] = video_id, languages
        return Transcript(text="[t=00:00:05] We buy when price closes above the 10 month moving average\n"
                               "[t=00:00:20] Exit to cash when it closes back below the 10 month average\n",
                          language="en", source="auto")

    monkeypatch.setattr("qsd.campaign.runner.fetch_transcript", fake_transcript)

    s, seen = settings(QSD_DISCOVERY__YOUTUBE_TRANSCRIPTS="true"), []
    e = make_engine(tmp_path / "db.sqlite")
    init_db(e)
    f = fetcher_for(s, seen)
    runner = CampaignRunner(e, s, f, AIGateway(e, s, {"fake": ScriptedAI()}),
                            [YouTubeConnector(f, api_key=KEY)],
                            CampaignLimits(max_queries=1, docs_per_round=5, deepen_top_ideas=2))
    cid = runner.create("trend following crash protection")
    runner.run(cid)

    assert spied["video_id"] == VIDEO["id"]
    with session_scope(e) as sess:
        video = sess.scalars(select(Source).where(Source.url.like("%youtube.com/watch%"))).one()
        assert video.format == "youtube-transcript"
        stored = sess.scalars(select(SourceFact.value).where(SourceFact.source_id == video.id,
                                                             SourceFact.fact_type == "TRANSCRIPT")).one()
        assert "10 month moving average" in stored
        log = sess.scalars(select(FetchLog).where(FetchLog.source_id == video.id)).all()
        assert any(x.retrieval_method == "API_TRANSCRIPT" for x in log)
    assert not any(r.url.host in ("www.youtube.com", "youtube.com") for r in seen)  # media still untouched


def test_transcript_is_reused_not_refetched(tmp_path, monkeypatch):
    """A resumed campaign must not pay for captions it already stored."""
    from qsd.fetch import Transcript

    calls: list[str] = []

    def fake_transcript(video_id, languages=None, timeout=None):
        calls.append(video_id)
        return Transcript(text="[t=00:00:01] buy the breakout", language="en", source="auto")

    monkeypatch.setattr("qsd.campaign.runner.fetch_transcript", fake_transcript)
    s = settings(QSD_DISCOVERY__YOUTUBE_TRANSCRIPTS="true")
    e = make_engine(tmp_path / "db.sqlite")
    init_db(e)
    f = fetcher_for(s, [])
    runner = CampaignRunner(e, s, f, AIGateway(e, s, {"fake": ScriptedAI()}),
                            [YouTubeConnector(f, api_key=KEY)],
                            CampaignLimits(max_queries=1, docs_per_round=5, deepen_top_ideas=0))
    cid = runner.create("trend following")
    runner.run(cid)
    assert len(calls) == 1
    with session_scope(e) as sess:
        video = sess.scalars(select(Source).where(Source.url.like("%youtube.com/watch%"))).one()
        vid = video.id
    runner._video_description(vid, cid)  # read the same video again
    assert len(calls) == 1  # served from the stored TRANSCRIPT fact
