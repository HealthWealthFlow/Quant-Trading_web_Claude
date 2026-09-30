# Checkpoint

**Last completed:** M4 — Discovery connectors (2026-09-30)
**Next:** M5 — AI layer (see `TODO.md`)
**Branch:** `claude/loving-noether-1m40os`

## State at this checkpoint
- Tests: 84 passing (`pytest`, network mocked); lint clean (`ruff check .`).
- Database: schema v1. Services: none yet. Research jobs: none.
- Try it: `qsd queries --asset ETF --regime CRASH`, `qsd discover --asset FOREX`, `qsd discover "fx carry"`.
- Set `discovery.contact_email` (config/local.yaml) before live OpenAlex/Crossref use.

## How to resume
Open a Claude Code session on this repo/branch and say "resume". The session follows `CLAUDE.md`.
