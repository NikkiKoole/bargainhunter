"""Splitting a search into price bands when a portal caps its pager.

Three portals cap pagination rather than serving the tail they advertise:
franimo stops around page 714, Le Figaro at 100, Green-Acres at 20. Past the
cap the listings are simply unreachable, and the crawl ends quietly holding
part of the catalogue.

The way round it is to ask for narrower price ranges until each one fits under
the cap, then crawl each in turn and deduplicate. Adapters differ in how a
price range is expressed in a URL, so they pass in their own rewriting.
"""
from __future__ import annotations

from typing import Callable

# Halving stops here: below this a band is too narrow to be worth another
# probe, and a portal that still overflows is telling us something else is
# wrong (a filter being ignored, say).
MIN_BAND_WIDTH = 500
MAX_DEPTH = 8


def split_bands(pages_for: Callable[[int, int], int | None],
                lo: int, hi: int, ceiling: int,
                depth: int = 0, log: Callable[[str], None] | None = None,
                ) -> list[list[int]]:
    """Halve [lo, hi] until every band fits under `ceiling` pages.

    `pages_for(lo, hi)` reports how many pages that range has — one request per
    call, so the result is a handful of probes rather than a crawl. A range
    that cannot be measured is returned as-is: better to crawl it and hit the
    cap than to drop it silently.
    """
    pages = pages_for(lo, hi)
    if pages is None or pages <= ceiling or depth >= MAX_DEPTH or hi - lo <= MIN_BAND_WIDTH:
        if log and pages and pages > ceiling:
            log(f"    €{lo:,}–€{hi:,} is still {pages} pages; crawling it anyway")
        return [[lo, hi]]
    mid = (lo + hi) // 2
    if log:
        log(f"    €{lo:,}–€{hi:,} is {pages} pages, splitting at €{mid:,}")
    return (split_bands(pages_for, lo, mid, ceiling, depth + 1, log)
            + split_bands(pages_for, mid + 1, hi, ceiling, depth + 1, log))


def describe(bands: list[list[int]]) -> str:
    return ", ".join(f"€{lo:,}–€{hi:,}" for lo, hi in bands)
