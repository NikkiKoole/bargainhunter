"""Parsers for akiyaportal.com list and detail pages.

Public EN HTML. List cards live in `#listings` (the /listings search) or
as `a.group` tiles on prefecture hub pages (`/akiya-in-{pref}`). Detail
URLs are `/listings/{slug}`. The portal's own id is the numeric
`listing_id` on the shortlist form; that is our `external_id`.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from .fx import FX_SOURCE, JPY_TO_EUR, USD_TO_EUR, jpy_to_eur, usd_to_eur
from .http import BASE

SOURCE = "akiyaportal"
PER_PAGE = 24

SLUG_RE = re.compile(r"^/listings/([A-Za-z0-9][A-Za-z0-9_-]+)$")
LAYOUT_RE = re.compile(r"(\d+)\s*(LDK|SDK|DK|LK|K|R|LD|S)\b", re.I)
AREA_RE = re.compile(r"([\d]+(?:[.,]\d+)?)\s*m[²2]", re.I)
YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
USD_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)", re.I)
JPY_RE = re.compile(
    r"(?:¥|￥)\s*([\d,]+)|([\d,]+)\s*(?:yen|JPY|円)\b",
    re.I,
)
PLACE_RE = re.compile(
    r"\b([A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+){0,3})\s+"
    r"(City|Town|Village|Ward|District)\b",
)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _int(text: str | None) -> int | None:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", str(text).replace("\xa0", " "))
    return int(digits) if digits else None


def _txt(node) -> str | None:
    if node is None:
        return None
    t = node.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", t) or None


def _area(text: str | None) -> int | None:
    """'176.88m²' / '105.3m²' → nearest int. '112.86m²5DK' still works."""
    if not text:
        return None
    m = AREA_RE.search(str(text).replace(",", ""))
    if not m:
        return None
    return int(round(float(m.group(1))))


def _layout_from_text(text: str | None) -> str | None:
    if not text:
        return None
    m = LAYOUT_RE.search(text)
    return f"{m.group(1)}{m.group(2).upper()}" if m else None


def _bedrooms_from_layout(layout: str | None) -> int | None:
    if not layout:
        return None
    m = LAYOUT_RE.search(layout)
    return int(m.group(1)) if m else None


def _year(text: str | None) -> int | None:
    if not text:
        return None
    m = YEAR_RE.search(text)
    return int(m.group(1)) if m else None


def parse_usd(text: str | None) -> int | None:
    if not text:
        return None
    m = USD_RE.search(str(text).replace("\xa0", " "))
    return _int(m.group(1)) if m else None


def parse_jpy(text: str | None) -> int | None:
    if not text:
        return None
    m = JPY_RE.search(str(text).replace("\xa0", " "))
    if not m:
        return None
    return _int(m.group(1) or m.group(2))


def priced_row(usd: int | None, jpy: int | None) -> dict[str, Any]:
    """Prefer the portal's USD display price; convert JPY only if USD is missing."""
    raw: dict[str, Any] = {}
    if usd is not None:
        raw["price_usd"] = usd
    if jpy is not None:
        raw["price_jpy"] = jpy
    if usd is not None:
        eur = usd_to_eur(usd)
        raw.update({
            "price_source": "portal_usd",
            "fx_rate": USD_TO_EUR,
            "fx_source": FX_SOURCE,
            "price_eur": eur,
        })
        return {"price": eur, "currency": "EUR", "raw_fields": raw}
    if jpy is not None:
        eur = jpy_to_eur(jpy)
        raw.update({
            "price_source": "jpy_converted",
            "fx_rate": JPY_TO_EUR,
            "fx_source": FX_SOURCE,
            "price_eur": eur,
        })
        return {"price": eur, "currency": "EUR", "raw_fields": raw}
    return {"price": None, "currency": "EUR", "raw_fields": raw}


def slug_from_href(href: str | None) -> str | None:
    if not href:
        return None
    path = urlparse(href).path.rstrip("/")
    m = SLUG_RE.match(path)
    return m.group(1) if m else None


def canonical_detail_url(slug: str) -> str:
    return f"{BASE}/listings/{slug}"


def _page_number(page_url: str) -> int:
    qs = parse_qs(urlparse(page_url).query)
    raw = (qs.get("page") or ["1"])[0]
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def with_page(page_url: str, page: int) -> str:
    """Keep the search query (max_price, prefecture, …) and set page=N.

    Page 1 on the live site omits `page=`, so we do too — cache keys stay
    a function of the URL the site actually serves.
    """
    parsed = urlparse(page_url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    if page <= 1:
        qs.pop("page", None)
    else:
        qs["page"] = [str(page)]
    query = urlencode({k: v[0] if len(v) == 1 else v for k, v in qs.items()},
                      doseq=True)
    return urlunparse(parsed._replace(query=query))


def _place_from_text(*blobs: str | None) -> str | None:
    for blob in blobs:
        if not blob:
            continue
        m = PLACE_RE.search(blob)
        if m:
            return f"{m.group(1)} {m.group(2)}"
    return None


def _listing_id(box) -> str | None:
    inp = box.select_one('input[name="listing_id"]')
    if inp and inp.get("value"):
        return str(inp["value"]).strip()
    el = box.select_one("[data-listing-id]")
    if el and el.get("data-listing-id"):
        return str(el["data-listing-id"]).strip()
    return None


def _card_row(box, page_url: str, slug: str) -> dict[str, Any] | None:
    ext = _listing_id(box) or slug
    if not ext:
        return None
    title = (_txt(box.select_one("h3"))
             or _txt(box.select_one("[aria-label]"))
             or box.get("aria-label")
             or slug.replace("-", " "))
    loc = None
    for p in box.select("p"):
        t = _txt(p)
        if t and not t.startswith("$") and "m²" not in t and t != title:
            if len(t) > 12:
                loc = t
                break
    if loc is None:
        loc = _txt(box.select_one("span.truncate"))

    usd = parse_usd(_txt(box.select_one("p.text-xl")) or _txt(box.select_one("p.text-lg"))
                    or _txt(box))
    jpy = parse_jpy(title)
    priced = priced_row(usd, jpy)

    pills = [_txt(sp) for sp in box.select("span.rounded-full")]
    pills = [p for p in pills if p]
    layout = next((p for p in pills if LAYOUT_RE.search(p)), None)
    living = next((_area(p) for p in pills if _area(p)), None)
    year = next((_year(p) for p in pills if _year(p) and "m²" not in p), None)
    # last amber pill on /listings cards is the prefecture
    prefecture = None
    for sp in box.select("span.rounded-full"):
        classes = " ".join(sp.get("class") or [])
        if "amber" in classes:
            prefecture = _txt(sp)
            break
    if not prefecture and pills:
        last = pills[-1]
        if not LAYOUT_RE.search(last) and _area(last) is None and _year(last) is None:
            prefecture = last

    src_el = box.select_one("[data-source]")
    portal_src = src_el.get("data-source") if src_el else None
    img = box.select_one("img[src]")
    thumb = urljoin(BASE + "/", img["src"]) if img and img.get("src") else None
    place = _place_from_text(title, loc)

    raw = dict(priced["raw_fields"])
    raw["slug"] = slug
    if _listing_id(box):
        raw["listing_id"] = _listing_id(box)
    if portal_src:
        raw["source_portal"] = portal_src
    if layout:
        raw["layout"] = layout
    if loc:
        raw["address"] = loc

    return {
        "source": SOURCE,
        "external_id": str(ext),
        "id": int(ext) if str(ext).isdigit() else ext,
        "url": canonical_detail_url(slug),
        "type": "Used single-family home",
        "place": place,
        "region": prefecture,
        "dept_nl": prefecture,
        "price": priced["price"],
        "currency": priced["currency"],
        "beds": _bedrooms_from_layout(layout),
        "bedrooms": _bedrooms_from_layout(layout),
        "living_m2": living,
        "year_built": year,
        "reference": str(ext),
        "thumb": thumb,
        "snippet": title,
        "promoted": bool(box.find(string=re.compile(r"^\s*New\s*$"))),
        "raw_fields": raw,
    }


def _parse_listings_grid(s: BeautifulSoup, page_url: str) -> list[dict[str, Any]]:
    grid = s.select_one("#listings")
    if not grid:
        return []
    out, seen = [], set()
    for box in grid.find_all("div", recursive=False):
        link = box.select_one('a[href*="/listings/"]')
        if not link:
            continue
        href = urljoin(page_url, link.get("href") or "")
        slug = slug_from_href(urlparse(href).path)
        if not slug or slug in seen:
            continue
        row = _card_row(box, page_url, slug)
        if not row:
            continue
        seen.add(slug)
        out.append(row)
    return out


def _parse_hub_cards(s: BeautifulSoup, page_url: str) -> list[dict[str, Any]]:
    """Prefecture landing pages (`/akiya-in-akita`) wrap each tile in `a.group`."""
    out, seen = [], set()
    for box in s.select("a.group[href]"):
        href = urljoin(page_url, box.get("href") or "")
        slug = slug_from_href(urlparse(href).path)
        if not slug or slug in seen:
            continue
        row = _card_row(box, page_url, slug)
        if not row:
            continue
        seen.add(slug)
        out.append(row)
    return out


def _total_pages(s: BeautifulSoup, page_url: str, n_cards: int) -> int:
    last = 1
    nav = s.select_one('nav[aria-label="Pagination"]')
    hrefs = (nav.select("a[href]") if nav else s.select('a[href*="page="]'))
    for a in hrefs:
        qs = parse_qs(urlparse(urljoin(page_url, a.get("href") or "")).query)
        raw = (qs.get("page") or [None])[0]
        if raw and str(raw).isdigit():
            last = max(last, int(raw))
    text = s.get_text(" ", strip=True)
    m = re.search(r"([\d,]+)\s+Properties Found", text, re.I)
    if m:
        found = int(m.group(1).replace(",", ""))
        if found:
            last = max(last, max(1, math.ceil(found / PER_PAGE)))
    if last == 1 and n_cards >= PER_PAGE:
        last = 2  # there's at least a next page we haven't seen
    return last


def parse_list(html: str, page_url: str) -> dict[str, Any]:
    s = _soup(html)
    listings = _parse_listings_grid(s, page_url) or _parse_hub_cards(s, page_url)
    total_pages = _total_pages(s, page_url, len(listings))
    current = _page_number(page_url)
    next_url = with_page(page_url, current + 1) if current < total_pages else None
    return {"listings": listings, "total_pages": total_pages, "next_url": next_url}


# --------------------------------------------------------------------------
# detail pages
# --------------------------------------------------------------------------

def _json_ld_listing(s: BeautifulSoup) -> dict[str, Any]:
    for sc in s.select('script[type="application/ld+json"]'):
        raw = sc.string or sc.get_text() or ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        nodes = data.get("@graph") if isinstance(data, dict) else None
        if isinstance(data, dict) and data.get("@type") == "RealEstateListing":
            return data
        if isinstance(nodes, list):
            for node in nodes:
                if isinstance(node, dict) and node.get("@type") == "RealEstateListing":
                    return node
    return {}


def _fact_map(s: BeautifulSoup) -> dict[str, str]:
    out: dict[str, str] = {}
    box = None
    for h2 in s.select("h2"):
        if "property details" in (h2.get_text() or "").lower():
            box = h2.parent
            break
    if box is None:
        return out
    for row in box.select("div.flex.justify-between"):
        spans = row.find_all("span", recursive=False)
        if len(spans) < 2:
            spans = row.select("span")
        if len(spans) >= 2:
            label = _txt(spans[0])
            value = _txt(spans[1])
            if label and value:
                out[label.lower()] = value
    return out


def _split_glued_area(value: str) -> tuple[int | None, str | None]:
    """'112.86m²5DK' → (113, '5DK')."""
    return _area(value), _layout_from_text(value)


def _photos(s: BeautifulSoup, page_url: str) -> list[str]:
    """Gallery images only — stop at 'Similar Properties' so neighbour thumbs stay out."""
    stop = None
    for h2 in s.select("h2"):
        if "similar" in (h2.get_text() or "").lower():
            stop = h2
            break
    photos, seen = [], set()
    for el in s.descendants:
        if stop is not None and el is stop:
            break
        if getattr(el, "name", None) != "a":
            continue
        href = (el.get("href") or "").strip()
        if "listing-images" not in href:
            continue
        key = re.sub(r"/\d+\.webp$", "", href)
        if key in seen:
            continue
        seen.add(key)
        photos.append(urljoin(page_url, href))
    return photos


# The real write-up is behind Akiya Portal's paid trial, so the JSON-LD
# description is a price/location line plus a sales pitch. The pitch is
# identical on ~5,570 listings, which buried free-text search: "free", "trial"
# and "unlimited" each matched a third of the whole database.
_PITCH = re.compile(r"\s*Start a free trial[^.]*\.\s*$", re.I)


def _clean_description(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = _PITCH.sub("", text).strip()
    return cleaned or None


def parse_detail(html: str, url: str) -> dict[str, Any]:
    s = _soup(html)
    out: dict[str, Any] = {"url": url, "source": SOURCE, "agent": "AkiyaPortal"}
    raw: dict[str, Any] = {}

    slug = slug_from_href(urlparse(url).path)
    if slug:
        out["url"] = canonical_detail_url(slug)
        raw["slug"] = slug

    lid = _listing_id(s)
    if lid:
        out["external_id"] = lid
        out["reference"] = lid
        raw["listing_id"] = lid
    elif slug:
        out["external_id"] = slug
        out["reference"] = slug

    ld = _json_ld_listing(s)
    facts = _fact_map(s)
    raw.update({f"fact_{k}": v for k, v in facts.items()})

    usd = None
    jpy = None
    if ld.get("price") is not None and str(ld.get("priceCurrency", "USD")).upper() == "USD":
        try:
            usd = int(round(float(ld["price"])))
        except (TypeError, ValueError):
            usd = None
    elif ld.get("price") is not None and str(ld.get("priceCurrency", "")).upper() in {"JPY", "YEN"}:
        try:
            jpy = int(round(float(ld["price"])))
        except (TypeError, ValueError):
            jpy = None
    if usd is None:
        usd = parse_usd(_txt(s.h1)) or parse_usd(
            _txt(next((p for p in s.select("p") if _txt(p) == "Price"
                        or (_txt(p) or "").startswith("Price")), None))
        )
        # sidebar "Price $3,208"
        if usd is None:
            usd = parse_usd(_txt(s.select_one("h1")) or _txt(s.title))
    priced = priced_row(usd, jpy)
    out["price"] = priced["price"]
    out["currency"] = priced["currency"]
    raw.update(priced["raw_fields"])

    addr = ld.get("address") if isinstance(ld.get("address"), dict) else {}
    region = facts.get("prefecture") or addr.get("addressRegion")
    locality = addr.get("addressLocality")
    if region:
        out["region"] = region
        out["dept_nl"] = region
    place = _place_from_text(locality, _txt(s.h1), ld.get("name"))
    if not place and locality:
        # "Kagoshima City, Harayoshi 4-chome" → "Kagoshima City"
        place = locality.split(",")[0].strip() or None
    if place:
        out["place"] = place
    if locality:
        raw["address"] = locality

    ptype = facts.get("property type") or "Used single-family home"
    out["type"] = ptype

    layout = facts.get("layout") or _layout_from_text(facts.get("land area") or "")
    if not layout:
        layout = _layout_from_text(_txt(s.h1)) or _layout_from_text(ld.get("name"))
    if layout:
        raw["layout"] = layout
        out["bedrooms"] = _bedrooms_from_layout(layout)

    land = _area(facts.get("land area"))
    living = _area(facts.get("building area"))
    if living is None and facts.get("land area"):
        # glued "112.86m²5DK" is land + layout, not living area
        glued_area, glued_layout = _split_glued_area(facts["land area"])
        land = land or glued_area
        if glued_layout and not layout:
            raw["layout"] = glued_layout
            out["bedrooms"] = _bedrooms_from_layout(glued_layout)
    out["land_m2"] = land
    out["living_m2"] = living

    built = facts.get("construction date")
    if built:
        raw["construction_date"] = built
        out["year_built"] = _year(built)

    if facts.get("source"):
        raw["source_portal"] = facts["source"]
        out["agent_name"] = facts["source"]
    if facts.get("transportation"):
        raw["transportation"] = facts["transportation"]

    name = ld.get("name") or _txt(s.h1)
    raw["headline"] = name
    desc = ld.get("description")
    # JSON-LD description is often just the name; keep it as snippet-quality text
    out["description"] = _clean_description(desc if desc and desc != name else name)
    out["snippet"] = name

    photos = _photos(s, url)
    if not photos and ld.get("image"):
        photos = [ld["image"]]
    out["photos"] = photos
    if photos:
        out["thumb"] = photos[0]

    out["raw_fields"] = raw
    return out
