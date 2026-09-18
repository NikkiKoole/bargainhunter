"""Polite, caching HTTP layer.

Every page we fetch is written to cache/ as a raw .html file. Re-running the
scraper therefore costs nothing for pages we already have, which keeps the
edit-parse-rerun loop fast and keeps us off franimo's servers.
"""
from __future__ import annotations

import gzip
import hashlib
import random
import threading
import time
from pathlib import Path

import requests

BASE = "https://www.franimo.nl"
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

# One in-flight request at a time per host, with a small gap between them.
_lock = threading.Lock()
_last = [0.0]
MIN_GAP = 0.6


class Fetcher:
    def __init__(self, cache_dir: Path = CACHE, refresh: bool = False,
                 max_age_days: float | None = None, gap: float = MIN_GAP):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.refresh = refresh
        self.max_age = None if max_age_days is None else max_age_days * 86400
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept-Language": "nl,en;q=0.8"})
        self.gap = gap
        self.hits = 0
        self.misses = 0

    def _path(self, url: str) -> Path:
        """Cached pages are gzipped: raw franimo HTML is ~60KB and compresses
        to ~9KB, which matters once a search runs to five figures."""
        return self.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".html.gz")

    @staticmethod
    def _read(path: Path) -> str:
        if path.suffix == ".gz":
            return gzip.decompress(path.read_bytes()).decode("utf-8", "replace")
        return path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _write(path: Path, html: str) -> None:
        path.write_bytes(gzip.compress(html.encode("utf-8"), 6))

    def get(self, url: str) -> str:
        if url.startswith("/"):
            url = BASE + url
        path = self._path(url)
        legacy = path.with_suffix("")          # pre-gzip cache files
        hit = path if path.exists() else (legacy if legacy.exists() else None)
        if hit is not None and not self.refresh:
            fresh = self.max_age is None or (time.time() - hit.stat().st_mtime) < self.max_age
            if fresh:
                self.hits += 1
                return self._read(hit)

        html = self._download(url)
        self._write(path, html)
        if legacy.exists():
            legacy.unlink()
        self.misses += 1
        return html

    def _download(self, url: str, tries: int = 3) -> str:
        last_err: Exception | None = None
        for attempt in range(tries):
            with _lock:
                gap = self.gap - (time.time() - _last[0])
                if gap > 0:
                    time.sleep(gap)
                _last[0] = time.time()
            try:
                r = self.session.get(url, timeout=30)
                r.raise_for_status()
                r.encoding = r.encoding or "utf-8"
                return r.text
            except Exception as e:  # noqa: BLE001 - retry anything transient
                last_err = e
                time.sleep(2 ** attempt + random.random())
        raise RuntimeError(f"failed to fetch {url}: {last_err}")
