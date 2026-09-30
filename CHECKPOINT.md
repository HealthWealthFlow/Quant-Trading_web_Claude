# Checkpoint

**Last completed:** M8 — Minimal dashboard (2026-09-30)
**Next:** M9 — Campaign runner (see `TODO.md`) — last Phase 1 milestone
**Branch:** `claude/loving-noether-1m40os`

## State at this checkpoint
- Tests: 119 passing (`pytest`); lint clean (`ruff check .`).
- Database: schema v2. Services: `qsd web` → http://127.0.0.1:8765/ (read-only). Research jobs: none.
- Flow: `qsd discover`/`fetch`/`extract` → `qsd score` → `qsd queue --submit-ready`; watch it in `qsd web`.

## How to resume
Open a Claude Code session on this repo/branch and say "resume". The session follows `CLAUDE.md`.
