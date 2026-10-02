"""GitHub strategy repositories as a discovery channel (spec §11: code, with licence tracking)."""

from __future__ import annotations

import httpx
import pytest
from test_campaign import Clock

from qsd.config import DEFAULT_CONFIG, load_settings
from qsd.discovery import build_connectors
from qsd.discovery.connectors import ConnectorError, GitHubConnector
from qsd.fetch import PoliteFetcher
from qsd.fetch.client import is_official_api

TOKEN = "ghp_" + "x" * 30

REPO = {
    "full_name": "quantdev/time-series-momentum",
    "html_url": "https://github.com/quantdev/time-series-momentum",
    "description": "Time series momentum backtest on futures",
    "language": "Python",
    "stargazers_count": 412,
    "forks_count": 88,
    "fork": False,
    "created_at": "2021-03-04T10:00:00Z",
    "pushed_at": "2024-06-11T08:30:00Z",
    "topics": ["trading", "momentum", "backtest"],
    "license": {"spdx_id": "MIT"},
    "owner": {"login": "quantdev"},
}


def github_site(seen):
    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.host == "api.github.com" and req.url.path == "/search/repositories":
            return httpx.Response(200, json={"total_count": 1, "items": [REPO]})
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(404)
    return handler


def settings(**env):
    return load_settings(DEFAULT_CONFIG, None, environ={"QSD_AI__DEFAULT_PROVIDER": "fake", **env})


def fetcher_for(s, seen, tmp_path=None):
    clock = Clock()
    return PoliteFetcher(s, cache_dir=(tmp_path / "cache") if tmp_path else None,
                         transport=httpx.MockTransport(github_site(seen)), clock=clock, sleep=clock.sleep)


def test_github_repo_metadata_becomes_a_candidate(tmp_path):
    seen = []
    gh = GitHubConnector(fetcher_for(settings(), seen, tmp_path))
    [c] = gh.search("time series momentum", limit=5)
    assert c.connector == "github" and c.work_type == "repository"
    assert c.title == "quantdev/time-series-momentum"
    assert c.authors == ["quantdev"] and c.venue == "GitHub"
    assert c.url == "https://github.com/quantdev/time-series-momentum"
    assert c.publication_date == "2024-06-11"  # last push, not creation
    assert c.ids == {"github": "quantdev/time-series-momentum"}
    assert c.canonical_link == "https://github.com/quantdev/time-series-momentum"


def test_licence_and_signals_are_carried_into_the_abstract(tmp_path):
    """licence tracking (spec §11): the terms must be visible before anything is reused."""
    gh = GitHubConnector(fetcher_for(settings(), [], tmp_path))
    [c] = gh.search("momentum", limit=5)
    assert "licence: MIT" in c.abstract and "stars: 412" in c.abstract
    assert "language: Python" in c.abstract and "topics: trading, momentum, backtest" in c.abstract


def test_nonstandard_licence_is_not_reported_as_a_licence(tmp_path):
    repo = {**REPO, "license": {"spdx_id": "NOASSERTION"}}
    def handler(req: httpx.Request):
        return httpx.Response(200, json={"items": [repo]})
    s = settings()
    clock = Clock()
    f = PoliteFetcher(s, transport=httpx.MockTransport(handler), clock=clock, sleep=clock.sleep)
    [c] = GitHubConnector(f).search("momentum")
    assert "licence:" not in c.abstract  # NOASSERTION means "unclear", not a licence name


def test_query_is_scoped_to_strategy_vocabulary_and_excludes_forks(tmp_path):
    seen = []
    gh = GitHubConnector(fetcher_for(settings(), seen, tmp_path))
    gh.search("breakout", limit=5)
    url = [r for r in seen if r.url.path == "/search/repositories"][0].url
    q = url.params["q"]
    assert "trading strategy" in q and "in:name,description,readme" in q and "fork:false" in q
    assert url.params["sort"] == "stars"


def test_forks_can_be_included_and_stars_filtered(tmp_path):
    seen = []
    gh = GitHubConnector(fetcher_for(settings(), seen, tmp_path), include_forks=True, min_stars=50)
    gh.search("momentum", limit=5)
    q = [r for r in seen if r.url.path == "/search/repositories"][0].url.params["q"]
    assert "fork:false" not in q and "stars:>=50" in q


def test_limit_is_capped_to_the_api_maximum(tmp_path):
    seen = []
    GitHubConnector(fetcher_for(settings(), seen, tmp_path)).search("x", limit=500)
    assert [r for r in seen if r.url.path == "/search/repositories"][0].url.params["per_page"] == "30"


def test_token_is_sent_as_a_header_and_never_in_the_url(tmp_path):
    seen, s = [], settings()
    gh = GitHubConnector(fetcher_for(s, seen, tmp_path), api_key=TOKEN)
    gh.search("momentum", limit=5)
    api = [r for r in seen if r.url.host == "api.github.com"]
    assert api and all(r.headers["authorization"] == f"Bearer {TOKEN}" for r in api)
    assert all(TOKEN not in str(r.url) for r in api)
    assert all(r.headers["accept"] == "application/vnd.github+json" for r in api)


def test_credentialed_responses_are_not_cached(tmp_path):
    seen, s = [], settings()
    gh = GitHubConnector(fetcher_for(s, seen, tmp_path), api_key=TOKEN)
    gh.search("momentum", limit=5)
    cache = tmp_path / "cache"
    assert not cache.exists() or not list(cache.glob("*.bin"))


def test_github_api_host_is_an_official_endpoint():
    assert is_official_api("https://api.github.com/search/repositories?q=x")
    assert is_official_api("https://api.github.com/repos/owner/name")
    assert not is_official_api("https://api.github.com/users/owner")  # not on the allowlist
    assert not is_official_api("https://github.com/owner/name")       # the site itself is crawled normally


def test_api_failure_is_a_typed_error(tmp_path):
    s = settings()
    clock = Clock()
    f = PoliteFetcher(s, transport=httpx.MockTransport(lambda r: httpx.Response(403, json={"message": "rate limited"})),
                      clock=clock, sleep=clock.sleep)
    with pytest.raises(ConnectorError):
        GitHubConnector(f).search("momentum")


def test_non_json_response_is_a_typed_error(tmp_path):
    s = settings()
    clock = Clock()
    f = PoliteFetcher(s, transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"<html>nope</html>")),
                      clock=clock, sleep=clock.sleep)
    with pytest.raises(ConnectorError):
        GitHubConnector(f).search("momentum")


def test_repo_without_a_name_is_skipped(tmp_path):
    def handler(req: httpx.Request):
        return httpx.Response(200, json={"items": [{"full_name": "", "html_url": "x"}, REPO]})
    s = settings()
    clock = Clock()
    f = PoliteFetcher(s, transport=httpx.MockTransport(handler), clock=clock, sleep=clock.sleep)
    out = GitHubConnector(f).search("momentum")
    assert [c.title for c in out] == ["quantdev/time-series-momentum"]


def test_connector_joins_only_when_enabled(tmp_path):
    """Unmatched, GitHub search is 60 requests/hour, so it must not join silently."""
    s, seen = settings(), []
    f = fetcher_for(s, seen, tmp_path)
    assert "github" not in [c.name for c in build_connectors(f, s)]
    s.discovery.github_enabled = True
    assert "github" in [c.name for c in build_connectors(f, s)]
    # explicitly asking for it while disabled must fail loudly, not silently return nothing
    with pytest.raises(ConnectorError):
        build_connectors(f, settings(), names=["github"])
