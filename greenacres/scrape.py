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
import sys
from concurrent.futures import ThreadPoolExecutor

from core import db
from core.listing import DEFAULT_SOURCE
from core.searches import enabled_names, load_searches

from . import adapter as _adapter  # noqa: F401  — register greenacres
from .http import BASE, DEFAULT_GAP, Fetcher
from .parse import SOURCE, is_blocked, listing_api_url, parse_detail, parse_list


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
                 workers: int = 1) -> dict:
    ts = db.now()
    started = ts
    counts = {"new": 0, "price_drop": 0, "price_rise": 0, "same": 0}
    seen_ids: list[int] = []
    pages = 0
    skip = spec.get("skip", {})

    seed = spec["path"] if spec["path"].startswith("http") else BASE + spec["path"]
    # HTML `?page=` is a no-op; featured/relevance paints luxury. Start on
    # AdvertsListing with price_i so page 1 is the cheap end.
    url = listing_api_url(seed, page=1)
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
        for row in page["listings"]:
            if _skipped(row, skip):
                continue
            row["source"] = SOURCE
            row["external_id"] = str(row.get("external_id") or row.get("id"))
            status, lid = db.upsert_from_list(con, row, name, ts, source=SOURCE)
            counts[status] += 1
            seen_ids.append(lid)
        con.commit()
        print(f"  page {pages}/{page['total_pages']}  "
              f"({len(set(seen_ids))} listings)", flush=True)
        if max_pages and pages >= max_pages:
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
