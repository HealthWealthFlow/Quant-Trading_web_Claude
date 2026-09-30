# Architecture

Quant Strategy Discovery & Source Intelligence (QSD). Finds, filters and packages *testable strategy
hypotheses* for a separate Quant Auto OS. It never backtests for validation and never trades.

## Pipeline (spec §2)

```
discovery connectors ─▶ polite fetcher ─▶ format handlers ─▶ provenance store
   (M4)                   (M3)             (M2)               (M1)
        ▼
cheap deterministic filters ─▶ cheap AI extraction ─▶ strong AI extraction (promising only)   (M5)
        ▼
scoring: source / evidence / idea quality, hard fails, red flags, dedupe, root evidence      (M6)
        ▼
research package ─▶ backtest queue ─▶ Quant Auto OS (downstream, separate system)             (M7)
```

Campaign runner (M9) drives the loop with budgets and stop conditions; dashboard (M8) reads the DB.

## Layout

```
config/default.yaml        defaults (budgets, crawling, AI models); overrides: config/local.yaml, QSD_* env
src/qsd/config.py          typed settings; safety options that may not be disabled are enforced here
src/qsd/logging_setup.py   JSON-lines logs with secret redaction
src/qsd/state.py           build-progress state (PROJECT_STATE.json)
src/qsd/taxonomy.py        shared vocabularies (statuses, reasons, regimes, missing-value markers)
src/qsd/db/                research DB: models.py (schema v2, additive migrations), engine/session helpers
src/qsd/handlers/          format detection + parsers (text, html, pdf, office, spreadsheet, epub, safe zip)
src/qsd/security.py        prompt-injection detection, untrusted-content wrapper
src/qsd/localscan.py       read-only local folder index
src/qsd/packaging/         research packages, handoff rule, file-based backtest queue
src/qsd/scoring/           deterministic scores, hard fails, dedupe, status pipeline
src/qsd/ai/                providers, gateway (cache/ledger/budgets), prompts, schemas, grounding, extraction
src/qsd/discovery/         connectors (arXiv/OpenAlex/Crossref/RSS), query families, tiers, budgets, runner
src/qsd/fetch/             polite fetcher: urls, robots, rate limits, access-barrier policy, cache, fetch→parse→store
src/qsd/cli.py             `qsd` command
tests/                     pytest suite
docs/MASTER_SPEC.md        full requirements (§ references)
schemas/                   JSON Schema of the research package (contract with the Quant Auto OS)
```

Planned packages:
`qsd.web` (M8), `qsd.campaign` (M9).

## Key technology choices
See `DECISIONS.md`.
