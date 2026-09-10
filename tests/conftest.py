"""Shared test helpers.

Everything here is offline: unit tests use an in-process ``httpx.MockTransport``
and the integration tests use a local HTTP server bound to loopback.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx

from crawler.config import CrawlerConfig
from crawler.fetch import Fetcher

Handler = Callable[[httpx.Request], httpx.Response]


def make_client(handler: Handler) -> httpx.Client:
    """Return an httpx client whose requests are served by ``handler``."""
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def make_fetcher(
    handler: Handler,
    config: CrawlerConfig | None = None,
    *,
    sleeps: list[float] | None = None,
) -> Fetcher:
    """Return a Fetcher backed by ``handler``, recording backoff sleeps.

    Sleeping is captured rather than performed, so retry tests are instant and
    deterministic.
    """
    recorded = sleeps if sleeps is not None else []
    return Fetcher(
        config or CrawlerConfig(),
        client=make_client(handler),
        sleep=recorded.append,
    )


def html_page(body: str, *, title: str = "Test Page") -> str:
    """Wrap ``body`` in a minimal HTML document."""
    return f"<html><head><title>{title}</title></head><body>{body}</body></html>"


def html_response(html: str, *, status_code: int = 200) -> httpx.Response:
    """Build an HTML response with a correct Content-Type."""
    return httpx.Response(
        status_code,
        content=html.encode("utf-8"),
        headers={"content-type": "text/html; charset=utf-8"},
    )


def long_prose(paragraphs: int = 6) -> str:
    """Return prose long enough to clear the default quality filters."""
    sentence = (
        "The crawler collects readable prose from the public web so that the "
        "language model has clean training documents to learn from. "
    )
    return "".join(f"<p>{sentence * 2}</p>" for _ in range(paragraphs))


class FakeTime:
    """A clock whose sleep advances time, as a real one does.

    Pairing a fake sleep with the real ``time.monotonic`` would let the rate
    limiter's projected wake-up time drift ahead of the clock, so the two must
    be faked together.
    """

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        """Simulate time passing for reasons other than sleeping."""
        self.now += seconds


class FakeAsyncTime:
    """The async counterpart of :class:`FakeTime`: sleeping advances the clock."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        """Simulate time passing for reasons other than sleeping."""
        self.now += seconds


class AsyncSiteTransport(httpx.AsyncBaseTransport):
    """An async transport serving a fixed path -> HTML map.

    Unlike ``httpx.MockTransport``, whose handler is synchronous, this one
    awaits inside the request. That gives concurrent tasks a real interleaving
    point, which is what the concurrency tests need to observe. It also records
    how many requests were in flight at once and how often each URL was
    requested.
    """

    def __init__(self, pages: dict[str, str], *, latency: float = 0.0) -> None:
        self.pages = pages
        self.latency = latency
        self.in_flight = 0
        self.max_in_flight = 0
        self.requests: list[str] = []
        self.started_at: list[tuple[str, float]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        self.started_at.append((str(request.url), asyncio.get_running_loop().time()))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.latency)
            body = self.pages.get(request.url.path)
            if body is None:
                return httpx.Response(
                    404, content=b"missing", headers={"content-type": "text/html"}
                )
            return httpx.Response(
                200,
                content=body.encode(),
                headers={"content-type": "text/html; charset=utf-8"},
            )
        finally:
            self.in_flight -= 1

    def count_for(self, url: str) -> int:
        return self.requests.count(url)
