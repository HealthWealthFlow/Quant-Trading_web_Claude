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
                        exit_rule="sell when < 0", claimed_cagr="25%")
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
