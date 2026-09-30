# Known issues

- No general web search provider yet (by decision D5). Add a Brave/Tavily adapter later if a key is provided.
- Video, audio, YouTube, community sources and DevTools inspection are Phase 2.
- The Claude Code cloud build container blocks many research hosts (e.g. arxiv.org) via its network policy.
  The code is tested with mocked HTTP; run live fetching on your PC/server, or widen the cloud environment's
  network access if you want live tests in the cloud.
- Corporate/proxy TLS: if HTTPS fails behind a proxy, set `SSL_CERT_FILE` to your CA bundle (httpx honours it).
