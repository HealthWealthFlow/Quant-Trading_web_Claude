"""Searching chosen websites automatically (D58): blogs and research libraries from `discovery.sites`.

A site is read the way a person's feed reader would read it, never by crawling its pages:
1. its **RSS/Atom feed** (found from the page's `<link rel="alternate">` tag, or a few conventional paths), else
2. its **XML sitemap** (optionally limited to a path such as `/insights/`).
Every fetch goes through `PoliteFetcher`, so robots.txt, rate limits and "no login / paywall / CAPTCHA bypass" apply
exactly as everywhere else; a site that blocks us, or offers neither a feed nor a sitemap, is reported and skipped.

A search keeps only the items that match the query's words (at least two of them when the query has two or more
distinct words), so a feed of everything a blog publishes does not flood the campaign. The matched pages are then
read, fact-checked and scored like any other source; the feed text is stored only as the cheap-filter abstract.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urljoin, urlsplit

from lxml import etree
from lxml import html as lxml_html

from ..fetch.urls import host_of
from ..taxonomy import UNKNOWN
from .connectors import _SAFE_XML, Candidate, Connector, ConnectorError, FeedConnector

FALLBACK_FEED_PATHS = ("feed/", "feed", "rss", "rss.xml", "atom.xml", "index.xml")
FEED_LINK_TYPES = ("application/rss+xml", "application/atom+xml")
MAX_SITEMAP_URLS = 5000
MAX_CHILD_SITEMAPS = 5
_PREFERRED_CHILD = ("post", "blog", "insight", "article", "research", "paper", "strateg")
_STOP = {"trading", "strategy", "strategies", "the", "and", "for", "with", "from", "using", "that", "this", "based",
         "into", "are", "its", "their"}
_SLUG_EXT = re.compile(r"\.(html?|php|aspx?)$", re.I)


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9\-]{2,}", text.lower()) if w not in _STOP}


def _xpath_text(root, expr: str) -> list[str]:
    return [t.strip() for t in root.xpath(expr) if isinstance(t, str) and t.strip()]


class SiteConnector(Connector):
    """One configured site. `search(query)` filters the site's feed or sitemap by the query's words."""

    memory_days = 1  # a feed changes daily: an identical search is worth repeating after a day, not a month
    uses_search_budget = False  # the site is fetched once per run and each query is filtered locally

    def __init__(self, fetcher, site, contact_email: str | None = None):
        super().__init__(fetcher, contact_email)
        self.site = site
        self.name = f"site:{host_of(site.url)}"[:40]
        self.resolved_via: str | None = None
        self._items: list[Candidate] | None = None
        self._error: ConnectorError | None = None

    # ---- loading -----------------------------------------------------------------------------------
    def items(self) -> list[Candidate]:
        """Everything the site's feed or sitemap lists (loaded once per run; a failure is remembered, not retried)."""
        if self._error is not None:
            raise self._error
        if self._items is None:
            try:
                self._items = self._load()
            except ConnectorError as e:
                self._error = ConnectorError(f"{self.site.name}: {e}")
                raise self._error from None
        return self._items

    def _load(self) -> list[Candidate]:
        site = self.site
        if site.kind == "feed":
            return self._remember(site.url, self._feed(site.url))
        if site.kind == "sitemap":  # the address may be the site's home page: its sitemap is at the conventional path
            url = site.url if site.url.lower().split("?")[0].endswith(".xml") else urljoin(site.url, "/sitemap.xml")
            return self._remember(url, self._sitemap(url))
        page = self._get(site.url, official_api=False)  # robots.txt applies
        candidates = self._feed_links(page, site.url)
        candidates += [urljoin(site.url, p) for p in FALLBACK_FEED_PATHS if urljoin(site.url, p) not in candidates]
        for url in candidates:
            try:
                items = self._feed(url)
            except ConnectorError:
                continue
            if items:
                return self._remember(url, items)
        sitemap = urljoin(site.url, "/sitemap.xml")
        try:
            items = self._sitemap(sitemap)
        except ConnectorError:
            items = []
        if items:
            return self._remember(sitemap, items)
        raise ConnectorError("no RSS/Atom feed or sitemap found; put its feed address in discovery.sites "
                             "(kind: feed) or list the pages you want in sources.txt")

    def _remember(self, via: str, items: list[Candidate]) -> list[Candidate]:
        self.resolved_via = via
        return items

    @staticmethod
    def _feed_links(page: bytes, base: str) -> list[str]:
        try:
            doc = lxml_html.fromstring(page)
        except (etree.ParserError, ValueError):
            return []
        out: list[str] = []
        for link in doc.xpath("//link[@rel='alternate'][@href]"):
            if (link.get("type") or "").lower() in FEED_LINK_TYPES:
                url = urljoin(base, link.get("href"))
                if url not in out:
                    out.append(url)
        return out

    def _feed(self, url: str) -> list[Candidate]:
        items = [c for c in FeedConnector.parse(self._get(url, official_api=False)) if c.url]
        for c in items:
            c.connector, c.work_type = self.name, "blog-post"
        return items

    def _sitemap(self, url: str, depth: int = 0) -> list[Candidate]:
        root = etree.fromstring(self._get(url, official_api=False), _SAFE_XML)
        if root is None:
            raise ConnectorError("sitemap is not valid XML")
        children = _xpath_text(root, "//*[local-name()='sitemap']/*[local-name()='loc']/text()")
        if children and depth == 0:  # a sitemap index: read a few of the children that look like articles
            children.sort(key=lambda u: not any(h in u.lower() for h in _PREFERRED_CHILD))
            out: list[Candidate] = []
            for child in children[:MAX_CHILD_SITEMAPS]:
                try:
                    out += self._sitemap(child, depth=1)
                except ConnectorError:
                    continue
            return out[:MAX_SITEMAP_URLS]
        prefix = (self.site.path or "").lower()
        out = []
        for entry in root.xpath("//*[local-name()='url']"):
            loc = _xpath_text(entry, "./*[local-name()='loc']/text()")
            if not loc:
                continue
            link = loc[0]
            path = urlsplit(link).path
            if prefix and not path.lower().startswith(prefix):
                continue
            slug = _SLUG_EXT.sub("", unquote(path.rstrip("/").rsplit("/", 1)[-1]))
            if not slug:
                continue
            c = Candidate(self.name, url=link, work_type="web-page")
            c.title = re.sub(r"[-_]+", " ", slug).strip() or UNKNOWN
            lastmod = _xpath_text(entry, "./*[local-name()='lastmod']/text()")
            if lastmod:
                c.publication_date = lastmod[0][:10]
            out.append(c)
            if len(out) >= MAX_SITEMAP_URLS:
                break
        return out

    # ---- searching ---------------------------------------------------------------------------------
    def search(self, query: str, limit: int = 10) -> list[Candidate]:
        words = _tokens(query)
        if not words:
            return []
        need = min(2, len(words))
        scored: list[tuple[int, int, Candidate]] = []
        for position, c in enumerate(self.items()):
            haystack = f"{c.title} {c.abstract or ''} {c.url}".lower()
            hits = sum(1 for w in words if re.search(r"\b" + re.escape(w), haystack))
            if hits >= need:
                scored.append((-hits, position, c))  # more matches first, then the site's own order (newest first)
        scored.sort(key=lambda t: (t[0], t[1]))
        return [c for _, _, c in scored[:limit]]
