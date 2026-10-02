# Checkpoint

**Last completed:** Multi-round harvest verified end to end (2026-10-02). A 3-round run advanced properly, deepened
real ideas, and produced two `PROMISING` ideas from live content, stopping on its own at `MAX_ROUNDS` with a known
cost ($0.349).
**Next:** two open items, both needing a decision rather than more code:
1. **Algorithm-type strategies are discarded.** An equation-shaped strategy (PAMR: portfolio-weight update) extracts
   as `signal/entry/exit = UNKNOWN` (completeness 10%) and is **hard-failed** as `RULES_NOT_QUANTIFIABLE`, even though
   the paper specifies it fully. The schema is shaped for retail bar-rules and has no representation for an update
   equation. Fixing it needs a schema/model decision — a prompt tweak would only invent rules.
2. **Blogs/news still have no connector** (no Brave/Tavily key configured on this machine).

**Branch:** `claude/loving-noether-1m40os` — all 2026-10-02 work is **uncommitted** in the working tree.

## State at this checkpoint
- Tests: **225 passing**; lint clean (`ruff check .`).
- Objective metric: **9 ideas at `PROMISING`** (was 0 before this session). 0 submitted to the backtest queue.
- Gate: `quality_gate.promising` = **60** (D37), in both `config/default.yaml` and the model default.
- YouTube transcripts and GitHub are both **off by default**; enable with
  `QSD_DISCOVERY__YOUTUBE_TRANSCRIPTS=true` / `QSD_DISCOVERY__GITHUB_ENABLED=true` or in `config/local.yaml`.
- Models: `deepseek-flash` (triage) / `deepseek-v4-pro` (extraction), `ai.stage_b_max_tokens` 16000.

## Verified live (do not re-derive)
- **A 3-round harvest advances and passes ideas.** Round 1: 12 new papers, 2 read, `#20 Optimal Mean Reversion
  Trading Strategy` PROMISING (48.1 → 50.1 after deepening, 3 supports / 2 contradicts). Round 2: `0 new papers` but
  2 read from the unfetched pool. Round 3: `#27 Altcoin-Bitcoin Arbitrage` PROMISING (52.4). Stop:
  `MAX_ROUNDS: 3 round(s) done, 2/3`.
- **Rounds 2+ read from the unfetched candidate pool** even when discovery finds nothing new — that is why widening
  the query plan matters less than expected.
- **Free-first selection works**: 1 of 4 selected candidates was free and produced 4 strategies; the 3 paywalled ones
  were skipped instead of consuming every slot.
- **GitHub is a discovery channel, not a reading one**: 9 candidates stored, 0 read (tier-1 papers outrank tier-3
  repos).
- **`RULES_NOT_QUANTIFIABLE` hard-fails algorithm strategies** — see "Next" above. This is the anti-fabrication rule
  behaving correctly (the model refuses to invent a bar-rule the paper never states) with a bad outcome.
- **Yield estimate: about one passing idea per 3–5 documents read.**
- Real captions: 148 KB VTT → 413 lines → 21 KB in 18 blocks. yt-dlp names the auto track `<id>.<lang>-orig.vtt`.
- **Correction:** a Windows Schannel TLS error (`SEC_E_NO_CREDENTIALS`) from a shell is NOT "no network". Python's own
  TLS stack reaches `api.deepseek.com` and YouTube. Verify with Python before concluding the network is unavailable.
- Reading `data/qsd.sqlite` by **file copy can miss recent commits held in the WAL**; open it read-only instead
  (`file:...?mode=ro`).

## How to resume
Say "resume". The session follows `CLAUDE.md`; the design is in `docs/GATE_CALIBRATION.md` (gate arithmetic) and the
workspace file `DESIGN_continuous_harvest.md` (modules M1–M5, phases P0–P4).
