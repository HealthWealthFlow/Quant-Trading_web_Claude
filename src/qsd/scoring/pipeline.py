"""Score ideas and move them through the status pipeline (spec §91, §92, §123, §126). Deterministic and repeatable:
re-scoring an idea overwrites its scores but never deletes history or rejection records.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, select

from ..config import Settings
from ..db import session_scope
from ..db.models import Idea, IdeaSource, IdeaStatusHistory, Rejection, Source, SourceFact
from ..security import INJECTION_FLAG
from ..taxonomy import UNKNOWN, IdeaSourceRole, IdeaStatus, RegimeBasis, RegimeSuitability
from . import dedupe, rules, scores

FROZEN_STATUSES = {IdeaStatus.SUBMITTED_TO_BACKTEST}

FAMILY_TAGS = {
    "MOMENTUM": "MOMENTUM", "TREND": "TREND", "RELATIVE STRENGTH": "MOMENTUM", "REVERSION": "MEAN_REVERSION",
    "REVERSAL": "MEAN_REVERSION", "CARRY": "FX_CARRY", "VOLATILITY RISK PREMIUM": "SHORT_VOL",
    "OPTIONS PREMIUM": "SHORT_VOL", "VALUE": "VALUE", "LIQUIDITY": "LIQUIDITY", "EVENT": "EVENT",
    "EARNINGS": "EVENT", "CRYPTO": "CRYPTO_BETA", "TAIL": "LONG_VOL", "CRISIS": "LONG_VOL",
}


@dataclass
class ScoreResult:
    idea_id: int
    status: str
    idea_quality: float
    coverage: float
    priority: float
    band: str
    hard_fails: list[str]


def _set_status(s, idea: Idea, new: IdeaStatus, reason: str) -> None:
    if idea.status is new:
        return
    s.add(IdeaStatusHistory(idea_id=idea.id, from_status=idea.status, to_status=new, reason=reason[:2000]))
    idea.status = new


def _reject_once(s, idea_id: int, reason, detail: str, hard: bool) -> None:
    exists = s.scalars(select(Rejection.id).where(Rejection.idea_id == idea_id, Rejection.reason == reason)).first()
    if exists is None:
        s.add(Rejection(idea_id=idea_id, reason=reason, detail=detail[:2000], is_hard_fail=hard))


def score_idea(engine: Engine, settings: Settings, idea_id: int) -> ScoreResult:
    gate = settings.quality_gate
    with session_scope(engine) as s:
        idea = s.get(Idea, idea_id)
        src = s.get(Source, idea.primary_source_id) if idea.primary_source_id else None
        src_facts = s.scalars(select(SourceFact).where(SourceFact.source_id == idea.primary_source_id)).all() \
            if src else []
        ids = {f.fact_type: f.value for f in src_facts if f.fact_type.startswith("ID_")}
        abstract = next((f.value for f in src_facts if f.fact_type == "ABSTRACT"), "")
        idea_facts = [f for f in src_facts if f.idea_id == idea.id]
        corpus = " ".join([abstract, idea.summary if idea.summary != UNKNOWN else ""] +
                          [f"{f.value} {f.quote or ''}" for f in idea_facts])
        rule_text = scores._rules_text(idea)

        # red flags, completeness, complexity, hard fails
        found = rules.find_red_flags(corpus + " " + rule_text)
        flags = sorted(set(idea.red_flags or []) | set(found))
        completeness, missing = rules.formalization_completeness(idea)
        complexity, complexity_parts = rules.parameter_complexity(idea)
        fails = rules.hard_fails(rule_text + " " + corpus, found, completeness, idea.signal,
                                 algorithm_rule=idea.algorithm_rule)

        # root evidence, source & evidence quality, replication
        root = dedupe.root_evidence_id(ids, src.id) if src else f"idea:{idea.id}"
        idea.root_evidence_id = root
        signals = scores.transparency_signals(corpus)
        if src:
            src.root_evidence_id = root
            sq, sq_details = scores.source_quality(
                src.tier, bool(ids), src.author != UNKNOWN, src.publication_date != UNKNOWN, signals,
                [f for f in found if f in rules.SCAM_FLAGS], INJECTION_FLAG in flags)
            src.source_quality_score = sq
        else:
            sq, sq_details = 0.0, {"components": {}, "note": "no primary source"}
        independent_roots = {r or f"source:{sid}" for r, sid in s.execute(
            select(Source.root_evidence_id, Source.id).join(IdeaSource, IdeaSource.source_id == Source.id).where(
                IdeaSource.idea_id == idea.id,
                IdeaSource.role.in_([IdeaSourceRole.SUPPORTS, IdeaSourceRole.REPLICATES])))} - {root}
        replication = scores.replication_score(len(independent_roots))
        eq, eq_details = scores.evidence_quality(signals, len(independent_roots), corpus)
        if src:
            src.evidence_quality_score = eq

        # dedupe & novelty
        idea.fingerprint = dedupe.fingerprint(idea)
        others = s.scalars(select(Idea).where(Idea.id != idea.id, Idea.id < idea.id)).all()
        dclass, dup_of = dedupe.classify(idea, others)
        idea.dedupe_class, idea.duplicate_of_id = dclass, dup_of
        families = {f.upper() for f in idea.strategy_families or []}
        overlap = any(families & {f.upper() for f in o.strategy_families or []} for o in others)
        nov = dedupe.novelty(dclass, overlap)

        # quality, priority, band
        iq = scores.idea_quality(idea, settings.scoring.idea_weights, completeness, complexity, eq_details,
                                 any(f.reason.value == "LOOKAHEAD_REQUIRED" for f in fails))
        priority = scores.research_priority(iq, sq, eq, replication, nov, completeness)
        normalized = iq.normalized or 0.0
        band = scores.gate_band(normalized, gate.high_priority, gate.promising, gate.research_further)

        # regimes and diversification tags (spec §79, §137)
        known = [r for r in idea.regimes if r.suitability is not RegimeSuitability.UNKNOWN]
        if known and all(r.basis is RegimeBasis.RATIONALE_INFERRED for r in known):
            flags.append("REGIME_INFERRED_ONLY")
        tags = {tag for fam in families for key, tag in FAMILY_TAGS.items() if key in fam}
        tags |= {f"REGIME:{r.regime.value}" for r in idea.regimes if r.suitability is RegimeSuitability.SUITED}
        idea.diversification_tags = sorted(tags)

        idea.red_flags = sorted(set(flags))
        idea.formalization_completeness = completeness
        idea.parameter_complexity = complexity
        idea.source_quality, idea.evidence_quality = sq, eq
        idea.replication_score, idea.novelty_score = replication, nov
        idea.idea_quality_score, idea.research_priority_score = iq.score, priority
        idea.hard_fail_reasons = [f.reason.value for f in fails]
        idea.search_depth_level = max(idea.search_depth_level, 3 if independent_roots else idea.search_depth_level)

        actions = []
        if idea.search_depth_level < 2:
            actions.append("find the original/primary source")
        if not independent_roots:
            actions.append("search for independent replication")
        actions.append("search for contradicting evidence")
        if iq.unscored:
            actions.append("assess: " + ", ".join(iq.unscored))
        if missing:
            actions.append("missing rules: " + ", ".join(missing))
        idea.next_research_action = "; ".join(actions)[:2000]
        idea.score_details = {
            "idea_quality": {"score": iq.score, "coverage": iq.coverage, "normalized": iq.normalized,
                             "components": {k: {"value": c.value, "basis": c.basis} for k, c in iq.components.items()},
                             "unscored": iq.unscored},
            "source_quality": sq_details, "evidence_quality": eq_details,
            "completeness": {"score": completeness, "band": rules.completeness_band(completeness),
                             "missing": missing},
            "complexity": complexity_parts, "dedupe": {"class": dclass, "of": dup_of}, "band": band,
            "priority": priority, "note": "Scores never use claimed performance (spec §81, §82).",
        }

        # status pipeline
        if idea.status not in FROZEN_STATUSES:
            if fails:
                for f in fails:
                    _reject_once(s, idea.id, f.reason, f.detail, hard=True)
                _set_status(s, idea, IdeaStatus.REJECTED, "hard fail: " + ", ".join(idea.hard_fail_reasons))
            elif dclass == "DUPLICATE":
                _reject_once(s, idea.id, rules.RejectionReason.DUPLICATE, f"duplicate of idea {dup_of}", hard=False)
                _set_status(s, idea, IdeaStatus.DUPLICATE, f"same fingerprint as idea {dup_of}")
            elif idea.status is IdeaStatus.NEEDS_REVIEW:
                pass  # human/second-opinion review first (spec §87)
            elif iq.coverage < settings.scoring.min_coverage_for_gate:
                _set_status(s, idea, IdeaStatus.RESEARCHING,
                            f"coverage {iq.coverage:.0%} < {settings.scoring.min_coverage_for_gate:.0%}; "
                            "assess missing components before gating")
            else:
                new = {"HIGH_PRIORITY": IdeaStatus.PROMISING, "PROMISING": IdeaStatus.PROMISING,
                       "RESEARCH_FURTHER": IdeaStatus.RESEARCHING, "ARCHIVE": IdeaStatus.ARCHIVED}[band]
                _set_status(s, idea, new, f"quality gate band {band} (normalized {normalized:.1f})")
        return ScoreResult(idea.id, idea.status.value, iq.score, iq.coverage, priority, band,
                           idea.hard_fail_reasons)


def score_all(engine: Engine, settings: Settings) -> list[ScoreResult]:
    with session_scope(engine) as s:
        ids = s.scalars(select(Idea.id).order_by(Idea.id)).all()
    return [score_idea(engine, settings, i) for i in ids]
