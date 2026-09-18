"""Local web UI.

    python3 -m franimo.serve            # http://localhost:8765

Serves the static page from franimo/web/ plus a small JSON API. The whole
result set is sent to the browser in one go (a few hundred KB) and all
filtering/sorting happens client-side, which is what makes it feel instant
compared to clicking through 16 pages.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import db

WEB = Path(__file__).resolve().parent / "web"

LISTINGS_SQL = """
SELECT
    l.*,
    CASE WHEN l.living_m2 > 0 THEN CAST(l.price AS REAL) / l.living_m2 END AS eur_m2,
    CASE WHEN l.land_m2   > 0 THEN CAST(l.price AS REAL) / l.land_m2   END AS eur_m2_land,
    (SELECT price FROM price_history h WHERE h.listing_id = l.id
      ORDER BY h.id ASC  LIMIT 1) AS first_price,
    (SELECT COUNT(*) - 1 FROM price_history h WHERE h.listing_id = l.id) AS n_changes,
    CAST(julianday('now') - julianday(l.first_seen) AS INT) AS days_known,
    (SELECT GROUP_CONCAT(search, ',') FROM listing_search s WHERE s.listing_id = l.id) AS searches
FROM listings l
"""

JSON_COLS = ("photos", "raw_fields")
# Kept out of the bulk listing payload: photo arrays alone would be megabytes
# across thousands of listings, and nothing filters or sorts on them.
HEAVY_COLS = ("photos", "raw_fields")


def listings(con: sqlite3.Connection, where: str = "", args: tuple = (),
             light: bool = True) -> list[dict]:
    out = []
    for r in con.execute(LISTINGS_SQL + where, args):
        d = dict(r)
        if light:
            for c in HEAVY_COLS:
                d.pop(c, None)
        for c in JSON_COLS:
            if c not in d:
                continue
            if d.get(c):
                try:
                    d[c] = json.loads(d[c])
                except (TypeError, ValueError):
                    d[c] = None
        # A drop franimo itself advertises (strikethrough price) counts too.
        first = d.get("first_price")
        drop = 0
        if d.get("old_price") and d.get("price") and d["old_price"] > d["price"]:
            drop = d["old_price"] - d["price"]
        elif first and d.get("price") and first > d["price"]:
            drop = first - d["price"]
        d["price_drop"] = drop or None
        out.append(d)
    return out


def meta(con: sqlite3.Connection) -> dict:
    runs = [dict(r) for r in con.execute(
        "SELECT * FROM runs ORDER BY id DESC LIMIT 20")]
    return {
        "runs": runs,
        "total": con.execute("SELECT COUNT(*) c FROM listings").fetchone()["c"],
        "searches": [dict(r) for r in con.execute(
            "SELECT search, COUNT(*) n FROM listing_search GROUP BY search")],
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, db_path: str, **kw):
        self.db_path = db_path
        super().__init__(*a, directory=str(WEB), **kw)

    def log_message(self, fmt, *args):  # quieter console
        pass

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            con = db.connect(self.db_path)
            try:
                if path == "/api/listings":
                    payload = listings(con)
                elif path == "/api/meta":
                    payload = meta(con)
                elif path.startswith("/api/listing/"):
                    lid = path.rsplit("/", 1)[-1]
                    if not lid.isdigit():
                        self.send_error(404)
                        return
                    rows = listings(con, " WHERE l.id = ?", (int(lid),), light=False)
                    if not rows:
                        self.send_error(404)
                        return
                    payload = rows[0]
                else:
                    self.send_error(404)
                    return
            finally:
                con.close()
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            gzipped = "gzip" in self.headers.get("Accept-Encoding", "")
            if gzipped:
                body = gzip.compress(body, 5)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            if gzipped:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Serve the local franimo browser UI.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--db", default=str(db.DB_PATH))
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args(argv)

    handler = partial(Handler, db_path=args.db)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    url = f"http://localhost:{args.port}/"
    print(f"franimo UI on {url}  (db: {args.db})\nCtrl-C to stop")
    if not args.no_open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
