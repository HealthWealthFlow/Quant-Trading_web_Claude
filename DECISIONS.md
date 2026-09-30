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
