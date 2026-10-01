from contextlib import contextmanager

import httpx
import pytest
from fixtures import make_pdf
from sqlalchemy import select
from test_campaign import PAPER_PAGES, Clock, ScriptedAI
from test_youtube import KEY, VIDEO, settings

from qsd.ai import AIGateway
from qsd.campaign import CampaignLimits, CampaignRunner
from qsd.cli import main
from qsd.db import init_db, make_engine, new_idea, session_scope
from qsd.db.models import Campaign, Idea, Source
from qsd.discovery import YouTubeConnector
from qsd.export import export_notes, md
from qsd.fetch import PoliteFetcher
from qsd.scoring.rules import maturity
from qsd.sources_file import parse_sources_text
from qsd.taxonomy import AccessStatus

UPLOADS = "UUabc"


def api_site(seen):
    def handler(req: httpx.Request):
        seen.append(req)
        path, q = req.url.path, req.url.params
        if req.url.host == "www.googleapis.com":
            if path == "/youtube/v3/channels":
                assert q.get("forHandle") == "@QuantChannel"
                return httpx.Response(200, json={"items": [{"snippet": {"title": "Quant Channel"}, "contentDetails": {
                    "relatedPlaylists": {"uploads": UPLOADS}}}]})
            if path == "/youtube/v3/playlistItems":
                if q.get("pageToken") == "p2":
                    return httpx.Response(200, json={"items": [{"contentDetails": {"videoId": "vid00000003"}}]})
                return httpx.Response(200, json={"nextPageToken": "p2", "items": [
                    {"contentDetails": {"videoId": VIDEO["id"]}}, {"contentDetails": {"videoId": "vid00000002"}}]})
            if path == "/youtube/v3/videos":
                ids = q.get("id").split(",")
                return httpx.Response(200, json={"items": [
                    {**VIDEO, "id": i, "snippet": {**VIDEO["snippet"], "title": f"Video {i}"}} for i in ids]})
        if path == "/robots.txt":
            return httpx.Response(200, content=b"User-agent: *\nAllow: /\n")
        if path in ("/trend.pdf", "/paper.pdf"):
            return httpx.Response(200, content=make_pdf(PAPER_PAGES), headers={"content-type": "application/pdf"})
        return httpx.Response(404)
    return handler


@pytest.fixture
def env(tmp_path):
    s, seen = settings(), []
    e = make_engine(tmp_path / "db.sqlite")
    init_db(e)
    clock = Clock()
    f = PoliteFetcher(s, transport=httpx.MockTransport(api_site(seen)), clock=clock, sleep=clock.sleep)
    yt = YouTubeConnector(f, api_key=KEY)
    ai = ScriptedAI()
    runner = CampaignRunner(e, s, f, AIGateway(e, s, {"fake": ai}), [yt], CampaignLimits(docs_per_round=20))
    return s, e, runner, yt, seen, tmp_path


def test_parse_sources_lines():
    items, problems = parse_sources_text("""
# comment
https://www.youtube.com/watch?v=abc123XYZ00&t=42s
https://youtu.be/abc123XYZ01
https://www.youtube.com/@QuantChannel | n:25
https://www.youtube.com/channel/UC1234567890123456789012
https://arxiv.org/abs/2101.00001 | My paper
D:\\Books\\some_book.epub | My Custom Title
ftp://example.com/x
""")
    assert [(i.kind, i.target) for i in items] == [
        ("youtube_video", "abc123XYZ00"), ("youtube_video", "abc123XYZ01"), ("youtube_channel", "@QuantChannel"),
        ("youtube_channel", "channel/UC1234567890123456789012"), ("url", "https://arxiv.org/abs/2101.00001"),
        ("path", "D:\\Books\\some_book.epub")]
    assert items[2].n == 25 and items[3].n == 10 and items[4].title == "My paper"
    assert items[5].title == "My Custom Title" and len(problems) == 1 and "ftp" in problems[0]


def test_channel_expansion_uses_uploads_playlist_and_skips_read_videos(env):
    s, e, runner, yt, seen, _ = env
    title, ids = yt.channel_video_ids("@QuantChannel", 2, skip={VIDEO["id"]})
    assert title == "Quant Channel" and ids == ["vid00000002", "vid00000003"]
    assert not any(r.url.path == "/youtube/v3/search" for r in seen)  # no 100-unit searches for channels


def test_sources_campaign_reads_files_links_videos_and_skips_already_read(env):
    s, e, runner, yt, seen, tmp = env
    (tmp / "docs").mkdir()
    (tmp / "docs" / "a.pdf").write_bytes(make_pdf(PAPER_PAGES))
    (tmp / "docs" / "notes.mp4").write_bytes(b"not read")  # unsupported formats are not queued
    single = tmp / "b.pdf"
    single.write_bytes(make_pdf(PAPER_PAGES + ["extra page"]))
    items, _ = parse_sources_text(f"""
{tmp / 'docs'}
{single} | Book B
https://papers.example/paper.pdf
https://www.youtube.com/watch?v={VIDEO['id']}
https://www.youtube.com/@QuantChannel | n:1
{tmp / 'missing.pdf'}
""")
    cid, rep = runner.create_from_sources(items, yt)
    assert rep.added >= 5 and any("not found" in p for p in rep.problems)
    runner.limits.docs_per_round = rep.added
    report = runner.run(cid)
    assert report.documents_processed == rep.added
    with session_scope(e) as sess:
        b = sess.scalars(select(Source).where(Source.local_path == str(single.resolve()))).one()
        assert b.title == "Book B" and b.access_status is AccessStatus.OK  # title override survives parsing
        assert sess.scalars(select(Source).where(Source.local_path.like("%a.pdf"))).one().campaign_id == cid
        assert not sess.scalars(select(Source).where(Source.local_path.like("%.mp4"))).all()
        assert sess.get(Campaign, cid).state["phases_done"][0] == "discover"  # no searching
    assert not any(r.url.path == "/youtube/v3/search" for r in seen)

    cid2, rep2 = runner.create_from_sources(items, yt)  # second run: everything already read
    assert rep2.already_read >= 4
    assert rep2.added == 1  # only the channel's next not-yet-read video
    single.write_bytes(make_pdf(["changed content"]))
    cid3, rep3 = runner.create_from_sources(parse_sources_text(str(single))[0], yt)
    assert rep3.added == 1  # changed files are read again


def test_youtube_lines_without_key_are_reported(env):
    s, e, runner, yt, seen, _ = env
    items, _ = parse_sources_text("https://www.youtube.com/watch?v=abc123XYZ00")
    cid, rep = runner.create_from_sources(items, None)
    assert rep.added == 0 and "YOUTUBE_API_KEY" in rep.problems[0] and seen == []


def test_maturity_levels():
    assert [maturity(x) for x in (None, 0, 34.9, 35, 60, 79, 80, 100)] == [
        "UNKNOWN", "CONCEPT", "CONCEPT", "PARTIAL", "TRADEABLE", "TRADEABLE", "BACKTEST-READY", "BACKTEST-READY"]


EVIL = 'Momentum ![x](http://evil.example/t.png) <script>alert(1)</script> [click](javascript:x)'


def test_notes_export_escapes_and_never_overwrites_foreign_files(tmp_path):
    s = settings()
    e = make_engine(tmp_path / "db.sqlite")
    init_db(e)
    with session_scope(e) as sess:
        src = Source(title="Paper", url="https://papers.example/p", canonical_url="https://papers.example/p", tier=1)
        sess.add(src)
        sess.flush()
        sess.add(new_idea(strategy_name=EVIL, summary=EVIL, primary_source_id=src.id, entry_rule="buy",
                          formalization_completeness=62.0))
        sess.add(new_idea(strategy_name="Other idea", summary="x"))
    vault = tmp_path / "vault"
    folder = vault / s.export.notes_subfolder
    folder.mkdir(parents=True)
    foreign = folder / "QSD-000002 Other idea.md"
    foreign.write_text("my own note, not from QSD\n", encoding="utf-8")

    rep = export_notes(e, s, notes_dir=vault)
    assert rep.written == 1 and rep.skipped == 1 and foreign.read_text() == "my own note, not from QSD\n"
    note = rep.files[0].read_text(encoding="utf-8")
    assert note.startswith("---\n") and "qsd_id: 1\n" in note and 'maturity: "TRADEABLE"' in note
    body = note.split("\n---\n", 1)[1]  # front matter is YAML data (JSON-quoted), not rendered Markdown
    assert "![x](" not in body and "<script>" not in body and "[click](" not in body
    assert "\\[click\\](javascript:x)" in body  # shown as text, not a link
    assert md("<b>") == "\\<b\\>" and "[Paper](https://papers.example/p)" in note
    assert "/" not in rep.files[0].name.replace("QSD-000001 ", "")

    with session_scope(e) as sess:
        sess.get(Idea, 1).strategy_name = "Renamed strategy"
    rep2 = export_notes(e, s, notes_dir=vault, idea_ids=[1])
    assert rep2.files == rep.files and "Renamed strategy" in rep2.files[0].read_text(encoding="utf-8")


def test_notes_cli_needs_a_folder(tmp_path, capsys):
    assert main(["notes", "--db", str(tmp_path / "x.sqlite")]) == 2
    assert "export.notes_dir" in capsys.readouterr().err


def test_sources_cli_dry_run(tmp_path, capsys):
    f = tmp_path / "sources.txt"
    f.write_text("https://www.youtube.com/@QuantChannel | n:5\nC:\\x.pdf | T\n", encoding="utf-8")
    assert main(["sources", "--file", str(f), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "youtube_channel  @QuantChannel  | n: 5" in out and "path" in out and "| title: T" in out


def test_guided_research_can_read_sources_txt(env):
    from qsd.research import run_wizard

    s, e, runner, yt, seen, tmp = env
    (tmp / "p.pdf").write_bytes(make_pdf(PAPER_PAGES))
    src_file = tmp / "sources.txt"
    src_file.write_text(f"{tmp / 'p.pdf'} | My PDF\n", encoding="utf-8")
    answers = iter(["s", "y", ""])
    out = []

    @contextmanager
    def make_runner(limits):
        runner.limits = limits
        yield runner

    rc = run_wizard(e, s, make_runner, ask=lambda p: next(answers), out=out.append, open_browser=lambda u: None,
                    dashboard=lambda *a: (None, "test"), sources_path=src_file)
    text = "\n".join(out)
    assert rc == 0 and "files / folders: 1" in text and "Backtest queue:" in text
    with session_scope(e) as sess:
        assert sess.scalars(select(Source).where(Source.title == "My PDF")).one().access_status is AccessStatus.OK
