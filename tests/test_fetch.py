"""Tests for fetching: size limits, content-type filtering, and retries."""

from __future__ import annotations

import httpx
import pytest

from crawler.config import CrawlerConfig
from crawler.errors import (
    ContentTypeRejectedError,
    FetchError,
    ResponseTooLargeError,
    TransientFetchError,
)
from tests.conftest import html_page, html_response, make_fetcher


def test_fetches_html() -> None:
    fetcher = make_fetcher(lambda request: html_response(html_page("<p>Hi</p>")))
    result = fetcher.fetch("https://example.com/")

    assert result.status_code == 200
    assert result.content_type == "text/html"
    assert result.charset == "utf-8"
    assert b"<p>Hi</p>" in result.body
    assert result.size_bytes == len(result.body)


def test_sends_the_configured_user_agent() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("user-agent", ""))
        return html_response(html_page("<p>Hi</p>"))

    config = CrawlerConfig(user_agent="BubuLM-Test/1.0")
    make_fetcher(handler, config).fetch("https://example.com/")
    assert seen == ["BubuLM-Test/1.0"]


def test_reports_the_final_url_after_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(301, headers={"location": "https://example.com/end"})
        return html_response(html_page("<p>Arrived</p>"))

    result = make_fetcher(handler).fetch("https://example.com/start")
    assert result.url == "https://example.com/end"


# --- Content-type filtering -------------------------------------------------


@pytest.mark.parametrize(
    "content_type",
    [
        "image/png",
        "video/mp4",
        "application/pdf",
        "application/zip",
        "application/octet-stream",
        "text/plain",
    ],
)
def test_rejects_non_html_content_types(content_type: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"binary", headers={"content-type": content_type})

    fetcher = make_fetcher(handler)
    with pytest.raises(ContentTypeRejectedError):
        fetcher.fetch("https://example.com/asset")


def test_rejects_a_missing_content_type() -> None:
    fetcher = make_fetcher(lambda request: httpx.Response(200, content=b"<p>x</p>"))
    with pytest.raises(ContentTypeRejectedError):
        fetcher.fetch("https://example.com/")


def test_accepted_types_are_configurable() -> None:
    config = CrawlerConfig(accepted_content_types=frozenset({"text/plain"}))
    fetcher = make_fetcher(
        lambda request: httpx.Response(200, content=b"hi", headers={"content-type": "text/plain"}),
        config,
    )
    assert fetcher.fetch("https://example.com/").content_type == "text/plain"


# --- Response-size limits ---------------------------------------------------


def test_rejects_a_body_over_the_limit() -> None:
    config = CrawlerConfig(max_response_bytes=100)
    fetcher = make_fetcher(lambda request: html_response("<p>" + "x" * 500 + "</p>"), config)
    with pytest.raises(ResponseTooLargeError):
        fetcher.fetch("https://example.com/big")


def test_rejects_on_declared_content_length_before_downloading() -> None:
    downloaded: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = b"x" * 5000
        downloaded.append(len(body))
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "text/html", "content-length": str(len(body))},
        )

    config = CrawlerConfig(max_response_bytes=100)
    with pytest.raises(ResponseTooLargeError, match="declares"):
        make_fetcher(handler, config).fetch("https://example.com/big")


def test_accepts_a_body_at_the_limit() -> None:
    body = "<html><body><p>ok</p></body></html>"
    config = CrawlerConfig(max_response_bytes=len(body.encode()))
    result = make_fetcher(lambda request: html_response(body), config).fetch("https://example.com/")
    assert result.size_bytes == config.max_response_bytes


def test_size_limit_is_configurable() -> None:
    body = html_page("<p>" + "x" * 500 + "</p>")
    generous = CrawlerConfig(max_response_bytes=10_000)
    assert make_fetcher(lambda request: html_response(body), generous).fetch("https://example.com/")


# --- Retry handling ---------------------------------------------------------


@pytest.mark.parametrize("status", [408, 425, 429, 500, 502, 503, 504])
def test_retries_transient_statuses_then_succeeds(status: int) -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(status, content=b"", headers={"content-type": "text/html"})
        return html_response(html_page("<p>Recovered</p>"))

    sleeps: list[float] = []
    result = make_fetcher(handler, sleeps=sleeps).fetch("https://example.com/")

    assert len(attempts) == 3
    assert result.status_code == 200
    assert sleeps == [0.5, 1.0]


def test_gives_up_after_max_attempts() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(503, content=b"", headers={"content-type": "text/html"})

    config = CrawlerConfig(max_attempts=4)
    with pytest.raises(TransientFetchError, match="4 attempts"):
        make_fetcher(handler, config).fetch("https://example.com/")
    assert len(attempts) == 4


def test_retries_transport_errors() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 2:
            raise httpx.ConnectError("connection refused", request=request)
        return html_response(html_page("<p>Recovered</p>"))

    assert make_fetcher(handler).fetch("https://example.com/").status_code == 200
    assert len(attempts) == 2


def test_retries_timeouts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(TransientFetchError):
        make_fetcher(handler).fetch("https://example.com/")


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 418, 451])
def test_does_not_retry_permanent_errors(status: int) -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(status, content=b"", headers={"content-type": "text/html"})

    with pytest.raises(FetchError) as caught:
        make_fetcher(handler).fetch("https://example.com/missing")

    assert not isinstance(caught.value, TransientFetchError)
    assert len(attempts) == 1


def test_a_single_attempt_never_sleeps() -> None:
    sleeps: list[float] = []
    config = CrawlerConfig(max_attempts=1)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"", headers={"content-type": "text/html"})

    with pytest.raises(TransientFetchError):
        make_fetcher(handler, config, sleeps=sleeps).fetch("https://example.com/")
    assert sleeps == []


def test_backoff_grows_exponentially_and_is_capped() -> None:
    config = CrawlerConfig(retry_backoff_base=1.0, retry_backoff_max=4.0)
    fetcher = make_fetcher(lambda request: html_response("<p>x</p>"), config)

    delays = [fetcher.backoff_delay(attempt) for attempt in range(1, 6)]
    assert delays == [1.0, 2.0, 4.0, 4.0, 4.0]


def test_content_type_rejection_is_not_retried() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})

    with pytest.raises(ContentTypeRejectedError):
        make_fetcher(handler).fetch("https://example.com/logo.png")
    assert len(attempts) == 1
