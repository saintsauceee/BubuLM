"""Tests for the URL frontier and its visited set."""

from __future__ import annotations

import pytest

from crawler.frontier import Frontier, hosts_of


def test_pops_in_fifo_order() -> None:
    frontier = Frontier()
    for path in ("a", "b", "c"):
        frontier.add(f"https://example.com/{path}")

    popped = [frontier.pop(), frontier.pop(), frontier.pop()]
    assert [item.url for item in popped if item] == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]


def test_pop_returns_none_when_empty() -> None:
    assert Frontier().pop() is None


def test_seeds_start_at_depth_zero() -> None:
    frontier = Frontier()
    assert frontier.add_seeds(["https://example.com/"]) == 1

    item = frontier.pop()
    assert item is not None
    assert item.depth == 0


def test_records_the_depth_a_url_was_added_at() -> None:
    frontier = Frontier()
    frontier.add("https://example.com/deep", depth=3)

    item = frontier.pop()
    assert item is not None
    assert item.depth == 3


# --- deduplication ----------------------------------------------------------


def test_rejects_an_exact_duplicate() -> None:
    frontier = Frontier()
    assert frontier.add("https://example.com/a") is True
    assert frontier.add("https://example.com/a") is False
    assert len(frontier) == 1


@pytest.mark.parametrize(
    "variant",
    [
        "https://example.com/a",
        "HTTPS://EXAMPLE.COM/a",
        "https://example.com:443/a",
        "https://example.com/a#section",
        "https://example.com/b/../a",
        "  https://example.com/a  ",
        "https://example.com/a?utm_source=twitter",
    ],
)
def test_normalizes_before_deduplicating(variant: str) -> None:
    frontier = Frontier()
    frontier.add("https://example.com/a")
    assert frontier.add(variant) is False
    assert len(frontier) == 1


def test_distinct_urls_are_both_kept() -> None:
    frontier = Frontier()
    assert frontier.add("https://example.com/a") is True
    assert frontier.add("https://example.com/b") is True
    assert len(frontier) == 2


def test_stores_the_normalized_form() -> None:
    frontier = Frontier()
    frontier.add("HTTPS://EXAMPLE.COM:443/a/../b?z=1&a=2#top")

    item = frontier.pop()
    assert item is not None
    assert item.url == "https://example.com/b?a=2&z=1"


def test_a_popped_url_is_not_re_enqueued() -> None:
    """The visited set survives popping, so cycles terminate."""
    frontier = Frontier()
    frontier.add("https://example.com/a")
    frontier.pop()
    assert frontier.add("https://example.com/a") is False


def test_has_seen_reports_membership() -> None:
    frontier = Frontier()
    frontier.add("https://example.com/a")
    assert frontier.has_seen("https://example.com/a#x") is True
    assert frontier.has_seen("https://example.com/b") is False
    assert frontier.has_seen("not a url") is False


def test_seen_count_counts_distinct_urls() -> None:
    frontier = Frontier()
    frontier.add("https://example.com/a")
    frontier.add("https://example.com/a")
    frontier.add("https://example.com/b")
    assert frontier.seen_count == 2


# --- rejection rules --------------------------------------------------------


@pytest.mark.parametrize(
    "url", ["", "   ", "not-a-url", "mailto:x@example.com", "ftp://example.com/f"]
)
def test_rejects_unusable_urls(url: str) -> None:
    assert Frontier().add(url) is False


def test_respects_max_depth() -> None:
    frontier = Frontier(max_depth=2)
    assert frontier.add("https://example.com/a", depth=2) is True
    assert frontier.add("https://example.com/b", depth=3) is False


def test_max_depth_zero_allows_only_seeds() -> None:
    frontier = Frontier(max_depth=0)
    assert frontier.add("https://example.com/a", depth=0) is True
    assert frontier.add("https://example.com/b", depth=1) is False


def test_no_max_depth_accepts_any_depth() -> None:
    assert Frontier().add("https://example.com/a", depth=99) is True


def test_restricts_to_allowed_hosts() -> None:
    frontier = Frontier(allowed_hosts=frozenset({"example.com"}))
    assert frontier.add("https://example.com/a") is True
    assert frontier.add("https://other.com/a") is False


def test_allowed_hosts_matches_after_normalization() -> None:
    frontier = Frontier(allowed_hosts=frozenset({"example.com"}))
    assert frontier.add("HTTPS://EXAMPLE.COM:443/a") is True


def test_no_allowed_hosts_accepts_any_host() -> None:
    assert Frontier().add("https://anywhere.example.org/a") is True


# --- hosts_of ---------------------------------------------------------------


def test_hosts_of_collects_normalized_hosts() -> None:
    assert hosts_of(["https://Example.COM/a", "http://other.com:80/b"]) == frozenset(
        {"example.com", "other.com"}
    )


def test_hosts_of_ignores_unparseable_urls() -> None:
    assert hosts_of(["not-a-url", "https://example.com/a"]) == frozenset({"example.com"})


def test_hosts_of_empty() -> None:
    assert hosts_of([]) == frozenset()
