# Changelog

## Fix: `qsd reground` hung on a slow website (2026-10-01)
- `reground` reads the saved download (HTTP cache) first and only downloads again when no copy exists; prints
  progress per source.
- Every download now has a total time limit (4 × `crawling.request_timeout_seconds`), so a server that trickles
  bytes can no longer stall a run (`DOWNLOAD_TIMEOUT`). Cache entries also record the final URL. 134 tests.

## Fact-check tuning after the first live run (2026-10-01)
- Grounding accepts a quote of ≥ 6 words when ≥ 85% of its words occur in order in one short stretch of the source
  and every number in it matches exactly; the stored quote is replaced by the source's own wording. Short quotes and
  numbers still need an exact match.
- Every removed value is saved with the model's quote, location and reason (`ideas.grounding`, schema v4,
  auto-migration) and shown on the idea page ("Removed by fact-check") and in the package (`grounding`, v1.1).
- NEEDS_REVIEW now means the extraction was unreliable (≥ half of ≥ 4 offered values failed) or the document
  contains injection text — not "3 values removed". Removed values are no longer listed as red flags of the strategy.
- Stage-B prompt b2: shortest exact quote, definitions of timeframe / data frequency / holding period / rebalance,
  SUITED only when the source reports favourable returns in that market direction.
- `qsd reground [--source ID]`: re-applies the current rules to stored AI answers (no AI call; document re-read from
  the local file or HTTP cache, refused if it changed) and re-scores the ideas.
- README: API keys are environment variables; `.env` is not read automatically. 132 tests.

## M9 — Campaign runner (2026-09-30) — Phase 1 complete
- `qsd.campaign`: plain-English request → stored spec (assets, market directions, families, mode, target) with
  `--dry-run`; loop discover → rank unfetched candidates (tier + abstract relevance) → fetch (PDF link first) →
  two-stage extraction → scoring → deepening of top ideas (replication, contradiction and recent-evidence queries).
- Relation check (stage C, cheap model, abstract only): REPLICATES / SUPPORTS / CONTRADICTS links are recorded only
  when the quote is found verbatim in the abstract and confidence ≥ 0.6; same-study copies are never counted.
- Stop controller: budgets (searches, URLs, documents, AI calls/tokens/cost, runtime), low yield, target reached
  (≥ N distinct promising families); stop reason stored. One campaign budget shared with the AI gateway.
- Resumable: phases, processed sources, deepened ideas and budget spent saved in `campaigns.state` (schema v3,
  auto-migration); AI cache + search memory prevent repeated paid work.
- README getting-started guide. 123 tests (end-to-end campaign with mocked APIs, PDF and AI).

## M8 — Minimal dashboard (2026-09-30)
- `qsd web` (FastAPI + Jinja2, server-rendered, no JavaScript, binds to 127.0.0.1 by default; warns if exposed).
- Overview tiles (§113), strategies by market direction (Long / Short / Consolidation / Crash, §137), top priorities.
- Ideas table (§115) with status / market-direction / asset filters; idea detail (§116) with red-flag panel (§117),
  unscored components, missing rules, provenance, claims marked "not validated", regime evidence, research
  completeness (§128), handoff eligibility, scores, known rules with quotes/pages, downstream checks, status history.
- Sources (§114/§118 basic filters), AI cost (§120: by provider/model/task, cache hit rate, linear month projection,
  cost per promising idea), errors (§133).
- Security: autoescape everywhere (untrusted titles/quotes), only http(s) links rendered, read-only (no write routes).
- Status badges pair colour with icon + text (never colour alone); light/dark via prefers-color-scheme.
- Package market-regime rows now in fixed order. 119 tests (incl. XSS test).

## M7 — Research package + backtest queue (2026-09-30)
- `qsd.packaging`: research package (spec §122) with provenance (DOI/URL/hash/root evidence), original /
  supporting / contradicting sources, known rules with quote + page, unknown rules, parameters, market-regime
  profile with evidence (§137), data and point-in-time requirements, per-asset downstream checks (§57–§62),
  concerns, scores with coverage and unscored components, research-completeness PASS/FAIL checklist (§128).
- Mandatory downstream warning (§124); source claims isolated, marked `validated: false`.
- Handoff rule (§123, configurable): no hard fail, eligible status, completeness, coverage, quality, data
  availability, known instrument, non-latency-critical, provenance known. Claims are never a criterion.
- File queue `<queue_dir>/pending/<strategy_id>.json` (atomic writes) → status SUBMITTED_TO_BACKTEST. No broker path.
- `schemas/research_package.schema.json` for the Quant Auto OS; `qsd package`, `qsd queue`. 113 tests.

## M6 — Scoring (2026-09-30)
- `qsd.scoring` (deterministic, no AI): red-flag language (§54), hard fails (§53: martingale, unlimited averaging,
  look-ahead, survivorship, scam signals, unquantifiable rules), formalization completeness (§48), parameter
  complexity (§55), source quality (§25), evidence quality (§26), replication from independent roots only (§70/§72),
  fingerprints + NEW/VARIANT/DUPLICATE (§69), novelty (§80), idea quality with configurable §52 weights, research
  priority (§81, never uses claims), quality-gate bands (§126).
- Components that can't be assessed are UNSCORED (not guessed); `coverage` recorded; ideas below the coverage
  threshold go to RESEARCHING with the list of what to assess, instead of being archived.
- Status pipeline with history; rejections recorded once and never deleted; regime-inferred-only flag and
  diversification tags incl. REGIME:*.
- Schema v2 (`ideas.score_details`) with an additive migration framework (v1 databases upgrade in place).
- `qsd score`. 108 tests.

## M5 — AI layer (2026-09-30)
- Provider adapters: DeepSeek (default) and OpenAI via their chat-completions HTTP APIs (JSON mode); Claude via the
  official `anthropic` SDK (optional extra) with `claude-opus-5-5` and server-side refusal fallback. Keys from env.
- `AIGateway`: cache (task + prompt version + provider + model + exact prompt), cost ledger for every call (hits,
  successes, failures), worst-case pre-call budget checks (campaign / day / month); unpriced models are never called.
- Two-stage extraction: cheap triage (stage A) → strong extraction (stage B) only for promising sources, on
  relevant sections only with [p.N]/[slide N] markers; source text always in the untrusted wrapper.
- Grounding (no AI): non-UNKNOWN values need a verbatim quote found in the source; numbers must exist in the source;
  claims need their number inside their own quote; unverifiable regime evidence downgraded to RATIONALE_INFERRED
  (confidence ≤ 0.5). Everything removed is flagged; heavily-flagged ideas go to NEEDS_REVIEW.
- Ideas stored with per-field provenance facts (page/slide, quote, confidence), regime rows, DESCRIBES link.
- Untrusted wrapper delimiter is now a content hash (unforgeable and cache-friendly).
- `qsd extract <file>`. 98 tests (fake provider; no real AI calls).

## M4 — Discovery connectors (2026-09-30)
- `qsd.discovery`: arXiv (q-fin categories), OpenAlex, Crossref (official APIs; `contact_email` for polite pools),
  RSS/Atom feeds, user URL lists. Metadata only; absent fields stay UNKNOWN; XML parsed without entity expansion.
- Official-API allowlist in code (`OFFICIAL_API_ENDPOINTS`): only these skip robots.txt; everything else is checked.
- Query families per asset class (spec §39), vocabulary expansion (§40), market-regime queries (§137).
- Cross-connector dedupe by DOI > arXiv id > URL; first known values never overwritten; abstracts, IDs, PDF links
  and tier basis stored as provenance facts.
- Deterministic initial tiers from the §24 hierarchy (unknown domains stay untiered).
- Search memory (normalized query per connector, configurable window) and campaign budgets that stop discovery.
- CLI: `qsd queries`, `qsd discover`, `qsd feed`. 84 tests (APIs mocked).

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
