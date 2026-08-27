"""Crawler configuration.

All tunable limits live in one frozen dataclass. Nothing in the crawler reads
environment variables or global state, so a future distributed worker can build
its own config and hand it to the same components.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_USER_AGENT = "BubuLM/0.1 (+https://github.com/saintsauceee/BubuLM)"

#: Content types the crawler will extract text from.
DEFAULT_ACCEPTED_CONTENT_TYPES = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
    }
)

#: HTTP status codes worth retrying: rate limiting and transient server errors.
DEFAULT_RETRY_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class CrawlerConfig:
    """Explicit, immutable configuration for a single crawl."""

    # Fetching
    request_timeout: float = 10.0
    max_redirects: int = 5
    user_agent: str = DEFAULT_USER_AGENT

    # Content-type filtering
    accepted_content_types: frozenset[str] = field(
        default_factory=lambda: DEFAULT_ACCEPTED_CONTENT_TYPES
    )

    # Response-size limit, applied to the raw body in bytes.
    max_response_bytes: int = 5 * 1024 * 1024

    # Retry handling for transient failures.
    max_attempts: int = 3
    retry_backoff_base: float = 0.5
    retry_backoff_max: float = 8.0
    retry_status_codes: frozenset[int] = field(default_factory=lambda: DEFAULT_RETRY_STATUS_CODES)

    # Text quality filtering, applied to the extracted text.
    min_text_length: int = 200
    max_text_length: int = 1_000_000
    min_alpha_ratio: float = 0.5

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        if self.min_text_length < 0:
            raise ValueError("min_text_length must not be negative")
        if self.max_text_length < self.min_text_length:
            raise ValueError("max_text_length must be >= min_text_length")
        if not 0.0 <= self.min_alpha_ratio <= 1.0:
            raise ValueError("min_alpha_ratio must be between 0.0 and 1.0")
