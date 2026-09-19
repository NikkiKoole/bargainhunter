# bargainhunter

A better way to browse cheap property listings. Today that means
[franimo.nl](https://www.franimo.nl) (Dutch portal for French property) and
[OK Bulgaria](https://www.cheap-bulgarian-house.co.uk/) (UK-facing Bulgarian
houses). The scrape → SQLite → static page pipeline is shared; each portal
is a `core.adapter` package. Scrapes saved searches into one database and
serves a single dense page you can sort, filter and map.

## What it does that the site doesn't

* **€/m² and €/m² grond**, computed and sortable. The site never shows these, and they are
  the fastest way to spot a bargain in a list sorted only by asking price.
* **One page, all results.** No pagination. Sort by any column.
* **Real filters**: land size, living area, bedrooms, energy label, and free-text search
  across the full description and feature list (`schuur`, `bron`, `vijver`, `te renoveren`…).
  Franimo's own filters can't touch most of this.
* **Price history and new/gone tracking.** Every run records price changes, so
  "what got cheaper" and "what's new since last week" are queries, not memory.
* **Map view** of whatever your filters currently select, with thumbnails in the pins.
* **A locator map on every listing**, showing where in France it is with its
  departement highlighted — the point of searching the whole country at once.
* **Several areas in one database**, filtered by the "gebied" facet. A listing found by
  more than one search is stored once and belongs to both.

Working on this with an agent? `CLAUDE.md` has the order of operations and the
traps; this file is the reference.

## Setup

```sh
pip install -r requirements.txt
```

## Use

```sh
python3 -m franimo.scrape          # enabled franimo searches
python3 -m ok_bulgaria.scrape      # enabled OK Bulgaria searches
python3 -m franimo.serve           # open http://localhost:8765
```

Each CLI only runs searches for its own `"source"`. A bare
`python3 -m franimo.scrape` will not touch Bulgaria.

Re-running the scraper is cheap and safe. Fetched pages are cached in `cache/`, so a
second run re-parses from disk instead of hitting the site, and every DB write is an
upsert — nothing is duplicated and nothing is lost.

### Scraper flags

| flag | what it does |
|---|---|
| `--no-details` | list pages only. Fast; gets price/place/type/beds but no m², land or description |
| `--detail-limit N` | fetch at most N detail pages this run. Resumable — cheapest first, next run continues |
| `--refresh` | ignore the HTML cache and re-download |
| `--max-age N` | treat cached pages older than N days as stale |
| `--redetail` | re-parse every detail page (from cache, so no network) — use after changing the parser |
| `--max-pages N` | stop after N result pages, for quick trials |
| `--gap S` / `--workers N` | request pacing. Default 0.3s between requests, 4 workers |

Typical flow for a big search: `--no-details` first so the UI is usable, then let the
detail backfill run in the background.

## Publishing a static copy

```sh
python3 -m franimo.scrape        # refresh the data
python3 -m franimo.export        # rebuild index.html + data/
git add -A && git commit -m "data refresh" && git push
```

The published site is `index.html`, `app.js`, `style.css` and `data/*.json` at
the repo root — the same UI reading packed JSON instead of the local API, with
no Python in it. GitHub Pages serves it from `main` at root; it also works from
a `file://` path or any static host. Re-run `export` after every scrape.

The database lives in `db/` (gitignored) so it doesn't collide with the
published `data/`.

The payload is packed rather than dumped, because a plain dump of 10k listings
is 14.7MB:

| | raw | over the wire |
|---|---|---|
| `listings.json` | 6.2 MB | 1.5 MB |
| `photos.json` | 2.8 MB | 0.4 MB |

How: columnar rows (30 key names aren't repeated 10,129 times), dictionary
coding for columns whose values repeat (`type`, `dept_nl`, `agent`, and the
timestamp columns, which hold one value per scrape run), a prefix dictionary for
image paths (the CDN prefix alone is ~50 characters), and derived fields
(`eur_m2`, `price_drop`, `days_known`) computed in the browser instead of
shipped. `days_known` is therefore correct however old the export is.

Each rebuild adds roughly its compressed size to git history and that never
shrinks — a handful of re-scrapes is fine, hundreds would not be.

## Mobile

The page is usable on a phone: filters slide in over the content behind a ☰
button, the card grid is the default view (a 12-column table is not a phone
layout, though it is still there and scrolls sideways), and the detail panel
goes full width. The filter panel is offset by the header's measured height
rather than a hard-coded value, since the header wraps to two lines when narrow.

## Adding an area

```sh
# how big would this be? (asks franimo, scrapes nothing)
python3 -m franimo.newsearch --count-only --lat 45.8992 --lon 6.1294 --km 300
python3 -m franimo.newsearch --count-only --france --price-to 100000

# add it
python3 -m franimo.newsearch --name annecy-300 --lat 45.8992 --lon 6.1294 --km 300
python3 -m franimo.newsearch --name france-100k --france --price-to 100000
python3 -m franimo.scrape annecy-300 --no-details   # list pages: fast, UI usable
python3 -m franimo.scrape annecy-300                # then backfill the details
```

`--types 3,7,12` restricts the property types; the default is everything except
appartement, nieuwbouw and kantoor. Always `--count-only` first — radius scales
area, so 300km is roughly four times the listings of 150km.

`--france` drops the radius and searches the whole country; `--price-to` caps the
asking price. Scrape the widest price band you might want and narrow it in the UI —
re-filtering is instant, re-scraping is not.

Run one scraper at a time. The rate limit is per process, so two at once doubles
the request rate at the site.

## Searches

`searches.json` holds named searches. Each entry has a `"source"` (the adapter
to run). If you omit it, the loader treats the search as `"franimo"` so older
files keep working. To add a franimo search: run it on franimo.nl, copy the
URL, and paste its path in:

```json
{
  "my-search": {
    "source": "franimo",
    "label": "shown in the scraper output",
    "path": "/woning/?pricefrom=0&...&submitted=true&orderby=price%20asc"
  }
}
```

Then `python3 -m franimo.scrape my-search`. Listings remember which searches found them
(`listing_search`), so overlapping searches don't fight each other.

The current set:

| name | area | listings | |
|---|---|---|---|
| `france-150k` | **all of France up to €150k**, 30 types | ~10.4k | active |
| `boerderijen-oost` | the original: boerderij, herenhuis, dorpsboerderij, bar-café, hotel | ~212 | active |
| `bg-houses-50k` | **OK Bulgaria houses £0–50k** | ~1.3k | active |
| `bg-houses-100k` / `bg-houses-150k` | same site, wider GBP bands |  | parked |
| `breed-oost` | 240km around the Ardennes | ~6.8k | parked |
| `annecy-300` | 300km around Annecy | ~14.2k | parked |
| `morvan-60` | 60km around Château-Chinon/Saulieu | ~574 | parked |

`france-150k` covers every area search, so the radius ones are parked with
`"enabled": false`: `python3 -m franimo.scrape` skips them, but naming one
explicitly still runs it. Nothing already scraped is lost — filter by region or
department in the UI instead.

Searches overlap; listings are deduplicated by `(source, external_id)`, and a
detail page already fetched for one search is never fetched again for another.

## OK Bulgaria

[cheap-bulgarian-house.co.uk](https://www.cheap-bulgarian-house.co.uk/) is a
classic PHP listing site (OK Bulgaria). Public list/detail pages only;
`robots.txt` disallows `/admin_files/`, `/_db_backups/`, `/images/`, `/support/`.

```sh
python3 -m ok_bulgaria.scrape bg-houses-50k --no-details --max-pages 1   # trial
python3 -m ok_bulgaria.scrape bg-houses-50k --no-details                 # all list pages
python3 -m ok_bulgaria.scrape bg-houses-50k --detail-limit 50            # then details
```

Same flags as franimo (`--refresh`, `--redetail`, `--gap`, `--workers`). Default
gap is 0.6s and default workers is 1 — be kind; do not run this in parallel
with another scrape of the same host.

The site's own copy says the **selling price is euro**; the pound figure is
"for reference only and can vary daily." We store `price` + `currency=EUR` as
the portal displays them, and keep the £ amount in `raw_fields`. If a card
has only pounds, we convert with a documented fixed rate (`ok_bulgaria/fx.py`,
≈1.166 EUR/GBP, matching their dual display — not a live ECB feed).

List pagination on the site uses a PHP session (`?page=N&use_session=yes`).
The scraper never follows that URL: it keeps the search query (`low_price` /
`high_price`) and sets `page=` itself, so the HTML cache stays a function of
the full URL.

## Multi-source layout

Shared infrastructure lives in `core/`. `franimo/` is the franimo.nl adapter;
`ok_bulgaria/` scrapes cheap-bulgarian-house.co.uk. Each CLI only runs its
own source. `akiyaportal` is not in this tree yet.

Listings are stored under `(source, external_id)` so two portals cannot
collide on the same numeric id. The integer `id` is an internal key (UI,
photos, price history). Existing franimo rows keep their current id; a
migration fills `source='franimo'` and `external_id` from it.

```
core/http.py        polite fetching + gzip HTML cache + MIN_GAP
core/db.py          schema, upserts, price history, source migration
core/listing.py     normalized fields (price, currency, m², place, photos, url, …)
core/searches.py    searches.json loader (`source` defaults to franimo)
core/export.py      SQLite → packed JSON + static UI at repo root
core/serve.py       localhost UI + JSON API
core/adapter.py     SourceAdapter protocol / registry
ok_bulgaria/        cheap-bulgarian-house.co.uk adapter + CLI
franimo/parse.py    franimo list-page and detail-page parsers
franimo/scrape.py   franimo CLI (only runs source=franimo searches)
franimo/newsearch.py  add a franimo radius search / size it up first
franimo/prune.py      drop listings outside the price range you care about
franimo/make_map.py   regenerate web/france.js (the locator map outline)
franimo/web/        the page itself — edit these, never the root copies
db/franimo.db       the database (gitignored)
index.html          the published page (written by export)
data/*.json         the published data (written by export)
```

The UI shows a source badge and a "bron" facet. With only franimo data that
is a single value; it starts to matter once Phase 1 adapters land.

## Pruning

Widening a search leaves rows behind that no longer interest you. `prune` drops them:

```sh
python3 -m franimo.prune --dry-run           # what would go, broken down by search
python3 -m franimo.prune --max-price 150000  # takes data/franimo.db.bak first
```

Listings with no price ("prijs op aanvraag") are **kept** by default — they match
every price filter precisely because they have no price, so pruning them silently
would be the wrong call. `--drop-priceless` removes them.

To stop a re-scrape from re-inserting what you pruned, record the policy on the
search itself — the scraper then refuses those listings at ingest:

```json
"france-150k": { "path": "...", "skip": { "priceless": true, "above": 150000 } }
```

Pruning is safe to undo: the scraped HTML stays in `cache/`, so re-running the
search rebuilds the rows without touching the network.

## franimo's pagination ceiling

franimo stops paginating at roughly **714 pages (~10,000 results)** — page 715 of a
bigger search redirects to an error page, so the tail is simply unreachable by paging.

`newsearch` checks for this and splits the search into price bands automatically,
recording them in `searches.json`:

```json
"france-150k": { "path": "/woning/?...", "bands": [[0, 75000], [75001, 150000]] }
```

The scraper crawls each band as its own pass and deduplicates by listing id, so the
bands are invisible in the results. If you hand-write a search that turns out to be
too big, the scraper warns at the ceiling instead of silently truncating.

## Data cleaning

€/m² is only as good as the m². franimo's "woning" field is agent-entered and
sometimes holds a parcel size instead of a floor area, which put land plots and
vineyards at the top of the cheapest-per-m² list. `parse.py` clears the living
area when:

* the type is `terrein` or `bouwgrond` — a plot has no living area (2,069 rows);
* the value exceeds 2,000 m² for a type that can't plausibly be that big
  (châteaux, hotels and the like are exempt via `BIG_TYPES`);
* the record has no rooms, no bedrooms and no plot but hundreds of m² of
  "woning" — that's a parcel record, not a home.

Land still gets €/m² grond, which is the right metric for it.

These run at parse time, so `--redetail` re-applies them to the whole database
from `cache/` without a single request.

**What is deliberately not cleaned:** listings whose *price* is implausible. A
Cannes house at €7,000 with 6 rooms is a complete record with a wrong price at
the source. Hiding listings that merely look too good is the opposite of what a
bargain-finder should do, so the tool shows franimo's data faithfully —
including where franimo is wrong.

## The locator map

`franimo/web/france.js` holds 96 departement outlines as SVG paths, simplified
with Ramer-Douglas-Peucker to ~7,500 points (90KB, 25KB gzipped, fetched once).
It is generated from [france-geojson](https://github.com/gregoiredavid/france-geojson)
(IGN data, Licence Ouverte) and committed, so no build step or GIS dependency is
needed:

```sh
python3 -m franimo.make_map path/to/departements-version-simplifiee.geojson
```

Listings are matched to a departement on an accent-folded name with any
parenthetical removed, so "Ardeche" finds "Ardèche" and "Paris (Seine)" finds
"Paris". The 130 overseas listings (Guadeloupe, Réunion, Guyane...) fall outside
the frame and say so rather than showing a misleading pin.

## Notes

* Coordinates come from franimo rounded to a ~0.05° grid (≈5km), so map pins show the
  area, not the house.
* `raw_fields` keeps every row of the "gegevens woning" table verbatim, so a listing type
  with fields we don't model yet still has its data in the DB.
* Energy labels exist for roughly 60% of listings; rural renovation projects often have no
  diagnostic on file.
* Cached pages are gzipped (~60KB of HTML becomes ~9KB), which matters once the cache
  runs into tens of thousands of pages. `python3 -m franimo.compact` converts any
  uncompressed leftovers.
* Be kind to the site: the default pacing is ~3 requests/second and the cache means you
  only pay for pages you haven't seen.
