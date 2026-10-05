# HANDOFF — Quant Strategy Discovery & Source Intelligence (QSD)

> Complete orientation for another AI model or harness (written for a DeepSeek-based harness taking over from Claude
> Code) and for any developer. Read **§0 first** (where things stand and what to do next), then the rest as reference.
> Also read `CLAUDE.md` (working rules), `DECISIONS.md` (why, D1–D49), `KNOWN_ISSUES.md`, `CHANGELOG.md` (what changed
> when, newest first) and `docs/GATE_CALIBRATION.md` (the score arithmetic and the 2026-10-04 re-measurement).
> Section numbers like "§84" refer to the requirements in `docs/MASTER_SPEC.md`.
>
> **Updated 2026-10-05** · `main` at the merge of PR #17 (`12c6390`) · **265 tests passing**, `ruff check .` clean ·
> DB schema **v5** · package v1.2 · prompts **a1 / b5 / c1** · quality gate **PROMISING = 70** (D48).
> Older text in this file that disagrees with `DECISIONS.md` or `CHANGELOG.md` is out of date; those two win.

---

## 0. Where things stand and what to do next

### 0.1 One-paragraph state
The pipeline is built and works end to end on real data: discover → select → read → AI-extract → ground → score →
deepen → package → backtest queue, run interactively (`research.bat`), unattended (`harvest.bat`, daily scheduled task)
or from a hand-made list (`sources.txt`). On 2026-10-04 the system was measured against the user's live database
(31 ideas from 14 sources) and four **measurement defects** were found and fixed (§0.4). **11 strategies are in the
backtest queue** (`data/backtest_queue/pending/`), a 12th (idea 10) is eligible and waiting for
`qsd queue --submit-ready`. **No strategy has been backtested yet** — the user's separate backtester ("Quant Auto OS")
has not reported results, so *whether the scoring actually picks good strategies is still unproven.* That is the
single most important open question (§0.3, item 1).

### 0.2 Live database snapshot (user's PC, as last observed 2026-10-04)
| Group | Ideas |
|---|---|
| **In the backtest queue (SUBMITTED_TO_BACKTEST)** | 1, 5, 6, 7, 8, 9, 20, 21, 22, 27, 28 |
| Eligible, not yet queued | 10 (normalized 70.8, completeness 70; its source has promotional claims; its entry rule was removed by grounding) |
| RESEARCHING, below the gate | 4, 11, 12, 13, 23, 30 (scores 48–63 normalized; 12/13 lack an instrument and have completeness 50) |
| ARCHIVED / REJECTED | 2, 3, 15, 29, 31 archived; 14, 16–19, 24–26 rejected (`RULES_NOT_QUANTIFIABLE`: old extractions of algorithm-type papers, see D38) |

Best by normalized quality: **#20 78.1** (mean-reversion pairs; reports an out-of-sample test), **#27 74.5**
(altcoin–Bitcoin arbitrage), **#28 71.6** (deep hedging with options; out-of-sample 2021–2023, costs, code on GitHub).
Ideas 5–9 are **one regime-switching system** from one paper (#9 switches between the regime strategies #5–#8): they
are parts of a single strategy and must not be backtested as five independent candidates. Two of their extracted exit
rules look inverted (#5 "RSI crosses below 70", #8 "price crosses above the upper band") and must be checked against
the paper before coding. Ideas 11–15 all come from source 511 (old prompt split one paper into five).

### 0.3 Open issues, in priority order
1. **No ground truth.** Nothing in the scores has been compared with a backtest result. Plan: the user backtests the
   queue (suggested order 20, 28, 27, 9 with 5–8 as its parts, then 21, 22, 1; 10 last), reports results, and the
   score weights/gate are re-examined against them. A feedback path (backtest result → stored on the idea → used to
   calibrate) does not exist yet and is the next design task. Do not tune the gate or weights without it unless a
   new measurement justifies it (the user's explicit instruction: *do not lower the gate again without new measurement*).
2. **Two known scoring quirks (measured, deliberately not changed):**
   - *Stating costs is penalised relative to silence*: `edge_vs_cost` is 0.6 (the cap) when the source addresses
     costs and **unassessed** when it is silent, so a paper that discusses costs scores lower than one that doesn't
     (64.3 vs 64.9 on the same idea). The same shape exists for `liquidity` and `diversification`.
   - `parameter_simplicity` counts words/thresholds in the rule text and double-counts thresholds, so it punishes
     well-specified rules (ideas 5–9 score 0.0–0.08). Counting *tunable parameters* would be more faithful.
   Neither changes which ideas clear 70 (checked), so they are quality-of-measurement work, not blockers.
3. **A quote that stitches two sentences is rejected** (idea 10: the AI quoted "...closed beneath the level. That is
   confirmation..." and the paper has a sentence in between). The rejection is correct under "a quote is a continuous
   stretch of text", but a design question remains: allow an ordered subsequence with a small, explicitly logged gap?
   Not decided.
4. **Duplicate/variant handling inside one paper**: ideas that are parts of one system (5–8 under 9) or one paper
   split by an old prompt (11–15) are not linked. Consider a "system vs component" relation, so a harvest target of
   "N distinct ideas" cannot be met by components of one strategy. The fingerprint dedupe only catches identical rules.
5. **Evidence detector residue**: 24 of 30 live sentences were right at the first check; 6 wrong patterns were fixed
   (D45). A signal *definition* ("this definition is 100% out-of-sample") still counts as an out-of-sample test.
   Re-check precision on the next ~30 ideas by reading the `EVIDENCE:*` facts (query in §0.6).
6. **Completeness credits a study horizon as an exit** (`holding_period` "10-year investment period" on a passive
   allocation, idea 1). Score impact is small; left alone (KNOWN_ISSUES).
7. **No general web/blog/news connector** — needs a Brave or Tavily key (not configured). Academic papers rarely contain
   retail technical-analysis setups; YouTube transcripts, `sources.txt` and a web connector are the better channels.
8. **Unobserved live:** a harvest crossing a UTC day boundary (budget sleep/resume); the Windows Scheduled Task.
9. **Offered to the user, not built:** YouTube playlist links (same `playlistItems` call as channels) and pasting a
   YouTube link directly at the `research.bat` prompt.
10. **Phase 2 backlog** (see `TODO.md`, `docs/ROADMAP_AUTONOMY.md`): second-opinion review by a stronger model before
    handoff (§89); reference tracing; local Whisper transcription for the user's own audio/video; Vision OCR for scanned
    PDFs; source-performance learning; Postgres option.

### 0.4 What the 2026-10-04 measurement found and fixed (read this to understand the method)
The user asked to "solve the issues facing now" from another agent's brief. The brief's own numbers were partly wrong
(it believed `normalized ≥ 55` and coverage blocked PROMISING ideas, and treated a raw score (56.5 = normalized ×
coverage) as the normalized one). The work was done
**measure first, change second**, using only free commands on the live database:

| Defect found | Evidence | Fix | Result |
|---|---|---|---|
| Harvest never submitted to the queue | 11 campaigns, queue empty | D44: `harvest.submit_eligible` after each round; `qsd queue --why` | the queue now fills (11 queued by `qsd queue --submit-ready`) |
| Robustness blind spot | `expected_robustness` = 0.00 on 14 of 15 ideas; only abstract + summary + AI quotes were searched | D42: evidence sentences from the **whole document**, own-work only, references excluded, stored as `EVIDENCE:*` facts | idea 20: 47 → 57 raw (78.1 normalized) |
| Two-column PDFs garble quotes | MM-ARC paper: 13 of 19 values removed (pdfplumber merged tokens across the gutter, 242–504 pt wide on a 612 pt page) | D41: second reading with pdfminer layout analysis (`alt_blocks`) used for **grounding only** | MM-ARC 13 → 2 removed; source 767 12 → 3; 727 11 → 7 |
| Grounding loophole | test: "six month" aligned with "twelve month" and the value "6-month" passed via an unrelated "6" | D43: approximately matched quote must contain the value's numbers | closed |
| Evidence precision | hand check of 30 live sentences: 6 wrong (negations, future work, "Crown Castle International Corp.") | D45 | pinned as a regression test with the real sentences |
| Data availability unassessed for commodities / volatility / multi-asset / fixed income | idea 1 (completeness 100) blocked only by this | D46 | ideas 1, 21, 22 queued |
| Gate at 60 was compensating for the above | no idea scored between 63.7 and 70.8 once measured honestly | D48: gate back to **70** (supersedes D37) | 4 ideas above, none in the 61–64 cluster without robustness evidence |
| False injection flag held a good idea in NEEDS_REVIEW | idea 10: red flag `PROMPT_INJECTION_TEXT_PRESENT` from a trader saying "place a trade" | D49 + `reground`/`reextract` recompute the flag from the document | idea 10 → PROMISING, eligible |
| No way to re-ask the AI for a source already read | `qsd extract` takes local files and would duplicate ideas | D47: `qsd reextract --idea/--source [--yes]`, in place | cost $0.025 for source 511 |

Two honest corrections made during the work (keep this habit): an early claim that idea 10's one removed value was
the "claimed win rate" was a guess and wrong (it was the entry rule); and a `git pull` that fetched only a feature
branch looked like "already up to date" while `main` had not been fetched.

### 0.5 Working method that has paid off
1. **Measure on the live database before changing anything** (`qsd reground`, `qsd score`, `qsd queue --why`,
   `qsd factcheck IDEA` — all cost $0; `reextract` costs a few cents and is a dry run without `--yes`).
2. **Hand-check a sample** (e.g. 30 evidence sentences, the removed values of one idea) and turn the real sentences into
   a regression test.
3. **Change one thing per pull request**, with a decision (D-number), changelog line and test; user says "merge it".
4. **Never change the gate/weights to make results look better.** Fix the measurement; re-measure; then decide.
5. **Say what was inferred versus observed.** The cloud environment cannot reach arxiv.org/doi.org, so live behaviour is
   only visible through output the user pastes or files they upload.

### 0.6 Handy read-only commands (user's Windows PC, in the activated venv)
```
.venv\Scripts\activate                      (every new Command Prompt window; otherwise "qsd is not recognized")
qsd queue --why                             per-idea eligibility with blocking reasons + tally
qsd reground                                re-check cached AI answers against cached documents ($0), then re-score
qsd score                                   re-score all ideas ($0)
qsd factcheck 10                            why an idea's values were removed (closest passage per quote)
qsd reextract --idea 12                     dry run: prompt version stored vs current, last cost; add --yes to spend
qsd queue --submit-ready                    send every eligible idea to data\backtest_queue\pending\
set PYTHONIOENCODING=utf-8                  before redirecting output that contains non-ASCII characters
python -c "import sqlite3; from qsd.config import load_settings; s=load_settings(); c=sqlite3.connect(s.resolve_path(s.paths.data_dir)/'qsd.sqlite'); [print(*r, sep=' | ') for r in c.execute('select source_id, fact_type, location, quote from source_facts where fact_type like ? order by source_id', ('EVIDENCE%',))]" > evidence.txt
```
Redirecting with `>` hides errors on screen — they land in the file; read the file if it looks empty.
Opening `data/qsd.sqlite` by *copying the file* can miss recent commits held in the WAL; open it read-only instead.

### 0.7 Instructions for the harness taking over
- You are continuing, not restarting. The code, tests and docs are the source of truth; `git log --oneline -20` and
  `DECISIONS.md` tell you what was decided and why. Do not re-derive established facts.
- Run `pip install -e ".[dev]"`, `pytest -q` (expect 265 passed) and `ruff check .` before and after every change.
- The user approves every merge to `main` ("merge it"). If your harness cannot open pull requests, commit to a feature
  branch, tell the user the branch name and what to review, and **never push to `main` on your own**.
- Respect the non-negotiables in §2 — especially: no fabrication (unverifiable values become `UNKNOWN`), claimed
  performance never affects ranking, fetched content is data not instructions, secrets only from environment
  variables, the system never trades.
- Model-specific note: prompts in `src/qsd/ai/prompts.py` are versioned and part of the AI cache key. Changing prompt
  text **requires** bumping its version (b5 → b6); otherwise cached answers will be returned and the change will look
  like it did nothing. The AI layer talks to DeepSeek via an OpenAI-compatible JSON-mode provider; thinking mode is
  disabled because thinking tokens were consuming the output budget and truncating the JSON (see CHANGELOG).
- Suggested first tasks, in order: (1) wait for / ask for the user's backtest results and design the feedback path
  (§0.3 item 1); (2) decide the stitched-quote question (item 3) with a measurement on idea 10's source; (3) fix the two
  scoring quirks (item 2) one at a time with before/after tables; (4) system-vs-component relation (item 4);
  (5) web search connector once the user provides a Brave/Tavily key; (6) YouTube playlist support.

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
`D:\Quant-Trading_web_Claude`. Built with Claude Code in a cloud container (Linux), in pull requests #1–#17.

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
- **Do not lower the PROMISING gate again without a new measurement** (it was lowered to 60 in D37 and restored to
  70 in D48 after the measurement defects were fixed).
- **Never ask the user to paste API keys.** They live in Windows user environment variables.
- **YouTube: the official Data API for search/metadata; no video download.** Captions via yt-dlp (`--skip-download`,
  subtitles only) were added in D29 at the user's explicit approval, behind `discovery.youtube_transcripts` (default off;
  the user's own runs have it on for transcripts such as source 504).

---

## 3. Status at handoff

| Area | State |
|---|---|
| Phase 1 milestones M0–M9 | Done |
| Post-launch (PRs #2–#17) | Grounding fixes, `reground`/`factcheck`, PDF spacing + two-column fixes, dashboard + port 8877, guided research + live monitor, YouTube (metadata, channels, captions), `sources.txt`, maturity badge, Obsidian notes, continuous harvest + self-directed search, GitHub code discovery (off), algorithm-shaped strategies (schema v5, prompt b5), whole-document evidence, queue visibility, `reextract`, gate restored to 70 |
| Tests | **265** (`pytest`), all network/AI mocked; `ruff check .` clean; GitHub Actions CI (`.github/workflows/ci.yml`) |
| Live usage | 14 sources read, 31 ideas, 11 queued for backtest; see §0.2 and §15 |
| Not validated | No backtest results exist yet (§0.3 item 1) |

---

## 4. The user's environment (Windows)

| Item | Value / note |
|---|---|
| Project folder | `D:\Quant-Trading_web_Claude` (git clone; `git pull` to update) |
| Python | venv at `.venv` (`.venv\Scripts\activate`, `.venv\Scripts\qsd.exe`). User's base Python is 3.14 (`C:\Python314`) |
| Install | `pip install -e ".[dev]"` (editable: `git pull` updates code without reinstall unless dependencies change) |
| AI key | `DEEPSEEK_API_KEY` as a **Windows user environment variable** (`setx`). `.env` is **not** loaded |
| YouTube key | `YOUTUBE_API_KEY` set with `setx` (Google Cloud project "OpenClaw Assistant", key restricted to YouTube Data API v3) |
| Local config | `config\local.yaml` (git-ignored). Models and prices now come from `config/default.yaml`: cheap `deepseek-flash` (0.30 / 1.20 USD per Mtok), strong `deepseek-v4-pro` (1.32 / 3.96); keep them in step with the provider's pricing page |
| Dashboard | http://127.0.0.1:8877/ (port 8877 because another local app, "AQS Strategy Monitor", uses 8801) |
| Launchers | `research.bat` (guided), `harvest.bat` (unattended rounds), `dashboard.bat`, `Install-HarvestSchedule.ps1` (daily Windows Scheduled Task, not yet observed live). `auto_idea_finder.bat` in the folder is **not ours** |
| Command Prompt | `qsd` is only found inside the venv: run `.venv\Scripts\activate` in every new window. The `.bat` files call `.venv\Scripts\qsd.exe` directly |
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
| `CLAUDE.md` | Rules for AI coding sessions; `PROJECT_STATE.json`, `CHECKPOINT.md`, `TODO.md`, `KNOWN_ISSUES.md`, `DECISIONS.md` (D1–D49), `CHANGELOG.md`, `ARCHITECTURE.md`, `README.md` |
| `src/qsd/cli.py` | `qsd` command (argparse). All subcommands, see §13 |
| `src/qsd/config.py` | Pydantic settings (`Settings`, `AIConfig`, `Budgets`, `Crawling`, `Discovery`, `Scoring`, `Handoff`, `Export`); `get_secret()` reads env only; `REPO_ROOT` |
| `src/qsd/taxonomy.py` | Enums: `MarketRegime`, `RegimeSuitability`, `RegimeBasis`, `AssetClass`, `AccessStatus`, `IdeaStatus`, `RejectionReason`, `SourceRelation`, `IdeaSourceRole`, `CampaignStatus`, `ExtractionMethod`, `MissingValue`/`UNKNOWN`, `REGIME_LABELS` ("Long (uptrend)", "Short (downtrend)", "Consolidation (sideways)", "Crash (crisis)") |
| `src/qsd/db/` | SQLAlchemy 2 models (`models.py`, `SCHEMA_VERSION = 4`), `make_engine` (SQLite WAL + busy_timeout), `init_db` with additive `MIGRATIONS`, `new_idea()` (creates 4 regime rows) |
| `src/qsd/handlers/` | Format detection + parsers; `HandlerResult` (blocks with `Location`, tables, links, metadata, limitations, sha256 of bytes); PDF v3: re-reads glued-word pages and adds a pdfminer layout reading (`alt_blocks`, grounding only); safe ZIP limits |
| `src/qsd/fetch/` | `PoliteFetcher` (robots RFC 9309, per-host rate limiter, backoff/Retry-After, cooldown, size cap, total-download deadline, no cookies, ETag cache, `extra_headers` never cached), `OFFICIAL_API_ENDPOINTS` allowlist, URL canonicalisation, access-barrier detection, `fetch_and_store` |
| `src/qsd/discovery/` | Connectors (`ArxivConnector`, `OpenAlexConnector`, `CrossrefConnector`, `YouTubeConnector`, `FeedConnector`), `build_connectors()`, `run_discovery()` (search memory, budgets, `on_search` progress, video links), query families, tiering |
| `src/qsd/ai/` | Providers (DeepSeek/OpenAI-compatible JSON mode; Anthropic SDK optional), `AIGateway` (cache, ledger, budgets), prompts (versioned), schemas, `sections.select_relevant`, grounding, `extract_ideas`, `reground_source` |
| `src/qsd/scoring/` | Rules (red flags, hard fails, completeness, maturity, complexity), scores (source/evidence/idea quality, priority, gate band), dedupe/fingerprints/root evidence, `evidence.py` (whole-document evidence sentences, D42/D45), `score_idea` status pipeline |
| `src/qsd/packaging/` | `ResearchPackage` (v1.2), `build_package`, `submit_to_queue` (handoff rule), schema export |
| `src/qsd/campaign/` | `parse.py` (request → spec), `runner.py` (`CampaignRunner`, `plan_queries`, progress `_note`, video/local reading, deepen), `seed.py` (`sources.txt` seeding), `harvest.py` (`run_harvest`: rounds until N distinct PROMISING ideas, budget-day sleep, `submit_eligible`), `directions.py` (self-directed follow-up queries from the campaign's own extracted ideas) |
| `src/qsd/research.py` | Guided wizard (`run_wizard`), in-process dashboard start, cost estimate, summary, auto queue + notes export |
| `src/qsd/sources_file.py` | `sources.txt` parser (`SourceItem`: youtube_video / youtube_channel / url / path, `| n:25`, `| title`) |
| `src/qsd/export.py` | Markdown/Obsidian notes (escaped; only overwrites files with its own `qsd_id`) |
| `src/qsd/web/` | FastAPI + inline Jinja2 templates (autoescape, no JS): `/live`, `/`, `/ideas`, `/ideas/{id}`, `/sources`, `/ai-cost`, `/errors`, `/healthz` |
| `src/qsd/security.py` | Injection detection, `wrap_untrusted` (content-hash nonce) |
| `src/qsd/logging_setup.py` | JSON logs + `redact()` (bearer tokens, key=value secrets, `sk-…`, `AKIA…`, `AIza…`) |
| `src/qsd/localscan.py`, `state.py` | Read-only folder index; build-milestone state |
| `tests/` | pytest suite; `fixtures.py` generates PDFs/DOCX/PPTX/XLSX/EPUB/ZIP; HTTP via `httpx.MockTransport`; AI via fake providers |

---

## 7. Data model (SQLite, `data/qsd.sqlite`, schema v5)

| Table | Key contents |
|---|---|
| `schema_meta` | `schema_version` |
| `campaigns` | request_text, mode (`QUICK_DISCOVERY`, `SOURCES_FILE`, …), asset classes, families, target regimes, `spec` JSON, `state` JSON (`phases_done`, `processed_sources`, `deepened_ideas`, `spent`, `progress` = counters + last 80 events for the live monitor), status (`PLANNED/RUNNING/COMPLETED/STOPPED`), stop_reason |
| `sources` | title, author, organization, url, canonical_url (unique), local_path, publication_date, tier (1–5/NULL), format, content_hash (sha256 of bytes), access_status, quality scores, root_evidence_id, campaign_id |
| `source_links` | from → to, relation (`CITES` = video → linked paper/code, …) |
| `fetch_log` | every retrieval (redacted): request_url, status, retrieval_method (`HTTP`, `CACHE`, `API_METADATA`, `LOCAL_FILE`), error |
| `source_facts` | provenance: fact_type (`ABSTRACT`, `PDF_URL`, `ID_DOI`, `ID_YOUTUBE`, `TITLE_OVERRIDE`, `TRIAGE`, `EVIDENCE:<signal>` (whole-document evidence sentences with page), rule names, `PARAMETER:x`, `REGIME:x`, …), value, quote (≤300), page/slide/location, method, confidence |
| `ideas` | strategy fields (instrument, universe, timeframe, signal, entry/exit/stop/take-profit rules, sizing, rebalance, holding period, costs, …), parameters JSON, unknown_rules, claimed_* (verbatim), economic_rationale, red_flags, scores (source/evidence/idea quality, completeness, complexity, replication, novelty, priority), status, hard_fail_reasons, fingerprint/dedupe, `strategy_kind` + `algorithm_rule` (v5: BAR_RULE / ALGORITHM / PORTFOLIO_WEIGHT / MACHINE_LEARNING), `score_details` JSON (v2; includes `evidence_sentences`), `grounding` JSON (v4: removed values with the AI's quote + reason, values offered, realigned) |
| `idea_regimes` | 4 rows per idea (BULLISH/BEARISH/CONSOLIDATION/CRASH): suitability, basis, confidence, source_fact_id |
| `idea_sources` | idea ↔ source role (`DESCRIBES`, `SUPPORTS`, `REPLICATES`, `CONTRADICTS`, `ORIGINAL`) + note |
| `idea_status_history`, `rejections` | audit trail; rejections never deleted |
| `search_queries` | search memory: connector, query hash, purpose, results/useful counts |
| `ai_calls` | ledger: task, prompt version, model, tokens, cost, cache hit, success, cache_key |
| `ai_cache` | validated AI responses by cache key (enables free `reground`) |
| `errors`, `local_files` | error records (redacted); local scan index |

Migrations are **additive only** (`db/__init__.py: MIGRATIONS`): v1→2 `ideas.score_details`, v2→3 `campaigns.spec/state`,
v3→4 `ideas.grounding`, v4→5 `ideas.strategy_kind` / `ideas.algorithm_rule`. `init_db` runs them automatically on every command.

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
- PDF: pdfplumber per page; pages with "glued" words (≥25-letter tokens) are re-read with `x_tolerance` 1.5 / 1.0. Every page also gets a pdfminer layout reading (`HandlerResult.alt_blocks`, ≤80 pages, only when it differs) that grounding may use but the AI never sees (D41).
- YouTube transcripts (D29, off by default): captions text with `[t=hh:mm:ss]` markers, so quotes may span timestamp markers.
- `sections.select_relevant`: keyword-scored blocks with `[p.N]` markers, ≤ `stage_b_max_chars` (40k).

### 8.5 AI extraction (`ai/extract.py`, `ai/prompts.py`)
- **Stage A** (cheap model, ≤6k chars, prompt a1): is it strategy research, worth a deep read?
- **Stage B** (strong model, prompt **b5**): at most 3 strategies; only stated fields (UNKNOWN omitted); each value
  with a verbatim `evidence_quote` (<30 words) + location; regimes SUITED only if the source reports favourable
  returns there. If the answer is invalid/cut off → **one retry asking for 1 strategy** (`STAGE_B_RETRIED_SHORT`).
- **Stage C** (cheap, prompt c1): relation of a found paper's abstract to an idea (REPLICATES/SUPPORTS/CONTRADICTS/
  UNRELATED/UNCLEAR); recorded only if the quote is in the abstract and confidence ≥ 0.6.
- Prompt b5 also asks for `strategy_kind` and `algorithm_rule`, and tells the model to leave `entry_rule`/`exit_rule` UNKNOWN for an algorithm rather than invent bar conditions. b5 is stricter than b4: on source 511 it returned 1 strategy where b4 returned 5.
- Models: DeepSeek `deepseek-flash` (stage A, C) and `deepseek-v4-pro` (stage B); Claude optional (`anthropic` extra).
- `qsd reextract` (D47) re-asks stage B for chosen sources and updates their ideas in place (matched by strategy name).

### 8.6 Grounding (`ai/grounding.py`) — the anti-fabrication core
For every non-UNKNOWN value:
1. The quote must occur in the source text: exact after normalisation (case, whitespace, typographic quotes/dashes,
   PDF hyphenation), **or** ignoring all whitespace for quotes ≥12 chars (PDFs that lose spaces), **or** word
   alignment for quotes ≥6 words (≥85 % of words in order in one short stretch, numbers exact, and the differing words
   must not be words the value depends on). Realigned quotes are replaced by the source's own wording.
2. Every number in the value must occur in the source; a claim's number must be in its own quote; an approximately matched (aligned) quote must itself contain the value's numbers (D43). Quotes may be found in either the primary text or the pdfminer layout reading of the pages the AI saw (D41).
3. Regime "SOURCE_STATED/EVIDENCE" without a found quote → downgraded to `RATIONALE_INFERRED`, confidence ≤ 0.5.
4. Failures → value set to UNKNOWN and recorded in `ideas.grounding.removed` (value, AI quote, location, reason).
5. **NEEDS_REVIEW** only if ≥50 % of ≥4 offered values failed (`UNRELIABLE_EXTRACTION`) or the document contains
   prompt-injection text. The injection detector no longer flags bare "place a trade" trading talk (D49); `reground` and
   `reextract` recompute the flag from the document rather than carrying a stored one.

Tools: `qsd reground` re-applies current grounding to cached AI answers (no AI cost; document re-read from file /
HTTP cache, refused if changed). `qsd factcheck <idea>` prints, per removed value, the closest source passage and the
share of words found (diagnosed the PDF-spacing bug).

### 8.7 Scoring (`scoring/`)
- Red flags (regex: guaranteed profit, 9x % win rate, martingale, …) and **hard fails** (martingale, unbounded
  averaging down, ≥2 scam flags, …) → `REJECTED`.
- Formalization completeness (rules present) → **maturity**: <35 CONCEPT, ≥35 PARTIAL, ≥60 TRADEABLE, ≥80 BACKTEST-READY.
- Evidence signals (out-of-sample, costs, code, sample period, cross-market) come from the **whole document**: only sentences where the source speaks of its own work ("we", "our", "this paper"), outside the reference list, not negated or deferred ("no", "not", "avoid", "future research") — stored verbatim as `EVIDENCE:*` facts (D42, D45). `data_availability`: 0.7 for stocks/ETF/forex/crypto/futures/commodities/volatility/multi-asset, 0.6 fixed income, 0.4 options, unassessed for intraday or unknown (D46).
- Source quality: tier base (1:75, 2:62, 3:42, 4:22, 5:5; untiered 30) + identifier/author/date/transparency − scam/
  injection penalties. Evidence quality: sample period, method, out-of-sample, costs, code, replication, cross-market.
- Idea quality: 14 weighted components (sum 100, `scoring.idea_weights`); unassessable ones are UNSCORED; **coverage** =
  assessed weight share; normalized = score over assessed weight.
- Gate (normalized): ≥85 HIGH_PRIORITY, **≥70 PROMISING (D48; was 60 under D37)**, ≥55 RESEARCH_FURTHER, else ARCHIVE. Because several components cap below 1.0 (`edge_vs_cost` 0.6, `data_availability` 0.7, `exit_executability` 0.7, `liquidity` 0.8), a perfect idea does not reach 100; see `docs/GATE_CALIBRATION.md`.
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
`qsd queue --submit-ready` (also automatic at the end of `research.bat` and, since D44, after every harvest round) writes `data/backtest_queue/pending/*.json`. `qsd queue --why` prints each PROMISING/RESEARCHING idea's blocking reasons with a tally.

### 8.9 Campaign loop (`CampaignRunner.run`)
discover (once; `phases_done`) → read `docs_per_round` selected sources → score all → deepen top `deepen` (5)
PROMISING/RESEARCHING ideas → stop with `ROUND_COMPLETE` / `TARGET_REACHED` (≥3 distinct promising families) /
`NO_NEW_SOURCES` / `BUDGET:…` / `CONFIG:…`. Ctrl+C → `STOPPED` with a resume hint. Everything saved in
`campaigns.state`, so `qsd campaign --resume ID` continues without repeating paid work (AI cache + search memory).

### 8.9b Harvest loop (`campaign/harvest.py`, `qsd harvest`, `harvest.bat`)
Rounds of the campaign loop until `target` **distinct** ideas are PROMISING (measured after scoring and dedupe, D31), `max_rounds`, `DOCS_TOTAL`, `NO_PROGRESS` (consecutive empty rounds), `STALLED`, or a hard error (`CONFIG:`). A spent daily AI budget **sleeps until the next UTC day** and continues; a spent per-round search allowance is not a stop (D40). Later rounds issue follow-up queries derived from the campaign's own extracted families, asset classes and the gate's measured gaps (`directions.py`, D39). After each round eligible ideas are submitted to the queue (D44). Unattended use: `Install-HarvestSchedule.ps1` registers a daily Windows task (D34). Observed yield: roughly one passing idea per 3–5 documents read.

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
- Observed costs: about $0.03–0.05 per paper read (stage A+B). Source 511: stage B with b4 cost $0.0506, a re-extraction
  with b5 cost $0.025. A 3-round harvest cost $0.35. Caps: $1/day, $20/month, $2/campaign (`config/default.yaml`).
- Everything in §0.6 marked $0 makes no AI call.

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
(85 / **70** / 55) · `discovery` (contact_email, results_per_query 10, search_memory_days 30, youtube_enabled,
youtube_results_per_query 10, youtube_max_links_per_video 5) · `scoring` (idea_weights, min_coverage_for_gate 0.6) ·
`handoff` (60 / 55 / 0.6 / 0.4, queue_dir) · `export` (notes_dir, notes_subfolder "QSD Strategies").
Secrets (env only): `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `YOUTUBE_API_KEY`.

---

## 13. CLI reference (`qsd …`)
`research` (guided; `--port`) · `harvest "request" [--target N] [--max-rounds N] [--docs N]` · `reextract [--source ID] [--idea ID] [--yes]` · `campaign "request" [--dry-run] [--docs N] [--max-queries N] [--deepen N]` /
`campaign --resume ID` · `sources [--file] [--dry-run]` · `discover [query…] [--connector arxiv,openalex,crossref,
youtube|all] [--asset] [--regime] [--force]` · `feed URL` · `fetch URL` · `parse FILE` · `scan [paths]` ·
`extract FILE [--force-deep]` · `score [--idea]` · `reground [--source]` · `factcheck IDEA` · `package IDEA` /
`package --schema PATH` · `queue --submit ID | --submit-ready | --list | --why` · `notes [--dir] [--campaign] [--idea]` ·
`web [--host] [--port]` · `queries [--asset] [--regime] [--expand]` · `db init|info` · `config` · `status`.
Every DB command accepts `--db PATH`.

---

## 14. Development workflow (for the next AI)
- Setup (any OS): `python -m venv .venv`, activate it, `pip install -e ".[dev]"`; run `pytest -q` (265 tests, no network, no
  AI keys needed) and `ruff check .` (line length 120). Use `set -o pipefail` when piping pytest output (a pipe once
  hid failures). Tests use `httpx.MockTransport` for HTTP and fake providers for AI.
- The previous harness worked in a Linux cloud container whose network policy **blocked arxiv.org, doi.org and most
  research hosts**: everything is tested with mocks; real behaviour is observable only on the user's PC, through
  output the user pastes or uploads. Build free read-only diagnostics (`factcheck`, `queue --why`) instead of guessing.
- **Never `pkill -f <pattern>`** that matches your own shell command (it killed the shell twice).
- After a failed `git fetch`, do not trust a remote-tracking ref (use `git ls-remote`). After a merge, reset the working
  branch to `origin/main` before new work. The user must `git checkout main && git pull` on Windows after each merge;
  a plain `git pull` while on a feature branch fetches only that branch (it once looked like "already up to date").
- One pull request per change, with: code, test, `DECISIONS.md` row (next free D-number), `CHANGELOG.md` entry, and any
  `KNOWN_ISSUES.md` update. Keep `.bat` files CRLF (`.gitattributes`). Regenerate the package schema when the package
  model changes. Bump prompt versions when prompt text changes. Add migrations, never drop columns.
- Tests to copy patterns from: `tests/test_campaign.py` (mock OpenAlex + PDF + scripted AI end to end),
  `tests/test_youtube.py`, `tests/test_ai.py` (grounding, reground, reextract), `tests/test_measurement_fixes.py`
  (two-column PDFs, whole-document evidence, queue visibility, data availability — each pins a *measured* failure),
  `tests/test_sources_and_notes.py`.
- Windows quirks: Command Prompt `>` hides errors in the file; non-ASCII output needs `set PYTHONIOENCODING=utf-8`; the
  venv must be activated; the console uses ASCII arrows and `errors="replace"`.

---

## 15. Live run history (user's PC) and lessons
1. **Campaign 1** "crash-protection ETF": 3 papers, 3 ideas (one retirement-portfolio paper), $0.085. Revealed grounding
   too strict (PDF spacing) → fixed (D26).
2. **Campaign 2** "support breakout with volume and follow by retracement…": 20 papers, 1 off-topic idea. Fixed in
   PR #9/#10: truncated AI answers, generic searches, off-topic finance filter, paywalled papers, stranded sources.
3. **Campaigns 3–11 (harvest, self-directed search, YouTube transcripts, GitHub)**: verified a 3-round harvest advances
   and passes ideas (≈ 1 passing idea per 3–5 documents), that free-first selection works, that GitHub works as
   discovery but not reading, and that algorithm-shaped papers were being hard-failed (fixed by schema v5, D38).
4. **2026-10-04 measurement and repair** (§0.4): $0 re-measurement on 31 ideas / 14 sources; eleven ideas queued; one
   re-extraction ($0.025). The ideas behind the best scores: #20 *Optimal Mean Reversion* (pairs, out-of-sample test),
   #27 *Altcoin–Bitcoin arbitrage*, #28 *Deep hedging with options*, #9 *Dynamically adaptive regime strategy*.

Lessons: (a) academic sources rarely contain retail technical-analysis setups — use YouTube transcripts / `sources.txt`;
(b) every "improvement" so far came from reading real output, not from theory; (c) a score that rises must be
explainable by quotes you can read (`EVIDENCE:*`, `factcheck`).

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
The ranked list of open problems is **§0.3**; the maintained list is `KNOWN_ISSUES.md`. Standing limitations:
no general web search (needs a key); YouTube spoken content is read only through captions and only when
`youtube_transcripts` is on; scanned PDFs are not OCR'd (`NO_TEXT_LAYER`); grounding proves a quote exists, not that it
means what the model says (a second-opinion review is planned); paywalled publishers are common for technical-analysis
topics; `.env` is not loaded (keys must be real environment variables); request parsing is keyword-based; dashboard
tables overflow on phone-width screens; GitHub search without a token is limited to 60 requests/hour.

---

## 18. Backlog / next steps
See **§0.3** (ranked) and **§0.7** (suggested first tasks). Longer term: `TODO.md`, `docs/ROADMAP_AUTONOMY.md`
(stages 2–6 of a self-running system: stage 1 self-directed search and stage 2 search-budget handling are done).

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

D29 captions via yt-dlp behind a flag (supersedes part of D27) · D30 work identity strips version suffix · D31 continuous
harvest, target measured after dedupe, sleeps to the budget reset · D32 gate/`parameter_simplicity` left alone after
measuring · D33 GitHub code channel (off, metadata only) · D34 unattended = daily scheduled task · D35 selection prefers
sources reporting robustness (tie-breaker) · D36 gate unchanged · D37 gate 70 → 60 · D38 algorithm-shaped strategies
(schema v5) · D39 self-directed follow-up queries · D40 spent search allowance is per-round · D41 pdfminer second reading
for grounding · D42 whole-document evidence · D43 aligned quote must contain the value's numbers · D44 harvest submits to
the queue, `queue --why` · D45 evidence precision (negations, future work) · D46 data availability for every bar-data
class · D47 `reextract` in place · **D48 gate back to 70 (supersedes D37)** · D49 injection detector no longer flags bare
"place a trade"; stale injection flags recomputed.

---

## 20. Glossary
**Campaign** — one research run (request or `sources.txt`) with its own budget and state. **Source** — any document
or video found or listed. **Idea** — one extracted strategy hypothesis. **Grounding** — checking AI values against
source quotes/numbers. **Coverage** — share of idea-quality weight that could be assessed. **Maturity** — how
completely the rules are written down. **Market direction / regime** — Long (uptrend), Short (downtrend),
Consolidation (sideways), Crash (crisis); separate from a strategy's long/short position direction. **Deepen** —
follow-up searches for replication, criticism and recent evidence. **Quant Auto OS** — the user's separate
backtesting system that consumes the queue.
