"""Command-line entry point: crawl a single URL and print the document.

uv run python -m crawler https://example.com
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from crawler.config import CrawlerConfig
from crawler.errors import CrawlError
from crawler.pipeline import Crawler


def build_parser() -> argparse.ArgumentParser:
    defaults = CrawlerConfig()
    parser = argparse.ArgumentParser(
        prog="python -m crawler",
        description="Crawl a single URL and emit a clean training document.",
    )
    parser.add_argument("url", help="Seed URL to crawl (http or https)")
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=defaults.max_response_bytes,
        help="Maximum response size in bytes (default: %(default)s)",
    )
    parser.add_argument(
        "--min-text",
        type=int,
        default=defaults.min_text_length,
        help="Minimum extracted text length (default: %(default)s)",
    )
    parser.add_argument(
        "--max-text",
        type=int,
        default=defaults.max_text_length,
        help="Maximum extracted text length (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=defaults.request_timeout,
        help="Request timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--attempts",
        type=int,
        default=defaults.max_attempts,
        help="Maximum fetch attempts (default: %(default)s)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the full document as JSON instead of a summary plus text",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI. Returns a process exit code."""
    args = build_parser().parse_args(argv)

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
