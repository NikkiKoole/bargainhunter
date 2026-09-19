# Working on bargainhunter

Scrapes property portals into SQLite and publishes a static browser at
https://mipolai.com/bargainhunter/. Live sources: franimo.nl, OK Bulgaria,
Akiya Portal, Holprop, Abruzzo Property Italy, and Abruzzo Rural Property.
Shared HTTP/DB/export live
in `core/` so adapters plug in without rewriting that path. `README.md` is the
reference; this file is the order of operations and the things that are easy
to get wrong. Home-IP checklist (Holprop cannot run from a datacenter):
`PLAYBOOK.md`.

Stdlib + `requests`/`beautifulsoup4`/`lxml` only. No build step, no framework.

## Refreshing the data — the whole sequence

```sh
python3 -m franimo.scrape                 # 1a. franimo list + detail pages
python3 -m ok_bulgaria.scrape             # 1b. OK Bulgaria (separate process; do not parallelise hosts)
python3 -m akiyaportal.scrape             # 1c. Akiya Portal (separate process)
python3 -m holprop.scrape                 # 1d. Holprop — home IP only (Cloudflare)
python3 -m abruzzopropertyitaly.scrape    # 1e. Abruzzo Property Italy (separate process)
python3 -m abruzzoruralproperty.scrape    # 1f. Abruzzo Rural Property (separate process)
python3 -m franimo.export                 # 2. only when you want Pages updated
git add -A && git commit -m "data refresh" && git push   # 3. Pages rebuilds in ~1 min
```

Franimo is optional if France is already fresh. Export last — one combined
`data/` for every source (bron facet). Full home-IP order, `--no-details`
first, and the parked searches you may flip on: `PLAYBOOK.md`.

Run `python3 -m franimo.serve` (http://localhost:8765) to check it locally first.
Pages serves from `main` at the repo root, so the site files must stay at the root.

## Rules that matter

**One scraper at a time per host.** The rate limit (`MIN_GAP` in `core.http`)
is a process-wide lock, so two processes double the request rate at the host.
Chain runs; don't parallelise them. Franimo's CLI defaults to 0.3s / 4 workers;
OK Bulgaria, Akiya Portal, Holprop, Abruzzo Property Italy and
Abruzzo Rural Property default to 0.6s / 1 worker.

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
not assume it equals the portal id once a second source exists. Point
experiments at `--db db/scratch.db` so a trial cannot trash the real file.

**`searches.json` `"source"`** selects the adapter. Omitted source means
`franimo`. `python3 -m franimo.scrape` only runs franimo searches;
`python3 -m ok_bulgaria.scrape` only runs `ok_bulgaria` (e.g. `bg-houses-50k`);
`python3 -m akiyaportal.scrape` only runs `akiyaportal` (e.g. `jp-houses-10k`);
`python3 -m holprop.scrape` only runs `holprop` (e.g. `hp-es-houses-100k`);
`python3 -m abruzzopropertyitaly.scrape` only runs `abruzzopropertyitaly`
(e.g. `api-houses-100k`);
`python3 -m abruzzoruralproperty.scrape` only runs `abruzzoruralproperty`
(e.g. `arp-houses-100k`).

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

sqlite3 -box db/franimo.db "
  SELECT source, external_id, type, place, price, currency, land_m2
  FROM listings WHERE source='ok_bulgaria' ORDER BY price LIMIT 10;"

sqlite3 -box db/franimo.db "
  SELECT source, external_id, place, region, price, currency, living_m2, land_m2
  FROM listings WHERE source='akiyaportal' ORDER BY price LIMIT 10;"

sqlite3 -box db/franimo.db "
  SELECT source, external_id, place, region, price, currency, living_m2, land_m2
  FROM listings WHERE source='holprop' ORDER BY price LIMIT 10;"

sqlite3 -box db/franimo.db "
  SELECT source, external_id, place, region, price, currency, living_m2, land_m2
  FROM listings WHERE source='abruzzopropertyitaly' ORDER BY price LIMIT 10;"

sqlite3 -box db/franimo.db "
  SELECT source, external_id, place, region, price, currency, living_m2, land_m2
  FROM listings WHERE source='abruzzoruralproperty' ORDER BY price LIMIT 10;"
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

## Adding a source adapter

`ok_bulgaria`, `akiyaportal`, `holprop`, `abruzzopropertyitaly` and
`abruzzoruralproperty` are in. Do not start Centrarium or Mubawab here.
Each adapter is its own package that:

* implements `core.adapter.SourceAdapter` (`parse_list` / `parse_detail`) and
  `register()`s itself
* writes listings through `core.db.upsert_from_list` with `source` +
  `external_id` set (never reuse another portal's integer `id`)
* is selected by `"source"` on a searches.json entry
* uses `core.http.Fetcher(base=..., gap=...)` so cache keys stay sha1(full URL)
* has its own `python3 -m <pkg>.scrape` CLI (franimo will refuse foreign names)

Do not invent a second database. Do not overwrite published `data/` with a
tiny one-source export; use `--out` if you need a local static build.

OK Bulgaria selling prices are euro (portal copy); GBP is stored in
`raw_fields`. See `ok_bulgaria/fx.py` if a card has pounds only.

Akiya Portal displays USD (sometimes yen in titles). We convert to EUR with
the documented fixed rates in `akiyaportal/fx.py` (0.85 EUR/USD, 170 JPY/EUR;
not a live ECB feed) and keep the original in `raw_fields`. The seed search
is `/listings?max_price=10000` — the site's own Under $10k bucket, verified
live — not the 2,234-page unfiltered index.

Holprop displays EUR (GBP/USD sit next to it on detail pages). We store
`price` + `currency=EUR` and keep the extras in `raw_fields`. Searches are
per-country house filters
(`/sale/pt/villa-house/scr/{country}/price/100000/`). Spain is the enabled
seed (68 listings); Bulgaria / Portugal / Greece / Italy are parked. Italy
has a usable house+price URL (319 under €100k) — not a skip. Detail ids are
`bg62388419`-style; keep `?ctype=EUR` on the stored URL.

Abruzzo Property Italy displays EUR. The search URL is a tilde path
(`/property-search~for=1,minprice=0,maxprice=100000,order=priceasc,do=search`)
— a `?minprice=` query string is ignored. Verified 2026-09-19: 155 listings
under €100k (4 pages × 50). `type=1` returns zero rows, so the seed is the
price bucket, not a house-only filter. The site's pager drops the price
filter; keep minprice/maxprice and set `page=` (0-based; page 1 omits it).
Detail ids are numeric pids (`pid=3223`). A leftover "PCM" label on sale
pages is ignored. 0.00 SQM means not stated.

Abruzzo Rural Property displays EUR. The for-sale list paginates with
`start=0,6,12,…` (6 per page). A GET `search[price_from]` /
`search[price_to]` slider works — the seed is
`/find-a-property/for-sale?search[price_from]=0&search[price_to]=100000&fwrealestate_update_search=1`
(verified 2026-09-19: End start=342, ~345 cards including sold/under
offer). Detail ids are numeric CMS ids (`/item/1792-…`). Agency refs
(FL4245) are `reference`. Site-wide geo.position is the San Salvo
office — do not store it as listing lat/lon. Complements
abruzzopropertyitaly (different agency stock).

## Regenerating the locator map

Only needed if the outline changes. Source data is not vendored; `france.js` is
committed:

```sh
python3 -m franimo.make_map path/to/departements-version-simplifiee.geojson
```
