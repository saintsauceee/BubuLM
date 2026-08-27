"""HTTP fetching with size limits, content-type filtering, and retries.

``Fetcher`` owns exactly one concern: turning a URL into raw HTML bytes, or
raising a typed error explaining why that was not possible. It accepts an
injected ``httpx.Client`` and ``sleep`` callable so tests stay deterministic
and offline.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType

import httpx

from crawler.config import CrawlerConfig
from crawler.content_types import ensure_accepted_content_type
from crawler.errors import FetchError, ResponseTooLargeError, TransientFetchError

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


class _RetryableStatus(Exception):
    """Internal signal: this status code is worth another attempt."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"Retryable status {status_code}")
        self.status_code = status_code


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
        delay = self.config.retry_backoff_base * (2 ** (attempt - 1))
        return min(delay, self.config.retry_backoff_max)

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
            except (_RetryableStatus, httpx.TransportError) as error:
                last_error = error
                if attempt < self.config.max_attempts:
                    self._sleep(self.backoff_delay(attempt))

        raise TransientFetchError(
            f"Failed to fetch {url!r} after {self.config.max_attempts} attempts: {last_error}"
        ) from last_error

    def _fetch_once(self, url: str) -> FetchResult:
        with self._client.stream("GET", url) as response:
            if response.status_code in self.config.retry_status_codes:
                raise _RetryableStatus(response.status_code)
            if response.status_code >= 400:
                raise FetchError(f"HTTP {response.status_code} for {url!r}")

            content_type, charset = ensure_accepted_content_type(
                response.headers.get("content-type"),
                self.config.accepted_content_types,
            )

            self._reject_if_declared_too_large(response, url)
            body = self._read_capped(response, url)

        return FetchResult(
            url=str(response.url),
            status_code=response.status_code,
            content_type=content_type,
            charset=charset,
            body=body,
        )

    def _reject_if_declared_too_large(self, response: httpx.Response, url: str) -> None:
        """Reject on the advertised Content-Length before downloading anything."""
        declared = response.headers.get("content-length")
        if declared is None:
            return
        try:
            length = int(declared)
        except ValueError:
            return
        if length > self.config.max_response_bytes:
            raise ResponseTooLargeError(
                f"{url!r} declares {length} bytes, limit is {self.config.max_response_bytes}"
            )

    def _read_capped(self, response: httpx.Response, url: str) -> bytes:
        """Stream the body, aborting as soon as the limit is exceeded."""
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > self.config.max_response_bytes:
                raise ResponseTooLargeError(
                    f"{url!r} exceeds the {self.config.max_response_bytes} byte limit"
                )
            chunks.append(chunk)
        return b"".join(chunks)
