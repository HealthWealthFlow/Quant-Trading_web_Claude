"""Setup readiness: can this idea become a runnable backtest setup?

Two different questions were being answered by one number.

*How much did the source write down?* That is formalization completeness (`rules.formalization_completeness`),
and it must stay honest: a paper that leaves the stop-loss unstated is a less specified paper, and its score
must say so. It is the anti-inflation measure.

*Can this become a setup the downstream backtester can run?* That is setup readiness, added here. It differs
from completeness in exactly one way: a component the source left unstated counts as bridgeable only if a
*reasonable* value can be derived from the idea's own stated context (the asset class implies a tradable
proxy, the horizon implies a bar size). It is never counted bridgeable when filling it would invent the
strategy itself.

The load-bearing distinction, and the whole reason this module exists:

    A gap the source left in the *plumbing* (sizing, order type, costs, clock, scope, proxy instrument) can
    be filled from convention and still be a legitimate test of the source's hypothesis.
    A gap in the source's *entry* cannot: filling it means we invented the strategy, and backtesting it
    would measure our invention, not the source.
    An *exit* sits between the two. It is risk control, not the hypothesis: a source that never says when to
    leave leaves the backtest holding a position forever, which is unrealistic and usually worse than any
    sensible stop. So when the source is silent an exit is derived from a quant trader's standpoint and
    labelled as derived, never presented as the source's own.

So entry is deliberately NOT bridgeable, and it is the only component that is not. The values that fill the
other components must be recorded per field with their origin (SOURCE / DERIVED / DEFAULT / AI_SUGGESTED /
UNRESOLVED) so the downstream system can tell a source-stated parameter from a proposed one.

Nothing in this module changes a score, a gate or a status. It only reports.
"""

from __future__ import annotations

from ..taxonomy import UNKNOWN
from .rules import COMPLETENESS_WEIGHTS, _known

#: Components a reasonable value can be derived for, so a missing one does not stop the setup.
#: Plumbing: how the strategy is sized, costed, ordered, clocked and scoped — not what it decides.
BRIDGEABLE: frozenset[str] = frozenset({
    "sizing",       # position_sizing: a conventional fixed-fraction / equal-weight rule is testable
    "execution",    # order_type, trading_session, transaction_cost_assumption: a cost model is an
                    # assumption about the world, not the edge, and must be stated explicitly downstream
    "timeframe",    # data_frequency, rebalance: derivable from the stated time horizon
    "parameters",   # lookback: a conventional default can be swept by the optimizer later
    "instrument",   # a tradable proxy when the source names an asset class but no symbol
    # The traded universe is scope, not decision: "the paper states a rule but not which symbols" is a
    # testable scope choice, and a wrong guess shows up as a failed backtest rather than as a strategy we
    # invented. Measured: 17 of 31 live ideas were blocked on this alone, mostly because the source states a
    # universe (an index, a sector) that extraction filed elsewhere or missed.
    "universe",
    # Risk control, derived when the source is silent: see DERIVED_WHEN_SILENT below. Listed here as well
    # because this set decides whether a missing component can be satisfied at all.
    "exit",
})

#: Components that must come from the source. Entry is the hypothesis: if the source does not say what
#: triggers a position, there is nothing to test, and no amount of completion can create an edge.
NOT_BRIDGEABLE: frozenset[str] = frozenset({"entry"})

#: Derived by the system when the source is silent, from a quant trader's standpoint at that moment, and
#: always labelled with its origin. An exit is risk control rather than edge: a source that never says when
#: to leave leaves the backtest holding a position forever, which is unrealistic and usually *worse* than any
#: sensible stop, so supplying one makes the test more honest rather than more flattering. It is never
#: presented as the source's own wording.
DERIVED_WHEN_SILENT: frozenset[str] = frozenset({"exit"})


def _present(idea) -> dict[str, bool]:
    """Which weighted components the source itself supplied, by readiness's own definition.

    Deliberately *not* the same definition `rules.formalization_completeness` uses for `entry`. There, a
    descriptive `signal` counts, because completeness measures how much the source wrote down and a signal
    definition is something written down. Here it must not: readiness decides whether a strategy can be
    built and handed over, and a description is not a rule. Measured: idea 30's signal reads "Equity premium
    implied by the estimated SDF", which states no condition under which to trade, yet it satisfied the entry
    component under the completeness definition. Readiness and the eligibility gate must agree that such an
    idea has no entry, so this is the one definition both consult.
    """
    has_entry = _known(idea.entry_rule, idea.algorithm_rule)
    return {
        "instrument": _known(idea.instrument),
        "universe": _known(idea.universe),
        # An algorithm-shaped strategy states its decision as an update equation rather than a bar rule, and
        # that is a complete decision (D38). Without this the PAMR class reads as having no entry at all.
        "entry": has_entry,
        "exit": _known(idea.exit_rule, idea.stop_rule, idea.take_profit_rule, idea.holding_period),
        "timeframe": _known(idea.timeframe, idea.data_frequency, idea.rebalance),
        "parameters": _known(idea.lookback) or any(v != UNKNOWN for v in (idea.parameters or {}).values()),
        "sizing": _known(idea.position_sizing),
        "execution": _known(idea.order_type, idea.trading_session, idea.transaction_cost_assumption),
    }


def _context(idea) -> bool:
    """Is there anything stated to derive a replacement value *from*?

    Conservative on purpose. A value may only be derived from something the idea already states — its asset
    class, direction or horizon. With no stated context there is nothing to derive from, so the gap stays a
    real gap instead of being papered over by a generic default.
    """
    return bool(
        (idea.asset_classes or [])
        or _known(idea.instrument, idea.time_horizon, idea.position_direction)
    )


def _bridgeable_from(idea, component: str) -> bool:
    """Can a *reasonable* value for this component be derived from the idea's own stated context?"""
    if not _context(idea):
        return False
    if component in ("instrument", "universe"):
        # Scope may be chosen from a stated asset class ("US equities" -> the S&P 500 universe) or inferred
        # from a stated instrument ("S&P 500 Index" -> that index's members). With neither there is nothing to
        # scope from, so the gap stays real.
        return bool(idea.asset_classes) or _known(idea.instrument)
    if component == "exit":
        # An exit may only be derived for a strategy that actually has an entry: a stop or target on a
        # position we know how to open. Without an entry there is no position to exit from, so the gap is
        # not an exit problem at all and must not be papered over.
        return _known(idea.entry_rule, idea.algorithm_rule)
    return True


def setup_readiness(idea) -> dict:
    """How close this idea is to a runnable setup, and what still stands in the way.

    Returns a dict rather than a float because the caller needs to know *which* gaps are bridgeable: a
    runnable setup that needed five derived defaults is a different object from one the source specified,
    even when both can be handed over.
    """
    present = _present(idea)

    satisfied = {
        k: ok or (k in BRIDGEABLE and _bridgeable_from(idea, k))
        for k, ok in present.items()
    }
    readiness = float(sum(COMPLETENESS_WEIGHTS[k] for k, ok in satisfied.items() if ok))

    source_missing = sorted(k for k, ok in present.items() if not ok)
    return {
        "readiness": readiness,
        "from_source": sorted(k for k, ok in present.items() if ok),
        "bridgeable_missing": sorted(
            k for k in source_missing if k in BRIDGEABLE and _bridgeable_from(idea, k)
        ),
        # Everything the source must supply: the decision rule, the universe, or any gap with no stated
        # context to derive a replacement from.
        "blocking_missing": sorted(
            k for k in source_missing if k in NOT_BRIDGEABLE or not _bridgeable_from(idea, k)
        ),
        "runnable": all(satisfied[k] for k in satisfied),
        "note": "Readiness counts a bridgeable gap as fillable; the score does not. blocking_missing must "
                "come from the source and must never be invented.",
    }


#: How much of a setup the source must supply before completing it is honest work rather than invention.
#: Measured on the live database: the burden is bimodal with a wide empty band, so this sits inside the gap
#: and is not sensitive to the exact value. Sources that state almost nothing are skipped instead of being
#: completed, which also saves the AI spend they would otherwise consume.
MIN_SOURCE_SHARE = 0.35

#: Components that count toward how much the source supplied. The decision rule and risk control are the
#: strategy; the rest is how it is implemented, and is expected to come from convention rather than a paper.
DECISION_FIELDS: tuple[str, ...] = ("entry", "exit", "sizing", "execution")


def source_share(idea) -> float:
    """The share of a setup's essential components that the source itself supplied."""
    present = _present(idea)
    return sum(1 for k in DECISION_FIELDS if present[k]) / len(DECISION_FIELDS)


def skip_decision(idea, *, extracted_with: str | None = None, current_prompt: str | None = None) -> dict:
    """Should this idea be skipped as not worth completing, or is it worth spending AI budget on?

    A thin source is not the same thing as a failed extraction, and treating them alike would discard good
    papers whose rules were simply not captured. So a thin result is only accepted as "the source is thin"
    once it has been extracted with the current prompt: an idea captured by an older prompt is flagged for
    re-extraction instead, and never silently skipped (measured: one live source produced both a fully
    specified idea and an entirely empty one).
    """
    share = source_share(idea)
    if share >= MIN_SOURCE_SHARE:
        return {"skip": False, "share": share, "reason": None}

    stale = bool(extracted_with and current_prompt and extracted_with != current_prompt)
    if stale:
        return {
            "skip": False, "share": share, "reason": "REEXTRACT_FIRST",
            "detail": f"only {share:.0%} of the setup came from the source, but it was extracted with "
                      f"{extracted_with} rather than {current_prompt}; re-extract before judging the source",
        }
    return {
        "skip": True, "share": share, "reason": "INSUFFICIENT_INFORMATION",
        "detail": f"source supplied {share:.0%} of the setup (floor {MIN_SOURCE_SHARE:.0%}); completing it "
                  "would mean inventing the strategy rather than testing the source's",
    }
