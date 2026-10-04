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
- **Two-column PDFs are only partly handled (2026-10-04).** A gutter probe reads cleanly two-column pages column by
  column, but pages where pdfplumber has already merged words across the gutter (measured: tokens 242–504pt wide, far
  wider than a column) are deliberately left in pdfplumber's own order. On such a paper the text interleaves, so no
  contiguous quote exists and grounding correctly strips every value — measured: 13 of 19 values on one MM-ARC paper
  were removed as QUOTE_NOT_FOUND although the paper states them all. A safer fix is to stop the cross-gutter merge at
  word-extraction time (tighter x/y tolerance or line-level extraction) and then score candidate reading orders by how
  many source sentences survive intact, instead of probing for a gutter.
