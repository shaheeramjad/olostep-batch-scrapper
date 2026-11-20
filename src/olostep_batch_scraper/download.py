"""Step 3: download pages as Markdown, concurrently, while the batch runs."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from olostep import AsyncOlostep, BatchItemResult, Olostep_BaseError
from rich.progress import Progress, TaskID

from .utils import atomic_write_text

logger = logging.getLogger(__name__)

ITEMS_PAGE_SIZE = 10_000  # the maximum the items endpoint allows per page
BACKOFF_SECONDS = 1.0  # doubled on each retry: 1s, 2s, ...

# Pages that "succeed" technically but carry no real content. The title is the
# first line of Olostep's Markdown, so we only match there (a docs page *about*
# 404 errors is still real content).
_JUNK_TITLES = {
    "not found (404 page)": re.compile(
        r"page not found|^404\b|404 not found|no longer available"
        r"|page you.re looking for is no longer here",
        re.I,
    ),
    "access denied page": re.compile(r"access denied|^403\b", re.I),
}
# Filter or sign-in pages from the network in front of the scraper, not the site.
_BLOCK_PAGE = re.compile(
    r"page blocked by|this site is blocked|acceptable usage policy", re.I
)


@dataclass(frozen=True)
class PageResult:
    """Outcome for one URL in the batch."""

    custom_id: str
    url: str
    status: str  # "downloaded", "skipped" (already on disk) or "failed"
    path: str = ""
    bytes: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        """True when the Markdown file is on disk."""
        return self.status != "failed"


async def list_items(
    client: AsyncOlostep, batch_id: str, status: str
) -> list[BatchItemResult]:
    """Return all batch items with the given status (``completed``/``failed``).

    This also works while the batch is still running: completed items are
    listed (and retrievable) as soon as they finish.
    """
    items = client.batches.items(
        batch_id,
        batch_size=ITEMS_PAGE_SIZE,
        status=status,
        wait_for_completion=False,  # we track completion with our own poller
    )
    return [item async for item in items]


class Downloader:
    """Downloads batch items with bounded concurrency as they are queued.

    Call :meth:`add` as often as you like (duplicates are ignored), then
    :meth:`drain` to wait for everything queued so far.
    """

    def __init__(
        self,
        client: AsyncOlostep,
        pages_dir: Path,
        *,
        concurrency: int,
        timeout: float,
        retries: int,
        progress: Progress,
        task: TaskID,
    ) -> None:
        self._client = client
        self._pages_dir = pages_dir
        self._semaphore = asyncio.Semaphore(concurrency)
        self._timeout = timeout
        self._retries = retries
        self._progress = progress
        self._task = task
        self._jobs: dict[str, asyncio.Task[PageResult]] = {}
        pages_dir.mkdir(parents=True, exist_ok=True)

    def add(self, items: list[BatchItemResult]) -> int:
        """Queue items that are not queued yet. Returns how many were new."""
        new = [item for item in items if item.custom_id not in self._jobs]
        for item in new:
            self._jobs[item.custom_id] = asyncio.create_task(self._run(item))
        return len(new)

    @property
    def queued(self) -> int:
        """Number of distinct items queued so far."""
        return len(self._jobs)

    async def drain(self) -> list[PageResult]:
        """Wait for every queued download and return the results."""
        return list(await asyncio.gather(*self._jobs.values()))

    async def _run(self, item: BatchItemResult) -> PageResult:
        """Download one item under the concurrency limit."""
        async with self._semaphore:
            result = await download_one(
                self._client,
                item,
                self._pages_dir,
                timeout=self._timeout,
                retries=self._retries,
            )
        self._progress.advance(self._task)
        return result


async def feed_downloads(
    client: AsyncOlostep,
    batch_id: str,
    downloader: Downloader,
    batch_done: asyncio.Event,
    *,
    interval: float,
) -> None:
    """Queue completed items for download while the batch is still running.

    Re-lists completed items every ``interval`` seconds. After ``batch_done``
    is set, it does one final listing so no item is missed.
    """
    while True:
        finished = batch_done.is_set()
        try:
            added = downloader.add(await list_items(client, batch_id, "completed"))
            logger.debug("Queued %d new pages for download", added)
        except Olostep_BaseError as exc:
            if finished:
                raise  # the final listing must succeed
            logger.warning("Listing items failed (%s), will retry", exc)
        if finished:
            return
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(batch_done.wait(), timeout=interval)


async def download_one(
    client: AsyncOlostep,
    item: BatchItemResult,
    pages_dir: Path,
    *,
    timeout: float,
    retries: int,
) -> PageResult:
    """Fetch one page's Markdown with a timeout and retries, then save it."""
    path = pages_dir / f"{item.custom_id}.md"
    if path.exists():
        return _result(item, "skipped", path)

    last_error = ""
    for attempt in range(retries + 1):
        try:
            markdown = await asyncio.wait_for(_fetch_markdown(client, item), timeout)
            if problem := junk_reason(markdown):
                # Retrying returns the same stored content, so fail right away.
                return PageResult(item.custom_id, item.url, "failed", error=problem)
            atomic_write_text(path, _with_front_matter(item.url, markdown))
            return _result(item, "downloaded", path)
        except Exception as exc:  # one bad page must never crash the run
            last_error = f"{type(exc).__name__}: {exc}".strip()
            if attempt < retries:
                await asyncio.sleep(BACKOFF_SECONDS * 2**attempt)

    logger.debug("Giving up on %s: %s", item.url, last_error)
    return PageResult(item.custom_id, item.url, "failed", error=last_error)


def junk_reason(markdown: str) -> str | None:
    """Return why a page is junk (404, blocked, ...) or ``None`` if it is fine."""
    head = markdown.lstrip()[:600]
    if _BLOCK_PAGE.search(head):
        return "blocked or sign-in page (network filter)"
    title = head.splitlines()[0].strip() if head else ""
    for reason, pattern in _JUNK_TITLES.items():
        if pattern.search(title):
            return reason
    return None


async def _fetch_markdown(client: AsyncOlostep, item: BatchItemResult) -> str:
    """Return the Markdown for a batch item via ``/retrieve``."""
    if not item.retrieve_id:
        raise ValueError("item has no retrieve_id")
    result = await client.retrieve.get(item.retrieve_id, formats=["markdown"])
    markdown = getattr(result, "markdown_content", None)
    if not markdown:
        # Pages over ~6MB come back as a hosted file instead of inline content
        # (`size_exceeded`, per docs.olostep.com). That URL is plain file storage,
        # not the Olostep API, so a simple stdlib GET is enough.
        hosted_url = getattr(result, "markdown_hosted_url", None)
        if hosted_url:
            markdown = await asyncio.to_thread(_http_get_text, hosted_url)
    if not markdown or not markdown.strip():
        raise ValueError("empty markdown content")
    return markdown


def _http_get_text(url: str) -> str:
    """Blocking GET for a hosted content file (run in a worker thread)."""
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


def _with_front_matter(url: str, markdown: str) -> str:
    """Prefix the page with a tiny YAML header recording its source URL."""
    return f"---\nsource: {url}\n---\n\n{markdown.strip()}\n"


def _result(item: BatchItemResult, status: str, path: Path) -> PageResult:
    """Build a successful :class:`PageResult` for a file on disk."""
    return PageResult(
        custom_id=item.custom_id,
        url=item.url,
        status=status,
        path=str(path),
        bytes=path.stat().st_size,
    )
