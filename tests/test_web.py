import pytest
from fastapi.testclient import TestClient

from qsd.config import DEFAULT_CONFIG, load_settings
from qsd.db import init_db, make_engine, new_idea, session_scope
from qsd.db.models import AICall, ErrorRecord, IdeaSource, Source, SourceFact
from qsd.scoring import score_idea
from qsd.taxonomy import (
    ExtractionMethod,
    IdeaSourceRole,
    MarketRegime,
    RegimeBasis,
    RegimeSuitability,
    TimeHorizon,
)
from qsd.web import create_app

S = load_settings(DEFAULT_CONFIG, None, environ={})
EVIL = '<script>alert("x")</script>'


@pytest.fixture
def client(tmp_path):
    e = make_engine(tmp_path / "db.sqlite")
    init_db(e)
    with session_scope(e) as s:
        src = Source(title=f"Momentum {EVIL}", author="A", tier=1, url="javascript:alert(1)",
                     canonical_url="https://doi.org/10.1/x")
        bad = Source(title="Bad link", url="javascript:alert(2)")
        s.add_all([src, bad])
        s.flush()
        s.add(SourceFact(source_id=src.id, fact_type="ABSTRACT", value="1990 to 2010, out-of-sample",
                         extraction_method=ExtractionMethod.DETERMINISTIC, confidence=1))
        idea = new_idea(primary_source_id=src.id, strategy_name=f"TSMOM {EVIL}", asset_classes=["ETF"],
                        strategy_families=["TIME-SERIES MOMENTUM"], time_horizon=TimeHorizon.MONTHLY,
                        instrument="SPY", signal="12-month return > 0", entry_rule="buy when 12-month return > 0",
                        exit_rule="sell when < 0", claimed_cagr="25%",
                        grounding={"model": "deepseek-chat", "prompt_version": "b2", "values_offered": 8, "problems": 1,
                                   "needs_review": False, "realigned": ["signal"],
                                   "removed": [{"field": "stop_rule", "value": "10% stop", "quote": f"stop {EVIL}",
                                                "location": "p.4", "reason": "QUOTE_NOT_FOUND"}]})
        for r in idea.regimes:
            if r.regime is MarketRegime.CRASH:
                r.suitability, r.basis, r.confidence = RegimeSuitability.SUITED, RegimeBasis.SOURCE_STATED, 0.9
        s.add(idea)
        s.flush()
        s.add(IdeaSource(idea_id=idea.id, source_id=src.id, role=IdeaSourceRole.DESCRIBES))
        s.add(AICall(provider="deepseek", model="deepseek-chat", task="stage_a_triage", prompt_version="a1",
                     cache_key="k", input_tokens=100, output_tokens=10, cost_usd=0.0123))
        s.add(ErrorRecord(stage="fetch", error="HTTP_503", source_ref="https://x.example"))
        iid = idea.id
    score_idea(e, S, iid)
    return TestClient(create_app(e, S)), iid


def test_pages_render(client):
    c, iid = client
    for path in ("/", "/ideas", f"/ideas/{iid}", "/sources", "/ai-cost", "/errors", "/errors?all=true"):
        r = c.get(path)
        assert r.status_code == 200, path
    assert "Crash (crisis)" in c.get("/").text


def test_untrusted_text_is_escaped_and_bad_links_not_rendered(client):
    c, iid = client
    for path in ("/ideas", f"/ideas/{iid}", "/sources"):
        html = c.get(path).text
        assert EVIL not in html and "&lt;script&gt;" in html
        assert "javascript:" not in html


def test_idea_page_shows_claims_as_unvalidated_and_regime(client):
    c, iid = client
    html = c.get(f"/ideas/{iid}").text
    assert "EXTERNAL PERFORMANCE CLAIMS ARE NOT VALIDATED" in html and "not validated" in html
    assert "25%" in html and "source stated" in html
    assert "Research completeness" in html


def test_regime_filter(client):
    c, _ = client
    assert "TSMOM" in c.get("/ideas?regime=CRASH").text
    assert "TSMOM" not in c.get("/ideas?regime=BULLISH").text
    assert c.get("/ideas?regime=NOT_A_REGIME").status_code == 200  # ignored, not an error


def test_ai_cost_and_errors(client):
    c, _ = client
    assert "$0.0123" in c.get("/ai-cost").text
    assert "HTTP_503" in c.get("/errors").text


def test_read_only(client):
    c, iid = client
    assert c.post("/ideas").status_code == 405
    assert c.get("/ideas/999999").status_code == 404


def test_idea_page_lists_values_removed_by_fact_check(client):
    c, iid = client
    html = c.get(f"/ideas/{iid}").text
    assert "Removed by fact-check" in html and "1 of 8 values" in html
    assert "stop_rule" in html and "10% stop" in html and "quote not found" in html and "p.4" in html
    assert "small wording fixes" in html and EVIL not in html


def test_live_monitor(tmp_path):
    from qsd.db.models import Campaign
    from qsd.taxonomy import CampaignStatus

    e = make_engine(tmp_path / "live.sqlite")
    init_db(e)
    c = TestClient(create_app(e, S))
    assert "No research has been started yet" in c.get("/live").text
    with session_scope(e) as s:
        camp = Campaign(request_text=f"crash ETF {EVIL}", status=CampaignStatus.RUNNING, state={"progress": {
            "phase": "read", "current": f"[2/10] Paper {EVIL}", "papers_done": 2, "papers_total": 10,
            "searches_done": 6, "searches_total": 24, "ai_cost_usd": 0.05, "updated_at": "2999-01-01T00:00:00+00:00",
            "events": [{"t": "10:00:01", "phase": "search", "msg": "openalex: 10 results"},
                       {"t": "10:00:09", "phase": "read", "msg": "[2/10] Paper"}]}})
        s.add(camp)
        s.flush()
        s.add(new_idea(campaign_id=camp.id, strategy_name="Trend timing"))
        cid = camp.id
    html = c.get("/live").text
    assert 'http-equiv="refresh"' in html and "2 / 10" in html and "6 / 24" in html and "Trend timing" in html
    assert "width:20%" in html and EVIL not in html and "&lt;script&gt;" in html
    assert html.index("10:00:09") < html.index("10:00:01")  # newest first
    with session_scope(e) as s:
        s.get(Campaign, cid).status = CampaignStatus.COMPLETED
    assert 'http-equiv="refresh"' not in c.get(f"/live?id={cid}").text  # stops refreshing when finished
