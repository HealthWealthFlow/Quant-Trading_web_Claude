# Checkpoint

**Last completed:** M3 — Polite fetcher + compliance (2026-09-30)
**Next:** M4 — Discovery connectors (see `TODO.md`)
**Branch:** `claude/loving-noether-1m40os`

## State at this checkpoint
- Tests: 72 passing (`pytest`, network mocked); lint clean (`ruff check .`).
- Database: schema v1. Services: none yet. Research jobs: none.
- Try it: `qsd fetch <public url>`, `qsd parse <file>`, `qsd scan <folder>`.
- Note: the Claude Code cloud container's network policy blocks many research hosts (e.g. arxiv.org);
  live runs are meant for the user's PC/server. See KNOWN_ISSUES.md.

## How to resume
Open a Claude Code session on this repo/branch and say "resume". The session follows `CLAUDE.md`.
