"""Integration tests: crawl a small linked site over a real loopback server."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from crawler.crawl import CrawlSettings, SiteCrawler
from crawler.storage import RawHtmlStore

#: A four-page site: home links to two articles, which link back and to a
#: missing page, plus an image and an off-site link that must not be followed.
SITE: dict[str, str] = {
    "/": """<!doctype html><html><head><title>Home</title></head><body>
        <nav><a href="/">Home</a></nav>
        <a href="/one.html">One</a>
        <a href="one.html">One again, relative</a>
        <a href="/two.html">Two</a>
        <a href="https://external.invalid/page">Off site</a>
        <a href="mailto:someone@example.com">Mail</a>
        </body></html>""",
    "/one.html": """<html><head><title>One</title></head><body>
        <p>First article.</p>
        <a href="/">Home</a><a href="/two.html">Two</a>
        <a href="/logo.png">Logo</a>
        </body></html>""",
    "/two.html": """<html><head><title>Two</title></head><body>
        <p>Second article.</p>
        <a href="/one.html">One</a><a href="/missing.html">Broken</a>
        <a href="/deep.html">Deep</a>
        </body></html>""",
    "/deep.html": """<html><head><title>Deep</title></head><body>
        <p>Depth two.</p><a href="/">Home</a>
        </body></html>""",
}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        """Silence the default stderr access log."""

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        if self.path == "/logo.png":
            self._send(200, b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "image/png")
        elif self.path in SITE:
            self._send(200, SITE[self.path].encode(), "text/html; charset=utf-8")
        else:
            self._send(404, b"<html><body>not found</body></html>", "text/html")


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: object, client_address: object) -> None:
        del request, client_address


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    server = _QuietServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{server.server_address[0]}:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def settings() -> CrawlSettings:
    return CrawlSettings(max_pages=20, max_depth=3, delay_per_domain=0.0)


def crawl(base_url: str, tmp_path: Path, settings: CrawlSettings, path: str = "/"):
    with SiteCrawler(RawHtmlStore(tmp_path), settings) as crawler:
        return crawler.crawl([f"{base_url}{path}"])


def test_crawls_the_whole_site(base_url: str, tmp_path: Path, settings: CrawlSettings) -> None:
    report = crawl(base_url, tmp_path, settings)

    assert {page.url.removeprefix(base_url) for page in report.pages} == {
        "/",
        "/one.html",
        "/two.html",
        "/deep.html",
    }


def test_stores_raw_html_on_disk(base_url: str, tmp_path: Path, settings: CrawlSettings) -> None:
    report = crawl(base_url, tmp_path, settings)

    stored = list(tmp_path.rglob("*.html"))
    assert len(stored) == report.pages_crawled == 4

    home = next(page for page in report.pages if page.url.endswith("/"))
    raw = home.path.read_bytes().decode()
    assert "<nav>" in raw and "mailto:" in raw  # raw means untouched


def test_visits_each_page_once_despite_cycles(
    base_url: str, tmp_path: Path, settings: CrawlSettings
) -> None:
    report = crawl(base_url, tmp_path, settings)

    urls = [page.url for page in report.pages]
    assert len(urls) == len(set(urls))


def test_relative_and_absolute_links_reach_the_same_page(
    base_url: str, tmp_path: Path, settings: CrawlSettings
) -> None:
    """Home links to /one.html twice, absolutely and relatively."""
    report = crawl(base_url, tmp_path, settings)

    assert sum(1 for page in report.pages if page.url.endswith("/one.html")) == 1


def test_does_not_leave_the_seed_host(
    base_url: str, tmp_path: Path, settings: CrawlSettings
) -> None:
    report = crawl(base_url, tmp_path, settings)

    assert all(page.url.startswith(base_url) for page in report.pages)


def test_a_broken_link_is_recorded_but_does_not_stop_the_crawl(
    base_url: str, tmp_path: Path, settings: CrawlSettings
) -> None:
    report = crawl(base_url, tmp_path, settings)

    assert report.pages_crawled == 4
    reasons = {failure.reason for failure in report.failures}
    assert "FetchError" in reasons
    assert any(failure.url.endswith("/missing.html") for failure in report.failures)


def test_an_image_link_is_rejected_by_content_type(
    base_url: str, tmp_path: Path, settings: CrawlSettings
) -> None:
    report = crawl(base_url, tmp_path, settings)

    assert "ContentTypeRejectedError" in {failure.reason for failure in report.failures}
    assert not any(page.url.endswith(".png") for page in report.pages)


def test_max_pages_stops_the_crawl(base_url: str, tmp_path: Path) -> None:
    settings = CrawlSettings(max_pages=2, max_depth=3, delay_per_domain=0.0)
    report = crawl(base_url, tmp_path, settings)

    assert report.pages_crawled == 2
    assert len(list(tmp_path.rglob("*.html"))) == 2


def test_max_depth_stops_the_crawl(base_url: str, tmp_path: Path) -> None:
    settings = CrawlSettings(max_pages=20, max_depth=1, delay_per_domain=0.0)
    report = crawl(base_url, tmp_path, settings)

    assert {page.url.removeprefix(base_url) for page in report.pages} == {
        "/",
        "/one.html",
        "/two.html",
    }


def test_the_crawl_is_reproducible(base_url: str, tmp_path: Path, settings: CrawlSettings) -> None:
    first = crawl(base_url, tmp_path / "a", settings)
    second = crawl(base_url, tmp_path / "b", settings)

    assert [page.url for page in first.pages] == [page.url for page in second.pages]


def test_politeness_delays_a_real_crawl(base_url: str, tmp_path: Path) -> None:
    """A non-zero delay measurably slows a multi-page crawl."""
    import time

    settings = CrawlSettings(max_pages=3, max_depth=2, delay_per_domain=0.05)
    started = time.monotonic()
    report = crawl(base_url, tmp_path, settings)
    elapsed = time.monotonic() - started

    assert report.pages_crawled == 3
    assert elapsed >= 0.10  # two gaps between three pages
