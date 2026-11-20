"""Small helpers: filenames, timing, atomic writes and logging setup."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from rich.console import Console
from rich.logging import RichHandler

MAX_SLUG_LENGTH = 80
_NON_SLUG_CHARS = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_length: int = MAX_SLUG_LENGTH) -> str:
    """Turn arbitrary text into a lowercase, filesystem-safe slug.

    >>> slugify("Library/AsyncIO Task.html")
    'library-asyncio-task-html'
    """
    slug = _NON_SLUG_CHARS.sub("-", text.lower()).strip("-")
    return slug[:max_length].rstrip("-") or "page"


def custom_id_for_url(url: str) -> str:
    """Build a deterministic, unique and readable ``custom_id`` for a URL.

    The id combines a slug of the URL path with a short hash of the full URL,
    so two URLs never collide and the same URL always maps to the same id.
    It doubles as the Markdown filename (``<custom_id>.md``).
    """
    parts = urlsplit(url)
    readable = slugify(f"{parts.path} {parts.query}")
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"{readable}-{digest}"


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temp file + rename.

    A crash mid-write leaves no partial file behind, which keeps downloads
    idempotent: if the final file exists, it is complete.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def format_duration(seconds: float) -> str:
    """Format seconds for humans: ``2m 54s``, ``41.3s`` or ``1h 02m 03s``."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(round(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m {secs:02d}s"


@dataclass
class Timer:
    """A monotonic stopwatch. Use as a context manager or call ``stop()``."""

    started: float = field(default_factory=time.perf_counter)
    ended: float | None = None

    @property
    def elapsed(self) -> float:
        """Seconds since start (frozen once stopped)."""
        end = self.ended if self.ended is not None else time.perf_counter()
        return end - self.started

    def stop(self) -> float:
        """Stop the timer and return the elapsed seconds."""
        self.ended = time.perf_counter()
        return self.elapsed

    def __enter__(self) -> Timer:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()


def setup_logging(console: Console, verbose: bool = False) -> None:
    """Route ``logging`` through rich so logs and progress bars play nicely.

    Pass the same ``console`` the progress bars use: rich then prints log lines
    above the live bars instead of tangling them.
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, show_path=False, rich_tracebacks=True)],
        force=True,
    )
    # The SDK and httpx are chatty at INFO; keep them quiet unless verbose.
    noisy_level = logging.DEBUG if verbose else logging.WARNING
    for name in ("olostep", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(noisy_level)
    if not verbose:
        # olostep 1.2.3 warns when the API returns fields newer than the SDK
        # models (e.g. `storage` on Maps). Harmless, so hide it by default.
        logging.getLogger("olostep").setLevel(logging.ERROR)
        warnings.filterwarnings("ignore", message=r".*extra field", module="olostep")
