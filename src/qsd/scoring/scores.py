"""Scores (spec §25, §26, §52, §72, §81, §126). Every score returns its components and the basis of each, so the
dashboard can show *why*. Components that cannot be assessed from stored evidence are listed as UNSCORED and add
nothing — they are never filled with a default guess. `coverage` = share of weight actually assessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..taxonomy import UNKNOWN, RegimeBasis, RegimeSuitability, TimeHorizon

# ---- source quality (spec §24, §25) -------------------------------------------------------------------

TIER_BASE = {1: 75, 2: 62, 3: 42, 4: 22, 5: 5}
UNTIERED_BASE = 30
_TRANSPARENCY = {
    "method_described": r"\b(methodology|we (construct|form|sort|define)|portfolio formation|data (set|sample))",
    "out_of_sample": r"out[- ]of[- ]sample|walk[- ]forward|hold[- ]?out",
    "costs_considered": r"transaction costs?|trading costs?|slippage|bid[- ]ask|commissions?",
    "code_available": r"github\.com|source code|code is available|replication (code|package)",
    "sample_period": r"\b(19|20)\d\d\s?(-|–|to|through)\s?(19|20)\d\d\b",
}
_TRANSPARENCY_RE = {k: re.compile(v, re.I) for k, v in _TRANSPARENCY.items()}


def transparency_signals(text: str) -> list[str]:
    return sorted(k for k, rx in _TRANSPARENCY_RE.items() if rx.search(text or ""))


def source_quality(tier: int | None, has_identifier: bool, author_known: bool, date_known: bool,
                   signals: list[str], red_flags: list[str], injection: bool) -> tuple[float, dict]:
    base = TIER_BASE.get(tier, UNTIERED_BASE) if tier else UNTIERED_BASE
    parts = {"tier_base": base, "identifier": 5 if has_identifier else 0, "author_known": 4 if author_known else 0,
             "date_known": 3 if date_known else 0, "transparency": min(15, 3 * len(signals)),
             "red_flag_penalty": -min(45, 15 * len(red_flags)), "injection_penalty": -15 if injection else 0}
    return float(max(0, min(100, sum(parts.values())))), {"components": parts, "tier": tier, "signals": signals}


# ---- evidence quality (spec §26) ------------------------------------------------------------------------

EVIDENCE_WEIGHTS = {"sample_period": 15, "method_described": 10, "out_of_sample": 20, "costs_considered": 15,
                    "code_available": 10, "replication": 20, "cross_market": 10}
_CROSS_MARKET = re.compile(r"\b(international|across (countries|markets|asset classes)|\d+ (countries|markets))\b",
                           re.I)


def evidence_quality(signals: list[str], replication_sources: int, text: str) -> tuple[float, dict]:
    present = set(signals)
    if replication_sources > 0:
        present.add("replication")
    if _CROSS_MARKET.search(text or ""):
        present.add("cross_market")
    score = sum(w for k, w in EVIDENCE_WEIGHTS.items() if k in present)
    return float(score), {"present": sorted(present & set(EVIDENCE_WEIGHTS)),
                          "missing": sorted(set(EVIDENCE_WEIGHTS) - present)}


def replication_score(independent_supporting_roots: int) -> float:
    return {0: 0.0, 1: 50.0, 2: 80.0}.get(independent_supporting_roots, 100.0)


# ---- idea quality (spec §52) ---------------------------------------------------------------------------

_LIQUID = re.compile(r"s&p ?500|large[- ]cap|nasdaq[- ]?100|\bspy\b|\bqqq\b|\bes\b futures|major (currency )?pairs|"
                     r"eur/?usd|usd/?jpy|\bbtc\b|bitcoin|\beth\b|ethereum|index futures|g10", re.I)
# Daily bars are freely available: commodities and volatility trade as futures / ETFs / indices (e.g. VIX), and
# a multi-asset portfolio is built from such series. Fixed income scores lower (D46): index and yield series are
# free, individual bond prices are not.
_BAR_DATA = {"STOCK", "ETF", "FOREX", "CRYPTO", "FUTURES", "COMMODITIES", "VOLATILITY", "MULTI_ASSET"}
_ILLIQUID = re.compile(r"micro[- ]?cap|small[- ]cap|penny|illiquid|otc\b|low[- ]volume", re.I)
_SHORT_VOL = re.compile(r"sell(ing)? (puts?|calls?|options|straddles?|strangles?|volatility)|short (vol|volatility|"
                        r"puts?|straddle|strangle)|write (puts?|calls?)|variance risk premium", re.I)


@dataclass
class Component:
    value: float | None  # 0..1, or None = UNSCORED
    basis: str


@dataclass
class IdeaScore:
    score: float
    coverage: float
    components: dict[str, Component] = field(default_factory=dict)

    @property
    def unscored(self) -> list[str]:
        return sorted(k for k, c in self.components.items() if c.value is None)

    @property
    def normalized(self) -> float | None:
        """Score over the assessed part only (for gating when coverage is sufficient)."""
        return None if self.coverage == 0 else self.score / self.coverage


_TEXT_FIELDS = ("instrument", "universe", "signal", "entry_rule", "exit_rule", "stop_rule", "risk_rules", "summary",
                # an algorithm-shaped strategy states its rule here instead of in signal/entry_rule
                "algorithm_rule")


def _rules_text(idea) -> str:
    return " ".join(str(getattr(idea, f)) for f in _TEXT_FIELDS if getattr(idea, f) != UNKNOWN)


def idea_quality(idea, weights: dict[str, float], completeness: float, complexity: float, evidence: dict,
                 hard_fail: bool) -> IdeaScore:
    text = _rules_text(idea)
    horizon = idea.time_horizon
    assets = set(idea.asset_classes or [])
    c: dict[str, Component] = {}

    if idea.economic_rationale != UNKNOWN:
        c["economic_rationale"] = Component(min(1.0, idea.rationale_confidence or 0.5), "SOURCE_RATIONALE_GROUNDED")
    else:
        c["economic_rationale"] = Component(0.0, "NO_RATIONALE_IN_SOURCE")
    c["rule_quantifiability"] = Component(completeness / 100, "FORMALIZATION_COMPLETENESS")
    c["point_in_time"] = Component(0.0, "LOOKAHEAD_DETECTED") if hard_fail else \
        Component(None, "NOT_ASSESSABLE_FROM_TEXT")
    intraday = horizon in (TimeHorizon.HFT, TimeHorizon.SECONDS)
    if not assets:
        c["data_availability"] = Component(None, "ASSET_CLASS_UNKNOWN")
    elif assets <= _BAR_DATA and not intraday:
        c["data_availability"] = Component(0.7, "ASSET_CLASS_HEURISTIC:bar_data_widely_available")
    elif "OPTIONS" in assets:
        c["data_availability"] = Component(0.4, "ASSET_CLASS_HEURISTIC:options_chain_history_needed")
    elif assets <= _BAR_DATA | {"FIXED_INCOME"} and not intraday:
        c["data_availability"] = Component(0.6, "ASSET_CLASS_HEURISTIC:bond_index_and_yield_series_available")
    else:
        c["data_availability"] = Component(None, "NOT_ASSESSED")
    if _ILLIQUID.search(text):
        c["liquidity"] = Component(0.2, "TEXT:illiquid_universe")
    elif _LIQUID.search(text):
        c["liquidity"] = Component(0.8, "TEXT:liquid_universe")
    else:
        c["liquidity"] = Component(None, "UNIVERSE_LIQUIDITY_UNKNOWN")
    c["exit_executability"] = Component(0.7, "EXIT_RULE_STATED") if idea.exit_rule != UNKNOWN else \
        Component(0.0, "NO_EXIT_RULE")
    if horizon in (TimeHorizon.HFT, TimeHorizon.SECONDS):
        c["edge_vs_cost"] = Component(0.1, "HORIZON_TOO_SHORT_FOR_RETAIL_COSTS")
    elif idea.transaction_cost_assumption != UNKNOWN or "costs_considered" in evidence.get("present", []):
        c["edge_vs_cost"] = Component(0.6, "SOURCE_ADDRESSES_COSTS")
    else:
        c["edge_vs_cost"] = Component(None, "COSTS_NOT_DISCUSSED")
    c["parameter_simplicity"] = Component(1 - complexity / 100, "PARAMETER_COMPLEXITY")
    ev_present = set(evidence.get("present", []))
    robust = len(ev_present & {"out_of_sample", "replication", "cross_market"}) / 3
    c["expected_robustness"] = Component(robust, "EVIDENCE_SIGNALS")
    freq = {TimeHorizon.INTRADAY: 0.8, TimeHorizon.MULTI_DAY: 0.7, TimeHorizon.SWING: 0.7, TimeHorizon.WEEKLY: 0.6,
            TimeHorizon.MONTHLY: 0.5, TimeHorizon.LONG_TERM: 0.3, TimeHorizon.MINUTES: 0.8}.get(horizon)
    c["opportunity_frequency"] = Component(freq, f"TIME_HORIZON:{horizon.value}")
    if _SHORT_VOL.search(text) or "MARTINGALE" in (idea.red_flags or []):
        c["tail_risk"] = Component(0.2, "SHORT_VOLATILITY_OR_MARTINGALE_EXPOSURE")
    else:
        c["tail_risk"] = Component(None, "TAIL_RISK_NOT_ASSESSED")
    c["capacity"] = Component(None, "NOT_ASSESSABLE_FROM_TEXT")
    c["automation"] = Component(min(1.0, completeness / 80), "FORMALIZATION_COMPLETENESS")
    source_regimes = [r for r in idea.regimes if r.suitability is RegimeSuitability.SUITED and
                      r.basis in (RegimeBasis.SOURCE_STATED, RegimeBasis.SOURCE_EVIDENCE)]
    c["diversification"] = Component(0.6, "SOURCE_STATED_REGIME_FIT") if source_regimes else \
        Component(None, "NOT_ASSESSED_UNTIL_PORTFOLIO_CONTEXT")

    score = sum(weights[k] * comp.value for k, comp in c.items() if comp.value is not None)
    coverage = sum(weights[k] for k, comp in c.items() if comp.value is not None) / 100
    return IdeaScore(round(score, 2), round(coverage, 3), c)


# ---- research priority (spec §81) and gate (§126) -----------------------------------------------------------

PRIORITY_WEIGHTS = {"idea_quality": 0.35, "source_quality": 0.15, "evidence_quality": 0.15, "replication": 0.10,
                    "novelty": 0.10, "completeness": 0.10, "diversification": 0.05}


def research_priority(idea_q: IdeaScore, source_q: float, evidence_q: float, replication: float, novelty: float,
                      completeness: float) -> float:
    """Never uses claimed returns. Discounted when much of the idea score could not be assessed."""
    div = idea_q.components.get("diversification")
    values = {"idea_quality": idea_q.normalized or 0.0, "source_quality": source_q, "evidence_quality": evidence_q,
              "replication": replication, "novelty": novelty, "completeness": completeness,
              "diversification": (div.value * 100) if div and div.value is not None else 0.0}
    raw = sum(PRIORITY_WEIGHTS[k] * v for k, v in values.items())
    return round(raw * (0.5 + 0.5 * idea_q.coverage), 2)


def gate_band(score: float, high: int, promising: int, research: int) -> str:
    return "HIGH_PRIORITY" if score >= high else "PROMISING" if score >= promising else \
        "RESEARCH_FURTHER" if score >= research else "ARCHIVE"
