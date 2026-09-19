"""home.ge HTTP adapter. Shared fetch/cache lives in core.http."""
from __future__ import annotations

from pathlib import Path

from core.http import CACHE, MIN_GAP, Fetcher as _Fetcher

BASE = "https://www.home.ge"

# Same floor as core.http.MIN_GAP; one worker in the CLI. Do not parallelise
# another scrape of this host — the lock is per process.
DEFAULT_GAP = max(MIN_GAP, 0.6)

# First anonymous GET returns an empty 200 + Set-Cookie + Refresh: 0.
# The session cookie then unlocks the real HTML. Retry once in-process
# (requests.Session keeps the cookie) so we never cache the bounce.
_BOUNCE_MAX = 200


class Fetcher(_Fetcher):
    def __init__(self, cache_dir: Path = CACHE, refresh: bool = False,
                 max_age_days: float | None = None, gap: float = DEFAULT_GAP):
        super().__init__(
            base=BASE, cache_dir=cache_dir, refresh=refresh,
            max_age_days=max_age_days, gap=gap,
            headers={"Accept-Language": "en,en-US;q=0.9"},
        )

    def _download(self, url: str, tries: int = 3) -> str:
        html = super()._download(url, tries=tries)
        if len(html.strip()) < _BOUNCE_MAX:
            html = super()._download(url, tries=tries)
        return html
