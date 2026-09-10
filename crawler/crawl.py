"""The single-machine crawl loop.

Ties the existing single-page pipeline into a breadth-first walk:

    seeds -> frontier -> fetch -> save raw HTML -> extract links
          -> normalize -> skip seen -> enqueue -> repeat

Everything that decides *whether* a page is worth having already lives in
``Fetcher`` (retries, content-type filtering, size limits), and everything that
decides whether a URL is worth visiting lives in ``Frontier``. This module only
sequences them and enforces the crawl budget.

Text extraction and quality filtering are deliberately not run here: this stage
stores raw bytes, and turning bytes into training documents belongs to the
corpus stage.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType

from crawler.config import CrawlerConfig
from crawler.errors import CrawlError
from crawler.extract import decode_html
from crawler.fetch import Fetcher
from crawler.frontier import Frontier, hosts_of
from crawler.links import extract_links
from crawler.politeness import PerDomainDelay
from crawler.storage import RawHtmlStore


@dataclass(frozen=True, slots=True)
class CrawlSettings:
    """Budget and politeness limits for one crawl."""

    max_pages: int = 50
    """Stop after this many pages have been stored."""

    max_depth: int = 2
    """Seeds are depth 0; a value of 0 crawls the seeds only."""

    delay_per_domain: float = 1.0
    """Minimum seconds between two requests to the same host."""

    follow_external_links: bool = False
    """When false, the crawl stays on the hosts named by the seeds."""

    def __post_init__(self) -> None:
        if self.max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        if self.max_depth < 0:
            raise ValueError("max_depth must not be negative")
        if self.delay_per_domain < 0:
            raise ValueError("delay_per_domain must not be negative")


@dataclass(frozen=True, slots=True)
class CrawledPage:
    """A page that was fetched and stored."""

    url: str
    depth: int
    path: Path
    content_type: str
    size_bytes: int
    links_found: int


@dataclass(frozen=True, slots=True)
class CrawlFailure:
    """A URL that could not be crawled. The crawl continues past these."""

    url: str
    depth: int
    reason: str
    """The name of the CrawlError subclass that rejected the URL."""

    detail: str


@dataclass(slots=True)
class CrawlReport:
    """What one crawl did."""

    pages: list[CrawledPage] = field(default_factory=list[CrawledPage])
    failures: list[CrawlFailure] = field(default_factory=list[CrawlFailure])
    urls_seen: int = 0

    @property
    def pages_crawled(self) -> int:
        return len(self.pages)

    @property
    def bytes_stored(self) -> int:
        return sum(page.size_bytes for page in self.pages)


class SiteCrawler:
    """Crawls outward from one or more seed URLs, storing raw HTML."""

    def __init__(
        self,
        store: RawHtmlStore,
        settings: CrawlSettings | None = None,
        config: CrawlerConfig | None = None,
        *,
        fetcher: Fetcher | None = None,
        politeness: PerDomainDelay | None = None,
    ) -> None:
        self.store = store
        self.settings = settings or CrawlSettings()
        self.config = config or CrawlerConfig()
        self._owns_fetcher = fetcher is None
        self._fetcher = fetcher or Fetcher(self.config)
        self._politeness = politeness or PerDomainDelay(self.settings.delay_per_domain)

    def __enter__(self) -> SiteCrawler:
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

    def crawl(self, seeds: Iterable[str]) -> CrawlReport:
        """Crawl breadth-first from ``seeds`` until the budget is spent."""
        seed_list = list(seeds)
        frontier = Frontier(
            max_depth=self.settings.max_depth,
            allowed_hosts=None if self.settings.follow_external_links else hosts_of(seed_list),
        )
        frontier.add_seeds(seed_list)

        report = CrawlReport()
        while report.pages_crawled < self.settings.max_pages:
            item = frontier.pop()
            if item is None:
                break
            self._visit(item.url, item.depth, frontier, report)

        report.urls_seen = frontier.seen_count
        return report

    def _visit(self, url: str, depth: int, frontier: Frontier, report: CrawlReport) -> None:
        """Fetch, store, and enqueue the links of one page.

        A failure here is recorded and swallowed: one bad page must not end the
        crawl.
        """
        self._politeness.wait(url)

        try:
            response = self._fetcher.fetch(url)
        except CrawlError as error:
            report.failures.append(
                CrawlFailure(url=url, depth=depth, reason=type(error).__name__, detail=str(error))
            )
            return

        path = self.store.save(response.url, response.body)

        html = decode_html(response.body, response.charset)
        links = extract_links(html, response.url)
        for link in links:
            frontier.add(link, depth + 1)

        report.pages.append(
            CrawledPage(
                url=response.url,
                depth=depth,
                path=path,
                content_type=response.content_type,
                size_bytes=response.size_bytes,
                links_found=len(links),
            )
        )
