"""The single-worker crawl pipeline.

Wires the components together in one place:

    normalize URL -> fetch -> decode -> extract -> quality check -> hash

Each stage is an independent module, so a distributed worker can later reuse
them without taking this orchestration along.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType

from crawler.config import CrawlerConfig
from crawler.extract import decode_html, extract_text
from crawler.fetch import Fetcher
from crawler.hashing import content_hash
from crawler.quality import check_text_quality
from crawler.urls import normalize_url


@dataclass(frozen=True, slots=True)
class CrawlDocument:
    """A clean, validated training document produced from one URL."""

    url: str
    """The normalized URL that was requested."""

    final_url: str
    """The URL the response came from, after redirects."""

    title: str | None
    text: str
    content_hash: str
    content_type: str
    fetched_bytes: int

    @property
    def text_length(self) -> int:
        return len(self.text)


class Crawler:
    """Crawls one URL at a time and returns a :class:`CrawlDocument`."""

    def __init__(
        self,
        config: CrawlerConfig | None = None,
        *,
        fetcher: Fetcher | None = None,
    ) -> None:
        self.config = config or CrawlerConfig()
        self._owns_fetcher = fetcher is None
        self._fetcher = fetcher or Fetcher(self.config)

    def __enter__(self) -> Crawler:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying fetcher, if this crawler created it."""
        if self._owns_fetcher:
            self._fetcher.close()

    def crawl(self, url: str) -> CrawlDocument:
        """Fetch ``url`` and return a validated document.

        Raises:
            CrawlError: any subclass, naming the stage that rejected the URL.
        """
        normalized = normalize_url(url)
        response = self._fetcher.fetch(normalized)

        html = decode_html(response.body, response.charset)
        extracted = extract_text(html)

        check_text_quality(extracted.text, self.config)

        return CrawlDocument(
            url=normalized,
            final_url=response.url,
            title=extracted.title,
            text=extracted.text,
            content_hash=content_hash(extracted.text),
            content_type=response.content_type,
            fetched_bytes=response.size_bytes,
        )
