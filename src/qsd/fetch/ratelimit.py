"""Per-domain politeness: request spacing, backoff with jitter, Retry-After, cooldown (spec §102, §103)."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field

MAX_RETRY_AFTER_SECONDS = 600
ERRORS_BEFORE_COOLDOWN = 5
COOLDOWN_SECONDS = 1800


@dataclass
class HostState:
    next_allowed: float = 0.0
    consecutive_errors: int = 0
    blocked_until: float = 0.0
    requests: int = 0
    rate_limited: int = 0


@dataclass
class DomainLimiter:
    requests_per_minute: int
    backoff_base: float = 2.0
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    rng: random.Random = field(default_factory=random.Random)
    hosts: dict[str, HostState] = field(default_factory=dict)

    def _state(self, host: str) -> HostState:
        return self.hosts.setdefault(host, HostState())

    def is_cooling_down(self, host: str) -> bool:
        return self._state(host).blocked_until > self.clock()

    def wait_turn(self, host: str) -> None:
        st = self._state(host)
        now = self.clock()
        delay = st.next_allowed - now
        if delay > 0:
            self.sleep(delay)
            now = self.clock()
        st.next_allowed = now + 60.0 / self.requests_per_minute
        st.requests += 1

    def backoff_delay(self, attempt: int) -> float:
        """Exponential backoff with full jitter: uniform(0, base * 2**attempt), at least 0.5s."""
        return max(0.5, self.rng.uniform(0, self.backoff_base * (2 ** attempt)))

    def record_success(self, host: str) -> None:
        self._state(host).consecutive_errors = 0

    def record_error(self, host: str) -> None:
        st = self._state(host)
        st.consecutive_errors += 1
        if st.consecutive_errors >= ERRORS_BEFORE_COOLDOWN:
            st.blocked_until = self.clock() + COOLDOWN_SECONDS
            st.consecutive_errors = 0

    def delay_next(self, host: str, seconds: float) -> None:
        st = self._state(host)
        st.next_allowed = max(st.next_allowed, self.clock() + seconds)

    def record_retry_after(self, host: str, seconds: float) -> None:
        self._state(host).rate_limited += 1
        self.delay_next(host, seconds)


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(value)
        return max(0.0, dt.timestamp() - time.time())
    except (TypeError, ValueError):
        return None
