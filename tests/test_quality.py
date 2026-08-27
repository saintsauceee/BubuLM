"""Tests for text quality filtering."""

from __future__ import annotations

import pytest

from crawler.config import CrawlerConfig
from crawler.errors import QualityRejectedError
from crawler.quality import alpha_ratio, check_text_quality

CONFIG = CrawlerConfig(min_text_length=20, max_text_length=100, min_alpha_ratio=0.5)


def test_accepts_text_within_limits() -> None:
    check_text_quality("This is a perfectly ordinary sentence of prose.", CONFIG)


@pytest.mark.parametrize("text", ["", "   ", "\n\n", "\t \n"])
def test_rejects_empty_text(text: str) -> None:
    with pytest.raises(QualityRejectedError, match="empty"):
        check_text_quality(text, CONFIG)


def test_rejects_text_below_minimum_length() -> None:
    with pytest.raises(QualityRejectedError, match="minimum"):
        check_text_quality("too short", CONFIG)


def test_accepts_text_at_exactly_the_minimum() -> None:
    check_text_quality("a" * CONFIG.min_text_length, CONFIG)


def test_rejects_text_above_maximum_length() -> None:
    with pytest.raises(QualityRejectedError, match="maximum"):
        check_text_quality("a" * (CONFIG.max_text_length + 1), CONFIG)


def test_accepts_text_at_exactly_the_maximum() -> None:
    check_text_quality("a" * CONFIG.max_text_length, CONFIG)


def test_rejects_text_that_is_mostly_not_letters() -> None:
    with pytest.raises(QualityRejectedError, match="alphabetic"):
        check_text_quality("12345 67890 {}[]()<>!@#$ 1234 5678 90", CONFIG)


def test_limits_are_configurable() -> None:
    permissive = CrawlerConfig(min_text_length=1, max_text_length=10, min_alpha_ratio=0.0)
    check_text_quality("123456", permissive)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("abc", 1.0),
        ("a b c", 1.0),
        ("ab12", 0.5),
        ("1234", 0.0),
        ("", 0.0),
        ("   ", 0.0),
    ],
)
def test_alpha_ratio(text: str, expected: float) -> None:
    assert alpha_ratio(text) == pytest.approx(expected)


def test_invalid_config_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="max_text_length"):
        CrawlerConfig(min_text_length=100, max_text_length=10)
    with pytest.raises(ValueError, match="max_attempts"):
        CrawlerConfig(max_attempts=0)
    with pytest.raises(ValueError, match="min_alpha_ratio"):
        CrawlerConfig(min_alpha_ratio=1.5)
    with pytest.raises(ValueError, match="max_response_bytes"):
        CrawlerConfig(max_response_bytes=0)
