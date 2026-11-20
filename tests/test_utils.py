"""Tests for filename, timing and file-writing helpers."""

from pathlib import Path

from olostep_batch_scraper.utils import (
    atomic_write_text,
    custom_id_for_url,
    format_duration,
    slugify,
)


def test_slugify_basic() -> None:
    assert slugify("Library/AsyncIO Task.html") == "library-asyncio-task-html"


def test_slugify_empty_falls_back() -> None:
    assert slugify("///") == "page"


def test_slugify_truncates() -> None:
    assert len(slugify("a" * 500)) <= 80


def test_custom_id_is_deterministic_and_unique() -> None:
    a = custom_id_for_url("https://docs.python.org/3/library/os.html")
    b = custom_id_for_url("https://docs.python.org/3/library/os.html")
    c = custom_id_for_url("https://docs.python.org/3/library/sys.html")
    assert a == b
    assert a != c
    assert a.startswith("3-library-os-html-")


def test_custom_id_distinguishes_query_strings() -> None:
    a = custom_id_for_url("https://example.com/search?q=a")
    b = custom_id_for_url("https://example.com/search?q=b")
    assert a != b


def test_custom_id_is_filename_safe() -> None:
    cid = custom_id_for_url("https://example.com/a b/ü?x=1&y=2")
    assert all(ch.isalnum() or ch == "-" for ch in cid)


def test_format_duration() -> None:
    assert format_duration(41.26) == "41.3s"
    assert format_duration(174) == "2m 54s"
    assert format_duration(3723) == "1h 02m 03s"


def test_atomic_write_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "pages" / "x.md"
    atomic_write_text(target, "hello")
    assert target.read_text() == "hello"
    assert [p.name for p in target.parent.iterdir()] == ["x.md"]
