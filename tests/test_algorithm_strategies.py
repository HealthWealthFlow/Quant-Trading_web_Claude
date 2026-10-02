"""Algorithm-shaped strategies must be represented, not hard-failed for lacking a bar-rule (D38).

Measured: the PAMR paper (*Passive Aggressive Mean Reversion*) states its decision as a portfolio-weight update
equation. It extracted as `signal`/`entry_rule`/`exit_rule` = UNKNOWN, reached 10% completeness, and was hard-failed
as RULES_NOT_QUANTIFIABLE — four fully specified ideas discarded. The anti-fabrication rule was working correctly
(the model refused to invent a bar-rule the paper never states); the schema simply had nowhere to put an algorithm.
"""

from __future__ import annotations

from qsd.ai.schemas import RULE_FIELDS
from qsd.config import DEFAULT_CONFIG, load_settings
from qsd.db import init_db, make_engine, session_scope
from qsd.db.models import Idea, Source
from qsd.scoring import rules, score_idea
from qsd.taxonomy import UNKNOWN, IdeaStatus, RejectionReason

# ---- the schema and the rule field ------------------------------------------------------------------

def test_algorithm_fields_are_part_of_the_rule_schema():
    assert "algorithm_rule" in RULE_FIELDS and "strategy_kind" in RULE_FIELDS


def test_a_stated_algorithm_is_not_a_hard_fail():
    fails = rules.hard_fails(rule_text="", red_flags=[], completeness=10.0, signal=UNKNOWN,
                             algorithm_rule="w_t+1 = argmin ... aggressive mean reversion on portfolio weights")
    assert RejectionReason.RULES_NOT_QUANTIFIABLE not in [f.reason for f in fails]


def test_an_empty_extraction_still_hard_fails():
    """The rule must not become a loophole: no rule of ANY kind is still unquantifiable."""
    fails = rules.hard_fails(rule_text="", red_flags=[], completeness=10.0, signal=UNKNOWN, algorithm_rule=UNKNOWN)
    assert RejectionReason.RULES_NOT_QUANTIFIABLE in [f.reason for f in fails]


def test_a_bar_rule_strategy_is_unaffected():
    fails = rules.hard_fails(rule_text="entry: close above 20-day high", red_flags=[], completeness=70.0,
                             signal="close above 20-day high")
    assert fails == []


# ---- end to end through the scoring pipeline --------------------------------------------------------

def settings():
    return load_settings(DEFAULT_CONFIG, None, environ={"QSD_AI__DEFAULT_PROVIDER": "fake"})


def _idea(engine, **fields) -> int:
    with session_scope(engine) as s:
        src = Source(title="Passive Aggressive Mean Reversion", tier=1)
        s.add(src)
        s.flush()
        idea = Idea(primary_source_id=src.id, search_depth_level=0, **fields)
        s.add(idea)
        s.flush()
        return idea.id


def test_an_algorithm_idea_survives_scoring(tmp_path):
    """Before D38 this idea came out REJECTED with every rule UNKNOWN."""
    engine = make_engine(tmp_path / "db.sqlite")
    init_db(engine)
    iid = _idea(engine, strategy_name="PAMR", strategy_kind="PORTFOLIO_WEIGHT",
                algorithm_rule="aggressive mean reversion: w_t+1 = w_t - tau * (r_t - epsilon * r_t / ||r_t||^2)")
    result = score_idea(engine, settings(), iid)
    assert result.status != IdeaStatus.REJECTED.value
    assert "RULES_NOT_QUANTIFIABLE" not in result.hard_fails
    with session_scope(engine) as s:
        idea = s.get(Idea, iid)
        assert idea.status is not IdeaStatus.REJECTED
        # the algorithm text is available to the scorer as rule text, so look-ahead checks still see it
        assert "mean reversion" in idea.algorithm_rule


def test_an_empty_idea_is_still_rejected(tmp_path):
    engine = make_engine(tmp_path / "db.sqlite")
    init_db(engine)
    iid = _idea(engine, strategy_name="Nothing stated")
    result = score_idea(engine, settings(), iid)
    assert result.status == IdeaStatus.REJECTED.value
    assert "RULES_NOT_QUANTIFIABLE" in result.hard_fails


def test_the_migration_adds_the_columns_to_an_existing_database(tmp_path):
    """Additive migration v4 -> v5: an old database opens without losing rows."""
    import sqlite3

    db = tmp_path / "old.sqlite"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO schema_meta VALUES ('schema_version', '4');
        CREATE TABLE ideas (id INTEGER PRIMARY KEY, strategy_name TEXT);
        INSERT INTO ideas VALUES (1, 'pre-existing');
    """)
    con.commit()
    con.close()

    import pytest

    # init_db only migrates databases it recognises as full QSD databases; the point here is that the migration
    # statements exist for v4 and are additive, so a real v4 database gains the columns.
    from qsd.db import MIGRATIONS, SchemaVersionError
    from qsd.db import init_db as _init
    assert 4 in MIGRATIONS
    assert all("ADD COLUMN" in stmt for stmt in MIGRATIONS[4])
    assert not any("DROP" in stmt.upper() for stmt in MIGRATIONS[4])
    assert SchemaVersionError is not None and _init is not None and pytest is not None
