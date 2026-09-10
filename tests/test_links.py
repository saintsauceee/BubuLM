"""Tests for link extraction."""

from __future__ import annotations

import pytest

from crawler.links import extract_links

BASE = "https://example.com/docs/guide.html"


def test_extracts_an_absolute_link() -> None:
    html = '<a href="https://other.com/page">x</a>'
    assert extract_links(html, BASE) == ["https://other.com/page"]


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("page.html", "https://example.com/docs/page.html"),
        ("./page.html", "https://example.com/docs/page.html"),
        ("../page.html", "https://example.com/page.html"),
        ("/page.html", "https://example.com/page.html"),
        ("//cdn.example.com/x", "https://cdn.example.com/x"),
        ("?q=1", "https://example.com/docs/guide.html?q=1"),
        ("sub/deep.html", "https://example.com/docs/sub/deep.html"),
    ],
)
def test_resolves_relative_links(href: str, expected: str) -> None:
    assert extract_links(f'<a href="{href}">x</a>', BASE) == [expected]


def test_preserves_document_order() -> None:
    html = '<a href="/c">c</a><a href="/a">a</a><a href="/b">b</a>'
    assert extract_links(html, BASE) == [
        "https://example.com/c",
        "https://example.com/a",
        "https://example.com/b",
    ]


def test_collapses_exact_duplicate_hrefs() -> None:
    html = '<a href="/a">one</a><a href="/a">two</a><a href="/a">three</a>'
    assert extract_links(html, BASE) == ["https://example.com/a"]


@pytest.mark.parametrize(
    "href",
    [
        "mailto:someone@example.com",
        "javascript:alert(1)",
        "tel:+15551234",
        "data:text/html,<p>x</p>",
        "ftp://example.com/file",
        "#section",
        "",
    ],
)
def test_drops_non_followable_hrefs(href: str) -> None:
    assert extract_links(f'<a href="{href}">x</a>', BASE) == []


def test_ignores_anchors_without_href() -> None:
    assert extract_links('<a name="top">x</a><a>y</a>', BASE) == []


def test_honours_a_base_href() -> None:
    html = '<head><base href="https://cdn.example.com/v2/"></head><body><a href="p.html">x</a>'
    assert extract_links(html, BASE) == ["https://cdn.example.com/v2/p.html"]


def test_uses_the_first_base_href() -> None:
    html = (
        '<base href="https://first.example.com/"><base href="https://second.example.com/">'
        '<a href="p">x</a>'
    )
    assert extract_links(html, BASE) == ["https://first.example.com/p"]


def test_ignores_a_base_tag_without_href() -> None:
    assert extract_links('<base><a href="/a">x</a>', BASE) == ["https://example.com/a"]


def test_strips_whitespace_around_hrefs() -> None:
    assert extract_links('<a href="  /a  ">x</a>', BASE) == ["https://example.com/a"]


def test_decodes_entities_in_hrefs() -> None:
    html = '<a href="/search?a=1&amp;b=2">x</a>'
    assert extract_links(html, BASE) == ["https://example.com/search?a=1&b=2"]


def test_finds_links_in_nested_markup() -> None:
    html = '<div><ul><li><a href="/deep">x</a></li></ul></div>'
    assert extract_links(html, BASE) == ["https://example.com/deep"]


@pytest.mark.parametrize(
    "html",
    [
        '<a href="/a">unclosed',
        '<a href="/a"><div></a></div>',
        '<a href="/a">x</a><script>var a = "<a href=\'/b\'>";',
        "<not-a-tag><a href='/a'>x</a>",
    ],
)
def test_tolerates_malformed_markup(html: str) -> None:
    assert "https://example.com/a" in extract_links(html, BASE)


def test_empty_document_has_no_links() -> None:
    assert extract_links("", BASE) == []


def test_does_not_normalize_or_deduplicate_canonically() -> None:
    """Canonical dedup belongs to the frontier, not here."""
    html = '<a href="/a">x</a><a href="/a?utm_source=z">y</a><a href="/A">z</a>'
    assert len(extract_links(html, BASE)) == 3
