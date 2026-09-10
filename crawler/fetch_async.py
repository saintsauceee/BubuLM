"""Asynchronous HTTP fetching.

``AsyncFetcher`` is the concurrent twin of :class:`crawler.fetch.Fetcher`. It
applies the same rules -- status handling, content-type filtering, the two size
checks, retry and backoff, httpx error translation -- by calling the same
module-level functions; only the I/O model differs.

Nothing here holds cross-request state, so one instance is safe to share
between concurrent tasks. ``httpx.AsyncClient`` is itself designed for
concurrent use and pools connections across them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from types import TracebackType

import httpx

from crawler.config import CrawlerConfig
from crawler.content_types import ensure_accepted_content_type
from crawler.errors import TransientFetchError
from crawler.fetch import (
    HTTPX_FAILURES,
    FetchResult,
    RetryableStatus,
    backoff_delay,
    check_declared_size,
    check_running_size,
    check_status,
    translate_httpx_error,
)

AsyncSleepFunc = Callable[[float], Awaitable[None]]


class AsyncFetcher:
    """Fetches URLs over HTTP/HTTPS concurrently, according to a config."""

    def __init__(
        self,
        config: CrawlerConfig | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: AsyncSleepFunc = asyncio.sleep,
    ) -> None:
        self.config = config or CrawlerConfig()
        self._sleep = sleep
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=self.config.request_timeout,
            follow_redirects=True,
            max_redirects=self.config.max_redirects,
            headers={"User-Agent": self.config.user_agent},
        )

    async def __aenter__(self) -> AsyncFetcher:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying client, if this fetcher created it."""
        if self._owns_client:
            await self._client.aclose()

    def backoff_delay(self, attempt: int) -> float:
        """Return the delay in seconds before ``attempt`` (1-based) is retried."""
        return backoff_delay(self.config, attempt)

    async def fetch(self, url: str) -> FetchResult:
        """Fetch ``url`` and return its body.

        Raises:
            ResponseTooLargeError: the body exceeded ``max_response_bytes``.
            ContentTypeRejectedError: the response was not accepted (X)HTML.
            TransientFetchError: retries were exhausted on a transient failure.
            FetchError: a non-retryable HTTP error.
        """
        last_error: Exception | None = None

        for attempt in range(1, self.config.max_attempts + 1):
            try:
                return await self._fetch_once(url)
            except (RetryableStatus, httpx.TransportError) as error:
                last_error = error
                if attempt < self.config.max_attempts:
                    await self._sleep(self.backoff_delay(attempt))

        raise TransientFetchError(
            f"Failed to fetch {url!r} after {self.config.max_attempts} attempts: {last_error}"
        ) from last_error

    async def _fetch_once(self, url: str) -> FetchResult:
        """Make one attempt, translating httpx failures into crawler errors."""
        try:
            return await self._request(url)
        except HTTPX_FAILURES as error:
            translate_httpx_error(error, url)

    async def _request(self, url: str) -> FetchResult:
        headers = {"User-Agent": self.config.user_agent}
        async with self._client.stream("GET", url, headers=headers) as response:
            check_status(response.status_code, self.config, url)
            content_type, charset = ensure_accepted_content_type(
                response.headers.get("content-type"),
                self.config.accepted_content_types,
            )
            check_declared_size(response.headers, self.config, url)
            body = await self._read_capped(response, url)

        return FetchResult(
            url=str(response.url),
            status_code=response.status_code,
            content_type=content_type,
            charset=charset,
            body=body,
        )

    async def _read_capped(self, response: httpx.Response, url: str) -> bytes:
        """Stream the body, aborting as soon as the limit is exceeded."""
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            check_running_size(total, self.config, url)
            chunks.append(chunk)
        return b"".join(chunks)
