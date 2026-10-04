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
- **Two-column PDFs (2026-10-04, fix pending real measurement).** pdfplumber can merge characters from both columns
  into one token (measured on MM-ARC: 242–504pt tokens; 13 of 19 values stripped as QUOTE_NOT_FOUND). Since D41 every
  PDF page also gets a pdfminer layout reading (text boxes, columns whole) that grounding may verify quotes against;
  the AI still sees the primary text. Reproduced and fixed on a synthetic merged-token page; **not yet measured on the
  real paper** — success criterion: `qsd reground --source 887` drops its removed-value count well below 13.
