# Changelog

## M3 — Polite fetcher + compliance (2026-09-30)
- `qsd.fetch`: URL canonicalization (tracking params, fragments, default ports; rejects embedded credentials).
- robots.txt checked for every URL and redirect target (RFC 9309: 4xx → allow, 5xx/unreachable → disallow);
  Crawl-delay honoured; cannot be disabled.
- Per-host spacing, exponential backoff with jitter, Retry-After honoured (over-long waits end the attempt),
  cooldown after repeated errors, optional per-host request budget.
- No credentials sent, cookies cleared after every request, streaming size cap, max 5 redirects.
- Barrier detection: login walls/401/403 → ACCESS_RESTRICTED, CAPTCHA → MANUAL_ACCESS_REQUIRED (body discarded),
  paywall → PAYWALLED (publicly delivered part only).
- Request classification (spec §14) and header redaction (§15). ETag/Last-Modified cache.
- `fetch_and_store` + `qsd fetch <url>`: Source per canonical URL, fetch_log without headers/bodies,
  errors table for every failure. 72 tests (network mocked).

## M2 — Source handlers (2026-09-30)
- `qsd.handlers`: format detection (magic bytes > content type > extension) and handlers for TXT/MD/JSON/XML/code,
  HTML (scripts removed, `citation_*` meta), PDF (per-page), DOCX, PPTX (slides, notes, tables), CSV/XLSX
  (formulas never evaluated), EPUB (no AGPL dependency; DRM books refused), ZIP (one level, safe).
- Every text block keeps its location (page / slide / section / sheet / member) for fact provenance.
- Limitations are reported, never papered over: ENCRYPTED, DRM_PROTECTED, NO_TEXT_LAYER_OCR_REQUIRED,
  UNREADABLE_CHART_DATA, UNSUPPORTED_FORMAT, UNSAFE_ARCHIVE, TRUNCATED, PARSE_ERROR, macro containers.
- File-creation dates are stored as `file_created_date`, never as the publication date.
- `qsd.security`: prompt-injection phrase detection (flagged, text preserved) and nonce-delimited untrusted wrapper.
- Safe ZIP: member/size/ratio limits, no path traversal, absolute paths or symlinks.
- `qsd.localscan` + `qsd scan`: read-only folder indexing into `local_files`; `qsd parse <file>` summary.
- References (DOI / arXiv / SSRN) extracted from text and links. 53 tests.

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
