"""Evidence signals read from the whole document (spec §26, §81). Deterministic, no AI.

Why this exists — a measured blind spot: robustness and evidence quality were detected only in the abstract, the
idea summary and the AI's own quotes. A paper that reports an out-of-sample test in section 5 scored
`expected_robustness = 0` unless the model happened to quote that sentence (measured: 0.00 on 14 of 15 ideas).

Rule: a signal counts only from a sentence in which the source speaks about its *own* work ("we", "our",
"this paper/study") and that matches the signal's pattern. A literature review that mentions someone else's
out-of-sample test does not earn the paper credit. Each match is stored verbatim with its location, so every point of
evidence a score uses can be read on the dashboard and checked against the document.
"""

from __future__ import annotations

import re

from sqlalchemy import delete

from ..db.models import SourceFact
from ..handlers import HandlerResult
from ..taxonomy import ExtractionMethod

FACT_PREFIX = "EVIDENCE:"
MAX_PER_SIGNAL = 3
MAX_SENTENCE = 400

SIGNALS: dict[str, re.Pattern] = {
    "out_of_sample": re.compile(r"out(-\s?| )of(-\s?| )sample|walk(-\s?| )forward|hold[- ]?out (period|sample|set)|"
                                r"held[- ]out (period|sample|data|set)|test (period|sample|set)", re.I),
    "costs_considered": re.compile(r"transaction costs?|trading costs?|slippage|bid[- ]ask|commissions?", re.I),
    "code_available": re.compile(r"github\.com|source code|code is (publicly )?available|replication (code|package)",
                                 re.I),
    "sample_period": re.compile(r"\b(19|20)\d\d\s?(-|–|to|through|until)\s?(19|20)\d\d\b", re.I),
    "cross_market": re.compile(r"\binternational ((stock|equity|bond) )?(markets|samples?|evidence|data)\b|"
                               r"across (\w+ )?(countries|markets|asset classes|exchanges)|"
                               r"\b\d+ (countries|markets|asset classes|exchanges)\b", re.I),
}
# The source must be talking about its own analysis in the same sentence.
_OWN_WORK = re.compile(r"\b(we|our|this (paper|study|article|work|note|analysis))\b", re.I)
# ...and must not say it did *not* do it (measured 2026-10-04: "we assume no transaction cost", "we avoid
# incorporating transaction cost", "future research might ... model transaction costs" were all counted).
_NOT_DONE = re.compile(r"\b(no|not|never|avoid\w*|ignor\w*|neglect\w*|exclud\w*|future (research|work|studies)|"
                       r"beyond the scope|left for)\b", re.I)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[])")
_REFERENCES = re.compile(r"^\s*(references|bibliography|literature cited)\s*$", re.I | re.M)


def _sentences(text: str):
    cut = _REFERENCES.search(text)
    body = text[:cut.start()] if cut else text  # a reference list is other people's work
    for raw in _SENTENCE_END.split(" ".join(body.split())):
        sentence = raw.strip()
        if 20 <= len(sentence) <= MAX_SENTENCE:
            yield sentence


def find_evidence(result: HandlerResult) -> dict[str, list[tuple[str, str]]]:
    """{signal: [(verbatim sentence, location label), ...]} from the whole document."""
    found: dict[str, list[tuple[str, str]]] = {}
    for block in result.blocks:
        for sentence in _sentences(block.text):
            if not _OWN_WORK.search(sentence) or _NOT_DONE.search(sentence):
                continue
            for name, rx in SIGNALS.items():
                hits = found.setdefault(name, [])
                if len(hits) < MAX_PER_SIGNAL and rx.search(sentence):
                    hits.append((sentence, block.location.label()))
    return {k: v for k, v in found.items() if v}


def store_evidence(s, source_id: int, result: HandlerResult) -> dict[str, int]:
    """Replace this source's evidence facts with what the document says now. Returns {signal: sentences}."""
    s.execute(delete(SourceFact).where(SourceFact.source_id == source_id,
                                       SourceFact.fact_type.like(f"{FACT_PREFIX}%")))
    found = find_evidence(result)
    for name, hits in found.items():
        for sentence, where in hits:
            page = re.search(r"p\.(\d+)", where)
            s.add(SourceFact(source_id=source_id, fact_type=f"{FACT_PREFIX}{name}"[:40], value=name,
                             quote=sentence[:300], location=where, page=int(page.group(1)) if page else None,
                             extraction_method=ExtractionMethod.DETERMINISTIC, confidence=1.0))
    return {k: len(v) for k, v in found.items()}


def stored_signals(facts) -> tuple[list[str], list[dict]]:
    """Signals and their quotes from a source's stored facts (for scoring and the dashboard)."""
    quotes = [{"signal": f.value, "quote": f.quote, "location": f.location}
              for f in facts if f.fact_type.startswith(FACT_PREFIX)]
    return sorted({q["signal"] for q in quotes}), quotes
