# Decisions

| # | Date | Decision | Reason |
|---|---|---|---|
| D1 | 2026-09-30 | Python 3.11+, src layout, setuptools | Best ecosystem for parsing, data and quant work |
| D2 | 2026-09-30 | SQLite first (via SQLAlchemy in M1), Postgres in Phase 2 | Zero-ops, runs on Windows PC or server |
| D3 | 2026-09-30 | Dashboard: FastAPI + server-rendered templates, no SPA | Minimal, cheap to build and maintain (§113) |
| D4 | 2026-09-30 | AI: provider adapters, DeepSeek default; OpenAI/Anthropic optional | Spec §88; avoid lock-in |
| D5 | 2026-09-30 | Discovery starts with free official APIs (arXiv, OpenAlex, Crossref, RSS, user URLs) | No search-API key needed; preferred access order §11 |
| D6 | 2026-09-30 | Avoid AGPL parsers (e.g. PyMuPDF); prefer pdfplumber/pypdf | License safety (§100, §109) |
| D7 | 2026-09-30 | robots.txt respect cannot be disabled via config | Spec §104, §134 |
| D8 | 2026-09-30 | Cross-platform paths (Windows + Linux) | User may run on Windows PC or a server |
| D9 | 2026-09-30 | Phase 1 = milestones M0–M9; Phase 2 backlog in PROJECT_STATE.json | Claude Code build budget capped at ~$30 |
| D10 | 2026-09-30 | Market-regime grouping (Bullish/Bearish/Consolidation/Crash) as its own multi-label dimension with suitability + basis + confidence, separate from position long/short | User requirement §137; avoids mixing market state with trade direction |
| D11 | 2026-09-30 | HTML via BeautifulSoup+lxml (no trafilatura); EPUB via safe-zip + HTML parser (no EbookLib, AGPL) | Fewer/lighter deps, license safety |
| D12 | 2026-09-30 | File-creation dates stored as `file_created_date`, never as publication date | Template dates would be fabricated publication dates (spec §1) |
| D13 | 2026-09-30 | Official APIs (arXiv, OpenAlex, Crossref) use their API terms/rate limits instead of robots.txt, via a code-level endpoint allowlist | robots.txt targets crawlers; API priority per §11; allowlist can't be widened by config |
| D14 | 2026-09-30 | Work identity: DOI > arXiv id > URL as canonical link | Cross-connector dedupe without a schema change |
| D15 | 2026-09-30 | Claude adapter uses the official `anthropic` SDK (optional extra), model `claude-opus-5-5`, server-side refusal fallback "default" | Current API guidance; Claude is optional (second opinion) |
| D16 | 2026-09-30 | Models without a configured price are never called; DeepSeek/OpenAI prices ship unset | Budgets need prices; avoid stale/guessed prices |
| D17 | 2026-09-30 | Deterministic grounding after every extraction (verbatim quote + number checks) | AI output can't be trusted to follow no-fabrication rules by itself |
| D18 | 2026-09-30 | Default strong model = deepseek-chat (same as cheap) | Uncertain JSON-mode support of other DeepSeek models; user can change in config |
| D19 | 2026-09-30 | Unassessable score components are UNSCORED with coverage tracked; low-coverage ideas go to RESEARCHING, not ARCHIVED | Avoids both guessing and burying ideas for lack of data |
| D20 | 2026-09-30 | Additive-only schema migrations (MIGRATIONS map) | Research data must never be dropped |
| D21 | 2026-09-30 | Backtest queue is a folder of JSON files (pending/) with a published JSON Schema | Simple, inspectable, decoupled from the Quant Auto OS; no network path to brokers |
| D22 | 2026-09-30 | Found papers are linked as SUPPORTS/REPLICATES/CONTRADICTS only after a grounded abstract check | A search hit is not evidence; avoids fabricated evidence links |
| D23 | 2026-09-30 | One CampaignBudget shared by discovery, fetching and the AI gateway; progress in campaigns.state | Hard caps across all spend; safe resume |
