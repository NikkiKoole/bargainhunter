"""Scrape Green-Acres (green-acres.fr) saved searches.

    python3 -m greenacres.scrape                          # enabled greenacres searches
    python3 -m greenacres.scrape ga-fr-houses-150k
    python3 -m greenacres.scrape ga-fr-houses-150k --no-details --max-pages 1
    python3 -m greenacres.scrape ga-fr-houses-150k --detail-limit 20

`python3 -m franimo.scrape` will refuse these — it only runs source=franimo.

`prc_max` on the HTML search is ignored. The seed uses `mx_p-150000` plus
`skip.above` at ingest. Pagination is the site's AdvertsListing JSON
(`order=price_i`) — featured/relevance paints luxury first. Point trials
at `--db db/scratch.db`.
"""
from __future__ import annotations

import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor

from core import db
from core.bands import describe, split_bands
from core.listing import DEFAULT_SOURCE
from core.searches import enabled_names, load_searches

from . import adapter as _adapter  # noqa: F401  — register greenacres
from .http import BASE, DEFAULT_GAP, Fetcher
from .parse import (SOURCE, _search_tokens, is_blocked, listing_api_url,
                    parse_detail, parse_list)

# The AdvertsListing pager 404s on p_n=21 whatever the query: 20 pages x 24
# cards = 480 listings is all any single search can return. The catalogue is
# ~4,700 houses, so the price range has to be split until each band fits.
PAGE_CEILING = 20


def with_prices(path: str, lo: int, hi: int) -> str:
    """Set mn_p / mx_p in a green-acres searchQuery token string."""
    def sub(m):
        tokens = re.sub(r"-?\bmn_p-\d+", "", m.group(1))
        tokens = re.sub(r"-?\bmx_p-\d+", "", tokens)
        return "searchQuery=" + tokens.strip("-") + f"-mn_p-{lo}-mx_p-{hi}"
    return re.sub(r"searchQuery=([^&]*)", sub, path)


def seed_price_range(path: str, skip: dict) -> tuple[int, int]:
    tokens = _search_tokens(path)
    lo = int(re.sub(r"[^\d]", "", tokens.get("mn_p") or "") or 0)
    hi = int(re.sub(r"[^\d]", "", tokens.get("mx_p") or "") or 0)
    return lo, hi or int(skip.get("above") or 0)


def _skipped(row: dict, skip: dict) -> bool:
    """A search can refuse listings at ingest, so pruning stays pruned."""
    price = row.get("price")
    if price is None:
        return bool(skip.get("priceless"))
    if skip.get("above") is not None and price > skip["above"]:
        return True
    if skip.get("below") is not None and price < skip["below"]:
        return True
    return False


def _plan_bands(fetcher: Fetcher, seed: str, skip: dict) -> list:
    """Measure the search and, if it overflows the pager, split it by price.

    One request per probe. Returns [None] (crawl the seed as-is) when the
    search already fits.
    """
    def pages_for(lo: int, hi: int) -> int | None:
        url = listing_api_url(with_prices(seed, lo, hi), page=1)
        try:
            return parse_list(fetcher.get(url), url)["total_pages"]
        except Exception:
            return None

    probe = listing_api_url(seed, page=1)
    try:
        total = parse_list(fetcher.get(probe), probe)["total_pages"]
    except Exception:
        return [None]
    if total <= PAGE_CEILING:
        return [None]

    lo, hi = seed_price_range(seed, skip)
    if not hi:
        print(f"  ! {total} pages but no price range to split on; "
              f"only the first {PAGE_CEILING} pages are reachable",
              file=sys.stderr, flush=True)
        return [None]

    print(f"  {total} pages exceeds the {PAGE_CEILING}-page pager limit; "
          f"splitting €{lo:,}–€{hi:,} by price", flush=True)
    bands = split_bands(pages_for, lo, hi, PAGE_CEILING, log=lambda m: print(m, flush=True))
    print(f"  {len(bands)} bands: {describe(bands)}", flush=True)
    return bands


def crawl_search(con, fetcher: Fetcher, name: str, spec: dict, max_pages: int | None,
                 want_details: bool, redetail: bool, detail_limit: int | None = None,
                 workers: int = 1) -> dict:
    ts = db.now()
    started = ts
    counts = {"new": 0, "price_drop": 0, "price_rise": 0, "same": 0}
    seen_ids: list[int] = []
    pages = 0
    skip = spec.get("skip", {})

    seed = spec["path"] if spec["path"].startswith("http") else BASE + spec["path"]

    bands = [b for b in (spec.get("bands") or [])] or [None]
    if bands == [None] and not max_pages:
        bands = _plan_bands(fetcher, seed, skip)

    for band in bands:
        band_seed = seed if band is None else with_prices(seed, band[0], band[1])
        if band:
            print(f"  band €{band[0]:,}–€{band[1]:,}", flush=True)
        band_pages = 0
        # HTML `?page=` is a no-op; featured/relevance paints luxury. Start on
        # AdvertsListing with price_i so page 1 is the cheap end.
        url = listing_api_url(band_seed, page=1)
        while url:
            try:
                html = fetcher.get(url)
            except RuntimeError as e:
                print(f"  ! stopping: {e}", file=sys.stderr, flush=True)
                if "403" in str(e) or "Forbidden" in str(e):
                    print(
                        "  ! Green-Acres refused this IP. Cached pages in cache/ "
                        "re-parse with zero requests.",
                        file=sys.stderr, flush=True,
                    )
                break
            if is_blocked(html):
                print(
                    "  ! Cloudflare blocked this IP. Cached pages in cache/ "
                    "re-parse with zero requests.",
                    file=sys.stderr, flush=True,
                )
                break
            page = parse_list(html, url)
            pages += 1
            band_pages += 1
            for row in page["listings"]:
                if _skipped(row, skip):
                    continue
                row["source"] = SOURCE
                row["external_id"] = str(row.get("external_id") or row.get("id"))
                status, lid = db.upsert_from_list(con, row, name, ts, source=SOURCE)
                counts[status] += 1
                seen_ids.append(lid)
            con.commit()
            print(f"  page {band_pages}/{page['total_pages']}  "
                  f"({len(set(seen_ids))} listings)", flush=True)
            if band_pages >= PAGE_CEILING:
                # p_n=21 is a 404. Exactly 20 pages is a complete band, not a
                # truncated one — only a band claiming more has lost its tail.
                if page["total_pages"] > PAGE_CEILING:
                    print(f"  ! band stopped at the {PAGE_CEILING}-page ceiling "
                          f"with {page['total_pages']} pages claimed; narrow the "
                          f"price range to reach the rest",
                          file=sys.stderr, flush=True)
                break
            if max_pages and band_pages >= max_pages:
                break
            url = page["next_url"]

    seen_ids = list(dict.fromkeys(seen_ids))

    gone = db.mark_gone(con, name, ts)

    if want_details:
        todo = seen_ids if redetail else db.needs_detail(con, seen_ids)
        if todo:
            marks = ",".join("?" * len(todo))
            priced = {r["id"]: r["price"] for r in con.execute(
                f"SELECT id, price FROM listings WHERE id IN ({marks})", todo)}
            todo.sort(key=lambda x: (priced.get(x) is None, priced.get(x) or 0))
        outstanding = len(todo)
        if detail_limit:
            todo = todo[:detail_limit]
        if todo:
            print(f"  fetching {len(todo)} detail pages"
                  f"{f' (of {outstanding} outstanding)' if outstanding > len(todo) else ''}…",
                  flush=True)
            _fetch_details(con, fetcher, todo, ts, workers)
            if outstanding > len(todo):
                print(f"  {outstanding - len(todo)} still without details "
                      f"— run again to continue", flush=True)

    con.commit()
    con.execute(
        "INSERT INTO runs (search, started_at, finished_at, pages, seen, new, price_drops,"
        " price_rises, gone) VALUES (?,?,?,?,?,?,?,?,?)",
        (name, started, db.now(), pages, len(seen_ids), counts["new"],
         counts["price_drop"], counts["price_rise"], gone),
    )
    con.commit()
    return {**counts, "pages": pages, "seen": len(seen_ids), "gone": gone}


def _fetch_details(con, fetcher: Fetcher, ids: list[int], ts: str, workers: int = 1) -> None:
    urls = {r["id"]: r["url"] for r in con.execute(
        f"SELECT id, url FROM listings WHERE id IN ({','.join('?' * len(ids))})", ids)}

    def job(lid: int):
        try:
            html = fetcher.get(urls[lid])
            if is_blocked(html):
                print(f"    ! {lid}: Cloudflare blocked detail fetch", file=sys.stderr)
                return lid, None
            return lid, parse_detail(html, urls[lid])
        except Exception as e:  # keep going; one bad page shouldn't kill a run
            print(f"    ! {lid}: {e}", file=sys.stderr)
            return lid, None

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for lid, detail in pool.map(job, ids):
            if detail:
                db.update_from_detail(con, lid, detail, ts)
            done += 1
            if done % 25 == 0:
                con.commit()
                print(f"    {done}/{len(ids)}", flush=True)
    con.commit()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Scrape Green-Acres (green-acres.fr) into SQLite.")
    ap.add_argument("search", nargs="*",
                    help="search names from searches.json (default: enabled greenacres)")
    ap.add_argument("--refresh", action="store_true", help="ignore cached HTML")
    ap.add_argument("--max-age", type=float, default=None,
                    help="treat cached pages older than N days as stale")
    ap.add_argument("--max-pages", type=int, default=None)
    ap.add_argument("--no-details", action="store_true", help="skip detail pages")
    ap.add_argument("--redetail", action="store_true",
                    help="re-fetch detail pages even for listings we already have")
    ap.add_argument("--detail-limit", type=int, default=None,
                    help="fetch at most N detail pages this run (resumable; cheapest first)")
    ap.add_argument("--workers", type=int, default=1,
                    help="detail-page workers (default 1 — be kind)")
    ap.add_argument("--gap", type=float, default=DEFAULT_GAP,
                    help="minimum seconds between requests (be kind; robots Crawl-delay: 1)")
    ap.add_argument("--db", default=str(db.DB_PATH))
    args = ap.parse_args(argv)

    searches = load_searches()
    names = args.search or enabled_names(searches, source=SOURCE)
    unknown = [n for n in names if n not in searches]
    if unknown:
        ap.error(f"unknown search(es): {', '.join(unknown)}. Known: {', '.join(searches)}")
    foreign = [n for n in names
               if searches[n].get("source", DEFAULT_SOURCE) != SOURCE]
    if foreign:
        ap.error(f"not a greenacres search: {', '.join(foreign)}. "
                 f"Use that source's scrape module instead.")

    con = db.connect(args.db)
    fetcher = Fetcher(refresh=args.refresh, max_age_days=args.max_age, gap=args.gap)

    for name in names:
        print(f"\n=== {name}: {searches[name].get('label', '')}")
        r = crawl_search(con, fetcher, name, searches[name], args.max_pages,
                         not args.no_details, args.redetail, args.detail_limit, args.workers)
        print(f"  -> {r['seen']} listings over {r['pages']} pages | "
              f"{r['new']} new, {r['price_drop']} cheaper, {r['price_rise']} dearer, "
              f"{r['gone']} gone")

    print(f"\ncache: {fetcher.hits} hits, {fetcher.misses} downloads")
    print(f"db:    {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
