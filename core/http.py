"""Polite, caching HTTP layer.

Every page we fetch is written to cache/ as a gzipped .html file. Re-running
a scraper therefore costs nothing for pages we already have. The cache key is
sha1(full URL), so existing franimo files stay valid — never delete cache/.

Rate limiting (MIN_GAP) is process-wide: two processes double the request
rate at a host. Chain runs; don't parallelise scrapers against the same site.
"""
from __future__ import annotations

import gzip
import hashlib
import os
import random
import threading
import time
import zlib
from pathlib import Path

import requests

from .paths import CACHE

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

# One in-flight request at a time per process, with a small gap between them.
_lock = threading.Lock()
_last = [0.0]
MIN_GAP = 0.6


def cache_path(cache_dir: Path, url: str) -> Path:
    """Cached pages are gzipped: raw portal HTML is ~60KB and compresses
    to ~9KB, which matters once a search runs to five figures."""
    return cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".html.gz")


def decode(response) -> str:
    """Decode a response, trusting the bytes over the declared charset.

    Small portals often serve UTF-8 while their headers claim a legacy
    codepage; cheap-bulgarian-house.co.uk declares windows-1251 and en-dashes
    came back as "вЂ“". The byte sequence is genuinely ambiguous — E2 80 93 is
    both a UTF-8 en-dash and the cp1251 bytes for that mojibake — so the test
    is whether the document as a whole reads as UTF-8, not whether it decodes
    without a single error.
    """
    raw = response.content
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass

    # A few stray bytes shouldn't condemn the whole document to a legacy
    # codepage: cheap-bulgarian-house.co.uk is UTF-8 apart from ~14 bytes
    # inside a JavaScript string, and falling back on those mangled every real
    # UTF-8 character in the page. Keep UTF-8 when the damage is negligible.
    lossy = raw.decode("utf-8", errors="replace")
    if lossy.count("\ufffd") <= max(2, len(lossy) // 100):
        return lossy

    enc = response.encoding or response.apparent_encoding or "utf-8"
    return raw.decode(enc, errors="replace")


class Fetcher:
    def __init__(self, cache_dir: Path = CACHE, refresh: bool = False,
                 max_age_days: float | None = None, gap: float = MIN_GAP,
                 base: str = "", headers: dict | None = None):
        self.base = base.rstrip("/")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.refresh = refresh
        self.max_age = None if max_age_days is None else max_age_days * 86400
        self.session = requests.Session()
        hdrs = {"User-Agent": UA, "Accept-Language": "nl,en;q=0.8"}
        if headers:
            hdrs.update(headers)
        self.session.headers.update(hdrs)
        self.gap = gap
        self.hits = 0
        self.misses = 0

    def resolve(self, url: str) -> str:
        if url.startswith("/") and self.base:
            return self.base + url
        return url

    def _path(self, url: str) -> Path:
        return cache_path(self.cache_dir, url)

    @staticmethod
    def _read(path: Path) -> str:
        if path.suffix == ".gz":
            return gzip.decompress(path.read_bytes()).decode("utf-8", "replace")
        return path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _write(path: Path, html: str) -> None:
        """Write atomically: a scraper killed mid-write used to leave a
        truncated .gz behind, and that listing then failed on every later run."""
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_bytes(gzip.compress(html.encode("utf-8"), 6))
        os.replace(tmp, path)

    def get(self, url: str) -> str:
        url = self.resolve(url)
        path = self._path(url)
        legacy = path.with_suffix("")          # pre-gzip cache files
        hit = path if path.exists() else (legacy if legacy.exists() else None)
        if hit is not None and not self.refresh:
            fresh = self.max_age is None or (time.time() - hit.stat().st_mtime) < self.max_age
            if fresh:
                try:
                    html = self._read(hit)
                except (OSError, gzip.BadGzipFile, EOFError, zlib.error):
                    # Unreadable cache entry: drop it and fetch again rather
                    # than failing this listing forever.
                    hit.unlink(missing_ok=True)
                else:
                    self.hits += 1
                    return html

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
                return decode(r)
            except Exception as e:  # noqa: BLE001 - retry anything transient
                last_err = e
                time.sleep(2 ** attempt + random.random())
        raise RuntimeError(f"failed to fetch {url}: {last_err}")
