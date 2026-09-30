# TODO

Milestone status lives in `PROJECT_STATE.json` (run `python -m qsd.cli status`). This file holds finer tasks.

## M6 — Scoring (next) — deterministic, no AI
- [ ] Source quality score 0–100 from tier + transparency signals (citations, code, method), marketing/red-flag
      penalties (spec §25); evidence quality score (§26) from sample/OOS/cost/replication signals present
- [ ] Red-flag language detector (§54) incl. martingale/doubling; hard-fail rules (§53) → Rejection rows
- [ ] Formalization completeness (§48) from known vs UNKNOWN rule fields; parameter complexity (§55)
- [ ] Idea quality score (§52) with configurable weights; unknown components score 0 and are listed, never guessed
- [ ] Strategy fingerprint + NEW/VARIANT/DUPLICATE (§69); root evidence id (§70); novelty (§80)
- [ ] Research priority score (§81) and quality gate bands (§126); status transitions with history
- [ ] Regime: flag ideas whose regimes are only RATIONALE_INFERRED; diversification tags incl. regime coverage
- [ ] `qsd score` command; tests for each rule

## Later milestones — market-regime grouping (§137)
- [ ] M5: extraction prompt returns regime suitability + basis + confidence; UNKNOWN by default
- [ ] M6: flag RATIONALE_INFERRED-only regimes for review; regime coverage in diversification tags
- [ ] M7: regime profile in research package
- [ ] M8: dashboard grouping/filter by regime (Long / Short / Consolidation / Crash)
- [ ] M9: campaigns can target a regime
