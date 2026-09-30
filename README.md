# Quant-Trading_web_Claude — Quant Strategy Discovery & Source Intelligence

Finds, filters and packages **testable quant strategy hypotheses** (stocks, ETFs, options, forex, crypto) with full
source provenance, grouped by market direction (Long / Short / Consolidation / Crash), for independent validation by
a separate Quant Auto OS. It **never trades**, and never treats a source's claimed performance as verified.

Requirements: [`docs/MASTER_SPEC.md`](docs/MASTER_SPEC.md) · Design: [`ARCHITECTURE.md`](ARCHITECTURE.md) ·
Progress: [`CHECKPOINT.md`](CHECKPOINT.md) · Package contract: [`schemas/research_package.schema.json`](schemas/research_package.schema.json)

## 1. Install (Windows or Linux, Python 3.11+)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate        Linux/macOS: source .venv/bin/activate
pip install -e ".[dev,anthropic]"
qsd db init
```

## 2. Configure (once)

Create `.env` (copy `.env.example`) with your AI key(s): `DEEPSEEK_API_KEY=...` (optional: `ANTHROPIC_API_KEY`).

Create `config/local.yaml` (not committed):

```yaml
discovery:
  contact_email: you@example.com          # OpenAlex/Crossref "polite pool"
ai:
  prices:                                  # USD per 1M tokens, from DeepSeek's pricing page (required!)
    deepseek-chat: {input_per_mtok: 0.0, output_per_mtok: 0.0}
budgets:
  max_ai_cost_usd_per_day: 1.0
  max_ai_cost_usd_per_month: 20.0
paths:
  local_sources: ["D:\\QuantResearch", "D:\\StrategyPapers"]   # read-only
```

A model without a price is never called. All budgets are hard caps checked *before* each AI call.

## 3. Use it

```bash
qsd campaign "Find crash-protection ETF strategies" --dry-run   # see how the request is understood
qsd campaign "Find crash-protection ETF strategies"             # search → fetch → extract → score → deepen
qsd campaign --resume 1                                          # continue (never repeats paid work)
qsd web                                                          # dashboard: http://127.0.0.1:8765/
qsd queue --submit-ready                                         # hand eligible packages to the backtest queue
```

Other tools: `qsd discover`, `qsd fetch <url>`, `qsd parse <file>`, `qsd scan <folder>`, `qsd extract <file>`,
`qsd score`, `qsd package <id>`, `qsd queries --asset ETF --regime CRASH`, `qsd status`, `qsd config`.

The Quant Auto OS reads `data/backtest_queue/pending/*.json`. Every package says:
**EXTERNAL PERFORMANCE CLAIMS ARE NOT VALIDATED. DOWNSTREAM SYSTEM MUST RECOMPUTE EVERYTHING.**

## Safeguards (not configurable off)
robots.txt and rate limits respected · no paywall/CAPTCHA/login circumvention · retrieved text treated as untrusted
data · secrets never logged · AI output grounded against verbatim quotes (unverifiable values become UNKNOWN) ·
performance stored only as source claims · no broker or live-trading path.

## Status
Phase 1 complete (M0–M9). Phase 2 backlog: see `TODO.md`.
