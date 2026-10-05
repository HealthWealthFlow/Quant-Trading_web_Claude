import json

import pytest

from qsd.config import DEFAULT_CONFIG, load_settings
from qsd.db import init_db, make_engine, new_idea, session_scope
from qsd.db.models import Idea, IdeaSource, Source, SourceFact
from qsd.packaging import DOWNSTREAM_WARNING, ResearchPackage, build_package, export_schema, submit_to_queue
from qsd.scoring import score_idea
from qsd.taxonomy import (
    ExtractionMethod,
    IdeaSourceRole,
    IdeaStatus,
    MarketRegime,
    RegimeBasis,
    RegimeSuitability,
    TimeHorizon,
)

GOOD = dict(
    strategy_name="Time-series momentum", summary="Trend following in liquid index futures.",
    asset_classes=["FUTURES"], strategy_families=["TIME-SERIES MOMENTUM"], time_horizon=TimeHorizon.MONTHLY,
    instrument="S&P 500 index futures", universe="liquid index futures", signal="positive 12-month return",
    entry_rule="go long when 12-month return > 0", exit_rule="exit when 12-month return < 0", rebalance="monthly",
    lookback="12 months", position_sizing="volatility scaled", economic_rationale="investor underreaction",
    rationale_confidence=0.8, transaction_cost_assumption="1bp per trade", claimed_sharpe="1.2",
)
ABSTRACT = "Data from 1985 to 2009 across 58 markets; robust out-of-sample and after transaction costs."


@pytest.fixture
def env(tmp_path):
    s = load_settings(DEFAULT_CONFIG, None, environ={"QSD_HANDOFF__QUEUE_DIR": str(tmp_path / "queue")})
    e = make_engine(tmp_path / "db.sqlite")
    init_db(e)
    return s, e, tmp_path


def make(engine, **overrides):
    with session_scope(engine) as s:
        n = len(s.query(Source).all())
        src = Source(title="TSMOM", author="Moskowitz", publication_date="2012", tier=1,
                     canonical_url=f"https://doi.org/10.1/tsmom{n}", content_hash="ab" * 32)
        s.add(src)
        s.flush()
        for t, v in (("ID_DOI", "10.1/tsmom"), ("ABSTRACT", ABSTRACT)):
            s.add(SourceFact(source_id=src.id, fact_type=t, value=v, extraction_method=ExtractionMethod.DETERMINISTIC,
                             confidence=1.0))
        idea = new_idea(primary_source_id=src.id, **{**GOOD, **overrides})
        s.add(idea)
        s.flush()
        s.add(IdeaSource(idea_id=idea.id, source_id=src.id, role=IdeaSourceRole.DESCRIBES))
        s.add(SourceFact(source_id=src.id, idea_id=idea.id, fact_type="CLAIMED_SHARPE", value="1.2",
                         quote="Sharpe ratio of 1.2", page=7, extraction_method=ExtractionMethod.AI_STRONG,
                         confidence=0.9))
        s.add(SourceFact(source_id=src.id, idea_id=idea.id, fact_type="ENTRY_RULE", value=GOOD["entry_rule"],
                         quote="long when the past 12-month return is positive", page=3,
                         extraction_method=ExtractionMethod.AI_STRONG, confidence=0.95))
        for r in idea.regimes:
            if r.regime is MarketRegime.CRASH:
                r.suitability, r.basis, r.confidence = RegimeSuitability.SUITED, RegimeBasis.SOURCE_STATED, 0.9
        return idea.id


def test_package_contents(env):
    s, e, _ = env
    iid = make(e)
    score_idea(e, s, iid)
    pkg = build_package(e, s, iid)
    assert pkg.warning == DOWNSTREAM_WARNING
    assert pkg.known_rules["entry_rule"]["page"] == 3 and pkg.known_rules["entry_rule"]["quote"]
    assert "stop_rule" in pkg.unknown_rules
    assert pkg.source_claims["claims"]["claimed_sharpe"]["validated"] is False
    assert pkg.provenance["primary_source"]["doi"] == "10.1/tsmom" and pkg.provenance["provenance_known"]
    crash = next(r for r in pkg.market_regimes if r["regime"] == "CRASH")
    assert (crash["suitability"], crash["basis"]) == ("SUITED", "SOURCE_STATED")
    assert "continuous-contract roll methodology" in pkg.downstream_checks
    assert pkg.research_completeness["checks"]["contradiction_search"] == "FAIL"  # not done yet: said so honestly
    assert "capacity" in pkg.scores["unscored_components"]
    ResearchPackage.model_validate_json(pkg.model_dump_json())  # round-trips against its own schema


def test_submit_eligible_idea_writes_queue_file(env):
    s, e, tmp = env
    iid = make(e)
    # A source that states the decision and the risk control is ready to hand over, which is now a distinct
    # status from merely scoring well (D52).
    assert score_idea(e, s, iid).status == "READY_FOR_FORMALIZATION"
    ok, path, reasons = submit_to_queue(e, s, iid)
    assert ok and reasons == [] and path.parent == tmp / "queue" / "pending"
    data = json.loads(path.read_text())
    assert data["warning"] == DOWNSTREAM_WARNING and data["handoff"]["eligible"]
    with session_scope(e) as sess:
        assert sess.get(Idea, iid).status is IdeaStatus.SUBMITTED_TO_BACKTEST
    assert list((tmp / "queue" / "pending").glob("*.tmp")) == []  # atomic write left no temp files


def test_ineligible_ideas_blocked_with_reasons(env):
    """An idea whose entry the source never stated cannot be handed over: there would be nothing of the
    source left to test, however complete the rest of its fields look."""
    s, e, tmp = env
    vague = make(e, strategy_name="vague", signal="UNKNOWN", instrument="UNKNOWN", entry_rule="UNKNOWN",
                 exit_rule="UNKNOWN", lookback="UNKNOWN", position_sizing="UNKNOWN", rebalance="UNKNOWN",
                 transaction_cost_assumption="UNKNOWN")
    score_idea(e, s, vague)
    ok, path, reasons = submit_to_queue(e, s, vague)
    assert not ok and path is None
    assert any("no entry rule stated by the source" in r for r in reasons)
    hft = make(e, strategy_name="hft", signal="order book imbalance", time_horizon=TimeHorizon.HFT)
    score_idea(e, s, hft)
    assert any("latency" in r for r in submit_to_queue(e, s, hft)[2])
    assert not (tmp / "queue" / "pending").exists() or not list((tmp / "queue" / "pending").glob("*.json"))


def test_claims_do_not_affect_eligibility(env):
    s, e, _ = env
    a = make(e, claimed_sharpe=None)
    score_idea(e, s, a)
    assert build_package(e, s, a).handoff["eligible"]


def test_schema_export(tmp_path):
    p = export_schema(tmp_path / "schema.json")
    schema = json.loads(p.read_text())
    assert "source_claims" in schema["properties"] and "warning" in schema["required"]
