# Changelog

## Unreleased
- Market-regime taxonomy (`qsd.taxonomy`) and spec §137 (user addendum).

## M0 — Foundation (2026-09-30)
- Project layout (`src/qsd`), `pyproject.toml`, `qsd` CLI (`status`, `config`).
- Typed configuration (YAML + local overrides + `QSD_*` env), budgets, crawling policy; robots.txt respect enforced.
- JSON-lines logging with secret redaction.
- Resume system: `PROJECT_STATE.json`, `CHECKPOINT.md`, `TODO.md`, `DECISIONS.md`, `KNOWN_ISSUES.md`,
  `ARCHITECTURE.md`, `CLAUDE.md`; full spec in `docs/MASTER_SPEC.md`.
- CI: ruff + pytest on GitHub Actions. 17 tests.
