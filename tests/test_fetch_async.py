"""Tests for the asynchronous fetcher.

Tests are plain functions driving ``asyncio.run``, so the suite needs no
pytest-asyncio plugin.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from crawler.config import CrawlerConfig
from crawler.errors import (
    ContentTypeRejectedError,
    CrawlError,
    FetchError,
    InvalidURLError,
    ResponseTooLargeError,
    TransientFetchError,
)
from crawler.fetch import FetchResult
from crawler.fetch_async import AsyncFetcher

Handler = Callable[[httpx.Request], httpx.Response]


def make_fetcher(
    handler: Handler, config: CrawlerConfig | None = None, *, sleeps: list[float] | None = None
) -> AsyncFetcher:
    recorded = sleeps if sleeps is not None else []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )
    return AsyncFetcher(config or CrawlerConfig(), client=client, sleep=fake_sleep)


def html(body: str = "<p>Hi</p>", status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, content=body.encode(), headers={"content-type": "text/html; charset=utf-8"}
    )


def fetch(fetcher: AsyncFetcher, url: str) -> FetchResult:
    return asyncio.run(fetcher.fetch(url))


def test_fetches_html() -> None:
    result = fetch(make_fetcher(lambda request: html()), "https://example.com/")

    assert result.status_code == 200
    assert result.content_type == "text/html"
    assert result.charset == "utf-8"
    assert b"<p>Hi</p>" in result.body


def test_sends_the_configured_user_agent() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("user-agent", ""))
        return html()

    fetch(make_fetcher(handler, CrawlerConfig(user_agent="BubuLM-Async/1.0")), "https://x.test/")
    assert seen == ["BubuLM-Async/1.0"]


def test_reports_the_final_url_after_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(301, headers={"location": "https://example.com/end"})
        return html()

    assert (
        fetch(make_fetcher(handler), "https://example.com/start").url == "https://example.com/end"
    )


@pytest.mark.parametrize("content_type", ["image/png", "application/pdf", "text/plain"])
def test_rejects_non_html_content_types(content_type: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x", headers={"content-type": content_type})

    with pytest.raises(ContentTypeRejectedError):
        fetch(make_fetcher(handler), "https://example.com/asset")


def test_rejects_a_body_over_the_limit() -> None:
    config = CrawlerConfig(max_response_bytes=100)
    with pytest.raises(ResponseTooLargeError):
        fetch(
            make_fetcher(lambda request: html("<p>" + "x" * 500 + "</p>"), config),
            "https://e.test/",
        )


def test_rejects_a_chunked_body_over_the_limit() -> None:
    """No Content-Length, so only the streaming cap can catch this."""

    def handler(request: httpx.Request) -> httpx.Response:
        async def chunks() -> AsyncIterator[bytes]:
            for _ in range(50):
                yield b"x" * 1000

        return httpx.Response(200, content=chunks(), headers={"content-type": "text/html"})

    config = CrawlerConfig(max_response_bytes=1000)
    with pytest.raises(ResponseTooLargeError, match="exceeds"):
        fetch(make_fetcher(handler, config), "https://example.com/chunked")


def test_rejects_on_declared_content_length() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = b"x" * 5000
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "text/html", "content-length": str(len(body))},
        )

    with pytest.raises(ResponseTooLargeError, match="declares"):
        fetch(make_fetcher(handler, CrawlerConfig(max_response_bytes=100)), "https://e.test/")


@pytest.mark.parametrize("status", [408, 429, 500, 503])
def test_retries_transient_statuses(status: int) -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(status, content=b"", headers={"content-type": "text/html"})
        return html()

    sleeps: list[float] = []
    assert fetch(make_fetcher(handler, sleeps=sleeps), "https://example.com/").status_code == 200
    assert len(attempts) == 3
    assert sleeps == [0.5, 1.0]


def test_gives_up_after_max_attempts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"", headers={"content-type": "text/html"})

    with pytest.raises(TransientFetchError, match="4 attempts"):
        fetch(make_fetcher(handler, CrawlerConfig(max_attempts=4)), "https://example.com/")


def test_retries_transport_errors() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 2:
            raise httpx.ConnectError("refused", request=request)
        return html()

    assert fetch(make_fetcher(handler), "https://example.com/").status_code == 200
    assert len(attempts) == 2


@pytest.mark.parametrize("status", [400, 403, 404, 410])
def test_does_not_retry_permanent_errors(status: int) -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(status, content=b"", headers={"content-type": "text/html"})

    with pytest.raises(FetchError) as caught:
        fetch(make_fetcher(handler), "https://example.com/missing")

    assert not isinstance(caught.value, TransientFetchError)
    assert len(attempts) == 1


def test_backoff_matches_the_sync_fetcher() -> None:
    config = CrawlerConfig(retry_backoff_base=1.0, retry_backoff_max=4.0)
    fetcher = make_fetcher(lambda request: html(), config)

    assert [fetcher.backoff_delay(n) for n in range(1, 6)] == [1.0, 2.0, 4.0, 4.0, 4.0]


def test_no_httpx_exception_escapes_the_crawl_error_hierarchy() -> None:
    failures = [
        httpx.TooManyRedirects("too many"),
        httpx.DecodingError("bad encoding"),
        httpx.InvalidURL("malformed"),
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
    ]
    for failure in failures:

        def handler(request: httpx.Request, error: Exception = failure) -> httpx.Response:
            raise error

        with pytest.raises(CrawlError):
            fetch(make_fetcher(handler, CrawlerConfig(max_attempts=1)), "https://example.com/")


def test_invalid_url_is_translated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.InvalidURL("malformed URL")

    with pytest.raises(InvalidURLError):
        fetch(make_fetcher(handler), "https://example.com/")


def test_closes_the_client_it_created() -> None:
    """After the context exits, the fetcher's own client is unusable."""

    async def run() -> None:
        async with AsyncFetcher(CrawlerConfig()) as fetcher:
            pass
        # httpx refuses on a closed client before it touches the network.
        await fetcher.fetch("https://example.com/")

    with pytest.raises(RuntimeError):
        asyncio.run(run())


def test_leaves_an_injected_client_open() -> None:
    async def run() -> bool:
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: html()))
        async with AsyncFetcher(CrawlerConfig(), client=client):
            pass
        closed = client.is_closed
        await client.aclose()
        return closed

    assert asyncio.run(run()) is False


def test_one_fetcher_serves_concurrent_requests() -> None:
    """A single AsyncFetcher instance holds no per-request state."""

    def handler(request: httpx.Request) -> httpx.Response:
        return html(f"<p>{request.url.path}</p>")

    async def run() -> list[str]:
        fetcher = make_fetcher(handler)
        results = await asyncio.gather(
            *(fetcher.fetch(f"https://example.com/{index}") for index in range(20))
        )
        return [result.body.decode() for result in results]

    bodies = asyncio.run(run())
    assert bodies == [f"<p>/{index}</p>" for index in range(20)]
