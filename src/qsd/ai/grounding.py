"""Anti-fabrication checks on AI output (spec §1, §47, §82, §137). Deterministic, no AI.

Every non-UNKNOWN value must be backed by a quote that really occurs in the source text, and every number in a value
must occur in the source. Anything that fails is reset to UNKNOWN, recorded (value + quote + reason, so a human can
check it) and flagged — never kept "just in case".

Quote matching is exact after normalisation (case, whitespace, typographic quotes/dashes, PDF hyphenation). Some
PDFs extract with spaces missing between words ("Thissuggeststhat..."), so a quote of at least `MIN_COMPACT_CHARS`
characters also counts when its characters, ignoring all whitespace, occur in the source in the same order. Models
often copy a sentence with small slips (a dropped "the", changed punctuation), so a quote of at least
`MIN_FUZZY_WORDS` words is also accepted when almost all of its words occur *in order* in one short stretch of the
source and every number in the quote occurs in that stretch. The stored quote is then replaced by the source's own
wording, so what is kept is always verbatim source text.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from ..taxonomy import UNKNOWN, RegimeBasis, RegimeSuitability
from .schemas import RULE_FIELDS, Evidenced, ExtractedStrategy, Parameter

MAX_QUOTE = 300
INFERRED_CONFIDENCE_CAP = 0.5

MIN_COMPACT_CHARS = 12      # space-insensitive matching only for quotes this long
MIN_FUZZY_WORDS = 6          # shorter quotes must match exactly
FUZZY_MIN_SHARE = 0.85       # share of the quote's words that must be found, in order
FUZZY_MAX_STRETCH = 1.25     # matched stretch may be at most this much longer than the quote (+2 words)
_MAX_POSITIONS = 400         # very common words don't vote for alignment candidates

REVIEW_MIN_ATTEMPTED = 4     # don't judge a model's reliability on fewer values than this
REVIEW_PROBLEM_SHARE = 0.5   # at least half of the offered values failed → a human should look

_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_TRANS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                        "−": "-", " ": " "})
_ELLIPSIS = re.compile(r"\.\.\.|…|\[\.\.\.\]")


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").translate(_TRANS)
    return re.sub(r"-\s*\n\s*", "", text)  # de-hyphenate PDF line breaks


def normalize(text: str) -> str:
    return " ".join(_clean(text).lower().split())


def _key(word: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", word.lower())


def _numbers(text: str) -> set[str]:
    return {n.replace(",", ".") for n in _NUM.findall(text or "")}


def _compact(text: str) -> str:
    """Normalised text without any whitespace: PDF extraction can drop or add spaces between words."""
    return re.sub(r"\s+", "", normalize(text))


def _parts(quote: str | None) -> list[str]:
    return [p for p in _ELLIPSIS.split(quote or "") if p.strip()]


class SourceIndex:
    """Normalised text plus a word index of one source, built once per extraction."""

    def __init__(self, text: str):
        self.norm = normalize(text)
        self.compact = re.sub(r"\s+", "", self.norm)
        self.words = [w for w in _clean(text).split() if _key(w)]
        self.keys = [_key(w) for w in self.words]
        self.positions: dict[str, list[int]] = {}
        for i, k in enumerate(self.keys):
            self.positions.setdefault(k, []).append(i)

    def find(self, quote: str | None, value: str = "") -> str | None:
        """Return the source's own wording for `quote`, or None when it does not occur in the source.

        With approximate matching, a quote word that is missing from the source may not be one the value relies on
        ("monthly" in the quote and the value, "annually" in the source → rejected)."""
        parts = _parts(quote)
        if not parts:
            return None
        found = []
        for p in parts:
            if normalize(p) in self.norm:
                found.append(p.strip())
                continue
            if len(_compact(p)) >= MIN_COMPACT_CHARS and _compact(p) in self.compact:
                found.append(p.strip())  # same characters in the same order; only spacing differs
                continue
            stretch = self._align(p, {_key(w) for w in _clean(value).split()})
            if stretch is None:
                return None
            found.append(stretch)
        return " … ".join(found)

    def _align(self, part: str, value_keys: set[str] = frozenset()) -> str | None:
        q = [k for k in (_key(w) for w in _clean(part).split()) if k]
        n = len(q)
        if n < MIN_FUZZY_WORDS:
            return None
        votes: Counter[int] = Counter()
        for i, k in enumerate(q):
            pos = self.positions.get(k, ())
            if len(pos) <= _MAX_POSITIONS:
                for p in pos:
                    votes[p - i] += 1
        slack = max(3, n // 4)
        for start, _ in votes.most_common(5):
            lo, hi = max(0, start - slack), min(len(self.keys), start + n + slack)
            blocks = [b for b in SequenceMatcher(None, q, self.keys[lo:hi], autojunk=False).get_matching_blocks()
                      if b.size]
            if not blocks or sum(b.size for b in blocks) / n < FUZZY_MIN_SHARE:
                continue
            matched = {b.a + j for b in blocks for j in range(b.size)}
            if {q[i] for i in range(n) if i not in matched} & value_keys:
                continue  # the value depends on a word the source does not have here
            a, b = lo + blocks[0].b, lo + blocks[-1].b + blocks[-1].size
            if b - a > n * FUZZY_MAX_STRETCH + 2:
                continue
            stretch = " ".join(self.words[a:b])
            if not _numbers(_clean(part)) <= _numbers(stretch):
                continue  # numbers are never approximated
            return stretch
        return None

    def closest(self, quote: str, context_words: int = 8) -> tuple[float, str]:
        """Diagnostics only: the best-matching stretch for `quote` and the share of its words found there, in
        order, with no thresholds applied. Used to explain why a quote was rejected."""
        q = [k for k in (_key(w) for w in _clean(quote).split()) if k]
        if not q or not self.keys:
            return 0.0, ""
        votes: Counter[int] = Counter()
        for i, k in enumerate(q):
            for p in self.positions.get(k, ()):
                votes[p - i] += 1
        best = (0.0, "")
        for start, _ in votes.most_common(5):
            lo, hi = max(0, start - 2 * len(q)), min(len(self.keys), start + 3 * len(q))
            blocks = [b for b in SequenceMatcher(None, q, self.keys[lo:hi], autojunk=False).get_matching_blocks()
                      if b.size]
            share = sum(b.size for b in blocks) / len(q)
            if blocks and share > best[0]:
                a, b = lo + blocks[0].b, lo + blocks[-1].b + blocks[-1].size
                a, b = max(0, a - context_words), min(len(self.words), b + context_words)
                best = (share, " ".join(self.words[a:b]))
        return best


def quote_in_source(quote: str | None, source_norm: str) -> bool:
    """Exact (normalised) match only; used where no index is built, e.g. abstracts in relation checks."""
    parts = _parts(quote)
    compact = re.sub(r"\s+", "", source_norm)
    return bool(parts) and all(normalize(p) in source_norm or
                               (len(_compact(p)) >= MIN_COMPACT_CHARS and _compact(p) in compact) for p in parts)


def numbers_supported(value: str, source_norm: str) -> bool:
    return _numbers(value) <= _numbers(source_norm)


@dataclass
class GroundingReport:
    flags: list[str] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)  # what was thrown away and why, for human review
    attempted: int = 0                                 # non-UNKNOWN values the model offered
    problems: int = 0                                  # removed values + downgraded regime judgements
    realigned: int = 0                                 # quotes accepted after word alignment

    @property
    def needs_review(self) -> bool:
        """Removed values are already harmless; review is for a model that was unreliable on this source."""
        return self.attempted >= REVIEW_MIN_ATTEMPTED and self.problems / self.attempted >= REVIEW_PROBLEM_SHARE


class _Grounder:
    def __init__(self, source_text: str):
        self.idx = SourceIndex(source_text)
        self.report = GroundingReport()

    def remove(self, flag: str, label: str, f: Evidenced, reason: str, field_name: str | None = None) -> None:
        self.report.flags.append(f"{flag}:{label}")
        self.report.problems += 1
        self.report.removed.append({"field": field_name or label, "value": f.value[:500],
                                    "quote": (f.evidence_quote or None) and f.evidence_quote[:MAX_QUOTE],
                                    "location": f.location, "reason": reason})

    def _note_realigned(self, found: str, quote: str | None, label: str) -> None:
        if normalize(found) != normalize(quote or ""):
            self.report.realigned += 1
            self.report.flags.append(f"QUOTE_REALIGNED:{label}")

    def verbatim(self, quote: str | None, label: str, value: str = "") -> str | None:
        found = self.idx.find(quote, value)
        if found is not None:
            self._note_realigned(found, quote, label)
        return found

    def ground(self, f: Evidenced, label: str) -> Evidenced:
        if f.value == UNKNOWN:
            f.evidence_quote = None
            return f
        self.report.attempted += 1
        found = self.idx.find(f.evidence_quote, f.value)
        if found is None:
            self.remove("UNGROUNDED_VALUE_REMOVED", label, f, "QUOTE_NOT_FOUND")
            return Evidenced(value=UNKNOWN)
        if not numbers_supported(f.value, self.idx.norm):
            self.remove("UNSUPPORTED_NUMBER_REMOVED", label, f, "NUMBER_NOT_IN_SOURCE")
            return Evidenced(value=UNKNOWN)
        self._note_realigned(found, f.evidence_quote, label)
        f.evidence_quote = found[:MAX_QUOTE]
        return f


def ground_strategy(s: ExtractedStrategy, source_text: str) -> GroundingReport:
    """Mutates `s` in place; returns what was kept, removed and flagged."""
    g = _Grounder(source_text)
    s.rules = {k: g.ground(v, k) for k, v in s.rules.items()}
    for key in RULE_FIELDS:
        s.rules.setdefault(key, Evidenced())
    params = []
    for p in s.parameters:
        grounded = g.ground(p, f"parameter:{p.name}")
        # Keep the parameter's name even when its value could not be verified: "lookback: UNKNOWN" is useful.
        params.append(p if grounded is p else Parameter(name=p.name))
    s.parameters = params
    s.rationale = g.ground(s.rationale, "rationale")
    kept_claims = {}
    for k, v in s.claims.items():
        offered = v.model_copy()
        c = g.ground(v, k)
        # A claim's number must be inside its own quote, not merely somewhere in the document.
        if c.value != UNKNOWN and not _numbers(c.value) <= _numbers(normalize(c.evidence_quote or "")):
            g.remove("UNSUPPORTED_CLAIM_REMOVED", k, offered, "CLAIM_NUMBER_NOT_IN_QUOTE")
            continue
        if c.value != UNKNOWN:
            kept_claims[k] = c
    s.claims = kept_claims
    s.failure_modes_from_source = [f for f in (g.ground(f, "failure_mode") for f in s.failure_modes_from_source)
                                   if f.value != UNKNOWN]

    for r in s.regimes:
        if r.basis in (RegimeBasis.SOURCE_STATED, RegimeBasis.SOURCE_EVIDENCE):
            g.report.attempted += 1
            found = g.verbatim(r.evidence_quote, f"regime:{r.regime.value}", r.regime.value.lower())
            if found is None:
                g.remove("REGIME_EVIDENCE_NOT_FOUND", r.regime.value,
                         Evidenced(value=r.suitability.value, evidence_quote=r.evidence_quote, location=r.location),
                         "QUOTE_NOT_FOUND; downgraded to RATIONALE_INFERRED", field_name=f"regime:{r.regime.value}")
                r.basis = RegimeBasis.RATIONALE_INFERRED
                r.evidence_quote = None
            else:
                r.evidence_quote = found[:MAX_QUOTE]
        if r.basis is RegimeBasis.RATIONALE_INFERRED:
            r.confidence = min(r.confidence, INFERRED_CONFIDENCE_CAP)
        if r.basis is RegimeBasis.UNKNOWN:
            r.suitability, r.confidence = RegimeSuitability.UNKNOWN, 0.0

    s.unknown_rules = sorted({k for k, v in s.rules.items() if v.value == UNKNOWN} | set(s.unknown_rules))
    return g.report
