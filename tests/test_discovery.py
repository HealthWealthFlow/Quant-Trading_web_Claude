import json

import httpx
import pytest
from sqlalchemy import select

from qsd.config import DEFAULT_CONFIG, load_settings
from qsd.db import init_db, make_engine, session_scope
from qsd.db.models import ErrorRecord, SearchQuery, Source, SourceFact
from qsd.discovery import (
    ArxivConnector,
    BudgetExhausted,
    CampaignBudget,
    CrossrefConnector,
    FeedConnector,
    OpenAlexConnector,
    build_queries,
    expand_query,
    run_discovery,
    tier_for_candidate,
    tier_for_url,
)
from qsd.fetch import PoliteFetcher
from qsd.taxonomy import UNKNOWN, AccessStatus, AssetClass, MarketRegime

ARXIV_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2101.00001v2</id>
    <published>2021-01-01T00:00:00Z</published>
    <title>Time Series
      Momentum in Crypto</title>
    <summary>We study 12-month momentum.</summary>
    <author><name>Ada Lovelace</name></author><author><name>Alan Turing</name></author>
    <arxiv:doi>10.1234/ABC.99</arxiv:doi>
    <link href="http://arxiv.org/abs/2101.00001v2" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/2101.00001v2" rel="related" type="application/pdf"/>
  </entry>
</feed>"""

OPENALEX_JSON = json.dumps({"results": [
    {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1234/abc.99", "display_name": "Time Series Momentum",
     "publication_date": "2021-02-03", "type": "article",
     "authorships": [{"author": {"display_name": "Ada Lovelace"}}],
     "primary_location": {"landing_page_url": "https://journal.example/x", "source": {"display_name": "J. Finance"}},
     "best_oa_location": {"pdf_url": "https://journal.example/x.pdf"},
     "abstract_inverted_index": {"We": [0], "study": [1], "momentum.": [2]}},
    {"id": "https://openalex.org/W2", "doi": None, "display_name": "No DOI Paper", "type": "article",
     "authorships": [], "primary_location": {"landing_page_url": "https://blog.example/p"}},
]}).encode()

CROSSREF_JSON = json.dumps({"message": {"items": [
    {"DOI": "10.5555/CARRY.1", "title": ["Carry <i>Trades</i>"], "author": [{"given": "B.", "family": "Smith"}],
     "issued": {"date-parts": [[2012, 5]]}, "URL": "https://doi.org/10.5555/carry.1",
     "container-title": ["Review of Finance"], "abstract": "<jats:p>FX carry returns.</jats:p>",
     "type": "journal-article"},
]}}).encode()

RSS_XML = b"""<rss version="2.0"><channel><title>Blog</title>
<item><title>Dual momentum update</title><link>https://alphaarchitect.com/dm</link>
<description>&lt;p&gt;Notes&lt;/p&gt;</description><pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate></item>
</channel></rss>"""


class Clock:
    t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def api_fetcher(tmp_path, seen=None):
    def handler(req: httpx.Request):
        if seen is not None:
            seen.append(req)
        if req.url.host == "export.arxiv.org":
            return httpx.Response(200, content=ARXIV_XML, headers={"content-type": "application/atom+xml"})
        if req.url.host == "api.openalex.org":
            return httpx.Response(200, content=OPENALEX_JSON, headers={"content-type": "application/json"})
        if req.url.host == "api.crossref.org":
            return httpx.Response(200, content=CROSSREF_JSON, headers={"content-type": "application/json"})
        if req.url.path == "/robots.txt":
            return httpx.Response(200, content=b"User-agent: *\nDisallow: /private\n")
        if req.url.path == "/feed.xml":
            return httpx.Response(200, content=RSS_XML, headers={"content-type": "application/rss+xml"})
        return httpx.Response(404)

    s = load_settings(DEFAULT_CONFIG, tmp_path / "none.yaml", environ={})
    clock = Clock()
    return PoliteFetcher(s, transport=httpx.MockTransport(handler), clock=clock, sleep=clock.sleep), s


# ---- parsing --------------------------------------------------------------------------------------

def test_arxiv_parse():
    (c,) = ArxivConnector.parse(ARXIV_XML)
    assert c.title == "Time Series Momentum in Crypto"
    assert c.authors == ["Ada Lovelace", "Alan Turing"]
    assert c.ids == {"arxiv": "2101.00001", "doi": "10.1234/abc.99"}
    assert c.pdf_url.endswith("2101.00001v2") and c.publication_date == "2021-01-01"
    assert c.canonical_link == "https://doi.org/10.1234/abc.99"


def test_openalex_parse_reconstructs_abstract_and_keeps_unknowns():
    a, b = OpenAlexConnector.parse(OPENALEX_JSON)
    assert a.abstract == "We study momentum." and a.venue == "J. Finance"
    assert b.publication_date == UNKNOWN and b.authors == [] and b.abstract is None
    assert b.canonical_link == "https://blog.example/p"


def test_crossref_parse_strips_markup():
    (c,) = CrossrefConnector.parse(CROSSREF_JSON)
    assert c.title == "Carry Trades" and c.abstract == "FX carry returns."
    assert c.publication_date == "2012-05" and c.authors == ["B. Smith"]


def test_rss_parse():
    (c,) = FeedConnector.parse(RSS_XML)
    assert c.title == "Dual momentum update" and c.abstract == "Notes"


def test_malicious_xml_entities_not_expanded():
    evil = b"""<?xml version="1.0"?><!DOCTYPE f [<!ENTITY x SYSTEM "file:///etc/passwd">]>
    <rss><channel><item><title>&x;</title><link>https://a.example/</link></item></channel></rss>"""
    (c,) = FeedConnector.parse(evil)
    assert "root:" not in c.title


# ---- queries / tiers / budgets -------------------------------------------------------------------

def test_query_families_regimes_and_expansion():
    qs = build_queries([AssetClass.ETF], [MarketRegime.CRASH])
    assert "dual momentum ETF" in qs and "ETF strategy crash protection" in qs
    assert len(qs) == len({q.lower() for q in qs})
    variants = expand_query("ETF momentum quantitative research")
    assert "ETF relative strength quantitative research" in variants and len(variants) <= 4


def test_tiers():
    assert tier_for_url("https://papers.ssrn.com/sol3/x")[0] == 1
    assert tier_for_url("https://www.quantpedia.com/strategies/x")[0] == 2
    assert tier_for_url("https://old.reddit.com/r/algotrading")[0] == 4
    assert tier_for_url("https://econ.someuniversity.edu/paper.pdf")[0] == 1
    assert tier_for_url("https://unknown-blog.example/post") == (None, "UNKNOWN_DOMAIN")
    assert tier_for_candidate("https://unknown.example/x", "crossref", "journal-article")[0] == 1


def test_budget_stops_before_overspend():
    s = load_settings(DEFAULT_CONFIG, None, environ={"QSD_BUDGETS__MAX_SEARCH_REQUESTS_PER_CAMPAIGN": "2"})
    b = CampaignBudget(s.budgets)
    b.spend("search_requests")
    b.spend("search_requests")
    with pytest.raises(BudgetExhausted):
        b.spend("search_requests")
    assert b.spent["search_requests"] == 2


# ---- end-to-end discovery -------------------------------------------------------------------------

def test_official_api_bypasses_robots_only_for_allowlisted_endpoints(tmp_path):
    fetcher, _ = api_fetcher(tmp_path)
    assert fetcher.fetch("https://example.org/private/x", official_api=True).error == "NOT_AN_OFFICIAL_API_ENDPOINT"
    assert fetcher.fetch("https://example.org/private/x").access_status is AccessStatus.ROBOTS_DISALLOWED


def test_run_discovery_dedupes_across_connectors_and_uses_memory(tmp_path):
    seen = []
    fetcher, s = api_fetcher(tmp_path, seen)
    engine = make_engine(tmp_path / "db.sqlite")
    init_db(engine)
    conns = [ArxivConnector(fetcher), OpenAlexConnector(fetcher, "me@example.com"), CrossrefConnector(fetcher)]
    budget = CampaignBudget(s.budgets)
    r = run_discovery(engine, conns, ["time series momentum"], budget)
    assert r.queries_run == 3 and r.candidates == 4
    assert r.new_sources == 3 and r.existing_sources == 1  # arXiv + OpenAlex share DOI 10.1234/abc.99
    assert any("mailto=me%40example.com" in str(q.url) for q in seen if q.url.host == "api.openalex.org")

    with session_scope(engine) as sess:
        src = sess.scalars(select(Source).where(Source.canonical_url == "https://doi.org/10.1234/abc.99")).one()
        assert src.title == "Time Series Momentum in Crypto"  # first connector's value kept, not overwritten
        assert src.tier == 1 and src.access_status is AccessStatus.NOT_FETCHED
        facts = {f.fact_type: f.value for f in sess.scalars(select(SourceFact).where(SourceFact.source_id == src.id))}
        assert facts["ABSTRACT"] == "We study 12-month momentum." and facts["ID_ARXIV"] == "2101.00001"
        no_doi = sess.scalars(select(Source).where(Source.canonical_url == "https://blog.example/p")).one()
        assert no_doi.publication_date == UNKNOWN and no_doi.author == UNKNOWN
        assert len(sess.scalars(select(SearchQuery)).all()) == 3

    n_api_calls = len([q for q in seen if q.url.host.startswith(("export", "api."))])
    r2 = run_discovery(engine, conns, ["Time-Series  momentum!"], CampaignBudget(s.budgets))
    assert r2.queries_skipped_memory == 3 and r2.queries_run == 0
    assert len([q for q in seen if q.url.host.startswith(("export", "api."))]) == n_api_calls


def test_url_budget_stops_campaign(tmp_path):
    fetcher, _ = api_fetcher(tmp_path)
    s = load_settings(DEFAULT_CONFIG, None, environ={"QSD_BUDGETS__MAX_URLS_PER_CAMPAIGN": "1"})
    engine = make_engine(tmp_path / "db.sqlite")
    init_db(engine)
    r = run_discovery(engine, [OpenAlexConnector(fetcher)], ["momentum"], CampaignBudget(s.budgets))
    assert r.new_sources == 1 and "urls" in r.stopped_reason


def test_connector_failure_is_recorded(tmp_path):
    def down(req):
        return httpx.Response(503)

    s = load_settings(DEFAULT_CONFIG, None, environ={"QSD_CRAWLING__MAX_RETRIES": "0"})
    clock = Clock()
    fetcher = PoliteFetcher(s, transport=httpx.MockTransport(down), clock=clock, sleep=clock.sleep)
    engine = make_engine(tmp_path / "db.sqlite")
    init_db(engine)
    r = run_discovery(engine, [CrossrefConnector(fetcher)], ["carry"], CampaignBudget(s.budgets))
    assert r.errors and r.queries_run == 0
    with session_scope(engine) as sess:
        assert sess.scalars(select(ErrorRecord)).one().stage == "discovery"
