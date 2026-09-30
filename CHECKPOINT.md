# Checkpoint

**Last completed:** M6 — Scoring (2026-09-30)
**Next:** M7 — Research package + backtest queue (see `TODO.md`)
**Branch:** `claude/loving-noether-1m40os`

## State at this checkpoint
- Tests: 108 passing (`pytest`); lint clean (`ruff check .`).
- Database: schema v2 (auto-migrates v1 on `qsd db init` or any command). Services: none. Research jobs: none.
- Flow so far: `qsd discover` / `qsd fetch` / `qsd extract <file>` → `qsd score`.

## How to resume
Open a Claude Code session on this repo/branch and say "resume". The session follows `CLAUDE.md`.
