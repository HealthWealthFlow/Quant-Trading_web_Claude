# HANDOFF — Quant Strategy Discovery & Source Intelligence (QSD)

> Complete orientation for another AI model (or developer) taking over this project. Read this first, then
> `CLAUDE.md` (working rules), `CHANGELOG.md` (what changed when) and `DECISIONS.md` (why). Section numbers like
> "§84" refer to the full requirements in `docs/MASTER_SPEC.md`.
>
> Last updated: 2026-10-02 · main at the merge of PR #10 · 164 tests passing · DB schema v4 · package v1.2 ·
> prompts a1 / b3 / c1.

---

## 1. What this system is (and is not)

**QSD finds, reads, fact-checks, scores and packages *trading-strategy hypotheses*** from research papers, web
pages, local documents and YouTube metadata, so that a separate downstream system (the user's "Quant Auto OS")
can backtest them.

It **is**: a research assistant with provenance — every extracted rule carries a verbatim quote and location
(page / slide / description) from its source, unverifiable values become `UNKNOWN`, and scores never use the
source's claimed performance.

It **is not**: a backtester, a signal service or a trading bot. It **never places trades** and has no broker code.
Claimed returns/Sharpe are stored only as `claimed_*` ("NOT VALIDATED").

Owner: GitHub `HealthWealthFlow/Quant-Trading_web_Claude` (private). The user runs it on **Windows** at
`D:\Quant-Trading_web_Claude`. Built with Claude Code in a cloud container (Linux), in pull requests #1–#10.

---

## 2. Non-negotiable rules (system + user)

From the spec (enforced in code; do not weaken):
1. **No fabrication.** Missing information is `UNKNOWN` (or another `MissingValue`), never guessed (§1, §47).
   Every non-UNKNOWN AI value must be grounded in the source text (see §8 Grounding).
2. **External performance = claims only** (`claimed_sharpe`, `claimed_cagr`, …), never used for ranking (§82).
3. **Fetched content is untrusted data**, never instructions; it is wrapped before going to an AI, and prompt-
   injection phrases are flagged (§7, §8). Never execute downloaded content.
4. **No circumvention**: robots.txt always respected (cannot be disabled), no paywall/CAPTCHA/login bypass,
   polite rate limits (§10, §102–§107).
5. **Secrets never reach logs, the DB, URLs or caches** (§13–§15). API keys come only from environment variables.
6. **Never trades** (§125).

From the user (keep honouring these):
- **Merge only when the user says "merge it".** Workflow: develop on branch `claude/loving-noether-1m40os`, open a
  PR to `main`, wait for CI, user says "merge it", merge, user runs `git pull` on Windows.
- **Budget awareness**: the user capped AI spend; runtime caps are in `config` (see §12). The Claude Code build
  budget was originally capped at ~$30 — keep changes focused.
- **Never edit the user's own "Idea Extractor"** (`D:\Quant Trading\trading-extractor\…`, launched from
  `Desktop\RunIdeaExtractor.bat - Shortcut.lnk`). It was used only as a read-only reference (see §16).
- **Do not touch the unrelated `healthwealthflow/maxcare-hr` repository** (it was only inspected; its plan was never
  approved).
- **Never ask the user to paste API keys.** They live in Windows user environment variables.
- **YouTube: official Data API only** — no yt-dlp, no video download, no transcript scraping (YouTube terms).

---

## 3. Status at handoff

| Area | State |
|---|---|
| Phase 1 milestones M0–M9 | Done (foundation, DB, handlers, polite fetcher, discovery, AI layer, scoring, packaging, dashboard, campaign runner) |
| Post-launch work (PRs #2–#10) | Grounding fixes, `reground`/`factcheck`, PDF spacing fix, dashboard launcher + port 8877, guided research + live monitor, YouTube, `sources.txt`, maturity badge, Obsidian notes, search/selection fixes |
| Tests | 164 (`pytest`), all network/AI mocked; `ruff check .` clean; GitHub Actions CI (`.github/workflows/ci.yml`) |
| Live usage | 3 real campaigns on the user's PC (see §15). System works end to end; yield of good strategies still low |
| Open offers to the user | (a) YouTube **playlist** support, (b) paste a YouTube link directly at the `research.bat` prompt. Not yet built |

---

## 4. The user's environment (Windows)

| Item | Value / note |
|---|---|
| Project folder | `D:\Quant-Trading_web_Claude` (git clone; `git pull` to update) |
| Python | venv at `.venv` (`.venv\Scripts\activate`, `.venv\Scripts\qsd.exe`). User's base Python is 3.14 (`C:\Python314`) |
| Install | `pip install -e ".[dev]"` (editable: `git pull` updates code without reinstall unless dependencies change) |
| AI key | `DEEPSEEK_API_KEY` as a **Windows user environment variable** (`setx`). `.env` is **not** loaded |
| YouTube key | `YOUTUBE_API_KEY` set with `setx` (Google Cloud project "OpenClaw Assistant", key restricted to YouTube Data API v3) |
| Local config | `config\local.yaml` (git-ignored) holds at least the DeepSeek price, e.g. `ai: {prices: {deepseek-chat: {input_per_mtok: 1.32, output_per_mtok: 3.96}}}` (conservative; real price not confirmed) |
| Dashboard | http://127.0.0.1:8877/ (port 8877 because another local app, "AQS Strategy Monitor", uses 8801) |
| Launchers | `research.bat` (main entry), `dashboard.bat` (may be missing locally — user's folder showed `auto_idea_finder.bat` instead, a file not created by us) |
| Optional | `sources.txt` (not yet created by user), `export.notes_dir` → Obsidian vault `D:\Quant Trading\TradingVault` (not yet configured) |
| Console | Windows code page; console output uses ASCII arrows and `errors="replace"` |

---

## 5. Architecture and pipeline

```
                       request (plain English)  or  sources.txt list
                                   │
             ┌─────────────────────┴──────────────────────┐
             │ campaign/parse.py → CampaignSpec           │ campaign/seed.py (videos, channels, links,
             │ (assets, market directions, strategy types)│  files, folders; already-read skipped)
             └─────────────────────┬──────────────────────┘
                                   ▼
  DISCOVER  discovery/ connectors (arXiv, OpenAlex, Crossref, YouTube*) ← runner.plan_queries()
            search memory (30 d), tiering, abstracts → Source rows (metadata only)
                                   ▼
  SELECT    finance-word filter → free PDFs first → relevance → tier   (CampaignRunner._select)
                                   ▼
  READ      fetch/ polite fetcher (robots, rate limits, barriers, cache, deadline) or local file or
            YouTube description → handlers/ (PDF, HTML, DOCX, PPTX, XLSX/CSV, EPUB, TXT/MD, ZIP)
                                   ▼
  EXTRACT   ai/ stage A triage (cheap) → stage B extraction (≤3 strategies, retry 1) → grounding
            (quotes must exist in source; numbers must exist) → Idea rows + SourceFact provenance
                                   ▼
  SCORE     scoring/ red flags, hard fails, completeness, maturity, source/evidence/idea quality,
            coverage, dedupe, root evidence, priority → status (PROMISING / RESEARCHING / ARCHIVED …)
                                   ▼
  DEEPEN    replication / criticism / recent-evidence searches (papers only) → stage C relation check
            on abstracts (grounded) → SUPPORTS / CONTRADICTS links → re-score
                                   ▼
  OUTPUT    packaging/ ResearchPackage JSON → data/backtest_queue/pending/*.json (eligible only)
            export.py Markdown notes (Obsidian) · web/ dashboard + /live monitor · console progress
```
`*` YouTube joins only when `YOUTUBE_API_KEY` is set.

---

## 6. Repository map

| Path | Purpose |
|---|---|
| `research.bat` | Windows double-click entry: runs `qsd research` (guided wizard) |
| `dashboard.bat` | Starts dashboard on 8877, opens browser; checks the port owner is QSD |
| `sources.example.txt` | Template for the user's `sources.txt` (git-ignored) |
| `config/default.yaml` | All defaults (see §12). Overrides: `config/local.yaml`, then `QSD_<SECTION>__<KEY>` env vars |
| `schemas/research_package.schema.json` | JSON Schema of the package (contract with the Quant Auto OS); regenerate with `qsd package --schema` |
| `docs/MASTER_SPEC.md` | Condensed requirements (§1–§137; §137 = market-direction grouping added by the user) |
| `CLAUDE.md` | Rules for AI coding sessions; `PROJECT_STATE.json`, `CHECKPOINT.md`, `TODO.md`, `KNOWN_ISSUES.md`, `DECISIONS.md` (D1–D28), `CHANGELOG.md`, `ARCHITECTURE.md`, `README.md` |
| `src/qsd/cli.py` | `qsd` command (argparse). All subcommands, see §13 |
| `src/qsd/config.py` | Pydantic settings (`Settings`, `AIConfig`, `Budgets`, `Crawling`, `Discovery`, `Scoring`, `Handoff`, `Export`); `get_secret()` reads env only; `REPO_ROOT` |
| `src/qsd/taxonomy.py` | Enums: `MarketRegime`, `RegimeSuitability`, `RegimeBasis`, `AssetClass`, `AccessStatus`, `IdeaStatus`, `RejectionReason`, `SourceRelation`, `IdeaSourceRole`, `CampaignStatus`, `ExtractionMethod`, `MissingValue`/`UNKNOWN`, `REGIME_LABELS` ("Long (uptrend)", "Short (downtrend)", "Consolidation (sideways)", "Crash (crisis)") |
| `src/qsd/db/` | SQLAlchemy 2 models (`models.py`, `SCHEMA_VERSION = 4`), `make_engine` (SQLite WAL + busy_timeout), `init_db` with additive `MIGRATIONS`, `new_idea()` (creates 4 regime rows) |
| `src/qsd/handlers/` | Format detection + parsers; `HandlerResult` (blocks with `Location`, tables, links, metadata, limitations, sha256 of bytes); PDF v2 re-reads glued-word pages; safe ZIP limits |
| `src/qsd/fetch/` | `PoliteFetcher` (robots RFC 9309, per-host rate limiter, backoff/Retry-After, cooldown, size cap, total-download deadline, no cookies, ETag cache, `extra_headers` never cached), `OFFICIAL_API_ENDPOINTS` allowlist, URL canonicalisation, access-barrier detection, `fetch_and_store` |
| `src/qsd/discovery/` | Connectors (`ArxivConnector`, `OpenAlexConnector`, `CrossrefConnector`, `YouTubeConnector`, `FeedConnector`), `build_connectors()`, `run_discovery()` (search memory, budgets, `on_search` progress, video links), query families, tiering |
| `src/qsd/ai/` | Providers (DeepSeek/OpenAI-compatible JSON mode; Anthropic SDK optional), `AIGateway` (cache, ledger, budgets), prompts (versioned), schemas, `sections.select_relevant`, grounding, `extract_ideas`, `reground_source` |
| `src/qsd/scoring/` | Rules (red flags, hard fails, completeness, maturity, complexity), scores (source/evidence/idea quality, priority, gate band), dedupe/fingerprints/root evidence, `score_idea` status pipeline |
| `src/qsd/packaging/` | `ResearchPackage` (v1.2), `build_package`, `submit_to_queue` (handoff rule), schema export |
| `src/qsd/campaign/` | `parse.py` (request → spec), `runner.py` (`CampaignRunner`, `plan_queries`, progress `_note`, video/local reading, deepen), `seed.py` (`sources.txt` seeding) |
| `src/qsd/research.py` | Guided wizard (`run_wizard`), in-process dashboard start, cost estimate, summary, auto queue + notes export |
| `src/qsd/sources_file.py` | `sources.txt` parser (`SourceItem`: youtube_video / youtube_channel / url / path, `| n:25`, `| title`) |
| `src/qsd/export.py` | Markdown/Obsidian notes (escaped; only overwrites files with its own `qsd_id`) |
| `src/qsd/web/` | FastAPI + inline Jinja2 templates (autoescape, no JS): `/live`, `/`, `/ideas`, `/ideas/{id}`, `/sources`, `/ai-cost`, `/errors`, `/healthz` |
| `src/qsd/security.py` | Injection detection, `wrap_untrusted` (content-hash nonce) |
| `src/qsd/logging_setup.py` | JSON logs + `redact()` (bearer tokens, key=value secrets, `sk-…`, `AKIA…`, `AIza…`) |
| `src/qsd/localscan.py`, `state.py` | Read-only folder index; build-milestone state |
| `tests/` | pytest suite; `fixtures.py` generates PDFs/DOCX/PPTX/XLSX/EPUB/ZIP; HTTP via `httpx.MockTransport`; AI via fake providers |

---

## 7. Data model (SQLite, `data/qsd.sqlite`, schema v4)

| Table | Key contents |
|---|---|
| `schema_meta` | `schema_version` |
| `campaigns` | request_text, mode (`QUICK_DISCOVERY`, `SOURCES_FILE`, …), asset classes, families, target regimes, `spec` JSON, `state` JSON (`phases_done`, `processed_sources`, `deepened_ideas`, `spent`, `progress` = counters + last 80 events for the live monitor), status (`PLANNED/RUNNING/COMPLETED/STOPPED`), stop_reason |
| `sources` | title, author, organization, url, canonical_url (unique), local_path, publication_date, tier (1–5/NULL), format, content_hash (sha256 of bytes), access_status, quality scores, root_evidence_id, campaign_id |
| `source_links` | from → to, relation (`CITES` = video → linked paper/code, …) |
| `fetch_log` | every retrieval (redacted): request_url, status, retrieval_method (`HTTP`, `CACHE`, `API_METADATA`, `LOCAL_FILE`), error |
| `source_facts` | provenance: fact_type (`ABSTRACT`, `PDF_URL`, `ID_DOI`, `ID_YOUTUBE`, `TITLE_OVERRIDE`, `TRIAGE`, rule names, `PARAMETER:x`, `REGIME:x`, …), value, quote (≤300), page/slide/location, method, confidence |
| `ideas` | strategy fields (instrument, universe, timeframe, signal, entry/exit/stop/take-profit rules, sizing, rebalance, holding period, costs, …), parameters JSON, unknown_rules, claimed_* (verbatim), economic_rationale, red_flags, scores (source/evidence/idea quality, completeness, complexity, replication, novelty, priority), status, hard_fail_reasons, fingerprint/dedupe, `score_details` JSON (v2), `grounding` JSON (v4: removed values with the AI's quote + reason, values offered, realigned) |
| `idea_regimes` | 4 rows per idea (BULLISH/BEARISH/CONSOLIDATION/CRASH): suitability, basis, confidence, source_fact_id |
| `idea_sources` | idea ↔ source role (`DESCRIBES`, `SUPPORTS`, `REPLICATES`, `CONTRADICTS`, `ORIGINAL`) + note |
| `idea_status_history`, `rejections` | audit trail; rejections never deleted |
| `search_queries` | search memory: connector, query hash, purpose, results/useful counts |
| `ai_calls` | ledger: task, prompt version, model, tokens, cost, cache hit, success, cache_key |
| `ai_cache` | validated AI responses by cache key (enables free `reground`) |
| `errors`, `local_files` | error records (redacted); local scan index |

Migrations are **additive only** (`db/__init__.py: MIGRATIONS`): v1→2 `ideas.score_details`, v2→3 `campaigns.spec/state`,
v3→4 `ideas.grounding`. `init_db` runs them automatically on every command.

Other data on disk: `data/http_cache/` (sha256(url).json/.bin, never for credentialed requests),
`data/backtest_queue/pending/<strategy_id>.json`, `logs/`.

---

## 8. Core logic in detail

### 8.1 Request parsing (`campaign/parse.py`)
Deterministic keyword regexes → `CampaignSpec`: `asset_classes`, `regimes` (crash/bear/bull/sideways words),
`families` (momentum, mean reversion, carry, rotation, volatility, funding rate, breakout, seasonality, pairs
trading, trend following, PEAD, intraday, support and resistance, pullback, volume confirmation, moving average),
`mode`, `core_query` (request minus filler words), `target_families=3`. Shown to the user before running.

### 8.2 Search planning (`campaign/runner.plan_queries`)
1. full core query (+ "trading strategy" if it has no finance word); 2. short keyword form (arXiv ANDs all words);
3. each family: "<family> trading strategy [asset]" and "<family> strategy <regime term>"; 4. two vocabulary
variants per family; 5. generic `QUERY_FAMILIES` only if no family was recognised. De-duplicated, capped at
`max_queries` (8). Each query × each connector = one search (memory skips identical searches for 30 days).

### 8.3 Selection (`CampaignRunner._select`)
Only the current campaign's `NOT_FETCHED` sources. Skip papers whose title+abstract contain no market/trading word
(`FINANCE_WORDS`) unless they have no metadata yet or come from `sources.txt`/YouTube. Rank: free (open PDF / arXiv /
local / video) first → relevance (family/keyword hits) → tier. An unread source found again by a newer campaign is
moved to it.

### 8.4 Reading
- URL → `PoliteFetcher.fetch` → `parse_bytes` (format by content type/extension/magic).
- Local file → `parse_file` (read-only).
- YouTube video → title + channel + description as text (`API_METADATA`); the video is never downloaded.
- PDF: pdfplumber per page; pages with "glued" words (≥25-letter tokens) are re-read with `x_tolerance` 1.5 / 1.0.
- `sections.select_relevant`: keyword-scored blocks with `[p.N]` markers, ≤ `stage_b_max_chars` (40k).

### 8.5 AI extraction (`ai/extract.py`, `ai/prompts.py`)
- **Stage A** (cheap model, ≤6k chars, prompt a1): is it strategy research, worth a deep read?
- **Stage B** (strong model, prompt **b3**): at most 3 strategies; only stated fields (UNKNOWN omitted); each value
  with a verbatim `evidence_quote` (<30 words) + location; regimes SUITED only if the source reports favourable
  returns there. If the answer is invalid/cut off → **one retry asking for 1 strategy** (`STAGE_B_RETRIED_SHORT`).
- **Stage C** (cheap, prompt c1): relation of a found paper's abstract to an idea (REPLICATES/SUPPORTS/CONTRADICTS/
  UNRELATED/UNCLEAR); recorded only if the quote is in the abstract and confidence ≥ 0.6.
- Models: DeepSeek `deepseek-chat` for cheap + strong by default; Claude optional (`anthropic` extra, `claude-opus-5-5`).

### 8.6 Grounding (`ai/grounding.py`) — the anti-fabrication core
For every non-UNKNOWN value:
1. The quote must occur in the source text: exact after normalisation (case, whitespace, typographic quotes/dashes,
   PDF hyphenation), **or** ignoring all whitespace for quotes ≥12 chars (PDFs that lose spaces), **or** word
   alignment for quotes ≥6 words (≥85 % of words in order in one short stretch, numbers exact, and the differing words
   must not be words the value depends on). Realigned quotes are replaced by the source's own wording.
2. Every number in the value must occur in the source; a claim's number must be in its own quote.
3. Regime "SOURCE_STATED/EVIDENCE" without a found quote → downgraded to `RATIONALE_INFERRED`, confidence ≤ 0.5.
4. Failures → value set to UNKNOWN and recorded in `ideas.grounding.removed` (value, AI quote, location, reason).
5. **NEEDS_REVIEW** only if ≥50 % of ≥4 offered values failed (`UNRELIABLE_EXTRACTION`) or the document contains
   prompt-injection text.

Tools: `qsd reground` re-applies current grounding to cached AI answers (no AI cost; document re-read from file /
HTTP cache, refused if changed). `qsd factcheck <idea>` prints, per removed value, the closest source passage and the
share of words found (diagnosed the PDF-spacing bug).

### 8.7 Scoring (`scoring/`)
- Red flags (regex: guaranteed profit, 9x % win rate, martingale, …) and **hard fails** (martingale, unbounded
  averaging down, ≥2 scam flags, …) → `REJECTED`.
- Formalization completeness (rules present) → **maturity**: <35 CONCEPT, ≥35 PARTIAL, ≥60 TRADEABLE, ≥80 BACKTEST-READY.
- Source quality: tier base (1:75, 2:62, 3:42, 4:22, 5:5; untiered 30) + identifier/author/date/transparency − scam/
  injection penalties. Evidence quality: sample period, method, out-of-sample, costs, code, replication, cross-market.
- Idea quality: 14 weighted components (sum 100, `scoring.idea_weights`); unassessable ones are UNSCORED; **coverage** =
  assessed weight share; normalized = score over assessed weight.
- Gate (normalized): ≥85 HIGH_PRIORITY, ≥70 PROMISING, ≥55 RESEARCH_FURTHER, else ARCHIVE.
- Status: hard fail → REJECTED; duplicate fingerprint → DUPLICATE; NEEDS_REVIEW stays; coverage < 0.6 →
  RESEARCHING; else band → PROMISING / RESEARCHING / ARCHIVED. SUBMITTED_TO_BACKTEST is frozen.
- Research priority = weighted mix (idea 35 %, source 15 %, evidence 15 %, replication 10 %, novelty 10 %,
  completeness 10 %, diversification 5 %) × (0.5 + 0.5 × coverage). Claimed returns never used.

### 8.8 Packaging and handoff (`packaging/package.py`)
`ResearchPackage` v1.2: provenance, known rules (value+quote+location), unknown rules, parameters, market regimes,
data / point-in-time requirements, downstream checks per asset class, concerns + red flags, `source_claims` (not
validated), `grounding`, `maturity`, scores, research-completeness checklist, `handoff` (eligible + blocking reasons).
Always carries: **"EXTERNAL PERFORMANCE CLAIMS ARE NOT VALIDATED. DOWNSTREAM SYSTEM MUST RECOMPUTE EVERYTHING."**
Eligible when status PROMISING/READY, completeness ≥60, normalized quality ≥55, coverage ≥0.6, data availability ≥0.4.
`qsd queue --submit-ready` (also automatic at the end of `research.bat`) writes `data/backtest_queue/pending/*.json`.

### 8.9 Campaign loop (`CampaignRunner.run`)
discover (once; `phases_done`) → read `docs_per_round` selected sources → score all → deepen top `deepen` (5)
PROMISING/RESEARCHING ideas → stop with `ROUND_COMPLETE` / `TARGET_REACHED` (≥3 distinct promising families) /
`NO_NEW_SOURCES` / `BUDGET:…` / `CONFIG:…`. Ctrl+C → `STOPPED` with a resume hint. Everything saved in
`campaigns.state`, so `qsd campaign --resume ID` continues without repeating paid work (AI cache + search memory).

### 8.10 Guided research (`research.py`, `research.bat`)
Ask request (or **S** for `sources.txt`, **Q** quit) → show understood spec + sources line (YouTube on/off) → papers
per round (default 10) → cost estimate from history + caps → confirm → start dashboard in-process (or reuse; refuse a
foreign app on the port) and open `/live` → run with console progress → backtest queue → notes export (if configured)
→ summary → offer another round.

### 8.11 YouTube (`YouTubeConnector`, D27)
`search` (100 quota units) + `videos` (1 unit/50); channels via `channels` + `playlistItems` uploads (≈3 units);
key in `X-Goog-Api-Key` header; responses never cached; description research links (papers, DOIs, PDFs, code;
social/shop/affiliate/sign-up skipped) become linked candidates; deepen searches never use YouTube; videos are tier 3.

### 8.12 Dashboard (`web/`)
Read-only, 127.0.0.1:8877, autoescaped, http(s)-only links. `/live` auto-refreshes every 3 s while a campaign runs
(searches, papers found/read + progress bar, strategies with status/quality/market direction, AI cost vs cap,
activity log, stale warning). Idea page: summary, red flags, "Removed by fact-check", provenance, claims (not
validated), market direction with evidence, research completeness, handoff, scores, rules with quotes, history.

---

## 9. Process flows for the user (Windows)

| Goal | How |
|---|---|
| New research | double-click `research.bat` → type request → Enter (10 papers) → Y → watch `/live` |
| Own list | `notepad sources.txt` (format in `sources.example.txt`) → `research.bat` → **S** |
| Continue a campaign | `qsd campaign --resume ID --docs 10` |
| See results | dashboard (`research.bat` keeps it open while the window is open; or `dashboard.bat` / `qsd web`) or `qsd score` |
| Strategy details | dashboard idea page, `qsd package ID`, `qsd factcheck ID` |
| Send to backtester | automatic at end of research run; or `qsd queue --submit-ready` |
| Obsidian notes | set `export.notes_dir` in `config\local.yaml`; automatic at end of run, or `qsd notes` |
| Update the code | `git pull` (close research/dashboard windows first) |

---

## 10. AI cost and budget control
- Prices per model in config (`ai.prices`); **a model without a price is never called**.
- Before every call the gateway checks worst-case cost (input estimate + `max_tokens`) against campaign / day / month
  caps and refuses rather than overspend. Every call (hit, miss, failure) goes to `ai_calls`.
- Cache key = sha256(task | prompt version | provider | model | system | user prompt); content-hash nonces keep
  prompts stable. Changing prompt text **requires** bumping its version.
- Observed costs: ~$0.03 per paper read (stage A+B, DeepSeek at the conservative price). Today's spend ≈ $0.40 of the
  $1/day cap (as of the last run).

---

## 11. Fetching policy (`fetch/`)
User-Agent `QSD-Research-Bot/0.1`; robots.txt per origin (4xx = allow, 5xx/unreachable = disallow; official API
endpoints in `OFFICIAL_API_ENDPOINTS` bypass robots only); 10 req/min/domain; retries with backoff + Retry-After;
host cooldown after errors; 50 MB cap; **total download deadline 4 × 30 s**; cookies cleared; login/CAPTCHA pages
discarded, paywalls keep only the public part; ETag/Last-Modified cache (not for credentialed requests); URLs
canonicalised (tracking params removed); no credentials in URLs.

---

## 12. Configuration reference (`config/default.yaml`)
`paths` (data_dir `data`, log_dir, local_sources) · `asset_classes.enabled` · `ai` (provider, cheap/strong/
second-opinion models, stage A/B char and token limits — B 40k chars / 8000 tokens, `prices`) · `budgets`
(per day $1, month $20, campaign $2; AI calls 200, tokens 2M, searches 100, URLs 300, documents 150, per domain 25,
runtime 60 min) · `crawling` (robots always on, rate, retries, timeout 30 s, 50 MB) · `exploration` · `quality_gate`
(85/70/55) · `discovery` (contact_email, results_per_query 10, search_memory_days 30, youtube_enabled,
youtube_results_per_query 10, youtube_max_links_per_video 5) · `scoring` (idea_weights, min_coverage_for_gate 0.6) ·
`handoff` (60 / 55 / 0.6 / 0.4, queue_dir) · `export` (notes_dir, notes_subfolder "QSD Strategies").
Secrets (env only): `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `YOUTUBE_API_KEY`.

---

## 13. CLI reference (`qsd …`)
`research` (guided; `--port`) · `campaign "request" [--dry-run] [--docs N] [--max-queries N] [--deepen N]` /
`campaign --resume ID` · `sources [--file] [--dry-run]` · `discover [query…] [--connector arxiv,openalex,crossref,
youtube|all] [--asset] [--regime] [--force]` · `feed URL` · `fetch URL` · `parse FILE` · `scan [paths]` ·
`extract FILE [--force-deep]` · `score [--idea]` · `reground [--source]` · `factcheck IDEA` · `package IDEA` /
`package --schema PATH` · `queue --submit ID | --submit-ready | --list` · `notes [--dir] [--campaign] [--idea]` ·
`web [--host] [--port]` · `queries [--asset] [--regime] [--expand]` · `db init|info` · `config` · `status`.
Every DB command accepts `--db PATH`.

---

## 14. Development workflow (for the next AI)
- Cloud container clone: `/home/user/quant-trading_web_claude` (Linux). venv `.venv`; run `.venv/bin/python -m pytest -q`
  and `ruff check .` (line length 120). Use `set -o pipefail` when piping pytest output (a pipe once hid failures).
- **Never `pkill -f <pattern>`** that matches your own shell command (it killed the shell twice).
- The cloud environment **cannot reach most research hosts** (arxiv, doi.org …) — everything is tested with mocks;
  live behaviour is only observable on the user's PC. Ask for screenshots/pasted output; build diagnostics
  (`factcheck`) when needed.
- Branch `claude/loving-noether-1m40os`; after a merge, reset it to `origin/main` before new work. Open a PR per change,
  wait for CI (2 runs: push + PR), merge only on "merge it", then tell the user to `git pull`.
- Keep `.bat` files CRLF (`.gitattributes`). Regenerate the package schema when the package model changes. Bump prompt
  versions when prompt text changes. Add migrations, never drop columns.
- Tests to copy patterns from: `tests/test_campaign.py` (mock OpenAlex + PDF + scripted AI end to end),
  `tests/test_youtube.py` (Google API mocks), `tests/test_ai.py` (grounding), `tests/test_sources_and_notes.py`.

---

## 15. Live run history (user's PC) and lessons
1. **Campaign 1** "crash-protection ETF": 85 sources, 3 papers read, 3 ideas (all from one retirement-portfolio paper),
   $0.085. Revealed: grounding too strict → **every value dropped** because pdfplumber lost spaces (fixed: PDF v2 +
   space-insensitive matching; removed values 7/9/5 → 1/1/1, coverage 55 % → 75 %).
2. **Campaign 2** "support breakout with volume and follow by retracement strategy in bull market", 20 papers, $0.31:
   1 off-topic idea. Causes fixed in PR #9/#10: 6/20 AI answers cut off (b3 prompt + retry), 18/24 searches generic
   (request-based query plan), supernova/geology "breakout" papers (finance filter), 8/20 paywalled (free-first
   ranking), unread papers stranded in old campaigns (re-assignment).
3. A rerun with the fixes was started by the user; result not yet reported at handoff.

Lesson: academic sources rarely contain retail technical-analysis setups with exact rules. YouTube (now available),
`sources.txt` and a future general web search are the better channels for those.

---

## 16. The user's own "Idea Extractor" (read-only reference, never edit)
At `D:\Quant Trading\trading-extractor` (Windows-only: msvcrt locks). `RunIdeaExtractor.bat` → choose DeepSeek or
Claude → `extractor.py --file sources.txt --vault "D:\Quant Trading\TradingVault"`; uses yt-dlp subtitles for
YouTube/TikTok/Facebook, PyMuPDF + Claude Vision OCR, ebooklib, trafilatura, writes Obsidian notes with a gap
analysis. **Adopted into QSD:** `sources.txt` format (`| n:N`, `| title`), channel expansion, maturity badge,
Obsidian notes. **Not adopted:** yt-dlp subtitles (YouTube terms), Vision OCR (cost; Phase 2). Known quirks in it
(reported to the user, not fixed): DeepSeek option still requires `ANTHROPIC_API_KEY`; existing-note collision still
cached as saved; stale paths in its `CLAUDE.md`.

---

## 17. Known issues and limitations
- No general web search (Brave/Tavily-type key needed) — Phase 2.
- YouTube: metadata only (title/description/links), not spoken content; playlists not supported yet.
- Scanned/image-only PDFs are not OCR'd (`NO_TEXT_LAYER`).
- Grounding proves a quote exists, not that it means what the model says (e.g. a regime label); b3 prompt narrows
  this; a second-opinion review (Claude) is planned.
- Paywalled publishers are common for TA topics; only public parts are read.
- DeepSeek price in `local.yaml` is a conservative guess; confirm on the pricing page.
- `.env` is not loaded (keys must be real environment variables).
- Request parsing is keyword based; check the "I understood" lines.
- Dashboard tables overflow on phone-width screens (desktop is the target).

---

## 18. Backlog / next steps (suggested order)
1. **Offered, pending user answer:** YouTube playlist links (`playlistItems`, same as channels) and pasting a YouTube
   link (video/channel/playlist) directly at the `research.bat` prompt.
2. Review the user's rerun of the breakout request and tune selection/prompt if needed.
3. Local video/audio + podcasts via local Whisper transcription with timestamps (user's own files; podcast RSS
   enclosures) — Phase 2.
4. General web search connector (needs a key), with the same polite fetcher and finance filter.
5. Second-opinion review (Claude) for high-priority ideas before handoff (§89).
6. Reference tracing (cited DOIs/arXiv ids → originals), source-performance learning, watchlists/scheduled refresh,
   GitHub code handler with licence tracking, Vision OCR for scanned PDFs — see `TODO.md`.
7. Quant Auto OS integration beyond the file queue.

---

## 19. Decision log (short; full table in `DECISIONS.md`)
D1 Python 3.11+ src layout · D2 SQLite first · D3 FastAPI server-rendered dashboard · D4 provider adapters, DeepSeek
default · D5 free official APIs first · D6 no AGPL parsers · D7 robots not configurable · D8 cross-platform · D9 Phase 1
= M0–M9 · D10 market-regime dimension separate from long/short · D11 BeautifulSoup + safe-zip EPUB · D12 file dates ≠
publication dates · D13 official-API allowlist · D14 identity DOI > arXiv > URL · D15 Anthropic SDK optional · D16 no
price → no call · D17 deterministic grounding · D18 strong = deepseek-chat · D19 UNSCORED + coverage · D20 additive
migrations · D21 file-based backtest queue · D22 relation links only when grounded · D23 one shared campaign budget ·
D24 tolerant quote matching · D25 NEEDS_REVIEW = unreliable extraction · D26 space-insensitive matching + PDF re-read ·
D27 YouTube official API metadata only · D28 adopt Idea Extractor ideas except yt-dlp/OCR.

---

## 20. Glossary
**Campaign** — one research run (request or `sources.txt`) with its own budget and state. **Source** — any document
or video found or listed. **Idea** — one extracted strategy hypothesis. **Grounding** — checking AI values against
source quotes/numbers. **Coverage** — share of idea-quality weight that could be assessed. **Maturity** — how
completely the rules are written down. **Market direction / regime** — Long (uptrend), Short (downtrend),
Consolidation (sideways), Crash (crisis); separate from a strategy's long/short position direction. **Deepen** —
follow-up searches for replication, criticism and recent evidence. **Quant Auto OS** — the user's separate
backtesting system that consumes the queue.
