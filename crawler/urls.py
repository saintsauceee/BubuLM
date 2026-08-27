"""URL normalization.

Normalizing before fetching keeps the crawl frontier free of URLs that differ
textually but point at the same document. This module is pure and has no
network or configuration dependencies.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from crawler.errors import InvalidURLError

SUPPORTED_SCHEMES = frozenset({"http", "https"})

DEFAULT_PORTS = {"http": 80, "https": 443}

#: Query parameters that identify a marketing campaign rather than a document.
TRACKING_PARAM_PREFIXES = ("utm_",)
TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "msclkid",
        "ref_src",
    }
)


def _is_tracking_param(name: str) -> bool:
    lowered = name.lower()
    return lowered in TRACKING_PARAMS or lowered.startswith(TRACKING_PARAM_PREFIXES)


def _resolve_dot_segments(path: str) -> str:
    """Collapse ``.`` and ``..`` segments, per RFC 3986 section 5.2.4."""
    resolved: list[str] = []
    for segment in path.split("/"):
        if segment == ".":
            continue
        if segment == "..":
            if resolved:
                resolved.pop()
            continue
        resolved.append(segment)
    return "/".join(resolved)


def normalize_url(url: str, *, drop_tracking_params: bool = True) -> str:
    """Return a canonical form of ``url``.

    Applies the normalizations that are safe for arbitrary sites:

    * strips surrounding whitespace
    * lowercases the scheme and host, and drops a trailing dot on the host
    * removes the default port for the scheme
    * removes the fragment
    * resolves ``.`` and ``..`` path segments and supplies a ``/`` for an empty path
    * sorts query parameters and, by default, drops tracking parameters

    Raises:
        InvalidURLError: if the URL is empty, has no host, or uses a scheme
            other than http/https.
    """
    candidate = url.strip()
    if not candidate:
        raise InvalidURLError("URL is empty")

    parts = urlsplit(candidate)

    scheme = parts.scheme.lower()
    if not scheme:
        raise InvalidURLError(f"URL has no scheme: {url!r}")
    if scheme not in SUPPORTED_SCHEMES:
        raise InvalidURLError(f"Unsupported URL scheme {scheme!r}: {url!r}")

    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise InvalidURLError(f"URL has no host: {url!r}")

    netloc = host
    if parts.port is not None and parts.port != DEFAULT_PORTS[scheme]:
        netloc = f"{host}:{parts.port}"

    path = _resolve_dot_segments(parts.path) or "/"

    query = parts.query
    if query:
        pairs = parse_qsl(query, keep_blank_values=True)
        if drop_tracking_params:
            pairs = [(name, value) for name, value in pairs if not _is_tracking_param(name)]
        query = urlencode(sorted(pairs))

    return urlunsplit((scheme, netloc, path, query, ""))
