"""HTML to clean text extraction.

Built on the standard library's :mod:`html.parser`, so extraction has no
third-party dependency and behaves identically on every machine.

The strategy is deliberately simple and predictable:

* discard the contents of non-prose elements (``script``, ``style``, ``svg``, ...)
* discard common boilerplate containers (``nav``, ``header``, ``footer``, ``aside``)
* prefer the text inside ``<main>``/``<article>`` when the page marks it up and
  that region carries a meaningful amount of text
* insert newlines at block boundaries, then normalize whitespace
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser

#: Elements whose entire subtree is dropped -- they never contain page prose.
SKIP_TAGS = frozenset(
    {
        "button",
        "canvas",
        "embed",
        "form",
        "iframe",
        "noscript",
        "object",
        "script",
        "select",
        "style",
        "svg",
        "template",
        "textarea",
    }
)

#: Containers that usually hold navigation or site furniture rather than content.
BOILERPLATE_TAGS = frozenset({"aside", "footer", "header", "nav"})

#: Elements that mark the page's primary content region.
MAIN_TAGS = frozenset({"article", "main"})

#: Elements that force a line break in the extracted text.
BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "li",
        "main",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
)

#: Minimum characters inside <main>/<article> before that region is trusted
#: over the whole document body.
MAIN_CONTENT_MIN_CHARS = 100

_META_CHARSET_RE = re.compile(rb"""charset=["']?\s*([a-zA-Z0-9_\-]+)""", re.IGNORECASE)
_INLINE_WHITESPACE_RE = re.compile(r"[^\S\n]+")
_SOURCE_NEWLINE_RE = re.compile(r"[\r\n]+")


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    """The result of extracting text from an HTML document."""

    title: str | None
    text: str


def decode_html(body: bytes, charset: str | None = None) -> str:
    """Decode raw HTML bytes to text.

    Prefers the charset from the Content-Type header, falls back to a ``<meta>``
    declaration in the document head, and finally to UTF-8. Undecodable bytes are
    replaced rather than raising, so a single bad byte cannot lose a document.
    """
    for candidate in (charset, _sniff_meta_charset(body), "utf-8"):
        if not candidate:
            continue
        try:
            return body.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def _sniff_meta_charset(body: bytes) -> str | None:
    match = _META_CHARSET_RE.search(body[:4096])
    if match is None:
        return None
    return match.group(1).decode("ascii", errors="ignore").lower() or None


class _TextExtractor(HTMLParser):
    """Collects document text and, separately, main-region text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._all: list[str] = []
        self._main: list[str] = []
        self._title: list[str] = []
        self._skip_depth = 0
        self._boilerplate_depth = 0
        self._main_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag in BOILERPLATE_TAGS:
            self._boilerplate_depth += 1
        if tag in MAIN_TAGS:
            self._main_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in BLOCK_TAGS:
            self._emit("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in BOILERPLATE_TAGS:
            self._boilerplate_depth = max(0, self._boilerplate_depth - 1)
        if tag in MAIN_TAGS:
            self._main_depth = max(0, self._main_depth - 1)
        if tag == "title":
            self._in_title = False
        if tag in BLOCK_TAGS:
            self._emit("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title.append(data)
        # Newlines in the source are ordinary whitespace in HTML; only block
        # tags introduce line breaks in the extracted text.
        self._emit(_SOURCE_NEWLINE_RE.sub(" ", data))

    def _emit(self, text: str) -> None:
        if self._skip_depth or self._in_title:
            return
        if self._boilerplate_depth:
            return
        self._all.append(text)
        if self._main_depth:
            self._main.append(text)

    def result(self) -> ExtractedDocument:
        main_text = clean_text("".join(self._main))
        body_text = clean_text("".join(self._all))
        text = main_text if len(main_text) >= MAIN_CONTENT_MIN_CHARS else body_text

        title = clean_text("".join(self._title)) or None
        return ExtractedDocument(title=title, text=text)


def clean_text(raw: str) -> str:
    """Normalize extracted text: unify unicode, collapse whitespace, drop blank lines."""
    normalized = unicodedata.normalize("NFC", raw).replace("\xa0", " ")
    lines = (_INLINE_WHITESPACE_RE.sub(" ", line).strip() for line in normalized.split("\n"))
    return "\n".join(line for line in lines if line)


def extract_text(html: str) -> ExtractedDocument:
    """Extract the title and main textual content from an HTML document.

    Malformed markup is tolerated: unbalanced or unknown tags never raise, they
    simply produce the best text the parser can recover.
    """
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return parser.result()
