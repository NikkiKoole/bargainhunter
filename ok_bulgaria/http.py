"""OK Bulgaria HTTP adapter. Shared fetch/cache lives in core.http."""
from __future__ import annotations

from pathlib import Path

from core.http import CACHE, MIN_GAP, Fetcher as _Fetcher

BASE = "https://www.cheap-bulgarian-house.co.uk"

# A touch slower than franimo's CLI default: this host is a small PHP site.
DEFAULT_GAP = max(MIN_GAP, 0.6)


class Fetcher(_Fetcher):
    def __init__(self, cache_dir: Path = CACHE, refresh: bool = False,
                 max_age_days: float | None = None, gap: float = DEFAULT_GAP):
        super().__init__(
            base=BASE, cache_dir=cache_dir, refresh=refresh,
            max_age_days=max_age_days, gap=gap,
            headers={"Accept-Language": "en,en-GB;q=0.9"},
        )
