"""Gzip any cache files left over from before the cache was compressed.

    python3 -m franimo.compact
"""
from __future__ import annotations

import gzip

from .http import CACHE


def main() -> int:
    before = after = 0
    files = sorted(CACHE.glob("*.html"))
    for f in files:
        raw = f.read_bytes()
        gz = f.with_suffix(".html.gz")
        gz.write_bytes(gzip.compress(raw, 6))
        before += len(raw)
        after += gz.stat().st_size
        f.unlink()
    print(f"compacted {len(files)} files: {before / 1e6:.0f}MB -> {after / 1e6:.0f}MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
