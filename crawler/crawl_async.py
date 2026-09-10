"""The concurrent single-machine crawl loop.

``AsyncSiteCrawler`` runs the same walk as :class:`crawler.crawl.SiteCrawler`
with several pages in flight at once. It reuses the sequential crawler's
admission rules, storage, and report types; only the scheduling differs.

Concurrency model
-----------------

A fixed pool of ``concurrency`` worker tasks pulls from an ``asyncio.Queue``.
Everything runs on one event loop, so shared state is safe as long as each
read-modify-write is free of ``await`` -- a critical section without an await
point cannot be interleaved. Three pieces of state matter:

* **the visited set**, guarded by ``Frontier.admit``, which decides and records
  in one uninterrupted step. A URL is handed to at most one worker, ever.
* **the page budget**, held as a reservation count. A worker claims a slot
  *before* fetching, so N workers in flight cannot overshoot ``max_pages``. A
  failed fetch returns its slot, keeping the budget a count of stored pages.
* **the per-host schedule**, in :class:`AsyncPerDomainDelay`, which reserves a
  host's next slot rather than recording its last.

Termination is ``Queue.join``: a page's links are enqueued before its own
``task_done``, so the join cannot complete while work is still reachable.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
from types import TracebackType

from crawler.config import CrawlerConfig
from crawler.crawl import (
    CrawlReport,
    CrawlSettings,
    make_failure,
    make_page,
    store_and_extract,
)
from crawler.errors import CrawlError
from crawler.fetch_async import AsyncFetcher
from crawler.frontier import Frontier, FrontierItem, hosts_of
from crawler.politeness import AsyncPerDomainDelay
from crawler.storage import RawHtmlStore


@dataclass(slots=True)
class _Run:
    """Mutable state for one crawl, kept off the crawler so runs cannot collide."""

    frontier: Frontier
    queue: asyncio.Queue[FrontierItem]
    report: CrawlReport
    max_pages: int
    reserved: int = field(default=0)

    def reserve(self) -> bool:
        """Claim a slot in the page budget. Returns False when it is spent.

        No ``await`` between the check and the increment, so two workers cannot
        claim the same slot.
        """
        if self.reserved >= self.max_pages:
            return False
        self.reserved += 1
        return True

    def release(self) -> None:
        """Return a slot claimed for a page that turned out not to be stored."""
        self.reserved -= 1

    def enqueue(self, url: str, depth: int) -> bool:
        """Admit ``url`` and queue it if it has not been seen."""
        item = self.frontier.admit(url, depth)
        if item is None:
            return False
        self.queue.put_nowait(item)
        return True


class AsyncSiteCrawler:
    """Crawls outward from seed URLs with several pages in flight at once."""

    def __init__(
        self,
        store: RawHtmlStore,
        settings: CrawlSettings | None = None,
        config: CrawlerConfig | None = None,
        *,
        fetcher: AsyncFetcher | None = None,
        politeness: AsyncPerDomainDelay | None = None,
    ) -> None:
        self.store = store
        self.settings = settings or CrawlSettings()
        self.config = config or CrawlerConfig()
        self._owns_fetcher = fetcher is None
        self._fetcher = fetcher or AsyncFetcher(self.config)
        self._politeness = politeness or AsyncPerDomainDelay(self.settings.delay_per_domain)

    async def __aenter__(self) -> AsyncSiteCrawler:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying fetcher, if this crawler created it."""
        if self._owns_fetcher:
            await self._fetcher.aclose()

    async def crawl(self, seeds: Iterable[str]) -> CrawlReport:
        """Crawl from ``seeds`` until the budget is spent or the frontier empties."""
        seed_list = list(seeds)
        run = _Run(
            frontier=Frontier(
                max_depth=self.settings.max_depth,
                allowed_hosts=None if self.settings.follow_external_links else hosts_of(seed_list),
            ),
            queue=asyncio.Queue(),
            report=CrawlReport(),
            max_pages=self.settings.max_pages,
        )
        for seed in seed_list:
            run.enqueue(seed, 0)

        workers = [asyncio.create_task(self._worker(run)) for _ in range(self.settings.concurrency)]
        try:
            await run.queue.join()
        finally:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

        run.report.urls_seen = run.frontier.seen_count
        return run.report

    async def _worker(self, run: _Run) -> None:
        """Pull items until cancelled. One bad page must not take the worker down.

        ``_visit`` already turns a CrawlError into a recorded failure; the broad
        catch here is a backstop for an unexpected exception, which would
        otherwise kill this worker and quietly shrink the pool.
        """
        while True:
            item = await run.queue.get()
            try:
                await self._visit(item, run)
            except Exception as error:  # noqa: BLE001 - a worker must outlive any page
                run.report.failures.append(make_failure(item.url, item.depth, error))
            finally:
                run.queue.task_done()

    async def _visit(self, item: FrontierItem, run: _Run) -> None:
        """Fetch, store, and enqueue the links of one page."""
        if not run.reserve():
            return

        await self._politeness.wait(item.url)

        try:
            response = await self._fetcher.fetch(item.url)
        except CrawlError as error:
            run.release()
            run.report.failures.append(make_failure(item.url, item.depth, error))
            return

        path, links = store_and_extract(response, self.store)
        for link in links:
            run.enqueue(link, item.depth + 1)

        run.report.pages.append(make_page(response, item.depth, path, len(links)))
