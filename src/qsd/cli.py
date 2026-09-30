"""Command-line entry point: `qsd <command>`."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .config import load_settings
from .db import DB_FILENAME, init_db, make_engine, table_counts
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
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
