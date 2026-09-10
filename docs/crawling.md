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
| `crawler/crawl.py` | `SiteCrawler`, the sequential loop that sequences the above |
| `crawler/crawl_async.py` | `AsyncSiteCrawler`, the same walk with pages in flight at once |
| `crawler/fetch_async.py` | `AsyncFetcher`, the concurrent twin of `Fetcher` |

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
--concurrency N     pages in flight at once (default: 4)
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

## Concurrency

The CLI crawls concurrently. `--concurrency` sets how many pages may be in
flight at once; per-host politeness still applies on top, so raising it speeds
up a crawl spanning several hosts far more than one confined to a single host.

Against a server with 100 ms of latency, crawling 12 pages:

```text
--concurrency 1  ->  ~1.5s
--concurrency 4  ->  ~0.65s
```

### How correctness is preserved

Everything runs on one event loop, so shared state is safe as long as each
read-modify-write is free of `await` -- a critical section with no await point
cannot be interleaved by another task. Three pieces of state matter:

| State | Guard |
| --- | --- |
| Visited set | `Frontier.admit` decides and records in one uninterrupted step, so a URL is handed to at most one worker, ever |
| Page budget | A reservation count claimed *before* fetching, so workers in flight cannot overshoot `max_pages`. A failed fetch returns its slot, keeping the budget a count of *stored* pages |
| Per-host schedule | `AsyncPerDomainDelay` reserves when a host may *next* be contacted, rather than recording when it was *last* contacted, so concurrent waiters queue rather than waking together |

`AsyncSiteCrawler` runs a fixed pool of `concurrency` worker tasks against an
`asyncio.Queue`. Termination is `Queue.join`: a page's links are enqueued
before its own `task_done`, so the join cannot complete while work is still
reachable.

A failure is isolated to its page. `CrawlError` becomes a recorded failure, and
the worker also catches unexpected exceptions as a backstop -- otherwise a bug
on one page would kill a worker and quietly shrink the pool.

### Sequential and concurrent crawlers

`SiteCrawler` (sequential) and `AsyncSiteCrawler` differ only in scheduling.
They share the admission rules, storage, report types, and fetch rules, and
produce the same set of pages for the same site and settings. The sequential
crawler needs no event loop, which keeps it usable from ordinary sync code;
the concurrent one is what the CLI uses.

Page order in the report is *completion* order, so with concurrency above 1 it
is not deterministic. The set of pages is.

## Using it as a library

Sequentially, with no event loop:

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

Concurrently:

```python
import asyncio
from pathlib import Path
from crawler import AsyncSiteCrawler, CrawlSettings, RawHtmlStore

settings = CrawlSettings(max_pages=100, max_depth=3, delay_per_domain=1.0, concurrency=8)

async def main() -> None:
    async with AsyncSiteCrawler(RawHtmlStore(Path("./raw")), settings) as crawler:
        report = await crawler.crawl(["https://example.com"])
    print(report.pages_crawled, report.bytes_stored)

asyncio.run(main())
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
| `concurrency` | `4` | Pages in flight at once; `AsyncSiteCrawler` only |

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

`robots.txt`, crawl-delay directives from a site's own policy,
resuming an interrupted crawl, and any persistent record mapping stored files
back to their URLs. That mapping arrives with the corpus format; today the
`url → path` association lives only in the returned `CrawlReport`.

**URL deduplication is not content deduplication.** `/` and `/index.html` are
distinct URLs and are stored as two files even when the bytes are identical.
Collapsing those is the corpus stage's job, using the existing `content_hash`
over extracted text.
