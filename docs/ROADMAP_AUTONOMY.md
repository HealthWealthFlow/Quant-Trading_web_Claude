# Roadmap to a genuinely self-running system

**Status:** Stage 1 delivered (self-directed search). Stages 2–6 below.
Derived from what six rounds of live verification actually showed, not from the original spec.

## The honest diagnosis

The harvest loop today is an **engine that needs a driver**. It runs rounds, respects budgets, resumes, deepens and
stops correctly — but it cannot decide *what to look for next*. Two live measurements show the consequence:

- Rounds 2 and 3 of campaign 9 both reported `0 new papers found`, because round 1 had already run the entire static
  query plan and every query was in search memory.
- Campaign 10 advanced only because unfetched candidates from round 1 were still in the pool. Once that pool empties,
  a harvest stops finding anything regardless of budget or time.

So "runs by itself" is not about scheduling or budgets. It is about the loop generating its own research directions.

---

## Stage 1 — Self-directed search ✅ DELIVERED

**Problem:** the query plan is static and exhausted after round 1.
**Delivery:** `campaign/directions.py` derives follow-up queries from the campaign's *own* extracted ideas —
strategy families (weighted by how much the campaign found on each), their asset classes, and templates aimed at the
gate's measured gaps (`out of sample`, `replication`, `transaction costs`, `robustness`). `run_harvest` feeds them to
each later round (`runner.run(cid, extra_queries=...)`), tracks what it already asked, and reports the directions it
chose. Round 1 still follows the operator's request.
**Verified:** 9 tests — directions require real extracted families (nothing invented when there are no ideas),
ordered by investment, de-duplicated across rounds, limit respected, and the full loop directing its own round 2.
**Effect:** the loop can now keep finding material after its opening plan is spent.

## Stage 2 — Search-budget exhaustion must not end a run (NEXT)

**Problem:** a per-campaign search cap is permanent. Measured in campaign 5: one round spent 97 of 100 searches on
discovery + deepening, and the run stopped with `BUDGET: budget exhausted: search_requests` even though thousands of
unused query combinations remained and the daily *cost* budget was untouched. Searching is free; AI calls are not.
**Plan:** treat a spent search allowance like the daily cost cap — a per-round state that resets, not a terminal
condition. Keep a hard lifetime ceiling so a runaway loop cannot hammer APIs forever, but make it generous and
configurable. Stop reasons must still name the cap so it is never silent.
**Test:** exhaust the per-round allowance mid-round, assert the next round runs and searches again; assert the
lifetime ceiling still stops the run.

## Stage 3 — Fill the blog/news channel

**Problem:** the objective requires blogs, and there is no connector. Needs a Brave/Tavily key, which is not
configured. **This one is blocked on the operator**, but the connector itself can be written and tested against
mocked responses now, so the only remaining step when a key appears is configuration.
**Plan:** a `WebSearchConnector` behind the same `Connector` interface, with the existing finance-word filter, polite
fetching and the same candidate metadata. Firecrawl/Scrapling tooling is available in this environment and could be
evaluated as an alternative backend.
**Test:** mocked search responses → candidates → a stored source, exactly like the YouTube and GitHub connector tests.

## Stage 4 — Represent algorithm-type strategies (schema decision)

**Problem:** PAMR (a fully specified portfolio-weight algorithm) extracted as `signal/entry/exit = UNKNOWN`, hit
10% completeness, and was **hard-failed** as `RULES_NOT_QUANTIFIABLE`. The anti-fabrication rule is behaving
correctly — the model refuses to invent a bar-rule the paper never states — but the schema has no representation for
an update equation, so a whole class of academic quant strategy is discarded.
**Plan:** add an additive rule field (e.g. `algorithm_rule`) plus a `strategy_kind` discriminator
(`BAR_RULE` / `ALGORITHM` / `PORTFOLIO_WEIGHT`), and change the hard fail to fire only when *no* rule form is stated.
Additive migration only; no existing column changes.
**Test:** an algorithm-shaped extraction scores low but is not hard-failed; a genuinely empty extraction still is.

## Stage 5 — Per-channel yield accounting

**Problem:** the system cannot yet learn which sources or connectors actually produce gate-passing ideas. It has the
raw data (`ai_calls` cost, idea scores, campaign yield) but no report and no feedback into selection.
**Plan:** a `qsd yield` report over the existing tables (ideas per dollar, per connector, per document read), and a
selection weight from it. This is the mechanism behind "high quality" — measured rather than assumed.
**Test:** the aggregation is computed from seeded rows, including the zero-division cases.

## Stage 6 — Prove the full cycle

**Problem:** every run so far landed inside one budget day. The cross-day resume path is tested but never witnessed,
and a target of N has never been reached by continuous operation.
**Plan:** a target-10 harvest across two budget days (cost ≈ $1/day). Watch for: `BUDGET_DAY_EXHAUSTED` → sleep →
resume, the target counter climbing across days, and a real yield figure to plan with.
**This needs operator time, not more code.**

---

## What is deliberately NOT proposed

- **Lowering the gate again.** It moved 70 → 60 for a measured reason (`edge_vs_cost` and others cap below 1.0, so
  even a perfect idea scores 88.2). No further movement without new measurement.
- **Letting the AI invent rules to raise completeness.** That is the one thing this system must never do.
- **Autonomous code changes.** The loop may choose its own *research directions* (Stage 1); it must not rewrite its
  own pipeline.
