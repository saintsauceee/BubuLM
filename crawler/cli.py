"""Command-line entry point: crawl a single URL and print the document.

uv run python -m crawler https://example.com
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from crawler.config import CrawlerConfig
from crawler.crawl import CrawlSettings, SiteCrawler
from crawler.errors import CrawlError
from crawler.pipeline import Crawler
from crawler.storage import RawHtmlStore


def build_parser() -> argparse.ArgumentParser:
    defaults = CrawlerConfig()
    parser = argparse.ArgumentParser(
        prog="python -m crawler",
        description="Fetch one page, or crawl outward from seed URLs.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch", help="Fetch one URL and emit a clean training document")
    fetch.add_argument("url", help="URL to fetch (http or https)")
    fetch.add_argument(
        "--max-bytes",
        type=int,
        default=defaults.max_response_bytes,
        help="Maximum response size in bytes (default: %(default)s)",
    )
    fetch.add_argument(
        "--min-text",
        type=int,
        default=defaults.min_text_length,
        help="Minimum extracted text length (default: %(default)s)",
    )
    fetch.add_argument(
        "--max-text",
        type=int,
        default=defaults.max_text_length,
        help="Maximum extracted text length (default: %(default)s)",
    )
    fetch.add_argument(
        "--timeout",
        type=float,
        default=defaults.request_timeout,
        help="Request timeout in seconds (default: %(default)s)",
    )
    fetch.add_argument(
        "--attempts",
        type=int,
        default=defaults.max_attempts,
        help="Maximum fetch attempts (default: %(default)s)",
    )
    fetch.add_argument(
        "--json",
        action="store_true",
        help="Emit the full document as JSON instead of a summary plus text",
    )

    crawl = subparsers.add_parser(
        "crawl", help="Crawl outward from seed URLs, storing raw HTML on disk"
    )
    crawl.add_argument("seeds", nargs="+", help="One or more seed URLs")
    crawl.add_argument(
        "--output",
        type=Path,
        default=Path("./raw"),
        help="Directory to store raw HTML in (default: %(default)s)",
    )
    crawl_defaults = CrawlSettings()
    crawl.add_argument(
        "--max-pages",
        type=int,
        default=crawl_defaults.max_pages,
        help="Stop after this many pages (default: %(default)s)",
    )
    crawl.add_argument(
        "--max-depth",
        type=int,
        default=crawl_defaults.max_depth,
        help="Link depth from the seeds; 0 crawls the seeds only (default: %(default)s)",
    )
    crawl.add_argument(
        "--delay",
        type=float,
        default=crawl_defaults.delay_per_domain,
        help="Minimum seconds between requests to one host (default: %(default)s)",
    )
    crawl.add_argument(
        "--follow-external",
        action="store_true",
        help="Follow links off the seed hosts (default: stay on them)",
    )
    crawl.add_argument(
        "--timeout",
        type=float,
        default=defaults.request_timeout,
        help="Request timeout in seconds (default: %(default)s)",
    )
    crawl.add_argument(
        "--max-bytes",
        type=int,
        default=defaults.max_response_bytes,
        help="Maximum response size in bytes (default: %(default)s)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    if args.command == "crawl":
        return _run_crawl(args)
    return _run_fetch(args)


def _run_fetch(args: argparse.Namespace) -> int:
    config = CrawlerConfig(
        request_timeout=args.timeout,
        max_response_bytes=args.max_bytes,
        max_attempts=args.attempts,
        min_text_length=args.min_text,
        max_text_length=args.max_text,
    )

    try:
        with Crawler(config) as crawler:
            document = crawler.crawl(args.url)
    except CrawlError as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "url": document.url,
                    "final_url": document.final_url,
                    "title": document.title,
                    "content_hash": document.content_hash,
                    "content_type": document.content_type,
                    "fetched_bytes": document.fetched_bytes,
                    "text_length": document.text_length,
                    "text": document.text,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"url:      {document.final_url}")
        print(f"title:    {document.title or '-'}")
        print(f"hash:     {document.content_hash}")
        print(f"bytes:    {document.fetched_bytes}")
        print(f"text len: {document.text_length}")
        print()
        print(document.text)

    return 0


def _run_crawl(args: argparse.Namespace) -> int:
    config = CrawlerConfig(
        request_timeout=args.timeout,
        max_response_bytes=args.max_bytes,
    )
    settings = CrawlSettings(
        max_pages=args.max_pages,
        max_depth=args.max_depth,
        delay_per_domain=args.delay,
        follow_external_links=args.follow_external,
    )

    store = RawHtmlStore(args.output)
    with SiteCrawler(store, settings, config) as crawler:
        report = crawler.crawl(args.seeds)

    for page in report.pages:
        print(f"  [d{page.depth}] {page.size_bytes:>7} B  {page.links_found:>3} links  {page.url}")
    for failure in report.failures:
        print(f"  [d{failure.depth}] {failure.reason}: {failure.url}", file=sys.stderr)

    print()
    print(f"pages crawled: {report.pages_crawled}")
    print(f"failures:      {len(report.failures)}")
    print(f"urls seen:     {report.urls_seen}")
    print(f"bytes stored:  {report.bytes_stored}")
    print(f"output:        {store.root}")

    return 0 if report.pages_crawled else 1
