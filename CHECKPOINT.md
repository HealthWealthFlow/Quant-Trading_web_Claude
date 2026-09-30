# Checkpoint

**Last completed:** M7 — Research package + backtest queue (2026-09-30)
**Next:** M8 — Minimal dashboard (see `TODO.md`)
**Branch:** `claude/loving-noether-1m40os`

## State at this checkpoint
- Tests: 113 passing (`pytest`); lint clean (`ruff check .`).
- Database: schema v2. Services: none yet. Research jobs: none.
- Flow: `qsd discover`/`fetch`/`extract` → `qsd score` → `qsd queue --submit-ready` → `data/backtest_queue/pending/`.
- Package contract for the Quant Auto OS: `schemas/research_package.schema.json`.

## How to resume
Open a Claude Code session on this repo/branch and say "resume". The session follows `CLAUDE.md`.
