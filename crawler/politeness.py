"""Per-domain rate limiting.

Keeps one clock per host, so a crawl spreads its requests across domains instead
of hammering a single server. The clock and the sleep function are injected,
which keeps the crawl tests instant and deterministic.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urlsplit

SleepFunc = Callable[[float], None]
ClockFunc = Callable[[], float]


class PerDomainDelay:
    """Enforces a minimum interval between requests to the same host."""

    def __init__(
        self,
        delay: float,
        *,
        sleep: SleepFunc = time.sleep,
        clock: ClockFunc = time.monotonic,
    ) -> None:
        if delay < 0:
            raise ValueError("delay must not be negative")
        self.delay = delay
        self._sleep = sleep
        self._clock = clock
        self._last_seen: dict[str, float] = {}

    def wait(self, url: str) -> float:
        """Block until ``url``'s host may be contacted. Returns the seconds waited."""
        host = urlsplit(url).hostname or ""
        now = self._clock()

        waited = 0.0
        last = self._last_seen.get(host)
        if last is not None:
            remaining = self.delay - (now - last)
            if remaining > 0:
                self._sleep(remaining)
                waited = remaining
                now += remaining

        self._last_seen[host] = now
        return waited
