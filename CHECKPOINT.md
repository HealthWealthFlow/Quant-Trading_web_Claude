# Checkpoint

**Last completed:** M9 — Campaign runner (2026-09-30). **Phase 1 complete.**
**Next:** Phase 2 (only when the user approves a new budget) — see `TODO.md`.
**Branch:** `claude/loving-noether-1m40os` (Phase 1 merged into `main`; this branch now carries post-launch fixes).
**Since Phase 1:** fact-check tuning after the first live run (CHANGELOG 2026-10-01, D24, D25).

## State at this checkpoint
- Tests: 134 passing (`pytest`, network and AI mocked); lint clean (`ruff check .`).
- Database: schema v4 (older databases auto-migrate). Services: `qsd web`. Research jobs: none.
- First live run on the user's PC (campaign 1: 85 sources, 3 documents, 3 ideas, ~$0.09) found all ideas stuck in
  NEEDS_REVIEW; fixed by D24/D25. Next live step: `qsd reground`, then a `--docs 10` campaign.

## How to resume
Open a Claude Code session on this repo/branch and say "resume". The session follows `CLAUDE.md`.
