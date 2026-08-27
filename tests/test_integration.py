"""Integration tests against a real HTTP server bound to loopback.

No external network access: the server is started in-process for the duration
of the module and serves fixed responses.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from crawler.config import CrawlerConfig
from crawler.errors import (
    ContentTypeRejectedError,
    FetchError,
    QualityRejectedError,
    ResponseTooLargeError,
)
from crawler.pipeline import Crawler

ARTICLE_HTML = """<!doctype html>
<html>
  <head>
    <title>Local Test Article</title>
    <style>body { margin: 0; }</style>
    <script>window.analytics = true;</script>
  </head>
  <body>
    <nav><a href="/">Home</a> <a href="/about">About</a></nav>
    <main>
      <h1>A Local Article</h1>
      <p>The crawler fetches this page over a real socket and turns it into a
         clean training document without any external network access.</p>
      <p>It removes navigation, scripts, styles, and footers, keeping only the
         readable prose that belongs in the corpus.</p>
    </main>
    <footer>Copyright 2026</footer>
  </body>
</html>
"""

TINY_HTML = "<html><head><title>Tiny</title></head><body><p>Hi.</p></body></html>"

_flaky_attempts = 0


class _Handler(BaseHTTPRequestHandler):
    """Serves the fixed routes the integration tests exercise."""

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
        global _flaky_attempts

        if self.path == "/article":
            self._send(200, ARTICLE_HTML.encode(), "text/html; charset=utf-8")
        elif self.path == "/tiny":
            self._send(200, TINY_HTML.encode(), "text/html; charset=utf-8")
        elif self.path == "/image.png":
            self._send(200, b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "image/png")
        elif self.path == "/huge":
            padding = "<p>" + ("word " * 20000) + "</p>"
            self._send(200, f"<html><body>{padding}</body></html>".encode(), "text/html")
        elif self.path == "/flaky":
            _flaky_attempts += 1
            if _flaky_attempts < 3:
                self._send(503, b"unavailable", "text/html")
            else:
                self._send(200, ARTICLE_HTML.encode(), "text/html; charset=utf-8")
        elif self.path == "/redirect":
            body = b""
            self.send_response(302)
            self.send_header("Location", "/article")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
        elif self.path == "/latin1":
            paragraph = (
                "Caf\xe9 na Rua Jos\xe9: um texto em portugu\xeas com acentua\xe7\xe3o "
                "suficiente para passar pelo filtro de qualidade do rastreador."
            )
            body = f"<html><body><p>{paragraph}</p></body></html>"
            self._send(200, body.encode("iso-8859-1"), "text/html; charset=iso-8859-1")
        else:
            self._send(404, b"<html><body>not found</body></html>", "text/html")


class _QuietServer(ThreadingHTTPServer):
    """A server that does not log the resets caused by aborted downloads.

    The response-size test deliberately hangs up mid-body, which is a normal
    client abort rather than a server fault.
    """

    daemon_threads = True

    def handle_error(self, request: object, client_address: object) -> None:
        del request, client_address


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    """Run a local HTTP server on an ephemeral loopback port."""
    server = _QuietServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[0], server.server_address[1]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def config() -> CrawlerConfig:
    return CrawlerConfig(request_timeout=5.0, min_text_length=50, retry_backoff_base=0.01)


def test_crawls_a_real_response(base_url: str, config: CrawlerConfig) -> None:
    with Crawler(config) as crawler:
        document = crawler.crawl(f"{base_url}/article")

    assert document.title == "Local Test Article"
    assert "A Local Article" in document.text
    assert "clean training document" in document.text
    assert "Home" not in document.text
    assert "Copyright" not in document.text
    assert "window.analytics" not in document.text
    assert len(document.content_hash) == 64


def test_crawl_is_reproducible(base_url: str, config: CrawlerConfig) -> None:
    with Crawler(config) as crawler:
        first = crawler.crawl(f"{base_url}/article")
        second = crawler.crawl(f"{base_url}/article")

    assert first.content_hash == second.content_hash
    assert first.text == second.text


def test_follows_redirects(base_url: str, config: CrawlerConfig) -> None:
    with Crawler(config) as crawler:
        document = crawler.crawl(f"{base_url}/redirect")

    assert document.final_url == f"{base_url}/article"
    assert document.title == "Local Test Article"


def test_rejects_a_real_image(base_url: str, config: CrawlerConfig) -> None:
    with Crawler(config) as crawler, pytest.raises(ContentTypeRejectedError):
        crawler.crawl(f"{base_url}/image.png")


def test_enforces_the_size_limit_over_the_wire(base_url: str) -> None:
    config = CrawlerConfig(request_timeout=5.0, max_response_bytes=1024)
    with Crawler(config) as crawler, pytest.raises(ResponseTooLargeError):
        crawler.crawl(f"{base_url}/huge")


def test_rejects_a_page_that_is_too_short(base_url: str, config: CrawlerConfig) -> None:
    with Crawler(config) as crawler, pytest.raises(QualityRejectedError):
        crawler.crawl(f"{base_url}/tiny")


def test_retries_a_flaky_endpoint(base_url: str, config: CrawlerConfig) -> None:
    with Crawler(config) as crawler:
        document = crawler.crawl(f"{base_url}/flaky")

    assert document.title == "Local Test Article"


def test_reports_a_404(base_url: str, config: CrawlerConfig) -> None:
    with Crawler(config) as crawler, pytest.raises(FetchError, match="404"):
        crawler.crawl(f"{base_url}/does-not-exist")


def test_decodes_a_non_utf8_page(base_url: str, config: CrawlerConfig) -> None:
    with Crawler(config) as crawler:
        document = crawler.crawl(f"{base_url}/latin1")

    assert "Café" in document.text
    assert "José" in document.text
