"""Text quality filtering.

Rejects extracted text that is too short, too long, or does not look like
natural-language prose, so obviously unusable documents never reach the corpus.
"""

from __future__ import annotations

from crawler.config import CrawlerConfig
from crawler.errors import QualityRejectedError


def alpha_ratio(text: str) -> float:
    """Return the fraction of non-whitespace characters that are alphabetic.

    Returns ``0.0`` for text with no non-whitespace characters. A page of pure
    markup residue, numbers, or punctuation scores near zero; ordinary prose
    scores well above 0.8.
    """
    considered = [char for char in text if not char.isspace()]
    if not considered:
        return 0.0
    return sum(1 for char in considered if char.isalpha()) / len(considered)


def check_text_quality(text: str, config: CrawlerConfig) -> None:
    """Raise :class:`QualityRejectedError` if ``text`` fails any quality filter."""
    if not text.strip():
        raise QualityRejectedError("Extracted text is empty")

    length = len(text)
    if length < config.min_text_length:
        raise QualityRejectedError(
            f"Extracted text is {length} characters, minimum is {config.min_text_length}"
        )
    if length > config.max_text_length:
        raise QualityRejectedError(
            f"Extracted text is {length} characters, maximum is {config.max_text_length}"
        )

    ratio = alpha_ratio(text)
    if ratio < config.min_alpha_ratio:
        raise QualityRejectedError(
            f"Extracted text is {ratio:.2f} alphabetic, minimum is {config.min_alpha_ratio:.2f}"
        )
