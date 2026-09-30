import json
from datetime import UTC, datetime

import pytest
from fixtures import make_pdf
from sqlalchemy import select

from qsd.ai import (
    AIGateway,
    AIOutputError,
    ProviderError,
    ProviderResponse,
    StageAResult,
    UnpricedModelError,
    extract_ideas,
    ground_strategy,
    quote_in_source,
)
from qsd.ai.schemas import ExtractedStrategy
from qsd.ai.sections import select_relevant
from qsd.config import DEFAULT_CONFIG, load_settings
from qsd.db import init_db, make_engine, session_scope
from qsd.db.models import AICall, Idea, IdeaSource, Source, SourceFact
from qsd.discovery.budget import BudgetExhausted, CampaignBudget
from qsd.handlers import parse_bytes
from qsd.taxonomy import UNKNOWN, IdeaStatus, MarketRegime, RegimeBasis, RegimeSuitability

PAGES = [
    "Time-series momentum in futures. We go long assets with positive 12-month excess return.",
    "Positions are rebalanced monthly. The strategy earned a Sharpe ratio of 1.2 over 1985-2009.",
    "The strategy performs best in extended bear markets and during crises such as 2008.",
]
SOURCE_TEXT = " ".join(PAGES)

STAGE_A = {"is_strategy_research": True, "asset_classes": ["FUTURES", "NOT_REAL"],
           "strategy_families": ["TIME-SERIES MOMENTUM"], "basic_idea": "trend", "red_flags": [],
           "worth_deep_read": True, "confidence": 0.9}

STAGE_B = {"strategies": [{
    "strategy_name": "Time-series momentum", "summary": "Long positive 12m return assets",
    "asset_classes": ["FUTURES"], "strategy_families": ["time-series momentum"],
    "position_direction": "long_short", "time_horizon": "MONTHLY",
    "rules": {
        "signal": {"value": "positive 12-month excess return",
                   "evidence_quote": "long assets with positive 12-month excess return", "location": "p.1",
                   "confidence": 0.95},
        "rebalance": {"value": "monthly", "evidence_quote": "rebalanced monthly", "location": "p.2",
                      "confidence": 0.9},
        # fabricated: no quote support in the source
        "stop_rule": {"value": "exit after 10% loss", "evidence_quote": "stop loss of 10%", "location": "p.2",
                      "confidence": 0.8},
        # invented number: quote real, but "20-day" is not in the source
        "lookback": {"value": "20-day", "evidence_quote": "positive 12-month excess return", "location": "p.1",
                     "confidence": 0.7},
        "not_a_field": {"value": "x"},
    },
    "parameters": [{"name": "lookback_months", "value": "12", "evidence_quote": "12-month excess return",
                    "location": "p.1", "confidence": 0.9},
                   {"name": "threshold", "value": "top 10%", "evidence_quote": "top decile", "confidence": 0.5}],
    "rationale": {"value": "UNKNOWN"},
    "claims": {"claimed_sharpe": {"value": "1.2", "evidence_quote": "earned a Sharpe ratio of 1.2",
                                  "location": "p.2", "confidence": 0.95},
               "claimed_cagr": {"value": "25%", "evidence_quote": "earned a Sharpe ratio of 1.2",
                                "confidence": 0.5}},
    "regimes": [
        {"regime": "BEARISH", "suitability": "SUITED", "basis": "SOURCE_STATED", "confidence": 0.9,
         "evidence_quote": "performs best in extended bear markets", "location": "p.3"},
        {"regime": "CRASH", "suitability": "SUITED", "basis": "SOURCE_STATED", "confidence": 0.9,
         "evidence_quote": "always profits in crashes", "location": "p.3"},
        {"regime": "sideways", "suitability": "SUITED"},
    ],
    "unknown_rules": [],
}]}


class FakeProvider:
    name = "fake"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, model, system, user, max_tokens):
        self.calls.append({"model": model, "system": system, "user": user})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        text = reply if isinstance(reply, str) else json.dumps(reply)
        return ProviderResponse(text=text, input_tokens=1000, output_tokens=200, latency_ms=5, model=model)


def settings(**env):
    base = {"QSD_AI__DEFAULT_PROVIDER": "fake"}
    base.update(env)
    s = load_settings(DEFAULT_CONFIG, None, environ=base)
    s.ai.prices["deepseek-chat"] = type(s.ai.prices["claude-opus-5-5"])(input_per_mtok=1.0, output_per_mtok=2.0)
    return s


@pytest.fixture
def engine(tmp_path):
    e = make_engine(tmp_path / "db.sqlite")
    init_db(e)
    return e


def run_simple(gw, user="hello"):
    return gw.run_json(task="stage_a_triage", prompt_version="a1", provider="fake", model="deepseek-chat",
                       system="sys", user=user, schema=StageAResult, max_tokens=100)


# ---- gateway ---------------------------------------------------------------------------------------

def test_cache_hit_costs_nothing_and_skips_provider(engine):
    fake = FakeProvider([STAGE_A])
    gw = AIGateway(engine, settings(), {"fake": fake})
    obj, info = run_simple(gw)
    assert info.cost_usd == pytest.approx((1000 * 1.0 + 200 * 2.0) / 1e6)
    assert [a.value for a in obj.asset_classes] == ["FUTURES"]  # invalid "NOT_REAL" dropped, not guessed
    obj2, info2 = run_simple(gw)
    assert info2.cache_hit and info2.cost_usd == 0 and len(fake.calls) == 1
    with session_scope(engine) as s:
        rows = s.scalars(select(AICall)).all()
        assert [r.cache_hit for r in rows] == [False, True]


def test_unpriced_model_is_refused(engine):
    s = settings()
    s.ai.prices["deepseek-chat"] = None
    fake = FakeProvider([STAGE_A])
    with pytest.raises(UnpricedModelError):
        run_simple(AIGateway(engine, s, {"fake": fake}))
    assert fake.calls == []


def test_daily_cap_blocks_before_calling(engine):
    s = settings(QSD_BUDGETS__MAX_AI_COST_USD_PER_DAY="0.0001")
    fake = FakeProvider([STAGE_A])
    with pytest.raises(BudgetExhausted):
        run_simple(AIGateway(engine, s, {"fake": fake}, now=lambda: datetime(2026, 9, 30, 12, tzinfo=UTC)))
    assert fake.calls == []


def test_campaign_cost_cap(engine):
    s = settings(QSD_BUDGETS__MAX_AI_CALLS_PER_CAMPAIGN="1")
    budget = CampaignBudget(s.budgets)
    gw = AIGateway(engine, s, {"fake": FakeProvider([STAGE_A, STAGE_A])}, budget=budget)
    run_simple(gw, "one")
    with pytest.raises(BudgetExhausted):
        run_simple(gw, "two")
    assert budget.spent["ai_calls"] == 1


def test_invalid_json_recorded_not_cached(engine):
    fake = FakeProvider(["not json at all", STAGE_A])
    gw = AIGateway(engine, settings(), {"fake": fake})
    with pytest.raises(AIOutputError):
        run_simple(gw)
    run_simple(gw)  # retried call goes to provider again (failure was not cached)
    assert len(fake.calls) == 2
    with session_scope(engine) as s:
        assert [r.success for r in s.scalars(select(AICall))] == [False, True]


def test_provider_error_recorded(engine):
    gw = AIGateway(engine, settings(), {"fake": FakeProvider([ProviderError("boom")])})
    with pytest.raises(ProviderError):
        run_simple(gw)
    with session_scope(engine) as s:
        assert s.scalars(select(AICall)).one().success is False


def test_json_in_code_fence_accepted(engine):
    gw = AIGateway(engine, settings(), {"fake": FakeProvider(["```json\n" + json.dumps(STAGE_A) + "\n```"])})
    obj, _ = run_simple(gw)
    assert obj.is_strategy_research


# ---- grounding -------------------------------------------------------------------------------------

def test_quote_matching_tolerates_whitespace_case_and_ellipsis():
    src = "We go long assets with positive\n12-month excess return. Positions are rebalanced monthly."
    from qsd.ai import normalize
    n = normalize(src)
    assert quote_in_source("LONG assets with positive 12-month", n)
    assert quote_in_source("go long ... rebalanced monthly", n)
    assert not quote_in_source("stop loss of 10%", n)


def test_grounding_removes_fabrications():
    st = ExtractedStrategy.model_validate(STAGE_B["strategies"][0])
    flags = ground_strategy(st, SOURCE_TEXT)
    assert st.rules["signal"].value == "positive 12-month excess return"
    assert st.rules["rebalance"].value == "monthly"
    assert st.rules["stop_rule"].value == UNKNOWN          # quote not in source
    assert st.rules["lookback"].value == UNKNOWN           # invented number
    assert "not_a_field" not in st.rules
    assert "claimed_sharpe" in st.claims and "claimed_cagr" not in st.claims  # 25% not in its quote
    params = {p.name: p.value for p in st.parameters}
    assert params == {"lookback_months": "12", "threshold": UNKNOWN}
    regimes = {r.regime: r for r in st.regimes}
    assert set(regimes) == {MarketRegime.BEARISH, MarketRegime.CRASH}  # invalid "sideways" dropped
    assert regimes[MarketRegime.BEARISH].basis is RegimeBasis.SOURCE_STATED
    assert regimes[MarketRegime.CRASH].basis is RegimeBasis.RATIONALE_INFERRED  # quote not found → downgraded
    assert regimes[MarketRegime.CRASH].confidence <= 0.5
    assert "stop_rule" in st.unknown_rules and "entry_rule" in st.unknown_rules
    assert any(f.startswith("UNGROUNDED_VALUE_REMOVED:stop_rule") for f in flags)
    assert any(f.startswith("UNSUPPORTED_NUMBER_REMOVED:lookback") for f in flags)


def test_section_selection_respects_budget_and_keeps_locations():
    r = parse_bytes(make_pdf(PAGES + ["Acknowledgements and unrelated text " * 50]), name="p.pdf")
    sel = select_relevant(r, max_chars=400)
    assert len(sel.text) <= 400 and "[p.1]" in sel.text and sel.truncated


# ---- end-to-end ------------------------------------------------------------------------------------

def test_extract_ideas_end_to_end(engine):
    result = parse_bytes(make_pdf(PAGES), name="tsmom.pdf")
    with session_scope(engine) as s:
        src = Source(title="Time Series Momentum", format="pdf")
        s.add(src)
        s.flush()
        sid = src.id
    fake = FakeProvider([STAGE_A, STAGE_B])
    gw = AIGateway(engine, settings(), {"fake": fake})
    rep = extract_ideas(gw, engine, settings(), sid, result)
    assert rep.deep_read and len(rep.idea_ids) == 1 and rep.flags
    assert all("<<UNTRUSTED_CONTENT" in c["user"] for c in fake.calls)  # source text always wrapped

    with session_scope(engine) as s:
        idea = s.get(Idea, rep.idea_ids[0])
        assert idea.rebalance == "monthly" and idea.stop_rule == UNKNOWN and idea.lookback == UNKNOWN
        assert idea.claimed_sharpe == "1.2" and idea.claimed_cagr is None
        assert idea.idea_quality_score is None  # scoring is M6, never guessed here
        assert idea.status is IdeaStatus.NEEDS_REVIEW  # several grounding flags
        regimes = {r.regime: r for r in idea.regimes}
        assert regimes[MarketRegime.BEARISH].suitability is RegimeSuitability.SUITED
        assert regimes[MarketRegime.BEARISH].source_fact_id is not None
        assert regimes[MarketRegime.BULLISH].suitability is RegimeSuitability.UNKNOWN
        fact = s.scalars(select(SourceFact).where(SourceFact.idea_id == idea.id,
                                                  SourceFact.fact_type == "REBALANCE")).one()
        assert fact.page == 2 and fact.quote == "rebalanced monthly"
        assert s.scalars(select(IdeaSource)).one().role.value == "DESCRIBES"


def test_triage_rejection_skips_expensive_stage(engine):
    result = parse_bytes(b"A cooking blog about pasta.", name="x.txt")
    with session_scope(engine) as s:
        src = Source(title="Pasta")
        s.add(src)
        s.flush()
        sid = src.id
    fake = FakeProvider([{**STAGE_A, "is_strategy_research": False, "worth_deep_read": False}])
    rep = extract_ideas(AIGateway(engine, settings(), {"fake": fake}), engine, settings(), sid, result)
    assert rep.skipped_reason == "TRIAGE_NOT_PROMISING" and len(fake.calls) == 1 and not rep.idea_ids


def test_rerun_extraction_hits_cache(engine):
    result = parse_bytes(make_pdf(PAGES), name="tsmom.pdf")
    with session_scope(engine) as s:
        src = Source(title="TSMOM")
        s.add(src)
        s.flush()
        sid = src.id
    fake = FakeProvider([STAGE_A, STAGE_B])
    gw = AIGateway(engine, settings(), {"fake": fake})
    extract_ideas(gw, engine, settings(), sid, result)
    rep2 = extract_ideas(gw, engine, settings(), sid, result)
    assert len(fake.calls) == 2 and rep2.cost_usd == 0  # second run fully served from cache
