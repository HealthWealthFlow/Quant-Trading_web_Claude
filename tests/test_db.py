import re

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError, StatementError

from qsd.db import (
    SchemaVersionError,
    check_schema,
    init_db,
    make_engine,
    new_idea,
    session_scope,
    table_counts,
)
from qsd.db.models import (
    MAX_QUOTE_CHARS,
    SCHEMA_VERSION,
    AICall,
    Base,
    Idea,
    IdeaRegime,
    Rejection,
    SchemaMeta,
    Source,
    SourceFact,
)
from qsd.taxonomy import (
    UNKNOWN,
    AccessStatus,
    ExtractionMethod,
    IdeaStatus,
    MarketRegime,
    RegimeBasis,
    RegimeSuitability,
    RejectionReason,
)


@pytest.fixture
def engine(tmp_path):
    eng = make_engine(tmp_path / "t.sqlite")
    init_db(eng)
    return eng


def test_init_creates_all_tables_and_version(engine):
    tables = set(inspect(engine).get_table_names())
    assert {t.name for t in Base.metadata.sorted_tables} <= tables
    check_schema(engine)
    assert init_db(engine) == SCHEMA_VERSION  # idempotent


def test_schema_version_mismatch_refused(engine):
    with session_scope(engine) as s:
        s.get(SchemaMeta, "schema_version").value = "999"
    with pytest.raises(SchemaVersionError):
        init_db(engine)


def test_idea_defaults_are_unknown_not_guessed(engine):
    with session_scope(engine) as s:
        s.add(new_idea(strategy_name="Cross-sectional momentum"))
    with session_scope(engine) as s:
        idea = s.scalars(select(Idea)).one()
        for field in ("lookback", "entry_rule", "exit_rule", "rebalance", "economic_rationale", "universe"):
            assert getattr(idea, field) == UNKNOWN
        assert idea.idea_quality_score is None  # not scored yet, not zero
        assert idea.claimed_cagr is None
        assert idea.status is IdeaStatus.DISCOVERED
        assert {r.regime for r in idea.regimes} == set(MarketRegime)
        assert all(r.suitability is RegimeSuitability.UNKNOWN and r.basis is RegimeBasis.UNKNOWN
                   for r in idea.regimes)


def test_claims_stored_as_claims(engine):
    with session_scope(engine) as s:
        s.add(new_idea(strategy_name="X", claimed_cagr="25%"))
    with session_scope(engine) as s:
        idea = s.scalars(select(Idea)).one()
        assert idea.claimed_cagr == "25%"
        assert not hasattr(idea, "cagr")


def test_regime_unique_per_idea(engine):
    with pytest.raises(IntegrityError), session_scope(engine) as s:
        idea = new_idea(strategy_name="X")
        idea.regimes.append(IdeaRegime(regime=MarketRegime.CRASH))
        s.add(idea)


def test_fact_provenance_and_quote_limit(engine):
    with session_scope(engine) as s:
        src = Source(title="Paper", url="https://example.org/p.pdf", access_status=AccessStatus.OK)
        s.add(src)
        s.flush()
        s.add(SourceFact(source_id=src.id, fact_type="ENTRY_RULE", value="buy top decile", page=14,
                         extraction_method=ExtractionMethod.AI_STRONG, confidence=0.96))
    with pytest.raises(ValueError):
        SourceFact(source_id=1, fact_type="Q", value="v", extraction_method=ExtractionMethod.MANUAL,
                   confidence=0.5, quote="x" * (MAX_QUOTE_CHARS + 1))


def test_confidence_bounds_enforced(engine):
    with pytest.raises(IntegrityError), session_scope(engine) as s:
        src = Source()
        s.add(src)
        s.flush()
        s.add(SourceFact(source_id=src.id, fact_type="X", value="v",
                         extraction_method=ExtractionMethod.MANUAL, confidence=1.5))


def test_invalid_enum_value_rejected(engine):
    with pytest.raises(StatementError), session_scope(engine) as s:
        s.add(Rejection(idea_id=1, reason="NOT_A_REASON"))


def test_source_tier_range(engine):
    with pytest.raises(IntegrityError), session_scope(engine) as s:
        s.add(Source(tier=7))


def test_rejection_and_ai_ledger_round_trip(engine):
    with session_scope(engine) as s:
        idea = new_idea(strategy_name="Double after loss")
        s.add(idea)
        s.flush()
        s.add(Rejection(idea_id=idea.id, reason=RejectionReason.MARTINGALE, is_hard_fail=True))
        s.add(AICall(provider="deepseek", model="deepseek-chat", task="stage_a", prompt_version="v1",
                     cache_key="k" * 64, input_tokens=1000, output_tokens=200, cost_usd=0.0004))
    counts = table_counts(engine)
    assert counts["rejections"] == 1 and counts["ai_calls"] == 1 and counts["idea_regimes"] == 4


SECRET_COLUMN = re.compile(r"(password|passwd|cookie|token|secret|api_?key|authorization|session_?id|csrf)", re.I)

ALLOWED_TOKEN_COUNTS = {"input_tokens", "output_tokens"}  # AI usage counts, not credentials


def test_no_secret_columns_in_schema():
    offenders = [f"{t.name}.{c.name}" for t in Base.metadata.sorted_tables for c in t.columns
                 if SECRET_COLUMN.search(c.name) and c.name not in ALLOWED_TOKEN_COUNTS]
    assert offenders == []
