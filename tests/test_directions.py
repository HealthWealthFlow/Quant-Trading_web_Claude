"""Self-directed follow-up search: the loop picks its next directions from its own findings."""

from __future__ import annotations

from qsd.campaign import run_harvest
from qsd.campaign.directions import next_queries
from qsd.db import init_db, make_engine, session_scope
from qsd.db.models import Campaign, Idea
from qsd.taxonomy import IdeaStatus


def engine_for(tmp_path):
    engine = make_engine(tmp_path / "db.sqlite")
    init_db(engine)
    with session_scope(engine) as s:
        c = Campaign(request_text="x", state={"phases_done": []})
        s.add(c)
        s.flush()
        return engine, c.id


def add_idea(engine, cid, families, assets=("STOCK",), status=IdeaStatus.RESEARCHING):
    with session_scope(engine) as s:
        s.add(Idea(campaign_id=cid, strategy_name="i", strategy_families=list(families),
                   asset_classes=list(assets), status=status, search_depth_level=0))


def test_no_ideas_means_no_invented_directions(tmp_path):
    engine, cid = engine_for(tmp_path)
    assert next_queries(engine, cid) == []


def test_directions_come_only_from_extracted_families(tmp_path):
    engine, cid = engine_for(tmp_path)
    add_idea(engine, cid, ["MEAN REVERSION"], assets=("FOREX",))
    out = next_queries(engine, cid)
    assert out and all("mean reversion" in q.lower() for q in out)
    assert any("out of sample" in q for q in out)   # targets expected_robustness, the gate's biggest gap
    assert any("replication" in q for q in out)
    assert any("forex" in q for q in out)           # the asset the idea was tagged with


def test_families_are_pressed_in_order_of_how_much_was_found(tmp_path):
    engine, cid = engine_for(tmp_path)
    add_idea(engine, cid, ["CARRY"])
    for _ in range(3):
        add_idea(engine, cid, ["MOMENTUM"])
    out = next_queries(engine, cid, limit=3)
    assert all("momentum" in q.lower() for q in out)  # 3 ideas beat 1


def test_already_searched_directions_are_not_repeated(tmp_path):
    engine, cid = engine_for(tmp_path)
    add_idea(engine, cid, ["MOMENTUM"])
    first = next_queries(engine, cid, limit=2)
    second = next_queries(engine, cid, limit=2, already=set(first))
    assert not set(first) & set(second)


def test_limit_is_respected(tmp_path):
    engine, cid = engine_for(tmp_path)
    add_idea(engine, cid, ["A", "B", "C", "D", "E"])
    assert len(next_queries(engine, cid, limit=4)) == 4


class _Runner:
    """Records the follow-up queries each round is given, and adds an idea so round 2 has a direction."""

    def __init__(self, engine):
        from test_harvest import TEST_SETTINGS

        self.engine = engine
        self.calls: list[list[str]] = []
        self.limits = None
        self.per_round_search_budget = False
        self.created: list[str] = []
        self.settings = TEST_SETTINGS.model_copy(deep=True)

    def create(self, request):
        with session_scope(self.engine) as s:
            c = Campaign(request_text=request, state={"phases_done": []})
            s.add(c)
            s.flush()
            return c.id

    def run(self, cid, extra_queries=None):
        from qsd.campaign.runner import CampaignReport
        self.calls.append(list(extra_queries or []))
        if len(self.calls) == 1:  # round 1 discovers something for round 2 to build on
            add_idea(self.engine, cid, ["BREAKOUT"])
        return CampaignReport(campaign_id=cid, status="COMPLETED", stop_reason=None, spec={},
                              documents_processed=1, budget_spent={"ai_cost_usd": 0.01})


def test_the_loop_directs_its_own_later_rounds(tmp_path):
    """Round 1 follows the operator's request; round 2 must follow the campaign's own findings."""
    engine, cid = engine_for(tmp_path)
    runner = _Runner(engine)
    rep = run_harvest(runner, campaign_id=cid, target=5, max_rounds=3, stall_rounds=9, empty_round_limit=9)
    assert runner.calls[0] == [], "round 1 works from the request, not from non-existent ideas"
    assert runner.calls[1], "round 2 must be given directions derived from round 1"
    assert all("breakout" in q.lower() for q in runner.calls[1])
    assert rep.follow_up_queries == runner.calls[1]
    assert "follow_up_queries" in rep.as_dict()
