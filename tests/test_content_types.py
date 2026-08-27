"""Tests for Content-Type parsing and filtering."""

from __future__ import annotations

import pytest

from crawler.config import DEFAULT_ACCEPTED_CONTENT_TYPES
from crawler.content_types import (
    ensure_accepted_content_type,
    is_accepted_content_type,
    parse_content_type,
)
from crawler.errors import ContentTypeRejectedError

ACCEPTED = DEFAULT_ACCEPTED_CONTENT_TYPES


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("text/html", ("text/html", None)),
        ("text/html; charset=utf-8", ("text/html", "utf-8")),
        ("TEXT/HTML; CHARSET=UTF-8", ("text/html", "utf-8")),
        ('text/html; charset="iso-8859-1"', ("text/html", "iso-8859-1")),
        ("  text/html ; charset = utf-8 ", ("text/html", "utf-8")),
        ("application/xhtml+xml", ("application/xhtml+xml", None)),
        ("text/html; boundary=x; charset=utf-8", ("text/html", "utf-8")),
        ("text/html; charset=", ("text/html", None)),
        (None, ("", None)),
        ("", ("", None)),
    ],
)
def test_parse_content_type(header: str | None, expected: tuple[str, str | None]) -> None:
    assert parse_content_type(header) == expected


@pytest.mark.parametrize("mime", ["text/html", "application/xhtml+xml"])
def test_accepts_html_types(mime: str) -> None:
    assert is_accepted_content_type(mime, ACCEPTED)


@pytest.mark.parametrize(
    "mime",
    [
        "image/png",
        "image/jpeg",
        "image/svg+xml",
        "video/mp4",
        "audio/mpeg",
        "application/pdf",
        "application/zip",
        "application/gzip",
        "application/octet-stream",
        "application/json",
        "text/plain",
        "text/css",
        "application/javascript",
        "font/woff2",
    ],
)
def test_rejects_non_html_types(mime: str) -> None:
    assert not is_accepted_content_type(mime, ACCEPTED)
    with pytest.raises(ContentTypeRejectedError):
        ensure_accepted_content_type(mime, ACCEPTED)


def test_ensure_returns_mime_and_charset() -> None:
    assert ensure_accepted_content_type("text/html; charset=utf-8", ACCEPTED) == (
        "text/html",
        "utf-8",
    )


@pytest.mark.parametrize("header", [None, "", "   "])
def test_ensure_rejects_missing_header(header: str | None) -> None:
    with pytest.raises(ContentTypeRejectedError):
        ensure_accepted_content_type(header, ACCEPTED)
