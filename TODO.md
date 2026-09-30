# TODO

Milestone status lives in `PROJECT_STATE.json` (run `python -m qsd.cli status`). This file holds finer tasks.

## M1 — Database schema (next)
- [ ] SQLAlchemy models: sources, fetch_log, source_facts (provenance: page/slide/timestamp/section, method, confidence)
- [ ] ideas (spec §90 fields, `CLAIMED_*` metrics, UNKNOWN defaults), idea_status history, rejected reasons (§92)
- [ ] idea_regimes table (§137): idea × regime → suitability, basis, confidence, source_fact_id
- [ ] search_queries / search memory (§93), ai_calls ledger (§120), errors (§133), campaigns
- [ ] Schema versioning + `qsd db init` command
- [ ] Tests: create/read round-trip, UNKNOWN defaults, no secret columns

## Later milestones — market-regime grouping (§137)
- [ ] M5: extraction prompt returns regime suitability + basis + confidence; UNKNOWN by default
- [ ] M6: flag RATIONALE_INFERRED-only regimes for review; regime coverage in diversification tags
- [ ] M7: regime profile in research package
- [ ] M8: dashboard grouping/filter by regime (Long / Short / Consolidation / Crash)
- [ ] M9: campaigns can target a regime
