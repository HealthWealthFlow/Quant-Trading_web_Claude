# TODO

Milestone status lives in `PROJECT_STATE.json` (run `python -m qsd.cli status`). This file holds finer tasks.

## M2 — Source handlers (next)
- [ ] `SourceHandler` interface (spec §6) + registry; `HandlerResult` with text, structure, links, references,
      tables, metadata (UNKNOWN when absent), limitations, sha256
- [ ] Handlers: PlainText/Markdown, HTML (trafilatura), PDF (pdfplumber; encrypted → UNSUPPORTED/limitation),
      DOCX, PPTX (slides, notes, tables; charts → UNREADABLE_CHART_DATA), CSV/XLSX, EPUB
- [ ] Location tracking (page / slide / section) for later fact provenance
- [ ] Untrusted-content boundary wrapper (spec §8)
- [ ] Safe archive extraction (size/count limits, path traversal, zip bombs) (spec §101)
- [ ] Local folder scan (read-only) → `local_files` index (spec §111, §112)
- [ ] Tests incl. failure cases: bad PDF, encrypted PDF, invalid HTML, malicious ZIP, injection text

## Later milestones — market-regime grouping (§137)
- [ ] M5: extraction prompt returns regime suitability + basis + confidence; UNKNOWN by default
- [ ] M6: flag RATIONALE_INFERRED-only regimes for review; regime coverage in diversification tags
- [ ] M7: regime profile in research package
- [ ] M8: dashboard grouping/filter by regime (Long / Short / Consolidation / Crash)
- [ ] M9: campaigns can target a regime
