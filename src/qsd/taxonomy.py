"""Classification vocabularies shared across the pipeline."""

from __future__ import annotations

from enum import StrEnum


class MarketRegime(StrEnum):
    """Market direction/state a strategy is designed for (spec §137).

    Distinct from position direction (long/short, spec §46): a short-selling strategy can be designed for a
    BULLISH market (e.g. shorting laggards), and a long-only strategy for a CRASH (e.g. buying puts or bonds).
    """

    BULLISH = "BULLISH"              # "Long" market: sustained uptrend
    BEARISH = "BEARISH"              # "Short" market: sustained downtrend
    CONSOLIDATION = "CONSOLIDATION"  # sideways / range-bound
    CRASH = "CRASH"                  # sharp, fast decline / crisis / volatility spike


REGIME_LABELS = {
    MarketRegime.BULLISH: "Long (uptrend)",
    MarketRegime.BEARISH: "Short (downtrend)",
    MarketRegime.CONSOLIDATION: "Consolidation (sideways)",
    MarketRegime.CRASH: "Crash (crisis)",
}


class RegimeSuitability(StrEnum):
    SUITED = "SUITED"
    UNSUITED = "UNSUITED"      # source says or shows it fails in this regime
    UNKNOWN = "UNKNOWN"        # default: never guessed (spec §1, §47)


class RegimeBasis(StrEnum):
    """Where a regime label came from. Only SOURCE_* bases count as evidence."""

    SOURCE_STATED = "SOURCE_STATED"          # source explicitly says which regime it targets
    SOURCE_EVIDENCE = "SOURCE_EVIDENCE"      # source reports results split by regime (a claim, not verified)
    RATIONALE_INFERRED = "RATIONALE_INFERRED"  # inferred from the mechanism; needs confidence + review
    UNKNOWN = "UNKNOWN"


def default_regime_profile() -> dict[MarketRegime, dict[str, object]]:
    """Every idea starts with all four regimes UNKNOWN until extraction finds support."""
    return {
        r: {"suitability": RegimeSuitability.UNKNOWN, "basis": RegimeBasis.UNKNOWN, "confidence": 0.0,
            "source_fact_id": None}
        for r in MarketRegime
    }
