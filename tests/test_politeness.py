"""Tests for per-domain rate limiting."""

from __future__ import annotations

import asyncio

import pytest

from crawler.politeness import AsyncPerDomainDelay, PerDomainDelay
from tests.conftest import FakeAsyncTime, FakeTime


def make_delay(delay: float, time: FakeTime) -> PerDomainDelay:
    return PerDomainDelay(delay, sleep=time.sleep, clock=time.monotonic)


def test_first_request_to_a_host_does_not_wait() -> None:
    time = FakeTime()
    limiter = make_delay(1.0, time)

    assert limiter.wait("https://example.com/a") == 0.0
    assert time.sleeps == []


def test_second_request_to_the_same_host_waits() -> None:
    time = FakeTime()
    limiter = make_delay(1.0, time)

    limiter.wait("https://example.com/a")
    assert limiter.wait("https://example.com/b") == pytest.approx(1.0)
    assert time.sleeps == [pytest.approx(1.0)]


def test_waits_only_the_remaining_time() -> None:
    time = FakeTime()
    limiter = make_delay(1.0, time)

    limiter.wait("https://example.com/a")
    time.advance(0.6)

    assert limiter.wait("https://example.com/b") == pytest.approx(0.4)


def test_does_not_wait_once_the_delay_has_elapsed() -> None:
    time = FakeTime()
    limiter = make_delay(1.0, time)

    limiter.wait("https://example.com/a")
    time.advance(5.0)

    assert limiter.wait("https://example.com/b") == 0.0
    assert time.sleeps == []


def test_different_hosts_do_not_block_each_other() -> None:
    time = FakeTime()
    limiter = make_delay(1.0, time)

    assert limiter.wait("https://example.com/a") == 0.0
    assert limiter.wait("https://other.com/a") == 0.0
    assert limiter.wait("https://third.com/a") == 0.0
    assert time.sleeps == []


def test_tracks_each_host_separately() -> None:
    time = FakeTime()
    limiter = make_delay(1.0, time)

    limiter.wait("https://example.com/a")
    limiter.wait("https://other.com/a")

    assert limiter.wait("https://example.com/b") == pytest.approx(1.0)
    assert limiter.wait("https://other.com/b") == 0.0


def test_consecutive_requests_each_wait_a_full_delay() -> None:
    time = FakeTime()
    limiter = make_delay(1.0, time)

    for path in ("a", "b", "c"):
        limiter.wait(f"https://example.com/{path}")

    assert time.sleeps == [pytest.approx(1.0), pytest.approx(1.0)]
    assert time.now == pytest.approx(2.0)


def test_a_slow_page_counts_towards_the_delay() -> None:
    """Time spent fetching is time already waited."""
    time = FakeTime()
    limiter = make_delay(1.0, time)

    limiter.wait("https://example.com/a")
    time.advance(1.5)  # the fetch itself took longer than the delay

    assert limiter.wait("https://example.com/b") == 0.0


def test_zero_delay_never_waits() -> None:
    time = FakeTime()
    limiter = make_delay(0.0, time)

    limiter.wait("https://example.com/a")
    limiter.wait("https://example.com/b")

    assert time.sleeps == []


def test_negative_delay_is_rejected() -> None:
    with pytest.raises(ValueError, match="delay"):
        PerDomainDelay(-1.0)


# --- the concurrent limiter -------------------------------------------------


def make_async_delay(delay: float, time: FakeAsyncTime) -> AsyncPerDomainDelay:
    return AsyncPerDomainDelay(delay, sleep=time.sleep, clock=time.monotonic)


def test_async_first_request_does_not_wait() -> None:
    time = FakeAsyncTime()
    limiter = make_async_delay(1.0, time)

    assert asyncio.run(limiter.wait("https://example.com/a")) == 0.0
    assert time.sleeps == []


def test_async_second_request_to_one_host_waits() -> None:
    time = FakeAsyncTime()
    limiter = make_async_delay(1.0, time)

    async def run() -> list[float]:
        return [
            await limiter.wait("https://example.com/a"),
            await limiter.wait("https://example.com/b"),
        ]

    assert asyncio.run(run()) == [0.0, pytest.approx(1.0)]


def test_concurrent_waiters_on_one_host_queue_up() -> None:
    """Each task must take its own slot, not all wake at the same moment.

    This is the difference between recording when a host was last contacted and
    reserving when it may next be contacted. What matters is the moment each
    waiter is released, not how long it individually slept, so the assertion is
    on the clock at release: one delay apart, every time.
    """
    time = FakeAsyncTime()
    limiter = make_async_delay(1.0, time)

    async def released_at(index: int) -> float:
        await limiter.wait(f"https://example.com/{index}")
        return time.monotonic()

    async def run() -> list[float]:
        return list(await asyncio.gather(*(released_at(index) for index in range(3))))

    assert asyncio.run(run()) == [0.0, pytest.approx(1.0), pytest.approx(2.0)]


def test_concurrent_waiters_on_separate_hosts_do_not_queue() -> None:
    time = FakeAsyncTime()
    limiter = make_async_delay(5.0, time)

    async def run() -> list[float]:
        hosts = ["https://a.test/", "https://b.test/", "https://c.test/"]
        return list(await asyncio.gather(*(limiter.wait(host) for host in hosts)))

    assert asyncio.run(run()) == [0.0, 0.0, 0.0]


def test_async_does_not_wait_once_the_delay_has_elapsed() -> None:
    time = FakeAsyncTime()
    limiter = make_async_delay(1.0, time)

    async def run() -> float:
        await limiter.wait("https://example.com/a")
        time.advance(5.0)
        return await limiter.wait("https://example.com/b")

    assert asyncio.run(run()) == 0.0


def test_async_zero_delay_never_waits() -> None:
    time = FakeAsyncTime()
    limiter = make_async_delay(0.0, time)

    async def run() -> None:
        for index in range(5):
            await limiter.wait(f"https://example.com/{index}")

    asyncio.run(run())
    assert time.sleeps == []


def test_async_negative_delay_is_rejected() -> None:
    with pytest.raises(ValueError, match="delay"):
        AsyncPerDomainDelay(-1.0)
