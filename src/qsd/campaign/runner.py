"""Campaign runner (spec §41–§45, §72–§74, §127, §129): discover → select → fetch → extract → score → deepen
(replication / contradiction / recent evidence) → score → stop. Every phase saves progress in `campaigns.state`,
so an interrupted campaign resumes where it stopped instead of repeating (and re-paying for) work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import Engine, func, select

from ..ai import AIGateway, AIOutputError, ProviderError, UnpricedModelError, extract_ideas, prompts
from ..ai.grounding import normalize, quote_in_source
from ..ai.schemas import RelationResult
from ..config import Settings
from ..db import session_scope
from ..db.models import Campaign, ErrorRecord, FetchLog, Idea, IdeaSource, Source, SourceFact
from ..discovery import BudgetExhausted, CampaignBudget, build_queries, run_discovery
from ..discovery.connectors import Connector
from ..fetch.client import PoliteFetcher
from ..fetch.pipeline import apply_result
from ..handlers import parse_bytes
from ..scoring import score_idea
from ..security import wrap_untrusted
from ..taxonomy import AccessStatus, AssetClass, CampaignStatus, IdeaSourceRole, IdeaStatus, MarketRegime
from .parse import CampaignSpec, parse_request

RELATION_ROLES = {"REPLICATES": IdeaSourceRole.REPLICATES, "SUPPORTS": IdeaSourceRole.SUPPORTS,
                  "CONTRADICTS": IdeaSourceRole.CONTRADICTS}
MIN_RELATION_CONFIDENCE = 0.6
MAX_EVENTS = 80  # progress events kept in campaigns.state for the live monitor


@dataclass
class CampaignLimits:
    max_queries: int = 8
    docs_per_round: int = 10
    deepen_top_ideas: int = 5
    results_per_query: int = 10


@dataclass
class CampaignReport:
    campaign_id: int
    status: str
    stop_reason: str | None
    spec: dict
    phases: list[str] = field(default_factory=list)
    new_sources: int = 0
    documents_processed: int = 0
    ideas: list[int] = field(default_factory=list)
    promising: list[int] = field(default_factory=list)
    links: dict[str, int] = field(default_factory=dict)
    budget_spent: dict = field(default_factory=dict)


def _relevance(text: str, terms: list[str]) -> int:
    t = text.lower()
    return sum(1 for term in terms if term and term.lower() in t)


class CampaignRunner:
    def __init__(self, engine: Engine, settings: Settings, fetcher: PoliteFetcher, gateway: AIGateway,
                 connectors: list[Connector], limits: CampaignLimits | None = None, on_event=None):
        """`on_event(phase, message)` receives every progress message (e.g. to print it); the same messages and
        counters are stored in `campaigns.state["progress"]` for the dashboard's live monitor."""
        self.engine, self.settings, self.fetcher, self.gw = engine, settings, fetcher, gateway
        self.connectors = connectors
        self.limits = limits or CampaignLimits()
        self.on_event = on_event

    # -- campaign persistence -----------------------------------------------------------------------------------

    def create(self, request: str) -> int:
        spec = parse_request(request)
        with session_scope(self.engine) as s:
            c = Campaign(request_text=request, mode=spec.mode, asset_classes=spec.asset_classes,
                         strategy_families=spec.families, target_regimes=spec.regimes, spec=spec.to_dict(),
                         budgets=self.settings.budgets.model_dump(), state={"phases_done": []},
                         status=CampaignStatus.PLANNED)
            s.add(c)
            s.flush()
            return c.id

    def _save(self, cid: int, budget: CampaignBudget, **state) -> None:
        with session_scope(self.engine) as s:
            c = s.get(Campaign, cid)
            new = dict(c.state or {})
            new.update(state)
            new["spent"] = budget.snapshot()
            c.state = new

    def _note(self, cid: int, budget: CampaignBudget, phase: str, message: str, **counters) -> None:
        """Record one progress step (live monitor) and pass it to `on_event`."""
        now = datetime.now(UTC)
        with session_scope(self.engine) as s:
            c = s.get(Campaign, cid)
            state = dict(c.state or {})
            prog = dict(state.get("progress") or {})
            prog.update(counters)
            prog["strategies_found"] = s.execute(select(func.count()).select_from(Idea)
                                                 .where(Idea.campaign_id == cid)).scalar_one()
            prog.update(phase=phase, current=message[:300], updated_at=now.isoformat(timespec="seconds"),
                        ai_cost_usd=round(float(budget.spent.get("ai_cost_usd", 0.0)), 6))
            prog["events"] = (list(prog.get("events") or []) +
                              [{"t": now.strftime("%H:%M:%S"), "phase": phase, "msg": message[:300]}])[-MAX_EVENTS:]
            state["progress"], state["spent"] = prog, budget.snapshot()
            c.state = state
        if self.on_event:
            self.on_event(phase, message)

    def _idea_line(self, idea_id: int) -> str:
        with session_scope(self.engine) as s:
            i = s.get(Idea, idea_id)
            q = "-" if i.idea_quality_score is None else f"{i.idea_quality_score:.1f}"
            return f"#{i.id} {i.strategy_name[:90]} -> {i.status.value} (quality {q})"

    def _state(self, cid: int) -> dict:
        with session_scope(self.engine) as s:
            return dict(s.get(Campaign, cid).state or {})

    def _error(self, cid: int, stage: str, ref: str | None, handler: str, err: Exception | str) -> None:
        with session_scope(self.engine) as s:
            s.add(ErrorRecord(campaign_id=cid, stage=stage, source_ref=ref, handler=handler, error=str(err)[:2000]))

    # -- phases ---------------------------------------------------------------------------------------------------

    def _discover(self, cid: int, spec: CampaignSpec, budget: CampaignBudget) -> int:
        found_total = [0]

        def on_search(conn: str, query: str, found, new: int, done: int, total: int) -> None:
            found_total[0] += new
            what = ("skipped (searched recently)" if found is None else "failed" if found == -1
                    else f"{found} results, {new} new")
            self._note(cid, budget, "search", f"[{done}/{total}] {conn}: \"{query[:80]}\" - {what}",
                       searches_done=done, searches_total=total, sources_found=found_total[0])

        assets = [AssetClass(a) for a in spec.asset_classes] or None
        regimes = [MarketRegime(r) for r in spec.regimes] or None
        extra = [spec.core_query] if spec.core_query else []
        extra += [f"{f} {' '.join(a.lower() for a in spec.asset_classes)}".strip() for f in spec.families]
        queries = extra + build_queries(assets, regimes, limit=self.limits.max_queries)
        rep = run_discovery(self.engine, self.connectors, queries[:self.limits.max_queries], budget,
                            limit_per_query=self.limits.results_per_query,
                            memory_days=self.settings.discovery.search_memory_days, campaign_id=cid,
                            on_search=on_search)
        for e in rep.errors:
            self._error(cid, "discovery", None, "connector", e)
        if rep.stopped_reason:
            raise _budget_from_reason(rep.stopped_reason)
        return rep.new_sources

    def _select(self, cid: int, spec: CampaignSpec, done: set[int]) -> list[tuple[int, str]]:
        """Rank unfetched campaign sources by tier and abstract relevance (cheap, deterministic; spec §84)."""
        terms = spec.families + [w for w in spec.core_query.split() if len(w) > 3] + \
            [a.lower() for a in spec.asset_classes]
        with session_scope(self.engine) as s:
            rows = s.scalars(select(Source).where(Source.campaign_id == cid,
                                                  Source.access_status == AccessStatus.NOT_FETCHED)).all()
            ranked = []
            for src in rows:
                if src.id in done:
                    continue
                facts = {f.fact_type: f.value for f in s.scalars(select(SourceFact).where(
                    SourceFact.source_id == src.id))}
                target = facts.get("PDF_URL") or src.url
                if not target:
                    continue
                rel = _relevance(f"{src.title} {facts.get('ABSTRACT', '')}", terms)
                ranked.append(((src.tier or 9), -rel, src.id, target))
        ranked.sort()
        return [(sid, url) for _t, _r, sid, url in ranked[:self.limits.docs_per_round]]

    def _fetch_into(self, cid: int, source_id: int, url: str):
        resp = self.fetcher.fetch(url)
        with session_scope(self.engine) as s:
            src = s.get(Source, source_id)
            s.add(FetchLog(source_id=source_id, page_url=src.url, request_url=resp.url, status_code=resp.status_code,
                           content_type=(resp.content_type or "")[:200] or None, request_class=resp.request_class,
                           retrieval_method="CACHE" if resp.from_cache else "HTTP", error=resp.error))
            src.retrieved_at, src.access_status = resp.fetched_at, resp.access_status
            if not resp.ok:
                return None
            result = parse_bytes(resp.content, name=resp.final_url, content_type=resp.content_type,
                                 base_url=resp.final_url)
            apply_result(src, result)
            if result.access_status is not AccessStatus.OK:
                src.access_status = result.access_status
            return result

    def _deepen(self, cid: int, idea_id: int, budget: CampaignBudget) -> dict[str, int]:
        """Replication, contradiction and recent-evidence searches (spec §72–§74), then a grounded relation check."""
        with session_scope(self.engine) as s:
            idea = s.get(Idea, idea_id)
            name, root = idea.strategy_name, idea.root_evidence_id
            desc = f"{name}: {idea.summary}"[:600]
        year = datetime.now(UTC).year
        plans = [("REPLICATION", [f"{name} replication", f"{name} out of sample international evidence"]),
                 ("CONTRADICTION", [f"{name} fails", f"{name} transaction costs criticism",
                                    f"{name} post publication decay"]),
                 ("RECENT", [f"{name} {year - 2}", f"{name} {year - 1}"])]
        counts = {"REPLICATES": 0, "SUPPORTS": 0, "CONTRADICTS": 0, "UNRELATED": 0, "UNCLEAR": 0}
        for purpose, queries in plans:
            rep = run_discovery(self.engine, self.connectors, queries, budget, limit_per_query=5,
                                memory_days=self.settings.discovery.search_memory_days, campaign_id=cid,
                                purpose=purpose)
            if rep.stopped_reason:
                raise _budget_from_reason(rep.stopped_reason)
            for sid in rep.source_ids:
                rel = self._relation(cid, idea_id, root, desc, sid)
                if rel:
                    counts[rel] += 1
        with session_scope(self.engine) as s:
            idea = s.get(Idea, idea_id)
            idea.search_depth_level = max(idea.search_depth_level, 5)  # L4 contradiction + L5 recent searched (§43)
        return counts

    def _relation(self, cid: int, idea_id: int, root: str | None, desc: str, source_id: int) -> str | None:
        with session_scope(self.engine) as s:
            src = s.get(Source, source_id)
            if root and src.root_evidence_id == root:
                return None  # same study in another format is never independent evidence (spec §70)
            abstract = s.scalars(select(SourceFact.value).where(SourceFact.source_id == source_id,
                                                                SourceFact.fact_type == "ABSTRACT")).first()
            linked = s.scalars(select(IdeaSource.id).where(IdeaSource.idea_id == idea_id,
                                                           IdeaSource.source_id == source_id)).first()
        if not abstract or linked:
            return None
        res, _ = self.gw.run_json(task="stage_c_relation", prompt_version=prompts.STAGE_C_VERSION,
                                  provider=self.settings.ai.default_provider, model=self.settings.ai.cheap_model,
                                  system=prompts.SYSTEM, user=prompts.stage_c_user(desc, wrap_untrusted(
                                      abstract, f"source:{source_id}")),
                                  schema=RelationResult, max_tokens=300, campaign_id=cid, source_id=source_id,
                                  idea_id=idea_id)
        relation = res.relation
        grounded = quote_in_source(res.evidence_quote, normalize(abstract))
        if relation in RELATION_ROLES and (not grounded or res.confidence < MIN_RELATION_CONFIDENCE):
            relation = "UNCLEAR"  # unverifiable relation claims are not recorded as evidence
        if relation in RELATION_ROLES:
            with session_scope(self.engine) as s:
                s.add(IdeaSource(idea_id=idea_id, source_id=source_id, role=RELATION_ROLES[relation],
                                 note=f"abstract: “{res.evidence_quote[:280]}” (confidence "
                                      f"{res.confidence:.2f}, unverified beyond abstract)"))
        return relation

    # -- main -------------------------------------------------------------------------------------------------

    def run(self, cid: int) -> CampaignReport:
        with session_scope(self.engine) as s:
            c = s.get(Campaign, cid)
            spec = CampaignSpec(**c.spec)
            c.status = CampaignStatus.RUNNING
            request = c.request_text
        state = self._state(cid)
        budget = CampaignBudget(self.settings.budgets)
        budget.spent.update(state.get("spent", {}))
        self.gw.budget = budget  # AI calls count against the same campaign caps (spec §45)
        report = CampaignReport(cid, "RUNNING", None, spec.to_dict())
        done_docs = set(state.get("processed_sources", []))
        deepened = set(state.get("deepened_ideas", []))
        stop_reason, status = None, CampaignStatus.COMPLETED
        self._note(cid, budget, "start", f"Campaign {cid}: {request[:200]}")
        try:
            if "discover" not in state.get("phases_done", []):
                self._note(cid, budget, "search", "Searching arXiv, OpenAlex and Crossref for papers ...")
                report.new_sources = self._discover(cid, spec, budget)
                self._save(cid, budget, phases_done=state.get("phases_done", []) + ["discover"])
                report.phases.append("discover")
                self._note(cid, budget, "search", f"Search finished: {report.new_sources} new papers found")
                if report.new_sources == 0 and not self._select(cid, spec, done_docs):
                    stop_reason = "NO_NEW_SOURCES: searches returned nothing new (low yield, spec §44)"

            if stop_reason is None:
                chosen = self._select(cid, spec, done_docs)
                self._note(cid, budget, "read", f"Reading the {len(chosen)} most relevant papers",
                           papers_done=0, papers_total=len(chosen))
                for n, (sid, url) in enumerate(chosen, 1):
                    budget.spend("documents")
                    with session_scope(self.engine) as s:
                        title = s.get(Source, sid).title
                    self._note(cid, budget, "read", f"[{n}/{len(chosen)}] {title[:160]}", current_title=title[:300])
                    result = self._fetch_into(cid, sid, url)
                    done_docs.add(sid)
                    report.documents_processed += 1
                    if result is None or not result.blocks:
                        with session_scope(self.engine) as s:
                            access = s.get(Source, sid).access_status.value
                        self._note(cid, budget, "read", f"    not readable ({access.lower()}), skipped",
                                   papers_done=n)
                    else:
                        try:
                            ex = extract_ideas(self.gw, self.engine, self.settings, sid, result, campaign_id=cid)
                            report.ideas += ex.idea_ids
                            if ex.idea_ids:
                                with session_scope(self.engine) as s:
                                    names = [s.get(Idea, i).strategy_name[:90] for i in ex.idea_ids]
                                self._note(cid, budget, "extract",
                                           f"    {len(names)} strategy(ies): " + "; ".join(names), papers_done=n)
                            else:
                                why = {"TRIAGE_NOT_PROMISING": "no testable strategy in this paper",
                                       "NO_TEXT": "no text"}.get(ex.skipped_reason or "", "no strategy extracted")
                                self._note(cid, budget, "extract", f"    {why}", papers_done=n)
                        except (ProviderError, AIOutputError) as e:
                            self._error(cid, "extract", str(sid), "AIGateway", e)
                            self._note(cid, budget, "extract", f"    AI error, skipped: {str(e)[:120]}",
                                       papers_done=n)
                    self._save(cid, budget, processed_sources=sorted(done_docs))
                report.phases.append("fetch_extract")

                with session_scope(self.engine) as s:
                    ids = list(s.scalars(select(Idea.id).where(Idea.campaign_id == cid)))
                self._note(cid, budget, "score", f"Scoring {len(ids)} strategies")
                for i in ids:
                    score_idea(self.engine, self.settings, i)
                    self._note(cid, budget, "score", "    " + self._idea_line(i))
                report.phases.append("score")

                with session_scope(self.engine) as s:
                    top = list(s.scalars(select(Idea.id).where(
                        Idea.campaign_id == cid, Idea.status.in_([IdeaStatus.PROMISING, IdeaStatus.RESEARCHING]))
                        .order_by(Idea.research_priority_score.desc().nulls_last())
                        .limit(self.limits.deepen_top_ideas)))
                todo = [i for i in top if i not in deepened]
                if todo:
                    self._note(cid, budget, "deepen", f"Follow-up search (replication / criticism / recent) for "
                                                      f"{len(todo)} strategies")
                for i in todo:
                    with session_scope(self.engine) as s:
                        name = s.get(Idea, i).strategy_name[:90]
                    self._note(cid, budget, "deepen", f"    #{i} {name}")
                    counts = self._deepen(cid, i, budget)
                    for k, v in counts.items():
                        report.links[k] = report.links.get(k, 0) + v
                    score_idea(self.engine, self.settings, i)
                    deepened.add(i)
                    self._save(cid, budget, deepened_ideas=sorted(deepened))
                    self._note(cid, budget, "deepen",
                               f"      supports {counts['SUPPORTS'] + counts['REPLICATES']}, contradicts "
                               f"{counts['CONTRADICTS']} -> {self._idea_line(i)}")
                report.phases.append("deepen")
        except BudgetExhausted as e:
            stop_reason, status = f"BUDGET: {e}", CampaignStatus.STOPPED
        except UnpricedModelError as e:
            stop_reason, status = f"CONFIG: {e}", CampaignStatus.STOPPED

        with session_scope(self.engine) as s:
            ideas = s.scalars(select(Idea).where(Idea.campaign_id == cid)).all()
            report.ideas = [i.id for i in ideas]
            promising = [i for i in ideas if i.status in (IdeaStatus.PROMISING, IdeaStatus.SUBMITTED_TO_BACKTEST)]
            report.promising = [i.id for i in promising]
            families = {f for i in promising for f in (i.strategy_families or [])[:1]}
            if stop_reason is None:
                stop_reason = (f"TARGET_REACHED: {len(families)} distinct promising families"
                               if len(families) >= spec.target_families else
                               f"ROUND_COMPLETE: {len(families)}/{spec.target_families} promising families; "
                               "run again to continue (search memory avoids repeats)")
            c = s.get(Campaign, cid)
            c.status, c.stop_reason, c.finished_at = status, stop_reason, datetime.now(UTC)
        self._save(cid, budget)
        self._note(cid, budget, "done", f"Finished: {stop_reason}")
        report.status, report.stop_reason, report.budget_spent = status.value, stop_reason, budget.snapshot()
        return report

    def mark_interrupted(self, cid: int) -> None:
        """Ctrl+C: record the stop so the monitor and `--resume` show the right state."""
        with session_scope(self.engine) as s:
            c = s.get(Campaign, cid)
            c.status = CampaignStatus.STOPPED
            c.stop_reason = "INTERRUPTED by user; continue with: qsd campaign --resume " + str(cid)
            c.finished_at = datetime.now(UTC)
            state = dict(c.state or {})
            prog = dict(state.get("progress") or {})
            prog.update(phase="done", current=c.stop_reason)
            state["progress"] = prog
            c.state = state


def _budget_from_reason(reason: str) -> BudgetExhausted:
    m = re.search(r"budget exhausted: (\w+) \(limit ([\d.]+)\)", reason)
    return BudgetExhausted(m.group(1), float(m.group(2))) if m else BudgetExhausted("unknown", 0)
