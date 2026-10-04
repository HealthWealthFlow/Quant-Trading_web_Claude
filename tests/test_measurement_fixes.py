"""Measurement fixes after the 2026-10-04 review: two-column quotes, whole-document evidence, handoff visibility.

Each test pins a measured failure of the live system, not a hypothetical one.
"""

from __future__ import annotations

import io

import pdfplumber
from fixtures import make_pdf, make_two_column_pdf
from sqlalchemy import select

from qsd.ai import ground_strategy
from qsd.ai.schemas import ExtractedStrategy
from qsd.ai.sections import grounding_text, select_relevant
from qsd.cli import main
from qsd.config import DEFAULT_CONFIG, load_settings
from qsd.db import init_db, make_engine, new_idea, session_scope
from qsd.db.models import Campaign, Idea, Source, SourceFact
from qsd.handlers import HandlerResult, parse_bytes
from qsd.handlers.base import Location, TextBlock
from qsd.scoring import score_idea
from qsd.scoring.evidence import find_evidence, store_evidence
from qsd.taxonomy import IdeaStatus

S = load_settings(DEFAULT_CONFIG, None, environ={})

# Long lines run past the gutter, so pdfplumber merges characters from both columns into one token — the failure
# measured on the real MM-ARC paper (242–504pt tokens on a 612pt page, 13 of 19 values lost).
LEFT = [f"Left sentence number {i} says the momentum signal uses a twelve month lookback window." for i in range(30)]
RIGHT = [f"Right sentence number {i} explains the exit rule closes the position at month end." for i in range(30)]


def _one_rule(value, quote):
    return ExtractedStrategy.model_validate({"strategy_name": "x", "rules": {"signal": {
        "value": value, "evidence_quote": quote, "location": "p.1"}}})


def test_merged_columns_garble_the_primary_text_but_not_the_layout_reading():
    data = make_two_column_pdf(LEFT, RIGHT)
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        primary = pdf.pages[0].extract_text()
    assert "uses a twelve month lookback window" not in primary  # the failure being fixed
    result = parse_bytes(data, name="paper.pdf")
    assert result.alt_blocks and result.alt_blocks[0].location.page == 1
    assert "Left sentence number 3 says the momentum signal uses a twelve month lookback window." \
        in result.alt_blocks[0].text


def test_grounding_recovers_quotes_only_present_in_the_layout_reading():
    result = parse_bytes(make_two_column_pdf(LEFT, RIGHT), name="paper.pdf")
    sel = select_relevant(result, 40000)
    quote = "signal uses a twelve month lookback window"  # the garbled middle of the merged line
    before = ground_strategy(_one_rule("12-month lookback", quote), sel.text)
    after = ground_strategy(_one_rule("12-month lookback", quote), grounding_text(result, sel))
    assert before.removed and not after.removed
    # and the second reading still does not make up text: an invented quote fails either way
    fake = ground_strategy(_one_rule("6-month lookback", "the momentum signal uses a six month lookback"),
                           grounding_text(result, sel))
    assert fake.removed


def test_layout_reading_is_limited_to_the_pages_the_ai_saw():
    result = HandlerResult(handler="t", handler_version="1", format="pdf", sha256="x", size=1,
                           blocks=[TextBlock("page one text", Location(page=1))],
                           alt_blocks=[TextBlock("seen page", Location(page=1)),
                                       TextBlock("unseen page", Location(page=7))])
    text = grounding_text(result, select_relevant(result, 40000))
    assert "seen page" in text and "unseen page" not in text


def test_single_column_pdfs_get_no_redundant_second_reading():
    result = parse_bytes(make_pdf(["We go long assets with positive 12-month excess return."]), name="p.pdf")
    assert result.alt_blocks == []


PAPER = HandlerResult(handler="t", handler_version="1", format="pdf", sha256="x", size=1, blocks=[
    TextBlock("Smith (2010) reports out-of-sample failures of momentum. Prior work ignores transaction costs.",
              Location(page=2)),
    TextBlock("We test the rule out-of-sample from 2010 to 2020 and deduct transaction costs of 5bp. Our results "
              "hold across 23 countries. Our code is available at github.com/author/repo.", Location(page=9)),
    TextBlock("References\nJones (2004) walk-forward analysis across countries.", Location(page=20)),
])


def test_evidence_counts_only_the_papers_own_work_and_skips_references():
    found = find_evidence(PAPER)
    assert set(found) == {"out_of_sample", "costs_considered", "sample_period", "cross_market", "code_available"}
    assert all(where == "p.9" for hits in found.values() for _s, where in hits)  # not the literature review (p.2)
    assert not any("Jones" in s for hits in found.values() for s, _w in hits)  # nor the reference list


def test_evidence_in_the_body_raises_robustness_with_quotes_shown(tmp_path):
    e = make_engine(tmp_path / "db.sqlite")
    init_db(e)
    with session_scope(e) as s:
        src = Source(title="Paper", url="https://p.example/x", canonical_url="https://p.example/x", tier=1,
                     content_hash="h")
        s.add(src)
        s.flush()
        sid = src.id
        s.add(new_idea(primary_source_id=sid, strategy_name="TSMOM", instrument="SPY", asset_classes=["ETF"],
                       signal="12-month return > 0", entry_rule="buy when 12-month return > 0",
                       exit_rule="sell when 12-month return < 0"))
    score_idea(e, S, 1)
    with session_scope(e) as s:
        before = s.get(Idea, 1).score_details["idea_quality"]["components"]["expected_robustness"]["value"]
        store_evidence(s, sid, PAPER)
    score_idea(e, S, 1)
    with session_scope(e) as s:
        details = s.get(Idea, 1).score_details
        after = details["idea_quality"]["components"]["expected_robustness"]["value"]
        stored = s.scalars(select(SourceFact).where(SourceFact.fact_type.like("EVIDENCE:%"))).all()
    assert before == 0 and after > 0
    assert {q["signal"] for q in details["evidence_sentences"]} >= {"out_of_sample", "cross_market"}
    assert all(f.page == 9 for f in stored)
    with session_scope(e) as s:  # idempotent: re-reading the document replaces, never duplicates
        store_evidence(s, sid, PAPER)
        assert len(s.scalars(select(SourceFact).where(SourceFact.fact_type.like("EVIDENCE:%"))).all()) == len(stored)


def test_queue_why_reports_blocking_reasons(tmp_path, capsys):
    db = str(tmp_path / "q.sqlite")
    e = make_engine(db)
    init_db(e)
    with session_scope(e) as s:
        s.add(new_idea(strategy_name="Vague idea", status=IdeaStatus.PROMISING))
    assert main(["queue", "--why", "--db", db]) == 0
    out = capsys.readouterr().out
    assert "blocked:" in out and "instrument unknown" in out and "0 of 1 idea(s) eligible" in out
    assert "Blocking reasons (ideas affected):" in out


def test_harvest_submits_eligible_ideas(tmp_path, monkeypatch):
    from qsd.campaign import harvest

    e = make_engine(tmp_path / "h.sqlite")
    init_db(e)
    with session_scope(e) as s:
        s.add(Campaign(request_text="x"))
        s.flush()
        s.add_all([Idea(campaign_id=1, strategy_name="a", status=IdeaStatus.PROMISING, search_depth_level=0),
                   Idea(campaign_id=1, strategy_name="b", status=IdeaStatus.RESEARCHING, search_depth_level=0)])
    calls = []
    monkeypatch.setattr("qsd.packaging.submit_to_queue",
                        lambda engine, settings, i: (calls.append(i) or True, None, []))
    assert harvest.submit_eligible(e, S, 1) == [1] and calls == [1]  # only PROMISING ideas are offered


# Hand-checked sentences from the live database (2026-10-04, `evidence.txt`): 24 of 30 were right, the misses were
# negations, future work and a company name. Each line is (sentence, signals it must yield).
LIVE = [
    ("We then applied the bid-ask model developed by DiLellio and Stanley (2011) based on the three- month moving "
     "average volume, producing a bid-ask spread cost of 0.10% and 0.08%.", {"costs_considered"}),
    ("Consequently, the updated simulation results include the small and midcap results in both 10-year time periods "
     "(1991–2000 and 2001–2010), but our Nasdaq funds could only support simulated results for 2001–2010.",
     {"sample_period"}),
    ("We select the best threshold for our out-of-sample test.", {"out_of_sample"}),
    ("In particular, we examine the impacts of transaction costs and regime-switching timings on the VIX futures "
     "trading strategies.", {"costs_considered"}),
    ("Our approach integrates surface-informed decisions with multiple hedging instruments and explicitly accounts "
     "for transaction costs.", {"costs_considered"}),
    ("Tested on a historical out- of-sample set of straddles from 2020 to 2023, our method consistently outperforms "
     "traditional delta-gamma hedging strategies.", {"out_of_sample", "sample_period"}),
    # wrong before, must yield nothing now
    ("Although many other stock index funds could be considered, such as international developed and emerging "
     "markets, we chose these because of their wide familiarity to individual investors.", set()),
    ("Future research might take this work forward by including more sentiment indicators, modelling transaction "
     "costs, and doing multi-asset portfolio optimization.", set()),
    ("Transaction cost: we assume no transaction cost or taxes exists in this portfolio selection model.", set()),
    ("Similar to previous studies, we avoid incorporating transaction cost in the original formulations.", set()),
    ("3.1 CCI vs HCP We construct a portfolio by holding $1 Crown Castle International Corp.", set()),
]


def test_evidence_precision_on_hand_checked_live_sentences():
    for sentence, expected in LIVE:
        doc = HandlerResult(handler="t", handler_version="1", format="pdf", sha256="x", size=1,
                            blocks=[TextBlock(sentence, Location(page=1))])
        assert set(find_evidence(doc)) == expected, sentence
