"""Franimo HTTP adapter. Shared fetch/cache lives in core.http."""
from __future__ import annotations

from pathlib import Path

from core.http import CACHE, MIN_GAP, Fetcher as _Fetcher

BASE = "https://www.franimo.nl"


class Fetcher(_Fetcher):
    def __init__(self, cache_dir: Path = CACHE, refresh: bool = False,
                 max_age_days: float | None = None, gap: float = MIN_GAP):
        super().__init__(base=BASE, cache_dir=cache_dir, refresh=refresh,
                         max_age_days=max_age_days, gap=gap)
