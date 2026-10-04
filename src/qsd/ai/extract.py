"""Two-stage extraction (spec §84, §86): cheap triage → strong extraction only for promising sources → grounding →
ideas in the DB with per-field provenance facts (§28), regime rows (§137), and explicit UNKNOWNs (§47).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import Engine, delete, select

from ..config import Settings
from ..db import new_idea, session_scope
from ..db.models import AICache, AICall, Idea, IdeaSource, IdeaStatusHistory, Source, SourceFact
from ..handlers import HandlerResult
from ..scoring.evidence import store_evidence
from ..security import INJECTION_FLAG, wrap_untrusted
from ..taxonomy import UNKNOWN, ExtractionMethod, IdeaSourceRole, IdeaStatus, RegimeBasis, RegimeSuitability
from . import prompts
from .gateway import AIGateway, AIOutputError
from .grounding import GroundingReport, ground_strategy
from .schemas import CLAIM_FIELDS, Evidenced, ExtractedStrategy, StageAResult, StageBResult
from .sections import grounding_text as build_grounding_text
from .sections import select_relevant

_PAGE = re.compile(r"p\.(\d+)")
_SLIDE = re.compile(r"slide (\d+)", re.I)


@dataclass
class ExtractionReport:
    source_id: int
    triage: StageAResult | None = None
    deep_read: bool = False
    idea_ids: list[int] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    skipped_reason: str | None = None
    retried_short: bool = False  # stage B first answer was truncated; retried as one compact strategy


def _loc(location: str | None) -> dict:
    out: dict = {"location": location}
    if location:
        if m := _PAGE.search(location):
            out["page"] = int(m.group(1))
        if m := _SLIDE.search(location):
            out["slide"] = int(m.group(1))
    return out


def _fact(source_id: int, idea_id: int, fact_type: str, ev: Evidenced) -> SourceFact:
    return SourceFact(source_id=source_id, idea_id=idea_id, fact_type=fact_type.upper(), value=ev.value,
                      quote=(ev.evidence_quote or None) and ev.evidence_quote[:300],
                      extraction_method=ExtractionMethod.AI_STRONG, confidence=ev.confidence, **_loc(ev.location))


GROUNDING_FLAG_PREFIXES = ("UNGROUNDED_VALUE_REMOVED:", "UNSUPPORTED_NUMBER_REMOVED:", "UNSUPPORTED_CLAIM_REMOVED:",
                           "REGIME_EVIDENCE_NOT_FOUND:", "QUOTE_REALIGNED:")
REVIEW_FLAG = "UNRELIABLE_EXTRACTION"  # at least half of the model's values failed grounding


def _red_flags(other: list[str], unreliable: bool) -> list[str]:
    """Removed values are reported in `idea.grounding`, not as red flags of the strategy."""
    kept = [f for f in other if not f.startswith(GROUNDING_FLAG_PREFIXES) and f != REVIEW_FLAG]
    return sorted(set(kept + ([REVIEW_FLAG] if unreliable else [])))


def _grounding_record(rep: GroundingReport, model: str, prompt_version: str) -> dict:
    return {"model": model, "prompt_version": prompt_version, "values_offered": rep.attempted,
            "problems": rep.problems, "needs_review": rep.needs_review, "removed": rep.removed,
            "realigned": [f.split(":", 1)[1] for f in rep.flags if f.startswith("QUOTE_REALIGNED:")]}


def _apply_strategy(s, idea: Idea, source_id: int, st: ExtractedStrategy, rep: GroundingReport,
                    red_flags: list[str], model: str, prompt_version: str) -> None:
    """Write one grounded strategy onto `idea` (new or existing) plus its provenance facts and regime rows."""
    for k, v in dict(
        strategy_name=st.strategy_name[:500], summary=st.summary[:4000],
        asset_classes=[a.value for a in st.asset_classes],
        strategy_families=[f.upper() for f in st.strategy_families][:10], position_direction=st.position_direction,
        time_horizon=st.time_horizon, parameters={p.name: p.value for p in st.parameters},
        data_required=st.data_required[:30], unknown_rules=st.unknown_rules,
        economic_rationale=st.rationale.value,
        rationale_confidence=st.rationale.confidence if st.rationale.value != UNKNOWN else None,
        red_flags=_red_flags(red_flags, rep.needs_review),
        grounding=_grounding_record(rep, model, prompt_version),
        **{k: v.value for k, v in st.rules.items()},
        **{k: st.claims[k].value if k in st.claims else None for k in CLAIM_FIELDS},
    ).items():
        setattr(idea, k, v)
    if idea.id is None:
        s.add(idea)
    s.flush()
    for key, ev in st.rules.items():
        if ev.value != UNKNOWN:
            s.add(_fact(source_id, idea.id, key, ev))
    for key, ev in st.claims.items():
        s.add(_fact(source_id, idea.id, key, ev))
    for p in st.parameters:
        if p.value != UNKNOWN:
            s.add(_fact(source_id, idea.id, f"PARAMETER:{p.name}"[:40], p))
    if st.rationale.value != UNKNOWN:
        s.add(_fact(source_id, idea.id, "RATIONALE", st.rationale))
    for fm in st.failure_modes_from_source:
        s.add(_fact(source_id, idea.id, "FAILURE_MODE", fm))
    s.flush()
    rows = {r.regime: r for r in idea.regimes}
    for row in rows.values():
        row.suitability, row.basis, row.confidence, row.source_fact_id = (
            RegimeSuitability.UNKNOWN, RegimeBasis.UNKNOWN, 0.0, None)
    for j in st.regimes:
        row = rows[j.regime]
        row.suitability, row.basis, row.confidence = j.suitability, j.basis, j.confidence
        if j.evidence_quote:
            f = _fact(source_id, idea.id, f"REGIME:{j.regime.value}",
                      Evidenced(value=j.suitability.value, evidence_quote=j.evidence_quote, location=j.location,
                                confidence=j.confidence))
            s.add(f)
            s.flush()
            row.source_fact_id = f.id


def _carried_flags(idea: Idea, result: HandlerResult) -> list[str]:
    """The idea's stored flags, with the injection flag recomputed from the document as it is read now (the
    detector has changed before; a stale flag would otherwise keep an idea in NEEDS_REVIEW for good)."""
    kept = [f for f in idea.red_flags or [] if f != INJECTION_FLAG]
    return kept + ([INJECTION_FLAG] if result.injection_phrases else [])


def _needs_review(rep: GroundingReport, red_flags: list[str]) -> bool:
    return rep.needs_review or INJECTION_FLAG in red_flags


def _store_strategy(s, source_id: int, campaign_id: int | None, st: ExtractedStrategy, rep: GroundingReport,
                    red_flags: list[str], model: str, prompt_version: str) -> int:
    idea = new_idea(campaign_id=campaign_id, primary_source_id=source_id, search_depth_level=0)
    _apply_strategy(s, idea, source_id, st, rep, red_flags, model, prompt_version)
    if _needs_review(rep, red_flags):
        idea.status = IdeaStatus.NEEDS_REVIEW
    s.add(IdeaStatusHistory(idea_id=idea.id, from_status=None, to_status=idea.status,
                            reason=f"extracted by {model}; {rep.problems}/{rep.attempted} value(s) failed grounding"))
    s.add(IdeaSource(idea_id=idea.id, source_id=source_id, role=IdeaSourceRole.DESCRIBES))
    return idea.id


def extract_ideas(gw: AIGateway, engine: Engine, settings: Settings, source_id: int, result: HandlerResult,
                  campaign_id: int | None = None, force_deep: bool = False) -> ExtractionReport:
    report, strategies, red_flags, grounding_text = _extract(gw, engine, settings, source_id, result, campaign_id,
                                                             force_deep)
    with session_scope(engine) as s:
        for st in strategies:
            rep = ground_strategy(st, grounding_text)
            report.flags.extend(rep.flags)
            report.idea_ids.append(_store_strategy(s, source_id, campaign_id, st, rep, red_flags,
                                                   settings.ai.strong_model, prompts.STAGE_B_VERSION))
    return report


def _extract(gw: AIGateway, engine: Engine, settings: Settings, source_id: int, result: HandlerResult,
             campaign_id: int | None, force_deep: bool) -> tuple[ExtractionReport, list, list[str], str]:
    """Triage + stage B + the text quotes are checked against. Stores nothing but the triage and evidence facts."""
    report = ExtractionReport(source_id)
    ai = settings.ai
    with session_scope(engine) as s:
        src = s.get(Source, source_id)
        title = src.title if src else result.metadata.get("title", UNKNOWN)
        abstract = s.scalars(select(SourceFact.value).where(SourceFact.source_id == source_id,
                                                            SourceFact.fact_type == "ABSTRACT")).first()
    ref = f"source:{source_id}"
    red_flags = [INJECTION_FLAG] if result.injection_phrases else []
    with session_scope(engine) as s:
        store_evidence(s, source_id, result)  # deterministic, whole document; scoring reads it

    # Stage A: cheap triage on abstract + opening/relevant text (spec §86 A)
    head = select_relevant(result, ai.stage_a_max_chars)
    triage_text = (f"[abstract] {abstract}\n\n" if abstract else "") + head.text
    if not triage_text.strip():
        report.skipped_reason = "NO_TEXT"
        return report, [], red_flags, ""
    wrapped = wrap_untrusted(triage_text[:ai.stage_a_max_chars], ref)
    triage, info = gw.run_json(task="stage_a_triage", prompt_version=prompts.STAGE_A_VERSION,
                               provider=ai.default_provider, model=ai.cheap_model, system=prompts.SYSTEM,
                               user=prompts.stage_a_user(title, wrapped),
                               schema=StageAResult, max_tokens=ai.stage_a_max_tokens, campaign_id=campaign_id,
                               source_id=source_id)
    report.triage, report.cost_usd = triage, info.cost_usd
    with session_scope(engine) as s:
        s.add(SourceFact(source_id=source_id, fact_type="TRIAGE", value=triage.model_dump_json()[:4000],
                         extraction_method=ExtractionMethod.AI_CHEAP, model=ai.cheap_model,
                         confidence=triage.confidence))
    if not force_deep and not (triage.is_strategy_research and triage.worth_deep_read):
        report.skipped_reason = "TRIAGE_NOT_PROMISING"
        return report, [], red_flags, ""

    # Stage B: strong extraction on relevant sections only (spec §21, §86 B). A multi-strategy answer can run out
    # of output tokens mid-JSON; then the partial answer is discarded (never cached) and the model is asked once
    # more for a single compact strategy, which fits well inside the budget.
    sel = select_relevant(result, ai.stage_b_max_chars)
    wrapped_sel = wrap_untrusted(sel.text, ref)

    def stage_b(max_strategies: int):
        return gw.run_json(task="stage_b_extract", prompt_version=prompts.STAGE_B_VERSION,
                           provider=ai.default_provider, model=ai.strong_model, system=prompts.SYSTEM,
                           user=prompts.stage_b_user(title, wrapped_sel, max_strategies),
                           schema=StageBResult, max_tokens=ai.stage_b_max_tokens, campaign_id=campaign_id,
                           source_id=source_id)

    try:
        extraction, info = stage_b(3)
    except AIOutputError:
        # Usually a long answer cut off at the output limit: ask once more for the single best-specified strategy.
        # (A truncated answer is never cached, so this retry is a real second call, not a cache hit.)
        extraction, info = stage_b(1)
        report.retried_short = True  # both the attribute and the flag: callers and reports read each
        report.flags.append("STAGE_B_RETRIED_SHORT")
    report.cost_usd += info.cost_usd
    report.deep_read = True
    red_flags += [f"TRIAGE_RED_FLAG:{f}"[:80] for f in triage.red_flags]
    strategies = extraction.strategies if extraction is not None else []
    return report, strategies, red_flags, build_grounding_text(result, sel, abstract)


@dataclass
class RegroundReport:
    source_id: int
    idea_ids: list[int] = field(default_factory=list)
    changes: list[dict] = field(default_factory=list)  # per idea: status and removed count before/after
    skipped_reason: str | None = None


def stored_extraction(engine: Engine, source_id: int) -> tuple[StageBResult, str, str] | None:
    """The latest successful stage-B answer for a source from the AI cache, with its model and prompt version."""
    with session_scope(engine) as s:
        calls = s.scalars(select(AICall).where(
            AICall.source_id == source_id, AICall.task == "stage_b_extract", AICall.success.is_(True))
            .order_by(AICall.id.desc())).all()
        for call in calls:
            cached = s.get(AICache, call.cache_key)
            if cached is not None:
                return StageBResult.model_validate(cached.response), call.model, call.prompt_version
    return None


def _removed_count(idea: Idea) -> int:
    if idea.grounding:
        return len(idea.grounding.get("removed", []))
    return sum(f.startswith(GROUNDING_FLAG_PREFIXES[:4]) for f in idea.red_flags or [])  # pre-v4 ideas


def reground_source(engine: Engine, settings: Settings, source_id: int, result: HandlerResult) -> RegroundReport:
    """Re-run grounding (current rules) on the stored AI answer for `source_id` and update its ideas in place.

    Costs nothing: the model is not called. Ideas are matched to the stored strategies by name; ideas already
    submitted to the backtest queue are left untouched.
    """
    report = RegroundReport(source_id)
    stored = stored_extraction(engine, source_id)
    if stored is None:
        report.skipped_reason = "NO_STORED_EXTRACTION"
        return report
    extraction, model, version = stored
    with session_scope(engine) as s:
        abstract = s.scalars(select(SourceFact.value).where(SourceFact.source_id == source_id,
                                                            SourceFact.fact_type == "ABSTRACT")).first()
        ideas = s.scalars(select(Idea).where(Idea.primary_source_id == source_id).order_by(Idea.id)).all()
        if not ideas:
            report.skipped_reason = "NO_IDEAS_FOR_SOURCE"
            return report
        store_evidence(s, source_id, result)
        sel = select_relevant(result, settings.ai.stage_b_max_chars)
        grounding_text = build_grounding_text(result, sel, abstract)
        for st in extraction.strategies:
            for idea in [i for i in ideas if i.strategy_name == st.strategy_name[:500]]:
                if idea.status is IdeaStatus.SUBMITTED_TO_BACKTEST:
                    continue
                _update_in_place(s, idea, source_id, st.model_copy(deep=True), grounding_text,
                                 _carried_flags(idea, result), model, version, "re-grounded", report)
    return report


def _update_in_place(s, idea: Idea, source_id: int, strategy: ExtractedStrategy, grounding_text: str,
                     other: list[str], model: str, version: str, why: str, report: RegroundReport) -> None:
    """Ground `strategy` and write it over `idea`, keeping the idea's id, history and campaign."""
    rep = ground_strategy(strategy, grounding_text)
    before = {"status": idea.status.value, "removed": _removed_count(idea)}
    for row in idea.regimes:
        row.source_fact_id = None
    s.flush()
    s.execute(delete(SourceFact).where(SourceFact.idea_id == idea.id, SourceFact.source_id == source_id,
                                       SourceFact.extraction_method == ExtractionMethod.AI_STRONG))
    _apply_strategy(s, idea, source_id, strategy, rep, other, model, version)
    old = idea.status
    if _needs_review(rep, other):
        idea.status = IdeaStatus.NEEDS_REVIEW
    elif old is IdeaStatus.NEEDS_REVIEW:
        idea.status = IdeaStatus.DISCOVERED  # scoring moves it on
    if idea.status is not old:
        s.add(IdeaStatusHistory(idea_id=idea.id, from_status=old, to_status=idea.status,
                                reason=f"{why}: {rep.problems}/{rep.attempted} value(s) failed"))
    report.idea_ids.append(idea.id)
    report.changes.append({"idea_id": idea.id, "before": before,
                           "after": {"status": idea.status.value, "removed": len(rep.removed)},
                           "realigned": idea.grounding["realigned"]})


@dataclass
class ReextractReport(RegroundReport):
    new_idea_ids: list[int] = field(default_factory=list)   # strategies the new answer has and the old did not
    unmatched_idea_ids: list[int] = field(default_factory=list)  # old ideas the new answer no longer names
    cost_usd: float = 0.0


def reextract_source(gw: AIGateway, engine: Engine, settings: Settings, source_id: int,
                     result: HandlerResult) -> ReextractReport:
    """Ask the model again with the current prompt (this costs money) and update the source's ideas in place.

    A strategy is matched to an existing idea by name, so ids, status history and campaign survive. New strategy
    names become new ideas; old ideas the new answer does not name are left untouched and reported. Ideas already
    submitted to the backtest queue are never changed.
    """
    report = ReextractReport(source_id)
    with session_scope(engine) as s:
        ideas = s.scalars(select(Idea).where(Idea.primary_source_id == source_id).order_by(Idea.id)).all()
        campaign_id = next((i.campaign_id for i in ideas if i.campaign_id), None)
    ext, strategies, red_flags, grounding_text = _extract(gw, engine, settings, source_id, result, campaign_id,
                                                          force_deep=True)
    report.cost_usd, report.skipped_reason = ext.cost_usd, ext.skipped_reason
    if not strategies:
        report.skipped_reason = report.skipped_reason or "NO_STRATEGIES_IN_NEW_ANSWER"
        return report  # never wipe existing ideas because a new answer came back empty
    model, version = settings.ai.strong_model, prompts.STAGE_B_VERSION
    with session_scope(engine) as s:
        ideas = s.scalars(select(Idea).where(Idea.primary_source_id == source_id).order_by(Idea.id)).all()
        matched: set[int] = set()
        for st in strategies:
            same = [i for i in ideas if i.strategy_name == st.strategy_name[:500] and i.id not in matched]
            if not same:
                rep = ground_strategy(st, grounding_text)
                report.new_idea_ids.append(_store_strategy(s, source_id, campaign_id, st, rep, red_flags, model,
                                                           version))
                continue
            for idea in same:
                matched.add(idea.id)
                if idea.status is not IdeaStatus.SUBMITTED_TO_BACKTEST:
                    _update_in_place(s, idea, source_id, st.model_copy(deep=True), grounding_text,
                                     sorted(set(_carried_flags(idea, result)) | set(red_flags)), model, version,
                                     f"re-extracted ({version})", report)
        report.unmatched_idea_ids = [i.id for i in ideas if i.id not in matched
                                     and i.status is not IdeaStatus.SUBMITTED_TO_BACKTEST]
    return report


def idea_summary(engine: Engine, idea_id: int) -> dict:
    with session_scope(engine) as s:
        i = s.get(Idea, idea_id)
        return {"id": i.id, "name": i.strategy_name, "status": i.status.value, "assets": i.asset_classes,
                "entry": i.entry_rule, "exit": i.exit_rule, "lookback": i.lookback, "claimed_cagr": i.claimed_cagr,
                "regimes": {r.regime.value: (r.suitability.value, r.basis.value) for r in i.regimes},
                "unknown_rules": i.unknown_rules, "red_flags": i.red_flags}
