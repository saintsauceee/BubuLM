"""In-memory URL frontier with a visited set.

The frontier is the single place where a URL becomes canonical: every URL is
normalized on the way in, and the visited set is keyed on the normalized form.
That is what makes ``/a/`` and ``HTTPS://HOST/a/?utm_source=x#top`` one entry
rather than three.

FIFO order gives breadth-first traversal, so a shallow crawl sees the pages
closest to the seeds first.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

from crawler.errors import InvalidURLError
from crawler.urls import normalize_url


@dataclass(frozen=True, slots=True)
class FrontierItem:
    """A URL waiting to be crawled, with the depth it was discovered at."""

    url: str
    """The normalized URL."""

    depth: int
    """Seeds are depth 0; links found on a depth-``n`` page are depth ``n + 1``."""


class Frontier:
    """A breadth-first queue of normalized URLs that never repeats itself."""

    def __init__(
        self,
        *,
        max_depth: int | None = None,
        allowed_hosts: frozenset[str] | None = None,
    ) -> None:
        self.max_depth = max_depth
        self.allowed_hosts = allowed_hosts
        self._queue: deque[FrontierItem] = deque()
        self._seen: set[str] = set()

    def __len__(self) -> int:
        """The number of URLs still waiting to be crawled."""
        return len(self._queue)

    @property
    def seen_count(self) -> int:
        """How many distinct URLs have ever been enqueued."""
        return len(self._seen)

    def has_seen(self, url: str) -> bool:
        """Whether ``url`` normalizes to something already enqueued."""
        try:
            return normalize_url(url) in self._seen
        except InvalidURLError:
            return False

    def add(self, url: str, depth: int = 0) -> bool:
        """Enqueue ``url`` at ``depth``. Returns whether it was actually added.

        A URL is skipped when it is malformed, already seen, deeper than
        ``max_depth``, or on a host outside ``allowed_hosts``.
        """
        if self.max_depth is not None and depth > self.max_depth:
            return False
        try:
            normalized = normalize_url(url)
        except InvalidURLError:
            return False
        if normalized in self._seen:
            return False
        if not self._host_allowed(normalized):
            return False

        self._seen.add(normalized)
        self._queue.append(FrontierItem(url=normalized, depth=depth))
        return True

    def add_seeds(self, urls: Iterable[str]) -> int:
        """Enqueue seed URLs at depth 0. Returns how many were added."""
        return sum(1 for url in urls if self.add(url, depth=0))

    def pop(self) -> FrontierItem | None:
        """Take the next URL, or None when the frontier is empty."""
        if not self._queue:
            return None
        return self._queue.popleft()

    def _host_allowed(self, normalized: str) -> bool:
        if self.allowed_hosts is None:
            return True
        return (urlsplit(normalized).hostname or "") in self.allowed_hosts


def hosts_of(urls: Iterable[str]) -> frozenset[str]:
    """Return the hostnames of ``urls``, ignoring any that cannot be parsed."""
    hosts: set[str] = set()
    for url in urls:
        try:
            host = urlsplit(normalize_url(url)).hostname
        except InvalidURLError:
            continue
        if host:
            hosts.add(host)
    return frozenset(hosts)
