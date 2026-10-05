"""Setup readiness: the decision rule must come from the source; the plumbing may be derived.

These tests pin the boundary that makes the "fill the gaps" feature safe. If a future change lets a
derived value satisfy `entry` or `exit`, an idea whose strategy we invented would start looking like an
idea the source specified, and the backtester would be measuring our invention.
"""

from __future__ import annotations

from qsd.db.models import Idea
from qsd.scoring import readiness
from qsd.taxonomy import UNKNOWN


def _idea(**fields) -> Idea:
    base: dict = {"strategy_name": "t"}
    base.update(fields)
    return Idea(**base)


def test_decision_rule_is_never_bridgeable():
    """Entry and exit stay blocking: no stated context can conjure a decision rule."""
    idea = _idea(asset_classes=["STOCK"], time_horizon="MULTI_DAY", position_direction="LONG")
    result = readiness.setup_readiness(idea)
    assert "entry" in result["blocking_missing"]
    assert "exit" in result["blocking_missing"]
    assert result["runnable"] is False


def test_plumbing_gaps_do_not_block_a_stated_strategy():
    """The source states the decision; the conventions are derivable, so the setup is runnable."""
    idea = _idea(
        asset_classes=["STOCK"],
        time_horizon="MULTI_DAY",
        position_direction="LONG",
        universe="S&P 500 members",
        entry_rule="buy when close crosses above the 20-day high",
        exit_rule="sell at a 2x ATR trailing stop",
    )
    result = readiness.setup_readiness(idea)
    assert result["runnable"] is True
    # Sizing, execution, instrument and scope were never stated; they are the bridgeable gaps.
    assert "sizing" in result["bridgeable_missing"]
    assert "execution" in result["bridgeable_missing"]
    assert result["blocking_missing"] == []


def test_readiness_never_exceeds_completeness_without_stated_context():
    """With nothing stated there is nothing to derive from, so gaps stay gaps."""
    idea = _idea()
    result = readiness.setup_readiness(idea)
    assert result["readiness"] == 0.0
    assert result["bridgeable_missing"] == []
    assert "instrument" in result["blocking_missing"]
    assert result["runnable"] is False


def test_algorithm_strategy_counts_as_having_a_decision():
    """An update equation is a complete decision (D38), so a portfolio-weight strategy is not 'undecided'."""
    idea = _idea(
        asset_classes=["STOCK"],
        universe="30 largest crypto pairs",
        strategy_kind="PORTFOLIO_WEIGHT",
        algorithm_rule="w_{t+1} = w_t - eta * (r_t - r_bar) * x_t",
    )
    result = readiness.setup_readiness(idea)
    assert "entry" not in result["blocking_missing"]
    # Its exit is still unstated and no context derives one, so it remains a real gap.
    assert "exit" in result["blocking_missing"]


def test_instrument_and_universe_are_bridgeable_only_from_stated_context():
    """Scope may be chosen from a stated asset class or instrument, never from nothing."""
    without_context = readiness.setup_readiness(_idea(entry_rule="e", exit_rule="x"))
    assert "instrument" not in without_context["bridgeable_missing"]
    assert "universe" not in without_context["bridgeable_missing"]
    assert "universe" in without_context["blocking_missing"]

    with_class = readiness.setup_readiness(
        _idea(asset_classes=["STOCK"], entry_rule="e", exit_rule="x")
    )
    assert "instrument" in with_class["bridgeable_missing"]
    assert "universe" in with_class["bridgeable_missing"]

    # A stated instrument scopes the universe too: "S&P 500 Index" -> that index's members.
    from_instrument = readiness.setup_readiness(
        _idea(instrument="S&P 500 Index", entry_rule="e", exit_rule="x")
    )
    assert "universe" in from_instrument["bridgeable_missing"]


def test_a_stated_universe_is_taken_from_the_source_never_derived():
    """When the source states the universe it is recorded as from_source, not as a gap to fill."""
    idea = _idea(
        asset_classes=["STOCK"],
        universe="Ten Nifty 50 large caps",
        entry_rule="e",
        exit_rule="x",
    )
    result = readiness.setup_readiness(idea)
    assert "universe" in result["from_source"]
    assert "universe" not in result["bridgeable_missing"]


def test_readiness_is_additive_to_completeness_and_never_replaces_it():
    """The score keeps using completeness, so deriving values cannot inflate quality."""
    from qsd.scoring.rules import formalization_completeness

    idea = _idea(
        asset_classes=["STOCK"],
        time_horizon="MULTI_DAY",
        universe="S&P 500 members",
        entry_rule="buy the breakout",
        exit_rule="sell the breakdown",
    )
    source_score, _ = formalization_completeness(idea)
    result = readiness.setup_readiness(idea)
    # The setup is more complete than the source, which is the entire point, and the two numbers differ.
    assert result["readiness"] > source_score
    assert source_score == 50.0  # universe 10 + entry 20 + exit 20; nothing else was written down
    assert result["readiness"] == 100.0  # every bridgeable gap can be derived from the stated context
    assert idea.formalization_completeness is None  # readiness never writes the scored field


def test_unknown_placeholder_is_not_treated_as_a_known_value():
    """The literal UNKNOWN string must never satisfy a component, even when scope could be derived."""
    idea = _idea(
        asset_classes=["STOCK"],
        instrument=UNKNOWN,
        universe=UNKNOWN,
        entry_rule=UNKNOWN,
        exit_rule=UNKNOWN,
    )
    result = readiness.setup_readiness(idea)
    # The decision rule has no source and no derivation, so it blocks.
    assert "entry" in result["blocking_missing"]
    # The UNKNOWN placeholders must not be counted as written down by the source.
    assert "universe" not in result["from_source"]
    assert "instrument" not in result["from_source"]
    # Scope is derivable from the stated asset class, so it is a bridgeable gap rather than a false "known".
    assert "universe" in result["bridgeable_missing"]
