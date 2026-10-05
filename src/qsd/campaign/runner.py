"""Campaign runner (spec §41–§45, §72–§74, §127, §129): discover → select → fetch → extract → score → deepen
(replication / contradiction / recent evidence) → score → stop. Every phase saves progress in `campaigns.state`,
so an interrupted campaign resumes where it stopped instead of repeating (and re-paying for) work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, func, select

from ..ai import AIGateway, AIOutputError, ProviderError, UnpricedModelError, extract_ideas, prompts
from ..ai.grounding import normalize, quote_in_source
from ..ai.schemas import RelationResult
from ..config import Settings
from ..db import session_scope
from ..db.models import Campaign, ErrorRecord, FetchLog, Idea, IdeaSource, Source, SourceFact
from ..discovery import BudgetExhausted, CampaignBudget, build_queries, run_discovery
from ..discovery.connectors import Connector
from ..discovery.queries import ASSET_WORDS, REGIME_TERMS, expand_query, normalize_query
from ..fetch.client import PoliteFetcher
from ..fetch.enrich import enrich_source
from ..fetch.pipeline import apply_result
from ..fetch.youtube import CAPTION_FACT, Transcript, TranscriptError, fetch_transcript
from ..handlers import parse_bytes, parse_file
from ..scoring import score_idea
from ..security import wrap_untrusted
from ..taxonomy import (
    UNKNOWN,
    AccessStatus,
    AssetClass,
    CampaignStatus,
    ExtractionMethod,
    IdeaSourceRole,
    IdeaStatus,
    MarketRegime,
)
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


FINANCE_WORDS = re.compile(
    r"\b(trad\w*|stocks?|equit\w*|markets?|prices?|returns?|portfolios?|invest\w*|financ\w*|forex|currenc\w*|"
    r"futures|options?|crypto\w*|bitcoin|etfs?|funds?|hedg\w*|volatil\w*|momentum|sharpe|assets?|indicators?|"
    r"technical analysis|securities|commodit\w*|bonds?|interest rates?)\b", re.I)
_QUERY_STOP = {"find", "follow", "followed", "with", "that", "work", "works", "strategy", "strategies", "market",
               "markets", "bull", "bear", "bullish", "bearish", "sideways", "crash", "good", "best", "using"}


def plan_queries(spec: CampaignSpec, assets, regimes, limit: int) -> list[str]:
    """Searches built from the request itself first; generic query families only when no strategy type was named."""
    qs: list[str] = []
    core = spec.core_query.strip()
    if core:
        qs.append(core if FINANCE_WORDS.search(core) else f"{core} trading strategy")
        keywords = [w for w in re.findall(r"[A-Za-z][A-Za-z\-]{3,}", core) if w.lower() not in _QUERY_STOP]
        if 1 < len(keywords) <= 8:
            qs.append(" ".join(keywords) + " trading strategy")  # short form: arXiv ANDs every word
    asset_word = " ".join(ASSET_WORDS.get(a, a.value.lower()) for a in assets or [])
    for fam in spec.families:
        qs.append(f"{fam} trading strategy {asset_word}".strip())
        qs += [f"{fam} strategy {REGIME_TERMS[r][0]}" for r in regimes or []]
    for fam in spec.families:
        qs += [f"{v} trading strategy" for v in expand_query(fam)[1:3]]
    if not spec.families:
        qs += build_queries(assets, regimes, limit=limit)
    seen, out = set(), []
    for q in qs:
        key = normalize_query(q)
        if key and key not in seen:
            seen.add(key)
            out.append(q)
    return out[:limit]


def _relevance(text: str, terms: list[str]) -> int:
    t = text.lower()
    return sum(1 for term in terms if term and term.lower() in t)


# Calibrated from the live score table (docs/GATE_CALIBRATION.md): the best idea falls 6.31 points short of the
# PROMISING gate and `expected_robustness` is 0.00 on 14 of 15 ideas. That component scores the share of
# {out-of-sample, replication, cross-market} evidence present, so a source whose own abstract reports such testing
# is materially more likely to clear the gate than one that does not. This is a retrieval preference only: it never
# invents evidence, it only reads what the source already says.
EVIDENCE_TERMS = (
    "out-of-sample", "out of sample", "oos ", "holdout", "hold-out", "walk-forward", "walk forward",
    "robustness", "robust to", "replicat", "cross-market", "cross market", "across markets", "multiple markets",
    "international evidence", "subsample", "sub-sample", "sensitivity analysis", "parameter sensitivity",
    "transaction cost", "trading cost", "after costs", "net of costs", "backtest", "back-test",
)


def _evidence_hits(text: str) -> int:
    """How many distinct robustness/validation signals the text itself claims."""
    t = text.lower()
    return sum(1 for term in EVIDENCE_TERMS if term in t)


def _looks_free(src, facts: dict, seeded: bool = False) -> bool:
    """Whether a stored open copy is likely: a PDF link, an arXiv page, a local file, a video, or a user-listed source.

    Deliberately generous and purely a *ranking* signal — the fetcher still decides access for real, and a wrong guess
    costs one document slot, not correctness. `seeded` covers sources.txt/YouTube items, which are readable by
    construction; without it a paywall could outrank something the operator explicitly asked to read.
    """
    if seeded or facts.get("PDF_URL") or src.local_path:
        return True
    url = (src.url or src.canonical_url or "").lower()
    return any(token in url for token in ("arxiv.org", "youtube.com/watch", ".pdf", "openaccess", "ssrn.com"))


class CampaignRunner:
    def __init__(self, engine: Engine, settings: Settings, fetcher: PoliteFetcher, gateway: AIGateway,
                 connectors: list[Connector], limits: CampaignLimits | None = None, on_event=None,
                 per_round_search_budget: bool = False):
        """`on_event(phase, message)` receives every progress message (e.g. to print it); the same messages and
        counters are stored in `campaigns.state["progress"]` for the dashboard's live monitor.

        `per_round_search_budget` gives every run a fresh search allowance instead of the campaign-lifetime one. A
        harvest is one campaign spanning many rounds, and with a lifetime cap the searches spent on discovery and
        deepening in round 1 would block every later round (measured: a single round used 97 of 100).
        """
        self.engine, self.settings, self.fetcher, self.gw = engine, settings, fetcher, gateway
        self.connectors = connectors
        self.limits = limits or CampaignLimits()
        self.on_event = on_event
        self.per_round_search_budget = per_round_search_budget

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

    def create_from_sources(self, items, youtube=None, label: str = "sources.txt") -> tuple[int, object]:
        """A campaign that reads the listed sources instead of searching (search phase marked done)."""
        from .seed import seed_sources

        with session_scope(self.engine) as s:
            spec = CampaignSpec(request=f"Sources from {label}", mode="SOURCES_FILE")
            c = Campaign(request_text=spec.request, mode=spec.mode, asset_classes=[], strategy_families=[],
                         target_regimes=[], spec=spec.to_dict(), budgets=self.settings.budgets.model_dump(),
                         state={"phases_done": ["discover"]}, status=CampaignStatus.PLANNED)
            s.add(c)
            s.flush()
            cid = c.id
        budget = CampaignBudget(self.settings.budgets)
        self._note(cid, budget, "start", f"Reading {len(items)} line(s) from {label}")
        rep = seed_sources(self.engine, cid, items, youtube, budget,
                           self.settings.discovery.youtube_max_links_per_video,
                           note=lambda m: self._note(cid, budget, "sources", m))
        for p in rep.problems:
            self._note(cid, budget, "sources", f"problem: {p}")
        self._note(cid, budget, "sources", f"{rep.added} source(s) queued, {rep.already_read} already read before")
        self._save(cid, budget)
        return cid, rep

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

    def _discover(self, cid: int, spec: CampaignSpec, budget: CampaignBudget,
                  extra_queries: list[str] | None = None) -> int:
        found_total = [0]

        def on_search(conn: str, query: str, found, new: int, done: int, total: int) -> None:
            found_total[0] += new
            what = ("skipped (searched recently)" if found is None else "failed" if found == -1
                    else f"{found} results, {new} new")
            self._note(cid, budget, "search", f"[{done}/{total}] {conn}: \"{query[:80]}\" - {what}",
                       searches_done=done, searches_total=total, sources_found=found_total[0])

        assets = [AssetClass(a) for a in spec.asset_classes] or None
        regimes = [MarketRegime(r) for r in spec.regimes] or None
        # main's plan_queries() builds the request-derived plan; self-directed directions go in FRONT of it, because in
        # a multi-round harvest the static plan is exhausted after round 1 (every query lands in search memory) and
        # these — derived from what the campaign actually extracted — are the only ones likely to return anything new.
        planned = plan_queries(spec, assets, regimes, self.limits.max_queries)
        queries = list(dict.fromkeys(list(extra_queries or []) + planned))
        rep = run_discovery(self.engine, self.connectors, queries[:self.limits.max_queries], budget,
                            limit_per_query=self.limits.results_per_query,
                            memory_days=self.settings.discovery.search_memory_days, campaign_id=cid,
                            on_search=on_search, video_links=self.settings.discovery.youtube_max_links_per_video)
        for e in rep.errors:
            self._error(cid, "discovery", None, "connector", e)
        if rep.stopped_reason:
            raise _budget_from_reason(rep.stopped_reason)
        return rep.new_sources

    def _select(self, cid: int, spec: CampaignSpec, done: set[int]) -> list[tuple[int, str]]:
        """Rank unfetched campaign sources by tier, free access, relevance and reported evidence (spec §84).

        Order of preference inside a tier:
        1. **free** — a stored `PDF_URL` (usually an open copy), an arXiv link, a local file or a video. Ranking a
           paywall first wastes one of the round's few document slots: measured live, a round selected four
           `access_restricted` papers and produced nothing at all.
        2. relevance to the request.
        3. reported robustness evidence (`expected_robustness` is 0.00 on almost every idea measured; see
           docs/GATE_CALIBRATION.md).

        Tier still dominates all three, so a paywalled tier-1 paper is read before a free tier-4 blog.
        """
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
                target = facts.get("PDF_URL") or src.url or src.local_path
                if not target:
                    continue
                text = f"{src.title} {facts.get('ABSTRACT', '')}"
                seeded = spec.mode == "SOURCES_FILE" or "ID_YOUTUBE" in facts
                judged = src.title != UNKNOWN or bool(facts.get("ABSTRACT"))
                if judged and not seeded and not FINANCE_WORDS.search(text):
                    continue  # e.g. "Shock breakout in supernovae": same word, not a market paper
                rel = _relevance(text, terms)
                evidence = _evidence_hits(text)
                free = _looks_free(src, facts, seeded)
                # readable-for-free first, then relevance, then TIER, then reported robustness evidence as the final
                # tie-break. Evidence must not outrank tier: `expected_robustness` scores 0.00 on almost every idea
                # measured, so a source that reports out-of-sample/replication testing is worth reading before an
                # equally-ranked one — but a tier-4 blog must never displace a tier-1 paper (docs/GATE_CALIBRATION.md).
                ranked.append((0 if free else 1, -rel, src.tier or 9, -evidence, src.id, target))
        ranked.sort()
        return [(sid, url) for *_k, sid, url in ranked[:self.limits.docs_per_round]]

    def _video_description(self, source_id: int, cid: int | None = None):
        """A YouTube video is read through its API metadata: title, description and — when enabled — its captions.

        The video file itself is never downloaded: caption text only (DECISIONS D29).
        """
        with session_scope(self.engine) as s:
            src = s.get(Source, source_id)
            facts = {f.fact_type: f.value for f in s.scalars(select(SourceFact).where(
                SourceFact.source_id == source_id))}
            if "ID_YOUTUBE" not in facts:
                return None, False
            if not src.title or src.title == UNKNOWN:
                src.title = f"YouTube video {facts['ID_YOUTUBE']}"[:2000]
            # Prefer the spoken content: a description is a title, links and marketing, while the strategy itself
            # is in the transcript. Falls back to the description whenever captions are unavailable.
            transcript = facts.get(CAPTION_FACT)
            if not transcript and self.settings.discovery.youtube_transcripts:
                transcript = self._fetch_transcript(cid, source_id, facts["ID_YOUTUBE"])
            body = transcript or facts.get("ABSTRACT", "")
            text = f"{src.title}\n\nChannel: {src.author}\n\n{body}".strip()
            s.add(FetchLog(source_id=source_id, page_url=src.url, request_url=src.url, status_code=200,
                           content_type="text/plain", retrieval_method="API_TRANSCRIPT" if transcript
                           else "API_METADATA"))
            src.retrieved_at, src.access_status = datetime.now(UTC), AccessStatus.OK
            if transcript:
                src.format = "youtube-transcript"
                # The transcript is untrusted like any fetched page: `parse_bytes` scans it for injection text and
                # `extract_ideas` wraps it before it reaches a model, while grounding validates quotes against this
                # same raw text (wrapping here would break every quote).
                result = parse_bytes(text.encode("utf-8"), name="youtube-transcript.txt", content_type="text/plain")
            else:
                result = parse_bytes(text.encode("utf-8"), name="youtube-description.txt", content_type="text/plain")
                src.format = "youtube-description"
            return result, True

    def _fetch_transcript(self, cid: int | None, source_id: int, video_id: str) -> str | None:
        """Captions for one video, cached as a source fact so a resumed campaign never pays for them twice."""
        try:
            got: Transcript | None = fetch_transcript(
                video_id, languages=self.settings.discovery.youtube_caption_languages,
                timeout=self.settings.discovery.youtube_transcript_timeout_seconds)
        except TranscriptError as e:
            self._error(cid, "transcript", str(source_id), "YouTubeTranscript", e)
            return None
        if got is None:
            return None
        with session_scope(self.engine) as s:
            s.add(SourceFact(source_id=source_id, fact_type=CAPTION_FACT, value=got.text,
                             location=f"t={got.language}", extraction_method=ExtractionMethod.DETERMINISTIC,
                             confidence=1.0))
        return got.text

    def _local_file(self, source_id: int):
        """Sources from sources.txt can be files on this computer: parsed read-only, never uploaded."""
        with session_scope(self.engine) as s:
            src = s.get(Source, source_id)
            if not src.local_path or src.url:
                return None, False
            path = Path(src.local_path)
            s.add(FetchLog(source_id=source_id, request_url=f"file:{src.local_path}", retrieval_method="LOCAL_FILE",
                           error=None if path.is_file() else "FILE_NOT_FOUND"))
            src.retrieved_at = datetime.now(UTC)
            if not path.is_file():
                src.access_status = AccessStatus.ERROR
                return None, True
            result = parse_file(path)
            apply_result(src, result)
            enrich_source(s, src, result, local_path=path)
            src.access_status = result.access_status
            _reapply_title(s, src)
            return result, True

    def _fetch_into(self, cid: int, source_id: int, url: str):
        video, is_video = self._video_description(source_id, cid)
        if is_video:
            return video
        local, is_local = self._local_file(source_id)
        if is_local:
            return local
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
            _reapply_title(s, src)
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
        papers_only = [c for c in self.connectors if c.name != "youtube"]  # evidence comes from papers
        for purpose, queries in plans:
            rep = run_discovery(self.engine, papers_only, queries, budget, limit_per_query=5,
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

    def run(self, cid: int, extra_queries: list[str] | None = None) -> CampaignReport:
        with session_scope(self.engine) as s:
            c = s.get(Campaign, cid)
            spec = CampaignSpec(**c.spec)
            c.status = CampaignStatus.RUNNING
            request = c.request_text
        state = self._state(cid)
        budget = CampaignBudget(self.settings.budgets)
        budget.spent.update(state.get("spent", {}))
        if self.per_round_search_budget:
            # A new round gets a new search allowance; every other cap (cost, documents, urls, tokens) stays
            # cumulative, so an unattended harvest still cannot outspend its budget.
            budget.spent["search_requests"] = 0.0
        self.gw.budget = budget  # AI calls count against the same campaign caps (spec §45)
        report = CampaignReport(cid, "RUNNING", None, spec.to_dict())
        done_docs = set(state.get("processed_sources", []))
        deepened = set(state.get("deepened_ideas", []))
        stop_reason, status = None, CampaignStatus.COMPLETED
        self._note(cid, budget, "start", f"Campaign {cid}: {request[:200]}")
        try:
            if "discover" not in state.get("phases_done", []):
                self._note(cid, budget, "search", "Searching arXiv, OpenAlex and Crossref for papers ...")
                report.new_sources = self._discover(cid, spec, budget, extra_queries=extra_queries)
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
                        src = s.get(Source, sid)
                        title = src.title + (" (YouTube video)" if "youtube.com/watch" in (src.url or "") else "")
                    self._note(cid, budget, "read", f"[{n}/{len(chosen)}] {title[:180]}", current_title=title[:300])
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
                        Idea.campaign_id == cid,
                        Idea.status.in_([IdeaStatus.PROMISING, IdeaStatus.READY_FOR_FORMALIZATION,
                                         IdeaStatus.RESEARCHING]))
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
            promising = [i for i in ideas if i.status in (IdeaStatus.PROMISING, IdeaStatus.READY_FOR_FORMALIZATION,
                                                          IdeaStatus.SUBMITTED_TO_BACKTEST)]
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


def _reapply_title(s, src: Source) -> None:
    """A title given in sources.txt wins over the document's own metadata title."""
    override = s.scalars(select(SourceFact.value).where(SourceFact.source_id == src.id,
                                                        SourceFact.fact_type == "TITLE_OVERRIDE")).first()
    if override:
        src.title = override


def _budget_from_reason(reason: str) -> BudgetExhausted:
    m = re.search(r"budget exhausted: (\w+) \(limit ([\d.]+)\)", reason)
    return BudgetExhausted(m.group(1), float(m.group(2))) if m else BudgetExhausted("unknown", 0)
