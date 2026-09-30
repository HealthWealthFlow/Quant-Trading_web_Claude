"""Command-line entry point: `qsd <command>`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import load_settings
from .db import DB_FILENAME, init_db, make_engine, session_scope, table_counts
from .discovery import CONNECTORS, CampaignBudget, FeedConnector, build_queries, run_discovery, store_candidate
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
        connectors = [CONNECTORS[n](fetcher, s.discovery.contact_email) for n in names]
        report = run_discovery(engine, connectors, queries, budget, limit_per_query=args.limit,
                               memory_days=0 if args.force else s.discovery.search_memory_days)
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


def _cmd_score(args: argparse.Namespace) -> int:
    from .scoring import score_all, score_idea

    s = load_settings()
    engine = make_engine(_db_path(args))
    init_db(engine)
    results = [score_idea(engine, s, args.idea)] if args.idea else score_all(engine, s)
    for r in results:
        print(f"idea {r.idea_id:>5}  {r.status:<24} quality {r.idea_quality:5.1f} (coverage {r.coverage:.0%})  "
              f"priority {r.priority:5.1f}  band {r.band}" + (f"  HARD FAIL: {', '.join(r.hard_fails)}"
                                                               if r.hard_fails else ""))
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


def _cmd_campaign(args: argparse.Namespace) -> int:
    from .ai import AIGateway, default_providers
    from .campaign import CampaignLimits, CampaignRunner, parse_request

    if args.dry_run:
        print(json.dumps(parse_request(args.request or "").to_dict(), indent=2))
        return 0
    s = load_settings()
    engine = make_engine(_db_path(args))
    init_db(engine)
    limits = CampaignLimits(max_queries=args.max_queries, docs_per_round=args.docs, deepen_top_ideas=args.deepen)
    with PoliteFetcher(s, cache_dir=s.resolve_path(s.paths.data_dir) / "http_cache",
                       max_requests_per_host=s.budgets.max_sources_per_domain) as fetcher:
        gw = AIGateway(engine, s, default_providers())
        connectors = [CONNECTORS[n](fetcher, s.discovery.contact_email) for n in CONNECTORS]
        runner = CampaignRunner(engine, s, fetcher, gw, connectors, limits)
        cid = args.resume or runner.create(args.request)
        if not args.resume:
            with session_scope(engine) as sess:
                from .db.models import Campaign
                print("Parsed request:", json.dumps(sess.get(Campaign, cid).spec))
        report = runner.run(cid)
    print(json.dumps(report.__dict__, indent=2, default=str))
    return 0


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
    di.add_argument("--connector", default="all", help="arxiv,openalex,crossref or all")
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
    sco = sub.add_parser("score", help="score ideas deterministically and move them through the status pipeline")
    sco.add_argument("--idea", type=int, help="score one idea (default: all)")
    sco.add_argument("--db", help="database path or SQLAlchemy URL")
    sco.set_defaults(func=_cmd_score)
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
    wb = sub.add_parser("web", help="start the read-only dashboard (default http://127.0.0.1:8765/)")
    wb.add_argument("--host", default="127.0.0.1")
    wb.add_argument("--port", type=int, default=8765)
    wb.add_argument("--db", help="database path or SQLAlchemy URL")
    wb.set_defaults(func=_cmd_web)
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
