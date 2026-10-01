"""Guided research run (`qsd research`, `research.bat`): ask what to research → show how it was understood →
run the campaign with live progress (console + dashboard monitor) → score → follow-up searches → hand eligible
ideas to the backtest queue → summary, with the option to read more papers for the same research.

Everything here reuses the normal campaign machinery; budgets and safeguards are unchanged.
"""

from __future__ import annotations

import socket
import threading
import time
import webbrowser
from collections.abc import Callable
from contextlib import AbstractContextManager

import httpx
from sqlalchemy import Engine, func, select

from .campaign import CampaignLimits, parse_request
from .config import Settings
from .db import session_scope
from .db.models import AICall, Campaign, Idea, Source
from .taxonomy import REGIME_LABELS, AccessStatus, IdeaStatus, MarketRegime

DEFAULT_PORT = 8877
DEFAULT_DOCS = 10
MAX_DOCS = 50
EXAMPLES = ('"Find crash-protection ETF strategies"', '"Momentum strategies for stocks in bull markets"',
            '"Mean-reversion forex strategies for sideways markets"')

RunnerFactory = Callable[[CampaignLimits], AbstractContextManager]


def port_owner(port: int) -> str:
    """'free', 'qsd' (our dashboard is already running) or 'other' (another program uses the port)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        if sock.connect_ex(("127.0.0.1", port)) != 0:
            return "free"
    try:
        page = httpx.get(f"http://127.0.0.1:{port}/", timeout=3).text
    except httpx.HTTPError:
        return "other"
    return "qsd" if "Quant Strategy Discovery" in page else "other"


def start_dashboard(engine: Engine, settings: Settings, port: int = DEFAULT_PORT) -> tuple[str | None, str]:
    """Make sure the dashboard is reachable; start it inside this process if needed. Returns (url, note)."""
    url = f"http://127.0.0.1:{port}/"
    owner = port_owner(port)
    if owner == "qsd":
        return url, "dashboard already running"
    if owner == "other":
        return None, f"port {port} is used by another program, so the live monitor could not start"
    import uvicorn

    from .web import create_app

    server = uvicorn.Server(uvicorn.Config(create_app(engine, settings), host="127.0.0.1", port=port,
                                           log_level="warning"))
    threading.Thread(target=server.run, daemon=True, name="qsd-dashboard").start()
    for _ in range(50):
        if server.started:
            return url, "dashboard started"
        time.sleep(0.1)
    return None, "the dashboard did not start in time"


def describe(spec) -> list[str]:
    regimes = [REGIME_LABELS[MarketRegime(r)] for r in spec.regimes]
    lines = [f"  Assets:            {', '.join(spec.asset_classes) or 'any'}",
             f"  Market direction:  {', '.join(regimes) or 'any'}",
             f"  Strategy types:    {', '.join(spec.families) or 'any'}",
             f"  Search words:      {spec.core_query or '-'}"]
    if not (spec.asset_classes or spec.regimes or spec.families):
        lines.append("  (No asset, market direction or strategy type recognised; your words are searched as given.)")
    return lines


def cost_per_paper(engine: Engine) -> float | None:
    """Average AI cost of reading one paper so far (for an honest estimate before starting)."""
    with session_scope(engine) as s:
        cost = s.execute(select(func.coalesce(func.sum(AICall.cost_usd), 0.0)).where(
            AICall.task.in_(["stage_a_triage", "stage_b_extract"]))).scalar_one()
        papers = s.execute(select(func.count(func.distinct(AICall.source_id))).where(
            AICall.task == "stage_a_triage", AICall.cache_hit.is_(False))).scalar_one()
    return float(cost) / papers if papers else None


def summary_lines(engine: Engine, cid: int) -> list[str]:
    with session_scope(engine) as s:
        c = s.get(Campaign, cid)
        ideas = s.scalars(select(Idea).where(Idea.campaign_id == cid)
                          .order_by(Idea.research_priority_score.desc().nulls_last())).all()
        read = s.execute(select(func.count()).select_from(Source).where(
            Source.campaign_id == cid, Source.access_status != AccessStatus.NOT_FETCHED)).scalar_one()
        found = s.execute(select(func.count()).select_from(Source).where(Source.campaign_id == cid)).scalar_one()
        cost = float((c.state or {}).get("spent", {}).get("ai_cost_usd", 0.0))
        lines = [f"Campaign {cid}: {c.status.value.lower()} - {c.stop_reason or ''}",
                 f"Papers found: {found}   papers read: {read}   strategies: {len(ideas)}   AI cost: ${cost:.3f}", ""]
        if ideas:
            lines.append("  id  status                 quality  strategy")
            for i in ideas:
                q = "   -  " if i.idea_quality_score is None else f"{i.idea_quality_score:6.1f}"
                lines.append(f"{i.id:>4}  {i.status.value:<22} {q}   {i.strategy_name[:80]}")
        promising = sum(i.status in (IdeaStatus.PROMISING, IdeaStatus.SUBMITTED_TO_BACKTEST) for i in ideas)
        lines += ["", f"Promising: {promising}. Ideas marked RESEARCHING need more evidence; ARCHIVED ones are weak "
                      "but kept on record."]
        return lines


def submit_ready(engine: Engine, settings: Settings, cid: int) -> list[str]:
    from .packaging import submit_to_queue

    with session_scope(engine) as s:
        ids = list(s.scalars(select(Idea.id).where(Idea.campaign_id == cid, Idea.status.in_(
            [IdeaStatus.PROMISING, IdeaStatus.READY_FOR_FORMALIZATION]))))
    out = []
    for i in ids:
        ok, path, reasons = submit_to_queue(engine, settings, i)
        out.append(f"  idea {i}: " + (f"sent to backtest queue -> {path}" if ok else
                                      "not sent yet: " + "; ".join(reasons)))
    return out or ["  No idea is ready for the backtest queue yet."]


def _ask_int(ask, prompt: str, default: int | None) -> int | None:
    raw = ask(prompt).strip()
    if not raw:
        return default
    try:
        return max(1, min(MAX_DOCS, int(raw)))
    except ValueError:
        return default


def run_wizard(engine: Engine, settings: Settings, make_runner: RunnerFactory, *, ask=input, out=print,
               open_browser=webbrowser.open, dashboard=start_dashboard, port: int = DEFAULT_PORT) -> int:
    from .cli import _run_with_interrupt

    out("=" * 72)
    out(" Quant Strategy Discovery - guided research")
    out(" Finds and fact-checks strategy ideas in research papers. It never trades.")
    out("=" * 72)
    b = settings.budgets
    while True:
        out("")
        out("What should I research? Examples: " + ", ".join(EXAMPLES))
        request = ask("Your research (or Q to quit): ").strip()
        if request.lower() in {"q", "quit", "exit"}:
            return 0
        if not request:
            continue
        spec = parse_request(request)
        out("")
        out("I understood:")
        for line in describe(spec):
            out(line)
        docs = _ask_int(ask, f"How many papers should I read in this round? [{DEFAULT_DOCS}]: ", DEFAULT_DOCS)
        per = cost_per_paper(engine)
        estimate = f"about ${per * docs:.2f} (based on papers read so far)" if per else "no estimate yet"
        out(f"AI cost for {docs} papers: {estimate}. Hard caps: ${b.max_ai_cost_usd_per_campaign:.2f} per campaign, "
            f"${b.max_ai_cost_usd_per_day:.2f} per day, ${b.max_ai_cost_usd_per_month:.2f} per month.")
        if ask("Start now? [Y/n]: ").strip().lower() in {"n", "no"}:
            continue
        break

    url, note = dashboard(engine, settings, port)
    if url:
        out(f"Live monitor: {url}live  ({note})")
        try:
            open_browser(f"{url}live")
        except Exception:  # noqa: BLE001 — no browser is not a reason to stop the research
            out("  (could not open the browser; open the address above yourself)")
    else:
        out(f"Live monitor not available: {note}. Progress is shown below.")
    out("")

    limits = CampaignLimits(docs_per_round=docs)
    cid = None
    while True:
        with make_runner(limits) as runner:
            if cid is None:
                cid = runner.create(request)
            report = _run_with_interrupt(runner, cid)
        if report is None:
            return 130
        out("")
        out("Backtest queue:")
        for line in submit_ready(engine, settings, cid):
            out(line)
        out("")
        for line in summary_lines(engine, cid):
            out(line)
        out("")
        more = _ask_int(ask, "Read more papers for this research? Type a number (e.g. 10), or press Enter to "
                             "finish: ", None)
        if not more:
            break
        limits = CampaignLimits(docs_per_round=more)
        out("")
    if url:
        out(f"Results stay on the dashboard: {url}ideas  (it closes when this window closes).")
        ask("Press Enter to close.")
    return 0
