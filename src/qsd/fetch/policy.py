"""Access-policy checks: login walls, paywalls, CAPTCHAs (spec §10, §105–§107), request classification (§14) and
header redaction (§15). Detection only ever makes the system *stop*; nothing here tries to get around a barrier.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from ..taxonomy import AccessStatus, RequestClass

SENSITIVE_HEADERS = {
    "authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "x-auth-token", "x-csrf-token",
    "x-xsrf-token", "x-session-id", "x-amz-security-token",
}
_SENSITIVE_HEADER_RE = re.compile(r"(auth|token|secret|session|cookie|api[-_]?key|csrf|xsrf)", re.I)

_CAPTCHA = re.compile(
    r"g-recaptcha|hcaptcha|cf-turnstile|cf-challenge|challenge-platform|captcha-delivery|"
    r"checking your browser before accessing|verify you are (a )?human|are you a robot|unusual traffic from your",
    re.I,
)
_PAYWALL = re.compile(
    r"subscribe to (continue|keep) reading|subscribers[- ]only|this (article|content) is (only )?(available )?"
    r"(to|for) (paid )?subscribers|\"isAccessibleForFree\"\s*:\s*\"?false|purchase (this|the) (article|pdf)|"
    r"buy (this|the) article|create an account to (continue|read)|paywall",
    re.I,
)
_LOGIN_PATH = re.compile(r"/(login|log-in|signin|sign-in|sign_in|auth/|account/login|sso|session/new)", re.I)


def redact_headers(headers: dict[str, str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in (headers or {}).items():
        out[k] = "[REDACTED]" if k.lower() in SENSITIVE_HEADERS or _SENSITIVE_HEADER_RE.search(k) else v
    return out


def classify_request(url: str, request_headers: dict[str, str] | None = None,
                     content_type: str | None = None) -> RequestClass:
    """Classify a request (spec §14). Requests carrying credentials are never auto-processed."""
    headers = {k.lower() for k in (request_headers or {})}
    if headers & {"authorization", "proxy-authorization", "x-api-key", "x-auth-token"}:
        return RequestClass.SENSITIVE_AUTH
    if "cookie" in headers:
        return RequestClass.SESSION_PRIVATE
    ct = (content_type or "").lower()
    path = urlsplit(url).path.lower()
    if "json" in ct or "graphql" in path or path.startswith("/api/") or "/api/" in path:
        return RequestClass.PUBLIC_API
    if ct.startswith(("text/", "application/pdf", "application/xml", "application/rss", "application/atom",
                      "application/epub", "application/vnd.openxmlformats")) or ct == "":
        return RequestClass.PUBLIC_STATIC
    return RequestClass.UNKNOWN


def detect_access_barrier(status_code: int, requested_url: str, final_url: str,
                          body_head: str = "") -> AccessStatus | None:
    """Return the barrier type, or None if the content is openly accessible."""
    if status_code in (401, 407):
        return AccessStatus.ACCESS_RESTRICTED
    if status_code == 402:
        return AccessStatus.PAYWALLED
    if _CAPTCHA.search(body_head):
        return AccessStatus.MANUAL_ACCESS_REQUIRED
    if status_code == 403:
        return AccessStatus.ACCESS_RESTRICTED
    if final_url != requested_url and _LOGIN_PATH.search(urlsplit(final_url).path) and \
            not _LOGIN_PATH.search(urlsplit(requested_url).path):
        return AccessStatus.ACCESS_RESTRICTED
    if _PAYWALL.search(body_head):
        return AccessStatus.PAYWALLED
    return None
