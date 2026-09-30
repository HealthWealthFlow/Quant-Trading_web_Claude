"""URL canonicalization for deduplication (spec §93, §132)."""

from __future__ import annotations

from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id", "gclid", "fbclid", "dclid",
    "msclkid", "mc_cid", "mc_eid", "igshid", "ref_src", "_hsenc", "_hsmi", "si",
}
_DEFAULT_PORTS = {"http": 80, "https": 443}


class InvalidURLError(ValueError):
    pass


def canonicalize_url(url: str) -> str:
    """Lowercase scheme/host, drop default ports, fragments and tracking params, sort the query, normalise path."""
    raw = url.strip()
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise InvalidURLError(f"unsupported URL scheme: {url!r}")
    if not parts.hostname:
        raise InvalidURLError(f"URL has no host: {url!r}")
    if parts.username or parts.password:
        raise InvalidURLError("URLs with embedded credentials are not allowed")
    host = parts.hostname.lower().rstrip(".")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    port = parts.port
    netloc = host if port is None or port == _DEFAULT_PORTS[scheme] else f"{host}:{port}"
    path = quote(unquote(parts.path or "/"), safe="/:@!$&'()*+,;=-._~%")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                             if k.lower() not in _TRACKING_PARAMS))
    return urlunsplit((scheme, netloc, path, query, ""))


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()
