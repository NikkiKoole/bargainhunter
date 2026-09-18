"""Drop listings outside the price range you actually care about.

    python3 -m franimo.prune --dry-run          # what would go
    python3 -m franimo.prune --max-price 150000

Takes a copy of the database first. Nothing here is unrecoverable anyway: the
scraped HTML stays in cache/, so re-running a search rebuilds the rows without
touching the network.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from pathlib import Path

from . import db


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-price", type=int, default=150000)
    ap.add_argument("--min-price", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    ap.add_argument("--drop-priceless", action="store_true",
                    help="also drop 'prijs op aanvraag' listings, which match every "
                         "price filter precisely because they have no price")
    ap.add_argument("--db", default=str(db.DB_PATH))
    args = ap.parse_args(argv)

    con = db.connect(args.db)
    where = "price > ? OR price < ?"
    params = (args.max_price, args.min_price)
    if args.drop_priceless:
        where = "price IS NULL OR " + where

    doomed = con.execute(f"SELECT COUNT(*) c FROM listings WHERE {where}", params).fetchone()["c"]
    keep = con.execute("SELECT COUNT(*) c FROM listings").fetchone()["c"] - doomed
    print(f"outside €{args.min_price:,}–€{args.max_price:,}"
          f"{' (or priceless)' if args.drop_priceless else ''}: {doomed} listings")
    print(f"keeping: {keep}")

    by_search = con.execute(
        f"SELECT s.search, COUNT(*) n FROM listing_search s JOIN listings l ON l.id = s.listing_id"
        f" WHERE {where} GROUP BY s.search ORDER BY n DESC", params).fetchall()
    for r in by_search:
        print(f"  {r['search']}: {r['n']}")

    if not args.drop_priceless:
        n = con.execute("SELECT COUNT(*) c FROM listings WHERE price IS NULL").fetchone()["c"]
        if n:
            print(f"keeping {n} 'prijs op aanvraag' listings (--drop-priceless removes them)")

    if args.dry_run or not doomed:
        return 0

    if not args.no_backup:
        backup = Path(args.db).with_suffix(".db.bak")
        con.commit()
        shutil.copy2(args.db, backup)
        print(f"backup: {backup}")

    con.execute(f"DELETE FROM price_history WHERE listing_id IN"
                f" (SELECT id FROM listings WHERE {where})", params)
    con.execute(f"DELETE FROM listing_search WHERE listing_id IN"
                f" (SELECT id FROM listings WHERE {where})", params)
    con.execute(f"DELETE FROM listings WHERE {where}", params)
    con.commit()
    print(f"deleted {doomed} listings")

    try:
        con.execute("VACUUM")
        print("vacuumed")
    except sqlite3.OperationalError as e:
        # A scraper mid-run holds the write lock; the rows are gone either way.
        print(f"skipped vacuum ({e}) — run it later to reclaim the space")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
