# TODO

Milestone status lives in `PROJECT_STATE.json` (run `python -m qsd.cli status`). This file holds finer tasks.

## M7 — Research package + backtest queue (next)
- [ ] Package builder (spec §122): JSON with provenance, original/supporting/contradicting sources, hypothesis,
      known + unknown rules, regimes (§137), data & point-in-time requirements, concerns, scores + coverage
- [ ] Mandatory downstream warning (§124) and CLAIMED_* section clearly separated
- [ ] Handoff rule (§123): no hard fail, completeness threshold, provenance known, quality above threshold
- [ ] `backtest_queue` (table or JSONL folder) + status SUBMITTED_TO_BACKTEST; never any broker/live path (§125)
- [ ] JSON schema file for the package so the Quant Auto OS can validate it; `qsd package` / `qsd queue`

## Later milestones — market-regime grouping (§137)
- [ ] M5: extraction prompt returns regime suitability + basis + confidence; UNKNOWN by default
- [ ] M6: flag RATIONALE_INFERRED-only regimes for review; regime coverage in diversification tags
- [ ] M7: regime profile in research package
- [ ] M8: dashboard grouping/filter by regime (Long / Short / Consolidation / Crash)
- [ ] M9: campaigns can target a regime
