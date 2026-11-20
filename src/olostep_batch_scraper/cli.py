"""Command-line entry point: discover -> batch -> download -> verify."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

from olostep import AsyncOlostep, Olostep_BaseError, RetryStrategy
from rich.console import Console
from rich.progress import Progress

from . import __version__
from .batch import (
    BatchState,
    build_items,
    build_progress,
    create_batch,
    wait_for_batch,
)
from .config import (
    DEFAULT_CONCURRENCY,
    DEFAULT_LIMIT,
    DEFAULT_OUT_DIR,
    ConfigError,
    Settings,
    load_api_key,
)
from .discover import filter_urls, host_of, map_site
from .download import Downloader, PageResult, feed_downloads, list_items
from .report import Summary, Timings, print_summary, reconcile, write_reports
from .utils import Timer, atomic_write_text, format_duration, setup_logging

logger = logging.getLogger(__name__)
console = Console()

FEED_INTERVAL = 10.0  # seconds between "which pages are ready?" checks


def build_parser() -> argparse.ArgumentParser:
    """Define the CLI flags."""
    parser = argparse.ArgumentParser(
        prog="olostep-batch-scraper",
        description="Scrape thousands of pages to Markdown with Olostep.",
    )
    parser.add_argument(
        "--site", action="append", default=[], metavar="URL",
        help="website to map, e.g. https://docs.djangoproject.com (repeatable)",
    )  # fmt: skip
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT, help="max URLs (default 7000)"
    )
    parser.add_argument(
        "--include", action="append", default=[], metavar="PATTERN",
        help="keep URLs matching this substring or path glob (repeatable)",
    )  # fmt: skip
    parser.add_argument(
        "--exclude", action="append", default=[], metavar="PATTERN",
        help="drop URLs matching this substring or path glob (repeatable)",
    )  # fmt: skip
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT_DIR, help="output folder"
    )
    parser.add_argument(
        "--concurrency", type=int, default=DEFAULT_CONCURRENCY,
        help="parallel downloads (default 50)",
    )  # fmt: skip
    parser.add_argument(
        "--resume", action="store_true", help="resume the batch in <out>/batch.json"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="discover URLs only, no batch"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logs")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def parse_settings(argv: list[str] | None = None) -> tuple[Settings, bool]:
    """Parse CLI args into :class:`Settings`. Returns ``(settings, verbose)``."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.site and not args.resume:
        parser.error("--site is required (or use --resume)")
    if args.limit < 1 or args.concurrency < 1:
        parser.error("--limit and --concurrency must be positive")
    try:
        api_key = load_api_key()
    except ConfigError as exc:
        parser.exit(2, f"error: {exc}\n")
    settings = Settings(
        api_key=api_key,
        sites=tuple(args.site),
        limit=args.limit,
        include=tuple(args.include),
        exclude=tuple(args.exclude),
        out_dir=args.out,
        concurrency=args.concurrency,
        resume=args.resume,
        dry_run=args.dry_run,
    )
    return settings, args.verbose


async def discover(client: AsyncOlostep, settings: Settings) -> list[str]:
    """Map every site (concurrently) and return the filtered, capped URL list."""
    per_site = await asyncio.gather(*(map_site(client, s) for s in settings.sites))
    raw = [url for urls in per_site for url in urls]
    urls = filter_urls(
        raw,
        hosts=[host_of(site) for site in settings.sites],
        include=settings.include,
        exclude=settings.exclude,
        limit=settings.limit,
    )
    logger.info("%s URLs after filtering (limit %s)", f"{len(urls):,}", settings.limit)
    return urls


async def start_batch(
    client: AsyncOlostep, settings: Settings
) -> tuple[BatchState, dict[str, str]] | None:
    """Discover URLs and submit the batch. Returns ``None`` for a dry run."""
    with Timer() as discovery:
        urls = await discover(client, settings)

    if settings.dry_run:
        _save_dry_run(settings, urls, discovery.elapsed)
        return None
    if not urls:
        raise SystemExit("No URLs left after filtering; nothing to scrape.")

    items = build_items(urls)
    settings.out_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(settings.urls_file, json.dumps(items, indent=2) + "\n")

    created_at = time.time()
    batch_id = await create_batch(client, items)
    state = BatchState(
        batch_id=batch_id,
        site=", ".join(settings.sites),
        total_urls=len(items),
        created_at=created_at,
        discovery_seconds=discovery.elapsed,
    )
    state.save(settings.state_file)  # saved immediately so --resume works
    return state, items


def load_previous_batch(settings: Settings) -> tuple[BatchState, dict[str, str]]:
    """Load ``batch.json`` and ``urls.json`` from a previous run."""
    if not settings.state_file.exists():
        raise SystemExit(f"Nothing to resume: {settings.state_file} not found.")
    state = BatchState.load(settings.state_file)
    items = json.loads(settings.urls_file.read_text(encoding="utf-8"))
    logger.info("Resuming batch %s (%s URLs)", state.batch_id, f"{len(items):,}")
    return state, items


async def run(settings: Settings) -> Summary | None:
    """Run the whole pipeline and return the verified summary."""
    total = Timer()
    retry = RetryStrategy(max_retries=2, initial_delay=1.0)

    async with AsyncOlostep(api_key=settings.api_key, retry_strategy=retry) as client:
        if settings.resume:
            state, items = load_previous_batch(settings)
        else:
            started = await start_batch(client, settings)
            if started is None:
                return None
            state, items = started

        with build_progress(console) as progress:
            downloads, scrape_failures, tail = await scrape_and_download(
                client, settings, state, progress
            )

    verified = reconcile(items, downloads, scrape_failures)
    succeeded = sum(r.ok for r in verified)
    assert state.scrape_seconds is not None
    # A fresh run is timed end to end. A resumed run spans several processes,
    # so its total is the sum of the recorded phases.
    total_seconds = (
        state.discovery_seconds + state.scrape_seconds + tail
        if settings.resume
        else total.stop()
    )
    summary = Summary(
        batch_id=state.batch_id,
        site=state.site,
        total=len(items),
        succeeded=succeeded,
        failed=len(items) - succeeded,
        milestones=state.milestones,
        timings=Timings(
            discovery=state.discovery_seconds,
            scrape=state.scrape_seconds,
            download=tail,
            total=total_seconds,
        ),
    )
    write_reports(settings.out_dir, verified, summary)
    print_summary(console, summary, settings.out_dir)
    return summary


async def scrape_and_download(
    client: AsyncOlostep,
    settings: Settings,
    state: BatchState,
    progress: Progress,
) -> tuple[list[PageResult], dict[str, str], float]:
    """Track the batch and download finished pages at the same time.

    Pages are downloaded as soon as Olostep marks them completed, so most of
    the download work overlaps with scraping.

    Returns:
        ``(download_results, {custom_id: reason}, download_tail_seconds)``,
        where the tail is the download time left after scraping finished.
    """
    total = state.total_urls
    scrape_bar = progress.add_task("Scraping", total=total)
    download_bar = progress.add_task("Downloading", total=total)
    downloader = Downloader(
        client,
        settings.pages_dir,
        concurrency=settings.concurrency,
        timeout=settings.request_timeout,
        retries=settings.download_retries,
        progress=progress,
        task=download_bar,
    )
    batch_done = asyncio.Event()
    feeder = asyncio.create_task(
        feed_downloads(
            client, state.batch_id, downloader, batch_done, interval=FEED_INTERVAL
        )
    )
    try:
        if state.scrape_seconds is None:
            state.scrape_seconds = await wait_for_batch(
                client,
                state,
                poll_interval=settings.poll_interval,
                progress=progress,
                task=scrape_bar,
            )
            state.save(settings.state_file)
        else:
            progress.update(scrape_bar, completed=total)
            progress.stop_task(scrape_bar)
            logger.info("Batch already finished earlier; downloading missing pages")

        batch_done.set()
        with Timer() as tail:
            await feeder  # final listing: every completed page is now queued
            progress.update(download_bar, total=downloader.queued)
            downloads = await downloader.drain()
            failed = await list_items(client, state.batch_id, "failed")
    finally:
        feeder.cancel()

    if failed:
        logger.info("%s pages failed in the batch", f"{len(failed):,}")
    scrape_failures = {item.custom_id: "failed in batch" for item in failed}
    return downloads, scrape_failures, tail.elapsed


def _save_dry_run(settings: Settings, urls: list[str], seconds: float) -> None:
    """Write the discovered URLs to ``urls.txt`` and print a short preview."""
    path = settings.out_dir / "urls.txt"
    atomic_write_text(path, "\n".join(urls) + "\n")
    console.print(
        f"[bold green]Dry run:[/] found [bold]{len(urls):,}[/] URLs "
        f"in {format_duration(seconds)} -> {path}"
    )
    for url in urls[:5]:
        console.print(f"  {url}")
    if len(urls) > 5:
        console.print(f"  ... and {len(urls) - 5:,} more")


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point. Returns a process exit code."""
    settings, verbose = parse_settings(argv)
    setup_logging(console, verbose)
    try:
        summary = asyncio.run(run(settings))
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/] Continue later with --resume.")
        return 130
    except Olostep_BaseError as exc:
        logger.error("Olostep API error: %s", exc)
        return 1
    # Individual page failures are expected at scale and listed in
    # failures.csv; only a run with zero successful pages is an error.
    if summary is not None and summary.succeeded == 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
