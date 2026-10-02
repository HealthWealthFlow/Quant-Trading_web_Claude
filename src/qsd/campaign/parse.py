"""Natural-language request → structured campaign spec (spec §94, §95, §137). Deterministic keyword parsing; the
parsed spec is shown back to the user and stored, so nothing is silently assumed.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from ..taxonomy import AssetClass, MarketRegime

ASSET_WORDS = {
    AssetClass.ETF: r"\betfs?\b|sector rotation",
    AssetClass.STOCK: r"\bstocks?\b|\bequit(y|ies)\b|\bshares\b",
    AssetClass.OPTIONS: r"\boptions?\b|\bspx\b|0dte|\bvix\b|volatility risk premium|put[- ]writ",
    AssetClass.FOREX: r"\bfx\b|forex|currenc(y|ies)",
    AssetClass.CRYPTO: r"crypto|bitcoin|\bbtc\b|\beth\b|ethereum|perpetual|funding rate",
    AssetClass.FUTURES: r"\bfutures\b|commodit",
}
REGIME_WORDS = {
    MarketRegime.CRASH: r"crash|tail[- ]?(risk|hedg)|crisis|protection|black swan",
    MarketRegime.BEARISH: r"\bbear(ish)?\b|downtrend|falling market",
    MarketRegime.BULLISH: r"\bbull(ish)?\b|uptrend|rising market",
    MarketRegime.CONSOLIDATION: r"sideways|range[- ]bound|consolidat|choppy",
}
FAMILY_PHRASES = {
    r"momentum": "momentum", r"mean[- ]revers|reversal": "mean reversion", r"\bcarry\b": "carry",
    r"rotation": "rotation", r"volatility": "volatility", r"funding": "funding rate", r"breakout": "breakout",
    r"seasonal": "seasonality", r"pairs|stat(istical)? arb": "pairs trading", r"trend": "trend following",
    r"earnings|pead": "post earnings announcement drift", r"intraday|0dte": "intraday",
    r"support|resistance": "support and resistance", r"retracement|pullback|fibonacci": "pullback",
    r"\bvolume\b": "volume confirmation", r"moving average|crossover": "moving average",
}
MODES = {r"\bacademic\b|papers? only": "ACADEMIC_ONLY", r"\bdeep\b|thorough": "DEEP_RESEARCH",
         r"\bquick\b": "QUICK_DISCOVERY", r"\brecent\b|latest|new": "RECENT_ONLY"}
_FILLER = re.compile(r"(?<![\w-])(find|search|look for|discover|me|some|good|reliable|best|strategies|strategy|"
                     r"ideas?|for|the|a|an|of|in|on|with|that|work|works)(?![\w-])", re.I)


@dataclass
class CampaignSpec:
    request: str
    asset_classes: list[str] = field(default_factory=list)
    regimes: list[str] = field(default_factory=list)
    families: list[str] = field(default_factory=list)
    mode: str = "QUICK_DISCOVERY"
    core_query: str = ""
    target_families: int = 3  # spec §129: distinct promising strategy families to aim for

    def to_dict(self) -> dict:
        return asdict(self)


def parse_request(text: str) -> CampaignSpec:
    t = text.lower()
    spec = CampaignSpec(request=text.strip())
    spec.asset_classes = [a.value for a, rx in ASSET_WORDS.items() if re.search(rx, t)]
    spec.regimes = [r.value for r, rx in REGIME_WORDS.items() if re.search(rx, t)]
    spec.families = [name for rx, name in FAMILY_PHRASES.items() if re.search(rx, t)]
    spec.mode = next((m for rx, m in MODES.items() if re.search(rx, t)), "QUICK_DISCOVERY")
    spec.core_query = " ".join(_FILLER.sub(" ", text).split())
    return spec
