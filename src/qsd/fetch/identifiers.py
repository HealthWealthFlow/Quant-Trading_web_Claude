"""Identifiers we can derive from a URL (spec §24: identity DOI > arXiv > URL).

A paper is the same study whether it arrives as an arXiv abstract page, an arXiv PDF, a publisher landing page or a
direct PDF link. Those routes must land on **one** root-evidence identity, so the harvesting loop cannot count the
same paper twice under different source rows.
"""

from __future__ import annotations

import re

_ARXIV = re.compile(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})(?:v[0-9]+)?", re.I)
_ARXIV_OLD = re.compile(r"arxiv\.org/(?:abs|pdf)/([a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v[0-9]+)?", re.I)
_DOI = re.compile(r"(?:dx\.|www\.)?doi\.org/(10\.[^\s?#]+)", re.I)


def identifiers_from_url(url: str | None) -> dict[str, str]:
    """DOI/arXiv ids implied by a URL, version suffix stripped. Empty dict when nothing is recognisable.

    The version suffix matters: `arXiv:1304.6846v2` and `arXiv:1304.6846` are the same study, and
    `scoring.dedupe.root_evidence_id` must produce one root for both.
    """
    if not url:
        return {}
    out: dict[str, str] = {}
    m = _ARXIV.search(url)
    if m is None:
        m = _ARXIV_OLD.search(url)
    if m:
        out["arxiv"] = m.group(1)
    if (d := _DOI.search(url)) is not None:
        out["doi"] = d.group(1).rstrip(".").lower()
    return out
