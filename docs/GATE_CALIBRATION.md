# Gate calibration: is `PROMISING` (normalized ≥ 70) reachable?

**Date:** 2026-10-02 · **Evidence:** live `data/qsd.sqlite`, best-scoring idea (#9, normalized 63.69) and all 14 ideas
with stored component scores. Produced by the analysis script `calibration.py`.

## What the score actually is

`normalized = Σ(value × weight over assessed components) / Σ(assessed weights) × 100`, where each component value is a
fraction 0–1. So the gate means "earn 70% of the weight that could be assessed". Unassessable components are excluded
from both numerator and denominator: they are **not** a penalty, and having fewer of them *raises* the achievable
percentage for the same quality.

## Measured position of the best idea (#9)

| | |
|---|---|
| normalized | **63.69** |
| coverage | 0.78 (78 of 100 weight assessed) |
| shortfall to gate | **6.31 points** |
| components at 0.0 | `expected_robustness` (weight 6), `parameter_simplicity` (weight 6 — value 0.08) |

Everything else on that idea is already at or near the ceiling its scoring rule allows:

| component | now | practical max | note |
|---|---|---|---|
| `economic_rationale` | 0.80 | 1.00 | source must argue the mechanism with confidence ≥ 0.7 |
| `rule_quantifiability` | 0.80 | 1.00 | formalization completeness 100 |
| `liquidity` | 0.80 | **0.80** | 0.8 is the top branch in `scores.py`; cannot go higher |
| `edge_vs_cost` | 0.60 | **0.60** | 0.6 is the maximum — "source addresses costs" |
| `data_availability` | 0.70 | **0.70** | 0.7 is the bar-data maximum |
| `exit_executability` | 0.70 | **0.70** | 0.7 is the maximum |
| `parameter_simplicity` | 0.08 | 1.00 | 4 indicator families + ~8 thresholds → complexity ~92 |
| `expected_robustness` | 0.00 | 1.00 | needs out-of-sample / replication / cross-market signals |
| `automation` | 1.00 | 1.00 | already maximal |
| `diversification` | 0.60 | 1.00 | family-overlap dependent |

## The answer

**The gate is reachable, but only just, and only with near-perfect extraction.** Arithmetic on the real components:

| scenario | score | verdict |
|---|---|---|
| today, as measured | 0.637 | fail by 0.063 |
| perfect extraction, **no** robustness evidence | **0.708** | passes by 0.008 — a 1-point margin |
| perfect extraction, `parameter_simplicity` improved to 0.5 | 0.746 | passes comfortably |

So the binding constraints are exactly two components, both worth 6:

1. **`expected_robustness`** — a property of the *source*, not of extraction. It only rises if the material reports
   out-of-sample, replication or cross-market evidence. A single YouTube video or a blog post essentially never does;
   a paper that reports an out-of-sample test does, and deep research can sometimes find a replication study.
2. **`parameter_simplicity`** — a property of the *strategy*. The best measured idea combines Bollinger Bands, RSI,
   Fibonacci, EMA and ATR, so complexity ≈ 92 and this component ≈ 0.08. **A genuinely simple idea (one signal, one
   filter) would score ~1.0 here and gain ~5.5 points on its own.** This is the single most valuable selection
   criterion the system is currently not steering by.

## Consequences for the harvest design

- The target "N ideas at `PROMISING`+" is achievable, so it is the right target — but the expected yield per document
  is **well under one**, so a target of 10 plausibly needs 30–80 documents, i.e. several budget days at $1/day.
  The harvest loop must therefore be *patient* rather than *wide*; the budget-day sleep is load-bearing, not a nicety.
- **Selection should favour simple strategies and evidence-reporting sources.** The system already ranks by relevance
  and tier, but it does not currently prefer (a) sources that report out-of-sample/replication evidence or (b) ideas
  whose rules are simple. Adding those two preferences is the cheapest available increase in gate yield.
- Videos remain worth reading (the transcript path works and produced a real idea), but they should not be expected
  to reach the gate on their own; they are a discovery channel feeding paper/blog evidence, not a completion channel.

## What this does NOT license

Lowering the 70 threshold or the component weights so that existing ideas pass. The measurement above shows the ideas
fall short for an honest reason — no robustness evidence, complex rules — and the fix is better sourcing and better
selection, not a friendlier gate.

---

## 2026-10-04: re-measured after the evidence fixes (D41–D46) → gate back to 70 (D48)

**Evidence:** all 31 ideas with stored component scores (`gate.txt` from the live database), after two-column grounding,
whole-document evidence with the precision fixes, and data availability for every bar-data asset class.

| normalized | ideas | what they have in common |
|---|---|---|
| 78.1 | 20 | out-of-sample test reported (`expected_robustness` 1.0) |
| 71.6–71.8 | 27, 28 | 28: out-of-sample 2021–2023, costs, code (robustness 0.67); 27: completeness 95, rationale 0.95 |
| 70.8 | 10 | NEEDS_REVIEW (grounding) |
| *(none)* | | **nothing between 63.7 and 70.8** |
| 61.6–63.7 | 1, 5–9, 21, 22 | `expected_robustness` 0 on every one; 5–9 and 21/22 `parameter_simplicity` ≤ 0.08 |
| < 60 | the rest | |

Pass counts by gate (coverage ≥ 0.6, not rejected): **70 → 4** (20, 27, 28, 10) · **65 → 4** · **60 → 12**.

Sensitivity to the two known scoring quirks:

| change | largest effect | changes a result at 70? |
|---|---|---|
| "costs discussed" (0.6) treated as unassessed | ≤ 1.6 points (idea 28: 71.6 → 73.2) | no |
| `parameter_simplicity` dropped | 5–9: 63.1 → 68.3; 21/22: 62.5 → 69.0 | no |
| both | 21/22 → 70.8, 5–9 → 69.7 | 21/22 borderline |

Ideas 5–9 really are complex (Bollinger, RSI, Fibonacci, EMA, ATR; about 9 thresholds), so their penalty is deserved.
The quirks are worth fixing on their own merits, but they do not justify a lower gate.

**Why 60 is no longer needed:** D37 lowered the gate because robustness read 0 almost everywhere (evidence was only
looked for in the abstract) and so that deepening would reach more ideas. The first is fixed. The second was mistaken:
the deepen stage already follows up RESEARCHING ideas as well as PROMISING ones (`campaign/runner.py`).

**Caveat:** 31 ideas is a small sample, and only 4 are above 70. Re-measure after the next research campaigns; lower the
gate again only with a new measurement.
