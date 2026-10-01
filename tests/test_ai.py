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
    reground_source,
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
    rep = ground_strategy(st, SOURCE_TEXT)
    flags = rep.flags
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
    removed = {r["field"]: r for r in rep.removed}
    assert removed["stop_rule"] == {"field": "stop_rule", "value": "exit after 10% loss", "quote": "stop loss of 10%",
                                    "location": "p.2", "reason": "QUOTE_NOT_FOUND"}
    assert removed["lookback"]["reason"] == "NUMBER_NOT_IN_SOURCE"
    assert removed["claimed_cagr"]["reason"] == "NUMBER_NOT_IN_SOURCE"
    assert removed["regime:CRASH"]["quote"] == "always profits in crashes"
    assert set(removed) == {"stop_rule", "lookback", "parameter:threshold", "claimed_cagr", "regime:CRASH"}
    assert (rep.attempted, rep.problems) == (10, 5) and rep.needs_review  # half of what the model offered failed


LONG_SOURCE = ("We hold a broad stock and bond index fund in a tax deferred retirement account that is rebalanced "
               "annually. Each year, 10% of the account is allocated towards an inverse stock fund.")


def _one_rule(key, value, quote):
    return ExtractedStrategy.model_validate({"strategy_name": "x", "rules": {key: {"value": value,
                                                                                  "evidence_quote": quote}}})


def test_small_copying_slips_are_realigned_to_source_wording():
    # dropped "that is", "a" → "the": same words, same order, one stretch
    st = _one_rule("rebalance", "annually", "hold the broad stock and bond index fund in a tax deferred retirement "
                                            "account rebalanced annually")
    rep = ground_strategy(st, LONG_SOURCE)
    assert st.rules["rebalance"].value == "annually"
    assert st.rules["rebalance"].evidence_quote == ("hold a broad stock and bond index fund in a tax deferred "
                                                    "retirement account that is rebalanced annually.")
    assert rep.realigned == 1 and "QUOTE_REALIGNED:rebalance" in rep.flags and not rep.removed


def test_alignment_never_approximates_numbers_or_short_quotes():
    st = _one_rule("position_sizing", "20%", "Each year, 20% of the account is allocated towards an inverse fund")
    assert ground_strategy(st, LONG_SOURCE).removed[0]["reason"] == "QUOTE_NOT_FOUND"
    st = _one_rule("rebalance", "annually", "rebalanced every year")  # paraphrase, too short to align
    assert ground_strategy(st, LONG_SOURCE).removed and st.rules["rebalance"].value == UNKNOWN
    st = _one_rule("universe", "stocks", "we buy small cap stocks with high momentum and hold them for a year")
    assert ground_strategy(st, LONG_SOURCE).removed  # unrelated sentence


def test_claim_number_must_be_in_its_own_quote():
    st = ExtractedStrategy.model_validate({"strategy_name": "x", "claims": {"claimed_sharpe": {
        "value": "10", "evidence_quote": "rebalanced annually"}}})  # 10 is in the source, not in this quote
    rep = ground_strategy(st, LONG_SOURCE)
    assert st.claims == {} and rep.removed[0]["reason"] == "CLAIM_NUMBER_NOT_IN_QUOTE"


def test_a_few_removed_values_do_not_trigger_review():
    rules = {"instrument": {"value": "index funds", "evidence_quote": "broad stock and bond index fund"},
             "rebalance": {"value": "annually", "evidence_quote": "rebalanced annually"},
             "position_sizing": {"value": "10%", "evidence_quote": "10% of the account"},
             "stop_rule": {"value": "5% stop", "evidence_quote": "a 5% stop loss"}}
    st = ExtractedStrategy.model_validate({"strategy_name": "x", "rules": rules})
    rep = ground_strategy(st, LONG_SOURCE)
    assert (rep.attempted, rep.problems) == (4, 1) and not rep.needs_review


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
        assert idea.status is IdeaStatus.NEEDS_REVIEW  # half of the offered values failed grounding
        assert "UNRELIABLE_EXTRACTION" in idea.red_flags
        assert not any(f.startswith("UNGROUNDED") for f in idea.red_flags)  # reported in idea.grounding instead
        assert len(idea.grounding["removed"]) == 5 and idea.grounding["prompt_version"] == "b2"
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


GOOD_B = {"strategies": [{
    "strategy_name": "Time-series momentum", "summary": "Long positive 12m return assets",
    "asset_classes": ["FUTURES"], "position_direction": "LONG_SHORT",
    "rules": {
        "signal": {"value": "positive 12-month excess return",
                   "evidence_quote": "long assets with positive 12-month excess return", "location": "p.1"},
        # small copying slip ("get" for "are"): realigned to the source's wording
        "rebalance": {"value": "monthly", "evidence_quote": "Positions get rebalanced monthly. The strategy earned a",
                      "location": "p.2"},
        "stop_rule": {"value": "exit after 10% loss", "evidence_quote": "stop loss of 10%", "location": "p.2"},
    },
    "parameters": [{"name": "lookback_months", "value": "12", "evidence_quote": "12-month excess return"}],
    "claims": {"claimed_sharpe": {"value": "1.2", "evidence_quote": "earned a Sharpe ratio of 1.2"}},
    "regimes": [{"regime": "BEARISH", "suitability": "SUITED", "basis": "SOURCE_STATED", "confidence": 0.9,
                 "evidence_quote": "performs best in extended bear markets", "location": "p.3"}],
}]}


def _source(engine, title="TSMOM"):
    with session_scope(engine) as s:
        src = Source(title=title)
        s.add(src)
        s.flush()
        return src.id


def test_reground_fixes_old_ideas_without_calling_the_model(engine):
    result = parse_bytes(make_pdf(PAGES), name="tsmom.pdf")
    sid = _source(engine)
    fake = FakeProvider([STAGE_A, GOOD_B])
    rep = extract_ideas(AIGateway(engine, settings(), {"fake": fake}), engine, settings(), sid, result)
    iid = rep.idea_ids[0]
    with session_scope(engine) as s:
        idea = s.get(Idea, iid)
        assert idea.status is IdeaStatus.DISCOVERED and idea.rebalance == "monthly"  # 1 of 6 removed: no review
        assert idea.grounding["realigned"] == ["rebalance"]
        # make it look like an idea stored by the old (exact-only, 3-flag) rules
        idea.rebalance, idea.status, idea.grounding = UNKNOWN, IdeaStatus.NEEDS_REVIEW, {}
        idea.red_flags = ["UNGROUNDED_VALUE_REMOVED:rebalance", "UNGROUNDED_VALUE_REMOVED:stop_rule",
                          "UNSUPPORTED_NUMBER_REMOVED:lookback", "PROMISSORY_LANGUAGE"]

    for _ in range(2):  # idempotent
        rg = reground_source(engine, settings(), sid, result)
    assert len(fake.calls) == 2  # the model was not called again
    assert rg.idea_ids == [iid]
    with session_scope(engine) as s:
        idea = s.get(Idea, iid)
        assert idea.rebalance == "monthly" and idea.stop_rule == UNKNOWN
        assert idea.status is IdeaStatus.DISCOVERED
        assert idea.red_flags == ["PROMISSORY_LANGUAGE"]  # non-grounding flags survive
        assert [r["field"] for r in idea.grounding["removed"]] == ["stop_rule"]
        fact = s.scalars(select(SourceFact).where(SourceFact.idea_id == iid,
                                                  SourceFact.fact_type == "REBALANCE")).one()  # no duplicates
        assert fact.quote.startswith("Positions are rebalanced monthly")
        assert {r.regime: r for r in idea.regimes}[MarketRegime.BEARISH].source_fact_id is not None


def test_reground_without_stored_extraction_is_skipped(engine):
    sid = _source(engine)
    rg = reground_source(engine, settings(), sid, parse_bytes(make_pdf(PAGES), name="x.pdf"))
    assert rg.skipped_reason == "NO_STORED_EXTRACTION" and not rg.idea_ids


# Real text from the first live paper: the PDF extracted with spaces missing between words.
GLUED_SOURCE = ('Thus, the "Ruleof120"hasappearedintheliterature.Thissuggeststhatforthestockallocationshould '
                "be120minustheageoftheindividual.Therulehasbeenmodifiedbyothers,fromabout100 to 130, to accommodate "
                "different economic and investment cycles. Table 1 was formatted to reflect the "
                "strategicassetallocationsusedinthisstudy.Additionally,theinvestorisassumedtoannually rebalance "
                "their portfolio to these allocations over a 10-year investment period. "
                "Annualfeeswereassumedat1%,andwere deducted from monthly returns")


def test_quotes_match_source_text_with_missing_spaces():
    st = ExtractedStrategy.model_validate({"strategy_name": "x", "rules": {
        "rebalance": {"value": "Annually", "evidence_quote": "the investor is assumed to annually rebalance their "
                                                             "portfolio to these allocations"},
        "portfolio_rules": {"value": "Stock allocation = 120 minus age", "evidence_quote":
                            "This suggests that for the stock allocation should be 120 minus the age of the "
                            "individual"}},
        "parameters": [{"name": "annual_fee", "value": "1%",
                        "evidence_quote": "Annual fees were assumed at 1%, and were deducted from monthly returns"}]})
    rep = ground_strategy(st, GLUED_SOURCE)
    assert rep.removed == [] and st.rules["rebalance"].value == "Annually"
    assert st.rules["portfolio_rules"].value == "Stock allocation = 120 minus age"
    assert st.parameters[0].value == "1%"


def test_space_insensitive_match_still_needs_the_same_characters():
    st = ExtractedStrategy.model_validate({"strategy_name": "x", "rules": {
        "rebalance": {"value": "monthly", "evidence_quote": "the investor is assumed to monthly rebalance"},
        "lookback": {"value": "130", "evidence_quote": "from about 130 to 100"},  # reordered numbers
        "signal": {"value": "x", "evidence_quote": "be 12 0"}}})  # too short for space-insensitive matching
    rep = ground_strategy(st, GLUED_SOURCE)
    assert {r["field"] for r in rep.removed} == {"rebalance", "lookback", "signal"}


def test_alignment_rejects_a_changed_word_the_value_depends_on():
    src = "Additionally, the investor is assumed to annually rebalance their portfolio to these allocations."
    st = _one_rule("rebalance", "monthly", "the investor is assumed to monthly rebalance their portfolio")
    rep = ground_strategy(st, src)
    assert st.rules["rebalance"].value == UNKNOWN and rep.removed[0]["reason"] == "QUOTE_NOT_FOUND"
    st = _one_rule("rebalance", "annually", "the investor is assumed to annually rebalance the portfolio")
    assert not ground_strategy(st, src).removed  # "the" vs "their" is a harmless slip
