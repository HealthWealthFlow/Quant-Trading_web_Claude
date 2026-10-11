"""Web search as a discovery channel: blogs, broker research and Substacks (the channel papers cannot reach).

Measured need: academic sources rarely describe the retail technical-analysis setups the objective asks for, and
many such papers are paywalled. These tests pin the properties that matter: metadata only, POST without paid
retries, and no credential ever placed in a URL.
"""

from __future__ import annotations

import json

import httpx
from test_campaign import Clock

from qsd.config import DEFAULT_CONFIG, load_settings
from qsd.discovery import build_connectors
from qsd.discovery.connectors import ConnectorError, WebSearchConnector
from qsd.fetch import PoliteFetcher
from qsd.fetch.client import is_official_api

KEY = "fc-" + "k" * 30

RESULTS = [
    {
        "url": "https://blog.example.com/opening-range-breakout",
        "title": "Opening range breakout on US equities",
        "description": "A rules-based opening range breakout with an ATR stop, tested after costs.",
        "date": "2024-03-11",
    },
    {"title": "no url here", "description": "must be skipped"},
]


def web_site(seen):
    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.host == "api.firecrawl.dev" and req.url.path == "/v1/search":
            return httpx.Response(200, json={"success": True, "data": RESULTS})
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(404)
    return handler


def settings(**env):
    return load_settings(DEFAULT_CONFIG, None, environ={"QSD_AI__DEFAULT_PROVIDER": "fake", **env})


def fetcher_for(s, seen, tmp_path=None):
    clock = Clock()
    return PoliteFetcher(s, cache_dir=(tmp_path / "cache") if tmp_path else None,
                         transport=httpx.MockTransport(web_site(seen)), clock=clock, sleep=clock.sleep)


def test_search_results_become_candidates(tmp_path):
    seen = []
    w = WebSearchConnector(fetcher_for(settings(), seen, tmp_path), api_key=KEY)
    [c] = w.search("opening range breakout", limit=5)  # the url-less row is skipped
    assert c.connector == "web" and c.work_type == "web-page"
    assert c.title == "Opening range breakout on US equities"
    assert c.url == "https://blog.example.com/opening-range-breakout"
    assert c.publication_date == "2024-03-11"
    assert "ATR stop" in (c.abstract or "")


def test_the_request_is_a_post_and_carries_the_query(tmp_path):
    """Firecrawl's search endpoint takes a JSON body, so the fetcher must POST it."""
    seen = []
    w = WebSearchConnector(fetcher_for(settings(), seen, tmp_path), api_key=KEY)
    w.search("mean reversion pairs", limit=3)
    req = next(r for r in seen if r.url.path == "/v1/search")
    assert req.method == "POST"
    body = json.loads(req.content)
    assert body["query"] == "mean reversion pairs" and body["limit"] == 3


def test_the_key_is_a_header_and_never_in_the_url(tmp_path):
    """URLs are logged and cached, so a credential must never appear in one."""
    seen = []
    w = WebSearchConnector(fetcher_for(settings(), seen, tmp_path), api_key=KEY)
    w.search("momentum", limit=2)
    req = next(r for r in seen if r.url.path == "/v1/search")
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in str(req.url)


def test_a_billed_search_is_never_retried(tmp_path):
    """A retry could be charged twice for the same query, so a failing POST gets exactly one attempt."""
    calls = []

    def handler(req: httpx.Request):
        if req.url.path == "/v1/search":
            calls.append(req)
            return httpx.Response(500)
        return httpx.Response(404)

    s = settings()
    clock = Clock()
    f = PoliteFetcher(s, transport=httpx.MockTransport(handler), clock=clock, sleep=clock.sleep)
    w = WebSearchConnector(f, api_key=KEY)
    try:
        w.search("momentum", limit=2)
    except ConnectorError:
        pass
    assert len(calls) == 1, f"expected a single attempt, saw {len(calls)}"


def test_a_missing_key_is_a_typed_error_naming_the_variable(tmp_path):
    w = WebSearchConnector(fetcher_for(settings(), [], tmp_path), api_key=None)
    try:
        w.search("momentum", limit=2)
    except ConnectorError as e:
        assert WebSearchConnector.KEY_ENV in str(e)
    else:
        raise AssertionError("a search without a key must fail loudly, not silently find nothing")


def test_the_search_host_is_an_official_endpoint():
    """Without this the polite fetcher refuses the call as a non-official API."""
    assert is_official_api("https://api.firecrawl.dev/v1/search")


def test_the_connector_joins_only_when_a_key_is_present(tmp_path, monkeypatch):
    """Secrets come from the environment (`get_secret` reads os.environ), so that is what the test sets."""
    clock = Clock()
    s = settings()
    monkeypatch.delenv(WebSearchConnector.KEY_ENV, raising=False)
    without = build_connectors(
        PoliteFetcher(s, transport=httpx.MockTransport(web_site([])), clock=clock, sleep=clock.sleep), s)
    assert "web" not in [c.name for c in without]

    monkeypatch.setenv(WebSearchConnector.KEY_ENV, KEY)
    with_key = build_connectors(
        PoliteFetcher(s, transport=httpx.MockTransport(web_site([])), clock=clock, sleep=clock.sleep), s)
    assert "web" in [c.name for c in with_key]


def test_asking_for_web_without_a_key_raises_rather_than_skipping_silently(tmp_path):
    s = settings()
    clock = Clock()
    f = PoliteFetcher(s, transport=httpx.MockTransport(web_site([])), clock=clock, sleep=clock.sleep)
    try:
        build_connectors(f, s, names=["web"])
    except ConnectorError as e:
        assert WebSearchConnector.KEY_ENV in str(e)
    else:
        raise AssertionError("an explicitly requested channel must not be dropped without a word")
