import httpx
import pytest
from fixtures import make_pdf
from sqlalchemy import select

from qsd.config import DEFAULT_CONFIG, load_settings
from qsd.db import init_db, make_engine, session_scope
from qsd.db.models import ErrorRecord, FetchLog, Source
from qsd.fetch import (
    InvalidURLError,
    PoliteFetcher,
    canonicalize_url,
    classify_request,
    detect_access_barrier,
    fetch_and_store,
    redact_headers,
)
from qsd.fetch.ratelimit import DomainLimiter, parse_retry_after
from qsd.taxonomy import AccessStatus, RequestClass

ROBOTS_OK = b"User-agent: *\nDisallow: /private/\n"


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.slept: list[float] = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def make_fetcher(handler, tmp_path=None, **kw):
    settings = load_settings(DEFAULT_CONFIG, tmp_path / "none.yaml" if tmp_path else None, environ={})
    clock = FakeClock()
    f = PoliteFetcher(settings, cache_dir=(tmp_path / "cache") if tmp_path else None,
                      transport=httpx.MockTransport(handler), clock=clock, sleep=clock.sleep, **kw)
    return f, clock


def site(routes, seen=None):
    """routes: path -> Response or callable(request) -> Response."""
    def handler(request: httpx.Request):
        if seen is not None:
            seen.append(request)
        r = routes.get(request.url.path)
        if r is None:
            if request.url.path == "/robots.txt":
                return httpx.Response(200, content=ROBOTS_OK)
            return httpx.Response(404)
        return r(request) if callable(r) else r
    return handler


HTML = b"<html><head><title>Momentum</title></head><body><p>Buy winners, sell losers.</p></body></html>"


# ---- URLs ----------------------------------------------------------------------------------------

def test_canonicalize_url():
    assert canonicalize_url("HTTPS://Example.COM:443/a/b?utm_source=x&b=2&a=1#frag") == \
        "https://example.com/a/b?a=1&b=2"
    assert canonicalize_url("http://example.com") == "http://example.com/"
    for bad in ("ftp://x.com/f", "javascript:alert(1)", "https://user:pw@x.com/", "https:///nohost"):
        with pytest.raises(InvalidURLError):
            canonicalize_url(bad)


# ---- policy ----------------------------------------------------------------------------------------

def test_redact_headers():
    out = redact_headers({"Authorization": "Bearer x", "Cookie": "a=b", "X-Api-Key": "k", "X-Session-Id": "s",
                          "Accept": "text/html"})
    assert out["Accept"] == "text/html"
    assert all(out[k] == "[REDACTED]" for k in ("Authorization", "Cookie", "X-Api-Key", "X-Session-Id"))


def test_classify_request():
    assert classify_request("https://x.com/a", {"Authorization": "t"}) is RequestClass.SENSITIVE_AUTH
    assert classify_request("https://x.com/a", {"Cookie": "s=1"}) is RequestClass.SESSION_PRIVATE
    assert classify_request("https://x.com/api/v1/items", None, "application/json") is RequestClass.PUBLIC_API
    assert classify_request("https://x.com/p.pdf", None, "application/pdf") is RequestClass.PUBLIC_STATIC


def test_detect_access_barriers():
    u = "https://x.com/paper"
    assert detect_access_barrier(401, u, u) is AccessStatus.ACCESS_RESTRICTED
    assert detect_access_barrier(402, u, u) is AccessStatus.PAYWALLED
    assert detect_access_barrier(200, u, u, '<div class="g-recaptcha">') is AccessStatus.MANUAL_ACCESS_REQUIRED
    assert detect_access_barrier(200, u, "https://x.com/login?next=/paper") is AccessStatus.ACCESS_RESTRICTED
    assert detect_access_barrier(200, u, u, "Subscribe to continue reading") is AccessStatus.PAYWALLED
    assert detect_access_barrier(200, u, u, "<p>An open article about carry.</p>") is None


def test_retry_after_parsing_and_backoff_bounds():
    assert parse_retry_after("30") == 30
    assert parse_retry_after(None) is None and parse_retry_after("soon") is None
    lim = DomainLimiter(requests_per_minute=60)
    assert all(0.5 <= lim.backoff_delay(a) <= 2 * 2 ** a + 0.5 for a in range(4))


# ---- fetcher ---------------------------------------------------------------------------------------

def test_fetch_ok_no_credentials_sent_and_spacing(tmp_path):
    seen = []
    f, clock = make_fetcher(site({"/a": httpx.Response(200, content=HTML, headers={"content-type": "text/html",
                                                                                   "set-cookie": "sid=1"}),
                                  "/b": httpx.Response(200, content=HTML, headers={"content-type": "text/html"})},
                                 seen), tmp_path)
    r1, r2 = f.fetch("https://example.org/a"), f.fetch("https://example.org/b")
    assert r1.ok and r2.ok and r1.request_class is RequestClass.PUBLIC_STATIC
    for req in seen:
        assert "authorization" not in req.headers and "cookie" not in req.headers  # cookies never replayed
        assert req.headers["user-agent"].startswith("QSD-Research-Bot")
    assert clock.slept and all(s > 0 for s in clock.slept)  # requests to one host are spaced


def test_robots_disallow_and_redirect_target_checked(tmp_path):
    routes = {"/private/x": httpx.Response(200, content=HTML),
              "/go": httpx.Response(302, headers={"location": "https://example.org/private/y"})}
    seen = []
    f, _ = make_fetcher(site(routes, seen), tmp_path)
    assert f.fetch("https://example.org/private/x").access_status is AccessStatus.ROBOTS_DISALLOWED
    assert all(r.url.path != "/private/x" for r in seen)  # never requested
    assert f.fetch("https://example.org/go").access_status is AccessStatus.ROBOTS_DISALLOWED


def test_robots_unreachable_means_disallow(tmp_path):
    f, _ = make_fetcher(site({"/robots.txt": httpx.Response(503), "/a": httpx.Response(200, content=HTML)}), tmp_path)
    assert f.fetch("https://down.example/a").access_status is AccessStatus.ROBOTS_DISALLOWED


def test_missing_robots_means_allow(tmp_path):
    f, _ = make_fetcher(site({"/robots.txt": httpx.Response(404), "/a": httpx.Response(200, content=HTML)}), tmp_path)
    assert f.fetch("https://open.example/a").ok


def test_429_retry_after_honoured_then_success(tmp_path):
    calls = {"n": 0}

    def flaky(_req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "7"})
        return httpx.Response(200, content=HTML)

    f, clock = make_fetcher(site({"/a": flaky}), tmp_path)
    r = f.fetch("https://example.org/a")
    assert r.ok and r.attempts == 2
    assert any(s >= 7 for s in clock.slept)


def test_huge_retry_after_gives_up(tmp_path):
    f, _ = make_fetcher(site({"/a": httpx.Response(429, headers={"retry-after": "86400"})}), tmp_path)
    r = f.fetch("https://example.org/a")
    assert not r.ok and r.error.startswith("RATE_LIMITED") and r.attempts == 1


def test_server_errors_retry_with_backoff_then_give_up(tmp_path):
    f, clock = make_fetcher(site({"/a": httpx.Response(503)}), tmp_path)
    r = f.fetch("https://example.org/a")
    assert not r.ok and r.attempts == 4 and "HTTP_503" in r.error
    assert len([s for s in clock.slept if s >= 0.5]) >= 3


def test_host_cooldown_after_repeated_errors(tmp_path):
    f, _ = make_fetcher(site({"/a": httpx.Response(500)}), tmp_path)
    f.fetch("https://example.org/a")
    f.fetch("https://example.org/a")
    r = f.fetch("https://example.org/a")
    assert r.error.startswith("HOST_COOLDOWN")


def test_oversize_body_rejected(tmp_path):
    big = b"x" * (2 * 1024 * 1024)
    settings_env = {"QSD_CRAWLING__MAX_DOWNLOAD_MB": "1"}
    s = load_settings(DEFAULT_CONFIG, tmp_path / "none.yaml", environ=settings_env)
    clock = FakeClock()
    f = PoliteFetcher(s, transport=httpx.MockTransport(site({"/big": httpx.Response(200, content=big)})),
                      clock=clock, sleep=clock.sleep)
    r = f.fetch("https://example.org/big")
    assert not r.ok and r.error.startswith("TOO_LARGE") and r.content is None


def test_captcha_and_login_bodies_discarded(tmp_path):
    routes = {"/c": httpx.Response(200, content=b'<div class="cf-turnstile">verify you are human</div>'),
              "/p": httpx.Response(302, headers={"location": "https://example.org/signin?next=/p"}),
              "/signin": httpx.Response(200, content=b"<form>login</form>")}
    f, _ = make_fetcher(site(routes), tmp_path)
    c = f.fetch("https://example.org/c")
    assert c.access_status is AccessStatus.MANUAL_ACCESS_REQUIRED and c.content is None
    p = f.fetch("https://example.org/p")
    assert p.access_status is AccessStatus.ACCESS_RESTRICTED and p.content is None


def test_paywall_keeps_only_public_part(tmp_path):
    body = b"<html><body><p>Abstract: momentum crashes.</p><p>Subscribe to continue reading</p></body></html>"
    f, _ = make_fetcher(site({"/art": httpx.Response(200, content=body)}), tmp_path)
    r = f.fetch("https://news.example/art")
    assert r.access_status is AccessStatus.PAYWALLED and r.ok


def test_etag_cache_revalidation(tmp_path):
    def page(req):
        if req.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=HTML, headers={"etag": '"v1"', "content-type": "text/html"})

    f, _ = make_fetcher(site({"/a": page}), tmp_path)
    assert not f.fetch("https://example.org/a").from_cache
    again = f.fetch("https://example.org/a")
    assert again.from_cache and again.content == HTML


def test_domain_budget(tmp_path):
    f, _ = make_fetcher(site({"/a": httpx.Response(200, content=HTML)}), tmp_path, max_requests_per_host=1)
    assert f.fetch("https://example.org/a").ok
    assert f.fetch("https://example.org/a?x=1").error == "DOMAIN_BUDGET_EXHAUSTED"


# ---- pipeline --------------------------------------------------------------------------------------

def test_fetch_and_store_records_source_log_and_errors(tmp_path):
    routes = {"/paper.pdf": httpx.Response(200, content=make_pdf(["Carry trade study"]),
                                           headers={"content-type": "application/pdf"}),
              "/gone": httpx.Response(404)}
    f, _ = make_fetcher(site(routes), tmp_path)
    engine = make_engine(tmp_path / "db.sqlite")
    init_db(engine)

    sid, resp, result = fetch_and_store("https://example.org/paper.pdf?utm_source=tw", f, engine)
    assert resp.ok and result and result.handler == "PDFHandler"
    fetch_and_store("https://example.org/gone", f, engine)
    fetch_and_store("not a url", f, engine)

    with session_scope(engine) as s:
        src = s.get(Source, sid)
        assert src.canonical_url == "https://example.org/paper.pdf"
        assert src.access_status is AccessStatus.OK and src.format == "pdf" and src.content_hash
        assert src.title == "Momentum Study"
        logs = s.scalars(select(FetchLog)).all()
        assert len(logs) == 3
        errors = s.scalars(select(ErrorRecord)).all()
        assert {e.stage for e in errors} == {"fetch"} and len(errors) == 2  # 404 + invalid URL: never silent
        cols = {c.name for c in FetchLog.__table__.columns}
        assert not cols & {"headers", "request_headers", "body", "cookies"}


def test_trickling_download_is_cut_off(tmp_path):
    clock_ref = {}

    def trickle():
        for _ in range(1000):
            clock_ref["c"].t += 10  # a byte every 10 s: never trips the per-read timeout
            yield b"x"

    f, clock = make_fetcher(site({"/slow.pdf": lambda req: httpx.Response(200, content=trickle())}), tmp_path)
    clock_ref["c"] = clock
    r = f.fetch("https://example.org/slow.pdf")
    assert not r.ok and r.error.startswith("DOWNLOAD_TIMEOUT") and r.attempts == 1


def test_saved_copy_reread_without_network(tmp_path):
    from qsd.cli import _load_document

    pdf = make_pdf(["Carry trade study"])
    f, _ = make_fetcher(site({"/paper.pdf": httpx.Response(200, content=pdf,
                                                           headers={"content-type": "application/pdf"})}), tmp_path)
    engine = make_engine(tmp_path / "db.sqlite")
    init_db(engine)
    sid, _, result = fetch_and_store("https://example.org/paper.pdf", f, engine)

    def offline(request):
        raise AssertionError(f"network used: {request.url}")

    f2, _ = make_fetcher(offline, tmp_path)  # same cache folder, no network allowed
    again, problem = _load_document(engine, None, sid, f2)
    assert problem is None and again.sha256 == result.sha256
