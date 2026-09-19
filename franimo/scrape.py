"""Scrape one or more saved searches into the local database.

    python3 -m franimo.scrape                # all searches in searches.json
    python3 -m franimo.scrape boerderijen-oost
    python3 -m franimo.scrape --refresh      # ignore the HTML cache
    python3 -m franimo.scrape --no-details   # cards only, much faster
    python3 -m franimo.scrape --max-pages 2  # dry-ish run while iterating

Re-running is cheap and safe: pages already in cache/ aren't re-downloaded
(unless --refresh or --max-age says otherwise) and every write is an upsert.
"""
from __future__ import annotations

import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor

from core.listing import DEFAULT_SOURCE
from core.searches import SEARCHES, enabled_names, load_searches

from . import adapter as _adapter  # noqa: F401  — register franimo
from . import db
from .http import BASE, Fetcher
from .parse import parse_detail, parse_list

SOURCE = "franimo"

# franimo stops paginating at ~714 pages (~10k results); page 715+ redirects to
# /error/. A search bigger than that has to be split into price bands.
PAGE_CEILING = 700


def with_prices(path: str, lo: int, hi: int) -> str:
    path = re.sub(r"pricefrom=\d+", f"pricefrom={lo}", path)
    return re.sub(r"priceto=\d+", f"priceto={hi}", path)


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


def crawl_search(con, fetcher: Fetcher, name: str, spec: dict, max_pages: int | None,
                 want_details: bool, redetail: bool, detail_limit: int | None = None,
                 workers: int = 4) -> dict:
    ts = db.now()
    started = ts
    counts = {"new": 0, "price_drop": 0, "price_rise": 0, "same": 0}
    seen_ids: list[int] = []
    pages = 0

    bands = spec.get("bands") or [None]
    for band in bands:
        path = spec["path"] if band is None else with_prices(spec["path"], band[0], band[1])
        url = BASE + path
        if band:
            print(f"  band €{band[0]:,}–€{band[1]:,}", flush=True)
        band_pages = 0
        skip = spec.get("skip", {})
        while url:
            try:
                html = fetcher.get(url)
            except RuntimeError as e:
                # A dead page shouldn't throw away everything already committed.
                print(f"  ! stopping this band: {e}", file=sys.stderr, flush=True)
                break
            page = parse_list(html, url)
            pages += 1
            band_pages += 1
            for row in page["listings"]:
                if _skipped(row, skip):
                    continue
                row["source"] = SOURCE
                row["external_id"] = str(row["id"])
                status, lid = db.upsert_from_list(con, row, name, ts, source=SOURCE)
                counts[status] += 1
                seen_ids.append(lid)
            con.commit()
            print(f"  page {band_pages}/{page['total_pages']}  "
                  f"({len(set(seen_ids))} listings)", flush=True)
            if band_pages >= PAGE_CEILING:
                print(f"  ! hit franimo's ~{PAGE_CEILING}-page pagination ceiling; "
                      f"split this search into price bands to see the rest",
                      file=sys.stderr, flush=True)
                break
            if max_pages and band_pages >= max_pages:
                break
            url = page["next_url"]

    seen_ids = list(dict.fromkeys(seen_ids))   # bands overlap at their boundaries

    gone = db.mark_gone(con, name, ts)

    if want_details:
        todo = seen_ids if redetail else db.needs_detail(con, seen_ids)
        # seen_ids is in search order (cheapest first), so a capped run always
        # backfills the most interesting listings first and the next run resumes.
        order = {lid: i for i, lid in enumerate(seen_ids)}
        todo.sort(key=lambda x: order.get(x, 0))
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


def _fetch_details(con, fetcher: Fetcher, ids: list[int], ts: str, workers: int = 4) -> None:
    urls = {r["id"]: r["url"] for r in con.execute(
        f"SELECT id, url FROM listings WHERE id IN ({','.join('?' * len(ids))})", ids)}

    def job(lid: int):
        try:
            return lid, parse_detail(fetcher.get(urls[lid]), urls[lid])
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
    ap = argparse.ArgumentParser(description="Scrape franimo.nl saved searches into SQLite.")
    ap.add_argument("search", nargs="*", help="search names from searches.json (default: all)")
    ap.add_argument("--refresh", action="store_true", help="ignore cached HTML")
    ap.add_argument("--max-age", type=float, default=None,
                    help="treat cached pages older than N days as stale")
    ap.add_argument("--max-pages", type=int, default=None)
    ap.add_argument("--no-details", action="store_true", help="skip detail pages")
    ap.add_argument("--redetail", action="store_true",
                    help="re-fetch detail pages even for listings we already have")
    ap.add_argument("--detail-limit", type=int, default=None,
                    help="fetch at most N detail pages this run (resumable; cheapest first)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--gap", type=float, default=0.3,
                    help="minimum seconds between requests (be kind)")
    ap.add_argument("--db", default=str(db.DB_PATH))
    args = ap.parse_args(argv)

    searches = load_searches()
    # Running with no arguments skips searches marked "enabled": false, so a
    # superseded search stays on file (and runnable by name) without being
    # re-scraped every time. Non-franimo searches are someone else's adapter.
    names = args.search or enabled_names(searches, source=SOURCE)
    unknown = [n for n in names if n not in searches]
    if unknown:
        ap.error(f"unknown search(es): {', '.join(unknown)}. Known: {', '.join(searches)}")
    foreign = [n for n in names
               if searches[n].get("source", DEFAULT_SOURCE) != SOURCE]
    if foreign:
        ap.error(f"not a franimo search: {', '.join(foreign)}. "
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
