"""Tests for the command-line entry point."""

from __future__ import annotations

import json

import pytest

from crawler.cli import build_parser, main


def test_parser_defaults_match_the_config() -> None:
    from crawler.config import CrawlerConfig

    args = build_parser().parse_args(["https://example.com"])
    defaults = CrawlerConfig()

    assert args.url == "https://example.com"
    assert args.max_bytes == defaults.max_response_bytes
    assert args.min_text == defaults.min_text_length
    assert args.max_text == defaults.max_text_length
    assert args.attempts == defaults.max_attempts
    assert args.json is False


def test_parser_accepts_overrides() -> None:
    args = build_parser().parse_args(
        ["https://example.com", "--max-bytes", "100", "--min-text", "5", "--json"]
    )
    assert args.max_bytes == 100
    assert args.min_text == 5
    assert args.json is True


def test_reports_a_crawl_error_without_traceback(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["ftp://example.com/file"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "InvalidURLError" in captured.err
    assert captured.out == ""


def test_json_output_is_valid(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from crawler import cli
    from crawler.pipeline import CrawlDocument

    document = CrawlDocument(
        url="https://example.com/",
        final_url="https://example.com/",
        title="Example",
        text="Some body text.",
        content_hash="0" * 64,
        content_type="text/html",
        fetched_bytes=123,
    )

    def fake_crawl(self: cli.Crawler, url: str) -> CrawlDocument:
        return document

    monkeypatch.setattr(cli.Crawler, "crawl", fake_crawl)

    assert main(["https://example.com", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["title"] == "Example"
    assert payload["text"] == "Some body text."
    assert payload["text_length"] == len("Some body text.")
