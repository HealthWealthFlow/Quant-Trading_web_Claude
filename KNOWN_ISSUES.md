# Known issues

- No general web search provider yet (by decision D5). Add a Brave/Tavily adapter later if a key is provided.
- YouTube is searched by metadata only (title, description, linked papers/code); spoken content of videos is not read.
  Local video/audio files and podcasts (local Whisper transcription), community sources and DevTools inspection are
  Phase 2.
- The Claude Code cloud build container blocks many research hosts (e.g. arxiv.org) via its network policy.
  The code is tested with mocked HTTP; run live fetching on your PC/server, or widen the cloud environment's
  network access if you want live tests in the cloud.
- Corporate/proxy TLS: if HTTPS fails behind a proxy, set `SSL_CERT_FILE` to your CA bundle (httpx honours it).
- AI extraction quality depends on the model; grounding removes unverifiable values, which can leave more UNKNOWNs
  when a model paraphrases instead of quoting. Removed values are listed on the idea page; review NEEDS_REVIEW ideas.
- Grounding checks that a quote exists, not that it means what the model says (e.g. a regime labelled SUITED from a
  quote about lower risk). The b2 prompt asks for stricter meanings; a second-opinion review is Phase 2.
- Claude prices in config/default.yaml are from Anthropic's published list (Sep 2026); re-check periodically.
- First live run (2026-10-01, 3 documents) worked end to end; fact-check rules were tuned after it (D24, D25).
- Request parsing is keyword-based; check `qsd campaign ... --dry-run` output before running.
- Relation checks read abstracts only; a CONTRADICTS link is a lead for review, not a verdict.
- Academic sources rarely describe retail technical-analysis setups (e.g. support breakout + volume + pullback);
  many such papers are paywalled. YouTube (with a key), sources.txt and Phase-2 web search cover them better.
- **Two-column PDFs (2026-10-04, measured fixed).** pdfplumber can merge characters from both columns into one token.
  Since D41 every PDF page also gets a pdfminer layout reading that grounding may verify quotes against. Measured on
  the live database: MM-ARC (source 887) 13 → 2 removed values, source 767 12 → 3, source 727 11 → 7.
- **Completeness credits a stated study horizon as an exit.** `holding_period` counts toward "exit", so "10-year
  investment period" (idea 1, a passive allocation) earns the exit points. For that idea the score is still about
  right (its real exit is the stated annual rebalance), so it is left as is; a wording rule would be fragile.
- **Evidence sentences:** 24 of 30 correct at the first hand check (D45 fixed the 6 misses). A signal *definition*
  that says "100% out-of-sample" still counts as an out-of-sample test.
- **No ground truth (2026-10-05).** No score has been compared with a backtest result; 11 strategies are queued. The
  scoring weights and the 70 gate are measured against evidence in the sources, not against outcomes.
- **Scoring quirks (measured, not changed):** stating costs scores lower than silence (`edge_vs_cost` 0.6 vs
  unassessed; same shape for `liquidity`, `diversification`); `parameter_simplicity` counts words and double-counts
  thresholds. Neither changes which ideas clear 70.
- **Stitched quotes:** a quote that joins two sentences and skips one between them is rejected (idea 10's entry rule).
  Correct under "continuous text"; whether to allow a logged small gap is undecided.
- **Parts of one system are separate ideas** (ideas 5–8 under 9; ideas 11–15 from one paper under an older prompt).
