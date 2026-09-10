"""Tests for the crawl loop."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from crawler.config import CrawlerConfig
from crawler.crawl import CrawlSettings, SiteCrawler
from crawler.politeness import PerDomainDelay
from crawler.storage import RawHtmlStore
from tests.conftest import FakeTime, make_fetcher

Pages = dict[str, str]

FAST = CrawlSettings(delay_per_domain=0.0)


def make_crawler(
    pages: Pages,
    tmp_path: Path,
    settings: CrawlSettings = FAST,
    *,
    clock: FakeTime | None = None,
) -> SiteCrawler:
    def handler(request: httpx.Request) -> httpx.Response:
        body = pages.get(request.url.path)
        if body is None:
            return httpx.Response(404, content=b"missing", headers={"content-type": "text/html"})
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "text/html; charset=utf-8"}
        )

    time = clock or FakeTime()
    return SiteCrawler(
        RawHtmlStore(tmp_path),
        settings,
        fetcher=make_fetcher(handler, CrawlerConfig()),
        politeness=PerDomainDelay(
            settings.delay_per_domain, sleep=time.sleep, clock=time.monotonic
        ),
    )


def link(path: str, text: str = "x") -> str:
    return f'<a href="{path}">{text}</a>'


# --- the basic walk ---------------------------------------------------------


def test_crawls_a_single_seed(tmp_path: Path) -> None:
    crawler = make_crawler({"/": "<p>Home</p>"}, tmp_path)
    report = crawler.crawl(["https://example.com/"])

    assert report.pages_crawled == 1
    assert report.pages[0].url == "https://example.com/"
    assert report.pages[0].depth == 0
    assert report.failures == []


def test_follows_links_to_discovered_pages(tmp_path: Path) -> None:
    pages = {"/": link("/a") + link("/b"), "/a": "<p>A</p>", "/b": "<p>B</p>"}
    report = make_crawler(pages, tmp_path).crawl(["https://example.com/"])

    assert {page.url for page in report.pages} == {
        "https://example.com/",
        "https://example.com/a",
        "https://example.com/b",
    }


def test_records_discovery_depth(tmp_path: Path) -> None:
    pages = {"/": link("/a"), "/a": link("/b"), "/b": "<p>B</p>"}
    report = make_crawler(pages, tmp_path, CrawlSettings(delay_per_domain=0.0, max_depth=5)).crawl(
        ["https://example.com/"]
    )

    depths = {page.url.rsplit("/", 1)[-1]: page.depth for page in report.pages}
    assert depths == {"": 0, "a": 1, "b": 2}


def test_crawls_breadth_first(tmp_path: Path) -> None:
    pages = {
        "/": link("/a") + link("/b"),
        "/a": link("/a-child"),
        "/b": "<p>B</p>",
        "/a-child": "<p>deep</p>",
    }
    report = make_crawler(pages, tmp_path, CrawlSettings(delay_per_domain=0.0, max_depth=5)).crawl(
        ["https://example.com/"]
    )

    order = [page.url for page in report.pages]
    assert order.index("https://example.com/b") < order.index("https://example.com/a-child")


def test_accepts_multiple_seeds(tmp_path: Path) -> None:
    pages = {"/one": "<p>1</p>", "/two": "<p>2</p>"}
    report = make_crawler(pages, tmp_path).crawl(
        ["https://example.com/one", "https://example.com/two"]
    )

    assert report.pages_crawled == 2


def test_counts_links_found_on_each_page(tmp_path: Path) -> None:
    pages = {"/": link("/a") + link("/b"), "/a": "<p>A</p>", "/b": "<p>B</p>"}
    report = make_crawler(pages, tmp_path).crawl(["https://example.com/"])

    home = next(page for page in report.pages if page.url == "https://example.com/")
    assert home.links_found == 2


# --- duplicates and cycles --------------------------------------------------


def test_does_not_crawl_a_url_twice(tmp_path: Path) -> None:
    pages = {
        "/": link("/a") + link("/a") + link("/a?utm_source=x") + link("/a#frag"),
        "/a": "<p>A</p>",
    }
    report = make_crawler(pages, tmp_path).crawl(["https://example.com/"])

    assert report.pages_crawled == 2
    assert [page.url for page in report.pages].count("https://example.com/a") == 1


def test_a_two_page_cycle_terminates(tmp_path: Path) -> None:
    pages = {"/a": link("/b"), "/b": link("/a")}
    report = make_crawler(pages, tmp_path, CrawlSettings(delay_per_domain=0.0, max_depth=99)).crawl(
        ["https://example.com/a"]
    )

    assert report.pages_crawled == 2


def test_a_self_link_terminates(tmp_path: Path) -> None:
    report = make_crawler({"/a": link("/a")}, tmp_path).crawl(["https://example.com/a"])

    assert report.pages_crawled == 1


def test_a_larger_cycle_terminates(tmp_path: Path) -> None:
    pages = {"/a": link("/b"), "/b": link("/c"), "/c": link("/d"), "/d": link("/a")}
    report = make_crawler(pages, tmp_path, CrawlSettings(delay_per_domain=0.0, max_depth=99)).crawl(
        ["https://example.com/a"]
    )

    assert report.pages_crawled == 4


def test_duplicate_seeds_are_crawled_once(tmp_path: Path) -> None:
    report = make_crawler({"/": "<p>Home</p>"}, tmp_path).crawl(
        ["https://example.com/", "https://example.com/", "HTTPS://EXAMPLE.COM/"]
    )

    assert report.pages_crawled == 1


# --- budget limits ----------------------------------------------------------


def test_respects_max_pages(tmp_path: Path) -> None:
    pages = {
        f"/p{index}": "".join(link(f"/p{other}") for other in range(20)) for index in range(20)
    }
    settings = CrawlSettings(max_pages=5, max_depth=99, delay_per_domain=0.0)
    report = make_crawler(pages, tmp_path, settings).crawl(["https://example.com/p0"])

    assert report.pages_crawled == 5


def test_max_pages_of_one_crawls_only_the_seed(tmp_path: Path) -> None:
    pages = {"/": link("/a"), "/a": "<p>A</p>"}
    settings = CrawlSettings(max_pages=1, delay_per_domain=0.0)
    report = make_crawler(pages, tmp_path, settings).crawl(["https://example.com/"])

    assert report.pages_crawled == 1
    assert report.pages[0].url == "https://example.com/"


def test_respects_max_depth(tmp_path: Path) -> None:
    pages = {"/": link("/a"), "/a": link("/b"), "/b": link("/c"), "/c": "<p>C</p>"}
    settings = CrawlSettings(max_depth=1, delay_per_domain=0.0)
    report = make_crawler(pages, tmp_path, settings).crawl(["https://example.com/"])

    assert {page.url for page in report.pages} == {
        "https://example.com/",
        "https://example.com/a",
    }


def test_max_depth_zero_crawls_only_seeds(tmp_path: Path) -> None:
    pages = {"/": link("/a"), "/a": "<p>A</p>"}
    settings = CrawlSettings(max_depth=0, delay_per_domain=0.0)
    report = make_crawler(pages, tmp_path, settings).crawl(["https://example.com/"])

    assert report.pages_crawled == 1


def test_stops_when_the_frontier_empties(tmp_path: Path) -> None:
    settings = CrawlSettings(max_pages=100, delay_per_domain=0.0)
    report = make_crawler({"/": "<p>No links</p>"}, tmp_path, settings).crawl(
        ["https://example.com/"]
    )

    assert report.pages_crawled == 1


def test_max_pages_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_pages"):
        CrawlSettings(max_pages=0)


def test_max_depth_must_not_be_negative() -> None:
    with pytest.raises(ValueError, match="max_depth"):
        CrawlSettings(max_depth=-1)


def test_delay_must_not_be_negative() -> None:
    with pytest.raises(ValueError, match="delay_per_domain"):
        CrawlSettings(delay_per_domain=-1.0)


# --- host scope -------------------------------------------------------------


def test_stays_on_the_seed_host_by_default(tmp_path: Path) -> None:
    pages = {"/": link("https://elsewhere.com/page") + link("/a"), "/a": "<p>A</p>"}
    report = make_crawler(pages, tmp_path).crawl(["https://example.com/"])

    assert all("example.com" in page.url for page in report.pages)
    assert report.pages_crawled == 2


def multi_host_crawler(tmp_path: Path, settings: CrawlSettings) -> SiteCrawler:
    """A crawler over two hosts: example.com links out to elsewhere.com."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            body = b'<a href="https://elsewhere.com/page">out</a>'
        else:
            body = b"<p>external page</p>"
        return httpx.Response(200, content=body, headers={"content-type": "text/html"})

    return SiteCrawler(
        RawHtmlStore(tmp_path),
        settings,
        fetcher=make_fetcher(handler),
        politeness=PerDomainDelay(0.0),
    )


def test_follows_external_links_when_enabled(tmp_path: Path) -> None:
    settings = CrawlSettings(follow_external_links=True, delay_per_domain=0.0)
    report = multi_host_crawler(tmp_path, settings).crawl(["https://example.com/"])

    assert {page.url for page in report.pages} == {
        "https://example.com/",
        "https://elsewhere.com/page",
    }


def test_does_not_follow_external_links_by_default(tmp_path: Path) -> None:
    report = multi_host_crawler(tmp_path, FAST).crawl(["https://example.com/"])

    assert [page.url for page in report.pages] == ["https://example.com/"]


def test_each_seed_host_is_in_scope(tmp_path: Path) -> None:
    """Seeding both hosts puts both in scope, without --follow-external."""
    report = multi_host_crawler(tmp_path, FAST).crawl(
        ["https://example.com/", "https://elsewhere.com/page"]
    )

    assert report.pages_crawled == 2


# --- failures ---------------------------------------------------------------


def test_a_failed_fetch_does_not_stop_the_crawl(tmp_path: Path) -> None:
    pages = {"/": link("/missing") + link("/a"), "/a": "<p>A</p>"}
    report = make_crawler(pages, tmp_path).crawl(["https://example.com/"])

    assert report.pages_crawled == 2
    assert len(report.failures) == 1
    assert report.failures[0].reason == "FetchError"
    assert report.failures[0].url == "https://example.com/missing"


def test_a_failed_seed_does_not_stop_the_crawl(tmp_path: Path) -> None:
    report = make_crawler({"/ok": "<p>Fine</p>"}, tmp_path).crawl(
        ["https://example.com/missing", "https://example.com/ok"]
    )

    assert report.pages_crawled == 1
    assert len(report.failures) == 1


def test_records_the_failure_reason_and_depth(tmp_path: Path) -> None:
    pages = {"/": link("/missing")}
    report = make_crawler(pages, tmp_path).crawl(["https://example.com/"])

    failure = report.failures[0]
    assert failure.depth == 1
    assert failure.reason == "FetchError"
    assert "404" in failure.detail


def test_a_rejected_content_type_is_a_failure_not_a_crash(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/logo.png":
            return httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})
        return httpx.Response(
            200,
            content=b'<a href="/logo.png">x</a>',
            headers={"content-type": "text/html"},
        )

    crawler = SiteCrawler(
        RawHtmlStore(tmp_path), FAST, fetcher=make_fetcher(handler), politeness=PerDomainDelay(0.0)
    )
    report = crawler.crawl(["https://example.com/"])

    assert report.pages_crawled == 1
    assert report.failures[0].reason == "ContentTypeRejectedError"


def test_every_page_failing_yields_an_empty_report(tmp_path: Path) -> None:
    report = make_crawler({}, tmp_path).crawl(["https://example.com/"])

    assert report.pages_crawled == 0
    assert len(report.failures) == 1


# --- storage ----------------------------------------------------------------


def test_stores_raw_html_for_each_page(tmp_path: Path) -> None:
    pages = {"/": link("/a"), "/a": "<p>A</p>"}
    report = make_crawler(pages, tmp_path).crawl(["https://example.com/"])

    assert len(list(tmp_path.rglob("*.html"))) == 2
    for page in report.pages:
        assert page.path.is_file()


def test_stored_bytes_are_the_untouched_response(tmp_path: Path) -> None:
    body = "<html><script>var a=1;</script><nav>Nav</nav><p>Body</p></html>"
    report = make_crawler({"/": body}, tmp_path).crawl(["https://example.com/"])

    assert report.pages[0].path.read_bytes() == body.encode()


def test_failed_pages_are_not_stored(tmp_path: Path) -> None:
    report = make_crawler({}, tmp_path).crawl(["https://example.com/missing"])

    assert list(tmp_path.rglob("*.html")) == []
    assert report.pages_crawled == 0


def test_report_totals(tmp_path: Path) -> None:
    pages = {"/": link("/a"), "/a": "<p>A</p>"}
    report = make_crawler(pages, tmp_path).crawl(["https://example.com/"])

    assert report.bytes_stored == sum(page.size_bytes for page in report.pages)
    assert report.urls_seen == 2


# --- politeness -------------------------------------------------------------


def test_waits_between_requests_to_one_host(tmp_path: Path) -> None:
    clock = FakeTime()
    pages = {"/": link("/a") + link("/b"), "/a": "<p>A</p>", "/b": "<p>B</p>"}
    settings = CrawlSettings(delay_per_domain=0.5)
    make_crawler(pages, tmp_path, settings, clock=clock).crawl(["https://example.com/"])

    # Three pages, so two gaps, each a full delay.
    assert clock.sleeps == [pytest.approx(0.5), pytest.approx(0.5)]


def test_the_first_request_is_not_delayed(tmp_path: Path) -> None:
    clock = FakeTime()
    settings = CrawlSettings(delay_per_domain=0.5)
    make_crawler({"/": "<p>Home</p>"}, tmp_path, settings, clock=clock).crawl(
        ["https://example.com/"]
    )

    assert clock.sleeps == []


def test_hosts_are_rate_limited_independently(tmp_path: Path) -> None:
    """Two seeds on different hosts are fetched without waiting on each other."""
    clock = FakeTime()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<p>x</p>", headers={"content-type": "text/html"})

    settings = CrawlSettings(delay_per_domain=1.0, max_depth=0)
    crawler = SiteCrawler(
        RawHtmlStore(tmp_path),
        settings,
        fetcher=make_fetcher(handler),
        politeness=PerDomainDelay(1.0, sleep=clock.sleep, clock=clock.monotonic),
    )
    crawler.crawl(["https://one.example.com/", "https://two.example.com/"])

    assert clock.sleeps == []


def test_no_waiting_when_the_delay_is_zero(tmp_path: Path) -> None:
    clock = FakeTime()
    pages = {"/": link("/a"), "/a": "<p>A</p>"}
    make_crawler(pages, tmp_path, FAST, clock=clock).crawl(["https://example.com/"])

    assert clock.sleeps == []
