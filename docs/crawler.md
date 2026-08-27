# Crawler

The single-worker crawler turns one seed URL into a clean training document.

## Pipeline

```text
normalize URL → fetch → decode → extract text → quality check → hash
```

Each stage is its own module, so a future distributed worker can reuse the
pieces without inheriting this orchestration.

| Module | Responsibility |
| --- | --- |
| `crawler/urls.py` | Canonicalize a URL before it is fetched or stored |
| `crawler/fetch.py` | HTTP/HTTPS retrieval, size cap, retries |
| `crawler/content_types.py` | Accept (X)HTML, reject everything else |
| `crawler/extract.py` | Decode bytes, strip boilerplate, produce clean text |
| `crawler/quality.py` | Reject text that is too short, too long, or not prose |
| `crawler/hashing.py` | Deterministic SHA-256 of the cleaned text |
| `crawler/pipeline.py` | `Crawler`, which wires the stages together |
| `crawler/config.py` | `CrawlerConfig`, every tunable limit in one place |

## Running it locally

```bash
uv sync --all-groups
uv run python -m crawler https://example.com
```

Emit the full document as JSON:

```bash
uv run python -m crawler https://example.com --json
```

Options:

```text
--max-bytes N    maximum response size in bytes (default 5242880)
--min-text N     minimum extracted text length (default 200)
--max-text N     maximum extracted text length (default 1000000)
--timeout S      request timeout in seconds (default 10.0)
--attempts N     maximum fetch attempts (default 3)
--json           emit JSON instead of a summary plus text
```

Exit code is `0` on success and `1` when the URL is rejected at any stage; the
reason is printed to stderr, for example `ContentTypeRejectedError: Rejected
content type 'image/png'`.

### Trying it without hitting the public internet

```bash
python3 -m http.server 8000 --directory /path/to/some/html &
uv run python -m crawler http://localhost:8000/
```

## Using it as a library

```python
from crawler import Crawler, CrawlerConfig

config = CrawlerConfig(max_response_bytes=2_000_000, min_text_length=500)

with Crawler(config) as crawler:
    document = crawler.crawl("https://example.com")

print(document.title, document.content_hash, document.text_length)
```

`crawl()` returns a `CrawlDocument` (`url`, `final_url`, `title`, `text`,
`content_hash`, `content_type`, `fetched_bytes`) or raises a `CrawlError`
subclass naming the stage that rejected the URL:

| Exception | Meaning |
| --- | --- |
| `InvalidURLError` | Empty, host-less, or non-HTTP(S) URL |
| `FetchError` | Non-retryable HTTP error, such as a 404 or a redirect loop |
| `TransientFetchError` | Timeouts or 5xx responses, after retries were exhausted |
| `ContentTypeRejectedError` | The response was not (X)HTML |
| `ResponseTooLargeError` | The body exceeded `max_response_bytes` |
| `QualityRejectedError` | The extracted text failed the quality filters |

## Configuration

All limits live in the frozen `CrawlerConfig` dataclass. Nothing reads
environment variables or global state, and invalid combinations are rejected at
construction time.

| Setting | Default | Purpose |
| --- | --- | --- |
| `request_timeout` | `10.0` | Per-request timeout in seconds |
| `max_redirects` | `5` | Redirect hops to follow |
| `user_agent` | `BubuLM/0.1 (+…)` | Sent on every request |
| `accepted_content_types` | `text/html`, `application/xhtml+xml` | Everything else is rejected |
| `max_response_bytes` | `5 MiB` | Body size cap |
| `max_attempts` | `3` | Total fetch attempts |
| `retry_backoff_base` | `0.5` | First backoff delay, doubling per attempt |
| `retry_backoff_max` | `8.0` | Backoff ceiling in seconds |
| `retry_status_codes` | `408, 425, 429, 500, 502, 503, 504` | Statuses worth retrying |
| `min_text_length` | `200` | Shortest acceptable document |
| `max_text_length` | `1_000_000` | Longest acceptable document |
| `min_alpha_ratio` | `0.5` | Fraction of characters that must be letters |

## Behaviour notes

**URL normalization** lowercases the scheme and host, drops the default port,
removes the fragment, resolves `.` and `..` segments, sorts query parameters,
and strips tracking parameters (`utm_*`, `fbclid`, `gclid`, and similar). Path
case and trailing slashes are preserved, since both can be significant.

**Size limits** are enforced twice: the advertised `Content-Length` is checked
before the body is downloaded, and the streamed body is aborted the moment it
exceeds the cap.

**Extraction** uses the standard library's `html.parser`, so there is no
third-party HTML dependency. It drops `script`, `style`, `svg`, `form`, and
similar subtrees, drops `nav`/`header`/`footer`/`aside` containers, and prefers
the text inside `<main>`/`<article>` when that region holds at least 100
characters. Whitespace inside `<pre>` is collapsed like any other text.

**Retries** cover transport errors and the retryable status codes, with
exponential backoff. Rejections that would not change on a second attempt --
bad content type, oversized body, redirect loop, failed quality check -- are
never retried.

**No httpx exception escapes.** Every failure httpx can raise is translated
into a `CrawlError`: transport errors stay retryable, while deterministic
failures (redirect loops, decoding errors, malformed URLs) become permanent
ones. A caller catching `CrawlError` catches everything the crawler can fail
with.

## Running the tests

```bash
uv run pytest                          # everything
uv run pytest tests/test_urls.py       # one module
uv run pytest tests/test_integration.py  # local HTTP server only
```

Unit tests serve responses through `httpx.MockTransport`; the integration tests
start a real HTTP server on an ephemeral loopback port. No test reaches the
public internet, so the suite is deterministic and runs offline.

## Not included yet

Queueing, deduplication storage, link discovery, politeness delays, and
`robots.txt` handling arrive alongside the distributed crawler.
