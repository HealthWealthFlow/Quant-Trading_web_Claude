# Known issues

- No general web search provider yet (by decision D5). Add a Brave/Tavily adapter later if a key is provided.
- Video, audio, YouTube, community sources and DevTools inspection are Phase 2.
- The Claude Code cloud build container blocks many research hosts (e.g. arxiv.org) via its network policy.
  The code is tested with mocked HTTP; run live fetching on your PC/server, or widen the cloud environment's
  network access if you want live tests in the cloud.
- Corporate/proxy TLS: if HTTPS fails behind a proxy, set `SSL_CERT_FILE` to your CA bundle (httpx honours it).
- AI extraction quality depends on the model; grounding removes unverifiable values, which can leave more UNKNOWNs
  when a model paraphrases instead of quoting. Review NEEDS_REVIEW ideas.
- Claude prices in config/default.yaml are from Anthropic's published list (Sep 2026); re-check periodically.
