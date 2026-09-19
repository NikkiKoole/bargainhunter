"""Domaza HTTP adapter. Shared fetch/cache lives in core.http."""
from __future__ import annotations

from pathlib import Path

from core.http import CACHE, MIN_GAP, Fetcher as _Fetcher

BASE = "https://www.domaza.com"

# Same floor as core.http.MIN_GAP; one worker in the CLI. Do not parallelise
# another scrape of this host — the lock is per process. robots.txt asks for
# Crawl-delay: 10; we stay at the shared floor and never overlap processes.
DEFAULT_GAP = max(MIN_GAP, 0.6)

# Session-only currency switch. The public list URL does not carry EUR/USD;
# a GET here (same cookie jar) makes subsequent pages print euro figures.
CURRENCY_EUR_PATH = "/ajaxfeeds/currency/currency/EUR"


class Fetcher(_Fetcher):
    def __init__(self, cache_dir: Path = CACHE, refresh: bool = False,
                 max_age_days: float | None = None, gap: float = DEFAULT_GAP):
        super().__init__(
            base=BASE, cache_dir=cache_dir, refresh=refresh,
            max_age_days=max_age_days, gap=gap,
            headers={"Accept-Language": "en,en-US;q=0.9"},
        )
        self._eur_ready = False

    def prefer_eur(self) -> None:
        """Ask the portal to render prices in EUR for this session.

        Cached HTML is whatever currency was active at download time. The
        parser accepts € or $ on the page; this just tilts live fetches
        toward euro so we store the portal's own figure.
        """
        if self._eur_ready:
            return
        try:
            self.session.get(
                self.base + CURRENCY_EUR_PATH,
                timeout=30,
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
        except Exception:
            pass
        self._eur_ready = True

    def _download(self, url: str, tries: int = 3) -> str:
        self.prefer_eur()
        return super()._download(url, tries)
