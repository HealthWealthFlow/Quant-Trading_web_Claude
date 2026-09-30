# TODO

Phase 1 (M0–M9) is complete. Phase 2 needs the user's go-ahead and budget.

## Before/at first live use (user)
- [ ] Set DeepSeek price + key, contact email; run `qsd campaign ... --dry-run`, then a small campaign (`--docs 3`)
- [ ] Review NEEDS_REVIEW ideas in the dashboard; tune `scoring.idea_weights` / `handoff` thresholds if needed

## Phase 2 backlog (priority order suggestion)
- [ ] Live smoke tests against arXiv/OpenAlex/Crossref (recorded fixtures from real responses)
- [ ] Second-opinion review (Claude) for high-priority ideas before handoff (spec §89)
- [ ] Reference tracing: follow cited DOIs/arXiv ids to originals (spec §30, §71) → search depth L1/L2
- [ ] Source performance learning + exploration/exploitation allocation (spec §34, §35, §97)
- [ ] Watchlists + scheduled refresh (spec §37, §38, §96)
- [ ] YouTube (Data API + captions), podcasts/audio (local Whisper, selective) (spec §17–§20)
- [ ] GitHub handler + license tracking (spec §109); community sources via official APIs (spec §23)
- [ ] Browser DevTools/network adapter, public only, with request classification + redaction (spec §12–§16)
- [ ] Author/organization discovery, source graph view (spec §29, §31, §32, §119)
- [ ] Postgres option; Quant Auto OS integration beyond the file queue (spec §21 stage)
