"""Allow ``python -m olostep_batch_scraper``."""

import sys

from .cli import main

sys.exit(main())
