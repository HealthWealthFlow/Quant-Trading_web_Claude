# Checkpoint

**Last completed:** M5 — AI layer (2026-09-30)
**Next:** M6 — Scoring (see `TODO.md`)
**Branch:** `claude/loving-noether-1m40os`

## State at this checkpoint
- Tests: 98 passing (`pytest`, network and AI mocked); lint clean (`ruff check .`).
- Database: schema v1. Services: none yet. Research jobs: none.
- To use AI for real: set `DEEPSEEK_API_KEY` in `.env`, and set `ai.prices.deepseek-chat` (USD per 1M tokens,
  from DeepSeek's pricing page) in `config/local.yaml`. Then `qsd extract <file.pdf>`.

## How to resume
Open a Claude Code session on this repo/branch and say "resume". The session follows `CLAUDE.md`.
