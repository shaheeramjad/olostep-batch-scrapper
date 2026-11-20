"""Step 2: submit one Olostep Batch, track it live, and support resume."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from olostep import AsyncOlostep, Olostep_BaseError
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

from .utils import atomic_write_text, custom_id_for_url, format_duration

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = ("completed", "failed")
MILESTONES = (50, 90, 99)  # percent done; elapsed time is recorded for each


@dataclass
class BatchState:
    """What we persist to ``batch.json`` so an interrupted run can resume."""

    batch_id: str
    site: str | None
    total_urls: int
    created_at: float  # wall-clock epoch seconds, set when we submitted
    discovery_seconds: float = 0.0
    scrape_seconds: float | None = None  # filled in once the batch completes
    # seconds from submission until N% of pages were done, e.g. {"90": 61.2}
    milestones: dict[str, float] = field(default_factory=dict)

    def save(self, path: Path) -> None:
        """Write the state as pretty JSON (atomically)."""
        atomic_write_text(path, json.dumps(asdict(self), indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> BatchState:
        """Read state written by :meth:`save`."""
        return cls(**json.loads(path.read_text(encoding="utf-8")))


def build_items(urls: list[str]) -> dict[str, str]:
    """Map each URL to its deterministic ``custom_id`` (``custom_id -> url``)."""
    return {custom_id_for_url(url): url for url in urls}


async def create_batch(client: AsyncOlostep, items: dict[str, str]) -> str:
    """Submit all URLs as a single batch and return the batch id."""
    payload = [{"url": url, "custom_id": cid} for cid, url in items.items()]
    logger.info("Submitting %s URLs as one batch...", f"{len(payload):,}")
    batch = await client.batches.create(payload)
    logger.info("Batch created: %s", batch.id)
    return batch.id


def build_progress(console: Console) -> Progress:
    """One live display with a bar per phase (scraping and downloading)."""
    return Progress(
        TextColumn("[bold]{task.description:<12}"),
        BarColumn(),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TextColumn("elapsed"),
        TimeElapsedColumn(),
        console=console,
    )


async def wait_for_batch(
    client: AsyncOlostep,
    state: BatchState,
    *,
    poll_interval: float,
    progress: Progress,
    task: TaskID,
) -> float:
    """Poll the batch until it finishes, updating the ``Scraping`` bar.

    Prints a single headline line (e.g. ``7,000 pages scraped in 2m 54s``)
    the first time the completed count reaches the total.

    Returns:
        Scrape time in seconds, from batch submission to completion.
    """
    # Show elapsed time since submission, not since this process started.
    bar = next(t for t in progress.tasks if t.id == task)
    bar.start_time = progress.get_time() - _since(state)
    announced = False
    while True:
        status, completed = await _poll(client, state.batch_id)
        progress.update(task, completed=completed)
        _record_milestones(state, completed)

        done = status in TERMINAL_STATUSES
        if not announced and (completed >= state.total_urls or done):
            announced = True
            elapsed = format_duration(_since(state))
            progress.console.print(
                f"[bold green]{completed:,} pages scraped in {elapsed}"
            )
        if done:
            if status == "failed":
                logger.warning("Batch %s ended as 'failed'", state.batch_id)
            progress.stop_task(task)
            return _since(state)
        await asyncio.sleep(poll_interval)


async def _poll(client: AsyncOlostep, batch_id: str) -> tuple[str, int]:
    """Return ``(status, completed_urls)``; transient errors are retried."""
    while True:
        try:
            info = await client.batches.info(batch_id)
            return str(info.status), info.completed_urls
        except Olostep_BaseError as exc:
            logger.warning("Status check failed (%s), retrying...", exc)
            await asyncio.sleep(2)


def _record_milestones(state: BatchState, completed: int) -> None:
    """Remember when the batch first passed 50%, 90% and 99% done."""
    for percent in MILESTONES:
        key = str(percent)
        if (
            key not in state.milestones
            and completed * 100 >= percent * state.total_urls
        ):
            state.milestones[key] = round(_since(state), 2)


def _since(state: BatchState) -> float:
    """Seconds elapsed since the batch was submitted."""
    return max(0.0, time.time() - state.created_at)
