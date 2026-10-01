"""Deterministic screening rules (spec §47, §48, §53–§56, §67). No AI, no guessing: a rule either finds explicit
evidence in the stored text or reports that it could not assess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..taxonomy import UNKNOWN, RejectionReason

# ---- red-flag language (spec §54) ---------------------------------------------------------------------

RED_FLAGS: dict[str, str] = {
    "GUARANTEED_PROFIT": r"guaranteed (profit|returns?|income|gains?)",
    "NEVER_LOSES": r"never (loses|lose|had a losing)",
    "PERFECT_ACCURACY": r"100\s?% (accurate|accuracy|win rate)",
    "EXTREME_WIN_RATE": r"\b9\d(\.\d+)?\s?% win(ning)? rate",
    "RISK_FREE": r"\brisk[- ]free (profit|returns?|strategy|trading|income)",
    "SECRET_STRATEGY": r"\bsecret (strategy|formula|system|method)",
    "HOLY_GRAIL": r"\bholy grail\b",
    "AI_PREDICTS_ALL": r"\bai predicts (every|all)",
    "GET_RICH": r"turn \$?\d[\d,]* into \$?\d[\d,]*\s?(k|m|million|thousand)?",
    "INSTITUTIONAL_SECRET": r"\binstitutional secret",
    "MARTINGALE": r"\bmartingale\b",
    "DOUBLE_AFTER_LOSS": r"double (the |your )?(position|lot|size|stake|bet)s? (after|on) (every |each |a )?loss",
    "AVERAGING_DOWN": r"\b(average|averaging) down\b",
}
_RED_RE = {k: re.compile(v, re.I) for k, v in RED_FLAGS.items()}
SCAM_FLAGS = {"GUARANTEED_PROFIT", "NEVER_LOSES", "PERFECT_ACCURACY", "RISK_FREE", "SECRET_STRATEGY", "HOLY_GRAIL",
              "AI_PREDICTS_ALL", "GET_RICH", "INSTITUTIONAL_SECRET", "EXTREME_WIN_RATE"}


def find_red_flags(text: str) -> list[str]:
    return sorted(k for k, rx in _RED_RE.items() if rx.search(text or ""))


# ---- hard fails (spec §53, §56, §58) --------------------------------------------------------------------

_LOOKAHEAD = re.compile(
    r"(tomorrow'?s|next[- ]day'?s?|future|next (bar|period|month)'?s?) (close|high|low|price|return|index membership)|"
    r"(high|low) of the day (is|was) known|known in advance|revised (gdp|data) (at|as of) (release|publication)|"
    r"(enter|buy|sell) at (the )?(day'?s )?(high|low)\b",
    re.I,
)
_SURVIVORSHIP = re.compile(r"(current|today'?s) (s&p ?500 |index |nasdaq ?100 )?(constituents|members)|"
                           r"stocks that (are|were) (still )?listed today", re.I)
_UNBOUNDED = re.compile(r"(no|without( a)?) stop[- ]loss|unlimited (loss|risk)|never (close|cut) (a )?los", re.I)


@dataclass
class HardFail:
    reason: RejectionReason
    detail: str


def hard_fails(rule_text: str, red_flags: list[str], completeness: float, signal: str) -> list[HardFail]:
    out: list[HardFail] = []
    if "MARTINGALE" in red_flags or "DOUBLE_AFTER_LOSS" in red_flags:
        out.append(HardFail(RejectionReason.MARTINGALE, "martingale / doubling after losses"))
    if "AVERAGING_DOWN" in red_flags and _UNBOUNDED.search(rule_text):
        out.append(HardFail(RejectionReason.UNLIMITED_AVERAGING, "averaging down without a loss limit"))
    if m := _LOOKAHEAD.search(rule_text):
        out.append(HardFail(RejectionReason.LOOKAHEAD_REQUIRED, f"rule uses information not yet known: '{m.group(0)}'"))
    if m := _SURVIVORSHIP.search(rule_text):
        out.append(HardFail(RejectionReason.OBVIOUS_SURVIVORSHIP_BIAS, f"universe defined as '{m.group(0)}'"))
    if len(set(red_flags) & SCAM_FLAGS) >= 2:
        out.append(HardFail(RejectionReason.SCAM_SIGNAL_SOURCE, "multiple promotional red flags: " +
                            ", ".join(sorted(set(red_flags) & SCAM_FLAGS))))
    if completeness < 20 and signal == UNKNOWN:
        out.append(HardFail(RejectionReason.RULES_NOT_QUANTIFIABLE, f"completeness {completeness:.0f} and no signal"))
    return out


# ---- formalization completeness (spec §48) ----------------------------------------------------------------

COMPLETENESS_WEIGHTS = {
    "instrument": 15, "universe": 10, "entry": 20, "exit": 20, "timeframe": 10, "parameters": 10, "sizing": 10,
    "execution": 5,
}


def _known(*values: str) -> bool:
    return any(v and v != UNKNOWN for v in values)


def formalization_completeness(idea) -> tuple[float, list[str]]:
    present = {
        "instrument": _known(idea.instrument),
        "universe": _known(idea.universe),
        "entry": _known(idea.entry_rule, idea.signal),
        "exit": _known(idea.exit_rule, idea.stop_rule, idea.take_profit_rule, idea.holding_period),
        "timeframe": _known(idea.timeframe, idea.data_frequency, idea.rebalance),
        "parameters": _known(idea.lookback) or any(v != UNKNOWN for v in (idea.parameters or {}).values()),
        "sizing": _known(idea.position_sizing),
        "execution": _known(idea.order_type, idea.trading_session, idea.transaction_cost_assumption),
    }
    score = sum(COMPLETENESS_WEIGHTS[k] for k, ok in present.items() if ok)
    return float(score), [k for k, ok in present.items() if not ok]


MATURITY_LEVELS = ((80, "BACKTEST-READY"), (60, "TRADEABLE"), (35, "PARTIAL"), (0, "CONCEPT"))


def maturity(completeness: float | None) -> str:
    """How far the rules are written down (formalization completeness): CONCEPT → PARTIAL → TRADEABLE →
    BACKTEST-READY. Says nothing about whether the strategy works."""
    if completeness is None:
        return "UNKNOWN"
    return next(label for floor, label in MATURITY_LEVELS if completeness >= floor)


def completeness_band(score: float) -> str:
    return "ALMOST_CODE_READY" if score >= 90 else "SOME_MISSING" if score >= 60 else \
        "CONCEPT_LEVEL" if score >= 30 else "INSUFFICIENT"


# ---- parameter complexity (spec §55) ----------------------------------------------------------------------

_INDICATORS = re.compile(r"\b(rsi|macd|atr|vix|bollinger|ema|sma|moving average|stochastic|adx|obv|vwap|"
                         r"ichimoku|cci|momentum|volume|volatility|z-?score|sentiment)\b", re.I)
_TIMING = re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|minute|hh:mm|\d{1,2}:\d{2}|weekday|"
                     r"first|last) ", re.I)


def parameter_complexity(idea) -> tuple[float, dict]:
    rules = " ".join(str(getattr(idea, f)) for f in ("signal", "entry_rule", "exit_rule", "stop_rule",
                                                    "indicators", "risk_rules") if getattr(idea, f) != UNKNOWN)
    n_indicators = len({m.group(1).lower() for m in _INDICATORS.finditer(rules)})
    n_params = sum(1 for v in (idea.parameters or {}).values() if v != UNKNOWN)
    n_timing = len(_TIMING.findall(rules))
    n_thresholds = len(re.findall(r"[<>]=?|\babove\b|\bbelow\b|\bcrosses\b", rules, re.I))
    raw = n_indicators * 12 + n_params * 8 + n_timing * 10 + n_thresholds * 6
    return float(min(100, raw)), {"indicators": n_indicators, "parameters": n_params, "timing": n_timing,
                                  "thresholds": n_thresholds}
