"""Relevant-section selection so long documents and books are never sent whole to AI (spec §21, §84).

Blocks are scored by research keywords; the opening blocks (title/abstract) are always kept; the best blocks are
returned in document order with location markers like [p.14] so the model can cite where each rule came from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..handlers import HandlerResult

KEYWORDS = (
    "strategy", "momentum", "mean reversion", "reversal", "alpha", "factor", "volatility", "option", "execution",
    "liquidity", "transaction cost", "portfolio", "backtest", "risk", "sharpe", "drawdown", "signal", "entry",
    "exit", "rebalanc", "lookback", "carry", "spread", "hedge", "return", "regime", "crash", "bear", "bull",
    "trend", "stop", "position", "turnover", "out-of-sample", "sample",
)
_KW = re.compile("|".join(re.escape(k) for k in KEYWORDS), re.I)


@dataclass
class Selection:
    text: str
    blocks_used: int
    blocks_total: int
    truncated: bool


def select_relevant(result: HandlerResult, max_chars: int, keep_first: int = 2) -> Selection:
    blocks = [b for b in result.blocks if b.text.strip()]
    if not blocks:
        return Selection("", 0, 0, False)
    scored = [(i, len(_KW.findall(b.text)) / (1 + len(b.text) / 2000)) for i, b in enumerate(blocks)]
    chosen: set[int] = set(range(min(keep_first, len(blocks))))
    budget = sum(len(blocks[i].text) for i in chosen)
    for i, score in sorted(scored, key=lambda t: t[1], reverse=True):
        if i in chosen or score <= 0:
            continue
        size = len(blocks[i].text) + 16
        if budget + size > max_chars:
            continue
        chosen.add(i)
        budget += size
    parts = []
    for i in sorted(chosen):
        b = blocks[i]
        text = b.text if len(b.text) <= max_chars else b.text[:max_chars]
        parts.append(f"[{b.location.label()}] {text}")
    text = "\n\n".join(parts)[:max_chars]
    return Selection(text, len(chosen), len(blocks), len(chosen) < len(blocks))
