import json

import httpx
import pytest
from fixtures import make_pdf
from sqlalchemy import select

from qsd.ai import AIGateway, ProviderResponse
from qsd.campaign import CampaignLimits, CampaignRunner, parse_request
from qsd.config import DEFAULT_CONFIG, load_settings
from qsd.db import init_db, make_engine, session_scope
from qsd.db.models import AICall, Campaign, Idea, IdeaSource, Source, SourceFact
from qsd.discovery import OpenAlexConnector
from qsd.fetch import PoliteFetcher
from qsd.taxonomy import CampaignStatus, ExtractionMethod, IdeaSourceRole

PAPER_PAGES = [
    "Crash protection with trend following ETFs. We hold SPY when its 10-month moving average is rising.",
    "Exit to cash when SPY closes below its 10-month moving average. Rebalanced monthly using SPY and cash.",
    "The rule reduced drawdowns during the 2008 crisis and other market crashes. Transaction costs of 5bp included.",
]

MAIN = {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/trend", "display_name":
        "Crash protection with trend following ETFs", "publication_date": "2015-01-01", "type": "article",
        "authorships": [{"author": {"display_name": "M. Faber"}}],
        "primary_location": {"landing_page_url": "https://papers.example/trend",
                             "source": {"display_name": "J. Portfolio"}},
        "best_oa_location": {"pdf_url": "https://papers.example/trend.pdf"},
        "abstract_inverted_index": {"Trend": [0], "following": [1], "ETF": [2], "crash": [3], "protection.": [4]}}
CRITIC = {"id": "https://openalex.org/W2", "doi": "https://doi.org/10.2/critic", "display_name":
          "Does trend following really protect?", "publication_date": "2022-01-01", "type": "article",
          "authorships": [{"author": {"display_name": "C. Critic"}}],
          "primary_location": {"landing_page_url": "https://papers.example/critic"},
          "abstract_inverted_index": {w: [i] for i, w in enumerate(
              "We find the timing benefit disappears after transaction costs in recent data.".split())}}
FAKE_SUPPORT = {"id": "https://openalex.org/W3", "doi": "https://doi.org/10.3/other", "display_name":
                "Unrelated equity study", "type": "article", "authorships": [],
                "primary_location": {"landing_page_url": "https://papers.example/other"},
                "abstract_inverted_index": {"Bond": [0], "duration": [1], "risk.": [2]}}


def openalex_handler(req: httpx.Request):
    q = req.url.params.get("search", "").lower()
    if req.url.host == "api.openalex.org":
        if any(k in q for k in ("fails", "criticism", "decay", "replication", "evidence", "20")):
            results = [CRITIC, FAKE_SUPPORT]
        else:
            results = [MAIN]
        return httpx.Response(200, json={"results": results})
    if req.url.path == "/robots.txt":
        return httpx.Response(200, content=b"User-agent: *\nAllow: /\n")
    if req.url.path == "/trend.pdf":
        return httpx.Response(200, content=make_pdf(PAPER_PAGES, title="Trend following"),
                              headers={"content-type": "application/pdf"})
    return httpx.Response(404)


STAGE_A = {"is_strategy_research": True, "asset_classes": ["ETF"], "strategy_families": ["TREND FOLLOWING"],
           "basic_idea": "trend filter", "red_flags": [], "worth_deep_read": True, "confidence": 0.9}
STAGE_B = {"strategies": [{
    "strategy_name": "10-month moving average timing", "summary": "Hold SPY above its 10-month MA, else cash.",
    "asset_classes": ["ETF"], "strategy_families": ["TREND FOLLOWING"], "position_direction": "LONG",
    "time_horizon": "MONTHLY",
    "rules": {
        "instrument": {"value": "SPY", "evidence_quote": "rebalanced monthly using spy and cash", "location": "p.2",
                       "confidence": 0.9},
        "universe": {"value": "SPY and cash", "evidence_quote": "using SPY and cash", "location": "p.2",
                     "confidence": 0.9},
        "entry_rule": {"value": "hold SPY when its 10-month moving average is rising",
                       "evidence_quote": "We hold SPY when its 10-month moving average is rising", "location": "p.1",
                       "confidence": 0.9},
        "exit_rule": {"value": "exit to cash when SPY closes below its 10-month moving average",
                      "evidence_quote": "Exit to cash when SPY closes below its 10-month moving average",
                      "location": "p.2", "confidence": 0.9},
        "rebalance": {"value": "monthly", "evidence_quote": "Rebalanced monthly", "location": "p.2",
                      "confidence": 0.9},
        "transaction_cost_assumption": {"value": "5bp", "evidence_quote": "Transaction costs of 5bp included",
                                        "location": "p.3", "confidence": 0.9},
        "position_sizing": {"value": "100% SPY or 100% cash", "evidence_quote": "using SPY and cash",
                            "location": "p.2", "confidence": 0.5},
        "lookback": {"value": "10-month", "evidence_quote": "10-month moving average", "location": "p.1",
                     "confidence": 0.9}},
    "rationale": {"value": "reduced drawdowns during crises",
                  "evidence_quote": "reduced drawdowns during the 2008 crisis", "location": "p.3", "confidence": 0.7},
    "regimes": [{"regime": "CRASH", "suitability": "SUITED", "basis": "SOURCE_STATED", "confidence": 0.9,
                 "evidence_quote": "reduced drawdowns during the 2008 crisis and other market crashes",
                 "location": "p.3"}]}]}
CONTRADICTS = {"relation": "CONTRADICTS", "evidence_quote": "the timing benefit disappears after transaction costs",
               "confidence": 0.85}
INVENTED = {"relation": "SUPPORTS", "evidence_quote": "strongly confirms moving average timing", "confidence": 0.9}


class ScriptedAI:
    name = "fake"

    def __init__(self):
        self.calls = []

    def complete(self, model, system, user, max_tokens):
        self.calls.append(user[:60])
        if "Task: triage" in user:
            out = STAGE_A
        elif "Task: extract" in user:
            out = STAGE_B
        elif "timing benefit disappears" in user:
            out = CONTRADICTS
        else:
            out = INVENTED  # relation claim with an invented quote → must be rejected by grounding
        return ProviderResponse(json.dumps(out), 800, 150, 3, model, truncated=False)


class Clock:
    t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


@pytest.fixture
def setup(tmp_path):
    s = load_settings(DEFAULT_CONFIG, None, environ={"QSD_AI__DEFAULT_PROVIDER": "fake",
                                                     "QSD_DISCOVERY__SEARCH_MEMORY_DAYS": "30"})
    s.ai.prices["deepseek-chat"] = type(s.ai.prices["claude-opus-5-5"])(input_per_mtok=1, output_per_mtok=2)
    e = make_engine(tmp_path / "db.sqlite")
    init_db(e)
    clock = Clock()
    fetcher = PoliteFetcher(s, transport=httpx.MockTransport(openalex_handler), clock=clock, sleep=clock.sleep)
    ai = ScriptedAI()
    runner = CampaignRunner(e, s, fetcher, AIGateway(e, s, {"fake": ai}), [OpenAlexConnector(fetcher)],
                            CampaignLimits(max_queries=2, docs_per_round=3, deepen_top_ideas=2))
    return s, e, runner, ai


def test_parse_request():
    spec = parse_request("Find crash-protection ETF trend strategies")
    assert spec.asset_classes == ["ETF"] and spec.regimes == ["CRASH"] and "trend following" in spec.families
    spec2 = parse_request("recent crypto funding rate strategies for sideways markets")
    assert spec2.asset_classes == ["CRYPTO"] and spec2.regimes == ["CONSOLIDATION"] and spec2.mode == "RECENT_ONLY"


def test_hyphenated_terms_survive_the_filler_words():
    """Measured bug: the filler regex used `\\b`, and a hyphen is a word boundary, so the `of` inside
    "out-of-sample" was stripped and the query became "out- -sample" — which went straight into the arXiv query as
    `all:out- AND all:-sample`, wasting a search and never matching the compound term."""
    import re

    spec = parse_request("trend following strategies with out-of-sample evidence")
    assert spec.core_query == "trend following out-of-sample evidence"
    terms = re.findall(r"[A-Za-z0-9\-]+", spec.core_query)
    assert "out-of-sample" in terms and "out-" not in terms and "-sample" not in terms
    # and the same for a term the operator naturally writes with a hyphen
    assert parse_request("Find crash-protection ETF strategies").core_query.startswith("crash-protection")


def test_selection_prefers_sources_that_report_evidence(setup):
    """Calibration-driven: `expected_robustness` is 0.00 on almost every idea, and a source whose abstract reports
    out-of-sample / replication testing is the cheapest way to change that. Evidence is a tie-breaker inside a tier,
    never a promotion across tiers, and it reads only what the source already claims."""
    from qsd.taxonomy import AccessStatus

    s, e, runner, _ai = setup
    runner.limits.docs_per_round = 1
    spec = parse_request("trend following ETF strategies")
    cid = runner.create("trend following ETF strategies")
    with session_scope(e) as sess:
        plain = Source(campaign_id=cid, title="Trend following in ETFs", tier=1,
                       access_status=AccessStatus.NOT_FETCHED, url="https://example.test/plain")
        robust = Source(campaign_id=cid, title="Trend following in ETFs", tier=1,
                        access_status=AccessStatus.NOT_FETCHED, url="https://example.test/robust")
        sess.add_all([plain, robust])
        sess.flush()
        # same relevance for both; only the evidence vocabulary differs
        sess.add(SourceFact(source_id=plain.id, fact_type="ABSTRACT", value="We study a trend strategy.",
                            extraction_method=ExtractionMethod.DETERMINISTIC, confidence=1.0))
        sess.add(SourceFact(source_id=robust.id, fact_type="ABSTRACT",
                            value="We study a trend strategy out-of-sample and across markets after costs.",
                            extraction_method=ExtractionMethod.DETERMINISTIC, confidence=1.0))
        robust_id = robust.id
    chosen = runner._select(cid, spec, done=set())
    assert len(chosen) == 1 and chosen[0][0] == robust_id


def test_selection_prefers_a_readable_source_over_a_paywall(setup):
    """Measured failure: a live round selected four `access_restricted` papers and produced nothing at all.

    A source with a stored open PDF (or arXiv/local/video) is read first inside its tier; the fetcher still decides
    access for real, so this only costs a slot when the guess is wrong — whereas the reverse wastes every slot.
    """
    from qsd.taxonomy import AccessStatus

    s, e, runner, _ai = setup
    runner.limits.docs_per_round = 1
    spec = parse_request("trend following ETF strategies")
    cid = runner.create("trend following ETF strategies")
    with session_scope(e) as sess:
        paywalled = Source(campaign_id=cid, title="Trend following", tier=1,
                           access_status=AccessStatus.NOT_FETCHED, url="https://publisher.test/paywalled")
        open_copy = Source(campaign_id=cid, title="Trend following", tier=1,
                           access_status=AccessStatus.NOT_FETCHED, url="https://publisher.test/landing")
        sess.add_all([paywalled, open_copy])
        sess.flush()
        sess.add(SourceFact(source_id=open_copy.id, fact_type="PDF_URL", value="https://arxiv.org/pdf/1304.6846",
                            extraction_method=ExtractionMethod.DETERMINISTIC, confidence=1.0))
        open_id = open_copy.id
    assert runner._select(cid, spec, done=set())[0][0] == open_id


def test_selection_does_not_let_evidence_outrank_a_better_tier(setup):
    from qsd.taxonomy import AccessStatus

    s, e, runner, _ai = setup
    runner.limits.docs_per_round = 1
    spec = parse_request("trend following ETF strategies")
    cid = runner.create("trend following ETF strategies")
    with session_scope(e) as sess:
        tier1 = Source(campaign_id=cid, title="Trend following", tier=1,
                       access_status=AccessStatus.NOT_FETCHED, url="https://example.test/t1")
        tier4 = Source(campaign_id=cid, title="Trend following", tier=4,
                       access_status=AccessStatus.NOT_FETCHED, url="https://example.test/t4")
        sess.add_all([tier1, tier4])
        sess.flush()
        sess.add(SourceFact(source_id=tier4.id, fact_type="ABSTRACT",
                            value="out-of-sample replication across markets after costs, walk-forward, robust",
                            extraction_method=ExtractionMethod.DETERMINISTIC, confidence=1.0))
        tier1_id = tier1.id
    assert runner._select(cid, spec, done=set())[0][0] == tier1_id


def test_campaign_end_to_end(setup):
    s, e, runner, ai = setup
    cid = runner.create("Find crash-protection ETF trend strategies")
    rep = runner.run(cid)
    assert rep.documents_processed >= 1 and len(rep.ideas) == 1
    assert rep.phases == ["discover", "fetch_extract", "score", "deepen"]
    with session_scope(e) as sess:
        idea = sess.get(Idea, rep.ideas[0])
        assert idea.strategy_name == "10-month moving average timing"
        assert idea.search_depth_level == 5
        roles = [x.role for x in sess.scalars(select(IdeaSource).where(IdeaSource.idea_id == idea.id))]
        assert IdeaSourceRole.CONTRADICTS in roles           # grounded contradiction recorded
        assert IdeaSourceRole.SUPPORTS not in roles          # invented supporting quote rejected
        c = sess.get(Campaign, cid)
        assert c.status is CampaignStatus.COMPLETED and c.stop_reason
        assert c.state["spent"]["documents"] >= 1
        assert sess.scalars(select(AICall)).first() is not None


def test_campaign_resume_does_not_repeat_paid_work(setup):
    s, e, runner, ai = setup
    cid = runner.create("Find crash-protection ETF trend strategies")
    runner.run(cid)
    with session_scope(e) as sess:
        first = set(sess.get(Campaign, cid).state["processed_sources"])
    n_extract = sum(1 for c in ai.calls if "Task: extract" in c)
    runner.run(cid)  # continue: new candidates found while deepening may be fetched; old ones never again
    with session_scope(e) as sess:
        second = set(sess.get(Campaign, cid).state["processed_sources"])
    assert first <= second
    assert sum(1 for c in ai.calls if "Task: extract" in c) == n_extract  # paper not re-extracted / re-paid
    runner.run(cid)
    with session_scope(e) as sess:
        assert set(sess.get(Campaign, cid).state["processed_sources"]) == second  # nothing left: no repeats


def test_budget_stop_is_recorded(setup, tmp_path):
    s, e, runner, ai = setup
    s.budgets.max_documents_per_campaign = 0
    cid = runner.create("Find crash-protection ETF trend strategies")
    rep = runner.run(cid)
    assert rep.status == "STOPPED" and "documents" in rep.stop_reason
    with session_scope(e) as sess:
        assert sess.get(Campaign, cid).status is CampaignStatus.STOPPED


def test_progress_is_reported_and_stored_for_the_live_monitor(setup):
    s, e, runner, ai = setup
    seen = []
    runner.on_event = lambda phase, msg: seen.append((phase, msg))
    cid = runner.create("Find crash-protection ETF trend strategies")
    runner.run(cid)
    phases = [p for p, _ in seen]
    assert phases[0] == "start" and phases[-1] == "done"
    assert {"search", "read", "extract", "score", "deepen"} <= set(phases)
    msgs = "\n".join(m for _, m in seen)
    assert "[1/1] Crash protection with trend following ETFs" in msgs
    assert "1 strategy(ies): 10-month moving average timing" in msgs
    assert "openalex:" in msgs and "results" in msgs
    with session_scope(e) as sess:
        prog = sess.get(Campaign, cid).state["progress"]
    assert prog["phase"] == "done" and prog["strategies_found"] == 1
    assert prog["papers_done"] == prog["papers_total"] == 1 and prog["searches_done"] >= 1
    assert len(prog["events"]) == len(seen) and prog["events"][-1]["msg"].startswith("Finished:")


def test_progress_log_is_capped(setup):
    from qsd.campaign.runner import MAX_EVENTS
    from qsd.discovery import CampaignBudget

    s, e, runner, ai = setup
    cid = runner.create("x")
    budget = CampaignBudget(s.budgets)
    for n in range(MAX_EVENTS + 25):
        runner._note(cid, budget, "search", f"event {n}")
    with session_scope(e) as sess:
        events = sess.get(Campaign, cid).state["progress"]["events"]
    assert len(events) == MAX_EVENTS and events[-1]["msg"] == f"event {MAX_EVENTS + 24}"


def test_interrupted_campaign_is_marked_stopped(setup):
    s, e, runner, ai = setup
    cid = runner.create("x")
    runner.mark_interrupted(cid)
    with session_scope(e) as sess:
        c = sess.get(Campaign, cid)
        assert c.status is CampaignStatus.STOPPED and "--resume" in c.stop_reason


def test_guided_research_runs_everything_after_one_question(setup):
    from contextlib import contextmanager

    from qsd.research import run_wizard

    s, e, runner, ai = setup
    answers = iter(["", "Find crash-protection ETF trend strategies", "1", "y", "", ""])
    out, opened = [], []

    @contextmanager
    def make_runner(limits):
        runner.limits = limits
        yield runner

    rc = run_wizard(e, s, make_runner, ask=lambda prompt: next(answers), out=out.append,
                    open_browser=opened.append, dashboard=lambda eng, st, port: ("http://127.0.0.1:9/", "test"))
    text = "\n".join(out)
    assert rc == 0 and opened == ["http://127.0.0.1:9/live"]
    assert "Assets:            ETF" in text and "Crash (crisis)" in text and "trend following" in text
    assert "Backtest queue:" in text and "10-month moving average timing" in text
    assert runner.limits.docs_per_round == 1
    with session_scope(e) as sess:
        assert sess.scalars(select(Campaign)).one().status is CampaignStatus.COMPLETED


def test_guided_research_quit_and_decline(setup):
    from qsd.research import run_wizard

    s, e, runner, ai = setup
    answers = iter(["crypto momentum", "", "n", "q"])
    rc = run_wizard(e, s, lambda limits: None, ask=lambda p: next(answers), out=lambda *_: None,
                    open_browser=lambda u: None, dashboard=lambda *a: (None, "x"))
    assert rc == 0
    with session_scope(e) as sess:
        assert sess.scalars(select(Campaign)).first() is None  # declined: nothing started, nothing spent
