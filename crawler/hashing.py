"""Deterministic content hashing.

The hash identifies a *document*, not a URL: two pages whose cleaned text is
identical hash identically, which is what a future deduplication stage needs.
"""

from __future__ import annotations

import hashlib
import unicodedata

HASH_ALGORITHM = "sha256"


def content_hash(text: str) -> str:
    """Return the hex SHA-256 of ``text`` after canonical normalization.

    Normalization (NFC, trailing whitespace stripped) makes the hash stable
    across equivalent unicode encodings of the same characters.
    """
    canonical = unicodedata.normalize("NFC", text).strip()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
