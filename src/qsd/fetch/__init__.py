"""Polite, compliance-aware retrieval (spec §10–§16, §102–§107)."""

from .client import FetchResponse, HttpCache, PoliteFetcher, RobotsCache
from .enrich import enrich_source
from .identifiers import identifiers_from_url
from .pipeline import fetch_and_store
from .policy import classify_request, detect_access_barrier, redact_headers
from .urls import InvalidURLError, canonicalize_url, host_of
from .youtube import Transcript, TranscriptError, fetch_transcript, vtt_to_text, yt_dlp_path

__all__ = [
    "FetchResponse", "HttpCache", "PoliteFetcher", "RobotsCache", "fetch_and_store", "classify_request",
    "detect_access_barrier", "redact_headers", "InvalidURLError", "canonicalize_url", "host_of",
    "enrich_source", "identifiers_from_url",
    "Transcript", "TranscriptError", "fetch_transcript", "vtt_to_text", "yt_dlp_path",
]
