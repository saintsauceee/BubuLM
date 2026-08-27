"""Smoke tests confirming the package layout is importable and the toolchain runs."""

import crawler
import llm


def test_packages_are_importable() -> None:
    assert crawler.__name__ == "crawler"
    assert llm.__name__ == "llm"
