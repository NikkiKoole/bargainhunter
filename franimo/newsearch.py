"""Add a radius search to searches.json without hand-building a franimo URL.

    python3 -m franimo.newsearch --name annecy-300 --lat 45.8992 --lon 6.1294 --km 300
    python3 -m franimo.newsearch --name x --lat .. --lon .. --km 50 --types 3,7,12
    python3 -m franimo.newsearch --count-only --lat .. --lon .. --km 50

franimo's own search URL takes the radius in `kmrange_custom` (with `kmrange=0`),
so any centre and radius works. `--count-only` asks the site how many results a
radius would return before you commit to scraping it.
"""
from __future__ import annotations

import argparse
import json
import re

from core.searches import SEARCHES

from .http import BASE, Fetcher

# every franimo property type except appartement (2), nieuwbouw (31, 32) and kantoor (33)
DEFAULT_TYPES = [i for i in range(1, 35) if i not in {2, 31, 32, 33}]

TYPE_NAMES = {
    1: "huis", 2: "appartement", 3: "boerderij", 4: "villa", 5: "terrein", 6: "kasteel",
    7: "herenhuis", 8: "schuur", 9: "dorpshuis", 10: "vrijstaand", 11: "stadshuis",
    12: "dorpsboerderij", 13: "camping", 14: "bedrijfsruimte/kantoor",
    15: "gîtes/chambres d'hôtes", 16: "hotel-restaurant", 17: "winkel", 18: "wijngaard",
    19: "restaurant", 20: "bar-café", 21: "discotheek", 22: "eco huis", 23: "huis met gîte",
    24: "landgoed", 25: "chalet", 26: "klooster", 27: "tiny house", 28: "garage",
    29: "gebouw", 30: "bouwgrond", 31: "nieuwbouw woning", 32: "nieuwbouw bedrijfspand",
    33: "kantoor", 34: "hotel",
}


def build_path(lat: float | None, lon: float | None, km: float | None,
               types: list[int], pricefrom: int = 0, priceto: int = 999999999) -> str:
    """Omitting lat/lon/km searches all of France; franimo only lists French property."""
    q = f"pricefrom={pricefrom}&priceto={priceto}&zonestype=or"
    if lat is not None:
        q += (f"&areaid=0&longitude={lon}&latitude={lat}&zoom=7&kmrange_custom={km}"
              f"&savedproperties=false&kmrange=0&rooms=0")
    q += "".join(f"&propertytypes%5B%5D={t}" for t in types)
    return f"/woning/?{q}&submitted=true&orderby=price%20asc"


def split_bands(path: str, lo: int, hi: int, depth: int = 0) -> list[list[int]]:
    """Halve a price range until every band fits under franimo's page ceiling."""
    from .scrape import PAGE_CEILING, with_prices

    pages = count(with_prices(path, lo, hi))
    if pages is None or pages <= PAGE_CEILING or depth >= 6 or hi - lo <= 1000:
        return [[lo, hi]]
    mid = (lo + hi) // 2
    print(f"    €{lo:,}–€{hi:,} is {pages} pages, splitting at €{mid:,}")
    return split_bands(path, lo, mid, depth + 1) + split_bands(path, mid + 1, hi, depth + 1)


def count(path: str) -> int | None:
    """Ask franimo how many result pages this search has."""
    html = Fetcher(refresh=True).get(BASE + path)
    m = re.search(r"van\s+(\d+)</p>", html)
    return int(m.group(1)) if m else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name")
    ap.add_argument("--label", default=None)
    ap.add_argument("--lat", type=float, default=None)
    ap.add_argument("--lon", type=float, default=None)
    ap.add_argument("--km", type=float, default=None)
    ap.add_argument("--france", action="store_true", help="all of France, no radius")
    ap.add_argument("--price-to", type=int, default=999999999)
    ap.add_argument("--price-from", type=int, default=0)
    ap.add_argument("--types", default=None,
                    help="comma-separated type ids (default: all but appartement/nieuwbouw/kantoor)")
    ap.add_argument("--count-only", action="store_true",
                    help="just report how big this search is")
    args = ap.parse_args(argv)

    if not args.france and args.lat is None:
        ap.error("pass --france, or --lat/--lon/--km for a radius search")
    types = [int(t) for t in args.types.split(",")] if args.types else DEFAULT_TYPES
    path = build_path(None if args.france else args.lat, args.lon, args.km, types,
                      args.price_from, args.price_to)

    pages = count(path)
    if pages:
        print(f"{pages} pages ≈ {pages * 14} listings")
    if args.count_only:
        return 0
    if not args.name:
        ap.error("--name is required unless --count-only")

    from .scrape import PAGE_CEILING
    bands = None
    if pages and pages > PAGE_CEILING:
        print(f"  {pages} pages exceeds franimo's ~{PAGE_CEILING}-page limit; "
              f"splitting into price bands")
        bands = split_bands(path, args.price_from, args.price_to)
        print(f"  bands: {bands}")

    searches = json.loads(SEARCHES.read_text(encoding="utf-8"))
    searches[args.name] = {
        "source": "franimo",
        "label": args.label or ("heel Frankrijk" if args.france
                                else f"{args.km:g}km rond {args.lat}, {args.lon}"),
        "note": (("whole of France" if args.france
                  else f"radius search: {args.km:g}km around ({args.lat}, {args.lon})")
                 + f"; up to EUR {args.price_to:,}; "
                 f"types {','.join(str(t) for t in types)}"
                 + (f" ({', '.join(TYPE_NAMES.get(t, str(t)) for t in types)})"
                    if len(types) <= 8 else "")),
        "path": path,
        **({"bands": bands} if bands else {}),
    }
    SEARCHES.write_text(json.dumps(searches, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"added '{args.name}' to searches.json\n"
          f"  python3 -m franimo.scrape {args.name} --no-details   # list pages first")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
