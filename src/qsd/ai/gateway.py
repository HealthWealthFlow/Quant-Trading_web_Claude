"""The only path to an AI model: cache → budget check → call → ledger → JSON validation (spec §45, §83–§87, §120).

- Cache key: sha256(task | prompt version | provider | model | exact prompt text). A hit costs nothing (§85).
- Before every call the worst-case cost (input estimate + max_tokens output) is checked against the campaign, daily
  and monthly caps; the call is refused rather than overspending. Unpriced models are refused outright.
- Every call — hit, success or failure — gets a row in `ai_calls`.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, ValidationError
from sqlalchemy import Engine, func, select

from ..config import Settings
from ..db import session_scope
from ..db.models import AICache, AICall
from ..discovery.budget import BudgetExhausted, CampaignBudget
from .providers import Provider, ProviderError

CHARS_PER_TOKEN_ESTIMATE = 3.0  # conservative (over-estimates tokens → under-spends)


class AIOutputError(RuntimeError):
    pass


class UnpricedModelError(RuntimeError):
    pass


@dataclass
class CallInfo:
    cache_hit: bool
    cost_usd: float
    input_tokens: int
    output_tokens: int
    model: str


def _extract_json(text: str) -> dict:
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S)
    if fence:
        t = fence.group(1)
    elif not t.startswith("{"):
        start, end = t.find("{"), t.rfind("}")
        if start == -1 or end <= start:
            raise AIOutputError("no JSON object in model output")
        t = t[start:end + 1]
    try:
        data = json.loads(t)
    except json.JSONDecodeError as e:
        raise AIOutputError(f"invalid JSON from model: {e}") from None
    if not isinstance(data, dict):
        raise AIOutputError("model output is not a JSON object")
    return data


class AIGateway:
    def __init__(self, engine: Engine, settings: Settings, providers: dict[str, Provider],
                 budget: CampaignBudget | None = None, now=lambda: datetime.now(UTC)):
        self.engine = engine
        self.settings = settings
        self.providers = providers
        self.budget = budget
        self.now = now

    # -- cost & budgets -------------------------------------------------------------------------------

    def price_of(self, model: str):
        price = self.settings.ai.prices.get(model)
        if price is None:
            raise UnpricedModelError(
                f"no price configured for model '{model}': set ai.prices.{model} in config/local.yaml "
                "from the provider's pricing page (calls are refused without it)")
        return price

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        p = self.price_of(model)
        return (input_tokens * p.input_per_mtok + output_tokens * p.output_per_mtok) / 1_000_000

    def _spent_since(self, since: datetime) -> float:
        with session_scope(self.engine) as s:
            return float(s.execute(select(func.coalesce(func.sum(AICall.cost_usd), 0.0))
                                   .where(AICall.called_at >= since)).scalar_one())

    def check_budget(self, model: str, est_input: int, max_output: int) -> float:
        worst = self.cost(model, est_input, max_output)
        now = self.now()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = day_start.replace(day=1)
        b = self.settings.budgets
        if self._spent_since(day_start) + worst > b.max_ai_cost_usd_per_day:
            raise BudgetExhausted("ai_cost_usd_per_day", b.max_ai_cost_usd_per_day)
        if self._spent_since(month_start) + worst > b.max_ai_cost_usd_per_month:
            raise BudgetExhausted("ai_cost_usd_per_month", b.max_ai_cost_usd_per_month)
        if self.budget is not None:
            for kind, amount in (("ai_calls", 1), ("ai_tokens", est_input + max_output), ("ai_cost_usd", worst)):
                if self.budget.remaining(kind) < amount:
                    raise BudgetExhausted(kind, self.budget.limit(kind))
        return worst

    # -- main entry --------------------------------------------------------------------------------------

    def run_json(self, *, task: str, prompt_version: str, provider: str, model: str, system: str, user: str,
                 schema: type[BaseModel], max_tokens: int, campaign_id: int | None = None,
                 source_id: int | None = None, idea_id: int | None = None) -> tuple[BaseModel, CallInfo]:
        key = hashlib.sha256("|".join([task, prompt_version, provider, model, system, user]).encode()).hexdigest()
        content_hash = hashlib.sha256(user.encode()).hexdigest()
        ledger = dict(campaign_id=campaign_id, source_id=source_id, idea_id=idea_id, provider=provider, model=model,
                      task=task, prompt_version=prompt_version, cache_key=key)

        with session_scope(self.engine) as s:
            cached = s.get(AICache, key)
            if cached is not None:
                s.add(AICall(**ledger, cache_hit=True, cost_usd=0.0))
                return schema.model_validate(cached.response), CallInfo(True, 0.0, 0, 0, model)

        est_input = int((len(system) + len(user)) / CHARS_PER_TOKEN_ESTIMATE)
        self.check_budget(model, est_input, max_tokens)
        prov = self.providers.get(provider)
        if prov is None:
            raise ProviderError(f"unknown provider '{provider}'")
        try:
            resp = prov.complete(model, system, user, max_tokens)
        except ProviderError:
            with session_scope(self.engine) as s:
                s.add(AICall(**ledger, success=False))
            raise
        cost = self.cost(model, resp.input_tokens, resp.output_tokens)
        if self.budget is not None:
            self.budget.spent["ai_calls"] = self.budget.spent.get("ai_calls", 0) + 1
            self.budget.spent["ai_tokens"] = self.budget.spent.get("ai_tokens", 0) + resp.input_tokens + \
                resp.output_tokens
            self.budget.spent["ai_cost_usd"] = self.budget.spent.get("ai_cost_usd", 0.0) + cost
        try:
            data = _extract_json(resp.text)
            obj = schema.model_validate(data)
            ok = True
        except (AIOutputError, ValidationError) as e:
            ok, error = False, e
        with session_scope(self.engine) as s:
            s.add(AICall(**ledger, cache_hit=False, input_tokens=resp.input_tokens, output_tokens=resp.output_tokens,
                         cost_usd=cost, latency_ms=resp.latency_ms, success=ok))
            if ok:
                s.add(AICache(cache_key=key, provider=provider, model=model, task=task,
                              prompt_version=prompt_version, content_hash=content_hash,
                              response=obj.model_dump(mode="json")))
        if not ok:
            raise AIOutputError(f"{task}: model output failed validation: {str(error)[:300]}")
        return obj, CallInfo(False, cost, resp.input_tokens, resp.output_tokens, resp.model)
