import pytest
from sqlalchemy import select

from qsd.config import DEFAULT_CONFIG, load_settings
from qsd.db import init_db, make_engine, new_idea, session_scope
from qsd.db.models import Idea, IdeaSource, IdeaStatusHistory, Rejection, Source, SourceFact
from qsd.scoring import score_idea
from qsd.scoring.rules import find_red_flags, formalization_completeness, hard_fails, parameter_complexity
from qsd.taxonomy import (
    UNKNOWN,
    ExtractionMethod,
    IdeaSourceRole,
    IdeaStatus,
    MarketRegime,
    RegimeBasis,
    RegimeSuitability,
    RejectionReason,
    TimeHorizon,
)

S = load_settings(DEFAULT_CONFIG, None, environ={})

GOOD = dict(
    strategy_name="Time-series momentum", summary="Trend following in liquid index futures.",
    asset_classes=["FUTURES"], strategy_families=["TIME-SERIES MOMENTUM"], time_horizon=TimeHorizon.MONTHLY,
    instrument="S&P 500 index futures", universe="liquid index futures", signal="positive 12-month return",
    entry_rule="go long when 12-month return > 0", exit_rule="exit when 12-month return < 0", rebalance="monthly",
    lookback="12 months", position_sizing="volatility scaled to 10%", economic_rationale="investor underreaction",
    rationale_confidence=0.8, transaction_cost_assumption="1bp per trade",
)


@pytest.fixture
def engine(tmp_path):
    e = make_engine(tmp_path / "db.sqlite")
    init_db(e)
    return e


def add(engine, abstract="", ids=None, tier=1, **fields):
    with session_scope(engine) as s:
        src = Source(title="Paper", author="A. Author", publication_date="2012", tier=tier)
        s.add(src)
        s.flush()
        for k, v in (ids or {}).items():
            s.add(SourceFact(source_id=src.id, fact_type=k, value=v, extraction_method=ExtractionMethod.DETERMINISTIC,
                             confidence=1.0))
        if abstract:
            s.add(SourceFact(source_id=src.id, fact_type="ABSTRACT", value=abstract,
                             extraction_method=ExtractionMethod.DETERMINISTIC, confidence=1.0))
        idea = new_idea(primary_source_id=src.id, **fields)
        s.add(idea)
        s.flush()
        s.add(IdeaSource(idea_id=idea.id, source_id=src.id, role=IdeaSourceRole.DESCRIBES))
        return idea.id, src.id


# ---- rules -------------------------------------------------------------------------------------------------

def test_red_flags():
    text = "Guaranteed profit! Our holy grail never loses. Double your position after every loss."
    assert {"GUARANTEED_PROFIT", "HOLY_GRAIL", "NEVER_LOSES", "DOUBLE_AFTER_LOSS"} <= set(find_red_flags(text))
    assert find_red_flags("A study of momentum with transaction costs.") == []


def test_hard_fails():
    reasons = lambda *a: {f.reason for f in hard_fails(*a)}  # noqa: E731
    assert RejectionReason.MARTINGALE in reasons("", ["MARTINGALE"], 80, "x")
    assert RejectionReason.LOOKAHEAD_REQUIRED in reasons("buy when tomorrow's close is higher", [], 80, "x")
    assert RejectionReason.OBVIOUS_SURVIVORSHIP_BIAS in reasons("universe: current S&P 500 constituents since 1990",
                                                                [], 80, "x")
    assert RejectionReason.SCAM_SIGNAL_SOURCE in reasons("", ["GUARANTEED_PROFIT", "NEVER_LOSES"], 80, "x")
    assert RejectionReason.RULES_NOT_QUANTIFIABLE in reasons("", [], 10, UNKNOWN)
    assert reasons("go long when 12-month return > 0", [], 80, "x") == set()


def test_completeness_and_complexity():
    idea = new_idea(**GOOD)
    score, missing = formalization_completeness(idea)
    assert (score, missing) == (100.0, [])
    bare = new_idea(strategy_name="momentum")
    assert formalization_completeness(bare)[0] == 0.0 and len(formalization_completeness(bare)[1]) == 8
    complex_idea = new_idea(entry_rule="RSI < 30 and MACD crosses above signal and ATR above 2, only on Monday "
                                       "at 10:30, VIX below 20", parameters={"a": "1", "b": "2", "c": "3"})
    assert parameter_complexity(complex_idea)[0] >= 60


# ---- pipeline ----------------------------------------------------------------------------------------------

def test_good_idea_scored_with_breakdown_and_no_claims_used(engine):
    abstract = ("We use data from 1985 to 2009 across 58 markets. Results hold out-of-sample and after "
                "transaction costs.")
    iid, sid = add(engine, abstract, ids={"ID_DOI": "10.1/TSMOM"}, **GOOD, claimed_cagr="999%")
    r = score_idea(engine, S, iid)
    with session_scope(engine) as s:
        idea = s.get(Idea, iid)
        d = idea.score_details
        assert idea.root_evidence_id == "doi:10.1/tsmom"
        assert s.get(Source, sid).root_evidence_id == idea.root_evidence_id
        assert d["idea_quality"]["components"]["capacity"]["value"] is None       # never guessed
        assert "capacity" in d["idea_quality"]["unscored"]
        assert set(d["evidence_quality"]["present"]) == {"cross_market", "out_of_sample", "costs_considered",
                                                         "sample_period"}
        assert idea.source_quality >= 80 and idea.formalization_completeness >= 90
        assert "999" not in str(d)  # claimed performance plays no part in scoring
    # The band still reports how promising it looks; the status now records that the setup is runnable (D52).
    assert r.coverage >= 0.6 and r.band in ("HIGH_PRIORITY", "PROMISING")
    assert r.status in ("PROMISING", "READY_FOR_FORMALIZATION")
    # claims don't change the score
    iid2, _ = add(engine, abstract, ids={"ID_DOI": "10.1/OTHER"}, **{**GOOD, "strategy_name": "B",
                                                                       "signal": "different signal b"})
    assert score_idea(engine, S, iid2).idea_quality == r.idea_quality


def test_martingale_rejected_and_rejection_kept(engine):
    iid, _ = add(engine, "Double your position after every loss until you win.", tier=None,
                 strategy_name="Recovery grid", asset_classes=["FOREX"], signal="price moves against us")
    r = score_idea(engine, S, iid)
    assert r.status == "REJECTED" and "MARTINGALE" in r.hard_fails
    score_idea(engine, S, iid)  # re-scoring doesn't duplicate rejection rows
    with session_scope(engine) as s:
        rows = s.scalars(select(Rejection).where(Rejection.idea_id == iid)).all()
        assert [x.reason for x in rows].count(RejectionReason.MARTINGALE) == 1
        hist = s.scalars(select(IdeaStatusHistory).where(IdeaStatusHistory.idea_id == iid)).all()
        assert hist[-1].to_status is IdeaStatus.REJECTED


def test_duplicate_detection(engine):
    a, _ = add(engine, **GOOD)
    b, _ = add(engine, **GOOD)
    c, _ = add(engine, **{**GOOD, "lookback": "6 months", "entry_rule": "go long when 6-month return > 0"})
    score_idea(engine, S, a)
    assert score_idea(engine, S, b).status == "DUPLICATE"
    score_idea(engine, S, c)
    with session_scope(engine) as s:
        assert s.get(Idea, b).duplicate_of_id == a and s.get(Idea, b).novelty_score == 0
        assert s.get(Idea, c).dedupe_class == "VARIANT"


def test_low_coverage_goes_to_research_not_archive(engine):
    iid, _ = add(engine, strategy_name="Vague idea", signal="buy strength", asset_classes=[])
    r = score_idea(engine, S, iid)
    assert r.coverage < 0.6 and r.status == "RESEARCHING"
    with session_scope(engine) as s:
        assert "assess:" in s.get(Idea, iid).next_research_action


def test_replication_counts_only_independent_roots(engine):
    iid, sid = add(engine, **GOOD, ids={"ID_DOI": "10.1/orig"})
    with session_scope(engine) as s:
        copy = Source(title="Blog about same paper", root_evidence_id="doi:10.1/orig")
        indep = Source(title="International replication", root_evidence_id="doi:10.2/replication")
        s.add_all([copy, indep])
        s.flush()
        s.add(IdeaSource(idea_id=iid, source_id=copy.id, role=IdeaSourceRole.SUPPORTS))
        s.add(IdeaSource(idea_id=iid, source_id=indep.id, role=IdeaSourceRole.REPLICATES))
    score_idea(engine, S, iid)
    with session_scope(engine) as s:
        assert s.get(Idea, iid).replication_score == 50.0  # the blog copy is not independent evidence


def test_regime_inferred_only_flag_and_tags(engine):
    iid, _ = add(engine, **GOOD)
    with session_scope(engine) as s:
        idea = s.get(Idea, iid)
        for r in idea.regimes:
            if r.regime is MarketRegime.CRASH:
                r.suitability, r.basis, r.confidence = RegimeSuitability.SUITED, RegimeBasis.RATIONALE_INFERRED, 0.4
    score_idea(engine, S, iid)
    with session_scope(engine) as s:
        idea = s.get(Idea, iid)
        assert "REGIME_INFERRED_ONLY" in idea.red_flags
        assert {"MOMENTUM", "REGIME:CRASH"} <= set(idea.diversification_tags)


def test_weights_must_sum_to_100():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        load_settings(DEFAULT_CONFIG, None, environ={"QSD_SCORING__IDEA_WEIGHTS__CAPACITY": "50"})
