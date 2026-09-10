"""Tests for the concurrent crawl loop.

The focus is correctness under concurrency: that shared state cannot be
corrupted, that no URL is scheduled twice, that the page budget is not
overshot, and that one bad page cannot take down a worker or the crawl.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from crawler.config import CrawlerConfig
from crawler.crawl import CrawlReport, CrawlSettings
from crawler.crawl_async import AsyncSiteCrawler
from crawler.fetch import FetchResult
from crawler.fetch_async import AsyncFetcher
from crawler.politeness import AsyncPerDomainDelay
from crawler.storage import RawHtmlStore
from tests.conftest import AsyncSiteTransport

Pages = dict[str, str]


def link(path: str) -> str:
    return f'<a href="{path}">x</a>'


def make_crawler(
    transport: AsyncSiteTransport,
    tmp_path: Path,
    settings: CrawlSettings,
    *,
    politeness: AsyncPerDomainDelay | None = None,
) -> AsyncSiteCrawler:
    client = httpx.AsyncClient(transport=transport, follow_redirects=True)
    return AsyncSiteCrawler(
        RawHtmlStore(tmp_path),
        settings,
        fetcher=AsyncFetcher(CrawlerConfig(), client=client),
        politeness=politeness or AsyncPerDomainDelay(0.0),
    )


def run_crawl(
    pages: Pages,
    tmp_path: Path,
    settings: CrawlSettings,
    *,
    latency: float = 0.0,
    seeds: list[str] | None = None,
) -> tuple[CrawlReport, AsyncSiteTransport]:
    transport = AsyncSiteTransport(pages, latency=latency)
    crawler = make_crawler(transport, tmp_path, settings)
    report = asyncio.run(crawler.crawl(seeds or ["https://example.com/"]))
    return report, transport


def mesh(size: int) -> Pages:
    """A densely interlinked site: every page links to every other page."""
    all_links = "".join(link(f"/p{index}") for index in range(size))
    return {f"/p{index}": all_links for index in range(size)}


# --- the walk still behaves like the sequential crawler ----------------------


def test_crawls_a_single_seed(tmp_path: Path) -> None:
    report, _ = run_crawl({"/": "<p>Home</p>"}, tmp_path, CrawlSettings(concurrency=4))

    assert report.pages_crawled == 1
    assert report.pages[0].url == "https://example.com/"


def test_follows_links(tmp_path: Path) -> None:
    pages = {"/": link("/a") + link("/b"), "/a": "<p>A</p>", "/b": "<p>B</p>"}
    report, _ = run_crawl(pages, tmp_path, CrawlSettings(concurrency=4))

    assert {page.url for page in report.pages} == {
        "https://example.com/",
        "https://example.com/a",
        "https://example.com/b",
    }


def test_respects_max_depth(tmp_path: Path) -> None:
    pages = {"/": link("/a"), "/a": link("/b"), "/b": link("/c"), "/c": "<p>C</p>"}
    report, _ = run_crawl(pages, tmp_path, CrawlSettings(max_depth=1, concurrency=4))

    assert {page.url for page in report.pages} == {
        "https://example.com/",
        "https://example.com/a",
    }


def test_stores_raw_html(tmp_path: Path) -> None:
    pages = {"/": link("/a"), "/a": "<p>A</p>"}
    report, _ = run_crawl(pages, tmp_path, CrawlSettings(concurrency=4))

    assert len(list(tmp_path.rglob("*.html"))) == 2
    assert all(page.path.is_file() for page in report.pages)


def test_stays_on_the_seed_host(tmp_path: Path) -> None:
    pages = {"/": link("https://elsewhere.test/x") + link("/a"), "/a": "<p>A</p>"}
    report, _ = run_crawl(pages, tmp_path, CrawlSettings(concurrency=4))

    assert all("example.com" in page.url for page in report.pages)


def test_matches_the_sequential_crawler(tmp_path: Path) -> None:
    """Same site, same settings: concurrency changes speed, not the result."""
    from crawler.crawl import SiteCrawler
    from tests.conftest import make_fetcher as make_sync_fetcher

    pages = {"/": link("/a") + link("/b"), "/a": link("/c"), "/b": "<p>B</p>", "/c": "<p>C</p>"}

    def handler(request: httpx.Request) -> httpx.Response:
        body = pages.get(request.url.path)
        if body is None:
            return httpx.Response(404, content=b"", headers={"content-type": "text/html"})
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/html"})

    settings = CrawlSettings(max_depth=5, delay_per_domain=0.0, concurrency=4)
    sequential = SiteCrawler(
        RawHtmlStore(tmp_path / "sync"), settings, fetcher=make_sync_fetcher(handler)
    ).crawl(["https://example.com/"])
    concurrent, _ = run_crawl(pages, tmp_path / "async", settings)

    assert {page.url for page in sequential.pages} == {page.url for page in concurrent.pages}
    assert sequential.urls_seen == concurrent.urls_seen


# --- concurrency limit ------------------------------------------------------


@pytest.mark.parametrize("concurrency", [1, 2, 3, 5])
def test_never_exceeds_the_concurrency_limit(tmp_path: Path, concurrency: int) -> None:
    settings = CrawlSettings(max_pages=30, max_depth=3, concurrency=concurrency)
    _, transport = run_crawl(
        mesh(20), tmp_path, settings, latency=0.01, seeds=["https://example.com/p0"]
    )

    assert transport.max_in_flight <= concurrency


def test_actually_uses_the_available_concurrency(tmp_path: Path) -> None:
    """With work available, the pool should saturate."""
    settings = CrawlSettings(max_pages=30, max_depth=3, concurrency=5)
    _, transport = run_crawl(
        mesh(20), tmp_path, settings, latency=0.01, seeds=["https://example.com/p0"]
    )

    assert transport.max_in_flight == 5


def test_concurrency_of_one_is_sequential(tmp_path: Path) -> None:
    settings = CrawlSettings(max_pages=10, max_depth=3, concurrency=1)
    _, transport = run_crawl(
        mesh(8), tmp_path, settings, latency=0.005, seeds=["https://example.com/p0"]
    )

    assert transport.max_in_flight == 1


def test_concurrency_must_be_positive() -> None:
    with pytest.raises(ValueError, match="concurrency"):
        CrawlSettings(concurrency=0)


# --- no duplicate scheduling ------------------------------------------------


@pytest.mark.parametrize("concurrency", [2, 4, 8, 16])
def test_no_url_is_fetched_twice(tmp_path: Path, concurrency: int) -> None:
    """A dense mesh gives every worker the same URLs to race over."""
    settings = CrawlSettings(max_pages=50, max_depth=4, concurrency=concurrency)
    report, transport = run_crawl(
        mesh(20), tmp_path, settings, latency=0.001, seeds=["https://example.com/p0"]
    )

    assert len(transport.requests) == len(set(transport.requests))
    urls = [page.url for page in report.pages]
    assert len(urls) == len(set(urls))


def test_duplicate_seeds_are_scheduled_once(tmp_path: Path) -> None:
    seeds = ["https://example.com/", "https://example.com/", "HTTPS://EXAMPLE.COM/?utm_source=x"]
    report, transport = run_crawl(
        {"/": "<p>Home</p>"}, tmp_path, CrawlSettings(concurrency=8), seeds=seeds
    )

    assert report.pages_crawled == 1
    assert len(transport.requests) == 1


def test_a_cycle_terminates_under_concurrency(tmp_path: Path) -> None:
    pages = {"/a": link("/b"), "/b": link("/c"), "/c": link("/a")}
    report, _ = run_crawl(
        pages,
        tmp_path,
        CrawlSettings(max_depth=99, concurrency=8),
        latency=0.001,
        seeds=["https://example.com/a"],
    )

    assert report.pages_crawled == 3


def test_every_page_is_stored_exactly_once(tmp_path: Path) -> None:
    settings = CrawlSettings(max_pages=20, max_depth=3, concurrency=8)
    report, _ = run_crawl(
        mesh(15), tmp_path, settings, latency=0.001, seeds=["https://example.com/p0"]
    )

    assert len(list(tmp_path.rglob("*.html"))) == report.pages_crawled


# --- the page budget is not overshot ----------------------------------------


@pytest.mark.parametrize("concurrency", [2, 4, 8, 16])
def test_max_pages_is_never_overshot(tmp_path: Path, concurrency: int) -> None:
    """Workers in flight must not each slip past the budget check."""
    settings = CrawlSettings(max_pages=5, max_depth=99, concurrency=concurrency)
    report, transport = run_crawl(
        mesh(40), tmp_path, settings, latency=0.002, seeds=["https://example.com/p0"]
    )

    assert report.pages_crawled == 5
    assert len(transport.requests) == 5  # no wasted fetches beyond the budget
    assert len(list(tmp_path.rglob("*.html"))) == 5


def test_failures_do_not_consume_the_page_budget(tmp_path: Path) -> None:
    """max_pages counts stored pages, as it does in the sequential crawler.

    The seed lists five broken links *before* four good ones, so the failures
    are attempted first. If a failed fetch kept its reserved slot, the budget
    would be spent before any of the good pages were reached.
    """
    broken = "".join(link(f"/missing{index}") for index in range(5))
    good = "".join(link(f"/p{index}") for index in range(1, 5))
    pages = {"/p0": broken + good}
    pages.update({f"/p{index}": "<p>ok</p>" for index in range(1, 5)})

    settings = CrawlSettings(max_pages=5, max_depth=2, concurrency=4)
    report, _ = run_crawl(pages, tmp_path, settings, seeds=["https://example.com/p0"])

    assert report.pages_crawled == 5  # the seed plus all four good pages
    assert len(report.failures) == 5


# --- failure isolation ------------------------------------------------------


def test_a_failed_page_does_not_stop_the_crawl(tmp_path: Path) -> None:
    pages = {"/": link("/missing") + link("/a"), "/a": "<p>A</p>"}
    report, _ = run_crawl(pages, tmp_path, CrawlSettings(concurrency=4))

    assert report.pages_crawled == 2
    assert [failure.reason for failure in report.failures] == ["FetchError"]


def test_many_failures_do_not_stop_the_crawl(tmp_path: Path) -> None:
    pages = {"/p0": "".join(link(f"/missing{index}") for index in range(20)) + link("/p1")}
    pages["/p1"] = "<p>survivor</p>"
    settings = CrawlSettings(max_pages=10, max_depth=2, concurrency=8)
    report, _ = run_crawl(pages, tmp_path, settings, seeds=["https://example.com/p0"])

    assert {page.url for page in report.pages} == {
        "https://example.com/p0",
        "https://example.com/p1",
    }
    assert len(report.failures) == 20


class ExplodingFetcher(AsyncFetcher):
    """Raises a non-CrawlError for one URL, to test the worker backstop."""

    def __init__(self, client: httpx.AsyncClient, bad_path: str) -> None:
        super().__init__(CrawlerConfig(), client=client)
        self.bad_path = bad_path

    async def fetch(self, url: str) -> FetchResult:
        if url.endswith(self.bad_path):
            raise RuntimeError("unexpected failure inside a worker")
        return await super().fetch(url)


def test_an_unexpected_exception_does_not_kill_the_pool(tmp_path: Path) -> None:
    """A bug in one page must not silently shrink the worker pool or hang."""
    pages = {"/": link("/boom") + link("/a") + link("/b"), "/a": "<p>A</p>", "/b": "<p>B</p>"}
    pages["/boom"] = "<p>never read</p>"

    transport = AsyncSiteTransport(pages)
    client = httpx.AsyncClient(transport=transport)
    crawler = AsyncSiteCrawler(
        RawHtmlStore(tmp_path),
        CrawlSettings(concurrency=2),
        fetcher=ExplodingFetcher(client, "/boom"),
        politeness=AsyncPerDomainDelay(0.0),
    )
    report = asyncio.run(crawler.crawl(["https://example.com/"]))

    assert {page.url for page in report.pages} == {
        "https://example.com/",
        "https://example.com/a",
        "https://example.com/b",
    }
    assert [failure.reason for failure in report.failures] == ["RuntimeError"]


def test_a_crawl_with_only_failures_terminates(tmp_path: Path) -> None:
    report, _ = run_crawl({}, tmp_path, CrawlSettings(concurrency=8))

    assert report.pages_crawled == 0
    assert len(report.failures) == 1


# --- politeness under concurrency -------------------------------------------


def test_requests_to_one_host_are_spaced(tmp_path: Path) -> None:
    """Concurrency must not let workers bypass the per-host delay."""
    delay = 0.05
    pages = {"/p0": link("/p1") + link("/p2"), "/p1": "<p>1</p>", "/p2": "<p>2</p>"}
    transport = AsyncSiteTransport(pages)
    crawler = make_crawler(
        transport,
        tmp_path,
        CrawlSettings(delay_per_domain=delay, concurrency=8),
        politeness=AsyncPerDomainDelay(delay),
    )
    asyncio.run(crawler.crawl(["https://example.com/p0"]))

    starts = sorted(when for _, when in transport.started_at)
    gaps = [later - earlier for earlier, later in zip(starts, starts[1:], strict=False)]
    assert all(gap >= delay * 0.9 for gap in gaps), gaps


def test_separate_hosts_are_not_serialized(tmp_path: Path) -> None:
    """Two hosts with a long delay each should still be fetched together."""
    pages = {"/": "<p>page</p>"}
    transport = AsyncSiteTransport(pages, latency=0.01)
    crawler = make_crawler(
        transport,
        tmp_path,
        CrawlSettings(delay_per_domain=5.0, max_depth=0, concurrency=4),
        politeness=AsyncPerDomainDelay(5.0),
    )
    report = asyncio.run(crawler.crawl(["https://one.example.com/", "https://two.example.com/"]))

    assert report.pages_crawled == 2
    assert transport.max_in_flight == 2
