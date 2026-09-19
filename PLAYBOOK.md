# When-you're-home scrape playbook

Home IP is available. Holprop will Cloudflare-challenge a datacenter; the
other hosts are kinder. One host at a time — the rate limit is process-wide,
so two scrapers at the same site double the request rate. Do not export until
the scrapes you want are in `db/franimo.db`.

`README.md` is the reference; `CLAUDE.md` is the traps. This file is the
checklist.

## Order

1. franimo — optional refresh (France is already in the published build)
2. OK Bulgaria `bg-houses-50k`
3. Akiya Portal `jp-houses-10k`
4. Holprop `hp-es-houses-100k` (enable BG / PT / GR / IT if you want them)
5. both Abruzzo adapters, full €100k (`api-houses-100k`, `arp-houses-100k`)
6. Centrarium `ct-me-houses-100k` (5s crawl-delay; ~43 houses)
7. Mubawab `mw-ma-houses-100k` (MAD `:pr:0-1100000`; ~375 houses)
8. home.ge `hg-ge-houses-100k` (EUR GET filter; ~84 houses)
9. **only then** export, and only when you want Pages updated

## Commands

```sh
# 1. franimo — skip this block if France is fresh enough
python3 -m franimo.scrape --no-details
python3 -m franimo.scrape --detail-limit 200          # next run resumes

# 2. OK Bulgaria
python3 -m ok_bulgaria.scrape bg-houses-50k --no-details
python3 -m ok_bulgaria.scrape bg-houses-50k --detail-limit 200

# 3. Akiya Portal
python3 -m akiyaportal.scrape jp-houses-10k --no-details
python3 -m akiyaportal.scrape jp-houses-10k --detail-limit 200

# 4. Holprop — home IP only. Datacenter gets Cloudflare.
python3 -m holprop.scrape hp-es-houses-100k --no-details
python3 -m holprop.scrape hp-es-houses-100k            # 68 listings; finish details
# parked countries, by name (or flip "enabled" in searches.json first):
# python3 -m holprop.scrape hp-bg-houses-100k --no-details
# python3 -m holprop.scrape hp-pt-houses-100k --no-details
# python3 -m holprop.scrape hp-gr-houses-100k --no-details
# python3 -m holprop.scrape hp-it-houses-100k --no-details

# 5. Abruzzo — both agencies, full 100k (small catalogues; omit --detail-limit)
python3 -m abruzzopropertyitaly.scrape api-houses-100k --no-details
python3 -m abruzzopropertyitaly.scrape api-houses-100k
python3 -m abruzzoruralproperty.scrape arp-houses-100k --no-details
python3 -m abruzzoruralproperty.scrape arp-houses-100k

# 6. Centrarium — robots.txt Crawl-delay: 5. ~43 houses under €100k.
python3 -m centrarium.scrape ct-me-houses-100k --no-details
python3 -m centrarium.scrape ct-me-houses-100k --detail-limit 20

# 7. Mubawab — MAD :pr:0-1100000 (~375 / 12 pages). Titled urban/peri-urban only.
python3 -m mubawab.scrape mw-ma-houses-100k --no-details
python3 -m mubawab.scrape mw-ma-houses-100k --detail-limit 20

# 8. home.ge — EUR GET filter (~84 / 2 pages). Non-ag freehold only.
python3 -m homege.scrape hg-ge-houses-100k --no-details
python3 -m homege.scrape hg-ge-houses-100k
```

Look before you publish:

```sh
python3 -m franimo.serve            # http://localhost:8765 — bron facet, all sources
```

## Flags that matter

**`--no-details` first** on anything large. List pages make the UI usable in
minutes; detail backfill is the long pole. **`--detail-limit N`** caps a run
and the next run resumes, cheapest-first. Drop the cap on the Abruzzo 100k
searches — they are hundreds of rows, not thousands. Centrarium's enabled
seed is also small (~43); keep the 5s gap. Mubawab's enabled seed is
~375 / 12 pages; `PRICE_ASC` does not actually sort, so do not stop
early — finish the `:pr:` band. home.ge's enabled seed is ~84 / 2
pages; drop the cap. First GET can bounce on a session cookie — the
Fetcher retries.

**Never delete `cache/`.** Every fetched page is gzipped, keyed by sha1(full
URL). `--redetail` re-parses from disk with zero requests. Do not move cache
files into per-source subdirectories.

**`--db` for experiments.** Default is `db/franimo.db`. A trial that might
write junk:

```sh
python3 -m holprop.scrape hp-es-houses-100k --no-details --db db/scratch.db
python3 -m franimo.serve --db db/scratch.db
python3 -m franimo.export --db db/scratch.db --out /tmp/bh-preview
```

**Do not overwrite published `data/` until you mean to.** `python3 -m
franimo.export` writes `index.html` + `data/*.json` at the repo root (what
Pages serves). Use `--out` for a local preview. A one-source experiment
must not become the published payload.

## Combined export

The UI is already multi-source: source badge + **bron** facet. One export
packs every row in the database — franimo, Bulgaria, Japan, Holprop, both
Abruzzo agencies, Centrarium, Mubawab, home.ge. There is no per-source publish step.

Export when you want https://mipolai.com/bargainhunter/ updated, not after
every scrape:

```sh
python3 -m franimo.export
git add -A && git commit -m "data refresh" && git push   # Pages rebuilds in ~1 min
```

`data/` is the published JSON. The database is `db/franimo.db`.

## Parked searches you may enable

`"enabled": false` is skipped by a bare `python3 -m <pkg>.scrape`. Naming
the search still runs it. Flip `"enabled": true` in `searches.json` if you
want it in the default pass.

| name | source | why it's parked |
|---|---|---|
| `bg-houses-100k` / `bg-houses-150k` | ok_bulgaria | wider GBP bands |
| `jp-houses-25k` / `jp-houses-50k` | akiyaportal | wider USD bands (20k / 36k listings) |
| `hp-bg-houses-100k` | holprop | 1,670 houses — volume |
| `hp-pt-houses-100k` / `hp-gr-houses-100k` / `hp-it-houses-100k` | holprop | other countries, same €100k house filter |
| `api-houses-150k` | abruzzopropertyitaly | wider EUR band (196 listings) |
| `arp-houses-150k` | abruzzoruralproperty | wider EUR band |
| `ct-me-houses` | centrarium | all 826 Montenegro houses, no price skip |
| `mw-ma-houses-150k` | mubawab | wider MAD band (1.6M DH / 701 listings) |
| `mw-ma-houses` | mubawab | all 2,017 Morocco houses, no price filter |
| `hg-ge-houses-150k` | homege | wider EUR band (136 listings) |
| `hg-ge-houses` | homege | all-price house-for-sale category (7 pages) |

```sh
python3 -m ok_bulgaria.scrape bg-houses-100k --no-details
python3 -m akiyaportal.scrape jp-houses-25k --no-details
python3 -m holprop.scrape hp-bg-houses-100k --no-details
python3 -m abruzzopropertyitaly.scrape api-houses-150k --no-details
python3 -m abruzzoruralproperty.scrape arp-houses-150k --no-details
python3 -m centrarium.scrape ct-me-houses --no-details
python3 -m mubawab.scrape mw-ma-houses-150k --no-details
python3 -m mubawab.scrape mw-ma-houses --no-details
python3 -m homege.scrape hg-ge-houses-150k --no-details
python3 -m homege.scrape hg-ge-houses --no-details
```

`jp-akita` and `api-houses-50k` are also parked (regional / narrower). Same
rule: run by name.

After any scrape, look at the cheap end of `eur_m2` in sqlite — see
`CLAUDE.md` — before you trust the run.
