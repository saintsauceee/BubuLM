"""Exception hierarchy for the crawler.

Every failure mode the pipeline can reject a URL for has a distinct type so
callers (and, later, distributed workers) can decide what to record, what to
drop, and what to re-queue.
"""

from __future__ import annotations


class CrawlError(Exception):
    """Base class for every crawler failure."""


class InvalidURLError(CrawlError):
    """The URL is malformed or uses an unsupported scheme."""


class FetchError(CrawlError):
    """The page could not be retrieved."""


class TransientFetchError(FetchError):
    """A retryable failure: a timeout, a connection drop, or a 5xx response.

    Raised only once retries have been exhausted.
    """


class ContentTypeRejectedError(CrawlError):
    """The response is not an accepted (X)HTML content type."""


class ResponseTooLargeError(CrawlError):
    """The response body exceeds the configured size limit."""


class QualityRejectedError(CrawlError):
    """The extracted text failed the quality filters."""
