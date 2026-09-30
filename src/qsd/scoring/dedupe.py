"""Strategy fingerprints and duplicate / variant detection (spec §69), root evidence (§70), novelty (§80)."""

from __future__ import annotations

import hashlib
import re

from ..taxonomy import UNKNOWN

_STOP = {"the", "a", "an", "of", "and", "or", "with", "on", "in", "to", "for", "by", "is", "are", "when", "if",
         "its", "their", "than", "that", "each", "at", "be"}


def _norm_text(value: str | None) -> str:
    if not value or value == UNKNOWN:
        return ""
    words = [w for w in re.findall(r"[a-z0-9]+", value.lower()) if w not in _STOP]
    return " ".join(sorted(set(words)))


def core_key(idea) -> str:
    """Concept identity: assets + families + signal concept (no parameters)."""
    signal = re.sub(r"\d+", "", _norm_text(idea.signal or idea.strategy_name))
    return "|".join([",".join(sorted(idea.asset_classes or [])), ",".join(sorted(f.upper() for f in
                                                                                  idea.strategy_families or [])),
                     " ".join(signal.split())])


def fingerprint(idea) -> str:
    """Full identity including parameters and rules (spec §69 fields)."""
    parts = [core_key(idea)] + [_norm_text(getattr(idea, f)) for f in
                                ("lookback", "entry_rule", "exit_rule", "holding_period", "instrument")]
    return hashlib.sha256("||".join(parts).encode()).hexdigest()


def classify(idea, others: list) -> tuple[str, int | None]:
    """Return (NEW | VARIANT | DUPLICATE, id of the matched earlier idea)."""
    fp, core = fingerprint(idea), core_key(idea)
    for o in others:
        if o.id != idea.id and o.fingerprint == fp:
            return "DUPLICATE", o.id
    for o in others:
        if o.id != idea.id and core_key(o) == core and core.split("|")[2]:
            return "VARIANT", o.id
    return "NEW", None


def novelty(dedupe_class: str, family_overlap: bool) -> float:
    if dedupe_class == "DUPLICATE":
        return 0.0
    if dedupe_class == "VARIANT":
        return 25.0
    return 60.0 if family_overlap else 100.0


def root_evidence_id(ids: dict[str, str], source_id: int) -> str:
    """One study → one root, whatever format it appears in (paper, blog, talk...)."""
    if ids.get("ID_DOI"):
        return f"doi:{ids['ID_DOI'].lower()}"
    if ids.get("ID_ARXIV"):
        return f"arxiv:{ids['ID_ARXIV']}"
    return f"source:{source_id}"
