"""Polite HTTP fetcher (spec §11, §102–§107).

Guarantees:
- robots.txt is checked for every URL, including redirect targets (RFC 9309 semantics); cannot be disabled.
- One request at a time per host, spaced by the configured rate (and any robots.txt Crawl-delay).
- 429 / 5xx / network errors: exponential backoff with jitter, Retry-After honoured; repeated failures cool the host
  down; an over-long Retry-After ends the attempt instead of hammering.
- No credentials are ever sent and no cookies are kept between requests.
- Bodies are size-capped while streaming; login walls / CAPTCHAs discard the body; paywalled pages keep only what
  was publicly delivered (spec §106).
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.robotparser
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ..config import Settings
from ..logging_setup import redact
from ..taxonomy import AccessStatus, RequestClass
from .policy import classify_request, detect_access_barrier
from .ratelimit import MAX_RETRY_AFTER_SECONDS, DomainLimiter, parse_retry_after
from .urls import InvalidURLError, canonicalize_url, host_of

RETRY_STATUSES = {429, 500, 502, 503, 504}

# Official, documented APIs (spec §11 priority 1). robots.txt governs crawlers; these endpoints are used under their
# published API terms and rate limits instead. Defined in code (not config) so it cannot be widened by settings.
OFFICIAL_API_ENDPOINTS = {
    ("export.arxiv.org", "/api/query"),
    ("api.openalex.org", "/works"),
    ("api.crossref.org", "/works"),
}


def is_official_api(url: str) -> bool:
    parts = httpx.URL(url)
    return (parts.host, parts.path) in OFFICIAL_API_ENDPOINTS
MAX_REDIRECTS = 5


@dataclass
class FetchResponse:
    requested_url: str
    url: str  # canonical
    final_url: str | None = None
    status_code: int | None = None
    content_type: str | None = None
    content: bytes | None = None
    access_status: AccessStatus = AccessStatus.NOT_FETCHED
    request_class: RequestClass = RequestClass.UNKNOWN
    error: str | None = None
    from_cache: bool = False
    invalid_url: bool = False
    attempts: int = 0
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def ok(self) -> bool:
        return self.content is not None and self.access_status in (AccessStatus.OK, AccessStatus.PAYWALLED)


class HttpCache:
    """Tiny on-disk cache with ETag / Last-Modified revalidation. Only openly accessible 200 responses are stored."""

    def __init__(self, directory: Path):
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)

    def _key(self, url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()

    def get(self, url: str) -> tuple[dict, bytes] | None:
        k = self._key(url)
        meta, body = self.dir / f"{k}.json", self.dir / f"{k}.bin"
        if not (meta.exists() and body.exists()):
            return None
        return json.loads(meta.read_text(encoding="utf-8")), body.read_bytes()

    def put(self, url: str, meta: dict, body: bytes) -> None:
        k = self._key(url)
        (self.dir / f"{k}.bin").write_bytes(body)
        (self.dir / f"{k}.json").write_text(json.dumps(meta), encoding="utf-8")


class RobotsCache:
    """robots.txt per origin. RFC 9309: 4xx → allow all; 5xx / network failure → disallow all (retry later)."""

    TTL_SECONDS = 24 * 3600

    def __init__(self, fetch_raw: Callable[[str], tuple[int | None, bytes]], user_agent: str,
                 clock: Callable[[], float] = time.monotonic):
        self._fetch_raw = fetch_raw
        self._ua = user_agent
        self._clock = clock
        self._cache: dict[str, tuple[float, urllib.robotparser.RobotFileParser | bool]] = {}

    def _origin(self, url: str) -> str:
        parts = httpx.URL(url)
        port = f":{parts.port}" if parts.port else ""
        return f"{parts.scheme}://{parts.host}{port}"

    def _load(self, origin: str) -> urllib.robotparser.RobotFileParser | bool:
        status, body = self._fetch_raw(f"{origin}/robots.txt")
        if status is None or status >= 500:
            return False  # unreachable: be conservative
        if 400 <= status < 500:
            return True  # no robots.txt: allowed
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(body.decode("utf-8", errors="replace").splitlines())
        return rp

    def _get(self, url: str) -> urllib.robotparser.RobotFileParser | bool:
        origin = self._origin(url)
        cached = self._cache.get(origin)
        if cached is None or self._clock() - cached[0] > self.TTL_SECONDS:
            cached = (self._clock(), self._load(origin))
            self._cache[origin] = cached
        return cached[1]

    def allowed(self, url: str) -> bool:
        rules = self._get(url)
        if isinstance(rules, bool):
            return rules
        return rules.can_fetch(self._ua, url)

    def crawl_delay(self, url: str) -> float | None:
        rules = self._get(url)
        if isinstance(rules, bool):
            return None
        delay = rules.crawl_delay(self._ua)
        return float(delay) if delay else None


class PoliteFetcher:
    def __init__(self, settings: Settings, cache_dir: Path | None = None, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 max_requests_per_host: int | None = None):
        c = settings.crawling
        self.max_bytes = c.max_download_mb * 1024 * 1024
        self.max_retries = c.max_retries
        self.max_requests_per_host = max_requests_per_host
        self.limiter = DomainLimiter(c.requests_per_minute_per_domain, c.backoff_base_seconds, clock=clock, sleep=sleep)
        self._client = httpx.Client(
            transport=transport, timeout=c.request_timeout_seconds, follow_redirects=True,
            max_redirects=MAX_REDIRECTS, headers={"User-Agent": c.user_agent, "Accept-Encoding": "gzip, deflate"},
        )
        self.robots = RobotsCache(self._fetch_robots_raw, c.user_agent, clock=clock)
        self.cache = HttpCache(cache_dir) if cache_dir else None
        self._host_counts: dict[str, int] = {}

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PoliteFetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- internals ------------------------------------------------------------------------------------

    def _fetch_robots_raw(self, robots_url: str) -> tuple[int | None, bytes]:
        host = host_of(robots_url)
        self.limiter.wait_turn(host)
        try:
            r = self._client.get(robots_url)
            return r.status_code, r.content[:512 * 1024]
        except httpx.HTTPError:
            return None, b""
        finally:
            self._client.cookies.clear()

    def _stream(self, url: str, headers: dict[str, str]) -> tuple[httpx.Response, bytes]:
        with self._client.stream("GET", url, headers=headers) as r:
            declared = r.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > self.max_bytes:
                raise _TooLarge(int(declared))
            chunks, total = [], 0
            for chunk in r.iter_bytes():
                total += len(chunk)
                if total > self.max_bytes:
                    raise _TooLarge(total)
                chunks.append(chunk)
            return r, b"".join(chunks)

    # -- public ---------------------------------------------------------------------------------------

    def fetch(self, url: str, official_api: bool = False) -> FetchResponse:
        try:
            canonical = canonicalize_url(url)
        except InvalidURLError as e:
            return FetchResponse(url, url, access_status=AccessStatus.ERROR, error=str(e), invalid_url=True)
        resp = FetchResponse(url, canonical)
        host = host_of(canonical)

        if official_api and not is_official_api(canonical):
            resp.access_status, resp.error = AccessStatus.ERROR, "NOT_AN_OFFICIAL_API_ENDPOINT"
            return resp
        if not official_api and not self.robots.allowed(canonical):
            resp.access_status = AccessStatus.ROBOTS_DISALLOWED
            return resp
        if self.limiter.is_cooling_down(host):
            resp.access_status, resp.error = AccessStatus.ERROR, "HOST_COOLDOWN: too many recent errors"
            return resp
        if self.max_requests_per_host is not None and self._host_counts.get(host, 0) >= self.max_requests_per_host:
            resp.access_status, resp.error = AccessStatus.NOT_FETCHED, "DOMAIN_BUDGET_EXHAUSTED"
            return resp
        delay = None if official_api else self.robots.crawl_delay(canonical)
        if delay:
            self.limiter.delay_next(host, min(delay, 60.0))

        headers: dict[str, str] = {}
        cached = self.cache.get(canonical) if self.cache else None
        if cached:
            if cached[0].get("etag"):
                headers["If-None-Match"] = cached[0]["etag"]
            if cached[0].get("last_modified"):
                headers["If-Modified-Since"] = cached[0]["last_modified"]

        for attempt in range(self.max_retries + 1):
            resp.attempts = attempt + 1
            self.limiter.wait_turn(host)
            self._host_counts[host] = self._host_counts.get(host, 0) + 1
            try:
                r, body = self._stream(canonical, headers)
            except _TooLarge as e:
                resp.access_status, resp.error = AccessStatus.ERROR, f"TOO_LARGE: {e.size} bytes > {self.max_bytes}"
                return resp
            except httpx.TooManyRedirects:
                resp.access_status, resp.error = AccessStatus.ERROR, "TOO_MANY_REDIRECTS"
                self.limiter.record_error(host)
                return resp
            except httpx.HTTPError as e:
                self.limiter.record_error(host)
                resp.access_status, resp.error = AccessStatus.ERROR, redact(f"{type(e).__name__}: {e}")[:300]
                if attempt < self.max_retries:
                    self.limiter.sleep(self.limiter.backoff_delay(attempt))
                    continue
                return resp
            finally:
                self._client.cookies.clear()

            resp.status_code = r.status_code
            resp.final_url = str(r.url)
            resp.content_type = r.headers.get("content-type")

            if r.status_code in RETRY_STATUSES:
                self.limiter.record_error(host)
                wait = parse_retry_after(r.headers.get("retry-after"))
                if r.status_code == 429 and wait is not None and wait > MAX_RETRY_AFTER_SECONDS:
                    resp.access_status, resp.error = AccessStatus.ERROR, f"RATE_LIMITED: Retry-After {wait:.0f}s"
                    return resp
                if attempt < self.max_retries:
                    if wait is not None:
                        self.limiter.record_retry_after(host, wait)
                    else:
                        self.limiter.sleep(self.limiter.backoff_delay(attempt))
                    continue
                resp.access_status, resp.error = AccessStatus.ERROR, f"HTTP_{r.status_code} after {resp.attempts} tries"
                return resp

            self.limiter.record_success(host)
            if r.status_code == 304 and cached:
                resp.content, resp.from_cache = cached[1], True
                resp.content_type = cached[0].get("content_type")
                resp.status_code = 200
                resp.access_status = AccessStatus.OK
                resp.request_class = classify_request(canonical, None, resp.content_type)
                return resp

            if resp.final_url != canonical and not (official_api and is_official_api(resp.final_url)) \
                    and not self.robots.allowed(resp.final_url):
                resp.access_status = AccessStatus.ROBOTS_DISALLOWED
                return resp

            head = body[:200_000].decode("utf-8", errors="ignore")
            barrier = detect_access_barrier(r.status_code, canonical, resp.final_url, head)
            resp.request_class = classify_request(canonical, None, resp.content_type)
            if barrier in (AccessStatus.ACCESS_RESTRICTED, AccessStatus.MANUAL_ACCESS_REQUIRED,
                           AccessStatus.PRIVATE_ACCESS_REQUIRED):
                resp.access_status = barrier  # stop: body discarded, never worked around (spec §105, §107)
                return resp
            if r.status_code >= 400:
                resp.access_status, resp.error = AccessStatus.ERROR, f"HTTP_{r.status_code}"
                return resp

            resp.content = body
            resp.access_status = barrier or AccessStatus.OK  # PAYWALLED keeps only what was publicly delivered
            if self.cache and resp.access_status is AccessStatus.OK and r.status_code == 200:
                self.cache.put(canonical, {"etag": r.headers.get("etag"),
                                           "last_modified": r.headers.get("last-modified"),
                                           "content_type": resp.content_type}, body)
            return resp
        return resp  # pragma: no cover


class _TooLarge(Exception):
    def __init__(self, size: int):
        super().__init__(size)
        self.size = size
