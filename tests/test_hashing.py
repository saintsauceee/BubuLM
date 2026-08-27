"""Tests for deterministic content hashing."""

from __future__ import annotations

import hashlib

from crawler.hashing import content_hash


def test_hash_is_deterministic() -> None:
    assert content_hash("hello world") == content_hash("hello world")


def test_hash_is_sha256_hex() -> None:
    digest = content_hash("hello world")
    assert len(digest) == 64
    assert set(digest) <= set("0123456789abcdef")
    assert digest == hashlib.sha256(b"hello world").hexdigest()


def test_different_text_hashes_differently() -> None:
    assert content_hash("document one") != content_hash("document two")


def test_hash_ignores_surrounding_whitespace() -> None:
    assert content_hash("  body text\n\n") == content_hash("body text")


def test_hash_is_sensitive_to_interior_text() -> None:
    assert content_hash("a b") != content_hash("a  b")


def test_hash_normalizes_equivalent_unicode() -> None:
    decomposed = "café"
    composed = "café"
    assert decomposed != composed
    assert content_hash(decomposed) == content_hash(composed)


def test_empty_text_hashes_stably() -> None:
    assert content_hash("") == content_hash("   \n  ")
