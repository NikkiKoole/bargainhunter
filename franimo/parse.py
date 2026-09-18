"""Parsers for franimo.nl search-result pages and property detail pages.

Everything franimo renders is plain server-side HTML with schema.org microdata,
so these are straightforward soup selectors. Anything we don't model explicitly
still gets kept in `raw_fields`, so a listing type with extra rows in its
"gegevens woning" table doesn't silently lose data.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .http import BASE


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _int(text: str | None) -> int | None:
    """'€ 14.950' / '1 200 m2' / '4' -> int, else None."""
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text.replace("\xa0", " "))
    return int(digits) if digits else None


def _area(text: str | None) -> int | None:
    """'120 m2' -> 120. Strips the unit first so the '2' in 'm2' isn't read
    as part of the number."""
    if not text:
        return None
    m = re.match(r"\s*([\d.,\s\xa0]+)", text)
    return _int(m.group(1)) if m else None


def _txt(node) -> str | None:
    if node is None:
        return None
    t = node.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", t) or None


# --------------------------------------------------------------------------
# search result pages
# --------------------------------------------------------------------------

def parse_list(html: str, page_url: str) -> dict[str, Any]:
    s = _soup(html)

    total_pages = 1
    cur = s.select_one(".current-page")
    if cur:
        m = re.search(r"van\s+(\d+)", cur.get_text(" ", strip=True))
        if m:
            total_pages = int(m.group(1))

    nxt = s.select_one("ul.paging li.next a[href]")
    next_url = urljoin(page_url, nxt["href"]) if nxt else None

    listings = []
    for box in s.select("div.box1[data-id]"):
        link = box.select_one("a[itemprop=url][href]")
        price_el = box.select_one("[itemprop=price]")
        h2, h3 = box.select_one("h2"), box.select_one("h3")
        img = box.select_one(".image")

        # The card's <h2> is "<type> <place>", which splits wrongly for types
        # containing spaces ("bedrijfsruimte/ kantoor"). The microdata name is
        # "<type> te koop <place>", which is unambiguous; fall back to the h2.
        name = box.select_one('meta[itemprop=name]')
        content = (name.get("content") if name else "") or ""
        if " te koop " in content:
            ptype, place = content.split(" te koop ", 1)
        else:
            title = _txt(h2) or ""
            ptype, place = (title.split(" ", 1) + [None])[:2] if title else (None, None)

        listings.append({
            "id": int(box["data-id"]),
            "url": urljoin(BASE, link["href"]) if link else None,
            "lat": _f(box.get("data-latitude")),
            "lon": _f(box.get("data-longitude")),
            "price": _int(price_el.get("content") if price_el else None) or _int(_txt(price_el)),
            "old_price": _int(_txt(box.select_one(".old-price"))),
            "type": ptype,
            "place": place,
            "dept_nl": (_txt(h3) or "").strip("()") or None,
            "beds": _int(_txt(box.select_one(".beds"))),
            "baths": _int(_txt(box.select_one(".baths"))),
            "thumb": (img.get("data-src") if img else None),
            "snippet": _txt(box.select_one("[itemprop=description]")),
            "promoted": "top-property" in (box.get("class") or []),
        })

    return {"listings": listings, "total_pages": total_pages, "next_url": next_url}


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# detail pages
# --------------------------------------------------------------------------

# "gegevens woning" row label -> our column name
FIELD_MAP = {
    "franimo nr": "_id",
    "referentie": "reference",
    "departement": "dept_fr",
    "nabij": "place",
    "type": "type",
    "kamers": "rooms",
    "slaapkamers": "bedrooms",
    "badkamers": "baths",
    "woning": "living_m2",
    "terrein": "land_m2",
    "prijs": "price",
    "bouwjaar": "year_built",
}
NUMERIC = {"rooms", "bedrooms", "baths", "price", "year_built"}
AREAS = {"living_m2", "land_m2"}

# A plot has no living area, but franimo echoes the plot size into the "woning"
# field for these, which would otherwise make €/m² meaningless (price / plot).
LAND_TYPES = {"terrein", "bouwgrond"}

# Types that can genuinely run to thousands of m² of floor area. For anything
# else, a four-figure "woning" value is the agent having typed a parcel size
# into the wrong field (Beaujolais vineyards listed as "huis", for example).
BIG_TYPES = {"kasteel", "landgoed", "klooster", "hotel", "hotel-restaurant", "camping",
             "gebouw", "bedrijfsruimte/ kantoor", "wijngaard", "discotheek", "restaurant",
             "winkel", "gîtes/ chambres d'hôtes"}
PLAUSIBLE_LIVING_M2 = 2000


def parse_detail(html: str, url: str) -> dict[str, Any]:
    s = _soup(html)
    out: dict[str, Any] = {"url": url}
    raw: dict[str, str] = {}

    info = s.select_one(".property-information table")
    if info:
        for tr in info.select("tr"):
            cells = [_txt(c) or "" for c in tr.select("th,td")]
            if len(cells) < 2:
                continue
            label = cells[0].rstrip(":").strip().lower()
            value = cells[1]
            raw[label] = value
            key = FIELD_MAP.get(label)
            if key and key != "_id":
                if key in AREAS:
                    out[key] = _area(value)
                elif key in NUMERIC:
                    out[key] = _int(value)
                else:
                    out[key] = value

    # Drop the echoed plot size: always for land, and for anything else only
    # when the two are equal and far too large to be a real living area.
    living, land = out.get("living_m2"), out.get("land_m2")
    ptype = out.get("type")
    if ptype in LAND_TYPES:
        out["living_m2"] = None
    elif living is not None and ptype not in BIG_TYPES:
        # No rooms, no bedrooms, no plot, but hundreds of m² of "woning": that's
        # a parcel record (Beaujolais vineyards get listed this way), not a home.
        no_dwelling_fields = (out.get("rooms") is None and out.get("bedrooms") is None
                              and land is None)
        if living > PLAUSIBLE_LIVING_M2 or (no_dwelling_fields and living > 500):
            out["living_m2"] = None

    out["raw_fields"] = raw
    out["energy_label"], out["energy_kwh"] = _energy(
        s.select_one(".energy-emmission table.energy-table"))
    out["gas_label"], out["gas_co2"] = _energy(s.select_one("table.gas-emmission"))

    desc = s.select_one("[itemprop=description]")
    out["description"] = _txt(desc)

    # The comma-separated feature line is an unclassed <p> sitting between the
    # description and the "gegevens woning" block.
    features = []
    if desc:
        for sib in desc.find_next_siblings():
            if "property-information" in (sib.get("class") or []):
                break
            if sib.name == "p":
                t = _txt(sib)
                if t:
                    features.append(t)
    out["features"] = ", ".join(features) or None

    vendor = s.select_one(".property-vendor table")
    if vendor:
        v = {}
        for tr in vendor.select("tr"):
            cells = [_txt(c) or "" for c in tr.select("th,td")]
            if len(cells) >= 2:
                v[cells[0].rstrip(":").strip().lower()] = cells[1]
        out["agent"] = v.get("makelaar")
        out["agent_name"] = v.get("naam")
        out["agent_address"] = v.get("adres")

    # breadcrumb: "<type> regio Grand Est" / "<type> departement Vogezen"
    for a in s.select("a"):
        t = _txt(a) or ""
        if " regio " in t and not out.get("region"):
            out["region"] = t.split(" regio ", 1)[1]
        elif " departement " in t and not out.get("dept_nl"):
            out["dept_nl"] = t.split(" departement ", 1)[1]

    photos, seen = [], set()
    for img in s.select(".splide img"):
        src = (img.get("data-splide-lazy") or img.get("src") or "").strip()
        if src and "thumb" not in src.rsplit("/", 1)[-1] and src not in seen:
            seen.add(src)
            photos.append(src)
    out["photos"] = photos

    return out


def _energy(table) -> tuple[str | None, int | None]:
    """Franimo renders the DPE/GES scale as a 7-row table.

    A 'faded' table means no diagnostic on file (it marks row A with 'XXX' as a
    placeholder). Otherwise exactly one row carries the measured value, and that
    row's letter is the label: ['180 - 250 D', '207'] -> ('D', 207).
    """
    if table is None or "faded" in (table.get("class") or []):
        return None, None
    for tr in table.select("tr"):
        cells = [_txt(c) or "" for c in tr.select("th,td")]
        if len(cells) < 2 or not re.search(r"\d", cells[1]):
            continue
        m = re.search(r"([A-G])\s*$", cells[0])
        if m:
            return m.group(1), _int(cells[1])
    return None, None
