"""Tests for the end-to-end crawl pipeline (mocked HTTP)."""

from __future__ import annotations

import httpx
import pytest

from crawler.config import CrawlerConfig
from crawler.errors import (
    ContentTypeRejectedError,
    FetchError,
    InvalidURLError,
    QualityRejectedError,
    ResponseTooLargeError,
    TransientFetchError,
)
from crawler.hashing import content_hash
from crawler.pipeline import Crawler
from tests.conftest import Handler, html_page, html_response, long_prose, make_fetcher

PERMISSIVE = CrawlerConfig(min_text_length=10, min_alpha_ratio=0.3)


def make_crawler(handler: Handler, config: CrawlerConfig = PERMISSIVE) -> Crawler:
    return Crawler(config, fetcher=make_fetcher(handler, config))


def test_produces_a_clean_document() -> None:
    markup = html_page(
        "<nav>Home About</nav><main><p>The quick brown fox jumps over the lazy dog.</p></main>"
        "<footer>Copyright</footer><script>track();</script>",
        title="Fox Article",
    )
    document = make_crawler(lambda request: html_response(markup)).crawl("https://example.com/fox")

    assert document.title == "Fox Article"
    assert document.text == "The quick brown fox jumps over the lazy dog."
    assert document.content_hash == content_hash(document.text)
    assert document.content_type == "text/html"
    assert document.text_length == len(document.text)
    assert document.fetched_bytes == len(markup.encode())


def test_normalizes_the_url_before_fetching() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return html_response(html_page(long_prose()))

    document = make_crawler(handler).crawl("HTTPS://Example.COM:443/a/./b?z=1&utm_source=x#frag")

    assert requested == ["https://example.com/a/b?z=1"]
    assert document.url == "https://example.com/a/b?z=1"


def test_records_the_final_url_after_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(302, headers={"location": "https://example.com/new"})
        return html_response(html_page(long_prose()))

    document = make_crawler(handler).crawl("https://example.com/old")
    assert document.url == "https://example.com/old"
    assert document.final_url == "https://example.com/new"


def test_identical_content_hashes_identically_across_urls() -> None:
    markup = html_page(long_prose())
    crawler = make_crawler(lambda request: html_response(markup))

    first = crawler.crawl("https://example.com/a")
    second = crawler.crawl("https://example.com/b")

    assert first.url != second.url
    assert first.content_hash == second.content_hash


def test_different_content_hashes_differently() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return html_response(html_page(f"<p>Body for {request.url.path} with enough words.</p>"))

    crawler = make_crawler(handler)
    assert crawler.crawl("https://example.com/a").content_hash != (
        crawler.crawl("https://example.com/b").content_hash
    )


def test_rejects_an_invalid_url_without_fetching() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never called
        raise AssertionError("should not fetch")

    with pytest.raises(InvalidURLError):
        make_crawler(handler).crawl("ftp://example.com/file")


def test_rejects_non_html() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})

    with pytest.raises(ContentTypeRejectedError):
        make_crawler(handler).crawl("https://example.com/logo.png")


def test_rejects_an_oversized_response() -> None:
    config = CrawlerConfig(max_response_bytes=50, min_text_length=1)
    with pytest.raises(ResponseTooLargeError):
        make_crawler(lambda request: html_response(html_page(long_prose())), config).crawl(
            "https://example.com/"
        )


def test_rejects_text_below_the_minimum_length() -> None:
    config = CrawlerConfig(min_text_length=500)
    with pytest.raises(QualityRejectedError, match="minimum"):
        make_crawler(lambda request: html_response(html_page("<p>Too short</p>")), config).crawl(
            "https://example.com/"
        )


def test_rejects_text_above_the_maximum_length() -> None:
    config = CrawlerConfig(min_text_length=1, max_text_length=20)
    with pytest.raises(QualityRejectedError, match="maximum"):
        make_crawler(lambda request: html_response(html_page(long_prose())), config).crawl(
            "https://example.com/"
        )


def test_rejects_a_page_with_no_extractable_text() -> None:
    markup = html_page("<script>var a = 1;</script><style>p { color: red; }</style>")
    with pytest.raises(QualityRejectedError, match="empty"):
        make_crawler(lambda request: html_response(markup)).crawl("https://example.com/")


def test_rejects_a_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"nope", headers={"content-type": "text/html"})

    with pytest.raises(FetchError):
        make_crawler(handler).crawl("https://example.com/missing")


def test_surfaces_exhausted_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(TransientFetchError):
        make_crawler(handler).crawl("https://example.com/")


def test_recovers_from_a_transient_failure() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(503, content=b"", headers={"content-type": "text/html"})
        return html_response(html_page(long_prose()))

    document = make_crawler(handler).crawl("https://example.com/")
    assert len(attempts) == 2
    assert document.text


def test_tolerates_malformed_html() -> None:
    markup = "<html><body><p>Unclosed paragraph with plenty of readable words in it"
    document = make_crawler(lambda request: html_response(markup)).crawl("https://example.com/")
    assert "Unclosed paragraph" in document.text


def test_crawler_closes_its_own_fetcher() -> None:
    with Crawler(PERMISSIVE) as crawler:
        assert crawler.config is PERMISSIVE
