# TODO

Milestone status lives in `PROJECT_STATE.json` (run `python -m qsd.cli status`). This file holds finer tasks.

## M9 — Campaign runner (next, last Phase 1 milestone)
- [ ] Natural-language request → structured campaign (deterministic keyword parsing for asset classes, families,
      regimes, mode) stored in `campaigns` with budgets
- [ ] Loop: discover → pick top candidates (tier, abstract relevance) → fetch (PDF link first) → extract → score
- [ ] Deepening per promising idea: replication + contradiction query templates (spec §72, §73); link found
      sources as SUPPORTS / CONTRADICTS candidates; update search depth level (§43)
- [ ] Stop controller (§44, §129): budgets, runtime, low yield, target reached; stop reason recorded
- [ ] Resumable: campaign state in DB, re-running continues where it stopped
- [ ] `qsd campaign "Find crash-protection ETF strategies"`; end-to-end test with mocked APIs + fake AI

## Later milestones — market-regime grouping (§137)
- [ ] M5: extraction prompt returns regime suitability + basis + confidence; UNKNOWN by default
- [ ] M6: flag RATIONALE_INFERRED-only regimes for review; regime coverage in diversification tags
- [ ] M7: regime profile in research package
- [ ] M8: dashboard grouping/filter by regime (Long / Short / Consolidation / Crash)
- [ ] M9: campaigns can target a regime
