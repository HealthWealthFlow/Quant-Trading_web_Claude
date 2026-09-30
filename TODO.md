# TODO

Milestone status lives in `PROJECT_STATE.json` (run `python -m qsd.cli status`). This file holds finer tasks.

## M8 — Minimal dashboard (next)
- [ ] FastAPI + Jinja2 server-rendered pages, no JS framework; read-only views of the DB; bind to 127.0.0.1
- [ ] Overview cards (spec §113): sources, high-quality sources, ideas, promising, backtest-ready, duplicates,
      rejected, AI spend (today/month), unresolved errors
- [ ] Ideas table (§115) with filters incl. market regime (Long/Short/Consolidation/Crash) and status
- [ ] Idea detail (§116) with red-flag panel (§117), known/unknown rules with quotes/pages, claims marked
      unvalidated, research-completeness checklist (§128), next research action
- [ ] Sources page (§114, §118 basic filters), AI cost page (§120), errors page (§133)
- [ ] `qsd web` command; tests with FastAPI TestClient

## Later milestones — market-regime grouping (§137)
- [ ] M5: extraction prompt returns regime suitability + basis + confidence; UNKNOWN by default
- [ ] M6: flag RATIONALE_INFERRED-only regimes for review; regime coverage in diversification tags
- [ ] M7: regime profile in research package
- [ ] M8: dashboard grouping/filter by regime (Long / Short / Consolidation / Crash)
- [ ] M9: campaigns can target a regime
