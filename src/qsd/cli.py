"""Command-line entry point: `qsd <command>`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import load_settings
from .db import DB_FILENAME, init_db, make_engine, table_counts
from .fetch import PoliteFetcher, fetch_and_store
from .handlers import parse_file
from .localscan import scan_paths
from .state import load_state, next_milestone, validate_state


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
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
