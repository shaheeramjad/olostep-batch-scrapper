"""Runtime settings and environment loading."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

API_KEY_ENV = "OLOSTEP_API_KEY"

DEFAULT_LIMIT = 7_000
DEFAULT_CONCURRENCY = 50
DEFAULT_OUT_DIR = Path("output")


class ConfigError(Exception):
    """Raised when required configuration (like the API key) is missing."""


@dataclass(frozen=True)
class Settings:
    """Everything a run needs, resolved from CLI flags and the environment."""

    api_key: str = field(repr=False)  # never show the key in logs or reprs
    sites: tuple[str, ...] = ()
    limit: int = DEFAULT_LIMIT
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    out_dir: Path = DEFAULT_OUT_DIR
    concurrency: int = DEFAULT_CONCURRENCY
    resume: bool = False
    dry_run: bool = False
    poll_interval: float = 2.0  # seconds between batch status checks
    request_timeout: float = 60.0  # seconds per page download attempt
    download_retries: int = 2  # retries after the first download attempt

    @property
    def pages_dir(self) -> Path:
        """Folder holding one Markdown file per page."""
        return self.out_dir / "pages"

    @property
    def state_file(self) -> Path:
        """Batch state used by ``--resume``."""
        return self.out_dir / "batch.json"

    @property
    def urls_file(self) -> Path:
        """The submitted URL list (``custom_id`` -> URL)."""
        return self.out_dir / "urls.json"


def load_api_key() -> str:
    """Read ``OLOSTEP_API_KEY`` from the environment or a local ``.env`` file.

    Raises:
        ConfigError: If the key is not set.
    """
    load_dotenv()
    key = os.getenv(API_KEY_ENV, "").strip()
    if not key or key == "your_key_here":
        raise ConfigError(
            f"{API_KEY_ENV} is not set. Copy .env.example to .env and add "
            "your key from https://www.olostep.com/dashboard"
        )
    return key
