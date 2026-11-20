"""Step 4: verify the run and write manifest, failures and summary files."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .download import PageResult
from .utils import atomic_write_text, format_duration


@dataclass(frozen=True)
class Timings:
    """Wall-clock seconds for each phase of the run."""

    discovery: float
    scrape: float
    download: float  # download time left after scraping finished
    total: float


@dataclass(frozen=True)
class Summary:
    """Final, verified numbers for the run."""

    batch_id: str
    site: str | None
    total: int
    succeeded: int
    failed: int
    timings: Timings
    milestones: dict[str, float] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        """Share of submitted URLs that ended up as Markdown files on disk."""
        return self.succeeded / self.total if self.total else 0.0

    def to_dict(self) -> dict[str, object]:
        """JSON-friendly representation for ``summary.json``."""
        t = self.timings
        return {
            "batch_id": self.batch_id,
            "site": self.site,
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "success_rate": round(self.success_rate, 4),
            "discovery_seconds": round(t.discovery, 2),
            "scrape_seconds": round(t.scrape, 2),
            "download_seconds": round(t.download, 2),
            "total_seconds": round(t.total, 2),
            "scrape_milestones_seconds": self.milestones,
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }


def reconcile(
    submitted: dict[str, str],
    downloads: list[PageResult],
    scrape_failures: dict[str, str],
) -> list[PageResult]:
    """Produce exactly one result per submitted URL.

    Every ``custom_id`` we submitted ends up as downloaded/skipped, or as a
    failure: failed in the batch, failed to download, or never returned.
    A "success" is only counted when its file really exists on disk.
    """
    by_id = {r.custom_id: r for r in downloads}
    results: list[PageResult] = []
    for custom_id, url in submitted.items():
        result = by_id.get(custom_id)
        if result is None:
            reason = scrape_failures.get(custom_id, "not returned by batch")
            result = PageResult(custom_id, url, "failed", error=f"scrape: {reason}")
        elif result.ok and not _file_ok(result.path):
            result = PageResult(custom_id, url, "failed", error="file missing")
        results.append(result)
    return results


def write_reports(out_dir: Path, results: list[PageResult], summary: Summary) -> None:
    """Write ``manifest.csv``, ``failures.csv`` and ``summary.json``."""
    _write_csv(
        out_dir / "manifest.csv",
        ["custom_id", "url", "status", "file", "bytes"],
        [[r.custom_id, r.url, r.status, r.path, r.bytes] for r in results],
    )
    _write_csv(
        out_dir / "failures.csv",
        ["custom_id", "url", "error"],
        [[r.custom_id, r.url, r.error] for r in results if not r.ok],
    )
    text = json.dumps(summary.to_dict(), indent=2) + "\n"
    atomic_write_text(out_dir / "summary.json", text)


def print_summary(console: Console, summary: Summary, out_dir: Path) -> None:
    """Render the final summary table."""
    t = summary.timings
    table = Table(title="Olostep batch scrape", show_header=False, min_width=40)
    table.add_column(style="bold")
    table.add_column(justify="right")
    table.add_row("Pages submitted", f"{summary.total:,}")
    table.add_row("Succeeded", f"[green]{summary.succeeded:,}")
    table.add_row("Failed", f"[red]{summary.failed:,}" if summary.failed else "0")
    table.add_row("Success rate", f"{summary.success_rate:.1%}")
    table.add_section()
    table.add_row("Discovery time", format_duration(t.discovery))
    table.add_row("Scrape time", format_duration(t.scrape))
    for percent, seconds in summary.milestones.items():
        table.add_row(f"  {percent}% scraped at", format_duration(seconds))
    table.add_row("Download (after scrape)", format_duration(t.download))
    table.add_row("Total time", f"[bold]{format_duration(t.total)}")
    console.print(table)
    console.print(f"Markdown files: [cyan]{out_dir / 'pages'}")
    console.print(f"Reports:        [cyan]{out_dir}/summary.json, manifest.csv")


def _file_ok(path: str) -> bool:
    """True if ``path`` is a non-empty file."""
    p = Path(path)
    return p.is_file() and p.stat().st_size > 0


def _write_csv(path: Path, header: list[str], rows: list[list[object]]) -> None:
    """Write a CSV file with a header row."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
