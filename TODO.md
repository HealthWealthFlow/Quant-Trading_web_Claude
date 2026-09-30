# TODO

Milestone status lives in `PROJECT_STATE.json` (run `python -m qsd.cli status`). This file holds finer tasks.

## M4 — Discovery connectors (next)
- [ ] Connector interface: search(query, limit) → list[Candidate(title, authors, date, url, pdf_url, abstract,
      source_type, venue, ids{doi,arxiv,ssrn})]; metadata only, UNKNOWN when absent
- [ ] arXiv API (q-fin categories, Atom), OpenAlex (works search, polite `mailto` config), Crossref (works),
      RSS/Atom feeds, user-provided URL lists
- [ ] Query families per asset class × strategy family (spec §39) + query expansion vocabulary (spec §40)
- [ ] Search memory: skip identical (connector, normalized query) within a freshness window; record results/useful
- [ ] Campaign budget counters (search requests, URLs, documents) that stop discovery when exhausted (spec §45)
- [ ] Initial source tiering from domain lists (spec §24) — deterministic, no AI
- [ ] Tests with recorded/mock API responses; `qsd discover "<query>" --connector arxiv`

## Later milestones — market-regime grouping (§137)
- [ ] M5: extraction prompt returns regime suitability + basis + confidence; UNKNOWN by default
- [ ] M6: flag RATIONALE_INFERRED-only regimes for review; regime coverage in diversification tags
- [ ] M7: regime profile in research package
- [ ] M8: dashboard grouping/filter by regime (Long / Short / Consolidation / Crash)
- [ ] M9: campaigns can target a regime
