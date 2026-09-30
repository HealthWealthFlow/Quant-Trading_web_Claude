# Checkpoint

**Last completed:** M9 — Campaign runner (2026-09-30). **Phase 1 complete.**
**Next:** Phase 2 (only when the user approves a new budget) — see `TODO.md`.
**Branch:** `claude/loving-noether-1m40os` (not yet merged into `main`).

## State at this checkpoint
- Tests: 123 passing (`pytest`, network and AI mocked); lint clean (`ruff check .`).
- Database: schema v3 (older databases auto-migrate). Services: `qsd web`. Research jobs: none.
- Not yet run against live APIs/AI from this environment (cloud network policy blocks research hosts); first live
  run should be on the user's PC with keys + prices configured (README §2).

## How to resume
Open a Claude Code session on this repo/branch and say "resume". The session follows `CLAUDE.md`.
