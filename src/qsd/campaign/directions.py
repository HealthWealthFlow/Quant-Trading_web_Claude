"""Self-directed follow-up search: pick the next research directions from what a round actually found.

This is what makes an unattended harvest keep looking instead of spinning. Widening `max_queries` alone only pads
the plan with more of the same static query families, so by round 2–3 every query is already in search memory and
discovery returns nothing new (measured: rounds 2 and 3 of campaign 9 both reported `0 new papers found`).

The directions here are derived only from what the campaign extracted — the strategy families, asset classes and
horizons actually present in its ideas, plus the gaps those ideas flagged. Nothing is invented: if no idea produced a
usable term, no follow-up query is generated and the caller falls through to its own stop conditions.
"""

from __future__ import annotations

from sqlalchemy import select

from ..db import session_scope
from ..db.models import Idea
from ..taxonomy import UNKNOWN

# How a promising-looking family is re-searched. "replication" and "out of sample" target the two components that
# hold every measured idea below the gate (see docs/GATE_CALIBRATION.md); the others widen coverage.
_DIRECTION_TEMPLATES = (
    "{family} out of sample evidence",
    "{family} replication study",
    "{family} transaction costs",
    "{family} {asset} strategy",
    "{family} strategy robustness",
)


def _known(value: str | None) -> str | None:
    return value if value and value != UNKNOWN else None


def next_queries(engine, campaign_id: int, limit: int = 8, already: set[str] | None = None) -> list[str]:
    """Follow-up queries derived from this campaign's own ideas, most-grounded first.

    Ordered by how much the campaign already invested in a family: a family that produced several ideas is the one
    worth pressing on, and a family with an unknown time horizon or missing rules is worth another angle.
    """
    already = {a.lower() for a in (already or set())}
    with session_scope(engine) as s:
        ideas = s.scalars(select(Idea).where(Idea.campaign_id == campaign_id)).all()
    if not ideas:
        return []

    weight: dict[str, int] = {}
    for idea in ideas:
        for family in (idea.strategy_families or []):
            fam = str(family).strip().lower()
            if fam:
                weight[fam] = weight.get(fam, 0) + 1

    assets: list[str] = []
    for idea in ideas:
        for asset in (idea.asset_classes or []):
            if asset not in assets:
                assets.append(str(asset))

    out: list[str] = []
    for family, _count in sorted(weight.items(), key=lambda kv: -kv[1]):
        asset = assets[0].lower() if assets else ""
        for template in _DIRECTION_TEMPLATES:
            query = template.format(family=family, asset=asset).replace("  ", " ").strip()
            if query.lower() in already or query in out:
                continue
            out.append(query)
            if len(out) >= limit:
                return out
    return out
