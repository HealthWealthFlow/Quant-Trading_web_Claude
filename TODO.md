# TODO

Milestone status lives in `PROJECT_STATE.json` (run `python -m qsd.cli status`). This file holds finer tasks.

## M1 — Database schema (next)
- [ ] SQLAlchemy models: sources, fetch_log, source_facts (provenance: page/slide/timestamp/section, method, confidence)
- [ ] ideas (spec §90 fields, `CLAIMED_*` metrics, UNKNOWN defaults), idea_status history, rejected reasons (§92)
- [ ] search_queries / search memory (§93), ai_calls ledger (§120), errors (§133), campaigns
- [ ] Schema versioning + `qsd db init` command
- [ ] Tests: create/read round-trip, UNKNOWN defaults, no secret columns
