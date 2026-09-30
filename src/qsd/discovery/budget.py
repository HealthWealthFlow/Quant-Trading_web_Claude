"""Campaign budgets (spec §45). Every spend is checked first; exhausting any budget stops the campaign (§44)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..config import Budgets


class BudgetExhausted(RuntimeError):
    def __init__(self, kind: str, limit: float):
        super().__init__(f"budget exhausted: {kind} (limit {limit})")
        self.kind = kind
        self.limit = limit


@dataclass
class CampaignBudget:
    budgets: Budgets
    clock: Callable[[], float] = time.monotonic
    started: float = field(default=0.0)
    spent: dict[str, float] = field(default_factory=dict)

    LIMITS = {
        "search_requests": "max_search_requests_per_campaign",
        "urls": "max_urls_per_campaign",
        "documents": "max_documents_per_campaign",
        "ai_calls": "max_ai_calls_per_campaign",
        "ai_tokens": "max_ai_tokens_per_campaign",
        "ai_cost_usd": "max_ai_cost_usd_per_campaign",
    }

    def __post_init__(self) -> None:
        self.started = self.clock()

    def limit(self, kind: str) -> float:
        return float(getattr(self.budgets, self.LIMITS[kind]))

    def remaining(self, kind: str) -> float:
        return self.limit(kind) - self.spent.get(kind, 0.0)

    def check_runtime(self) -> None:
        minutes = (self.clock() - self.started) / 60
        if minutes >= self.budgets.max_runtime_minutes_per_campaign:
            raise BudgetExhausted("runtime_minutes", self.budgets.max_runtime_minutes_per_campaign)

    def spend(self, kind: str, amount: float = 1.0) -> None:
        """Reserve `amount`; raises (without spending) if it would exceed the limit."""
        self.check_runtime()
        if self.spent.get(kind, 0.0) + amount > self.limit(kind):
            raise BudgetExhausted(kind, self.limit(kind))
        self.spent[kind] = self.spent.get(kind, 0.0) + amount

    def snapshot(self) -> dict[str, float]:
        return {k: self.spent.get(k, 0.0) for k in self.LIMITS}
