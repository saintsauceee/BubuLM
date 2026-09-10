# Crawling

The single-machine crawler walks outward from seed URLs, storing raw HTML on
local disk. It reuses the single-page pipeline for everything to do with
fetching and validating a page.

## Flow

```text
seeds → frontier → fetch → save raw HTML → extract links
      → normalize → skip seen → enqueue unseen → repeat
```

| Module | Responsibility |
| --- | --- |
| `crawler/links.py` | Pull `<a href>` values, resolve them against the page (or its `<base href>`) |
| `crawler/frontier.py` | Breadth-first queue plus the visited set; normalizes every URL on the way in |
| `crawler/storage.py` | Write raw response bytes to disk, keyed by the normalized URL |
| `crawler/politeness.py` | Per-host minimum interval between requests |
| `crawler/crawl.py` | `SiteCrawler`, the loop that sequences the above |

Fetching, retries, content-type filtering, and size limits are **not**
reimplemented here -- `SiteCrawler` calls the existing `Fetcher`, so a page
rejected by the single-page pipeline is rejected identically during a crawl.

## Running a crawl

```bash
uv run python -m crawler crawl https://example.com --output ./raw
```

Multiple seeds are accepted, and every seed host is in scope:

```bash
uv run python -m crawler crawl https://a.example.com https://b.example.com --output ./raw
```

Options:

```text
--output DIR        directory to store raw HTML in (default: ./raw)
--max-pages N       stop after this many pages (default: 50)
--max-depth N       link depth from the seeds; 0 crawls seeds only (default: 2)
--delay S           minimum seconds between requests to one host (default: 1.0)
--follow-external   follow links off the seed hosts (default: stay on them)
--timeout S         request timeout in seconds
--max-bytes N       maximum response size in bytes
```

Output lists each page with its depth, stored size, and link count, then a
summary. Failures go to stderr and do not stop the crawl:

```text
  [d0]     249 B    4 links  http://localhost:8811/
  [d1]     121 B    3 links  http://localhost:8811/a.html
  [d2] FetchError: http://localhost:8811/gone.html

pages crawled: 5
failures:      1
urls seen:     6
bytes stored:  759
```

Exit code is `0` when at least one page was stored, `1` when none was.

The default output directory `./raw/` is gitignored, so a crawl run from the
repository root never leaves crawled pages staged for commit. Point `--output`
somewhere outside the repository for anything you intend to keep.

## Using it as a library

```python
from pathlib import Path
from crawler import CrawlSettings, RawHtmlStore, SiteCrawler

settings = CrawlSettings(max_pages=100, max_depth=3, delay_per_domain=1.0)

with SiteCrawler(RawHtmlStore(Path("./raw")), settings) as crawler:
    report = crawler.crawl(["https://example.com"])

print(report.pages_crawled, report.bytes_stored)
for failure in report.failures:
    print(failure.reason, failure.url)
```

`CrawlReport` holds `pages` (`CrawledPage`: url, depth, path, content type,
size, link count), `failures` (`CrawlFailure`: url, depth, reason, detail), and
`urls_seen`.

## Settings

| Setting | Default | Purpose |
| --- | --- | --- |
| `max_pages` | `50` | Pages stored before the crawl stops |
| `max_depth` | `2` | Link distance from the seeds; 0 means seeds only |
| `delay_per_domain` | `1.0` | Minimum seconds between requests to one host |
| `follow_external_links` | `False` | Whether to leave the seed hosts |

Fetch-level limits (timeout, size cap, retries, accepted content types) come
from `CrawlerConfig`, unchanged from the single-page pipeline.

## Behaviour notes

**Normalization happens in one place.** `extract_links` returns absolute URLs
without canonicalizing them; `Frontier.add` normalizes and deduplicates. That
keeps the visited set and the normalizer from drifting apart, so `/a`,
`/a#top`, `/a?utm_source=x`, and `HTTPS://HOST/a` are one entry.

**Cycles terminate** because the visited set survives popping: a URL that has
been dequeued is never enqueued again.

**Breadth-first.** The frontier is FIFO, so a shallow crawl sees the pages
closest to the seeds first.

**Failures never stop a crawl.** Any `CrawlError` -- a 404, a rejected content
type, an oversized body, exhausted retries -- is recorded in
`report.failures` and the loop moves on.

**Storage is idempotent.** The filename is the SHA-256 of the normalized URL,
sharded by the first two hex characters, so re-crawling a page overwrites one
file rather than accumulating copies. Bytes are stored exactly as received:
no decoding, no cleaning.

**Politeness is per host.** Each host has its own clock, so a crawl across
several domains is not serialized behind the slowest one. Time spent fetching
counts towards the delay.

## Not included yet

`robots.txt`, crawl-delay directives from a site's own policy, concurrency,
resuming an interrupted crawl, and any persistent record mapping stored files
back to their URLs. That mapping arrives with the corpus format; today the
`url → path` association lives only in the returned `CrawlReport`.

**URL deduplication is not content deduplication.** `/` and `/index.html` are
distinct URLs and are stored as two files even when the bytes are identical.
Collapsing those is the corpus stage's job, using the existing `content_hash`
over extracted text.
