"""Tests for per-domain rate limiting."""

from __future__ import annotations

import pytest

from crawler.politeness import PerDomainDelay
from tests.conftest import FakeTime


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
