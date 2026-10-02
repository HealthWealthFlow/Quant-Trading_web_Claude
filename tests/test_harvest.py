"""The harvest loop: rounds until enough ideas clear the gate, and every way it stops (spec §44, §123)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from qsd.campaign import run_harvest
from qsd.campaign.runner import CampaignReport
from qsd.config import DEFAULT_CONFIG, load_settings
from qsd.db import init_db, make_engine, session_scope
from qsd.db.models import Campaign, Idea
from qsd.taxonomy import CampaignStatus, IdeaStatus

# The real runner always carries a real Settings; the loop deep-copies it to set a per-round search allowance.
TEST_SETTINGS = load_settings(DEFAULT_CONFIG, None, environ={})


class ScriptedRunner:
    """Stands in for CampaignRunner: each round runs a scripted step instead of searching and reading."""

    def __init__(self, engine, steps):
        self.engine = engine
        self.steps = list(steps)
        self.limits = None
        self.runs = 0
        self.created: list[str] = []
        self._seq = 0  # every idea gets its own fingerprint, like distinct studies would
        self.per_round_search_budget = False
        self.settings = TEST_SETTINGS.model_copy(deep=True)

    def create(self, request: str) -> int:
        self.created.append(request)
        with session_scope(self.engine) as s:
            c = Campaign(request_text=request, mode="QUICK_DISCOVERY", state={"phases_done": []},
                         status=CampaignStatus.PLANNED)
            s.add(c)
            s.flush()
            return c.id

    def run(self, cid: int, extra_queries=None) -> CampaignReport:
        self.runs += 1
        step = self.steps.pop(0) if self.steps else {}
        for status in step.get("promising", []):
            self._seq += 1
            with session_scope(self.engine) as s:
                s.add(Idea(campaign_id=cid, strategy_name=f"idea {self._seq}", status=status,
                           fingerprint=f"fp-{self._seq}", search_depth_level=0))
        with session_scope(self.engine) as s:
            c = s.get(Campaign, cid)
            c.status = CampaignStatus.COMPLETED
        return CampaignReport(campaign_id=cid, status=CampaignStatus.COMPLETED.value,
                              stop_reason=step.get("stop_reason"), spec={},
                              documents_processed=step.get("docs", 2),
                              budget_spent={"ai_cost_usd": step.get("cost", 0.05)})


def engine_for(tmp_path):
    engine = make_engine(tmp_path / "db.sqlite")
    init_db(engine)
    return engine


def test_harvest_stops_as_soon_as_the_target_is_met(tmp_path):
    engine = engine_for(tmp_path)
    runner = ScriptedRunner(engine, [{"promising": [IdeaStatus.PROMISING]},
                                     {"promising": [IdeaStatus.PROMISING, IdeaStatus.PROMISING]}])
    rep = run_harvest(runner, request="breakout strategies", target=3, max_rounds=9)
    assert rep.rounds == 2 and rep.reached == 3 and rep.target_met
    assert rep.stop_reason.startswith("TARGET_REACHED")
    assert runner.runs == 2  # it did not keep going after the target
    assert rep.spent_usd == pytest.approx(0.10)


def test_harvest_clears_the_discover_phase_so_later_rounds_search_again(tmp_path):
    """Without this, every round after the first would only re-select the first round's candidate pool."""
    engine = engine_for(tmp_path)
    runner = ScriptedRunner(engine, [{"promising": []}, {"promising": []}])
    seen: list[list[str]] = []

    original_run = runner.run

    def spy(cid: int, extra_queries=None) -> CampaignReport:
        with session_scope(engine) as s:
            seen.append(list((s.get(Campaign, cid).state or {}).get("phases_done", [])))
        return original_run(cid, extra_queries=extra_queries)

    runner.run = spy
    run_harvest(runner, request="x", target=1, max_rounds=2, stall_rounds=5)
    assert seen == [[], []]  # discovery is allowed to run in every round


def test_a_round_that_found_nothing_is_retried_with_a_wider_search(tmp_path):
    """Measured defect (campaign 9): round 1 reported NO_NEW_SOURCES only because its queries were already in search
    memory, and the loop gave up before the widened allowance could help — which is the *normal* case for a daily
    scheduled harvest. Genuine emptiness is caught by the no-progress guard, not by this."""
    engine = engine_for(tmp_path)
    runner = ScriptedRunner(engine, [
        {"docs": 0, "stop_reason": "NO_NEW_SOURCES: searches returned nothing new (low yield, spec §44)"},
        {"docs": 2, "promising": [IdeaStatus.PROMISING]},
    ])
    rep = run_harvest(runner, request="x", target=1, max_rounds=3, stall_rounds=9, empty_round_limit=9)
    assert runner.runs == 2 and rep.target_met, "the second round must be allowed to search with a wider plan"


def test_config_errors_still_stop_immediately(tmp_path):
    engine = engine_for(tmp_path)
    runner = ScriptedRunner(engine, [{"stop_reason": "CONFIG: no price configured for model 'x'"}])
    rep = run_harvest(runner, request="x", target=1, max_rounds=3)
    assert runner.runs == 1 and rep.stop_reason.startswith("CONFIG:")


def test_harvest_stops_when_rounds_stop_producing(tmp_path):
    engine = engine_for(tmp_path)
    runner = ScriptedRunner(engine, [{"promising": []}, {"promising": []}, {"promising": []}, {"promising": []}])
    rep = run_harvest(runner, request="x", target=5, max_rounds=10, stall_rounds=3, empty_round_limit=9)
    assert rep.rounds == 3 and rep.reached == 0
    assert rep.stop_reason.startswith("STALLED")
    assert runner.runs == 3


def test_harvest_stops_quickly_when_a_round_finds_nothing_at_all(tmp_path):
    """Measured: an empty round still costs ~45s of searching, so an unattended run must not walk all rounds.

    Seen live — a round reported NO_NEW_SOURCES internally while the outer loop carried on for another full round.
    """
    engine = engine_for(tmp_path)
    runner = ScriptedRunner(engine, [{"docs": 0, "promising": []}, {"docs": 0, "promising": []},
                                     {"docs": 0, "promising": []}, {"docs": 0, "promising": []}])
    rep = run_harvest(runner, request="x", target=5, max_rounds=10, stall_rounds=9, empty_round_limit=2)
    assert runner.runs == 2  # not 10
    assert rep.stop_reason.startswith("NO_PROGRESS")


def test_a_round_that_reads_documents_is_not_treated_as_empty(tmp_path):
    engine = engine_for(tmp_path)
    runner = ScriptedRunner(engine, [{"docs": 3, "promising": []}, {"docs": 3, "promising": []},
                                     {"docs": 3, "promising": []}])
    rep = run_harvest(runner, request="x", target=5, max_rounds=3, stall_rounds=9, empty_round_limit=1)
    assert runner.runs == 3 and rep.stop_reason.startswith("MAX_ROUNDS")


def test_harvest_respects_the_round_limit(tmp_path):
    engine = engine_for(tmp_path)
    runner = ScriptedRunner(engine, [{"promising": [IdeaStatus.PROMISING]}] * 4)
    rep = run_harvest(runner, request="x", target=99, max_rounds=4, stall_rounds=99)
    assert rep.rounds == 4 and not rep.target_met
    assert rep.stop_reason.startswith("MAX_ROUNDS")


def test_harvest_sleeps_through_a_daily_budget_stop_and_resumes(tmp_path):
    """'Continuous' means waiting for the cap to reset, not overspending it."""
    engine = engine_for(tmp_path)
    runner = ScriptedRunner(engine, [
        {"stop_reason": "BUDGET: budget exhausted: ai_cost_usd_per_day (limit 1.0)"},
        {"promising": [IdeaStatus.PROMISING]},
    ])
    slept: list[float] = []
    rep = run_harvest(runner, request="x", target=1, max_rounds=3, sleep=lambda s: slept.append(s),
                      max_sleep_seconds=24 * 3600,
                      now=lambda: datetime(2026, 10, 2, 12, tzinfo=UTC))
    assert slept and slept[0] == pytest.approx(12 * 3600)  # noon -> midnight UTC
    assert rep.reached == 1 and rep.target_met and runner.runs == 2


def test_harvest_gives_up_when_the_reset_is_too_far_away(tmp_path):
    engine = engine_for(tmp_path)
    runner = ScriptedRunner(engine, [{"stop_reason": "BUDGET: budget exhausted: ai_cost_usd_per_day (limit 1.0)"}])
    rep = run_harvest(runner, request="x", target=1, max_rounds=3, max_sleep_seconds=60, sleep=lambda s: None)
    assert rep.stop_reason.startswith("BUDGET_DAY_EXHAUSTED") and runner.runs == 1


def test_harvest_stops_on_a_campaign_cap_instead_of_waiting(tmp_path):
    """A per-campaign cap will not reset, so sleeping would be pointless."""
    engine = engine_for(tmp_path)
    runner = ScriptedRunner(engine, [{"stop_reason": "BUDGET: budget exhausted: ai_cost_usd (limit 2.0)"}])
    rep = run_harvest(runner, request="x", target=1, max_rounds=3, sleep=lambda s: None)
    assert rep.stop_reason.startswith("BUDGET") and runner.runs == 1


def test_a_spent_search_allowance_does_not_end_the_campaign(tmp_path):
    """Search requests cost no AI money, so the next round gets a fresh allowance and new directions.

    Measured (campaign 5): one round spent 97 of 100 searches on discovery + deepening and the run ended, while the
    daily cost budget was untouched and thousands of unused query combinations remained.
    """
    engine = engine_for(tmp_path)
    runner = ScriptedRunner(engine, [
        {"stop_reason": "BUDGET: budget exhausted: search_requests (limit 100.0)"},
        {"promising": [IdeaStatus.PROMISING]},
    ])
    rep = run_harvest(runner, request="x", target=1, max_rounds=3, stall_rounds=9, empty_round_limit=9)
    assert runner.runs == 2, "the campaign must continue past a spent search allowance"
    assert rep.search_stops == 1 and rep.target_met
    assert "search_stops" in rep.as_dict()


def test_harvest_stops_on_a_config_error_and_on_a_dead_end(tmp_path):
    engine = engine_for(tmp_path)
    runner = ScriptedRunner(engine, [{"stop_reason": "CONFIG: no price configured for model 'x'"}])
    rep = run_harvest(runner, request="x", target=1, max_rounds=3)
    assert rep.stop_reason.startswith("CONFIG:") and runner.runs == 1

    # A round that found nothing is NOT terminal (the next round widens the search plan); repeated emptiness is.
    runner2 = ScriptedRunner(engine, [{"docs": 0, "stop_reason": "NO_NEW_SOURCES: searches returned nothing new"}] * 6)
    rep2 = run_harvest(runner2, request="x", target=1, max_rounds=6, stall_rounds=9, empty_round_limit=2)
    assert rep2.stop_reason.startswith("NO_PROGRESS") and runner2.runs == 2


def test_harvest_counts_distinct_ideas_not_duplicates(tmp_path):
    """Re-reading one paper must not inflate the target: identical fingerprints count once."""
    engine = engine_for(tmp_path)
    runner = ScriptedRunner(engine, [{"promising": [IdeaStatus.PROMISING, IdeaStatus.PROMISING]}])

    def run_with_same_fingerprint(cid: int, extra_queries=None) -> CampaignReport:
        runner.runs += 1
        with session_scope(engine) as s:
            for n in range(2):
                s.add(Idea(campaign_id=cid, strategy_name=f"dupe {n}", status=IdeaStatus.PROMISING,
                           fingerprint="same-paper", search_depth_level=0))
            s.get(Campaign, cid).status = CampaignStatus.COMPLETED
        return CampaignReport(campaign_id=cid, status="COMPLETED", stop_reason=None, spec={},
                              documents_processed=1, budget_spent={"ai_cost_usd": 0.02})

    runner.run = run_with_same_fingerprint
    rep = run_harvest(runner, request="x", target=2, max_rounds=1, stall_rounds=9)
    assert rep.reached == 1  # two rows, one study
    assert not rep.target_met


def test_harvest_stops_reading_at_the_document_budget(tmp_path):
    engine = engine_for(tmp_path)
    runner = ScriptedRunner(engine, [{"docs": 3, "promising": []}, {"docs": 3, "promising": []}])
    rep = run_harvest(runner, request="x", target=5, max_rounds=5, stall_rounds=9, docs_per_round=3, docs_total=4)
    assert runner.runs == 2  # 3 + 1 documents, then the total is spent
    assert rep.stop_reason.startswith("DOCS_TOTAL")


def test_harvest_gives_every_round_a_fresh_search_allowance(tmp_path):
    """Measured: one real round spent 97 of the 100-search campaign lifetime cap, so round 2 could not search.

    The other caps must stay cumulative or an unattended harvest could outspend its budget.
    """
    engine = engine_for(tmp_path)
    runner = ScriptedRunner(engine, [{"promising": []}, {"promising": []}])
    runner.settings.budgets.max_search_requests_per_campaign = 100
    seen: list[float] = []

    def run_and_record(cid: int, extra_queries=None) -> CampaignReport:
        seen.append(runner.settings.budgets.max_search_requests_per_campaign)
        return ScriptedRunner.run(runner, cid, extra_queries=extra_queries)

    runner.run = run_and_record
    run_harvest(runner, request="x", target=5, max_rounds=2, stall_rounds=9, search_budget_per_round=120)
    assert runner.per_round_search_budget is True
    assert runner.settings.budgets.max_search_requests_per_campaign == 120
    # the AI cost cap is untouched: only the search allowance is per-round
    assert runner.settings.budgets.max_ai_cost_usd_per_campaign == 2.0
    assert len(seen) == 2


def test_promising_ideas_ignores_other_statuses(tmp_path):
    from qsd.campaign import promising_ideas

    engine = engine_for(tmp_path)
    with session_scope(engine) as s:
        c = Campaign(request_text="x", state={"phases_done": []})
        s.add(c)
        s.flush()
        cid = c.id
        for status in (IdeaStatus.PROMISING, IdeaStatus.SUBMITTED_TO_BACKTEST, IdeaStatus.RESEARCHING,
                       IdeaStatus.ARCHIVED, IdeaStatus.NEEDS_REVIEW):
            s.add(Idea(campaign_id=cid, strategy_name=status.value, status=status, search_depth_level=0))
    assert len(promising_ideas(engine, cid)) == 2


def test_harvest_requires_exactly_one_of_request_or_resume(tmp_path):
    engine = engine_for(tmp_path)
    runner = ScriptedRunner(engine, [])
    with pytest.raises(ValueError):
        run_harvest(runner, target=1)
    with pytest.raises(ValueError):
        run_harvest(runner, request="x", campaign_id=1, target=1)


def test_harvest_resumes_an_existing_campaign_without_creating_one(tmp_path):
    engine = engine_for(tmp_path)
    runner = ScriptedRunner(engine, [{"promising": [IdeaStatus.PROMISING]}])
    with session_scope(engine) as s:
        c = Campaign(request_text="original", state={"phases_done": ["discover"]},
                     status=CampaignStatus.COMPLETED)
        s.add(c)
        s.flush()
        cid = c.id
    rep = run_harvest(runner, campaign_id=cid, target=1, max_rounds=2)
    assert runner.created == [] and rep.campaign_id == cid and rep.target_met
    with session_scope(engine) as s:
        assert s.get(Campaign, cid).state["phases_done"] == []  # reopened for discovery
