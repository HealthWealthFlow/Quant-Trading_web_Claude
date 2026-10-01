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


# ---- YouTube (YouTube Data API v3, metadata only) -------------------------------------------------------
#
# Only what the official API returns is used: title, channel, date and the full description (often with links to
# the paper or code the video is based on). Videos are never downloaded and transcripts are not scraped (YouTube's
# terms allow neither); a video's description is read as its text, and its research links are followed.

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')\]]+")
_SKIP_LINK_HOSTS = ("youtube.com", "youtu.be", "instagram.com", "facebook.com", "tiktok.com", "x.com", "twitter.com",
                    "t.me", "discord.gg", "discord.com", "patreon.com", "linktr.ee", "bit.ly", "amzn.to",
                    "amazon.com", "spotify.com", "apple.com", "linkedin.com", "buymeacoffee.com", "ko-fi.com")
_SKIP_LINK_HINTS = ("affiliate", "ref=", "referral", "promo", "coupon", "discount", "signup", "sign-up", "register",
                    "join", "subscribe", "course", "checkout", "broker", "bonus")


def research_links(text: str | None, limit: int = 5) -> list[str]:
    """Links in a video description that may lead to the underlying research: papers, DOIs, PDFs, code, blogs.
    Social, shop, affiliate and sign-up links are skipped."""
    from ..fetch.urls import host_of

    out: list[str] = []
    for url in _URL_IN_TEXT.findall(text or ""):
        url = url.rstrip(".,;:!?")
        host = host_of(url)
        low = url.lower()
        if not host or any(host == h or host.endswith("." + h) for h in _SKIP_LINK_HOSTS):
            continue
        if any(hint in low for hint in _SKIP_LINK_HINTS):
            continue
        if url not in out:
            out.append(url)
        if len(out) >= limit:
            break
    return out


class YouTubeConnector(Connector):
    name = "youtube"

    def __init__(self, fetcher: PoliteFetcher, contact_email: str | None = None, api_key: str | None = None,
                 max_results: int = 10):
        super().__init__(fetcher, contact_email)
        self.api_key = api_key
        self.max_results = max_results  # each search costs 100 of the free 10,000 daily quota units

    def _api(self, path: str, params: dict) -> dict:
        if not self.api_key:
            raise ConnectorError("youtube: no API key (set the YOUTUBE_API_KEY environment variable)")
        # The key travels in a header, never in the URL (URLs are cached and may appear in logs/errors).
        resp = self.fetcher.fetch(f"{YOUTUBE_API}/{path}?{urlencode(params)}", official_api=True,
                                  extra_headers={"X-Goog-Api-Key": self.api_key})
        if not resp.ok or resp.content is None:
            hint = " (check the key, or the daily quota)" if resp.status_code in (400, 403) else ""
            raise ConnectorError(f"youtube: {resp.access_status.value} {resp.error or ''}{hint}".strip())
        return json.loads(resp.content)

    def search(self, query: str, limit: int = 10) -> list[Candidate]:
        found = self._api("search", {"part": "snippet", "type": "video", "q": query,
                                     "maxResults": min(limit, self.max_results, 50), "relevanceLanguage": "en",
                                     "order": "relevance"})
        ids = [it["id"]["videoId"] for it in found.get("items", []) if (it.get("id") or {}).get("videoId")]
        if not ids:
            return []
        return self.videos(ids)

    def videos(self, ids: list[str]) -> list[Candidate]:
        """Metadata for known video ids (1 quota unit per 50 videos)."""
        out: list[Candidate] = []
        for i in range(0, len(ids), 50):
            out += self.parse(self._api("videos", {"part": "snippet,contentDetails", "id": ",".join(ids[i:i + 50])}))
        return out

    def channel_video_ids(self, channel: str, n: int, skip: set[str] = frozenset(), max_scan: int = 1000
                          ) -> tuple[str, list[str]]:
        """Newest uploads of a channel that are not in `skip`: (channel title, up to n video ids).

        `channel` is '@handle', 'channel/UC…', 'user/name' or 'c/name'. Uses the channel's uploads playlist
        (1 quota unit per 50 videos), never the website."""
        kind, _, value = channel.partition("/") if "/" in channel else ("handle", "", channel)
        params = {"part": "snippet,contentDetails"}
        if kind == "handle":
            params["forHandle"] = value
        elif kind == "channel":
            params["id"] = value
        elif kind == "user":
            params["forUsername"] = value
        else:  # legacy /c/ custom URL: the API has no direct lookup, so find the channel by search (100 units)
            hit = self._api("search", {"part": "snippet", "type": "channel", "q": value, "maxResults": 1})
            items = hit.get("items") or []
            if not items:
                raise ConnectorError(f"youtube: channel '{channel}' not found")
            params["id"] = items[0]["id"]["channelId"]
        found = (self._api("channels", params).get("items") or [])
        if not found:
            raise ConnectorError(f"youtube: channel '{channel}' not found")
        title = _clean((found[0].get("snippet") or {}).get("title")) or channel
        uploads = ((found[0].get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        if not uploads:
            return title, []
        ids: list[str] = []
        scanned, token = 0, None
        while len(ids) < n and scanned < max_scan:
            page_params = {"part": "contentDetails", "playlistId": uploads, "maxResults": 50}
            if token:
                page_params["pageToken"] = token
            page = self._api("playlistItems", page_params)
            for it in page.get("items", []):
                scanned += 1
                vid = (it.get("contentDetails") or {}).get("videoId")
                if vid and vid not in skip and vid not in ids:
                    ids.append(vid)
                    if len(ids) >= n:
                        break
            token = page.get("nextPageToken")
            if not token:
                break
        return title, ids

    @staticmethod
    def parse(data: dict) -> list[Candidate]:
        out = []
        for it in data.get("items", []):
            vid, sn = it.get("id"), it.get("snippet") or {}
            if not vid:
                continue
            c = Candidate("youtube", work_type="video", url=f"https://www.youtube.com/watch?v={vid}")
            c.title = _clean(sn.get("title")) or UNKNOWN
            channel = _clean(sn.get("channelTitle"))
            c.authors = [channel] if channel else []
            c.venue = f"YouTube: {channel}" if channel else "YouTube"
            c.publication_date = (sn.get("publishedAt") or "")[:10] or UNKNOWN
            c.abstract = (sn.get("description") or "").strip() or None  # kept as written (links intact)
            c.ids["youtube"] = vid
            duration = (it.get("contentDetails") or {}).get("duration")
            if duration:
                c.ids["duration"] = duration
            out.append(c)
        return out


CONNECTORS = {"arxiv": ArxivConnector, "openalex": OpenAlexConnector, "crossref": CrossrefConnector,
              "youtube": YouTubeConnector}
PAPER_CONNECTORS = ("arxiv", "openalex", "crossref")


def build_connectors(fetcher: PoliteFetcher, settings, names: list[str] | None = None) -> list[Connector]:
    """Connectors for a run. YouTube joins only when enabled and YOUTUBE_API_KEY is set."""
    from ..config import get_secret

    key = get_secret("YOUTUBE_API_KEY")
    out: list[Connector] = []
    for name in names or list(CONNECTORS):
        if name == "youtube":
            if settings.discovery.youtube_enabled and key:
                out.append(YouTubeConnector(fetcher, settings.discovery.contact_email, api_key=key,
                                            max_results=settings.discovery.youtube_results_per_query))
            elif names:  # explicitly requested but unavailable
                raise ConnectorError("youtube: set the YOUTUBE_API_KEY environment variable first")
            continue
        out.append(CONNECTORS[name](fetcher, settings.discovery.contact_email))
    return out
