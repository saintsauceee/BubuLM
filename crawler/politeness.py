"""Per-domain rate limiting.

Keeps one clock per host, so a crawl spreads its requests across domains instead
of hammering a single server. The clock and the sleep function are injected,
which keeps the crawl tests instant and deterministic.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

SleepFunc = Callable[[float], None]
AsyncSleepFunc = Callable[[float], Awaitable[None]]
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


class AsyncPerDomainDelay:
    """Per-host rate limiting for concurrent crawls.

    Where the synchronous limiter records *when a host was last contacted*, this
    one reserves *when a host may next be contacted*. The difference matters
    under concurrency: several tasks can be waiting on one host at once, and
    each must take its own slot in the queue rather than all reading the same
    "last seen" value and waking together.

    Concurrency: the reservation is a read-modify-write with no ``await`` inside
    it, so under a single event loop it is atomic -- two tasks cannot claim the
    same slot. The sleep happens after the reservation is recorded, so waiting
    on one host never blocks another.
    """

    def __init__(
        self,
        delay: float,
        *,
        sleep: AsyncSleepFunc = asyncio.sleep,
        clock: ClockFunc = time.monotonic,
    ) -> None:
        if delay < 0:
            raise ValueError("delay must not be negative")
        self.delay = delay
        self._sleep = sleep
        self._clock = clock
        self._next_allowed: dict[str, float] = {}

    async def wait(self, url: str) -> float:
        """Wait until ``url``'s host may be contacted. Returns the seconds waited."""
        host = urlsplit(url).hostname or ""

        # Reserve this host's next slot. No await here: the read and the write
        # must not be separated, or two tasks would claim the same slot.
        now = self._clock()
        start_at = max(now, self._next_allowed.get(host, now))
        self._next_allowed[host] = start_at + self.delay

        waited = start_at - now
        if waited > 0:
            await self._sleep(waited)
        return waited
