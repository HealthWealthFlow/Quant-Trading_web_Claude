# Changelog

## M1 — Database schema (2026-09-30)
- SQLite research DB (SQLAlchemy 2, Postgres-compatible types), schema v1, `qsd db init` / `qsd db info`.
- Tables: campaigns, sources, source_links, fetch_log, source_facts, ideas, idea_regimes, idea_sources,
  idea_status_history, rejections, search_queries, ai_calls, ai_cache, errors, local_files.
- Missing text defaults to UNKNOWN; scores NULL until scored; performance only as `claimed_*`.
- Enforced: enum values, confidence 0–1, source tier 1–5, one row per idea × market regime,
  quote length ≤ 300 chars, no credential-like columns (test).
- Shared vocabularies in `qsd.taxonomy` (status pipeline, rejection reasons/hard fails, access states, etc.).
- Market-regime grouping (spec §137): Bullish / Bearish / Consolidation / Crash. 31 tests.

## M0 — Foundation (2026-09-30)
- Project layout (`src/qsd`), `pyproject.toml`, `qsd` CLI (`status`, `config`).
- Typed configuration (YAML + local overrides + `QSD_*` env), budgets, crawling policy; robots.txt respect enforced.
- JSON-lines logging with secret redaction.
- Resume system: `PROJECT_STATE.json`, `CHECKPOINT.md`, `TODO.md`, `DECISIONS.md`, `KNOWN_ISSUES.md`,
  `ARCHITECTURE.md`, `CLAUDE.md`; full spec in `docs/MASTER_SPEC.md`.
- CI: ruff + pytest on GitHub Actions. 17 tests.
