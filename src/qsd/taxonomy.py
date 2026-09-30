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


class MissingValue(StrEnum):
    """Explicit markers for missing information (spec §0). Never replaced by a guess."""

    UNKNOWN = "UNKNOWN"
    NOT_PROVIDED = "NOT_PROVIDED"
    NOT_VERIFIED = "NOT_VERIFIED"
    INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"
    ACCESS_RESTRICTED = "ACCESS_RESTRICTED"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


UNKNOWN = MissingValue.UNKNOWN.value


class AssetClass(StrEnum):
    STOCK = "STOCK"
    ETF = "ETF"
    OPTIONS = "OPTIONS"
    FOREX = "FOREX"
    CRYPTO = "CRYPTO"
    FUTURES = "FUTURES"
    COMMODITIES = "COMMODITIES"
    FIXED_INCOME = "FIXED_INCOME"
    VOLATILITY = "VOLATILITY"
    MULTI_ASSET = "MULTI_ASSET"


class AccessStatus(StrEnum):
    """Spec §10, §105–§107."""

    OK = "OK"
    ACCESS_RESTRICTED = "ACCESS_RESTRICTED"
    MANUAL_ACCESS_REQUIRED = "MANUAL_ACCESS_REQUIRED"    # CAPTCHA
    PRIVATE_ACCESS_REQUIRED = "PRIVATE_ACCESS_REQUIRED"
    PAYWALLED = "PAYWALLED"                              # metadata/abstract only
    ROBOTS_DISALLOWED = "ROBOTS_DISALLOWED"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    NOT_FETCHED = "NOT_FETCHED"
    ERROR = "ERROR"


class RequestClass(StrEnum):
    """Spec §14. Only PUBLIC_* is processed automatically."""

    PUBLIC_STATIC = "PUBLIC_STATIC"
    PUBLIC_API = "PUBLIC_API"
    AUTHORIZED_SESSION = "AUTHORIZED_SESSION"
    SESSION_PRIVATE = "SESSION_PRIVATE"
    SENSITIVE_AUTH = "SENSITIVE_AUTH"
    UNKNOWN = "UNKNOWN"


class ExtractionMethod(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    AI_CHEAP = "AI_CHEAP"
    AI_STRONG = "AI_STRONG"
    AI_SECOND_OPINION = "AI_SECOND_OPINION"
    MANUAL = "MANUAL"


class SourceRelation(StrEnum):
    """Source → source edges (spec §29, §70, §71)."""

    CITES = "CITES"
    REPLICATES = "REPLICATES"
    DERIVED_FROM = "DERIVED_FROM"
    CONTRADICTS = "CONTRADICTS"


class IdeaSourceRole(StrEnum):
    """Source → strategy edges (spec §29, §122)."""

    DESCRIBES = "DESCRIBES"
    ORIGINAL = "ORIGINAL"
    SUPPORTS = "SUPPORTS"
    REPLICATES = "REPLICATES"
    CONTRADICTS = "CONTRADICTS"


class IdeaStatus(StrEnum):
    """Spec §91."""

    DISCOVERED = "DISCOVERED"
    FILTERING = "FILTERING"
    RESEARCHING = "RESEARCHING"
    PROMISING = "PROMISING"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    READY_FOR_FORMALIZATION = "READY_FOR_FORMALIZATION"
    DUPLICATE = "DUPLICATE"
    REJECTED = "REJECTED"
    ARCHIVED = "ARCHIVED"
    SUBMITTED_TO_BACKTEST = "SUBMITTED_TO_BACKTEST"


class RejectionReason(StrEnum):
    """Spec §92 reasons plus §53 hard fails. Rejections are never deleted."""

    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    NO_RATIONALE = "NO_RATIONALE"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    LOOKAHEAD = "LOOKAHEAD"
    DUPLICATE = "DUPLICATE"
    IMPOSSIBLE_EXECUTION = "IMPOSSIBLE_EXECUTION"
    MARTINGALE = "MARTINGALE"
    EXCESS_COMPLEXITY = "EXCESS_COMPLEXITY"
    UNRELIABLE_SOURCE = "UNRELIABLE_SOURCE"
    HIGH_TAIL_RISK = "HIGH_TAIL_RISK"
    NO_EXIT = "NO_EXIT"
    BROKER_INCOMPATIBLE = "BROKER_INCOMPATIBLE"
    # §53 hard fails
    LOOKAHEAD_REQUIRED = "LOOKAHEAD_REQUIRED"
    FUTURE_INFORMATION_REQUIRED = "FUTURE_INFORMATION_REQUIRED"
    DATA_NOT_AVAILABLE = "DATA_NOT_AVAILABLE"
    RULES_NOT_QUANTIFIABLE = "RULES_NOT_QUANTIFIABLE"
    NO_REALISTIC_EXIT = "NO_REALISTIC_EXIT"
    SEVERE_LIQUIDITY_PROBLEM = "SEVERE_LIQUIDITY_PROBLEM"
    EXPECTED_COST_GT_EDGE = "EXPECTED_COST_GT_EDGE"
    UNLIMITED_AVERAGING = "UNLIMITED_AVERAGING"
    UNBOUNDED_RISK = "UNBOUNDED_RISK"
    CLEAR_FABRICATION = "CLEAR_FABRICATION"
    INVALID_SOURCE = "INVALID_SOURCE"
    BROKER_UNSUPPORTED = "BROKER_UNSUPPORTED"
    OBVIOUS_SURVIVORSHIP_BIAS = "OBVIOUS_SURVIVORSHIP_BIAS"
    UNREPRODUCIBLE_CRITICAL_INPUT = "UNREPRODUCIBLE_CRITICAL_INPUT"
    SCAM_SIGNAL_SOURCE = "SCAM_SIGNAL_SOURCE"


class PositionDirection(StrEnum):
    """Trade direction (spec §46). Not the market regime (§137)."""

    LONG = "LONG"
    SHORT = "SHORT"
    LONG_SHORT = "LONG_SHORT"
    UNKNOWN = "UNKNOWN"


class TimeHorizon(StrEnum):
    """Spec §67."""

    HFT = "HFT"
    SECONDS = "SECONDS"
    MINUTES = "MINUTES"
    INTRADAY = "INTRADAY"
    MULTI_DAY = "MULTI_DAY"
    SWING = "SWING"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    LONG_TERM = "LONG_TERM"
    UNKNOWN = "UNKNOWN"


class CampaignStatus(StrEnum):
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"     # stop condition hit (§44); reason stored separately
    FAILED = "FAILED"


class ErrorState(StrEnum):
    """Spec §133: no silent failures."""

    UNRESOLVED = "UNRESOLVED"
    RETRYING = "RETRYING"
    RESOLVED = "RESOLVED"
    GAVE_UP = "GAVE_UP"
