"""Tests for HTML to clean text extraction."""

from __future__ import annotations

import pytest

from crawler.extract import clean_text, decode_html, extract_text
from tests.conftest import html_page


def test_extracts_visible_text() -> None:
    document = extract_text(html_page("<p>Hello world</p>"))
    assert document.text == "Hello world"


def test_extracts_the_title() -> None:
    document = extract_text(html_page("<p>Body</p>", title="A Good Article"))
    assert document.title == "A Good Article"


def test_title_is_not_part_of_the_body_text() -> None:
    document = extract_text(html_page("<p>Body</p>", title="A Good Article"))
    assert "A Good Article" not in document.text


def test_missing_title_is_none() -> None:
    document = extract_text("<html><body><p>Body</p></body></html>")
    assert document.title is None


@pytest.mark.parametrize(
    "markup",
    [
        "<script>var secret = 1;</script>",
        "<style>body { color: red; }</style>",
        "<noscript>Enable JavaScript</noscript>",
        "<template><p>unused</p></template>",
        "<svg><text>icon</text></svg>",
        "<form><textarea>draft</textarea></form>",
        "<iframe>embedded</iframe>",
        "<button>Click me</button>",
    ],
)
def test_drops_non_prose_elements(markup: str) -> None:
    document = extract_text(html_page(f"{markup}<p>Real content</p>"))
    assert document.text == "Real content"


@pytest.mark.parametrize("tag", ["nav", "header", "footer", "aside"])
def test_drops_boilerplate_containers(tag: str) -> None:
    markup = f"<{tag}><a href='/'>Home</a> <a href='/about'>About</a></{tag}><p>Real content</p>"
    document = extract_text(html_page(markup))
    assert document.text == "Real content"


def test_separates_block_elements_with_newlines() -> None:
    document = extract_text(html_page("<p>First</p><p>Second</p>"))
    assert document.text == "First\nSecond"


def test_keeps_inline_elements_on_one_line() -> None:
    document = extract_text(html_page("<p>Hello <strong>brave</strong> <em>world</em></p>"))
    assert document.text == "Hello brave world"


def test_breaks_lines_on_br() -> None:
    document = extract_text(html_page("<p>First<br>Second</p>"))
    assert document.text == "First\nSecond"


def test_decodes_html_entities() -> None:
    document = extract_text(html_page("<p>Tom &amp; Jerry &lt;3 caf&eacute;</p>"))
    assert document.text == "Tom & Jerry <3 café"


def test_collapses_whitespace() -> None:
    document = extract_text(html_page("<p>Lots   of\t\tspace\n\n   here</p>"))
    assert document.text == "Lots of space here"


def test_prefers_main_region_when_substantial() -> None:
    sidebar = "<div>Related links and promotional filler content</div>"
    article = "<main><p>" + ("The real article body. " * 10) + "</p></main>"
    document = extract_text(html_page(sidebar + article))
    assert "Related links" not in document.text
    assert document.text.startswith("The real article body.")


def test_falls_back_to_body_when_main_is_trivial() -> None:
    markup = "<main><p>Hi</p></main><p>" + ("The actual page content. " * 10) + "</p>"
    document = extract_text(html_page(markup))
    assert "The actual page content." in document.text


def test_article_tag_is_treated_as_main_content() -> None:
    markup = "<div>Site chrome</div><article><p>" + ("Story text. " * 20) + "</p></article>"
    document = extract_text(html_page(markup))
    assert "Site chrome" not in document.text


def test_extraction_is_deterministic() -> None:
    markup = html_page("<main><p>Stable</p><p>Output</p></main>")
    assert extract_text(markup).text == extract_text(markup).text


@pytest.mark.parametrize(
    "markup",
    [
        "<html><body><p>Unclosed paragraph",
        "<p>Mismatched</div></p>",
        "<html><body><p>Text</p></body>",
        "<div><span>Nested <div>badly</span></div>",
        "<p>Text<unknown-tag>more</unknown-tag></p>",
        "<!-- comment only --><p>Text</p>",
        "<p>Text</p><script>unclosed",
    ],
)
def test_tolerates_malformed_markup(markup: str) -> None:
    document = extract_text(markup)
    assert isinstance(document.text, str)


def test_empty_document_yields_empty_text() -> None:
    document = extract_text("")
    assert document.text == ""
    assert document.title is None


def test_document_with_only_scripts_yields_empty_text() -> None:
    document = extract_text(html_page("<script>var a = 1;</script><style>p{}</style>"))
    assert document.text == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  spaced  ", "spaced"),
        ("a\n\n\nb", "a\nb"),
        ("a\xa0b", "a b"),
        ("line\t\tone", "line one"),
        ("\n\n", ""),
    ],
)
def test_clean_text(raw: str, expected: str) -> None:
    assert clean_text(raw) == expected


def test_decodes_using_the_declared_charset() -> None:
    body = "<p>café</p>".encode("iso-8859-1")
    assert decode_html(body, "iso-8859-1") == "<p>café</p>"


def test_decodes_using_the_meta_charset_when_header_is_absent() -> None:
    body = '<html><head><meta charset="iso-8859-1"></head><body>café</body></html>'.encode(
        "iso-8859-1"
    )
    assert "café" in decode_html(body, None)


def test_falls_back_to_utf8() -> None:
    assert decode_html("<p>café</p>".encode(), None) == "<p>café</p>"


def test_undecodable_bytes_do_not_raise() -> None:
    assert isinstance(decode_html(b"\xff\xfe<p>x</p>", "utf-8"), str)


def test_unknown_charset_falls_back() -> None:
    assert decode_html(b"<p>ok</p>", "not-a-real-charset") == "<p>ok</p>"
