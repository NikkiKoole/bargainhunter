# Home-machine agent handoff

You are on Nikki's laptop (home IP). This is the session that can
touch Holprop and the real `db/` + `cache/`. A Cursor datacenter /
cloud VM cannot do that reliably.

Read `CLAUDE.md`, `PLAYBOOK.md`, then this file. Commands live in
`PLAYBOOK.md` — do not copy them here. This file is what is home-only
and how to know you are done.

## Why home IP matters

**Must be home IP** (datacenter GET is Cloudflare 403):

* Holprop (`holprop.com`) — enabled seed `hp-es-houses-100k`. Confirmed
  working from home on 2026-09-20.

A failed fetch looks like `failed to fetch … 403` / Forbidden; Holprop
prints `! stopping:`. Stop. Do not retry that host from a datacenter.

> **Le Figaro is blocked everywhere, and a home IP does not help.** Verified
> 2026-09-20 from Nikki's laptop: `requests` and `curl` get 403 on every path
> including the site root, with a full browser header set. Real Chrome on the
> same machine and the same IP loads the page normally. That makes it TLS
> fingerprint bot detection, not an IP block. All `lf-*` seeds are parked. Do
> not spend a session "trying from home" — that experiment is done. Getting it
> would need a TLS-impersonating client or a real browser, which is a product
> decision, not a retry.


**Generally OK from a datacenter**, but a full production scrape +
export belongs here, on this machine, against the real `db/franimo.db`
and the existing `cache/`:

* Green-Acres (listing GET works; crawl-delay 1s)
* Abruzzo Property Italy, Abruzzo Rural Property
* OK Bulgaria, Akiya Portal, Centrarium (5s crawl-delay)
* Mubawab, home.ge, Bulgarian Properties, Domaza, franimo

Datacenter HTML can still paint the wrong currency (Green-Acres `$`
when `VisitorCountry=us`). The parsers convert; do not "fix" prices.

## Production scrape sequence

`PLAYBOOK.md` is the ordered command list. Home-only emphasis:

1. **One host at a time.** `MIN_GAP` in `core.http` is process-wide.
   Two scrapers at the same site double the request rate. Chain them.
2. **`--no-details` first** on anything large, then detail backfill.
   `--detail-limit N` resumes cheapest-first. Drop the cap on the
   small Abruzzo / home.ge / Bulgarian Properties seeds.
3. **Never delete `cache/`.** Pages are gzipped, keyed by sha1(full
   URL). `--redetail` re-parses from disk with zero requests. Do not
   move cache files into per-source subdirectories.
4. **Real `db/` for production.** Default is `db/franimo.db`. Use
   `--db db/scratch.db` only for experiments. Do not point a trial
   at the production file.
5. **Holprop must run on this IP.** Confirm it first (quick start
   below). Le Figaro is parked and unreachable from any IP.
6. **Export last, and only when you mean to update Pages.**
   `python3 -m franimo.export` writes `data/` + root static files
   (`index.html`, `app.js`, `style.css`). Those packs are large.
   Commit/push carefully. Use `--out /tmp/bh-preview` for a trial.

Franimo is optional if France is already fresh.

## Where this stands — 2026-09-20

Last full home run, including the akiyaportal detail backfill. Published to
https://mipolai.com/bargainhunter/ the same day. 17,970 listings, 8 countries,
every listing detail-fetched except Green-Acres, which has never run.

| source | land | rijen | details | met m² |
|---|---|---|---|---|
| `franimo` | FR | 10,129 | 10,129 | 63% |
| `akiyaportal` | JP | 5,577 | 5,577 | 98% |
| `ok_bulgaria` | BG | 1,300 | 1,300 | 25% |
| `mubawab` | MA | 355 | 355 | 100% |
| `abruzzoruralproperty` | IT | 272 | 272 | 6% |
| `abruzzopropertyitaly` | IT | 155 | 155 | 89% |
| `homege` | GE | 71 | 71 | 95% |
| `centrarium` | ME | 43 | 43 | 100% |
| `bulgarianproperties` | BG | 37 | 37 | 100% |
| `holprop` | ES | 20 | 20 | 90% |
| `domaza` | ME | 11 | 11 | 100% |

**Unfinished, in priority order:**

1. **Green-Acres has never been run here** (`ga-fr-houses-150k`, ~3,435 over 144
   list pages, 1s crawl-delay). Deliberately skipped — it overlaps franimo on
   France.
2. **`abruzzoruralproperty` living area: 6%.** Not a parser bug. That portal has
   no living-area field at all; the number is in prose in 86% of descriptions,
   mixed with cadastral and land areas ("800 sqm of land", "cadastral area of
   116 sqm"). Extracting it means deciding which area a sentence means. Left
   alone on purpose — a wrong guess silently corrupts the €/m² ranking.

3. **akiyaportal has almost no plot size** (10 of 5,577). The portal states
   floor area but rarely land, so €/m² grond is empty for Japan. Not a parser
   bug as far as anyone has checked; nobody has looked hard.

**Known source-data quirks — do not "fix" these:**

* Akiya Portal publishes 221 listings under €100 (€54 for a 300 m² house). The
  portal's own page title says "$63". Our parser is faithful; their data is
  wrong. Sub-€1,000 rows will top any €/m² sort.
* A dozen Côte d'Azur listings from one franimo agent are rentals in a sale
  feed; the agent's own reference ends in `L` for *location*.

**Le Figaro is parked and will stay parked** until someone changes the fetch
strategy — see the box above. Do not re-test it from home; that experiment is
done.

## Enabled seeds (from `searches.json` on main)

`"enabled": false` is skipped by a bare `python3 -m <pkg>.scrape`.
Naming the search still runs it. Re-check `searches.json` if it
moved; this table is the 2026-09-20 main snapshot.

### On by default

| name | source | notes |
|---|---|---|
| `boerderijen-oost` | franimo | original Ardennen 240km; no price cap |
| `france-150k` | franimo | whole France ≤ €150k; two price bands |
| `bg-houses-50k` | ok_bulgaria | houses £0–50k; `skip.above` EUR 60000 |
| `jp-houses-10k` | akiyaportal | site Under $10k bucket; `skip.above` EUR 10000 |
| `hp-es-houses-100k` | holprop | Spain houses ≤ €100k — **home IP** |
| `api-houses-100k` | abruzzopropertyitaly | tilde search ≤ €100k |
| `arp-houses-100k` | abruzzoruralproperty | GET slider ≤ €100k |
| `ct-me-houses-100k` | centrarium | Montenegro lowprice; `skip.above` EUR 100000 |
| `mw-ma-houses-100k` | mubawab | MAD `:pr:0-1100000`; `skip.above` EUR 100000 |
| `hg-ge-houses-100k` | homege | EUR GET filter; `skip.above` EUR 100000 |
| `bp-bg-under-10k` | bulgarianproperties | static under-£10k; `skip.above` EUR 15000 |
| `dz-me-houses` | domaza | Montenegro house list; `skip.above` EUR 100000 |
| `ga-fr-houses-150k` | greenacres | NL `mx_p-150000`; `skip.above` EUR 150000 |

### Parked (`"enabled": false`)

Run by name, or flip `"enabled"` first. Why they are parked is in
`PLAYBOOK.md`.

| name | source |
|---|---|
| `breed-oost`, `annecy-300`, `morvan-60` | franimo |
| `bg-houses-100k`, `bg-houses-150k` | ok_bulgaria |
| `jp-houses-25k`, `jp-houses-50k`, `jp-akita` | akiyaportal |
| `hp-bg-houses-100k`, `hp-pt-houses-100k`, `hp-gr-houses-100k`, `hp-it-houses-100k` | holprop |
| `api-houses-50k`, `api-houses-150k` | abruzzopropertyitaly |
| `arp-houses-150k` | abruzzoruralproperty |
| `ct-me-houses` | centrarium |
| `mw-ma-houses-150k`, `mw-ma-houses` | mubawab |
| `hg-ge-houses-150k`, `hg-ge-houses` | homege |
| `bp-bg-rural-houses`, `bp-bg-houses` | bulgarianproperties |
| `dz-al-houses`, `dz-rs-houses`, `dz-ge-houses` | domaza |
| all `lf-*` (incl. `lf-23-houses-150k`) | lefigaro — Cloudflare, see above |
| `ga-fr-houses`, `ga-23-houses-150k` | greenacres |

Do not re-enable any `lf-*` seed without a new fetch strategy; the
France-wide one (`lf-france-houses`) also has a page-100 hard stop and
silent tail loss. Do not seed Domaza `/s/HASH` URLs.

## After scrape — success criteria

Look at the data the way a user would. Do not trust the run log.

**Row counts by source:**

```sh
sqlite3 -box db/franimo.db "
 SELECT source, COUNT(*) n
 FROM listings
 GROUP BY source
 ORDER BY n DESC;"
```

Expect a row for every source you just ran. Zero or a handful after a
full seed is a parser or skip-rule bug, not "the site was empty".

**€/m² sanity.** Cheap-end queries are in `CLAUDE.md`. If the top of
`ORDER BY eur_m2` looks absurd (vineyard living-m², land echoing the
parcel), the *data* is wrong. Do not "fix" prices — surface source
errors.

**gone_at is guarded.** `mark_gone` refuses to flag listings sold when a run
looks truncated (nothing seen, or under 60% of known listings while more than 5
are missing) and says so on stderr. If you see that message, the run was
incomplete — re-run it, don't force it.

**UI bron facet.** `python3 -m franimo.serve` → http://localhost:8765.
The bron facet must show friendly names (`SOURCE_NAMES` in
`franimo/web/app.js`): Franimo, OK Bulgaria, Akiya Portal, Holprop,
Abruzzo Property Italy, Abruzzo Rural, Centrarium, Mubawab, home.ge,
Bulgarian Properties, Domaza, Green-Acres. Raw keys
(`ok_bulgaria`) mean the label map drifted.

**Do not overwrite Pages until she is happy.** Local `serve` against
the real DB, or `python3 -m franimo.export --out /tmp/bh-preview`.
Only then `python3 -m franimo.export` at the repo root, commit, push.
`data/` packs are large; one combined export for every source.

## Out of scope this session

* Building more source adapters unless she asks.
* Idealista, Kyero, Leboncoin — bot walls. Do not start them.

## Quick start

```sh
# 1. read CLAUDE.md, PLAYBOOK.md, this file
pip install -r requirements.txt

# 2. prove this IP is not Cloudflare-blocked before a real Holprop run
python3 -m holprop.scrape hp-es-houses-100k --no-details --max-pages 1 --db db/scratch.db
```

A page of cards printed → home IP is good; proceed with `PLAYBOOK.md`
against the real `db/`. `403` / `failed to fetch` → stop Holprop.
Everything else can still run, but production Holprop waits until she
is actually home.
