# Checkpoint

**Last updated:** 2026-10-05 · `main` at PR #17 (`12c6390`) · 265 tests passing, `ruff check .` clean · schema v5 ·
prompts a1 / b5 / c1 · quality gate PROMISING = 70 (D48).

**Orientation:** read `HANDOFF.md` §0 first — it holds the current state, the live-database snapshot, the ranked open
issues and the working method. This file is only a pointer.

**Last completed:** the 2026-10-04 measurement-and-repair round (D41–D49): harvest submits to the queue, whole-document
evidence, two-column PDF grounding, data availability for every bar-data class, `qsd reextract`, gate restored to 70,
false prompt-injection flag removed. 11 strategies are in the backtest queue; idea 10 is eligible
(`qsd queue --submit-ready`).

**Next (ranked, details in `HANDOFF.md` §0.3):**
1. Backtest results from the user's Quant Auto OS, and a path that feeds them back into the scoring (no ground truth yet).
2. Decide whether a quote that stitches two sentences may be accepted (idea 10's entry rule).
3. Two scoring quirks: stating costs scores lower than silence; `parameter_simplicity` counts words, not parameters.
4. "System vs component" relation between ideas from one paper (ideas 5–9; 11–15).
5. Web/blog connector (needs a Brave or Tavily key); YouTube playlists.

**Branch convention:** work on a feature branch, one pull request per change, merge only when the user says "merge it".
