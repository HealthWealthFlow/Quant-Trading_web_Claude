# TODO

Milestone status lives in `PROJECT_STATE.json` (run `python -m qsd.cli status`). This file holds finer tasks.

## M5 — AI layer (next)
- [ ] Provider interface + adapters: DeepSeek (default, OpenAI-compatible API), OpenAI, Anthropic; keys from env only
- [ ] Price table per model (config, USD per 1M tokens) → cost per call; ledger rows in `ai_calls`
- [ ] Cache keyed by sha256(content hash + prompt version + model + task) in `ai_cache` (spec §85)
- [ ] Hard budget checks before each call: per campaign / day / month (spec §45, config budgets)
- [ ] Versioned prompts: Stage A (cheap triage: asset, family, basic idea, red flags) and Stage B (strong: rules,
      rationale, missing rules, regime suitability + basis, claims) (spec §86, §137)
- [ ] Strict JSON schema validation (pydantic); missing → UNKNOWN; numbers only as CLAIMED_*; per-field confidence
- [ ] Untrusted wrapper for all source text; injection-flagged sources recorded; responses can't trigger actions
- [ ] Relevant-section selection for long docs (keyword index, spec §21) so books are never sent whole
- [ ] Tests with a fake provider: caching, cost ledger, budget stop, invalid JSON, fabricated-number rejection

## Later milestones — market-regime grouping (§137)
- [ ] M5: extraction prompt returns regime suitability + basis + confidence; UNKNOWN by default
- [ ] M6: flag RATIONALE_INFERRED-only regimes for review; regime coverage in diversification tags
- [ ] M7: regime profile in research package
- [ ] M8: dashboard grouping/filter by regime (Long / Short / Consolidation / Crash)
- [ ] M9: campaigns can target a regime
