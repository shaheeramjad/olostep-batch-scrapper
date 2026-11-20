"""Tests for streaming downloads, using a fake Olostep client (no network)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from rich.console import Console
from rich.progress import Progress

from olostep_batch_scraper.download import (
    Downloader,
    download_one,
    feed_downloads,
    junk_reason,
)


class FakeBatches:
    """Pretends pages finish over time: each listing reveals one more item."""

    def __init__(self, items: list[SimpleNamespace]) -> None:
        self._items = items
        self.visible = 0

    def items(self, batch_id: str, **_: Any):  # type: ignore[no-untyped-def]
        self.visible = min(self.visible + 1, len(self._items))
        ready = self._items[: self.visible]

        async def gen():  # type: ignore[no-untyped-def]
            for item in ready:
                yield item

        return gen()


class FakeRetrieve:
    """Returns Markdown, except for ids listed in ``broken``."""

    def __init__(self, broken: set[str]) -> None:
        self.broken = broken
        self.calls = 0

    async def get(self, retrieve_id: str, formats: list[str]) -> SimpleNamespace:
        self.calls += 1
        if retrieve_id in self.broken:
            return SimpleNamespace(markdown_content="")
        return SimpleNamespace(markdown_content=f"# Page {retrieve_id}")


def make_item(n: int) -> SimpleNamespace:
    return SimpleNamespace(
        custom_id=f"page-{n}", url=f"https://example.com/{n}", retrieve_id=f"r{n}"
    )


def run_feed(tmp_path: Path, broken: set[str]) -> tuple[list, FakeRetrieve]:
    items = [make_item(n) for n in range(3)]
    retrieve = FakeRetrieve(broken)
    client = SimpleNamespace(batches=FakeBatches(items), retrieve=retrieve)

    async def scenario():  # type: ignore[no-untyped-def]
        progress = Progress(console=Console(quiet=True))
        task = progress.add_task("dl", total=3)
        downloader = Downloader(
            client,  # type: ignore[arg-type]
            tmp_path,
            concurrency=2,
            timeout=5,
            retries=0,
            progress=progress,
            task=task,
        )
        done = asyncio.Event()
        feeder = asyncio.create_task(
            feed_downloads(client, "b1", downloader, done, interval=0.01)  # type: ignore[arg-type]
        )
        await asyncio.sleep(0.2)  # let several listings happen "mid-batch"
        done.set()
        await feeder
        return await downloader.drain()

    return asyncio.run(scenario()), retrieve


def test_streams_each_page_exactly_once(tmp_path: Path) -> None:
    results, retrieve = run_feed(tmp_path, broken=set())
    assert sorted(r.custom_id for r in results) == ["page-0", "page-1", "page-2"]
    assert all(r.status == "downloaded" for r in results)
    assert retrieve.calls == 3  # re-listing never re-downloads a page
    text = (tmp_path / "page-1.md").read_text()
    assert text.startswith("---\nsource: https://example.com/1\n---")


def test_bad_page_is_recorded_not_raised(tmp_path: Path) -> None:
    results, _ = run_feed(tmp_path, broken={"r2"})
    by_id = {r.custom_id: r for r in results}
    assert by_id["page-2"].status == "failed"
    assert "empty markdown" in by_id["page-2"].error
    assert not (tmp_path / "page-2.md").exists()


def test_existing_files_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "page-0.md").write_text("already here")
    results, retrieve = run_feed(tmp_path, broken=set())
    assert {r.custom_id: r.status for r in results}["page-0"] == "skipped"
    assert retrieve.calls == 2


def test_junk_reason_flags_404_and_block_pages() -> None:
    assert junk_reason("Page not found - GOV.UK\n\nSorry...") == "not found (404 page)"
    assert junk_reason("Page blocked by your school\n\nBLOCKED") is not None
    assert junk_reason("Access Denied\n\nYou don't have permission") is not None
    portal = "Example Org - Sign In\n\nPlease review the Acceptable Usage Policy."
    assert junk_reason(portal) is not None
    assert junk_reason("No longer available - GOV.UK\n\n...") == "not found (404 page)"


def test_junk_reason_keeps_real_pages_about_errors() -> None:
    page = "Built-in Views | Django\n\n## The 404 (page not found) view\n..."
    assert junk_reason(page) is None
    assert junk_reason("# Page 3\n\nhello") is None


def test_junk_page_is_failed_and_not_written(tmp_path: Path) -> None:
    item = make_item(9)
    client = SimpleNamespace(
        retrieve=SimpleNamespace(get=_fake_get("Page not found - GOV.UK\n\nGone"))
    )
    result = asyncio.run(
        download_one(client, item, tmp_path, timeout=5, retries=2)  # type: ignore[arg-type]
    )
    assert result.status == "failed"
    assert result.error == "not found (404 page)"
    assert not list(tmp_path.iterdir())


def _fake_get(markdown: str):  # type: ignore[no-untyped-def]
    async def get(retrieve_id: str, formats: list[str]) -> SimpleNamespace:
        return SimpleNamespace(markdown_content=markdown)

    return get
