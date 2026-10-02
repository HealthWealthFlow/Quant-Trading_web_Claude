# Changelog

## Algorithm-shaped strategies are no longer discarded (schema v5, D38) (2026-10-02)
The gap that silently threw away a whole class of academic quant research.
- **The problem.** PAMR (*Passive Aggressive Mean Reversion*) states its decision as a portfolio-weight update
  equation. It extracted with `signal`/`entry_rule`/`exit_rule` = UNKNOWN — correctly, since the paper never states a
  bar-rule — reached 10% completeness, and was **hard-failed** as `RULES_NOT_QUANTIFIABLE`. Four fully specified ideas
  discarded. The anti-fabrication rule was doing its job; the schema simply had nowhere to put an algorithm.
- **Fix:** `ideas.strategy_kind` (`BAR_RULE` / `ALGORITHM` / `PORTFOLIO_WEIGHT` / `MACHINE_LEARNING`) and
  `ideas.algorithm_rule` (the stated decision mechanism, verbatim-grounded), added as an **additive migration
  v4 → v5**. `RULES_NOT_QUANTIFIABLE` now fires only when there is **no rule of any kind** — a genuinely empty
  extraction is still rejected, so the rule did not become a loophole.
- **Prompt b5** asks for both fields, with explicit instruction to leave `entry_rule`/`exit_rule` UNKNOWN for an
  algorithm rather than inventing bar conditions, and that a fully stated algorithm is a quantifiable rule.
- `algorithm_rule` is now part of the rule text the scorer scans, so look-ahead / martingale / survivor checks still
  see an algorithm's wording.
- **Verified on the live database:** `qsd db init` migrated v4 → v5 with **all 30 existing ideas preserved** and the
  new columns defaulting to UNKNOWN.
- Tests: algorithm fields are part of the rule schema; a stated algorithm is not a hard fail; an empty extraction
  still is; a bar-rule strategy is unaffected; end-to-end scoring of a PAMR-shaped idea reaches a non-REJECTED status;
  the v1→v5 migration keeps data and defaults the new columns (239 tests).

## Self-directed search: the harvest drives itself (2026-10-02)
The gap that stopped the loop being autonomous: it could run rounds, respect budgets and resume, but could not decide
**what to look for next**. Consequence measured live — rounds 2 and 3 of campaign 9 both reported
`0 new papers found`, because round 1 had already run the entire static query plan.
- **`campaign/directions.py`** derives follow-up queries from the campaign's **own extracted ideas**: strategy
  families weighted by how much the campaign found on each, their asset classes, and templates aimed at the gate's
  measured gaps (`out of sample`, `replication`, `transaction costs`, `robustness` — the components that hold every
  idea below the gate per `docs/GATE_CALIBRATION.md`). Nothing is invented: no extracted family means no direction.
- `run_harvest` issues those directions to each later round (`runner.run(cid, extra_queries=...)`), tracks what it
  already asked so round 3 does not repeat round 2, and reports them under `follow_up_queries`. Round 1 still follows
  the operator's request, since it has no ideas to learn from yet.
- **Verified live** (`volatility risk premium options strategy`, $0.193): round 1 found 59 papers and produced
  `#28 Deep Hedging with Options Using the Implied Volatility Surface` **PROMISING** (54.5 → 56.5 after deepening,
  1 supporting source). Round 2 announced *"following up on 2 direction(s) from previous rounds (e.g. deep hedging
  out of sample evidence)"* and found **29 new papers** that the static plan would never have reached.
- **Also fixed (Stage 2): a spent search allowance no longer ends the campaign.** Searching costs HTTP requests, not
  AI money; measured in campaign 5, one round spent 97 of 100 searches and the run ended while the daily cost budget
  was untouched. A `search_requests` stop is now a per-round state (`search_stops` in the report) and the next round
  gets a fresh allowance with new directions. Genuine ceilings (campaign AI cost, urls, documents) still stop the run.
- Tests: directions require real extracted families, are ordered by investment, de-duplicated across rounds and
  limited; the loop directs its own round 2; a spent search allowance continues while a campaign cost cap stops
  (232 tests).

## Verified end to end: a 3-round harvest now advances and passes ideas (2026-10-02)
The multi-round behaviour the whole feature exists for, verified against live sources for the first time.
- **Fixed defect: a memory-saturated round 1 ended the run.** `NO_NEW_SOURCES` was treated as terminal, but a round
  usually reports it because it has run out of *unsearched* queries — the next round widens the plan. On a topic whose
  queries are already in search memory (the normal case for a daily scheduled harvest) the loop gave up in round 1,
  before widening could help. `NO_NEW_SOURCES` is no longer terminal; genuine emptiness is caught by the
  `NO_PROGRESS` guard, which requires *consecutive* empty rounds.
- **Verified run** (`qsd harvest "mean reversion trading strategy code" --target 3 --max-rounds 3 --docs 2`, $0.349):
  - Round 1: 12 new papers → 2 read → `#20 Optimal Mean Reversion Trading Strategy` **PROMISING** (48.1), deepened
    to 50.1 with 3 supporting and 2 contradicting sources found. (1/3)
  - Round 2: `0 new papers` yet 2 read from the unfetched pool → 3 strategies extracted, none passed. (1/3)
  - Round 3: 2 read → `#27 Altcoin-Bitcoin Arbitrage` **PROMISING** (52.4). (2/3)
  - Stopped on `MAX_ROUNDS: 3 round(s) done, 2/3 promising idea(s)` — an honest stop, target not reached.
- **What this proves:** rounds advance instead of repeating round 1; deepening runs on live ideas and finds both
  supporting and contradicting sources; ideas reach `PROMISING` from live content rather than from relabelling; and
  the run terminates on its own with an explicit reason and a known cost.
- **What it does not prove:** the target was not reached in 3 rounds (2 of 3), so yield stands at roughly one passing
  idea per 3–5 documents read; and a cross-day resume has still not been observed (every run so far is on one
  budget day).
- Tests: a memory-saturated round is retried with a wider plan; config errors still stop immediately; repeated empty
  rounds stop via `NO_PROGRESS` (225 tests).

## Live verification: free-first works; algorithm-type strategies are unextractable (2026-10-02)
A live round ($0.041) verified this session's changes and exposed a new, structural gap.
- **Free-first selection verified.** Of four selected candidates, the one free paper was read and produced four
  strategies; the three paywalled ones were correctly skipped *after* selection instead of consuming all four slots
  as they did before the fix. 1 of 4 read is the honest consequence of a topic whose free PDFs are scarce.
- **GitHub channel verified as discovery but not as a source of read documents.** 9 GitHub candidates were stored;
  `0` were read, because tier-1 papers outrank tier-3 repositories and `docs_per_round` is 4. The connector works;
  the ranking (correctly) does not prioritise code.
- **New finding: `RULES_NOT_QUANTIFIABLE` rejects algorithm-type strategies.** The PAMR paper (*Passive Aggressive
  Mean Reversion for portfolio selection*) produced 4 ideas that were all **hard-failed**, not merely scored low:
  completeness **10%**, `signal` UNKNOWN, `entry_rule` UNKNOWN, `exit_rule` UNKNOWN, `stop_rule` UNKNOWN, and the
  only parameter recorded was `epsilon: UNKNOWN` — even though PAMR is a fully specified algorithm with explicit
  update equations and parameters (η, λ, ε).
  This is the anti-fabrication rule working *correctly* — the model refused to invent a bar-based entry rule that the
  paper never states — but it means the pipeline silently discards an entire class of legitimate quantitative
  strategy, because the extraction schema is shaped for retail bar-rules (`signal`, `entry_rule`, `exit_rule`) and has
  no representation for a portfolio-weight algorithm whose "rule" is an update equation.
  Not fixed here: it needs a schema/model decision (e.g. an algorithm-shaped rule field), not a prompt tweak.

## Gate lowered 70 → 60 (D37), and why (2026-10-02)
The operator asked why nothing passes and whether the bar was too high. Measurement says: partly yes, for a reason the
config never intended.
- **Several components cap below 1.0 by design** — `edge_vs_cost` 0.60, `data_availability` 0.70,
  `exit_executability` 0.70, `liquidity` 0.80, `opportunity_frequency` 0.80 (`scoring/scores.py`). A *perfect* idea
  therefore scores **88.2**, not 100, so the "70" bar was really **~79% of what is achievable**.
- **Each idea's own ceiling was 86–94**, i.e. the gate was never unreachable — the ideas simply scored 20–27 points
  below their own ceiling, mostly through `expected_robustness` = 0 and `parameter_simplicity` ≈ 0.
- **Change:** `quality_gate.promising` 70 → **60** (the old `research_further` line), in both `config/default.yaml`
  and the `QualityGate` model default, which had drifted apart. Weights, `min_coverage_for_gate` and the handoff rule
  are untouched.
- **Effect:** 7 ideas are now `PROMISING` (was 0). Ideas #12/#13 still do not pass — their coverage is 54 %, below the
  60 % minimum — and #14 stays `REJECTED` on a hard fail, so the gate still discriminates.
- **The trade-off, stated plainly:** all seven have `replication = 0.0` and evidence quality 15–25. They clear the
  *idea-quality* gate, which measures **how completely an idea is specified**, not whether it has been validated.
  They are hypotheses ready for a backtest, which is exactly what a backtest is for — but they are not evidence-backed.
- **Consequence worth knowing:** `status` is part of the handoff rule, so lowering the gate did not merely relabel
  these ideas — idea #9 now reports `handoff.eligible: true` (it previously reported
  `status is RESEARCHING` as its blocking reason). `qsd queue --submit-ready` would now submit them.

## Three defects found by an actual harvest round (2026-10-02)
A live round (36 papers discovered, 4 read, **0 ideas, $0.00**) exposed three problems that only appear when the loop
runs against real sources.
- **Free sources were never preferred, despite the docs claiming otherwise.** `_select` ranked by tier and relevance
  only, so the round spent all four document slots on `access_restricted` papers and produced nothing. Measured: the
  evidence preference (below) correctly surfaced out-of-sample papers, and they were exactly the paywalled ones.
  Selection now ranks, inside a tier: **free** (stored `PDF_URL`, arXiv, local file or video) → relevance → reported
  evidence. The fetcher still decides access for real, so a wrong guess costs one slot instead of the whole round.
- **Hyphenated terms were destroyed by the filler-word regex.** `_FILLER` used `\b`, and a hyphen is a word boundary,
  so "out-of-sample" was stripped to `out- -sample` and sent to arXiv as `all:out- AND all:-sample` — a wasted search
  that could never match the compound term. Boundaries are now hyphen-aware
  (`(?<![\w-])…(?![\w-])`); "crash-protection" survives too. The same bug silently degraded every request containing
  a hyphen, which is common in this domain (out-of-sample, cross-sectional, post-earnings, short-term).
- **Later rounds found nothing because every query was already in search memory.** Round 1 runs the whole query plan;
  round 2 asks the same six queries, gets `Search finished: 0 new papers found`, and the harvest stalls for a reason
  unrelated to the topic. The loop now widens `max_queries` each round (`max_queries × round`), so new query families
  come into play instead of repeating a plan that is guaranteed to return nothing.
- Tests: free-first ordering inside a tier, the hyphen regression across several requests (223 tests).

## Gate calibration, and selection that targets the gap (2026-10-02)
Measured, not estimated: `docs/GATE_CALIBRATION.md` analyses the live per-component scores of all 14 ideas to answer
whether the `PROMISING` gate is reachable at all.
- **The finding.** The best idea (#9) scores normalized **63.69** against a gate of 70 — 6.31 points short — with 78
  of 100 weight assessed. Every component except two is already at the ceiling its scoring rule allows
  (`liquidity` 0.8, `data_availability` 0.7, `edge_vs_cost` 0.6 and `exit_executability` 0.7 are all **maxima**, not
  intermediate values). The shortfall is concentrated in `parameter_simplicity` (0.08) and `expected_robustness`
  (0.00), worth 6 points each.
- **The answer: reachable, but only just.** Perfect extraction with no robustness evidence lands at **0.708** — a
  1-point margin. Improving `parameter_simplicity` to 0.5 takes it to 0.746, comfortably through. So the gate is
  sound and the content is the constraint.
- **`expected_robustness` cannot be improved by extraction.** It scores the share of
  {out-of-sample, replication, cross-market} signals present, so it rises only if the source reports such testing, or
  if deepening finds a replication study. That is a sourcing problem, not a prompt problem.
- **Selection now targets that gap (D35).** `_select` ranks by tier, then relevance, then reported evidence, using a
  `EVIDENCE_TERMS` vocabulary (out-of-sample, walk-forward, robustness, replicat\*, cross-market, after costs,
  sensitivity analysis, ...). Evidence is a **tie-breaker inside a tier** and never a promotion across tiers, so a
  tier-4 blog cannot outrank a tier-1 paper; and it reads only what the abstract already claims, so it fabricates
  nothing. Measured on the real vocabulary: a plain abstract scores 0, an evidence-reporting one scores 4.
- **The gate and the weights were left alone (D36).** The shortfall has an honest cause and the achievable margin is
  about one point; lowering the bar so today's ideas pass would make the metric meaningless.
- Tests: evidence preference wins inside a tier, and cannot lift a lower tier over a higher one (221 tests).

## Live verification round: transcripts and search budget (2026-10-02)
Everything below was found by **running the system against live sources**, not by reasoning about it. This is the
first round in which the delivered features were exercised end to end.
- **YouTube transcripts verified against a real video.** yt-dlp fetched genuine auto-captions (148 KB VTT) and the
  parser turned them into 413 clean timestamped lines (0 malformed, 0 empty, 1 residual duplicate).
- **A real transcript was extracted by a real model.** `qsd extract` on a 15-minute price-action video produced an
  idea with concrete rules (`idea 10`), and the red-flag detector independently caught the video's hype
  ("36 wins and just five losses", "it just broke 1,000%") plus its prompt-injection text. The system is sceptical
  about the sources it reads, which is the whole point (spec §82).
- **New defect — caption shape.** Captions arrive as hundreds of 2-6 second blocks, and the stage-B window is
  character-budgeted **per block**, so a 20-minute video (413 blocks) would have been truncated long before the
  middle of the video, where the rules usually are. `youtube._sentence_stream` re-flows caption lines into ~1200-char
  timestamp-anchored paragraphs; measured 413 lines → 18 blocks, nothing lost, and the whole 21 KB transcript now
  fits one window. Regression tests assert no caption text is dropped or reordered.
- **New defect — the search allowance was a campaign lifetime cap.** A single live round spent **97 of 100** searches
  (39 discovery + 58 replication/contradiction for four ideas), so with a lifetime cap round 2 could not search at
  all and the harvest stopped with `BUDGET: budget exhausted: search_requests`. Two fixes: `qsd harvest` now sets
  `per_round_search_budget` (default `--search-budget 120`), which resets only the search counter each round while
  every cost/document/url/token cap stays cumulative; and the default lifetime cap rose 100 → 300, since a real round
  legitimately spends ~40 on discovery plus ~60 deepening.
- **yt-dlp temp-dir control.** `fetch_transcript` accepts a `base_dir`, so caption files can be written somewhere
  writable when the system temp directory is not; still subtitles only, never media.

## GitHub strategy code, and a daily schedule (2026-10-02)
Finishes the "code" channel of multi-source browsing and gives the unattended mode something to launch it. Both are
off by default; neither needs a new dependency or an API key.
- **`GitHubConnector`** (`discovery` connector `github`, off unless `discovery.github_enabled: true`). Searches
  repositories (`trading strategy in:name,description,readme`, `fork:false`, sorted by stars) and turns each hit into
  a candidate whose abstract carries the licence, language, star count and topics — so **licence terms are visible
  before anything is reused** (spec §11). Only metadata and the repository URL are collected: nothing is cloned and no
  code is downloaded; the repo page is read later by the ordinary HTML handler, so its README is extracted and
  grounded like any other source. Public REST API with no key needed, but unauthenticated search is limited to
  60 requests/hour, which is why it is opt-in; `GITHUB_TOKEN` raises the limit and is optional. `github_min_stars`
  keeps only repos with some community signal.
- **`OFFICIAL_API_PREFIXES`** in `fetch/client.py`: a narrow prefix allowlist for APIs whose documented paths are
  per-resource. GitHub's `/repos/` and `/search/` are the only entries; the existing exact-match allowlist is
  untouched, so no previous behaviour changes. `api.github.com/users/...` is deliberately still not allowed.
- **`Install-HarvestSchedule.ps1`**: registers a daily Windows Scheduled Task that runs `qsd harvest` (with
  `-Remove` and `-RunNow`). A schedule rather than an internal sleep loop, because the daily budget cap means a
  single process would have to sleep for hours to cross the reset, and a sleeping or rebooting machine would silently
  stop harvesting. Each run continues the same campaign, so already-read documents and cached answers are not paid for
  twice. The task runs as the current user because QSD reads API keys from the user environment.
- Tests: candidate mapping, licence/no-licence handling, query scoping, fork/star filters, the `per_page` cap,
  token-in-header-only (never in the URL), credentialed responses never cached, typed errors for HTTP and non-JSON
  failures, the allowlist boundaries, and that the connector joins only when explicitly enabled (216 tests).

## Continuous harvest: transcripts, identity, and a loop that resumes (2026-10-02)
The system could extract (previous entry) but only when a human launched one campaign and waited. This adds the
missing machinery for unattended harvesting toward a target, plus the two measured defects that would have blocked it.
- **YouTube transcripts (D29, supersedes D27).** A video is now read through its captions, not just its description:
  measured evidence is campaign 3, where 150 video descriptions were triaged for $0.26 and produced **zero**
  strategies — a video's strategy is spoken. `fetch/youtube.py` runs yt-dlp with `--skip-download` (subtitles only;
  media is never downloaded), converts VTT/SRT to `[t=HH:MM:SS]` lines, strips the rolling-caption repeats that would
  otherwise be stored and paid for several times, and caches the result as a `TRANSCRIPT` source fact so a resumed
  campaign never re-pays. Behind `discovery.youtube_transcripts` (**default off**) plus
  `youtube_caption_languages` and `youtube_transcript_timeout_seconds`; with it off, reading is exactly as before.
- **Source identity from any URL (D30).** `fetch/identifiers.py` derives DOI/arXiv ids from a page URL, a `PDF_URL`
  fact or a local path, with the **version suffix stripped**, and `fetch/enrich.py` writes them as `ID_*` facts when a
  source is read. Previously a paper reached by direct URL kept `title UNKNOWN`, `identifier 0` — 41 points of source
  quality forfeited — and `arXiv:1304.6846v2` could count as a different study from `arXiv:1304.6846`, which would
  have inflated both "independent replication" and any target that counts distinct ideas. Enrichment is additive only:
  a known title/author/date/identifier is never overwritten, and it never claims to have added what it did not add.
- **`qsd harvest` (D31).** Runs rounds until N **distinct** ideas are `PROMISING` (counted after scoring and dedupe,
  by fingerprint), with an explicit stop for every outcome: `TARGET_REACHED`, `BUDGET_DAY_EXHAUSTED` (sleeps to the
  daily reset and continues — the daily cap is hard, so this is what "continuous" can mean), `BUDGET` (a per-campaign
  cap will not reset, so it stops), `STALLED` (no new promising idea for `--stall-rounds`), `DEAD_END`, `CONFIG`,
  `DOCS_TOTAL`, `MAX_ROUNDS`. Between rounds the campaign's `discover` phase is re-opened, without which every later
  round would only re-select the first round's candidate pool and no new sources would ever be searched for.
- **`NO_PROGRESS` guard (`--empty-rounds`, default 2).** Found by running `qsd harvest` live against a machine that
  could not reach the research hosts: a round reported `NO_NEW_SOURCES` internally, yet the outer loop carried on for
  another full round (~45s of searching) because that condition was decided *inside* the runner and never surfaced.
  A round that finds no new source **and** reads nothing now counts toward an empty-round limit and stops the run with
  `NO_PROGRESS`. Never triggered by a round that did read documents, so a slow-but-productive query is unaffected.
- **`harvest.bat`** launcher (CRLF, like `research.bat`): asks for the request, target and rounds, then runs
  unattended. `dashboard.bat` was restored — it had been deleted in the working tree.
- **Stage B prompt b4.** `time_horizon`/`timeframe` must now be stated from the rules, and numeric settings the rules
  depend on must appear in `parameters`. Measured: `data_frequency: daily` was extracted while `time_horizon` stayed
  UNKNOWN, leaving `opportunity_frequency` (weight 5) unscored and `rule_quantifiability` short for no reason the
  source justified.
- **Not changed, deliberately (D32):** `parameter_simplicity` still scores 0 for the first five-idea paper and the
  `PROMISING` threshold is still 70. That 0 is honest — 4 indicator families and ~8 threshold comparisons — and
  lowering the bar to make a harvesting loop look successful would defeat the gate.
- Tests: VTT/SRT parsing and repeat-stripping, the `--skip-download` compliance assertion, transcript caching and
  reuse, identifier/version handling, additive-only enrichment, and the harvest loop's target/stall/budget/config/
  doc-budget stops, plus the live-test no-progress guard (203 tests).

## Fix: stage B answers truncated at max_tokens (2026-10-02)
Diagnosis from the live ledger: 7 of 9 stage-B extractions ended at exactly `output_tokens = 6000` with
`success = 0` — the JSON was cut off mid-object, so it failed validation, the full cost was paid and nothing was
cached. Two causes: `stage_b_max_tokens: 6000` is too small for one strategy with a verbatim quote on every field,
and the provider discards DeepSeek's `finish_reason`, so a truncated answer was indistinguishable from a bad one.
- Provider: `ProviderResponse.truncated` (`finish_reason == "length"`, or `stop_reason == "max_tokens"` for Claude)
  is now reported. The DeepSeek adapter sends `thinking: {"type": "disabled"}` — thinking tokens are billed and are
  drawn from the same `max_tokens` budget, which is what truncated the JSON (spec §86).
- Gateway: a truncated answer is never trusted and never cached; it raises `AIOutputTruncated` (an `AIOutputError`)
  with the token counts, so a partial object cannot silently lose fields.
- Stage B retry (prompt **b3**, `ExtractionReport.retried_short`): when the full answer is truncated, the source is
  asked once more for a single compact strategy (rules only, no rationale/claims/regimes) at the same output budget.
  A second truncation raises instead of looping. The retry is billed, so `ai_calls` shows the real cost of a failure.
- `ai.stage_b_max_tokens` **6000 → 16000**; `cheap_model`/`strong_model` set to the current DeepSeek models
  (`deepseek-flash` / `deepseek-v4-pro`) with their published peak prices, because the retired `deepseek-chat` alias
  no longer appears in the API's model list. Override either name in `config/local.yaml` to use another model.
- Tests: truncated-but-parseable answers, the length-finish mapping, the disabled thinking flag, the compact retry
  path and the double-truncation error (165 tests).

## YouTube search, sources.txt, maturity badge, Obsidian notes (2026-10-01)
Ideas adapted from the user's own Idea Extractor (read-only reference; nothing in it was changed). Not adopted:
subtitle download via yt-dlp (YouTube's terms), Claude-Vision OCR (Phase 2).
- `sources.txt` (+ `sources.example.txt`, git-ignored): YouTube videos, YouTube channels (`| n:25`, newest videos
  not read before, via the uploads playlist: ~3 quota units instead of 100), links, local files and folders, with
  `| title` overrides. `research.bat` → **S**, or `qsd sources [--dry-run]`. Already-read items are skipped; changed
  local files are read again; local files are parsed read-only.
- Maturity badge from rule completeness: CONCEPT (<35) / PARTIAL / TRADEABLE (≥60) / BACKTEST-READY (≥80) on the
  dashboard and in packages (`maturity`, package v1.2).
- `qsd notes` + `export.notes_dir`: one Markdown note per idea (YAML front matter: qsd_id, status, maturity,
  completeness, quality, instruments, market direction, source, tags) into an Obsidian vault subfolder; written at
  the end of each research run when configured. Untrusted text is escaped; only files with this idea's `qsd_id`
  are (over)written.
- `YouTubeConnector` (YouTube Data API v3, official endpoints `search` + `videos`): title, channel, date, duration
  and full description. Used by campaigns / `research.bat` when `YOUTUBE_API_KEY` is set
  (`discovery.youtube_enabled`, `youtube_results_per_query`, `youtube_max_links_per_video`).
- A video is read through its title + description (retrieval `API_METADATA`); videos are never downloaded and
  transcripts are not scraped (YouTube's terms). Research links in the description (papers, DOIs, PDFs, code;
  social / shop / affiliate / sign-up links skipped) become candidate sources linked to the video (CITES) and are
  read like any paper. Videos are tier 3; follow-up evidence searches use paper sources only.
- API key sent as `X-Goog-Api-Key` header, never in URLs; credentialed responses are never cached; Google key shape
  added to log redaction. `qsd discover --connector youtube`.

## Guided research with live progress (2026-10-01)
- `research.bat` / `qsd research`: asks what to research, shows how it was understood (assets, market direction,
  strategy types) and the cost caps, then runs everything: search → read → extract + fact-check → score →
  follow-up searches → backtest queue → summary; offers another round of papers for the same research.
- Live monitor `/live` on the dashboard (auto-refresh every 3 s while running): searches, papers found / read with
  progress bar, strategies found with status, quality and market direction, AI cost vs cap, activity log.
  `qsd research` starts the dashboard itself when it is not already running.
- Campaigns report progress (`on_event`) and store the last 80 steps in `campaigns.state.progress`;
  `qsd campaign` now prints them too. Ctrl+C marks the campaign STOPPED with a resume hint.
- SQLite busy timeout so the dashboard can read while a campaign writes. Console output never fails on unusual
  characters in paper titles.

## Fix: real quotes rejected because the PDF text lost its spaces (2026-10-01)
- `qsd factcheck` on the first live paper showed pdfplumber gluing words together
  ("Thissuggeststhatforthestockallocationshould be120minus..."); the AI quoted the sentences correctly spaced,
  so grounding rejected 7 true values.
- PDF handler v2: pages with implausibly long "words" are re-read with tighter word-gap tolerances (1.5, then 1.0)
  and the better-spaced text is kept.
- Grounding: quotes of ≥ 12 characters also match when their characters, ignoring whitespace, occur in the source
  in the same order.
- Grounding: approximate (word-aligned) matching now rejects a quote whose differing word is one the value relies on
  (e.g. "monthly" in quote and value, "annually" in the source).

## Diagnostics: `qsd factcheck <idea>` (2026-10-01)
- For each value removed by grounding, prints the AI's quote, whether it matches exactly, the share of its words
  found in order, the closest passage of the text the AI saw, and any unusual characters there (no AI cost).
  Added because real quotes from the first live paper were rejected and the cause needs the user's local copy.

## Windows launcher, own port (2026-10-01)
- Dashboard default port is now 8877 (was 8765, which can clash with other local dashboards).
- `dashboard.bat`: double-click to start the dashboard and open the browser; if it is already running, just opens
  the browser, after checking that the program on that port really is the QSD dashboard. `.gitattributes` keeps Windows line endings for `.bat` files.

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
