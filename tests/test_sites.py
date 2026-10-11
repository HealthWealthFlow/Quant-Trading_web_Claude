"""Automatic website search (D58): feed autodiscovery, sitemap fallback, filtering, polite failure."""

from __future__ import annotations

import httpx
from test_discovery import Clock

from qsd.cli import main
from qsd.config import DEFAULT_CONFIG, SiteSource, load_settings
from qsd.db import init_db, make_engine
from qsd.discovery import BudgetExhausted, CampaignBudget, ConnectorError, build_connectors, run_discovery  # noqa: F401
from qsd.discovery.sites import SiteConnector
from qsd.fetch import PoliteFetcher

HOME = b"""<html><head><link rel="alternate" type="application/rss+xml" href="/blog/rss.xml"></head>
<body>Welcome</body></html>"""

FEED = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Momentum and mean reversion in crypto pairs</title><link>https://blog.example/p1</link>
<description>&lt;p&gt;We test a pairs strategy out of sample.&lt;/p&gt;</description></item>
<item><title>Why I like coffee</title><link>https://blog.example/p2</link>
<description>Morning notes</description></item>
<item><title>Momentum crashes</title><link>https://blog.example/p3</link>
<description>Time-series momentum drawdowns</description></item>
</channel></rss>"""

SITEMAP = b"""<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://research.example/insights/trend-following-in-bonds</loc><lastmod>2026-09-01</lastmod></url>
<url><loc>https://research.example/insights/carry-and-momentum-factors.html</loc></url>
<url><loc>https://research.example/about-us</loc></url></urlset>"""


def fetcher(handler, tmp_path):
    s = load_settings(DEFAULT_CONFIG, tmp_path / "none.yaml", environ={})
    clock = Clock()
    return PoliteFetcher(s, transport=httpx.MockTransport(handler), clock=clock, sleep=clock.sleep), s


def site_handler(seen=None, feed_at="/blog/rss.xml", sitemap=False):
    def handler(req: httpx.Request):
        if seen is not None:
            seen.append(req.url.path)
        p = req.url.path
        if p == "/robots.txt":
            return httpx.Response(200, content=b"User-agent: *\nDisallow: /private\n")
        if req.url.host not in ("blog.example", "research.example"):
            return httpx.Response(404)  # any other site has nothing to offer
        if p == "/" and not sitemap:
            return httpx.Response(200, content=HOME, headers={"content-type": "text/html"})
        if p == feed_at:
            return httpx.Response(200, content=FEED, headers={"content-type": "application/rss+xml"})
        if p == "/sitemap.xml" and sitemap:
            return httpx.Response(200, content=SITEMAP, headers={"content-type": "application/xml"})
        return httpx.Response(404)
    return handler


def test_feed_is_found_from_the_page_and_filtered_by_the_query(tmp_path):
    f, _ = fetcher(site_handler(), tmp_path)
    conn = SiteConnector(f, SiteSource(name="Blog", url="https://blog.example/"))
    found = conn.search("momentum mean reversion pairs", limit=10)
    assert conn.resolved_via == "https://blog.example/blog/rss.xml"
    assert [c.url for c in found] == ["https://blog.example/p1"]  # two or more of the query's words must match
    assert found[0].connector == "site:blog.example" and found[0].work_type == "blog-post"
    assert "pairs strategy out of sample" in found[0].abstract
    assert [c.url for c in conn.search("momentum")] == ["https://blog.example/p1", "https://blog.example/p3"]
    assert conn.search("coffee espresso") == [] and conn.search("trading strategy") == []


def test_conventional_feed_path_is_tried_when_the_page_declares_none(tmp_path):
    f, _ = fetcher(site_handler(feed_at="/feed/"), tmp_path)
    conn = SiteConnector(f, SiteSource(name="Blog", url="https://blog.example/"))
    assert conn.search("momentum")[0].url == "https://blog.example/p1"
    assert conn.resolved_via == "https://blog.example/feed/"


def test_sitemap_is_used_with_a_path_filter_and_titles_come_from_the_slug(tmp_path):
    f, _ = fetcher(site_handler(sitemap=True), tmp_path)
    conn = SiteConnector(f, SiteSource(name="Research", url="https://research.example/", kind="sitemap",
                                       path="/insights/"))
    found = conn.search("momentum carry")
    assert [c.url for c in found] == ["https://research.example/insights/carry-and-momentum-factors.html"]
    assert found[0].title == "carry and momentum factors"
    assert {c.url for c in conn.search("trend following bonds")} == {
        "https://research.example/insights/trend-following-in-bonds"}
    assert all("about-us" not in c.url for c in conn.items())


def test_a_site_without_feed_or_sitemap_fails_once_with_a_helpful_message(tmp_path):
    seen: list[str] = []

    def handler(req):
        seen.append(req.url.path)
        if req.url.path == "/robots.txt":
            return httpx.Response(200, content=b"User-agent: *\nAllow: /\n")
        if req.url.path == "/":
            return httpx.Response(200, content=b"<html>hello</html>", headers={"content-type": "text/html"})
        return httpx.Response(404)

    f, _ = fetcher(handler, tmp_path)
    conn = SiteConnector(f, SiteSource(name="Plain", url="https://plain.example/"))
    for _ in range(3):  # the failure is remembered: no repeated fetching on every query
        try:
            conn.search("momentum")
        except ConnectorError as e:
            assert "Plain" in str(e) and "sources.txt" in str(e)
    assert seen.count("/") == 1


def test_robots_txt_disallowing_the_site_is_respected(tmp_path):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, content=b"User-agent: *\nDisallow: /\n")
        raise AssertionError("must not fetch " + req.url.path)

    f, _ = fetcher(handler, tmp_path)
    conn = SiteConnector(f, SiteSource(name="Closed", url="https://closed.example/"))
    try:
        conn.search("momentum")
        raise AssertionError("expected a ConnectorError")
    except ConnectorError as e:
        assert "Closed" in str(e)


def test_configured_sites_join_default_discovery_but_not_when_a_connector_is_named(tmp_path):
    f, s = fetcher(site_handler(), tmp_path)
    names = [c.name for c in build_connectors(f, s)]
    assert "site:quantocracy.com" in names and "site:www.aqr.com" in names
    assert not any(n.startswith("site:") for n in [c.name for c in build_connectors(f, s, ["arxiv"])])
    assert all(n.startswith("site:") for n in [c.name for c in build_connectors(f, s, ["sites"])])
    s.discovery.sites_enabled = False
    assert not any(c.name.startswith("site:") for c in build_connectors(f, s))


def test_discovery_stores_matching_posts_as_sources_and_failing_sites_do_not_stop_the_rest(tmp_path):
    e = make_engine(tmp_path / "d.sqlite")
    init_db(e)
    f, s = fetcher(site_handler(), tmp_path)
    good = SiteConnector(f, SiteSource(name="Blog", url="https://blog.example/"))
    bad = SiteConnector(f, SiteSource(name="Nowhere", url="https://nowhere.example/"))
    budget = CampaignBudget(s.budgets)
    rep = run_discovery(e, [bad, good], ["momentum mean reversion pairs"], budget)
    assert budget.snapshot()["search_requests"] == 0  # reading a site is not a search request
    assert rep.new_sources == 1 and len(rep.errors) == 1 and "Nowhere" in rep.errors[0]
    again = run_discovery(e, [good], ["momentum mean reversion pairs"], CampaignBudget(s.budgets))
    assert again.queries_skipped_memory == 1  # remembered for the day


def test_sites_command_reports_working_and_failing_sites(tmp_path, monkeypatch, capsys):
    import qsd.cli as cli

    monkeypatch.setattr(cli, "load_settings", lambda *a, **k: _settings_with_sites(tmp_path))
    real = cli.PoliteFetcher

    def make(settings, **kw):
        return real(settings, transport=httpx.MockTransport(site_handler()), clock=Clock(), sleep=Clock().sleep)

    monkeypatch.setattr(cli, "PoliteFetcher", make)
    assert main(["sites", "momentum"]) == 0
    out = capsys.readouterr().out
    assert "[ OK ] Blog: 3 item(s) via https://blog.example/blog/rss.xml; 2 match 'momentum'" in out
    assert "[FAIL] Nowhere:" in out


def _settings_with_sites(tmp_path):
    s = load_settings(DEFAULT_CONFIG, tmp_path / "none.yaml", environ={})
    s.discovery.sites = [SiteSource(name="Blog", url="https://blog.example/"),
                         SiteSource(name="Nowhere", url="https://nowhere.example/")]
    return s


def test_a_short_feed_is_extended_with_older_posts_from_the_sitemap(tmp_path):
    sitemap = (b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               b"<url><loc>https://blog.example/momentum-and-trend-following-in-etfs/</loc></url>"
               b"<url><loc>https://blog.example/p1</loc></url><url><loc>https://blog.example/about</loc></url></urlset>")

    def handler(req: httpx.Request):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, content=b"User-agent: *\nAllow: /\n")
        if req.url.path == "/":
            return httpx.Response(200, content=HOME, headers={"content-type": "text/html"})
        if req.url.path == "/blog/rss.xml":
            return httpx.Response(200, content=FEED, headers={"content-type": "application/rss+xml"})
        if req.url.path == "/sitemap.xml":
            return httpx.Response(200, content=sitemap, headers={"content-type": "application/xml"})
        return httpx.Response(404)

    f, _ = fetcher(handler, tmp_path)
    conn = SiteConnector(f, SiteSource(name="Blog", url="https://blog.example/"))
    urls = [c.url for c in conn.search("momentum trend following", limit=10)]
    assert urls == ["https://blog.example/momentum-and-trend-following-in-etfs/"]  # an older post, from the sitemap
    assert "https://blog.example/p1" not in [c.url for c in conn.items() if c.work_type == "web-page"]  # not twice
    assert "1 archive page(s)" in conn.resolved_via  # one-word addresses ("about") are not archive posts
