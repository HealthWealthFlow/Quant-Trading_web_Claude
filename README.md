# Quant-Trading_web_Claude — Quant Strategy Discovery & Source Intelligence

Finds, filters and packages **testable quant strategy hypotheses** (stocks, ETFs, options, forex, crypto) with full
source provenance, for independent validation by a separate Quant Auto OS.
It never trades, and it never treats a source's claimed performance as verified.

Full requirements: [`docs/MASTER_SPEC.md`](docs/MASTER_SPEC.md) · Design: [`ARCHITECTURE.md`](ARCHITECTURE.md)
· Progress: [`CHECKPOINT.md`](CHECKPOINT.md)

## Quick start (Windows or Linux, Python 3.11+)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
copy .env.example .env               # Linux/macOS: cp .env.example .env  — then add API keys
qsd status                           # build progress and next milestone
qsd config                           # effective configuration
pytest                               # tests
```

Configuration: `config/default.yaml`; per-machine overrides in `config/local.yaml` or `QSD_<SECTION>__<KEY>`
environment variables. API keys only via environment / `.env`.

## Status
Phase 1 in progress (milestones M0–M9). Run `qsd status` or see `PROJECT_STATE.json`.
