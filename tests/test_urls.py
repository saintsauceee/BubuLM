"""Tests for URL normalization."""

from __future__ import annotations

import pytest

from crawler.errors import InvalidURLError
from crawler.urls import normalize_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  https://example.com/  ", "https://example.com/"),
        ("HTTPS://Example.COM/Path", "https://example.com/Path"),
        ("https://example.com", "https://example.com/"),
        ("https://example.com.", "https://example.com/"),
        ("https://example.com:443/a", "https://example.com/a"),
        ("http://example.com:80/a", "http://example.com/a"),
        ("https://example.com:8080/a", "https://example.com:8080/a"),
        ("https://example.com/a#section", "https://example.com/a"),
        ("https://example.com/a/./b", "https://example.com/a/b"),
        ("https://example.com/a/b/../c", "https://example.com/a/c"),
        ("https://example.com/../etc", "https://example.com/etc"),
    ],
)
def test_normalizes_common_variations(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


def test_preserves_path_case_and_trailing_slash() -> None:
    assert normalize_url("https://example.com/Docs/Guide/") == "https://example.com/Docs/Guide/"


def test_sorts_query_parameters() -> None:
    assert normalize_url("https://example.com/s?b=2&a=1") == "https://example.com/s?a=1&b=2"


def test_keeps_blank_query_values() -> None:
    assert normalize_url("https://example.com/s?q=") == "https://example.com/s?q="


def test_drops_tracking_parameters() -> None:
    url = "https://example.com/post?utm_source=news&id=7&fbclid=abc&UTM_Medium=email"
    assert normalize_url(url) == "https://example.com/post?id=7"


def test_keeps_tracking_parameters_when_disabled() -> None:
    url = "https://example.com/post?utm_source=news&id=7"
    normalized = normalize_url(url, drop_tracking_params=False)
    assert normalized == "https://example.com/post?id=7&utm_source=news"


def test_variations_of_the_same_page_normalize_identically() -> None:
    variants = [
        "https://example.com/a/b?x=1&y=2",
        "HTTPS://EXAMPLE.COM:443/a/./b?y=2&x=1#top",
        "  https://example.com/a/c/../b?x=1&y=2&utm_source=twitter  ",
    ]
    assert len({normalize_url(url) for url in variants}) == 1


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "example.com/no-scheme",
        "ftp://example.com/file",
        "mailto:someone@example.com",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https:///no-host",
    ],
)
def test_rejects_unusable_urls(raw: str) -> None:
    with pytest.raises(InvalidURLError):
        normalize_url(raw)
