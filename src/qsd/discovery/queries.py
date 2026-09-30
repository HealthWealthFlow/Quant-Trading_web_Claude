"""Structured query families (spec §39), vocabulary expansion (§40) and market-regime queries (§137).

Deterministic: no AI is used to generate queries (spec §83). Search memory (§93) de-duplicates repeats.
"""

from __future__ import annotations

import hashlib
import re

from ..taxonomy import AssetClass, MarketRegime

QUERY_FAMILIES: dict[AssetClass, list[str]] = {
    AssetClass.STOCK: [
        "cross sectional momentum stock strategy study", "short term equity reversal academic paper",
        "overnight return anomaly", "post earnings announcement drift", "equity momentum SSRN",
        "market microstructure short term reversal",
    ],
    AssetClass.ETF: [
        "ETF momentum quantitative research", "sector rotation ETF", "ETF mean reversion", "dual momentum ETF",
        "asset allocation momentum",
    ],
    AssetClass.OPTIONS: [
        "SPX volatility risk premium study", "index option variance risk premium", "option skew strategy",
        "SPX term structure strategy", "0DTE quantitative strategy", "delta hedged option returns",
        "implied vs realized volatility strategy", "dispersion trading paper",
    ],
    AssetClass.FOREX: [
        "currency momentum quantitative strategy", "FX carry academic research", "currency value factor",
        "FX mean reversion", "currency trend following",
    ],
    AssetClass.CRYPTO: [
        "bitcoin momentum research", "crypto funding rate strategy", "cryptocurrency cross sectional momentum",
        "crypto basis arbitrage", "bitcoin intraday seasonality", "crypto perpetual funding premium",
        "crypto volatility risk premium",
    ],
}

# Groups of interchangeable research terms (spec §40). A query containing one term gets variants with the others.
EXPANSIONS: list[list[str]] = [
    ["momentum", "relative strength", "return continuation", "time-series momentum", "trend following"],
    ["mean reversion", "short-term reversal", "return reversal", "overreaction"],
    ["carry", "carry trade", "interest rate differential"],
    ["volatility risk premium", "variance risk premium", "implied minus realized volatility"],
    ["seasonality", "calendar anomaly", "intraday periodicity"],
    ["pairs trading", "statistical arbitrage", "cointegration trading"],
    ["breakout", "channel breakout", "range breakout"],
    ["funding rate", "perpetual funding", "funding premium"],
]

REGIME_TERMS: dict[MarketRegime, list[str]] = {
    MarketRegime.BULLISH: ["bull market", "uptrend", "rising market"],
    MarketRegime.BEARISH: ["bear market", "downtrend", "falling market"],
    MarketRegime.CONSOLIDATION: ["range-bound market", "sideways market", "low volatility regime"],
    MarketRegime.CRASH: ["crash protection", "tail risk hedging", "crisis alpha", "market crash"],
}

ASSET_WORDS: dict[AssetClass, str] = {
    AssetClass.STOCK: "stock", AssetClass.ETF: "ETF", AssetClass.OPTIONS: "options", AssetClass.FOREX: "currency",
    AssetClass.CRYPTO: "cryptocurrency",
}


def normalize_query(q: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", q.lower()))  # hyphens/punctuation/case ignored


def query_hash(connector: str, query: str) -> str:
    return hashlib.sha256(f"{connector}|{normalize_query(query)}".encode()).hexdigest()


def expand_query(query: str, max_variants: int = 3) -> list[str]:
    q = query.lower()
    out = [query]
    for group in EXPANSIONS:
        hit = next((term for term in group if term in q), None)
        if hit is None:
            continue
        for alt in group:
            if alt != hit and len(out) <= max_variants:
                out.append(re.sub(re.escape(hit), alt, query, flags=re.I))
    return out


def build_queries(asset_classes: list[AssetClass] | None = None, regimes: list[MarketRegime] | None = None,
                  extra_terms: list[str] | None = None, expand: bool = False, limit: int = 50) -> list[str]:
    assets = asset_classes or list(QUERY_FAMILIES)
    queries: list[str] = []
    for asset in assets:
        queries.extend(QUERY_FAMILIES.get(asset, []))
        for regime in regimes or []:
            word = ASSET_WORDS.get(asset, asset.value.lower())
            queries.extend(f"{word} strategy {term}" for term in REGIME_TERMS[regime][:2])
    queries.extend(extra_terms or [])
    if expand:
        queries = [v for q in queries for v in expand_query(q)]
    seen: set[str] = set()
    unique = []
    for q in queries:
        key = normalize_query(q)
        if key not in seen:
            seen.add(key)
            unique.append(q)
    return unique[:limit]
