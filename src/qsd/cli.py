"""Command-line entry point: `qsd <command>`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import load_settings
from .db import DB_FILENAME, init_db, make_engine, session_scope, table_counts
from .discovery import (
    CONNECTORS,
    CampaignBudget,
    ConnectorError,
    FeedConnector,
    build_connectors,
    build_queries,
    run_discovery,
    store_candidate,
)
from .fetch import PoliteFetcher, fetch_and_store
from .handlers import parse_file
from .localscan import scan_paths
from .state import load_state, next_milestone, validate_state
from .taxonomy import AssetClass, MarketRegime


def _cmd_config(_: argparse.Namespace) -> int:
    print(json.dumps(load_settings().model_dump(mode="json"), indent=2))
    return 0


def _cmd_status(_: argparse.Namespace) -> int:
    state = load_state()
    errors = validate_state(state)
    for m in state["milestones"]:
        print(f"[{m['status']:>11}] {m['id']:<4} {m['title']}")
    nxt = next_milestone(state)
    print(f"\nNext: {nxt['id']} - {nxt['title']}" if nxt else "\nAll milestones done.")
    for e in errors:
        print(f"STATE ERROR: {e}", file=sys.stderr)
    return 1 if errors else 0


def _db_path(args: argparse.Namespace):
    if getattr(args, "db", None):
        return args.db
    s = load_settings()
    return s.resolve_path(s.paths.data_dir) / DB_FILENAME


def _cmd_db_init(args: argparse.Namespace) -> int:
    path = _db_path(args)
    version = init_db(make_engine(path))
    print(f"Database ready at {path} (schema v{version})")
    return 0


def _cmd_db_info(args: argparse.Namespace) -> int:
    path = _db_path(args)
    for table, count in table_counts(make_engine(path)).items():
        print(f"{table:<22} {count}")
    return 0


def _cmd_parse(args: argparse.Namespace) -> int:
    r = parse_file(args.file)
    summary = {
        "handler": r.handler, "format": r.format, "access_status": r.access_status.value, "sha256": r.sha256,
        "size": r.size, "metadata": r.metadata, "blocks": len(r.blocks), "tables": len(r.tables),
        "links": len(r.links), "references": r.references, "limitations": r.limitations,
        "preview": [{"location": b.location.label(), "text": b.text[:160]} for b in r.blocks[:5]],
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    s = load_settings()
    roots = [Path(p) for p in args.paths] or [s.resolve_path(p) for p in s.paths.local_sources]
    if not roots:
        print("No folders given and paths.local_sources is empty in config.", file=sys.stderr)
        return 2
    engine = make_engine(_db_path(args))
    init_db(engine)
    report = scan_paths(roots, engine)
    print(json.dumps(report.__dict__))
    return 0


def _cmd_fetch(args: argparse.Namespace) -> int:
    s = load_settings()
    engine = make_engine(_db_path(args))
    init_db(engine)
    with PoliteFetcher(s, cache_dir=s.resolve_path(s.paths.data_dir) / "http_cache") as fetcher:
        source_id, resp, result = fetch_and_store(args.url, fetcher, engine)
    out = {"source_id": source_id, "url": resp.url, "final_url": resp.final_url, "status": resp.status_code,
           "access_status": resp.access_status.value, "error": resp.error, "from_cache": resp.from_cache}
    if result:
        out.update(handler=result.handler, metadata=result.metadata, blocks=len(result.blocks),
                   references=result.references[:20], limitations=result.limitations)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if resp.ok else 1


def _cmd_queries(args: argparse.Namespace) -> int:
    assets = [AssetClass(a.upper()) for a in args.asset] or None
    regimes = [MarketRegime(r.upper()) for r in args.regime] or None
    for q in build_queries(assets, regimes, expand=args.expand, limit=args.limit):
        print(q)
    return 0


def _cmd_discover(args: argparse.Namespace) -> int:
    s = load_settings()
    engine = make_engine(_db_path(args))
    init_db(engine)
    names = list(CONNECTORS) if args.connector == "all" else args.connector.split(",")
    assets = [AssetClass(a.upper()) for a in args.asset]
    regimes = [MarketRegime(r.upper()) for r in args.regime]
    queries = list(args.query) or build_queries(assets or None, regimes or None, limit=args.max_queries)
    budget = CampaignBudget(s.budgets)
    with PoliteFetcher(s, cache_dir=s.resolve_path(s.paths.data_dir) / "http_cache") as fetcher:
        try:
            connectors = build_connectors(fetcher, s, None if args.connector == "all" else names)
        except ConnectorError as e:
            print(f"Stopped: {e}", file=sys.stderr)
            return 2
        report = run_discovery(engine, connectors, queries, budget, limit_per_query=args.limit,
                               memory_days=0 if args.force else s.discovery.search_memory_days,
                               video_links=s.discovery.youtube_max_links_per_video)
    out = {k: v for k, v in report.__dict__.items() if k != "source_ids"}
    out["budget_spent"] = budget.snapshot()
    print(json.dumps(out, indent=2))
    return 0


def _cmd_feed(args: argparse.Namespace) -> int:
    s = load_settings()
    engine = make_engine(_db_path(args))
    init_db(engine)
    with PoliteFetcher(s, cache_dir=s.resolve_path(s.paths.data_dir) / "http_cache") as fetcher:
        items = FeedConnector(fetcher).fetch_feed(args.url)
    new = 0
    with session_scope(engine) as sess:
        for c in items:
            _src, created = store_candidate(sess, c, None)
            new += int(created)
    print(json.dumps({"items": len(items), "new_sources": new}))
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from .ai import AIGateway, ProviderError, UnpricedModelError, default_providers, extract_ideas, idea_summary
    from .db.models import Source
    from .discovery import BudgetExhausted
    from .fetch.pipeline import apply_result

    s = load_settings()
    engine = make_engine(_db_path(args))
    init_db(engine)
    path = Path(args.file).resolve()
    result = parse_file(path)
    with session_scope(engine) as sess:
        src = sess.scalars(select(Source).where(Source.local_path == str(path))).one_or_none()
        if src is None:
            src = Source(local_path=str(path), access_status=result.access_status)
            sess.add(src)
            sess.flush()
        apply_result(src, result)
        source_id = src.id
    gw = AIGateway(engine, s, default_providers(), budget=CampaignBudget(s.budgets))
    try:
        rep = extract_ideas(gw, engine, s, source_id, result, force_deep=args.force_deep)
    except (UnpricedModelError, ProviderError, BudgetExhausted) as e:
        print(f"Stopped: {e}", file=sys.stderr)
        return 2
    out = {"source_id": source_id, "deep_read": rep.deep_read, "skipped": rep.skipped_reason,
           "cost_usd": round(rep.cost_usd, 6), "grounding_flags": rep.flags,
           "ideas": [idea_summary(engine, i) for i in rep.idea_ids]}
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def _print_scores(results) -> None:
    for r in results:
        print(f"idea {r.idea_id:>5}  {r.status:<24} quality {r.idea_quality:5.1f} (coverage {r.coverage:.0%})  "
              f"priority {r.priority:5.1f}  band {r.band}" + (f"  HARD FAIL: {', '.join(r.hard_fails)}"
                                                               if r.hard_fails else ""))


def _cmd_score(args: argparse.Namespace) -> int:
    from .scoring import score_all, score_idea

    s = load_settings()
    engine = make_engine(_db_path(args))
    init_db(engine)
    _print_scores([score_idea(engine, s, args.idea)] if args.idea else score_all(engine, s))
    return 0


def _load_document(engine, settings, source_id: int, fetcher):
    """Re-read a source's document: local file, the saved download (HTTP cache), or — only if neither exists —
    the URL it was fetched from. Never calls the AI."""
    from sqlalchemy import select

    from .db.models import FetchLog, Source
    from .handlers import parse_bytes

    with session_scope(engine) as sess:
        src = sess.get(Source, source_id)
        local, expected = src.local_path, src.content_hash
        url = sess.scalars(select(FetchLog.request_url).where(
            FetchLog.source_id == source_id, FetchLog.status_code == 200).order_by(FetchLog.id.desc())).first()
    cached = fetcher.cache.get(url) if url and fetcher.cache else None
    if local:
        result = parse_file(local)
    elif cached:
        meta, body = cached
        result = parse_bytes(body, name=meta.get("final_url") or url, content_type=meta.get("content_type"),
                             base_url=meta.get("final_url") or url)
    elif url:
        print(f"source {source_id}: no saved copy, downloading again from {url} ...", flush=True)
        resp = fetcher.fetch(url)
        if not resp.ok:
            return None, f"could not re-read {url}: {resp.error or resp.access_status.value}"
        result = parse_bytes(resp.content, name=resp.final_url, content_type=resp.content_type,
                             base_url=resp.final_url)
    else:
        return None, "no local file or successful fetch recorded"
    if expected and result.sha256 != expected:
        return None, "document changed since extraction; run a new extraction instead"
    return result, None


def _cmd_reground(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from .ai import reground_source
    from .db.models import AICall
    from .scoring import score_idea

    s = load_settings()
    engine = make_engine(_db_path(args))
    init_db(engine)
    with session_scope(engine) as sess:
        ids = [args.source] if args.source else sorted(set(sess.scalars(select(AICall.source_id).where(
            AICall.task == "stage_b_extract", AICall.success.is_(True), AICall.source_id.is_not(None)))))
    if not ids:
        print("No stored AI extractions to re-check.")
        return 0
    touched: list[int] = []
    with PoliteFetcher(s, cache_dir=s.resolve_path(s.paths.data_dir) / "http_cache") as fetcher:
        for n, sid in enumerate(ids, 1):
            print(f"[{n}/{len(ids)}] source {sid} ...", flush=True)
            result, problem = _load_document(engine, s, sid, fetcher)
            if result is None:
                print(f"source {sid}: skipped ({problem})")
                continue
            rep = reground_source(engine, s, sid, result)
            if rep.skipped_reason:
                print(f"source {sid}: skipped ({rep.skipped_reason})")
            for c in rep.changes:
                b, a = c["before"], c["after"]
                print(f"source {sid}: idea {c['idea_id']}  {b['status']} -> {a['status']}  "
                      f"removed values {b['removed']} -> {a['removed']}"
                      + (f"  (quotes realigned: {', '.join(c['realigned'])})" if c["realigned"] else ""))
            touched += rep.idea_ids
    if touched:
        print("\nRe-scored (no AI calls were made):")
        _print_scores([score_idea(engine, s, i) for i in touched])
    return 0


def _cmd_factcheck(args: argparse.Namespace) -> int:
    """Explain why quotes were rejected: closest passage in the source text and the share of words found there."""
    import unicodedata

    from sqlalchemy import select

    from .ai.grounding import SourceIndex, normalize
    from .ai.sections import select_relevant
    from .db.models import Idea, SourceFact

    s = load_settings()
    engine = make_engine(_db_path(args))
    init_db(engine)
    with session_scope(engine) as sess:
        idea = sess.get(Idea, args.idea)
        if idea is None:
            print(f"No idea {args.idea}.", file=sys.stderr)
            return 2
        sid, removed = idea.primary_source_id, list((idea.grounding or {}).get("removed", []))
        abstract = sess.scalars(select(SourceFact.value).where(SourceFact.source_id == sid,
                                                               SourceFact.fact_type == "ABSTRACT")).first()
    if not removed:
        print(f"Idea {args.idea}: nothing removed (or extracted before this was recorded; run `qsd reground`).")
        return 0
    with PoliteFetcher(s, cache_dir=s.resolve_path(s.paths.data_dir) / "http_cache") as fetcher:
        result, problem = _load_document(engine, s, sid, fetcher)
    if result is None:
        print(f"source {sid}: {problem}", file=sys.stderr)
        return 2
    sel = select_relevant(result, s.ai.stage_b_max_chars)
    text = sel.text + ("\n" + abstract if abstract else "")
    idx = SourceIndex(text)
    print(f"Idea {args.idea}, source {sid}: text the AI saw = {len(sel.text)} characters "
          f"({'truncated' if sel.truncated else 'complete'}), {len(idx.words)} words.\n")
    for r in removed:
        quote = r.get("quote") or ""
        print(f"== {r['field']}: {r['value'][:120]}")
        print(f"   reason: {r['reason']}")
        if not quote:
            print("   (no quote given by the AI)\n")
            continue
        exact = normalize(quote) in idx.norm
        share, stretch = idx.closest(quote)
        odd = sorted({f"U+{ord(c):04X} {unicodedata.name(c, '?')}" for c in stretch
                      if ord(c) > 126 or (ord(c) < 32 and c not in "\n\t")})
        print(f"   AI quote:      {quote[:300]}")
        print(f"   exact match:   {'yes' if exact else 'no'};  words found in order: {share:.0%}")
        print(f"   closest text:  {stretch[:600]}")
        if odd:
            print(f"   unusual characters there: {', '.join(odd[:10])}")
        print()
    return 0


def _cmd_package(args: argparse.Namespace) -> int:
    from .packaging import build_package, export_schema

    if args.schema:
        print(f"Schema written to {export_schema(Path(args.schema))}")
        return 0
    if args.idea is None:
        print("give an idea id, or --schema PATH", file=sys.stderr)
        return 2
    s = load_settings()
    engine = make_engine(_db_path(args))
    init_db(engine)
    print(build_package(engine, s, args.idea).model_dump_json(indent=2))
    return 0


def _cmd_queue(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from .db.models import Idea
    from .packaging import submit_to_queue
    from .taxonomy import IdeaStatus

    s = load_settings()
    engine = make_engine(_db_path(args))
    init_db(engine)
    if args.list:
        pending = s.resolve_path(s.handoff.queue_dir) / "pending"
        for p in sorted(pending.glob("*.json")) if pending.exists() else []:
            print(p.name)
        return 0
    if args.submit_ready:
        with session_scope(engine) as sess:
            ids = list(sess.scalars(select(Idea.id).where(Idea.status.in_(
                [IdeaStatus.PROMISING, IdeaStatus.READY_FOR_FORMALIZATION]))))
    else:
        ids = args.submit
    if not ids:
        print("nothing to submit (use --submit ID or --submit-ready)", file=sys.stderr)
        return 2
    for i in ids:
        ok, path, reasons = submit_to_queue(engine, s, i)
        print(f"idea {i}: " + (f"queued -> {path}" if ok else "NOT queued: " + "; ".join(reasons)))
    return 0


def _cmd_web(args: argparse.Namespace) -> int:
    import uvicorn

    from .web import create_app

    s = load_settings()
    engine = make_engine(_db_path(args))
    init_db(engine)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: the dashboard has no login; exposing it beyond this computer shares your research data.",
              file=sys.stderr)
    print(f"Dashboard: http://{args.host}:{args.port}/")
    uvicorn.run(create_app(engine, s), host=args.host, port=args.port, log_level="warning")
    return 0


def _safe_console() -> None:
    """Never crash on a character the Windows console code page can't show (paper titles are untrusted text)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def _print_event(phase: str, message: str) -> None:
    print(message if message.startswith("    ") else f"[{phase}] {message}", flush=True)


def _campaign_runner(s, engine, fetcher, limits, on_event=_print_event):
    from .ai import AIGateway, default_providers
    from .campaign import CampaignRunner

    gw = AIGateway(engine, s, default_providers())
    connectors = build_connectors(fetcher, s)
    return CampaignRunner(engine, s, fetcher, gw, connectors, limits, on_event=on_event)


def _run_with_interrupt(runner, cid: int):
    try:
        return runner.run(cid)
    except KeyboardInterrupt:
        runner.mark_interrupted(cid)
        print(f"\nStopped by you. Nothing is lost; continue later with: qsd campaign --resume {cid}")
        return None


def _cmd_campaign(args: argparse.Namespace) -> int:
    from .campaign import CampaignLimits, parse_request

    _safe_console()
    if args.dry_run:
        print(json.dumps(parse_request(args.request or "").to_dict(), indent=2))
        return 0
    s = load_settings()
    engine = make_engine(_db_path(args))
    init_db(engine)
    limits = CampaignLimits(max_queries=args.max_queries, docs_per_round=args.docs, deepen_top_ideas=args.deepen)
    with PoliteFetcher(s, cache_dir=s.resolve_path(s.paths.data_dir) / "http_cache",
                       max_requests_per_host=s.budgets.max_sources_per_domain) as fetcher:
        runner = _campaign_runner(s, engine, fetcher, limits)
        cid = args.resume or runner.create(args.request)
        if not args.resume:
            with session_scope(engine) as sess:
                from .db.models import Campaign
                print("Parsed request:", json.dumps(sess.get(Campaign, cid).spec))
        report = _run_with_interrupt(runner, cid)
    if report is None:
        return 130
    print(json.dumps(report.__dict__, indent=2, default=str))
    return 0


def _cmd_research(args: argparse.Namespace) -> int:
    from contextlib import contextmanager

    from .research import run_wizard

    _safe_console()
    s = load_settings()
    engine = make_engine(_db_path(args))
    init_db(engine)

    @contextmanager
    def make_runner(limits):
        with PoliteFetcher(s, cache_dir=s.resolve_path(s.paths.data_dir) / "http_cache",
                           max_requests_per_host=s.budgets.max_sources_per_domain) as fetcher:
            yield _campaign_runner(s, engine, fetcher, limits)

    try:
        return run_wizard(engine, s, make_runner, port=args.port)
    except (KeyboardInterrupt, EOFError):
        print("\nClosed.")
        return 130


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="qsd", description="Quant Strategy Discovery & Source Intelligence")
    p.add_argument("--version", action="version", version=f"qsd {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("config", help="print the effective configuration").set_defaults(func=_cmd_config)
    sub.add_parser("status", help="show build milestones and the next one").set_defaults(func=_cmd_status)
    db = sub.add_parser("db", help="research database commands")
    db.add_argument("--db", help="database path or SQLAlchemy URL (default: <data_dir>/qsd.sqlite)")
    db_sub = db.add_subparsers(dest="db_command", required=True)
    db_sub.add_parser("init", help="create tables / check schema version").set_defaults(func=_cmd_db_init)
    db_sub.add_parser("info", help="row counts per table").set_defaults(func=_cmd_db_info)
    ps = sub.add_parser("parse", help="parse one local file and print a summary (read-only)")
    ps.add_argument("file")
    ps.set_defaults(func=_cmd_parse)
    sc = sub.add_parser("scan", help="index local folders read-only (default: paths.local_sources)")
    sc.add_argument("paths", nargs="*")
    sc.add_argument("--db", help="database path or SQLAlchemy URL")
    sc.set_defaults(func=_cmd_scan)
    fe = sub.add_parser("fetch", help="politely fetch one public URL, parse it and store it as a source")
    fe.add_argument("url")
    fe.add_argument("--db", help="database path or SQLAlchemy URL")
    fe.set_defaults(func=_cmd_fetch)
    qu = sub.add_parser("queries", help="show generated research queries (spec §39, §40, §137)")
    qu.add_argument("--asset", action="append", default=[], help="STOCK, ETF, OPTIONS, FOREX, CRYPTO (repeatable)")
    qu.add_argument("--regime", action="append", default=[], help="BULLISH, BEARISH, CONSOLIDATION, CRASH")
    qu.add_argument("--expand", action="store_true", help="add vocabulary variants")
    qu.add_argument("--limit", type=int, default=50)
    qu.set_defaults(func=_cmd_queries)
    di = sub.add_parser("discover", help="search official APIs and store candidate sources (metadata only)")
    di.add_argument("query", nargs="*", help="free-text queries; default: query families for --asset/--regime")
    di.add_argument("--connector", default="all", help="arxiv,openalex,crossref,youtube or all")
    di.add_argument("--asset", action="append", default=[])
    di.add_argument("--regime", action="append", default=[])
    di.add_argument("--limit", type=int, default=10, help="results per query per connector")
    di.add_argument("--max-queries", type=int, default=10)
    di.add_argument("--force", action="store_true", help="ignore search memory")
    di.add_argument("--db", help="database path or SQLAlchemy URL")
    di.set_defaults(func=_cmd_discover)
    fd = sub.add_parser("feed", help="read an RSS/Atom feed and store its items as candidate sources")
    fd.add_argument("url")
    fd.add_argument("--db", help="database path or SQLAlchemy URL")
    fd.set_defaults(func=_cmd_feed)
    ex = sub.add_parser("extract", help="AI-extract strategy ideas from a local file (uses your AI key + budget)")
    ex.add_argument("file")
    ex.add_argument("--force-deep", action="store_true", help="run stage B even if triage says not promising")
    ex.add_argument("--db", help="database path or SQLAlchemy URL")
    ex.set_defaults(func=_cmd_extract)
    rg = sub.add_parser("reground", help="re-check stored AI extractions with the current grounding rules (no AI "
                                         "cost) and re-score the ideas")
    rg.add_argument("--source", type=int, help="one source id (default: every source with a stored extraction)")
    rg.add_argument("--db", help="database path or SQLAlchemy URL")
    rg.set_defaults(func=_cmd_reground)
    sco = sub.add_parser("score", help="score ideas deterministically and move them through the status pipeline")
    sco.add_argument("--idea", type=int, help="score one idea (default: all)")
    sco.add_argument("--db", help="database path or SQLAlchemy URL")
    sco.set_defaults(func=_cmd_score)
    fc = sub.add_parser("factcheck", help="explain why an idea's values were removed: closest source passage per "
                                          "quote (no AI cost)")
    fc.add_argument("idea", type=int)
    fc.add_argument("--db", help="database path or SQLAlchemy URL")
    fc.set_defaults(func=_cmd_factcheck)
    pk = sub.add_parser("package", help="print an idea's research package (JSON) or export the package schema")
    pk.add_argument("idea", nargs="?", type=int)
    pk.add_argument("--schema", help="write the package JSON Schema to this path")
    pk.add_argument("--db", help="database path or SQLAlchemy URL")
    pk.set_defaults(func=_cmd_package)
    qq = sub.add_parser("queue", help="hand eligible ideas to the backtest queue (files only, never a broker)")
    qq.add_argument("--submit", type=int, action="append", default=[], help="idea id (repeatable)")
    qq.add_argument("--submit-ready", action="store_true", help="submit all PROMISING / READY ideas that pass")
    qq.add_argument("--list", action="store_true", help="list pending packages")
    qq.add_argument("--db", help="database path or SQLAlchemy URL")
    qq.set_defaults(func=_cmd_queue)
    wb = sub.add_parser("web", help="start the read-only dashboard (default http://127.0.0.1:8877/)")
    wb.add_argument("--host", default="127.0.0.1")
    wb.add_argument("--port", type=int, default=8877)
    wb.add_argument("--db", help="database path or SQLAlchemy URL")
    wb.set_defaults(func=_cmd_web)
    rs = sub.add_parser("research", help="guided research: describe what to find, then everything runs with a live "
                                         "monitor (used by research.bat)")
    rs.add_argument("--port", type=int, default=8877, help="dashboard / live monitor port")
    rs.add_argument("--db", help="database path or SQLAlchemy URL")
    rs.set_defaults(func=_cmd_research)
    ca = sub.add_parser("campaign", help='run a research campaign, e.g. qsd campaign "Find crash-protection ETF '
                                         'strategies"')
    ca.add_argument("request", nargs="?", help="what to research, in plain English")
    ca.add_argument("--resume", type=int, help="continue campaign ID where it stopped")
    ca.add_argument("--dry-run", action="store_true", help="only show how the request is understood")
    ca.add_argument("--max-queries", type=int, default=8)
    ca.add_argument("--docs", type=int, default=10, help="documents to fetch + extract per run")
    ca.add_argument("--deepen", type=int, default=5, help="top ideas to search replication/contradictions for")
    ca.add_argument("--db", help="database path or SQLAlchemy URL")
    ca.set_defaults(func=_cmd_campaign)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
