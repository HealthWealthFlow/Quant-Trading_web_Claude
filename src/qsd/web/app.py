"""Minimal read-only dashboard (spec §113–§121, §133, §137). Server-rendered, no JavaScript.

Security: every value is HTML-escaped (Jinja2 autoescape) because titles, quotes and URLs come from untrusted
sources; only http(s) links are rendered as links; the app has no write endpoints and binds to 127.0.0.1 by default.
"""

from __future__ import annotations

import calendar
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from jinja2 import DictLoader, Environment, select_autoescape
from sqlalchemy import Engine, case, func, select

from ..config import Settings
from ..db import session_scope
from ..db.models import AICall, Campaign, ErrorRecord, Idea, IdeaStatusHistory, Source, SourceFact
from ..packaging import build_package
from ..taxonomy import (
    REGIME_LABELS,
    WORTH_BACKTESTING,
    CampaignStatus,
    ErrorState,
    IdeaStatus,
    MarketRegime,
    RegimeSuitability,
)
from .templates import TEMPLATES

STATUS_KIND = {  # status → (tone, icon); tone colors always come with icon + text label
    "PROMISING": ("good", "▲"), "SUBMITTED_TO_BACKTEST": ("good", "✓"), "READY_FOR_FORMALIZATION": ("good", "✓"),
    "RESEARCHING": ("neutral", "…"), "DISCOVERED": ("neutral", "•"), "FILTERING": ("neutral", "•"),
    "NEEDS_REVIEW": ("warning", "!"), "DUPLICATE": ("neutral", "="), "ARCHIVED": ("neutral", "–"),
    "REJECTED": ("critical", "✕"),
}


def _safe_url(url: str | None) -> str | None:
    return url if url and url.lower().startswith(("http://", "https://")) else None


def _fmt(value, digits: int = 0) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    return f"{value:,}" if isinstance(value, int) else str(value)


def _money(value: float | None) -> str:
    return "—" if value is None else f"${value:,.4f}" if value < 1 else f"${value:,.2f}"


def _setup_state(idea) -> dict:
    """Whether this idea is a runnable setup, and what stops it.

    Readiness answers a different question from completeness - can this be handed to a backtester - so the
    dashboard shows both side by side rather than letting one number imply the other. Measured need: completeness
    alone made a fully-specified idea and an unusable one look alike.
    """
    from ..scoring import readiness as R

    state = R.setup_readiness(idea)
    skip = R.skip_decision(idea)
    if skip["skip"]:
        return {**state, "verdict": "skip", "skip": skip,
                "label": f"source states only {skip['share']:.0%} of the setup"}
    if not state["runnable"]:
        return {**state, "verdict": "blocked", "skip": skip,
                "label": "needs " + ", ".join(state["blocking_missing"])}
    if state["bridgeable_missing"]:
        return {**state, "verdict": "runnable", "skip": skip,
                "label": f"runnable · {len(state['bridgeable_missing'])} value(s) to fill"}
    return {**state, "verdict": "complete", "skip": skip, "label": "runnable as stated"}


def make_env() -> Environment:
    env = Environment(loader=DictLoader(TEMPLATES), autoescape=select_autoescape(default=True, default_for_string=True))
    env.filters.update(safe_url=_safe_url, fmt=_fmt, money=_money, setup=_setup_state)
    from ..scoring.rules import maturity

    env.globals.update(maturity=maturity, STATUS_KIND=STATUS_KIND,
                       REGIME_LABELS={r.value: v for r, v in REGIME_LABELS.items()})
    return env


def create_app(engine: Engine, settings: Settings) -> FastAPI:
    app = FastAPI(title="QSD dashboard", docs_url=None, redoc_url=None, openapi_url=None)
    env = make_env()

    def render(name: str, **ctx) -> HTMLResponse:
        return HTMLResponse(env.get_template(name).render(**ctx))

    def spent_since(s, since: datetime) -> float:
        return float(s.execute(select(func.coalesce(func.sum(AICall.cost_usd), 0.0))
                               .where(AICall.called_at >= since)).scalar_one())

    @app.get("/", response_class=HTMLResponse)
    def overview():
        now = datetime.now(UTC)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        with session_scope(engine) as s:
            count = lambda q: s.execute(q).scalar_one()  # noqa: E731
            by_status = dict(s.execute(select(Idea.status, func.count()).group_by(Idea.status)).all())
            regime_rows = []
            for r in MarketRegime:
                suited = s.execute(select(func.count()).select_from(Idea).where(Idea.regimes.any(
                    regime=r, suitability=RegimeSuitability.SUITED))).scalar_one()
                regime_rows.append({"regime": r.value, "label": REGIME_LABELS[r], "suited": suited})
            cards = [
                ("Sources discovered", count(select(func.count()).select_from(Source))),
                ("High-quality sources", count(select(func.count()).select_from(Source)
                                               .where(Source.source_quality_score >= 70))),
                ("Ideas discovered", count(select(func.count()).select_from(Idea))),
                ("Promising ideas", by_status.get(IdeaStatus.PROMISING, 0)),
                ("Sent to backtest", by_status.get(IdeaStatus.SUBMITTED_TO_BACKTEST, 0)),
                ("Duplicates", by_status.get(IdeaStatus.DUPLICATE, 0)),
                ("Rejected", by_status.get(IdeaStatus.REJECTED, 0)),
                ("Research jobs running", count(select(func.count()).select_from(Campaign)
                                                .where(Campaign.status == CampaignStatus.RUNNING))),
                ("AI spend today", _money(spent_since(s, day))),
                ("AI spend this month", _money(spent_since(s, day.replace(day=1)))),
                ("Unresolved errors", count(select(func.count()).select_from(ErrorRecord)
                                            .where(ErrorRecord.state == ErrorState.UNRESOLVED))),
            ]
            top = s.scalars(select(Idea).where(Idea.status.in_([IdeaStatus.PROMISING, IdeaStatus.RESEARCHING]))
                            .order_by(Idea.research_priority_score.desc().nulls_last()).limit(10)).all()
            return render("overview.html", cards=cards, regimes=regime_rows, top=top, now=now)

    @app.get("/ideas", response_class=HTMLResponse)
    def ideas(status: str | None = Query(None), regime: str | None = Query(None), asset: str | None = Query(None)):
        with session_scope(engine) as s:
            q = select(Idea).order_by(Idea.research_priority_score.desc().nulls_last(), Idea.id.desc()).limit(500)
            if status and status in IdeaStatus.__members__:
                q = q.where(Idea.status == IdeaStatus(status))
            if regime and regime in MarketRegime.__members__:
                q = q.where(Idea.regimes.any(regime=MarketRegime(regime), suitability=RegimeSuitability.SUITED))
            rows = s.scalars(q).all()
            if asset:
                rows = [i for i in rows if asset.upper() in (i.asset_classes or [])]
            sources = {src.id: src for src in s.scalars(select(Source).where(
                Source.id.in_([i.primary_source_id for i in rows if i.primary_source_id])))}
            return render("ideas.html", ideas=rows, sources=sources, statuses=list(IdeaStatus.__members__),
                          regimes=list(MarketRegime.__members__), f={"status": status, "regime": regime,
                                                                       "asset": asset})

    @app.get("/ideas/{idea_id}", response_class=HTMLResponse)
    def idea_detail(idea_id: int):
        with session_scope(engine) as s:
            idea = s.get(Idea, idea_id)
            if idea is None:
                raise HTTPException(404, "idea not found")
            history = s.scalars(select(IdeaStatusHistory).where(IdeaStatusHistory.idea_id == idea_id)
                                .order_by(IdeaStatusHistory.id)).all()
            pkg = build_package(engine, settings, idea_id)
            return render("idea.html", idea=idea, pkg=pkg, history=history)

    @app.get("/sources", response_class=HTMLResponse)
    def sources(tier: int | None = Query(None), access: str | None = Query(None)):
        with session_scope(engine) as s:
            q = select(Source).order_by(Source.source_quality_score.desc().nulls_last(), Source.id.desc()).limit(500)
            if tier:
                q = q.where(Source.tier == tier)
            if access:
                q = q.where(Source.access_status == access)
            rows = s.scalars(q).all()
            abstracts = {f.source_id for f in s.scalars(select(SourceFact).where(SourceFact.fact_type == "ABSTRACT"))}
            return render("sources.html", sources=rows, abstracts=abstracts, f={"tier": tier, "access": access})

    @app.get("/ai-cost", response_class=HTMLResponse)
    def ai_cost():
        now = datetime.now(UTC)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month = day.replace(day=1)
        with session_scope(engine) as s:
            base = select(AICall.provider, AICall.model, AICall.task, func.count(), func.sum(AICall.input_tokens),
                          func.sum(AICall.output_tokens), func.sum(AICall.cost_usd),
                          func.sum(case((AICall.cache_hit.is_(True), 1), else_=0)))
            rows = s.execute(base.where(AICall.called_at >= month)
                             .group_by(AICall.provider, AICall.model, AICall.task)).all()
            calls_today = s.execute(select(func.count()).select_from(AICall)
                                    .where(AICall.called_at >= day)).scalar_one()
            month_cost = spent_since(s, month)
            total_calls = sum(r[3] for r in rows)
            hits = sum(int(r[7] or 0) for r in rows)
            days_in_month = calendar.monthrange(now.year, now.month)[1]
            projection = month_cost / max(1, now.day) * days_in_month
            promising = s.execute(select(func.count()).select_from(Idea).where(
                Idea.status.in_([IdeaStatus.PROMISING, IdeaStatus.SUBMITTED_TO_BACKTEST]))).scalar_one()
            return render("ai_cost.html", rows=rows, calls_today=calls_today, month_cost=month_cost,
                          hit_rate=(hits / total_calls) if total_calls else None, projection=projection,
                          per_promising=(month_cost / promising) if promising else None, budgets=settings.budgets)

    @app.get("/errors", response_class=HTMLResponse)
    def errors(all: bool = Query(False)):  # noqa: A002
        with session_scope(engine) as s:
            q = select(ErrorRecord).order_by(ErrorRecord.id.desc()).limit(500)
            if not all:
                q = q.where(ErrorRecord.state.in_([ErrorState.UNRESOLVED, ErrorState.RETRYING]))
            return render("errors.html", errors=s.scalars(q).all(), show_all=all)

    @app.get("/live", response_class=HTMLResponse)
    def live(id: int | None = Query(None)):  # noqa: A002
        with session_scope(engine) as s:
            c = s.get(Campaign, id) if id else s.scalars(select(Campaign).order_by(Campaign.id.desc())).first()
            others = s.scalars(select(Campaign).where(Campaign.id != (c.id if c else 0))
                               .order_by(Campaign.id.desc()).limit(10)).all()
            if c is None:
                return render("live.html", c=None, others=others, running=False)
            p = dict((c.state or {}).get("progress") or {})
            ideas = s.scalars(select(Idea).where(Idea.campaign_id == c.id)
                              .order_by(Idea.research_priority_score.desc().nulls_last(), Idea.id)).all()
            sources_found = s.execute(select(func.count()).select_from(Source)
                                      .where(Source.campaign_id == c.id)).scalar_one()
            running = c.status in (CampaignStatus.RUNNING, CampaignStatus.PLANNED)
            stale = None
            if running and p.get("updated_at"):
                age = datetime.now(UTC) - datetime.fromisoformat(p["updated_at"])
                stale = int(age.total_seconds() // 60) if age.total_seconds() > 600 else None
            total = p.get("papers_total") or 0
            pct = round(100 * (p.get("papers_done") or 0) / total) if total else 0
            promising = sum(i.status in WORTH_BACKTESTING for i in ideas)
            return render("live.html", c=c, p=p, ideas=ideas, events=list(reversed(p.get("events") or [])),
                          sources_found=sources_found, running=running, stale=stale, pct=pct, promising=promising,
                          cap=settings.budgets.max_ai_cost_usd_per_campaign, others=others)

    @app.get("/healthz")
    def healthz(request: Request):
        return {"ok": True}

    return app
