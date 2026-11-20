"""Step 1: discover URLs with Olostep Maps, then filter and cap them."""

from __future__ import annotations

import fnmatch
import logging
from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

from olostep import AsyncOlostep

logger = logging.getLogger(__name__)

# Files that are not web pages and would waste batch credits.
SKIPPED_EXTENSIONS = (
    ".7z", ".avi", ".bz2", ".css", ".csv", ".doc", ".docx", ".epub", ".gif",
    ".gz", ".ico", ".jpeg", ".jpg", ".js", ".json", ".mp3", ".mp4", ".pdf",
    ".png", ".ppt", ".pptx", ".svg", ".tar", ".tgz", ".webp", ".xls", ".xlsx",
    ".xml", ".zip",
)  # fmt: skip


async def map_site(client: AsyncOlostep, site: str) -> list[str]:
    """Return every URL that Olostep Maps finds for ``site``.

    ``maps.create`` returns a ``Sitemap``; its ``urls()`` iterator follows
    pagination cursors for very large sites automatically.
    """
    logger.info("Mapping %s with Olostep Maps...", site)
    sitemap = await client.maps.create(url=site)
    urls = [url async for url in sitemap.urls()]
    logger.info("Maps returned %s URLs", f"{len(urls):,}")
    return urls


def normalize_url(url: str) -> str:
    """Strip whitespace and ``#fragments`` so duplicates collapse together."""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, parts.query, ""))


def matches(url: str, pattern: str) -> bool:
    """Check a URL against a pattern.

    Patterns containing ``*``, ``?`` or ``[`` are globs matched against the
    URL path (``/3/library/*``). Anything else is a plain substring match
    on the full URL (``/3/``).
    """
    if any(char in pattern for char in "*?["):
        return fnmatch.fnmatch(urlsplit(url).path, pattern)
    return pattern in url


def is_page_url(url: str) -> bool:
    """True for http(s) URLs that do not point at a known binary/asset file."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False
    return not parts.path.lower().endswith(SKIPPED_EXTENSIONS)


def host_of(url: str) -> str:
    """Lowercase host of a URL, accepting bare domains like ``gov.uk``."""
    if "://" not in url:
        url = f"https://{url}"
    return urlsplit(url).netloc.lower()


def filter_urls(
    urls: Iterable[str],
    *,
    hosts: Iterable[str] = (),
    include: Iterable[str] = (),
    exclude: Iterable[str] = (),
    limit: int | None = None,
) -> list[str]:
    """Normalize, de-duplicate, filter and cap a list of URLs.

    Args:
        urls: Raw URLs, e.g. from Olostep Maps.
        hosts: If given, keep only URLs on exactly these hosts. Maps can
            return links to other sites (e.g. a gov.uk *service* domain).
        include: Keep a URL only if it matches at least one of these patterns.
        exclude: Drop a URL if it matches any of these patterns.
        limit: Keep at most this many URLs (after filtering).

    Returns:
        A sorted, de-duplicated list, so the same input always yields the
        same batch.
    """
    allowed = {h.lower() for h in hosts}
    include, exclude = tuple(include), tuple(exclude)
    kept: set[str] = set()
    for raw in urls:
        url = normalize_url(raw)
        if not is_page_url(url):
            continue
        if allowed and urlsplit(url).netloc not in allowed:
            continue
        if include and not any(matches(url, p) for p in include):
            continue
        if any(matches(url, p) for p in exclude):
            continue
        kept.add(url)
    result = sorted(kept)
    return result[:limit] if limit is not None else result
