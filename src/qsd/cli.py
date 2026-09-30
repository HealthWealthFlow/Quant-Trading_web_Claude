"""Command-line entry point: `qsd <command>`."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .config import load_settings
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="qsd", description="Quant Strategy Discovery & Source Intelligence")
    p.add_argument("--version", action="version", version=f"qsd {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("config", help="print the effective configuration").set_defaults(func=_cmd_config)
    sub.add_parser("status", help="show build milestones and the next one").set_defaults(func=_cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
