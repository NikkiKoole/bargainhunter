# Working on bargainhunter

Scrapes property portals into SQLite and publishes a static browser at
https://mipolai.com/bargainhunter/. Live sources: franimo.nl, OK Bulgaria,
Akiya Portal, Holprop, Abruzzo Property Italy, Abruzzo Rural Property,
Centrarium, Mubawab, home.ge, Bulgarian Properties, Domaza,
Le Figaro Immobilier, and Green-Acres.
Shared HTTP/DB/export live
in `core/` so adapters plug in without rewriting that path. `README.md` is the
reference; this file is the order of operations and the things that are easy
to get wrong. Home-IP checklist (Holprop and Le Figaro Immobilier
cannot run from a datacenter; Green-Acres listing GET works):
`PLAYBOOK.md`. Agent on the laptop (home IP, real `db/` + `cache/`):
`HOME_HANDOFF.md`.

Stdlib + `requests`/`beautifulsoup4`/`lxml` only. No build step, no framework.

## Refreshing the data — the whole sequence

```sh
python3 -m franimo.scrape                 # 1a. franimo list + detail pages
python3 -m ok_bulgaria.scrape             # 1b. OK Bulgaria (separate process; do not parallelise hosts)
python3 -m akiyaportal.scrape             # 1c. Akiya Portal (separate process)
python3 -m holprop.scrape                 # 1d. Holprop — home IP only (Cloudflare)
python3 -m abruzzopropertyitaly.scrape    # 1e. Abruzzo Property Italy (separate process)
python3 -m abruzzoruralproperty.scrape    # 1f. Abruzzo Rural Property (separate process)
python3 -m centrarium.scrape              # 1g. Centrarium (separate process; 5s crawl-delay)
python3 -m mubawab.scrape                 # 1h. Mubawab Morocco (separate process)
python3 -m homege.scrape                  # 1i. home.ge Georgia (separate process)
python3 -m bulgarianproperties.scrape     # 1j. Bulgarian Properties (separate process)
python3 -m domaza.scrape                  # 1k. Domaza (separate process)
python3 -m lefigaro.scrape                # 1l. Le Figaro Immobilier — home IP only (Cloudflare)
python3 -m greenacres.scrape              # 1m. Green-Acres (separate process; 1s crawl-delay)
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
OK Bulgaria, Akiya Portal, Holprop, Abruzzo Property Italy,
Abruzzo Rural Property, Mubawab, home.ge, Bulgarian Properties,
Domaza and Le Figaro Immobilier default to 0.6s / 1 worker. Green-Acres defaults to 1s / 1 worker
(`robots.txt` Crawl-delay: 1). Centrarium defaults to 5s / 1 worker
(`robots.txt` Crawl-delay: 5).

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
(e.g. `arp-houses-100k`);
`python3 -m centrarium.scrape` only runs `centrarium`
(e.g. `ct-me-houses-100k`);
`python3 -m mubawab.scrape` only runs `mubawab`
(e.g. `mw-ma-houses-100k`);
`python3 -m homege.scrape` only runs `homege`
(e.g. `hg-ge-houses-100k`);
`python3 -m bulgarianproperties.scrape` only runs `bulgarianproperties`
(e.g. `bp-bg-under-10k`);
`python3 -m domaza.scrape` only runs `domaza` (e.g. `dz-me-houses`);
`python3 -m lefigaro.scrape` only runs `lefigaro` (e.g. `lf-23-houses-150k`);
`python3 -m greenacres.scrape` only runs `greenacres` (e.g. `ga-fr-houses-150k`).

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

sqlite3 -box db/franimo.db "
  SELECT source, external_id, place, region, price, currency, living_m2, land_m2
  FROM listings WHERE source='centrarium' ORDER BY price LIMIT 10;"

sqlite3 -box db/franimo.db "
  SELECT source, external_id, place, region, price, currency, living_m2, land_m2
  FROM listings WHERE source='mubawab' ORDER BY price LIMIT 10;"

sqlite3 -box db/franimo.db "
  SELECT source, external_id, place, region, price, currency, living_m2, land_m2
  FROM listings WHERE source='homege' ORDER BY price LIMIT 10;"

sqlite3 -box db/franimo.db "
  SELECT source, external_id, place, region, price, currency, living_m2, land_m2
  FROM listings WHERE source='bulgarianproperties' ORDER BY price LIMIT 10;"

sqlite3 -box db/franimo.db "
  SELECT source, external_id, place, region, price, currency, living_m2, land_m2
  FROM listings WHERE source='domaza' ORDER BY price LIMIT 10;"

sqlite3 -box db/franimo.db "
  SELECT source, external_id, place, region, dept_fr, price, currency, living_m2, land_m2
  FROM listings WHERE source='lefigaro' ORDER BY price LIMIT 10;"

sqlite3 -box db/franimo.db "
  SELECT source, external_id, place, region, dept_fr, price, currency, living_m2, land_m2
  FROM listings WHERE source='greenacres' ORDER BY price LIMIT 10;"
```

If the top of that list looks absurd, something is wrong with the *data*, not the
sort. Past examples: land plots echoing their parcel size into the living-area field
(2,069 rows), Beaujolais vineyards listed as `huis` with 18,176 m² of "living space".
Both only showed up at the top of the €/m² ranking.

## The cache must survive being killed

Cache writes are atomic (temp file + `os.replace`). They were not, and killing a
scraper mid-write left truncated .gz files that failed that listing on every
later run — 19 of them accumulated in one session. An unreadable entry is now
treated as a miss and re-fetched rather than raising.

Decoding trusts the bytes over the declared charset, but only when the document
as a whole reads as UTF-8: `cheap-bulgarian-house.co.uk` declares windows-1251,
is really UTF-8, and has ~14 stray bytes in a script tag. Note that `E2 80 93`
is both a UTF-8 en-dash and the cp1251 bytes for the mojibake it turns into, so
"does it decode without error" is not the test — "how much of it is damaged" is.

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

`ok_bulgaria`, `akiyaportal`, `holprop`, `abruzzopropertyitaly`,
`abruzzoruralproperty`, `centrarium`, `mubawab`, `homege`,
`bulgarianproperties`, `domaza`, `lefigaro` and `greenacres` are in.
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

Centrarium displays EUR on the EN lowprice list (`35 000 €`; JSON-LD
`priceCurrency: EUR`). The seed is
`/en/montenegro/sale/houses/lowprice-montenegro/` — the site's cheap-first
house list, not a hard ≤€100k filter (same 826 listings as
`/en/montenegro/sale/houses/`; page 23 is multi-million). `skip.above`
is EUR 100000 (~43 cards on 2026-09-19). Detail ids are numeric
(`/en/zabljak/…-64071.html`). `robots.txt` Crawl-delay is 5s;
`Disallow: /*?page=` is an indexer rule (path `/page/2/` 404s) — follow
the site's `?page=N` pager at 5s / 1 worker. Unfiltered `/houses/` can
paint USD; prefer lowprice. Serbia / Albania lowprice URLs 404'd.

Mubawab displays MAD on most cards (`350,000 DH`; JSON-LD
`priceCurrency: MAD`) and EUR on some (`85,000 EUR`). We convert MAD
with the documented fixed rate in `mubawab/fx.py` (10.9 MAD/EUR; not a
live ECB / BAM feed) and keep the original in `raw_fields`. The seed is
`/en/sc/houses-for-sale:pr:0-1100000` — the site's own MAD colon
filter, verified live (375 houses on 2026-09-19). `?maxPrice=` and
`:mp:` are no-ops. `skip.above` is EUR 100000. Pagination is `:p:N`
(page 1 omits it). `robots.txt` `Disallow: /*:` is an indexer rule —
follow the site's pager. Morocco: titled urban/peri-urban only; never
ag land without a lawyer. Detail ids are numeric (`/en/a/8418668/…`).

home.ge displays USD on most cards (`70,000.00 $`; JSON-LD
`priceCurrency: USD`) and sometimes GEL / EUR. We convert with the
documented fixed rates in `homege/fx.py` (0.85 EUR/USD, 3.03 GEL/EUR;
not a live ECB / NBG feed) and keep the original in `raw_fields`. The
seed is the site's own GET filter
`/en/saxlebi-agarakebi/search-results.html?…&f[Category_ID]=88&f[price][to]=100000&f[price][currency]=euro`
— House For Sale ≤ €100k, verified live (84 ads / 2 pages on
2026-09-19). `skip.above` is EUR 100000. Pagination is
`/search-results/indexN.html` (page 1 omits it). First anonymous GET
is an empty 200 + session cookie (`Refresh: 0`) — Fetcher retries.
`robots.txt` Allow: /; `?sort_by=` is an indexer rule. Georgia:
foreigners can own non-agricultural freehold (houses, household
plots); agricultural land is generally restricted. Detail ids are
numeric (`…-478109.html`). Area ≥ 1000 m² with no Yard is treated as
land (cottage cards echo the parcel into Area).

Bulgarian Properties displays EUR on cards (`€ 10 900`; JSON-LD
`priceCurrency: EUR`) with a JS £/$ switcher. We store euro and keep
GBP/USD in `raw_fields`. If a card has only pounds, convert with the
documented rate in `bulgarianproperties/fx.py` (1.163 EUR/GBP — the
portal's own dual display, not a live ECB feed). The seed is the
site's static under-£10k browse
`/properties-in-bulgaria-under-ten-thousand-pounds.html` — verified
live (37 listings / 2 pages on 2026-09-19). `skip.above` is EUR 15000.
`robots.txt` Disallow `/*search`, `*minprice=`, `*maxprice=`, `*page=`
— never use `/Search/index.php` or query-string filters. Pagination is
a path suffix (`…pounds1.html` / `/indexN.html`); never `index0.html`.
Detail ids are `ADxxxxxBG`. Complements ok_bulgaria (different
inventory). Anonymous datacenter GET works.

Domaza displays USD by default on .com; a session GET of
`/ajaxfeeds/currency/currency/EUR` prints euro (portal conversion). We
store `price` + `currency=EUR` and keep `$` in `raw_fields`. Fallback
FX is the documented rate in `domaza/fx.py` (0.87 EUR/USD; not a live
ECB feed). Seeds are stable country house lists
(`/house_{country}-17-4340-{id}-0-0-0-sl/`). Montenegro is the clean
house filter (~100 / 5 pages). Albania / Serbia / Georgia are parked
— .com EN country pages often serve leftover Greece/BG cards. Opaque `/s/HASH`
filter URLs expire — do not seed them. `_pricefrom` / `?priceto=` are
ignored. Detail ids are numeric (`-17-8687837-p/`). 0.000 lat/lon
means not stated.

Le Figaro Immobilier displays EUR on FR department SEO lists
(`37 500 €`; JSON-LD `priceCurrency: EUR`). The seed is
`/annonces/immobilier-vente-maison-creuse.html` — Creuse houses,
not a price query (`?priceMax=` was unreliable). Archive 2026:
1 125 maisons / ~47 pages, mixed prices on page 1. `skip.above`
is EUR 150000. Pagination is `?page=N` and stops at page 100.
`?option=petit_prix` / `?option=travaux` are parked until a
home-IP run confirms them. Detail ids are numeric
(`/annonces/annonce-103112502.html`). Datacenter GET is
Cloudflare-blocked — home IP only, same class of wall as Holprop.
Complements franimo (domestic FR agency/particulier stock);
overlap is expected — store `source=lefigaro`, do not merge.

Green-Acres displays EUR for a European visitor and USD from a
datacenter IP. We convert with the documented rate in
`greenacres/fx.py` (0.87 EUR/USD — the portal's own dual display
`$107,417` → `€93,500`; not a live ECB feed) and keep `$` in
`raw_fields`. The seed is the NL house list
`/onroerend-goed?searchQuery=lg-nl-cn-fr-hab_house-on-mx_p-150000`
— verified 2026-09-20: `prc_max` is ignored (still 56,805 houses);
`mx_p-150000` cuts to 3,435. `skip.above` is EUR 150000. Featured /
relevance paints luxury first; the scraper paginates
`/nl/AdvertListingActions/AdvertsListing` with `order=price_i`.
`robots.txt` Crawl-delay is 1s; `Disallow: */AdvertListingActions/AdvertsListing`
and `/*currency=` are indexer rules. Detail ids are
`At19xxy8r4yirjpy`-style (`data-advertid`; URL in base64 `data-o`).
A `/makelaar/` slug is the agency-listing template, not an agency
profile. Complements franimo and lefigaro (rural/lifestyle stock);
overlap is expected — store `source=greenacres`, do not merge.

## The locator map is per country

`franimo/web/maps.js` holds outlines for 12 countries (Natural Earth, public
domain) plus France's 96 departements (france-geojson, IGN, Licence Ouverte).
Regenerate with `python3 -m franimo.make_map --world <ne_50m_admin_0_countries>
--departements <departements-version-simplifiee>`; the output is committed so
there is no build step.

Every listing has a `country` (ISO-2), resolved in `core/geo.py`: what the
portal said in `raw_fields.country` first (the only reliable answer for
multi-country portals like Holprop and Domaza), then the adapter's home country
from `SOURCE_COUNTRY`. **Add an entry to `SOURCE_COUNTRY` when you add an
adapter**, or its listings get no map.

Only France ships subdivisions, so only France highlights a departement.
A listing whose country has no outline shows its place and country as text —
drawing the wrong country is worse than drawing none.

Coordinates are sparse outside France (akiyaportal and ok_bulgaria have none),
so the pin is drawn only when lat/lon exist; the caption says "ligging bij
benadering" otherwise.

Known cosmetic limit: Lisbon falls ~2.5% of map width outside the Portuguese
outline, because simplification smooths away the Tagus estuary. Portugal has no
listings yet.

## "Sold" has to be trustworthy

`mark_gone` flags listings a search used to find and didn't this time. A run
that ends early — fetch failure, page ceiling, `--max-pages` — reaches it too,
and everything it never got to looks absent. `--max-pages 1` on Holprop used to
report 11 of 20 listings sold when none had left the site.

It now refuses when the run doesn't look complete: nothing seen at all, or fewer
than 60% of the known listings seen while more than 5 are missing. It says so on
stderr and returns 0. Ordinary churn still registers (30 of 1000, or 3 of 8).
`force=True` overrides it.

The guard lives in `core/db.py`, not in the thirteen `*/scrape.py` copies of the
crawl loop, so a new adapter inherits it.

## Verifying the UI from an automation session

A backgrounded tab (`document.hidden === true`) freezes CSS transitions and
times out `Page.captureScreenshot`. That looks exactly like a broken drawer or a
hung renderer and is neither. Check `document.visibilityState` before believing
it, and verify geometry by clearing the transition
(`el.style.transition = 'none'`) and reading `getBoundingClientRect()`.

The maps can be checked without looking at them: project a known city and call
`isPointInFill` on the country path. 11 of 12 capitals land inside; see above
for the twelfth.

