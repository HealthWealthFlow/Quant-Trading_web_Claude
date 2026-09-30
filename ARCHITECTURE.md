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
src/qsd/cli.py             `qsd` command
tests/                     pytest suite
docs/MASTER_SPEC.md        full requirements (§ references)
```

Planned packages: `qsd.db` (M1), `qsd.handlers` (M2), `qsd.fetch` (M3), `qsd.discovery` (M4),
`qsd.ai` (M5), `qsd.scoring` (M6), `qsd.packaging` (M7), `qsd.web` (M8), `qsd.campaign` (M9).

## Key technology choices
See `DECISIONS.md`.
