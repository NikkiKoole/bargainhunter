"""One-page freshness check against the published counts. No database writes.

    python3 -m core.probe
    python3 -m core.probe france-150k bg-houses-50k
    python3 -m core.probe --refresh          # already the default
    python3 -m core.probe --threshold 5 --min-delta 50
    python3 -m core.probe --quiet

For each enabled search in searches.json (or the names you pass; parked
seeds are included only when named), this fetches page 1 with that
adapter's Fetcher and parse_list, then compares the portal count to
``data/meta.json`` ``searches[].n``. Detail pages are not fetched.

A seed drifts when ``|live - ours|`` is at least ``--min-delta`` (50)
or at least ``--threshold`` percent of the published count (5). Exit
status is 1 when any probed seed drifts, otherwise 0. A blocked or
unreachable portal is reported and is not scored — Holprop and Le Figaro
Immobilier are often Cloudflare from a datacenter.

Price-banded searches are probed on the seed URL, not band 1. Page 1 of
that URL carries the portal's headline for the whole filter. Green-Acres
is fetched through AdvertsListing (the same endpoint the scraper
paginates) so ``advertsCount`` is that seed total; the 20-page pager cap
hides cards, not the count. Band 1 would only be ~480 listings and would
always look like a collapse next to the published ~4.6k.

Where the page states a result count, ``parse_list`` puts it on ``total``
and the row is ``exact``. Otherwise live is
``(total_pages - 1) * page_size + cards on page 1`` and the row is
``estimate`` (the last page is assumed as full as a typical page).

``--refresh`` is on by default: this check must not trust a stale cache
entry. ``--no-refresh`` reads ``cache/`` when the page is already there.
The fetch still updates the HTML cache; it does not write SQLite.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

from core.listing import DEFAULT_SOURCE
from core.paths import ROOT
from core.searches import load_searches

META = ROOT / "data" / "meta.json"

DEFAULT_THRESHOLD_PCT = 5.0
DEFAULT_MIN_DELTA = 50

# Side-effect imports, the same way each scrape CLI registers itself.
_ADAPTERS = (
    "franimo.adapter",
    "ok_bulgaria.adapter",
    "akiyaportal.adapter",
    "holprop.adapter",
    "abruzzopropertyitaly.adapter",
    "abruzzoruralproperty.adapter",
    "centrarium.adapter",
    "mubawab.adapter",
    "homege.adapter",
    "bulgarianproperties.adapter",
    "domaza.adapter",
    "lefigaro.adapter",
    "greenacres.adapter",
)

_BLOCK_MARKERS = (
    "cf-error-details",
    "sorry, you have been blocked",
    "just a moment",
    "cdn-cgi/challenge",
    "attention required",
    "cf-browser-verification",
    "enable javascript and cookies",
)


def register_adapters() -> None:
    for name in _ADAPTERS:
        importlib.import_module(name)


def published_counts(path: Path | str | None = None) -> dict[str, int]:
    """``searches[].search`` → ``n`` from the last export's meta.json."""
    meta = Path(path or META)
    if not meta.is_file():
        return {}
    data = json.loads(meta.read_text(encoding="utf-8"))
    out: dict[str, int] = {}
    for row in data.get("searches") or []:
        if not isinstance(row, dict) or "search" not in row or "n" not in row:
            continue
        try:
            out[str(row["search"])] = int(row["n"])
        except (TypeError, ValueError):
            continue
    return out


def resolve_names(searches: dict, names: list[str]) -> list[str]:
    """Enabled seeds, or the names given (including parked ones)."""
    if not names:
        return [n for n, spec in searches.items() if spec.get("enabled", True)]
    missing = [n for n in names if n not in searches]
    if missing:
        known = ", ".join(searches) or "(none)"
        raise KeyError(f"unknown search {', '.join(missing)}; known: {known}")
    return list(names)


def is_drift(ours: int | None, live: int | None, *,
             pct: float = DEFAULT_THRESHOLD_PCT,
             min_delta: int = DEFAULT_MIN_DELTA) -> bool:
    """True when the gap is large in absolute terms or as a share of ours."""
    if ours is None or live is None:
        return False
    delta = abs(live - ours)
    if delta >= min_delta:
        return True
    return bool(ours > 0 and delta * 100 >= pct * ours)


def looks_blocked(html: str) -> bool:
    """Cloudflare / interstitial. A short bounce is not itself a block."""
    if not html:
        return False
    blob = html[:8000].lower()
    return any(marker in blob for marker in _BLOCK_MARKERS)


def _fetch_blocked(exc: BaseException) -> bool:
    blob = str(exc).lower()
    return any(token in blob for token in ("403", "forbidden", "cloudflare", "blocked"))


def page_size(source: str) -> int:
    mod = importlib.import_module(f"{source}.parse")
    return int(getattr(mod, "PER_PAGE", 20))


def live_count(page: dict[str, Any], source: str) -> tuple[int | None, str]:
    """``(count, 'exact'|'estimate')`` from a parse_list result.

    ``total`` / ``result_count`` win. A single page's cards are the whole
    result. More pages without a headline are estimated.
    """
    for key in ("total", "result_count"):
        raw = page.get(key)
        if isinstance(raw, bool) or raw is None:
            continue
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if n >= 0:
            return n, "exact"
    try:
        pages = max(1, int(page.get("total_pages") or 1))
    except (TypeError, ValueError):
        pages = 1
    cards = len(page.get("listings") or [])
    if pages <= 1:
        return cards, "exact"
    size = page_size(source)
    return (pages - 1) * size + cards, "estimate"


def seed_url(source: str, spec: dict) -> str:
    """Page-1 URL for this search. Bands are ignored; see the module doc."""
    from core.adapter import get

    path = spec["path"]
    if path.startswith("http://") or path.startswith("https://"):
        url = path
    else:
        base = get(source).base.rstrip("/")
        url = base + (path if path.startswith("/") else "/" + path)
    if source == "greenacres":
        from greenacres.parse import listing_api_url
        return listing_api_url(url, page=1)
    return url


def _note(kind: str, spec: dict, ours: int | None, live: int | None) -> str:
    skip = spec.get("skip") or {}
    # Centrarium's cheap-first list (and any seed whose URL is wider than
    # skip.above) has a headline far above the rows we store.
    if (kind == "exact" and skip and ours and live is not None
            and live > ours * 2):
        kind = "exact; list wider than skip"
    return kind


def _row(search: str, source: str, ours: int | None, live: int | None,
         note: str, *, drift: bool = False, detail: str = "") -> dict[str, Any]:
    delta = None if ours is None or live is None else live - ours
    return {
        "search": search,
        "source": source,
        "ours": ours,
        "live": live,
        "delta": delta,
        "note": note,
        "drift": drift,
        "detail": detail,
    }


def probe_one(name: str, spec: dict, fetcher, ours: int | None, *,
              pct: float = DEFAULT_THRESHOLD_PCT,
              min_delta: int = DEFAULT_MIN_DELTA) -> dict[str, Any]:
    """Fetch page 1 and score it. Never raises; never touches the database."""
    source = spec.get("source") or DEFAULT_SOURCE
    try:
        url = seed_url(source, spec)
        html = fetcher.get(url)
    except Exception as exc:  # one host must not abort the table
        blocked = _fetch_blocked(exc)
        return _row(name, source, ours, None,
                    "blocked" if blocked else "error", detail=str(exc))
    if looks_blocked(html):
        return _row(name, source, ours, None, "blocked")
    if not html or len(html.strip()) < 80:
        return _row(name, source, ours, None, "error", detail="empty response")
    try:
        from core.adapter import get
        page = get(source).parse_list(html, url)
    except Exception as exc:
        return _row(name, source, ours, None, "error", detail=str(exc))
    if page.get("blocked"):
        return _row(name, source, ours, None, "blocked")
    live, kind = live_count(page, source)
    note = _note(kind, spec, ours, live)
    if ours is None:
        note = f"{note}; no published count"
    drift = is_drift(ours, live, pct=pct, min_delta=min_delta)
    if drift:
        note = f"{note} drift"
    return _row(name, source, ours, live, note, drift=drift)


def _fmt_int(n: int | None) -> str:
    return "-" if n is None else f"{n:,}"


def _fmt_delta(n: int | None) -> str:
    return "-" if n is None else f"{n:+,}"


def format_report(rows: list[dict[str, Any]], *, quiet: bool,
                  pct: float, min_delta: int) -> str:
    shown = rows
    if quiet:
        shown = [r for r in rows if r["drift"] or r["note"].startswith(("blocked", "error"))]
    lines: list[str] = []
    if shown:
        cells = []
        for row in shown:
            cells.append([
                row["search"],
                row["source"],
                _fmt_int(row["ours"]),
                _fmt_int(row["live"]),
                _fmt_delta(row["delta"]),
                row["note"],
            ])
        header = ["search", "source", "ours", "live", "delta", "note"]
        widths = [len(h) for h in header]
        for row in cells:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(cell))
        def line(cols: list[str]) -> str:
            parts = []
            for i, cell in enumerate(cols):
                if i in (2, 3, 4):
                    parts.append(cell.rjust(widths[i]))
                else:
                    parts.append(cell.ljust(widths[i]))
            return "  ".join(parts)
        lines.append(line(header))
        for row in cells:
            lines.append(line(row))
    n_drift = sum(1 for r in rows if r["drift"])
    n_blocked = sum(1 for r in rows if str(r["note"]).startswith("blocked"))
    n_error = sum(1 for r in rows if str(r["note"]).startswith("error"))
    lines.append(
        f"{len(rows)} seeds, {n_drift} drifted, {n_blocked} blocked, {n_error} errors"
        f"  (|delta| >= {min_delta} or >= {pct:g}% of ours)"
    )
    return "\n".join(lines)


def _fetcher(source: str, refresh: bool, cache: dict):
    if source not in cache:
        mod = importlib.import_module(f"{source}.http")
        cache[source] = mod.Fetcher(refresh=refresh)
    return cache[source]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python3 -m core.probe",
        description=(
            "Compare each search's published count (data/meta.json) to one "
            "live list page. No database writes, no detail fetches."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python3 -m core.probe\n"
            "  python3 -m core.probe france-150k bg-houses-50k\n"
            "  python3 -m core.probe --quiet\n"
            "\n"
            "A seed drifts when |live - ours| >= --min-delta (50) or\n"
            ">= --threshold percent of the published count (5). Exit 1 if\n"
            "any seed drifts. Blocked portals (Cloudflare) are not scored.\n"
            "\n"
            "Price bands are not crawled: page 1 of the seed URL. Green-Acres\n"
            "uses AdvertsListing so the seed's advertsCount is the signal,\n"
            "not the ~480 cards in the first price band."
        ),
    )
    ap.add_argument("names", nargs="*", metavar="SEARCH",
                    help="seeds to probe (default: every enabled search)")
    ap.add_argument("--refresh", action="store_true", dest="refresh",
                    help="re-download page 1 (default; the probe does not trust the cache)")
    ap.add_argument("--no-refresh", action="store_false", dest="refresh",
                    help="use cache/ when that page is already stored")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD_PCT,
                    metavar="PCT",
                    help="percent of published n that counts as drift (default %(default)g)")
    ap.add_argument("--min-delta", type=int, default=DEFAULT_MIN_DELTA, metavar="N",
                    help="absolute listing change that counts as drift (default %(default)s)")
    ap.add_argument("--quiet", action="store_true",
                    help="print only drifted, blocked, and error rows")
    ap.add_argument("--meta", type=Path, default=META,
                    help=argparse.SUPPRESS)
    ap.set_defaults(refresh=True)
    args = ap.parse_args(argv)
    if args.threshold < 0 or args.min_delta < 0:
        print("threshold and min-delta must be >= 0", file=sys.stderr)
        return 2

    register_adapters()
    searches = load_searches()
    try:
        names = resolve_names(searches, args.names)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not args.meta.is_file():
        print(f"no published counts at {args.meta}", file=sys.stderr)
    published = published_counts(args.meta)

    fetchers: dict = {}
    rows = []
    for name in names:
        spec = searches[name]
        source = spec.get("source") or DEFAULT_SOURCE
        try:
            fetcher = _fetcher(source, args.refresh, fetchers)
            row = probe_one(name, spec, fetcher, published.get(name),
                            pct=args.threshold, min_delta=args.min_delta)
        except Exception as exc:  # unknown source, import error
            row = _row(name, source, published.get(name), None, "error", detail=str(exc))
        rows.append(row)
        if row["detail"] and row["note"].startswith(("error", "blocked")):
            print(f"{name}: {row['detail']}", file=sys.stderr)

    print(format_report(rows, quiet=args.quiet, pct=args.threshold,
                        min_delta=args.min_delta))
    return 1 if any(r["drift"] for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
