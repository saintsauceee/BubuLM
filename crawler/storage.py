"""Raw HTML persistence to the local filesystem.

Stores response bodies exactly as received -- no decoding, no cleaning -- so the
bytes on disk can be re-extracted later without re-crawling. The filename is
derived from the normalized URL, which makes saving idempotent: crawling the
same page twice overwrites one file rather than accumulating copies.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from crawler.urls import normalize_url

#: Characters of the URL digest used to shard files across subdirectories.
SHARD_WIDTH = 2


class RawHtmlStore:
    """Writes raw response bodies under ``root``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def key_for(self, url: str) -> str:
        """Return the storage key for ``url``: the SHA-256 of its normalized form."""
        return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()

    def path_for(self, url: str) -> Path:
        """Return the file path ``url`` is stored at, without creating anything."""
        key = self.key_for(url)
        return self.root / key[:SHARD_WIDTH] / f"{key}.html"

    def save(self, url: str, body: bytes) -> Path:
        """Write ``body`` for ``url`` and return the path written."""
        path = self.path_for(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return path

    def exists(self, url: str) -> bool:
        """Whether a body has already been stored for ``url``."""
        return self.path_for(url).is_file()
