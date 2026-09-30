"""Anti-fabrication checks on AI output (spec §1, §47, §82, §137). Deterministic, no AI.

Every non-UNKNOWN value must be backed by a verbatim quote that really occurs in the source text, and every number
in a value must occur in the source. Anything that fails is reset to UNKNOWN and flagged — never kept "just in case".
"""

from __future__ import annotations

import re
import unicodedata

from ..taxonomy import UNKNOWN, RegimeBasis, RegimeSuitability
from .schemas import RULE_FIELDS, Evidenced, ExtractedStrategy, Parameter

MAX_QUOTE = 300
INFERRED_CONFIDENCE_CAP = 0.5

_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_TRANS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                        "−": "-", " ": " "})


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").translate(_TRANS).lower()
    text = re.sub(r"-\s*\n\s*", "", text)  # de-hyphenate PDF line breaks
    return " ".join(text.split())


def quote_in_source(quote: str | None, source_norm: str) -> bool:
    if not quote or not quote.strip():
        return False
    parts = [p for p in re.split(r"\.\.\.|…|\[\.\.\.\]", quote) if p.strip()]
    return bool(parts) and all(normalize(p) in source_norm for p in parts)


def _numbers(text: str) -> set[str]:
    return {n.replace(",", ".") for n in _NUM.findall(text or "")}


def numbers_supported(value: str, source_norm: str) -> bool:
    src_numbers = _numbers(source_norm)
    return _numbers(value) <= src_numbers


def _ground(field: Evidenced, label: str, source_norm: str, flags: list[str]) -> Evidenced:
    if field.value == UNKNOWN:
        field.evidence_quote = None
        return field
    if field.evidence_quote and len(field.evidence_quote) > MAX_QUOTE:
        field.evidence_quote = field.evidence_quote[:MAX_QUOTE]
    if not quote_in_source(field.evidence_quote, source_norm):
        flags.append(f"UNGROUNDED_VALUE_REMOVED:{label}")
        return Evidenced(value=UNKNOWN)
    if not numbers_supported(field.value, source_norm):
        flags.append(f"UNSUPPORTED_NUMBER_REMOVED:{label}")
        return Evidenced(value=UNKNOWN)
    return field


def ground_strategy(s: ExtractedStrategy, source_text: str) -> list[str]:
    """Mutates `s` in place; returns the list of flags raised."""
    src = normalize(source_text)
    flags: list[str] = []
    s.rules = {k: _ground(v, k, src, flags) for k, v in s.rules.items()}
    for key in RULE_FIELDS:
        s.rules.setdefault(key, Evidenced())
    params = []
    for p in s.parameters:
        grounded = _ground(p, f"parameter:{p.name}", src, flags)
        # Keep the parameter's name even when its value could not be verified: "lookback: UNKNOWN" is useful.
        params.append(p if grounded is p else Parameter(name=p.name))
    s.parameters = params
    s.rationale = _ground(s.rationale, "rationale", src, flags)
    kept_claims = {}
    for k, v in s.claims.items():
        g = _ground(v, k, src, flags)
        # A claim's number must be inside its own quote, not merely somewhere in the document.
        if g.value != UNKNOWN and not _numbers(g.value) <= _numbers(normalize(g.evidence_quote or "")):
            flags.append(f"UNSUPPORTED_CLAIM_REMOVED:{k}")
            continue
        if g.value != UNKNOWN:
            kept_claims[k] = g
    s.claims = kept_claims
    s.failure_modes_from_source = [f for f in (_ground(f, "failure_mode", src, flags)
                                               for f in s.failure_modes_from_source) if f.value != UNKNOWN]

    for r in s.regimes:
        if r.basis in (RegimeBasis.SOURCE_STATED, RegimeBasis.SOURCE_EVIDENCE) and \
                not quote_in_source(r.evidence_quote, src):
            flags.append(f"REGIME_EVIDENCE_NOT_FOUND:{r.regime.value}")
            r.basis = RegimeBasis.RATIONALE_INFERRED
            r.evidence_quote = None
        if r.basis is RegimeBasis.RATIONALE_INFERRED:
            r.confidence = min(r.confidence, INFERRED_CONFIDENCE_CAP)
        if r.basis is RegimeBasis.UNKNOWN:
            r.suitability, r.confidence = RegimeSuitability.UNKNOWN, 0.0

    s.unknown_rules = sorted({k for k, v in s.rules.items() if v.value == UNKNOWN} | set(s.unknown_rules))
    return flags
