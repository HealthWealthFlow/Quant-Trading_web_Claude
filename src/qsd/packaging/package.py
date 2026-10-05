"""Strategy research packages (spec §122–§125, §128, §137) and the backtest handoff queue.

A package is a self-contained, machine-readable JSON document for the separate Quant Auto OS. It carries full
provenance, known vs unknown rules, market-regime profile, concerns and scores — and source performance only as
unvalidated CLAIMS. This module never talks to a broker; its only output is files in the queue directory (§125).
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, select

from ..config import Settings
from ..db import session_scope
from ..db.models import Idea, IdeaSource, IdeaStatusHistory, Source, SourceFact
from ..scoring import readiness as readiness_mod
from ..scoring import rules
from ..taxonomy import UNKNOWN, IdeaSourceRole, IdeaStatus, MarketRegime, RegimeSuitability, TimeHorizon

PACKAGE_VERSION = "1.2"  # 1.1: + grounding; 1.2: + maturity
DOWNSTREAM_WARNING = ("EXTERNAL PERFORMANCE CLAIMS ARE NOT VALIDATED. "
                      "DOWNSTREAM SYSTEM MUST RECOMPUTE EVERYTHING.")  # spec §124

RULE_FIELDS = ("instrument", "universe", "timeframe", "data_frequency", "indicators", "signal", "lookback",
               "entry_rule", "exit_rule", "stop_rule", "take_profit_rule", "position_sizing", "rebalance",
               "order_type", "trading_session", "holding_period", "transaction_cost_assumption",
               "liquidity_requirement", "portfolio_rules", "risk_rules")

# Downstream checks per asset class (spec §57–§62). Checklists for the backtester, not facts about the strategy.
ASSET_CHECKS: dict[str, list[str]] = {
    "STOCK": ["point-in-time universe/constituents", "survivorship-free prices incl. delistings",
              "corporate actions and split adjustments", "borrow/short availability if shorting",
              "earnings dates", "ADV, spread and market cap filters"],
    "ETF": ["fund inception and closures", "AUM, ADV, spread", "tracking error and index methodology",
            "leveraged/inverse ETF path dependency", "distributions"],
    "OPTIONS": ["full chain with bid/ask, volume, OI and timestamps", "exercise and settlement style (AM/PM)",
                "multiplier", "relative spread and quote size", "IV, term structure, skew",
                "assignment, expiration and pin risk", "multi-leg/combo fill assumptions"],
    "FOREX": ["bid/ask by session", "rollover/swap", "central-bank event calendar", "broker execution model",
              "leverage limits", "weekend gaps"],
    "CRYPTO": ["exchange and venue (spot/perpetual)", "funding and basis history", "maker/taker fees",
               "order book depth", "exchange outages and liquidation rules", "token delistings/survivorship",
               "stablecoin and counterparty exposure"],
    "FUTURES": ["continuous-contract roll methodology", "contract multipliers and margins", "session times"],
}
POINT_IN_TIME = ["every input must be known before the decision timestamp (spec §56)",
                 "no future highs/lows, future index membership, or end-of-day data for intraday entries",
                 "macro/fundamental data must use publication dates, not revised values"]


class ResearchPackage(BaseModel):
    """Package schema (exported as JSON Schema for the Quant Auto OS)."""

    model_config = ConfigDict(extra="forbid")
    package_version: str
    generated_at: str
    warning: str
    strategy_id: str
    idea_id: int
    status: str
    strategy_name: str
    hypothesis: str
    maturity: str = Field("UNKNOWN", description=(
        "How completely the rules are specified: CONCEPT, PARTIAL, TRADEABLE or BACKTEST-READY. Not a quality "
        "or profitability judgement."))
    asset_classes: list[str]
    strategy_families: list[str]
    position_direction: str
    time_horizon: str
    economic_rationale: dict[str, Any]
    provenance: dict[str, Any]
    original_sources: list[dict[str, Any]]
    supporting_sources: list[dict[str, Any]]
    contradicting_sources: list[dict[str, Any]]
    known_rules: dict[str, dict[str, Any]]
    unknown_rules: list[str]
    parameters: dict[str, str]
    market_regimes: list[dict[str, Any]]
    data_requirements: list[str]
    point_in_time_requirements: list[str]
    downstream_checks: list[str]
    concerns: dict[str, list[str]]
    source_claims: dict[str, Any] = Field(description="Unvalidated performance claims, verbatim from the source.")
    grounding: dict[str, Any] = Field(default_factory=dict, description=(
        "AI extraction audit: values the model offered that were removed because their quote or numbers were not "
        "found in the source (with the model's quote and the reason). Removed values are NOT facts."))
    scores: dict[str, Any]
    research_completeness: dict[str, Any]
    handoff: dict[str, Any]


def _fact_map(facts: list[SourceFact]) -> dict[str, SourceFact]:
    out: dict[str, SourceFact] = {}
    for f in facts:
        out.setdefault(f.fact_type, f)
    return out


def _evidence(f: SourceFact | None, value: str) -> dict[str, Any]:
    if f is None:
        return {"value": value, "quote": None, "location": None, "confidence": None}
    return {"value": value, "quote": f.quote, "page": f.page, "slide": f.slide,
            "location": f.location, "confidence": f.confidence}


def _source_ref(src: Source, ids: dict[str, str]) -> dict[str, Any]:
    return {"source_id": src.id, "title": src.title, "author": src.author, "organization": src.organization,
            "publication_date": src.publication_date, "url": src.url, "canonical_url": src.canonical_url,
            "local_path": src.local_path, "doi": ids.get("ID_DOI"), "arxiv": ids.get("ID_ARXIV"), "tier": src.tier,
            "access_status": src.access_status.value, "content_hash": src.content_hash,
            "retrieved_at": src.retrieved_at.isoformat() if src.retrieved_at else None,
            "root_evidence_id": src.root_evidence_id, "source_quality": src.source_quality_score}


def handoff_check(idea: Idea, provenance_known: bool, settings: Settings) -> list[str]:
    """Reasons the idea may NOT enter the backtest queue (empty list = allowed). Spec §123.

    Eligibility rests on the setup being *runnable* rather than on how many fields the source happened to
    write down. A source that states its decision rule but leaves the plumbing to convention is a legitimate
    hypothesis to test, and the downstream system is designed to complete and then backtest it. What cannot
    be handed over is an idea whose *entry* had to be invented (there would be nothing of the source left to
    test) or one whose source said so little that completing it would mean inventing the strategy.
    """
    h = settings.handoff
    reasons = []
    if idea.hard_fail_reasons:
        reasons.append("hard fail: " + ", ".join(idea.hard_fail_reasons))
    if idea.status not in (IdeaStatus.PROMISING, IdeaStatus.READY_FOR_FORMALIZATION,
                           IdeaStatus.SUBMITTED_TO_BACKTEST):
        reasons.append(f"status is {idea.status.value} (needs PROMISING or READY_FOR_FORMALIZATION)")

    readiness = readiness_mod.setup_readiness(idea)
    skip = readiness_mod.skip_decision(idea)
    if skip["skip"]:
        reasons.append(f"source states too little to be worth completing ({skip['share']:.0%} of the setup; "
                       f"floor {readiness_mod.MIN_SOURCE_SHARE:.0%})")
    if "entry" not in readiness["from_source"]:
        # The hypothesis itself is missing. Completing it would mean testing our rule, not the source's.
        reasons.append("no entry rule stated by the source")
    if not readiness["runnable"]:
        reasons.append("setup not runnable: " + ", ".join(readiness["blocking_missing"]))

    iq = (idea.score_details or {}).get("idea_quality", {})
    if (iq.get("coverage") or 0) < h.min_coverage:
        reasons.append(f"score coverage {iq.get('coverage')} < {h.min_coverage}")
    if (iq.get("normalized") or 0) < h.min_normalized_quality:
        reasons.append(f"idea quality {iq.get('normalized')} < {h.min_normalized_quality}")
    data = (iq.get("components") or {}).get("data_availability", {}).get("value")
    if data is None or data < h.min_data_availability:
        reasons.append("data availability not established")
    if idea.time_horizon in (TimeHorizon.HFT, TimeHorizon.SECONDS):
        reasons.append("latency-critical horizon not realistically tradable for retail infrastructure")
    if not provenance_known:
        reasons.append("source provenance incomplete (need identifier or URL/path plus content hash)")
    return reasons


def build_package(engine: Engine, settings: Settings, idea_id: int) -> ResearchPackage:
    with session_scope(engine) as s:
        idea = s.get(Idea, idea_id)
        if idea is None:
            raise KeyError(f"idea {idea_id} not found")
        links = s.execute(select(IdeaSource, Source).join(Source, Source.id == IdeaSource.source_id)
                          .where(IdeaSource.idea_id == idea_id)).all()

        def ids_of(src_id: int) -> dict[str, str]:
            return {f.fact_type: f.value for f in s.scalars(select(SourceFact).where(
                SourceFact.source_id == src_id, SourceFact.fact_type.like("ID_%")))}

        primary = s.get(Source, idea.primary_source_id) if idea.primary_source_id else None
        pids = ids_of(primary.id) if primary else {}
        facts = _fact_map(list(s.scalars(select(SourceFact).where(SourceFact.idea_id == idea_id))))
        by_role: dict[IdeaSourceRole, list[dict]] = {}
        for link, src in links:
            by_role.setdefault(link.role, []).append({**_source_ref(src, ids_of(src.id)), "note": link.note})

        provenance_known = bool(primary) and (bool(pids) or bool((primary.canonical_url or primary.local_path)
                                                                 and primary.content_hash))
        details = idea.score_details or {}
        iq = details.get("idea_quality", {})
        comps = iq.get("components", {})
        reasons = handoff_check(idea, provenance_known, settings)

        known = {f: _evidence(facts.get(f.upper()), getattr(idea, f)) for f in RULE_FIELDS
                 if getattr(idea, f) != UNKNOWN}
        claims = {k: {**_evidence(facts.get(k.upper()), getattr(idea, k)), "validated": False}
                  for k in ("claimed_sharpe", "claimed_cagr", "claimed_max_dd", "claimed_win_rate", "claimed_pf")
                  if getattr(idea, k)}
        regimes = [{"regime": r.regime.value, "suitability": r.suitability.value, "basis": r.basis.value,
                    "confidence": r.confidence, "evidence": _evidence(s.get(SourceFact, r.source_fact_id)
                                                                      if r.source_fact_id else None,
                                                                      r.suitability.value)}
                   for r in sorted(idea.regimes, key=lambda r: list(MarketRegime).index(r.regime))]
        flags = idea.red_flags or []
        concerns = {
            "liquidity": [comps.get("liquidity", {}).get("basis", "NOT_ASSESSED")],
            "execution": ([comps.get("edge_vs_cost", {}).get("basis", "NOT_ASSESSED")] +
                          ([idea.order_type] if idea.order_type != UNKNOWN else [])),
            "tail_risk": [comps.get("tail_risk", {}).get("basis", "NOT_ASSESSED")],
            "cost": [idea.transaction_cost_assumption if idea.transaction_cost_assumption != UNKNOWN
                     else "TRANSACTION_COSTS_NOT_STATED_BY_SOURCE"],
            "broker": ["LATENCY_CRITICAL"] if idea.time_horizon in (TimeHorizon.HFT, TimeHorizon.SECONDS) else [],
            "red_flags": flags,
        }
        checks = [c for a in idea.asset_classes or [] for c in ASSET_CHECKS.get(a, [])]
        roots = {x.get("root_evidence_id") for x in by_role.get(IdeaSourceRole.SUPPORTS, []) +
                 by_role.get(IdeaSourceRole.REPLICATES, [])} - {idea.root_evidence_id, None}
        checklist = {
            "primary_source": bool(primary and primary.tier in (1, 2)),
            "independent_support": bool(roots),
            "original_reference": bool(by_role.get(IdeaSourceRole.ORIGINAL)) or bool(primary and primary.tier == 1),
            "replication": (idea.replication_score or 0) > 0,
            "contradiction_search": idea.search_depth_level >= 4 or bool(by_role.get(IdeaSourceRole.CONTRADICTS)),
            "recent_evidence": idea.search_depth_level >= 5,
            "execution_evidence": idea.transaction_cost_assumption != UNKNOWN,
            "rules_complete": (idea.formalization_completeness or 0) >= settings.handoff.min_completeness,
        }
        pkg = ResearchPackage(
            package_version=PACKAGE_VERSION,
            generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
            warning=DOWNSTREAM_WARNING,
            strategy_id=f"QSD-{idea.id:06d}-{(idea.fingerprint or 'nofp')[:8]}",
            idea_id=idea.id, status=idea.status.value, strategy_name=idea.strategy_name,
            hypothesis=idea.summary, maturity=rules.maturity(idea.formalization_completeness),
            asset_classes=list(idea.asset_classes or []),
            strategy_families=list(idea.strategy_families or []),
            position_direction=idea.position_direction.value, time_horizon=idea.time_horizon.value,
            economic_rationale={**_evidence(facts.get("RATIONALE"), idea.economic_rationale),
                                "why_edge_persists": idea.why_edge_persists},
            provenance={"primary_source": _source_ref(primary, pids) if primary else None,
                        "root_evidence_id": idea.root_evidence_id, "search_depth_level": idea.search_depth_level,
                        "provenance_known": provenance_known},
            original_sources=by_role.get(IdeaSourceRole.ORIGINAL, []),
            supporting_sources=by_role.get(IdeaSourceRole.SUPPORTS, []) + by_role.get(IdeaSourceRole.REPLICATES, []),
            contradicting_sources=by_role.get(IdeaSourceRole.CONTRADICTS, []),
            known_rules=known,
            unknown_rules=sorted(set(idea.unknown_rules or []) |
                                 {f for f in RULE_FIELDS if getattr(idea, f) == UNKNOWN}),
            parameters={k: str(v) for k, v in (idea.parameters or {}).items()},
            market_regimes=regimes, data_requirements=list(idea.data_required or []),
            point_in_time_requirements=POINT_IN_TIME, downstream_checks=list(dict.fromkeys(checks)),
            concerns=concerns,
            source_claims={"note": "Verbatim source claims. NOT VALIDATED. Do not use for ranking or sizing.",
                           "claims": claims},
            grounding=dict(idea.grounding or {}),
            scores={"idea_quality": idea.idea_quality_score, "idea_quality_normalized": iq.get("normalized"),
                    "coverage": iq.get("coverage"), "unscored_components": iq.get("unscored", []),
                    "source_quality": idea.source_quality, "evidence_quality": idea.evidence_quality,
                    "formalization_completeness": idea.formalization_completeness,
                    "parameter_complexity": idea.parameter_complexity, "replication": idea.replication_score,
                    "novelty": idea.novelty_score, "research_priority": idea.research_priority_score,
                    "band": details.get("band")},
            research_completeness={"checks": {k: ("PASS" if v else "FAIL") for k, v in checklist.items()},
                                   "percent": round(100 * sum(checklist.values()) / len(checklist))},
            handoff={"eligible": not reasons, "blocking_reasons": reasons},
        )
    return pkg


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def submit_to_queue(engine: Engine, settings: Settings, idea_id: int) -> tuple[bool, Path | None, list[str]]:
    """Write the package to <queue_dir>/pending/ if the handoff rule passes; mark SUBMITTED_TO_BACKTEST."""
    pkg = build_package(engine, settings, idea_id)
    if not pkg.handoff["eligible"]:
        return False, None, pkg.handoff["blocking_reasons"]
    queue = settings.resolve_path(settings.handoff.queue_dir) / "pending"
    path = queue / f"{pkg.strategy_id}.json"
    _atomic_write(path, pkg.model_dump_json(indent=2))
    with session_scope(engine) as s:
        idea = s.get(Idea, idea_id)
        if idea.status is not IdeaStatus.SUBMITTED_TO_BACKTEST:
            s.add(IdeaStatusHistory(idea_id=idea.id, from_status=idea.status,
                                    to_status=IdeaStatus.SUBMITTED_TO_BACKTEST,
                                    reason=f"research package {pkg.strategy_id} queued for independent backtesting"))
            idea.status = IdeaStatus.SUBMITTED_TO_BACKTEST
    return True, path, []


def export_schema(path: Path) -> Path:
    _atomic_write(path, json.dumps(ResearchPackage.model_json_schema(), indent=2))
    return path


def suitable_regimes(pkg: ResearchPackage) -> list[str]:
    return [r["regime"] for r in pkg.market_regimes if r["suitability"] == RegimeSuitability.SUITED.value]


def handoff_report(engine: Engine, settings: Settings, statuses: list[IdeaStatus] | None = None) -> list[dict]:
    """Why each idea is (not) allowed into the backtest queue — the measurement behind "nothing reached it"."""
    statuses = statuses or [IdeaStatus.PROMISING, IdeaStatus.READY_FOR_FORMALIZATION, IdeaStatus.RESEARCHING]
    with session_scope(engine) as s:
        ids = list(s.scalars(select(Idea.id).where(Idea.status.in_(statuses)).order_by(Idea.id)))
    out = []
    for i in ids:
        pkg = build_package(engine, settings, i)
        out.append({"idea_id": i, "name": pkg.strategy_name, "status": pkg.status,
                    "normalized": pkg.scores.get("idea_quality_normalized"), "coverage": pkg.scores.get("coverage"),
                    "completeness": pkg.scores.get("formalization_completeness"), "maturity": pkg.maturity,
                    "eligible": pkg.handoff["eligible"], "reasons": pkg.handoff["blocking_reasons"]})
    return out
