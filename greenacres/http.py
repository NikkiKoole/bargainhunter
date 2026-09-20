"""Green-Acres HTTP adapter. Shared fetch/cache lives in core.http."""
from __future__ import annotations

from pathlib import Path

from core.http import CACHE, MIN_GAP, Fetcher as _Fetcher

BASE = "https://www.green-acres.fr"

# robots.txt Request-rate: 1/1 and Crawl-delay: 1. One worker in the CLI.
# Do not parallelise another scrape of this host — the lock is per process.
DEFAULT_GAP = max(MIN_GAP, 1.0)


class Fetcher(_Fetcher):
    def __init__(self, cache_dir: Path = CACHE, refresh: bool = False,
                 max_age_days: float | None = None, gap: float = DEFAULT_GAP):
        super().__init__(
            base=BASE, cache_dir=cache_dir, refresh=refresh,
            max_age_days=max_age_days, gap=gap,
            headers={
                "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.5",
                "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            },
        )
