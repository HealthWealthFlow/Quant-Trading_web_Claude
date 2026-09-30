"""Polite, compliance-aware retrieval (spec §10–§16, §102–§107)."""

from .client import FetchResponse, HttpCache, PoliteFetcher, RobotsCache
from .pipeline import fetch_and_store
from .policy import classify_request, detect_access_barrier, redact_headers
from .urls import InvalidURLError, canonicalize_url, host_of

__all__ = [
    "FetchResponse", "HttpCache", "PoliteFetcher", "RobotsCache", "fetch_and_store", "classify_request",
    "detect_access_barrier", "redact_headers", "InvalidURLError", "canonicalize_url", "host_of",
]
