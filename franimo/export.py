"""Export a static build that needs no Python at all.

    python3 -m franimo.export            # writes docs/

The result is the same UI reading JSON files instead of the local API, so it
works from a file:// path, GitHub Pages, or any static host. Re-run it after
every scrape; it overwrites the previous build.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from . import db, serve

ROOT = Path(__file__).resolve().parent.parent
WEB = Path(__file__).resolve().parent / "web"


# Dropped: derivable on the client (eur_m2, price_drop, days_known), or a
# truncated copy of something we already ship (snippet).
DROP = {"snippet", "eur_m2", "eur_m2_land", "price_drop", "days_known", "raw_fields", "photos"}
URL_PREFIX = "https://www.franimo.nl"


class Prefixes:
    """Image paths are mostly a long constant directory plus a short filename
    (the CDN prefix alone is ~50 characters). Store the directory once."""

    def __init__(self):
        self.order: list[str] = []
        self.seen: dict[str, int] = {}

    def split(self, url: str):
        head, _, name = url.rpartition("/")
        head += "/"
        if head not in self.seen:
            self.seen[head] = len(self.order)
            self.order.append(head)
        return self.seen[head], name


def pack(rows: list[dict]) -> dict:
    """Columnar + dictionary-coded.

    Array-of-objects repeats every key name 10k times, and columns like
    first_seen or agent hold a handful of distinct values across the whole set.
    Storing an index into a dictionary costs a couple of bytes instead.
    """
    cols = [c for c in rows[0].keys() if c not in DROP] if rows else []

    prefixes = Prefixes()
    for r in rows:
        if r.get("thumb"):
            r["thumb"] = list(prefixes.split(r["thumb"]))
        if r.get("url", "").startswith(URL_PREFIX):
            r["url"] = r["url"][len(URL_PREFIX):]

    dicts: dict[str, list] = {}
    for c in cols:
        vals = [r.get(c) for r in rows]
        distinct = {v for v in vals if isinstance(v, str)}
        # Only worth it when values actually repeat, and only for strings.
        if distinct and len(distinct) <= len(rows) * 0.6:
            dicts[c] = sorted(distinct)

    index = {c: {v: i for i, v in enumerate(vals)} for c, vals in dicts.items()}
    packed = []
    for r in rows:
        packed.append([
            index[c].get(r.get(c)) if c in index and r.get(c) is not None else r.get(c)
            for c in cols
        ])

    return {"v": 2, "cols": cols, "dict": dicts, "imgPrefixes": prefixes.order,
            "urlPrefix": URL_PREFIX, "rows": packed}


DEFAULT_EXT = ".jpg"


def _shorten(name: str) -> str:
    """Most images are .jpg; the client puts the extension back."""
    return name[:-len(DEFAULT_EXT)] if name.endswith(DEFAULT_EXT) else "=" + name


def write_json(path: Path, payload) -> int:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path.write_bytes(body)
    return len(body)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=".")
    ap.add_argument("--db", default=str(db.DB_PATH))
    ap.add_argument("--no-photos", action="store_true",
                    help="skip the photo index (smaller; drawer shows no photo strip)")
    args = ap.parse_args(argv)

    out = ROOT / args.out
    data = out / "data"
    data.mkdir(parents=True, exist_ok=True)

    for name in ("app.js", "style.css"):
        shutil.copy2(WEB / name, out / name)

    # The same page, told to read files instead of the API.
    html = (WEB / "index.html").read_text(encoding="utf-8")
    html = html.replace('<script src="app.js"></script>',
                        '<script>window.FRANIMO_STATIC = true;</script>\n'
                        '<script src="app.js"></script>')
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")

    con = db.connect(args.db)
    sizes = {}
    rows = serve.listings(con)
    sizes["listings.json"] = write_json(data / "listings.json", pack(rows))
    sizes["meta.json"] = write_json(data / "meta.json", serve.meta(con))

    if not args.no_photos:
        prefixes, photos = Prefixes(), {}
        for r in con.execute("SELECT id, photos FROM listings WHERE photos IS NOT NULL"):
            try:
                p = json.loads(r["photos"])
            except (TypeError, ValueError):
                continue
            if not p:
                continue
            split = [prefixes.split(x) for x in p]
            heads = {i for i, _ in split}
            if len(heads) == 1:
                # Normal case: every photo of a listing sits in one directory.
                photos[str(r["id"])] = [split[0][0], [_shorten(n) for _, n in split]]
            else:
                photos[str(r["id"])] = [None, list(p)]
        sizes["photos.json"] = write_json(
            data / "photos.json", {"prefixes": prefixes.order, "photos": photos})

    print(f"{len(rows)} listings -> {out}")
    for name, n in sizes.items():
        print(f"  data/{name:15s} {n / 1e6:6.1f} MB")
    print(f"\ncheck it with:  python3 -m http.server -d {args.out} 8080")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
