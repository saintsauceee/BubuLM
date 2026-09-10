"""Link extraction from ``<a href>`` elements.

Produces absolute http(s) URLs. Normalization and deduplication are deliberately
*not* done here -- the frontier owns those, so there is exactly one place in the
crawl loop where a URL becomes canonical.
"""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

FOLLOWABLE_SCHEMES = frozenset({"http", "https"})


class _LinkExtractor(HTMLParser):
    """Collects ``href`` values, honouring a ``<base href>`` if the page sets one."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []
        self.base_href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "base" and self.base_href is None:
            href = dict(attrs).get("href")
            if href:
                self.base_href = href.strip()
            return
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.hrefs.append(href.strip())


def extract_links(html: str, base_url: str) -> list[str]:
    """Return the absolute http(s) links in ``html``, in document order.

    Relative hrefs are resolved against ``base_url``, or against the document's
    ``<base href>`` when it declares one. Non-followable schemes -- ``mailto:``,
    ``javascript:``, ``tel:``, ``data:`` -- are dropped. Exact duplicate strings
    are collapsed, but canonical deduplication is the frontier's job.

    Malformed markup never raises; it simply yields the links that could be read.
    """
    parser = _LinkExtractor()
    parser.feed(html)
    parser.close()

    base = urljoin(base_url, parser.base_href) if parser.base_href else base_url

    links: list[str] = []
    seen: set[str] = set()
    for href in parser.hrefs:
        absolute = _resolve(base, href)
        if absolute is None or absolute in seen:
            continue
        seen.add(absolute)
        links.append(absolute)
    return links


def _resolve(base: str, href: str) -> str | None:
    """Resolve one href against ``base``, or return None if it is not followable."""
    if not href or href.startswith("#"):
        return None
    try:
        absolute = urljoin(base, href)
    except ValueError:
        return None
    if urlsplit(absolute).scheme.lower() not in FOLLOWABLE_SCHEMES:
        return None
    return absolute
