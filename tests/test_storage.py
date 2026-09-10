"""Tests for raw HTML persistence."""

from __future__ import annotations

from pathlib import Path

from crawler.storage import RawHtmlStore

BODY = b"<html><body><p>Raw bytes</p></body></html>"


def test_save_writes_the_exact_bytes(tmp_path: Path) -> None:
    store = RawHtmlStore(tmp_path)
    path = store.save("https://example.com/a", BODY)

    assert path.read_bytes() == BODY


def test_save_does_not_decode_or_clean(tmp_path: Path) -> None:
    """Raw means raw: scripts, markup, and odd bytes survive untouched."""
    raw = b"<html><script>var a=1;</script><p>Caf\xe9</p></html>"
    path = RawHtmlStore(tmp_path).save("https://example.com/a", raw)

    assert path.read_bytes() == raw


def test_save_creates_missing_directories(tmp_path: Path) -> None:
    store = RawHtmlStore(tmp_path / "does" / "not" / "exist")
    path = store.save("https://example.com/a", BODY)

    assert path.is_file()


def test_path_is_deterministic(tmp_path: Path) -> None:
    store = RawHtmlStore(tmp_path)
    assert store.path_for("https://example.com/a") == store.path_for("https://example.com/a")


def test_equivalent_urls_share_one_file(tmp_path: Path) -> None:
    store = RawHtmlStore(tmp_path)
    first = store.save("https://example.com/a", b"first")
    second = store.save("HTTPS://EXAMPLE.COM:443/a#top", b"second")

    assert first == second
    assert first.read_bytes() == b"second"
    assert len(list(tmp_path.rglob("*.html"))) == 1


def test_different_urls_get_different_files(tmp_path: Path) -> None:
    store = RawHtmlStore(tmp_path)
    store.save("https://example.com/a", BODY)
    store.save("https://example.com/b", BODY)

    assert len(list(tmp_path.rglob("*.html"))) == 2


def test_files_are_sharded_by_digest_prefix(tmp_path: Path) -> None:
    store = RawHtmlStore(tmp_path)
    path = store.save("https://example.com/a", BODY)
    key = store.key_for("https://example.com/a")

    assert path.parent.name == key[:2]
    assert path.name == f"{key}.html"


def test_path_for_does_not_create_anything(tmp_path: Path) -> None:
    store = RawHtmlStore(tmp_path)
    store.path_for("https://example.com/a")

    assert not any(tmp_path.iterdir())


def test_exists_reports_stored_pages(tmp_path: Path) -> None:
    store = RawHtmlStore(tmp_path)
    assert store.exists("https://example.com/a") is False

    store.save("https://example.com/a", BODY)
    assert store.exists("https://example.com/a") is True
    assert store.exists("https://example.com/b") is False
