"""Strict output schemas for AI extraction (spec §46, §86, §87, §137). Invalid enum values degrade to UNKNOWN
rather than being guessed; unexpected keys are ignored.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..taxonomy import (
    UNKNOWN,
    AssetClass,
    MarketRegime,
    PositionDirection,
    RegimeBasis,
    RegimeSuitability,
    TimeHorizon,
)

RULE_FIELDS = (
    "instrument", "universe", "timeframe", "data_frequency", "indicators", "signal", "lookback", "entry_rule",
    "exit_rule", "stop_rule", "take_profit_rule", "position_sizing", "rebalance", "order_type", "trading_session",
    "holding_period", "transaction_cost_assumption", "liquidity_requirement", "portfolio_rules", "risk_rules",
)
CLAIM_FIELDS = ("claimed_sharpe", "claimed_cagr", "claimed_max_dd", "claimed_win_rate", "claimed_pf")


def _enum_or_unknown(enum_cls, value: Any):
    try:
        return enum_cls(str(value).upper())
    except ValueError:
        return enum_cls("UNKNOWN")


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Evidenced(_Base):
    value: str = UNKNOWN
    evidence_quote: str | None = None  # verbatim from the source
    location: str | None = None        # e.g. "p.14", "slide 3"
    confidence: float = Field(0.0, ge=0.0, le=1.0)

    @field_validator("value", mode="before")
    @classmethod
    def _text(cls, v: Any) -> str:
        v = "" if v is None else str(v).strip()
        return v or UNKNOWN

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, v: Any) -> float:
        try:
            return min(1.0, max(0.0, float(v)))
        except (TypeError, ValueError):
            return 0.0


class Parameter(Evidenced):
    name: str


class RegimeJudgement(_Base):
    regime: MarketRegime
    suitability: RegimeSuitability = RegimeSuitability.UNKNOWN
    basis: RegimeBasis = RegimeBasis.UNKNOWN
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    evidence_quote: str | None = None
    location: str | None = None

    @field_validator("suitability", mode="before")
    @classmethod
    def _s(cls, v: Any):
        return _enum_or_unknown(RegimeSuitability, v)

    @field_validator("basis", mode="before")
    @classmethod
    def _b(cls, v: Any):
        return _enum_or_unknown(RegimeBasis, v)

    @field_validator("confidence", mode="before")
    @classmethod
    def _c(cls, v: Any) -> float:
        return Evidenced._clamp(v)


def _asset_list(v: Any) -> list[AssetClass]:
    out = []
    for item in v or []:
        try:
            out.append(AssetClass(str(item).upper()))
        except ValueError:
            continue
    return list(dict.fromkeys(out))


class StageAResult(_Base):
    """Cheap triage on metadata / first sections (spec §86 stage A)."""

    is_strategy_research: bool = False
    asset_classes: list[AssetClass] = Field(default_factory=list)
    strategy_families: list[str] = Field(default_factory=list)
    basic_idea: str = UNKNOWN
    red_flags: list[str] = Field(default_factory=list)
    worth_deep_read: bool = False
    confidence: float = Field(0.0, ge=0.0, le=1.0)

    @field_validator("asset_classes", mode="before")
    @classmethod
    def _assets(cls, v: Any) -> list[AssetClass]:
        return _asset_list(v)

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, v: Any) -> float:
        return Evidenced._clamp(v)


class ExtractedStrategy(_Base):
    strategy_name: str = UNKNOWN
    summary: str = UNKNOWN
    asset_classes: list[AssetClass] = Field(default_factory=list)
    strategy_families: list[str] = Field(default_factory=list)
    position_direction: PositionDirection = PositionDirection.UNKNOWN
    time_horizon: TimeHorizon = TimeHorizon.UNKNOWN
    rules: dict[str, Evidenced] = Field(default_factory=dict)
    parameters: list[Parameter] = Field(default_factory=list)
    rationale: Evidenced = Field(default_factory=Evidenced)
    claims: dict[str, Evidenced] = Field(default_factory=dict)
    regimes: list[RegimeJudgement] = Field(default_factory=list)
    data_required: list[str] = Field(default_factory=list)
    failure_modes_from_source: list[Evidenced] = Field(default_factory=list)
    unknown_rules: list[str] = Field(default_factory=list)

    @field_validator("asset_classes", mode="before")
    @classmethod
    def _assets(cls, v: Any) -> list[AssetClass]:
        return _asset_list(v)

    @field_validator("position_direction", mode="before")
    @classmethod
    def _pd(cls, v: Any):
        return _enum_or_unknown(PositionDirection, v)

    @field_validator("time_horizon", mode="before")
    @classmethod
    def _th(cls, v: Any):
        return _enum_or_unknown(TimeHorizon, v)

    @field_validator("rules", mode="before")
    @classmethod
    def _rules(cls, v: Any) -> dict:
        return {k: val for k, val in (v or {}).items() if k in RULE_FIELDS}

    @field_validator("claims", mode="before")
    @classmethod
    def _claims(cls, v: Any) -> dict:
        return {k: val for k, val in (v or {}).items() if k in CLAIM_FIELDS}

    @field_validator("regimes", mode="before")
    @classmethod
    def _regimes(cls, v: Any) -> list:
        out = []
        for item in v or []:
            try:
                MarketRegime(str((item or {}).get("regime", "")).upper())
            except ValueError:
                continue
            item = dict(item)
            item["regime"] = str(item["regime"]).upper()
            out.append(item)
        return out


class StageBResult(_Base):
    """Strong-model extraction (spec §86 stage B). May contain several distinct strategies."""

    strategies: list[ExtractedStrategy] = Field(default_factory=list)
