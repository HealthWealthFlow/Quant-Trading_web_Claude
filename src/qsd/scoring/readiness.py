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

    A gap the source left in the *plumbing* (sizing, order type, costs, clock, proxy instrument) can be
    filled from convention and still be a legitimate test of the source's hypothesis.
    A gap in the *decision rule* (entry, exit) cannot: filling it means we invented the strategy, and
    backtesting it would measure our invention, not the source.

So entry and exit are deliberately NOT bridgeable. The values that fill the bridgeable components must be
recorded per field with their origin (SOURCE / DERIVED / DEFAULT / AI_SUGGESTED / UNRESOLVED) so the
downstream system can tell a source-stated parameter from a proposed one.

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
})

#: Components that must come from the source. Filling these invents the strategy rather than completing it.
#: The decision rule is the source's hypothesis; without it there is nothing to test.
NOT_BRIDGEABLE: frozenset[str] = frozenset({"entry", "exit"})


def _present(idea) -> dict[str, bool]:
    """Which weighted components are already written down, by the same definition completeness uses."""
    return {
        "instrument": _known(idea.instrument),
        "universe": _known(idea.universe),
        # An algorithm-shaped strategy states its decision as an update equation rather than a bar rule, and
        # that is a complete decision (D38). Without this the PAMR class reads as having no entry at all.
        "entry": _known(idea.entry_rule, idea.signal, idea.algorithm_rule),
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
