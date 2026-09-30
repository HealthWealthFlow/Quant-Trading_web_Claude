# TODO

Milestone status lives in `PROJECT_STATE.json` (run `python -m qsd.cli status`). This file holds finer tasks.

## M3 — Polite fetcher + compliance (next)
- [ ] URL canonicalization (lowercase host, strip fragments/tracking params, sort query) + tests
- [ ] robots.txt cache per host; disallowed → ROBOTS_DISALLOWED (not configurable off)
- [ ] Per-domain rate limiter (requests/min), exponential backoff + jitter, Retry-After, cooldown after repeated errors
- [ ] HTTP client (httpx): size cap (max_download_mb), timeout, identifying User-Agent, no cookies persisted
- [ ] Detect login walls / paywalls / CAPTCHA → ACCESS_RESTRICTED / PAYWALLED / MANUAL_ACCESS_REQUIRED, stop
- [ ] Request classification (spec §14) + header redaction; fetch_log rows hold no headers/bodies
- [ ] HTTP cache (ETag / Last-Modified) to avoid refetching
- [ ] `fetch_and_parse(url)` → HandlerResult + Source row + fetch_log; errors → errors table (no silent failure)
- [ ] Tests with a local mock transport: 429 + Retry-After, robots disallow, paywall/CAPTCHA pages, oversize body,
      redirect to login, secret-bearing headers never logged

## Later milestones — market-regime grouping (§137)
- [ ] M5: extraction prompt returns regime suitability + basis + confidence; UNKNOWN by default
- [ ] M6: flag RATIONALE_INFERRED-only regimes for review; regime coverage in diversification tags
- [ ] M7: regime profile in research package
- [ ] M8: dashboard grouping/filter by regime (Long / Short / Consolidation / Crash)
- [ ] M9: campaigns can target a regime
