# Working on bargainhunter

Scrapes property portals into SQLite and publishes a static browser at
https://mipolai.com/bargainhunter/. Franimo.nl is the only live source today;
shared HTTP/DB/export live in `core/` so Phase 1 adapters (ok_bulgaria,
akiyaportal) can plug in without rewriting that path. `README.md` is the
reference; this file is the order of operations and the things that are
easy to get wrong.

Stdlib + `requests`/`beautifulsoup4`/`lxml` only. No build step, no framework.

## Refreshing the data — the whole sequence

```sh
python3 -m franimo.scrape                 # 1. list pages, then detail pages
python3 -m franimo.export                 # 2. rebuild index.html + data/*.json
git add -A && git commit -m "data refresh" && git push   # 3. Pages rebuilds in ~1 min
```

Run `python3 -m franimo.serve` (http://localhost:8765) to check it locally first.
Pages serves from `main` at the repo root, so the site files must stay at the root.

## Rules that matter

**One scraper at a time.** The rate limit (`MIN_GAP` in `core.http`, default
gap 0.3s on the franimo CLI) is a process-wide lock, so two processes double
the request rate at the host. Chain runs; don't parallelise them.

**List pages first on anything large.** `--no-details` finishes a 500-page search in
minutes and makes the UI usable; the detail backfill is the long pole (~3.3 pages/s).
`--detail-limit N` caps a run and the next run resumes, cheapest-first.

**Never delete `cache/`.** Every fetched page is stored gzipped, keyed by
sha1(full URL). This is what makes `--redetail` re-parse all 10k listings
*with zero requests* after a parser change — the single most useful property
of this codebase. Reach for it whenever you touch `parse.py`. Do not move
cache files into per-source subdirectories; that would orphan the existing
store.

**The database is `db/franimo.db`,** not `data/`. `data/` is the published JSON.
Rows are unique on `(source, external_id)`. `id` is an internal integer — do
not assume it equals the portal id once a second source exists.

**`searches.json` `"source"`** selects the adapter. Omitted source means
`franimo`. `python3 -m franimo.scrape` only runs franimo searches.

**Sizing a search before scraping it** is one read-only request:
```sh
python3 -m franimo.newsearch --count-only --france --price-to 100000
```

**franimo stops paginating at ~714 pages (~10k results).** Page 715 redirects to an
error page, so a bigger search silently loses its tail. `newsearch` detects this and
writes `"bands"` into `searches.json` to split the price range; the scraper crawls
each band and dedupes by id. Don't hand-write a search over that size without bands.

**`searches.json` is the source of truth for scope.** `"enabled": false` parks a
search (skipped when running with no arguments, still runnable by name).
`"skip": {"priceless": true, "above": 150000}` refuses listings at ingest, so a
re-scrape can't silently undo a `prune`.

## Verify by querying the result, not by trusting the run

Every data bug here was invisible until the data was sorted the way a user would.
After any parser or scrape change, actually look:

```sh
sqlite3 -box db/franimo.db "
  SELECT type, place, dept_nl, price, living_m2, land_m2,
         ROUND(price*1.0/living_m2) eur_m2
  FROM listings WHERE living_m2 >= 80 AND price <= 100000
  ORDER BY eur_m2 LIMIT 10;"
```

If the top of that list looks absurd, something is wrong with the *data*, not the
sort. Past examples: land plots echoing their parcel size into the living-area field
(2,069 rows), Beaujolais vineyards listed as `huis` with 18,176 m² of "living space".
Both only showed up at the top of the €/m² ranking.

## Don't "fix" prices

`parse.py` cleans implausible **areas** (see README → Data cleaning). It deliberately
leaves **prices** exactly as franimo states them, even absurd ones — e.g. a handful of
Côte d'Azur listings whose agent reference ends in `L` (*location*, rental) and whose
price is a weekly rate. Hiding listings that merely look too good defeats the point of
a bargain-finder. Surface source errors; don't silently filter them.

## Changing the page

`franimo/web/{index.html,app.js,style.css,france.js}` are the sources.
`core/export.py` copies them to the repo root — edit the originals in
`franimo/web/`, never the copies. `python3 -m franimo.export` is a wrapper.

`app.js` runs against both the local API and the static export (`window.FRANIMO_STATIC`).
The static build ships packed JSON (columnar + dictionary-coded); `unpack()` in
`app.js` must stay in step with `pack()` in `core/export.py`. A listing with no
`source` column (older exports) is treated as franimo.

Derived fields (`eur_m2`, `eur_m2_land`, `price_drop`, `days_known`) are computed in
the browser, not stored, so a published build doesn't go stale.

## Adding a source adapter (Phase 1 — not this PR)

Next adapters: `ok_bulgaria`, `akiyaportal`. Each is its own package that:

* implements `core.adapter.SourceAdapter` (`parse_list` / `parse_detail`) and
  `register()`s itself
* writes listings through `core.db.upsert_from_list` with `source` +
  `external_id` set (never reuse another portal's integer `id`)
* is selected by `"source"` on a searches.json entry
* uses `core.http.Fetcher(base=..., gap=...)` so cache keys stay sha1(full URL)

Do not scrape those sites until that PR. Do not invent a second database.

## Regenerating the locator map

Only needed if the outline changes. Source data is not vendored; `france.js` is
committed:

```sh
python3 -m franimo.make_map path/to/departements-version-simplifiee.geojson
```
