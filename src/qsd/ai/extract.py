"""Two-stage extraction (spec §84, §86): cheap triage → strong extraction only for promising sources → grounding →
ideas in the DB with per-field provenance facts (§28), regime rows (§137), and explicit UNKNOWNs (§47).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import Engine, select

from ..config import Settings
from ..db import new_idea, session_scope
from ..db.models import Idea, IdeaSource, IdeaStatusHistory, Source, SourceFact
from ..handlers import HandlerResult
from ..security import INJECTION_FLAG, wrap_untrusted
from ..taxonomy import UNKNOWN, ExtractionMethod, IdeaSourceRole, IdeaStatus
from . import prompts
from .gateway import AIGateway
from .grounding import ground_strategy
from .schemas import Evidenced, ExtractedStrategy, StageAResult, StageBResult
from .sections import select_relevant

_PAGE = re.compile(r"p\.(\d+)")
_SLIDE = re.compile(r"slide (\d+)", re.I)
REVIEW_FLAG_THRESHOLD = 3


@dataclass
class ExtractionReport:
    source_id: int
    triage: StageAResult | None = None
    deep_read: bool = False
    idea_ids: list[int] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    skipped_reason: str | None = None


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


def _store_strategy(s, source_id: int, campaign_id: int | None, st: ExtractedStrategy, flags: list[str],
                    red_flags: list[str], model: str) -> int:
    idea = new_idea(
        campaign_id=campaign_id, primary_source_id=source_id, strategy_name=st.strategy_name[:500],
        summary=st.summary[:4000], asset_classes=[a.value for a in st.asset_classes],
        strategy_families=[f.upper() for f in st.strategy_families][:10], position_direction=st.position_direction,
        time_horizon=st.time_horizon, parameters={p.name: p.value for p in st.parameters},
        data_required=st.data_required[:30], unknown_rules=st.unknown_rules,
        economic_rationale=st.rationale.value, rationale_confidence=st.rationale.confidence if
        st.rationale.value != UNKNOWN else None,
        red_flags=sorted(set(red_flags + flags)), search_depth_level=0,
        **{k: v.value for k, v in st.rules.items()},
        **{k: v.value for k, v in st.claims.items()},
    )
    if len(flags) >= REVIEW_FLAG_THRESHOLD or INJECTION_FLAG in red_flags:
        idea.status = IdeaStatus.NEEDS_REVIEW
    s.add(idea)
    s.flush()
    s.add(IdeaStatusHistory(idea_id=idea.id, from_status=None, to_status=idea.status,
                            reason=f"extracted by {model}; {len(flags)} grounding flag(s)"))
    s.add(IdeaSource(idea_id=idea.id, source_id=source_id, role=IdeaSourceRole.DESCRIBES))
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
    return idea.id


def extract_ideas(gw: AIGateway, engine: Engine, settings: Settings, source_id: int, result: HandlerResult,
                  campaign_id: int | None = None, force_deep: bool = False) -> ExtractionReport:
    report = ExtractionReport(source_id)
    ai = settings.ai
    with session_scope(engine) as s:
        src = s.get(Source, source_id)
        title = src.title if src else result.metadata.get("title", UNKNOWN)
        abstract = s.scalars(select(SourceFact.value).where(SourceFact.source_id == source_id,
                                                            SourceFact.fact_type == "ABSTRACT")).first()
    ref = f"source:{source_id}"
    red_flags = [INJECTION_FLAG] if result.injection_phrases else []

    # Stage A: cheap triage on abstract + opening/relevant text (spec §86 A)
    head = select_relevant(result, ai.stage_a_max_chars)
    triage_text = (f"[abstract] {abstract}\n\n" if abstract else "") + head.text
    if not triage_text.strip():
        report.skipped_reason = "NO_TEXT"
        return report
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
        return report

    # Stage B: strong extraction on relevant sections only (spec §21, §86 B)
    sel = select_relevant(result, ai.stage_b_max_chars)
    extraction, info = gw.run_json(task="stage_b_extract", prompt_version=prompts.STAGE_B_VERSION,
                                   provider=ai.default_provider, model=ai.strong_model, system=prompts.SYSTEM,
                                   user=prompts.stage_b_user(title, wrap_untrusted(sel.text, ref)),
                                   schema=StageBResult, max_tokens=ai.stage_b_max_tokens, campaign_id=campaign_id,
                                   source_id=source_id)
    report.deep_read = True
    report.cost_usd += info.cost_usd
    red_flags += [f"TRIAGE_RED_FLAG:{f}"[:80] for f in triage.red_flags]
    grounding_text = sel.text + ("\n" + abstract if abstract else "")
    with session_scope(engine) as s:
        for st in extraction.strategies:
            flags = ground_strategy(st, grounding_text)
            report.flags.extend(flags)
            report.idea_ids.append(_store_strategy(s, source_id, campaign_id, st, flags, red_flags, ai.strong_model))
    return report


def idea_summary(engine: Engine, idea_id: int) -> dict:
    with session_scope(engine) as s:
        i = s.get(Idea, idea_id)
        return {"id": i.id, "name": i.strategy_name, "status": i.status.value, "assets": i.asset_classes,
                "entry": i.entry_rule, "exit": i.exit_rule, "lookback": i.lookback, "claimed_cagr": i.claimed_cagr,
                "regimes": {r.regime.value: (r.suitability.value, r.basis.value) for r in i.regimes},
                "unknown_rules": i.unknown_rules, "red_flags": i.red_flags}
