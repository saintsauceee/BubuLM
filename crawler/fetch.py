"""HTTP fetching with size limits, content-type filtering, and retries.

``Fetcher`` owns exactly one concern: turning a URL into raw HTML bytes, or
raising a typed error explaining why that was not possible. It accepts an
injected ``httpx.Client`` and ``sleep`` callable so tests stay deterministic
and offline.

The rules that decide *whether* a response is acceptable -- status handling,
the declared-size check, backoff timing, and httpx error translation -- are
module-level functions rather than methods, because ``AsyncFetcher`` in
:mod:`crawler.fetch_async` applies exactly the same rules over a different I/O
model. Only the reading of the body differs between the two.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import NoReturn

import httpx

from crawler.config import CrawlerConfig
from crawler.content_types import ensure_accepted_content_type
from crawler.errors import (
    FetchError,
    InvalidURLError,
    ResponseTooLargeError,
    TransientFetchError,
)

SleepFunc = Callable[[float], None]


@dataclass(frozen=True, slots=True)
class FetchResult:
    """A successfully fetched (X)HTML response body."""

    url: str
    """The final URL, after any redirects."""

    status_code: int
    content_type: str
    charset: str | None
    body: bytes

    @property
    def size_bytes(self) -> int:
        return len(self.body)


class RetryableStatus(Exception):
    """Signal that a status code is worth another attempt.

    Package-internal: it never escapes a fetcher, but both the sync and async
    fetchers raise and catch it, so it cannot be module-private.
    """

    def __init__(self, status_code: int) -> None:
        super().__init__(f"Retryable status {status_code}")
        self.status_code = status_code


def backoff_delay(config: CrawlerConfig, attempt: int) -> float:
    """Return the delay in seconds before ``attempt`` (1-based) is retried."""
    delay = config.retry_backoff_base * (2 ** (attempt - 1))
    return min(delay, config.retry_backoff_max)


def check_status(status_code: int, config: CrawlerConfig, url: str) -> None:
    """Raise if ``status_code`` means the response is not usable.

    Raises:
        RetryableStatus: the status is worth another attempt.
        FetchError: the status is a permanent failure.
    """
    if status_code in config.retry_status_codes:
        raise RetryableStatus(status_code)
    if status_code >= 400:
        raise FetchError(f"HTTP {status_code} for {url!r}")


def check_declared_size(headers: httpx.Headers, config: CrawlerConfig, url: str) -> None:
    """Reject on the advertised Content-Length, before downloading anything."""
    declared = headers.get("content-length")
    if declared is None:
        return
    try:
        length = int(declared)
    except ValueError:
        return
    if length > config.max_response_bytes:
        raise ResponseTooLargeError(
            f"{url!r} declares {length} bytes, limit is {config.max_response_bytes}"
        )


def check_running_size(total: int, config: CrawlerConfig, url: str) -> None:
    """Reject once the bytes read so far exceed the limit."""
    if total > config.max_response_bytes:
        raise ResponseTooLargeError(f"{url!r} exceeds the {config.max_response_bytes} byte limit")


#: The httpx failures a fetch attempt has to translate. Catch these, then hand
#: them to :func:`translate_httpx_error`.
HTTPX_FAILURES = (httpx.HTTPError, httpx.InvalidURL)


def translate_httpx_error(error: Exception, url: str) -> NoReturn:
    """Re-raise an httpx failure as a crawler error.

    Transport errors pass through unchanged, so the caller's retry loop can
    decide whether to try again. Everything else httpx can raise -- redirect
    loops, decoding failures, malformed URLs -- is deterministic, so it becomes
    a permanent failure rather than escaping the CrawlError hierarchy.
    """
    if isinstance(error, httpx.TransportError):
        raise error
    if isinstance(error, httpx.InvalidURL):
        raise InvalidURLError(f"httpx rejected {url!r}: {error}") from error
    raise FetchError(f"Could not fetch {url!r}: {error}") from error


class Fetcher:
    """Fetches URLs over HTTP/HTTPS according to a :class:`CrawlerConfig`."""

    def __init__(
        self,
        config: CrawlerConfig | None = None,
        *,
        client: httpx.Client | None = None,
        sleep: SleepFunc = time.sleep,
    ) -> None:
        self.config = config or CrawlerConfig()
        self._sleep = sleep
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=self.config.request_timeout,
            follow_redirects=True,
            max_redirects=self.config.max_redirects,
            headers={"User-Agent": self.config.user_agent},
        )

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying client, if this fetcher created it."""
        if self._owns_client:
            self._client.close()

    def backoff_delay(self, attempt: int) -> float:
        """Return the delay in seconds before ``attempt`` (1-based) is retried."""
        return backoff_delay(self.config, attempt)

    def fetch(self, url: str) -> FetchResult:
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
                return self._fetch_once(url)
            except (RetryableStatus, httpx.TransportError) as error:
                last_error = error
                if attempt < self.config.max_attempts:
                    self._sleep(self.backoff_delay(attempt))

        raise TransientFetchError(
            f"Failed to fetch {url!r} after {self.config.max_attempts} attempts: {last_error}"
        ) from last_error

    def _fetch_once(self, url: str) -> FetchResult:
        """Make one attempt, translating httpx failures into crawler errors."""
        try:
            return self._request(url)
        except HTTPX_FAILURES as error:
            translate_httpx_error(error, url)

    def _request(self, url: str) -> FetchResult:
        headers = {"User-Agent": self.config.user_agent}
        with self._client.stream("GET", url, headers=headers) as response:
            check_status(response.status_code, self.config, url)
            content_type, charset = ensure_accepted_content_type(
                response.headers.get("content-type"),
                self.config.accepted_content_types,
            )
            check_declared_size(response.headers, self.config, url)
            body = self._read_capped(response, url)

        return FetchResult(
            url=str(response.url),
            status_code=response.status_code,
            content_type=content_type,
            charset=charset,
            body=body,
        )

    def _read_capped(self, response: httpx.Response, url: str) -> bytes:
        """Stream the body, aborting as soon as the limit is exceeded."""
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            check_running_size(total, self.config, url)
            chunks.append(chunk)
        return b"".join(chunks)
