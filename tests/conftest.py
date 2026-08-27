"""Shared test helpers.

Everything here is offline: unit tests use an in-process ``httpx.MockTransport``
and the integration tests use a local HTTP server bound to loopback.
"""

from __future__ import annotations

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
