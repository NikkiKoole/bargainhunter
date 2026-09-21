"""SQLite storage.

Design notes:
  * `listings` holds the latest known state of every property we've ever seen.
  * `source` + `external_id` is the natural key, so two portals can reuse the
    same numeric id without colliding. `id` is an opaque internal integer
    (UI, photos, foreign keys). Existing franimo rows keep their current id.
  * `price_history` gets a row only when a price actually changes, so
    "what got cheaper" is a query, not a guess.
  * `first_seen` / `last_seen` / `gone_at` let the UI answer "what's new?" and
    "what disappeared?" from the second run onwards. The first run just
    establishes the baseline.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .geo import country_for
from .listing import DEFAULT_CURRENCY, DEFAULT_SOURCE, as_row, identity
from .paths import DB_PATH, ROOT

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id              INTEGER PRIMARY KEY,
    source          TEXT NOT NULL DEFAULT 'franimo',
    external_id     TEXT,
    country         TEXT,
    url             TEXT,
    type            TEXT,
    place           TEXT,
    region          TEXT,
    dept_nl         TEXT,
    dept_fr         TEXT,
    lat             REAL,
    lon             REAL,
    price           INTEGER,
    currency        TEXT DEFAULT 'EUR',
    old_price       INTEGER,
    rooms           INTEGER,
    bedrooms        INTEGER,
    baths           INTEGER,
    living_m2       INTEGER,
    land_m2         INTEGER,
    year_built      INTEGER,
    energy_label    TEXT,
    gas_label       TEXT,
    energy_kwh      INTEGER,
    gas_co2         INTEGER,
    reference       TEXT,
    agent           TEXT,
    agent_name      TEXT,
    agent_address   TEXT,
    snippet         TEXT,
    description     TEXT,
    features        TEXT,
    thumb           TEXT,
    photos          TEXT,      -- json array
    raw_fields      TEXT,      -- json object, everything we didn't model
    promoted        INTEGER DEFAULT 0,
    first_seen      TEXT,
    last_seen       TEXT,
    detail_fetched  TEXT,
    gone_at         TEXT
);

-- append-only: one row per observed price change (plus the first sighting)
CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id  INTEGER NOT NULL,
    seen_at     TEXT NOT NULL,
    price       INTEGER
);

-- which saved search turned up which listing
CREATE TABLE IF NOT EXISTS listing_search (
    listing_id  INTEGER NOT NULL,
    search      TEXT NOT NULL,
    last_seen   TEXT,
    PRIMARY KEY (listing_id, search)
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    search      TEXT,
    started_at  TEXT,
    finished_at TEXT,
    pages       INTEGER,
    seen        INTEGER,
    new         INTEGER,
    price_drops INTEGER,
    price_rises INTEGER,
    gone        INTEGER
);

CREATE INDEX IF NOT EXISTS idx_listings_price ON listings(price);
CREATE INDEX IF NOT EXISTS idx_listings_gone  ON listings(gone_at);
CREATE INDEX IF NOT EXISTS idx_hist_listing   ON price_history(listing_id);
"""

LIST_COLS = ("url", "type", "place", "region", "dept_nl", "lat", "lon", "price", "old_price",
             "beds", "living_m2", "land_m2", "reference", "thumb", "snippet", "promoted")
DETAIL_COLS = ("url", "type", "place", "region", "dept_nl", "dept_fr", "lat", "lon",
               "price", "rooms",
               "bedrooms", "baths", "living_m2", "land_m2", "year_built", "energy_label",
               "gas_label", "energy_kwh", "gas_co2", "reference", "agent", "agent_name", "agent_address",
               "description", "features", "photos", "raw_fields", "currency")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A scrape can be running while you prune or browse, so wait for the write
    # lock rather than failing instantly. WAL lets the UI read during a scrape.
    con = sqlite3.connect(path, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 60000")
    try:
        con.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass
    con.executescript(SCHEMA)
    _migrate(con)
    return con


def _migrate(con: sqlite3.Connection) -> None:
    """Add any columns declared in SCHEMA that an older database is missing."""
    have = {r["name"] for r in con.execute("PRAGMA table_info(listings)")}
    block = SCHEMA.split("CREATE TABLE IF NOT EXISTS listings (", 1)[1].split(");", 1)[0]
    for line in block.splitlines():
        m = re.match(r"\s+(\w+)\s+(INTEGER|TEXT|REAL)\b", line)
        if m and m.group(1) not in have:
            try:
                con.execute(f"ALTER TABLE listings ADD COLUMN {m.group(1)} {m.group(2)}")
            except sqlite3.OperationalError:
                pass
    _backfill_source(con)
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_listings_source_ext "
                "ON listings(source, external_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_listings_source ON listings(source)")
    con.commit()


def _backfill_source(con: sqlite3.Connection) -> None:
    """Existing rows predate the source column: they are all franimo, and
    their internal id *was* the portal id."""
    have = {r["name"] for r in con.execute("PRAGMA table_info(listings)")}
    if "source" in have:
        con.execute("UPDATE listings SET source=? WHERE source IS NULL OR source=''",
                    (DEFAULT_SOURCE,))
    if "external_id" in have:
        con.execute("UPDATE listings SET external_id=CAST(id AS TEXT) "
                    "WHERE external_id IS NULL OR external_id=''")
    if "currency" in have:
        con.execute("UPDATE listings SET currency=? WHERE currency IS NULL OR currency=''",
                    (DEFAULT_CURRENCY,))
    if "country" in have:
        _backfill_country(con)


def _backfill_country(con: sqlite3.Connection) -> None:
    """Rows scraped before the country column: take what the portal said, else
    the adapter's home country."""
    rows = con.execute(
        "SELECT id, source, raw_fields FROM listings "
        "WHERE country IS NULL OR country=''").fetchall()
    for r in rows:
        raw = {}
        if r["raw_fields"]:
            try:
                raw = json.loads(r["raw_fields"]) or {}
            except (TypeError, ValueError):
                raw = {}
        code = country_for({"raw_fields": raw}, r["source"] or DEFAULT_SOURCE)
        if code:
            con.execute("UPDATE listings SET country=? WHERE id=?", (code, r["id"]))


def clean_latlon(row: dict) -> None:
    """Drop coordinates that cannot exist.

    Unlike a wrong price, an out-of-range coordinate is not data to surface --
    it is invalid by definition, and one of them poisons the whole map:
    abruzzopropertyitaly serves `google.maps.LatLng(42.0476654, 139256123)` for
    Sulmona (a lost decimal point), and that single row pushed Leaflet's
    fitBounds to zoom 0 centred at longitude 69,628,031, hiding all 10,774
    pins. We do not guess where the decimal belonged; we store nothing.
    """
    for key, limit in (("lat", 90.0), ("lon", 180.0)):
        value = row.get(key)
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            row[key] = None
            continue
        row[key] = number if -limit <= number <= limit else None


def _encode(value: Any) -> Any:
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value


def upsert_from_list(con: sqlite3.Connection, row: dict, search: str, ts: str,
                     source: str = DEFAULT_SOURCE) -> tuple[str, int]:
    """Insert or update a listing from a search-result card.

    Returns (status, internal_id) where status is 'new', 'price_drop',
    'price_rise' or 'same'. Lookup is by (source, external_id); the integer
    id is assigned by SQLite so two portals cannot collide.
    """
    row = as_row(row)
    if not row.get("source"):
        row["source"] = source
    src, external_id = identity(row, default_source=source)
    prev = con.execute(
        "SELECT id, price, gone_at FROM listings WHERE source=? AND external_id=?",
        (src, external_id),
    ).fetchone()

    data = {k: row.get(k) for k in LIST_COLS if k in row}
    data["baths"] = row.get("baths")
    data["bedrooms"] = row.get("beds") if row.get("beds") is not None else row.get("bedrooms")
    data.pop("beds", None)
    data["promoted"] = int(bool(row.get("promoted")))
    data["source"] = src
    data["external_id"] = external_id
    clean_latlon(row)
    data["lat"], data["lon"] = row.get("lat"), row.get("lon")
    data["currency"] = row.get("currency") or DEFAULT_CURRENCY
    code = country_for(row, src)
    if code:
        data["country"] = code
    data["last_seen"] = ts
    data["gone_at"] = None

    if prev is None:
        data["first_seen"] = ts
        cols = ", ".join(data)
        cur = con.execute(
            f"INSERT INTO listings ({cols}) VALUES ({', '.join('?' * len(data))})",
            [_encode(v) for v in data.values()],
        )
        lid = cur.lastrowid
        con.execute("INSERT INTO price_history (listing_id, seen_at, price) VALUES (?,?,?)",
                    (lid, ts, row.get("price")))
        status = "new"
    else:
        lid = prev["id"]
        # Never blank out detail-page fields with the card's sparser data.
        data = {k: v for k, v in data.items() if v is not None or k == "gone_at"}
        sets = ", ".join(f"{k}=?" for k in data)
        con.execute(f"UPDATE listings SET {sets} WHERE id=?",
                    [*(_encode(v) for v in data.values()), lid])
        old, new = prev["price"], row.get("price")
        if new is not None and old is not None and new != old:
            con.execute("INSERT INTO price_history (listing_id, seen_at, price) VALUES (?,?,?)",
                        (lid, ts, new))
            status = "price_drop" if new < old else "price_rise"
        else:
            status = "same"

    con.execute("INSERT OR REPLACE INTO listing_search VALUES (?,?,?)", (lid, search, ts))
    return status, lid


# Columns where a parsed None means "really unknown", so a re-parse can clear a
# value we stored earlier. Everything else is only ever filled in, never blanked.
CLEARABLE = ("living_m2",)

# ...but only for sources whose detail page *always* states living area, so that
# a missing value genuinely means "there is none" (franimo: a land parcel).
# Everywhere else a missing value only means "this page doesn't show it", and
# clearing destroyed m² the list card had already supplied — akiyaportal went
# from 99% coverage to 2.5% after its first 200 detail fetches.
CLEARS_LIVING_M2 = {"franimo"}


def update_from_detail(con: sqlite3.Connection, lid: int, detail: dict, ts: str) -> None:
    detail = as_row(detail)
    clean_latlon(detail)
    data = {k: detail.get(k) for k in DETAIL_COLS if detail.get(k) is not None}
    if detail.get("raw_fields"):           # we did parse the info table, so trust it
        row = con.execute("SELECT source FROM listings WHERE id=?", (lid,)).fetchone()
        if row and row["source"] in CLEARS_LIVING_M2:
            for col in CLEARABLE:
                if detail.get(col) is None:
                    data[col] = None
    data["detail_fetched"] = ts
    sets = ", ".join(f"{k}=?" for k in data)
    con.execute(f"UPDATE listings SET {sets} WHERE id=?",
                [*(_encode(v) for v in data.values()), lid])


# A run that ends early — a fetch failure, the page ceiling, --max-pages —
# still reaches mark_gone, and everything it never got to looks "absent". These
# guards decide whether a run saw enough of the search to be believed.
MIN_SEEN_FRACTION = 0.6      # below this, a run looks truncated rather than sold-out
SMALL_ATTRITION = 5          # this few disappearing is ordinary churn at any size


def mark_gone(con: sqlite3.Connection, search: str, ts: str, *,
              force: bool = False) -> int:
    """Flag listings this search used to find but didn't this time.

    Refuses when the run doesn't look complete. Marking a live listing as sold
    is far worse than missing one: the whole point of gone_at is that you can
    trust it, and a truncated crawl (`--max-pages 1` on Holprop) otherwise
    reports 11 of 20 listings as sold when none of them left the site.
    """
    seen = con.execute(
        "SELECT COUNT(*) c FROM listing_search WHERE search=? AND last_seen=?",
        (search, ts)).fetchone()["c"]
    missing = con.execute(
        "SELECT COUNT(*) c FROM listing_search WHERE search=? AND last_seen<>?",
        (search, ts)).fetchone()["c"]
    total = seen + missing

    if not force and total:
        why = None
        if seen == 0:
            why = "this run found nothing at all"
        elif missing > SMALL_ATTRITION and seen < MIN_SEEN_FRACTION * total:
            why = (f"this run saw {seen} of {total} known listings "
                   f"({seen / total:.0%})")
        if why:
            print(f"  ! not marking {missing} listing(s) gone for {search}: {why}."
                  f" A truncated crawl looks exactly like a sold-out portal;"
                  f" re-run it complete, or pass force=True if this is real.",
                  file=sys.stderr, flush=True)
            return 0

    cur = con.execute("""
        UPDATE listings SET gone_at=?
        WHERE gone_at IS NULL
          AND id IN (SELECT listing_id FROM listing_search WHERE search=? AND last_seen<>?)
    """, (ts, search, ts))
    return cur.rowcount


def needs_detail(con: sqlite3.Connection, ids: list[int]) -> list[int]:
    """Listings we've never pulled a detail page for."""
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = con.execute(
        f"SELECT id FROM listings WHERE id IN ({marks}) AND detail_fetched IS NULL", ids
    ).fetchall()
    return [r["id"] for r in rows]
