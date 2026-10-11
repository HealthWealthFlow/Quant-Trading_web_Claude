"""Continuous harvest (spec §44, §123): run rounds until enough ideas clear the quality gate.

One `qsd campaign` run answers a request once. This module answers the actual research question — *keep looking
until N ideas are good enough to backtest* — and is explicit about every way it can stop, including the honest one:
the daily AI budget is a hard cap, so "continuous" means sleeping until the cap resets rather than overspending.

The target counts **distinct ideas at PROMISING or better**, as scored and deduplicated. Counting raw extractions
would be meaningless: every re-read of the same paper would inflate it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from ..db import session_scope
from ..db.models import Campaign, Idea, Source
from ..taxonomy import WORTH_BACKTESTING, CampaignStatus, IdeaStatus
from .directions import next_queries
from .runner import CampaignLimits, CampaignRunner

PROMISING_STATUSES = WORTH_BACKTESTING
STALL_ROUNDS_DEFAULT = 3

# Stop reasons that mean "the next round could not do better". `NO_NEW_SOURCES` is deliberately NOT here: a round
# that finds nothing usually ran out of *unsearched* queries, and the next round widens the allowance — treating it
# as terminal stopped the very first round on a topic whose queries were already in search memory (measured: campaign
# 9, `Search finished: 0 new papers found` on round 1, then it gave up instead of widening). Genuine emptiness is
# caught by the NO_PROGRESS guard below.
_DEAD_END_PREFIXES = ("CONFIG:",)


@dataclass
class HarvestReport:
    campaign_id: int
    target: int
    reached: int = 0
    rounds: int = 0
    stop_reason: str = ""
    round_reports: list[dict] = field(default_factory=list)
    spent_usd: float = 0.0
    follow_up_queries: list[str] = field(default_factory=list)  # directions the loop derived from its own findings
    search_stops: int = 0  # rounds that ended only because their search allowance ran out (recoverable)
    submitted: int = 0  # ideas handed to the backtest queue by this harvest (the system's actual output)

    @property
    def target_met(self) -> bool:
        return self.reached >= self.target

    def as_dict(self) -> dict:
        return {"campaign_id": self.campaign_id, "target": self.target, "reached": self.reached,
                "target_met": self.target_met, "rounds": self.rounds, "stop_reason": self.stop_reason,
                "spent_usd": round(self.spent_usd, 6), "search_stops": self.search_stops,
                "submitted": self.submitted,
                "follow_up_queries": self.follow_up_queries, "round_reports": self.round_reports}


def promising_ideas(engine, campaign_id: int) -> list[int]:
    """Ideas from this campaign that cleared the gate, deduplicated by fingerprint."""
    with session_scope(engine) as s:
        rows = s.scalars(select(Idea).where(Idea.campaign_id == campaign_id,
                                            Idea.status.in_(PROMISING_STATUSES))).all()
    seen: set[str] = set()
    out: list[int] = []
    for idea in rows:
        key = idea.fingerprint or f"idea:{idea.id}"
        if key in seen:
            continue
        seen.add(key)
        out.append(idea.id)
    return out


def submit_eligible(engine, settings, campaign_id: int) -> list[int]:
    """Hand every eligible PROMISING idea of this campaign to the backtest queue (the handoff rule decides).

    Measured need: the harvest counted PROMISING ideas as its target but never submitted any, so after 11 campaigns
    and 10 PROMISING ideas the queue was still empty. Ideas that fail the handoff rule stay PROMISING; `qsd queue
    --why` lists what blocks them.
    """
    from ..packaging import submit_to_queue

    with session_scope(engine) as s:
        ids = list(s.scalars(select(Idea.id).where(Idea.campaign_id == campaign_id, Idea.status.in_(
            [IdeaStatus.PROMISING, IdeaStatus.READY_FOR_FORMALIZATION]))))
    return [i for i in ids if submit_to_queue(engine, settings, i)[0]]


def _budget_expired(reason: str) -> bool:
    return "ai_cost_usd_per_day" in reason or "ai_cost_usd_per_month" in reason


def _search_budget_spent(reason: str) -> bool:
    """Search requests are free (no AI spend), so a spent allowance is a round state, not the end of the campaign."""
    return "search_requests" in reason


def _reopen(engine, campaign_id: int) -> None:
    """Make a finished campaign able to run another round.

    Two things are undone here, both deliberate:
    - `status`: the resume path must find the campaign runnable again.
    - `phases_done`: `CampaignRunner.run` treats a completed `discover` phase as permanent, which is right for a
      resume but wrong for a harvest round — without clearing it, every later round would only re-select the first
      round's candidate pool and no new sources would ever be searched for.
    Sources already read stay marked and any source this campaign already read keeps its identity, so nothing is
    re-read (the fetcher's cache and the AI cache would serve it free anyway).
    """
    with session_scope(engine) as s:
        c = s.get(Campaign, campaign_id)
        if c is None:
            return
        if c.status is CampaignStatus.COMPLETED:
            c.status = CampaignStatus.PLANNED
        state = dict(c.state or {})
        state["phases_done"] = []
        c.state = state


def run_harvest(runner: CampaignRunner, request: str | None = None, campaign_id: int | None = None,
                target: int = 5, max_rounds: int = 5, docs_per_round: int = 10, docs_total: int | None = None,
                max_queries: int = 8, deepen: int = 5, sleep_seconds: int = 0, max_sleep_seconds: int = 6 * 3600,
                stall_rounds: int = STALL_ROUNDS_DEFAULT, empty_round_limit: int = 2,
                search_budget_per_round: int = 120, on_event=None,
                sleep=time.sleep, now=lambda: datetime.now(UTC)) -> HarvestReport:
    """Run rounds until `target` distinct PROMISING ideas exist, the budget runs out, or progress stalls.

    Stops with `TARGET_REACHED`, `BUDGET_DAY_EXHAUSTED`, `BUDGET_MONTH_EXHAUSTED`, `MAX_ROUNDS`, `STALLED`,
    `NO_PROGRESS`, `DEAD_END` or `CONFIG:`. A day-budget stop sleeps until the next UTC day and then continues, which
    is what makes an unattended run possible under a hard daily cap.

    `search_budget_per_round` replaces the campaign-lifetime search cap for every run. Measured need: one real round
    spent 97 of the 100-search lifetime cap (discovery plus replication/contradiction searches for four ideas), so
    with a lifetime cap round 2 could not search at all. Every other cap stays cumulative.
    """
    if (request is None) == (campaign_id is None):
        raise ValueError("give exactly one of request / campaign_id")

    runner.limits = CampaignLimits(max_queries=max_queries, docs_per_round=docs_per_round,
                                   deepen_top_ideas=deepen)
    if search_budget_per_round and search_budget_per_round != runner.settings.budgets.max_search_requests_per_campaign:
        # Copy rather than mutate: the same Settings object may be shared with a dashboard or another caller.
        runner.settings = runner.settings.model_copy(deep=True)
        runner.settings.budgets.max_search_requests_per_campaign = search_budget_per_round
    runner.per_round_search_budget = True
    cid = campaign_id if campaign_id is not None else runner.create(request)
    report = HarvestReport(campaign_id=cid, target=target)
    stall = 0
    docs_used = 0
    searched: set[str] = set()  # follow-up directions already issued, so round 3 does not repeat round 2's

    def note(message: str) -> None:
        if on_event is not None:
            on_event("harvest", message)

    reached = promising_ideas(runner.engine, cid)
    report.reached = len(reached)
    empty_rounds = 0
    note(f"Harvest {cid}: target {target} promising idea(s); currently {report.reached}")

    while report.rounds < max_rounds and len(reached) < target:
        if docs_total is not None and docs_used >= docs_total:
            report.stop_reason = f"DOCS_TOTAL: read {docs_used} document(s)"
            break
        _reopen(runner.engine, cid)
        report.rounds += 1
        runner.limits.max_queries = max_queries + (report.rounds - 1) * max_queries
        # Direct the search from what the campaign has actually extracted. Without this the static plan is exhausted
        # after round 1 and later rounds only repeat queries already in search memory (measured: rounds 2 and 3 both
        # reported `0 new papers found`). Round 1 has no ideas yet, so it relies on the caller's request.
        if report.rounds > 1:
            follow_up = next_queries(runner.engine, cid, limit=max_queries, already=searched)
            searched.update(follow_up)
            report.follow_up_queries = list(dict.fromkeys(report.follow_up_queries + follow_up))
            if follow_up:
                note(f"Round {report.rounds}: following up on {len(follow_up)} direction(s) from previous rounds "
                     f"(e.g. \"{follow_up[0][:70]}\")")
        else:
            follow_up = []
        if docs_total is not None:
            runner.limits.docs_per_round = min(docs_per_round, docs_total - docs_used)
        note(f"Round {report.rounds}/{max_rounds}: {report.reached}/{target} promising")
        rnd = runner.run(cid, extra_queries=follow_up)
        report.spent_usd += float(rnd.budget_spent.get("ai_cost_usd", 0.0) or 0.0)
        docs_read = int(getattr(rnd, "documents_processed", 0) or 0)
        docs_used += docs_read
        submitted = submit_eligible(runner.engine, runner.settings, cid)
        report.submitted += len(submitted)
        if submitted:
            note(f"Sent {len(submitted)} idea(s) to the backtest queue: {', '.join(map(str, submitted))}")
        now_promising = promising_ideas(runner.engine, cid)
        report.round_reports.append({"round": report.rounds, "promising": len(now_promising),
                                     "ideas": len(rnd.ideas), "submitted": len(submitted),
                                     "stop_reason": rnd.stop_reason})
        gained = len(now_promising) - len(reached)
        reached = now_promising
        report.reached = len(reached)
        note(f"Round {report.rounds} done: +{gained} promising, {report.reached}/{target}, "
             f"spent ${report.spent_usd:.4f} — {rnd.stop_reason}")

        if len(reached) >= target:
            report.stop_reason = f"TARGET_REACHED: {len(reached)} distinct promising idea(s)"
            break
        # A round that neither found a source nor read anything cannot be improved by repeating it. Measured need:
        # a round with nothing discoverable still costs ~45s of search time, so an unattended run must cut it short
        # instead of walking through every remaining round.
        if docs_read == 0 and int(getattr(rnd, "new_sources", 0) or 0) == 0:
            empty_rounds += 1
            if empty_rounds >= empty_round_limit:
                report.stop_reason = (f"NO_PROGRESS: {empty_rounds} round(s) found no new source and read nothing; "
                                      f"{len(reached)}/{target} found")
                break
        else:
            empty_rounds = 0
        stall = stall + 1 if gained <= 0 else 0
        if stall >= stall_rounds:
            report.stop_reason = (f"STALLED: no new promising idea in {stall} round(s); "
                                  f"{len(reached)}/{target} found")
            break
        if rnd.stop_reason and rnd.stop_reason.startswith("CONFIG:"):
            report.stop_reason = rnd.stop_reason
            break
        if rnd.stop_reason and rnd.stop_reason.startswith("BUDGET:"):
            if _search_budget_spent(rnd.stop_reason):
                # Free to recover: searching costs HTTP requests, not AI money, and the next round gets a fresh
                # allowance with new directions. Measured in campaign 5: a single round spent 97 of 100 searches on
                # discovery + deepening and the run ended, although the daily *cost* budget was untouched and
                # thousands of unused query combinations remained.
                report.search_stops += 1
                note(f"Search allowance for this round is spent ({report.search_stops} time(s)); "
                     "the next round gets a fresh allowance and new directions")
            elif _budget_expired(rnd.stop_reason):
                wait = _seconds_until_next_day(now())
                if sleep_seconds > 0 and report.rounds < max_rounds:
                    wait = max(wait, sleep_seconds)
                if wait > max_sleep_seconds:
                    report.stop_reason = f"BUDGET_DAY_EXHAUSTED: {wait / 3600:.1f}h until reset (over max sleep)"
                    break
                note(f"Daily AI budget reached; sleeping {wait / 60:.1f} min until it resets")
                sleep(wait)
                continue  # the budget object is rebuilt per run, so the next round can spend again
            else:
                # urls / documents / per-campaign AI cost: cumulative within this campaign, so another round cannot
                # help. A campaign-level AI-cost cap is the operator's ceiling, not a scheduling accident.
                report.stop_reason = rnd.stop_reason
                break
        if any((rnd.stop_reason or "").startswith(p) for p in _DEAD_END_PREFIXES):
            report.stop_reason = rnd.stop_reason
            break
        # A round whose only outcome was "the queries I was allowed to run are already in search memory" cannot be
        # improved by repeating it with the same allowance — the next round widens it, so only a genuine empty round
        # (nothing found AND nothing read) counts toward the no-progress limit. (Measured: round 2 reported
        # `0 new papers found` purely because round 1 had already run those six queries.)
        if sleep_seconds > 0:
            sleep(sleep_seconds)

    if not report.stop_reason:
        report.stop_reason = (f"MAX_ROUNDS: {report.rounds} round(s) done, {report.reached}/{target} "
                              "promising idea(s)")
    return report


def _seconds_until_next_day(now: datetime) -> float:
    tomorrow = (now.astimezone(UTC) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60.0, (tomorrow - now.astimezone(UTC)).total_seconds())


def harvest_state(engine, campaign_id: int) -> dict:
    """What a resumed harvest should know: sources seen and ideas found so far."""
    with session_scope(engine) as s:
        sources = s.scalar(select(Source.id).where(Source.campaign_id == campaign_id).limit(1))
    return {"has_sources": sources is not None}
