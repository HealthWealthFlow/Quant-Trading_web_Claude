"""Discovery connectors: official APIs and feeds return *metadata only* (spec §11, §27). Nothing is guessed:
absent fields stay UNKNOWN. Full documents are fetched later, only for candidates that pass cheap filters (§84).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlencode

from lxml import etree

from ..fetch.client import PoliteFetcher
from ..taxonomy import UNKNOWN

_SAFE_XML = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, recover=True)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _clean(text: str | None) -> str:
    return _WS.sub(" ", _TAG.sub(" ", text or "")).strip()


@dataclass
class Candidate:
    connector: str
    title: str = UNKNOWN
    authors: list[str] = field(default_factory=list)
    publication_date: str = UNKNOWN
    url: str | None = None
    pdf_url: str | None = None
    abstract: str | None = None
    venue: str = UNKNOWN
    work_type: str = UNKNOWN  # preprint / journal-article / feed-item / web-page ...
    ids: dict[str, str] = field(default_factory=dict)  # doi, arxiv, openalex

    @property
    def canonical_link(self) -> str | None:
        """One identity per work across connectors: DOI > arXiv abs page > landing URL > PDF URL."""
        if self.ids.get("doi"):
            return f"https://doi.org/{self.ids['doi'].lower()}"
        if self.ids.get("arxiv"):
            return f"https://arxiv.org/abs/{self.ids['arxiv']}"
        return self.url or self.pdf_url


class ConnectorError(RuntimeError):
    pass


class Connector:
    name = "base"

    def __init__(self, fetcher: PoliteFetcher, contact_email: str | None = None):
        self.fetcher = fetcher
        self.contact_email = contact_email

    def _get(self, url: str, official_api: bool = True) -> bytes:
        resp = self.fetcher.fetch(url, official_api=official_api)
        if not resp.ok or resp.content is None:
            raise ConnectorError(f"{self.name}: {resp.access_status.value} {resp.error or ''}".strip())
        return resp.content

    def search(self, query: str, limit: int = 10) -> list[Candidate]:
        raise NotImplementedError


def _doi(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip()
    v = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", v, flags=re.I)
    return v.lower() if v.startswith("10.") else None


# ---- arXiv (Atom API) ------------------------------------------------------------------------------

ARXIV_QFIN = ("q-fin.PM", "q-fin.TR", "q-fin.ST", "q-fin.CP", "q-fin.RM", "q-fin.GN", "q-fin.PR", "q-fin.MF")
_ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


class ArxivConnector(Connector):
    name = "arxiv"

    def search(self, query: str, limit: int = 10) -> list[Candidate]:
        cats = " OR ".join(f"cat:{c}" for c in ARXIV_QFIN)
        terms = " AND ".join(f"all:{w}" for w in re.findall(r"[A-Za-z0-9\-]+", query)) or "all:finance"
        qs = urlencode({"search_query": f"({cats}) AND ({terms})", "start": 0, "max_results": limit,
                        "sortBy": "relevance"})
        return self.parse(self._get(f"https://export.arxiv.org/api/query?{qs}"))

    @staticmethod
    def parse(data: bytes) -> list[Candidate]:
        root = etree.fromstring(data, _SAFE_XML)
        out = []
        for e in root.iterfind("a:entry", _ATOM):
            c = Candidate("arxiv", work_type="preprint")
            c.title = _clean(e.findtext("a:title", namespaces=_ATOM)) or UNKNOWN
            c.abstract = _clean(e.findtext("a:summary", namespaces=_ATOM)) or None
            published = (e.findtext("a:published", namespaces=_ATOM) or "")[:10]
            c.publication_date = published or UNKNOWN
            c.authors = [_clean(a.findtext("a:name", namespaces=_ATOM)) for a in e.iterfind("a:author", _ATOM)]
            abs_id = e.findtext("a:id", namespaces=_ATOM) or ""
            m = re.search(r"abs/([^v\s]+)", abs_id)
            if m:
                c.ids["arxiv"] = m.group(1)
            for link in e.iterfind("a:link", _ATOM):
                if link.get("title") == "pdf":
                    c.pdf_url = link.get("href")
                elif link.get("rel") == "alternate":
                    c.url = link.get("href")
            doi = _doi(e.findtext("arxiv:doi", namespaces=_ATOM))
            if doi:
                c.ids["doi"] = doi
            c.venue = _clean(e.findtext("arxiv:journal_ref", namespaces=_ATOM)) or "arXiv"
            out.append(c)
        return out


# ---- OpenAlex (JSON API) ---------------------------------------------------------------------------

class OpenAlexConnector(Connector):
    name = "openalex"

    def search(self, query: str, limit: int = 10) -> list[Candidate]:
        params = {"search": query, "per-page": limit}
        if self.contact_email:
            params["mailto"] = self.contact_email
        return self.parse(self._get(f"https://api.openalex.org/works?{urlencode(params)}"))

    @staticmethod
    def _abstract(inverted: dict | None) -> str | None:
        if not inverted:
            return None
        positions = [(pos, word) for word, poss in inverted.items() for pos in poss]
        return " ".join(w for _, w in sorted(positions)) or None

    @classmethod
    def parse(cls, data: bytes) -> list[Candidate]:
        out = []
        for w in json.loads(data).get("results", []):
            c = Candidate("openalex", work_type=w.get("type") or UNKNOWN)
            c.title = _clean(w.get("display_name") or w.get("title")) or UNKNOWN
            c.publication_date = w.get("publication_date") or (str(w["publication_year"]) if w.get("publication_year")
                                                               else UNKNOWN)
            c.authors = [a["author"]["display_name"] for a in w.get("authorships", [])
                         if a.get("author", {}).get("display_name")]
            loc = w.get("primary_location") or {}
            c.url = loc.get("landing_page_url")
            c.venue = ((loc.get("source") or {}).get("display_name")) or UNKNOWN
            c.pdf_url = (w.get("best_oa_location") or {}).get("pdf_url") or loc.get("pdf_url")
            c.abstract = cls._abstract(w.get("abstract_inverted_index"))
            doi = _doi(w.get("doi"))
            if doi:
                c.ids["doi"] = doi
            if w.get("id"):
                c.ids["openalex"] = w["id"].rsplit("/", 1)[-1]
            arxiv = (w.get("ids") or {}).get("arxiv") or ""
            m = re.search(r"(\d{4}\.\d{4,5})", arxiv)
            if m:
                c.ids["arxiv"] = m.group(1)
            out.append(c)
        return out


# ---- Crossref (JSON API) ---------------------------------------------------------------------------

class CrossrefConnector(Connector):
    name = "crossref"

    def search(self, query: str, limit: int = 10) -> list[Candidate]:
        params = {"query": query, "rows": limit,
                  "select": "DOI,title,author,issued,URL,container-title,abstract,type,link"}
        if self.contact_email:
            params["mailto"] = self.contact_email
        return self.parse(self._get(f"https://api.crossref.org/works?{urlencode(params)}"))

    @staticmethod
    def parse(data: bytes) -> list[Candidate]:
        out = []
        for it in json.loads(data).get("message", {}).get("items", []):
            c = Candidate("crossref", work_type=it.get("type") or UNKNOWN)
            c.title = _clean((it.get("title") or [UNKNOWN])[0]) or UNKNOWN
            c.authors = [" ".join(p for p in (a.get("given"), a.get("family")) if p) for a in it.get("author", [])
                         if a.get("family") or a.get("given")]
            parts = ((it.get("issued") or {}).get("date-parts") or [[None]])[0]
            if parts and parts[0]:
                c.publication_date = "-".join(f"{p:02d}" if i else str(p) for i, p in enumerate(parts) if p)
            c.url = it.get("URL")
            c.venue = _clean((it.get("container-title") or [UNKNOWN])[0]) or UNKNOWN
            c.abstract = _clean(it.get("abstract")) or None
            for link in it.get("link", []) or []:
                if link.get("content-type") == "application/pdf":
                    c.pdf_url = link.get("URL")
                    break
            doi = _doi(it.get("DOI"))
            if doi:
                c.ids["doi"] = doi
            out.append(c)
        return out


# ---- RSS / Atom feeds (robots.txt applies: these are ordinary web resources) ----------------------------

class FeedConnector(Connector):
    name = "rss"

    def fetch_feed(self, feed_url: str) -> list[Candidate]:
        return self.parse(self._get(feed_url, official_api=False))

    def search(self, query: str, limit: int = 10) -> list[Candidate]:
        raise ConnectorError("feeds are read with fetch_feed(url); filter the items afterwards")

    @staticmethod
    def parse(data: bytes) -> list[Candidate]:
        root = etree.fromstring(data, _SAFE_XML)
        if root is None:
            raise ConnectorError("rss: not a valid feed")
        out = []
        items = root.findall(".//item")
        if items:  # RSS 2.0
            for it in items:
                c = Candidate("rss", work_type="feed-item")
                c.title = _clean(it.findtext("title")) or UNKNOWN
                c.url = (it.findtext("link") or "").strip() or None
                c.abstract = _clean(it.findtext("description")) or None
                c.publication_date = (it.findtext("pubDate") or "").strip() or UNKNOWN
                author = it.findtext("author") or it.findtext("{http://purl.org/dc/elements/1.1/}creator")
                if author:
                    c.authors = [_clean(author)]
                out.append(c)
            return out
        for e in root.iterfind("a:entry", _ATOM):  # Atom
            c = Candidate("rss", work_type="feed-item")
            c.title = _clean(e.findtext("a:title", namespaces=_ATOM)) or UNKNOWN
            link = e.find("a:link", _ATOM)
            c.url = link.get("href") if link is not None else None
            c.abstract = _clean(e.findtext("a:summary", namespaces=_ATOM) or
                                e.findtext("a:content", namespaces=_ATOM)) or None
            c.publication_date = (e.findtext("a:published", namespaces=_ATOM) or
                                  e.findtext("a:updated", namespaces=_ATOM) or UNKNOWN)[:10]
            c.authors = [_clean(a.findtext("a:name", namespaces=_ATOM)) for a in e.iterfind("a:author", _ATOM)]
            out.append(c)
        return out


def candidates_from_urls(urls: list[str]) -> list[Candidate]:
    """User-provided URLs: identity only; everything else stays UNKNOWN until fetched."""
    return [Candidate("user_urls", url=u.strip(), work_type="web-page") for u in urls if u.strip()]


CONNECTORS = {"arxiv": ArxivConnector, "openalex": OpenAlexConnector, "crossref": CrossrefConnector}
