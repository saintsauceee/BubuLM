"""Content-Type parsing and filtering.

The crawler only extracts text from (X)HTML. Everything else -- images, video,
archives, PDFs, arbitrary binaries -- is rejected before the body is parsed.
"""

from __future__ import annotations

from crawler.errors import ContentTypeRejectedError


def parse_content_type(header: str | None) -> tuple[str, str | None]:
    """Split a Content-Type header into a lowercased MIME type and charset.

    Returns ``("", None)`` when the header is missing or empty.
    """
    if not header:
        return "", None

    parts = header.split(";")
    mime = parts[0].strip().lower()

    charset: str | None = None
    for parameter in parts[1:]:
        name, _, value = parameter.partition("=")
        if name.strip().lower() == "charset":
            charset = value.strip().strip('"').lower() or None
            break

    return mime, charset


def is_accepted_content_type(mime: str, accepted: frozenset[str]) -> bool:
    """Return whether ``mime`` is one of the accepted types."""
    return mime in accepted


def ensure_accepted_content_type(
    header: str | None, accepted: frozenset[str]
) -> tuple[str, str | None]:
    """Parse ``header`` and raise unless it names an accepted content type.

    Raises:
        ContentTypeRejectedError: if the header is missing or not accepted.
    """
    mime, charset = parse_content_type(header)
    if not mime:
        raise ContentTypeRejectedError("Response has no Content-Type header")
    if not is_accepted_content_type(mime, accepted):
        raise ContentTypeRejectedError(f"Rejected content type {mime!r}")
    return mime, charset
