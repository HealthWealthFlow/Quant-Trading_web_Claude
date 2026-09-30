"""Deterministic initial source tiers from the spec's hierarchy (§24). No AI; unknown domains stay untiered (NULL).

Tier 5 (signal sellers, scam promotions) needs content signals and is assigned by scoring in M6, never by domain
guesswork here.
"""

from __future__ import annotations

from ..fetch.urls import host_of

TIER_DOMAINS: dict[int, set[str]] = {
    1: {
        "arxiv.org", "export.arxiv.org", "ssrn.com", "papers.ssrn.com", "nber.org", "sec.gov", "federalreserve.gov",
        "newyorkfed.org", "bis.org", "imf.org", "ecb.europa.eu", "bankofengland.co.uk", "bnm.gov.my", "boj.or.jp",
        "cboe.com", "cmegroup.com", "nasdaq.com", "nyse.com", "theocc.com", "jstor.org", "sciencedirect.com",
        "onlinelibrary.wiley.com", "link.springer.com", "tandfonline.com", "academic.oup.com", "aeaweb.org",
        "journals.uchicago.edu", "cambridge.org", "jpm.pm-research.com", "msci.com", "spglobal.com",
    },
    2: {
        "quantconnect.com", "quantpedia.com", "alphaarchitect.com", "robotwealth.com", "quantstart.com",
        "quantinsti.com", "blog.quantinsti.com", "thinknewfound.com", "aqr.com", "researchaffiliates.com", "man.com",
        "twosigma.com", "hudsonthames.org", "pyquantnews.com",
    },
    3: {"youtube.com", "youtu.be", "github.com", "tradingview.com", "quant.stackexchange.com"},
    4: {
        "reddit.com", "x.com", "twitter.com", "t.me", "telegram.me", "facebook.com", "discord.com", "discord.gg",
        "medium.com", "substack.com", "mql5.com", "forexfactory.com", "elitetrader.com",
    },
}
_PRIMARY_SUFFIXES = (".edu", ".ac.uk", ".edu.au", ".ac.jp", ".edu.my", ".gov")
# Connector work types that indicate peer-reviewed or preprint research (spec §24 tier 1 includes arXiv q-fin).
_TIER1_WORK_TYPES = {"journal-article", "article", "preprint", "proceedings-article", "posted-content", "report"}


def tier_for_url(url: str | None) -> tuple[int | None, str]:
    """Return (tier, basis). Matches the domain or any parent domain."""
    if not url:
        return None, "NO_URL"
    host = host_of(url)
    labels = host.split(".")
    for i in range(len(labels) - 1):
        candidate = ".".join(labels[i:])
        for tier, domains in TIER_DOMAINS.items():
            if candidate in domains:
                return tier, f"DOMAIN:{candidate}"
    if host.endswith(_PRIMARY_SUFFIXES):
        return 1, "DOMAIN_SUFFIX"
    if host.endswith(".substack.com") or host.startswith(("forum.", "forums.", "community.")):
        return 4, "DOMAIN_PATTERN"
    return None, "UNKNOWN_DOMAIN"


def tier_for_candidate(url: str | None, connector: str, work_type: str) -> tuple[int | None, str]:
    tier, basis = tier_for_url(url)
    if tier is not None:
        return tier, basis
    if connector in ("arxiv", "openalex", "crossref") and work_type in _TIER1_WORK_TYPES:
        return 1, f"SCHOLARLY_RECORD:{connector}:{work_type}"
    return None, basis
