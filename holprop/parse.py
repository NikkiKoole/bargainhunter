"""Parsers for holprop.com list and detail pages.

Public EN HTML. List cards are `div.searchrestable` (house filters such as
`/sale/pt/villa-house/scr/bulgaria/price/100000/` and the broader
`/sale/property/{country}/` shape share this markup). Each card carries
schema.org JSON-LD. Detail URLs are `/s/sale/{cc}{digits}/?ctype=EUR`.

The portal can display EUR / GBP / USD on the same page. We store the
euro asking price and keep the others in `raw_fields`.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from .http import BASE

SOURCE = "holprop"
PER_PAGE = 39  # live house-filter pages, verified 2026-09-19

ID_RE = re.compile(r"/s/sale/([a-z]{2}\d+)/?", re.I)
PAGE_RE = re.compile(r"/page/(\d+)/?$")
AREA_RE = re.compile(
    r"([\d][\d\s.,]*)\s*(?:m2|m²|sq\.?\s*m|sqm)\b", re.I
)
FOUND_RE = re.compile(r"Found\s+([\d,]+)\b", re.I)

# Dual-price line on detail pages: "£8,171 GBP | $10,905 USD"
GBP_RE = re.compile(r"£\s*([\d.,\s]+)\s*GBP", re.I)
USD_RE = re.compile(r"\$\s*([\d.,\s]+)\s*USD", re.I)
EUR_RE = re.compile(r"€\s*([\d.,\s]+)|([\d.,\s]+)\s*EUR\b", re.I)


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
    if not text:
        return None
    m = AREA_RE.search(str(text).replace("\xa0", " "))
    if not m:
        return None
    return _int(m.group(1))


def _json_load(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _as_list(data: Any) -> list:
    if data is None:
        return []
    return data if isinstance(data, list) else [data]


def _country_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("name") or value.get("addressCountry")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _listing_id(href: str | None) -> str | None:
    if not href:
        return None
    m = ID_RE.search(urlparse(href).path)
    return m.group(1).lower() if m else None


def canonical_detail_url(ext: str, ctype: str = "EUR") -> str:
    return f"{BASE}/s/sale/{ext.lower()}/?ctype={ctype}"


def _page_number(page_url: str) -> int:
    m = PAGE_RE.search(urlparse(page_url).path)
    if m:
        return max(1, int(m.group(1)))
    return 1


def with_page(page_url: str, page: int) -> str:
    """Keep the country/type/price path and set /page/N/.

    Page 1 on the live site omits `/page/1/`, so we do too — cache keys stay
    a function of the URL the site actually serves.
    """
    parsed = urlparse(page_url)
    path = re.sub(r"/page/\d+/?$", "/", parsed.path)
    if not path.endswith("/"):
        path += "/"
    if page > 1:
        path = f"{path}page/{page}/"
    return urlunparse(parsed._replace(path=path))


def parse_money_blob(text: str | None) -> dict[str, int]:
    """Pull EUR / GBP / USD amounts out of a price block."""
    out: dict[str, int] = {}
    if not text:
        return out
    blob = text.replace("\xa0", " ")
    m = EUR_RE.search(blob)
    if m:
        amount = _int(m.group(1) or m.group(2))
        if amount is not None:
            out["EUR"] = amount
    m = GBP_RE.search(blob)
    if m:
        amount = _int(m.group(1))
        if amount is not None:
            out["GBP"] = amount
    m = USD_RE.search(blob)
    if m:
        amount = _int(m.group(1))
        if amount is not None:
            out["USD"] = amount
    return out


def priced_row(eur: int | None, gbp: int | None = None,
               usd: int | None = None) -> dict[str, Any]:
    """Prefer the portal's euro asking price; keep others in raw_fields."""
    raw: dict[str, Any] = {}
    if eur is not None:
        raw["price_eur"] = eur
    if gbp is not None:
        raw["price_gbp"] = gbp
    if usd is not None:
        raw["price_usd"] = usd
    if eur is not None:
        raw["price_source"] = "portal_eur"
        return {"price": eur, "currency": "EUR", "raw_fields": raw}
    if gbp is not None:
        raw["price_source"] = "portal_gbp"
        return {"price": gbp, "currency": "GBP", "raw_fields": raw}
    if usd is not None:
        raw["price_source"] = "portal_usd"
        return {"price": usd, "currency": "USD", "raw_fields": raw}
    return {"price": None, "currency": "EUR", "raw_fields": raw}


def _card_ld(box) -> dict[str, Any]:
    for sc in box.select('script[type="application/ld+json"]'):
        data = _json_load(sc.string or sc.get_text() or "")
        for node in _as_list(data):
            if not isinstance(node, dict):
                continue
            types = _as_list(node.get("@type"))
            if any(t in {"House", "Offer", "Residence", "Apartment",
                         "SingleFamilyResidence", "Product"} for t in types):
                return node
            if node.get("url") and node.get("price") is not None:
                return node
    return {}


def _type_from_ld(ld: dict[str, Any], box) -> str | None:
    types = [t for t in _as_list(ld.get("@type")) if t and t != "Offer"]
    if types:
        return str(types[0])
    cat = ld.get("category")
    if cat:
        return str(cat).split(" in ")[0].strip() or None
    beds = box.select_one(".searchbeds")
    text = _txt(beds) or ""
    first = text.split("•")[0].strip()
    return first or None


def _card_row(box, page_url: str) -> dict[str, Any] | None:
    ld = _card_ld(box)
    href = None
    link = box.select_one("a[href*='/s/sale/']")
    if link:
        href = urljoin(page_url, link.get("href") or "")
    href = href or ld.get("url")
    ext = _listing_id(href) or _listing_id(ld.get("url"))
    if not ext:
        return None

    addr = ld.get("address") if isinstance(ld.get("address"), dict) else {}
    place = addr.get("addressLocality")
    region = addr.get("addressRegion")
    country = _country_name(addr.get("addressCountry"))
    if not place:
        loc = box.select_one("[itemprop=addressLocality]")
        place = _txt(loc)
    if not region:
        region = _txt(box.select_one("[itemprop=addressRegion]"))
    if not country:
        country = _txt(box.select_one("[itemprop=addressCountry]"))

    eur = None
    if ld.get("price") is not None:
        try:
            eur = int(round(float(ld["price"])))
        except (TypeError, ValueError):
            eur = None
        cur = str(ld.get("priceCurrency") or "EUR").upper()
        if cur != "EUR":
            # unusual on our ?ctype=EUR cards; still record it
            eur = None
    if eur is None:
        money = parse_money_blob(_txt(box.select_one(".pricefield")) or _txt(box))
        eur = money.get("EUR")
    priced = priced_row(eur)

    rooms = ld.get("numberOfRooms")
    beds = None
    try:
        beds = int(rooms) if rooms is not None and str(rooms).strip() != "" else None
    except (TypeError, ValueError):
        beds = None
    if beds is None:
        b = box.select_one(".searchbeds b")
        beds = _int(_txt(b))

    living = None
    floor = ld.get("floorSize")
    if isinstance(floor, dict) and floor.get("value") is not None:
        living = _int(str(floor["value"]))
    if living is None:
        living = _area(_txt(box.select_one(".searchbeds")))

    lat = lon = None
    lat_el = box.select_one("[itemprop=latitude]")
    lon_el = box.select_one("[itemprop=longitude]")
    try:
        if lat_el and (lat_el.get("content") or _txt(lat_el)):
            lat = float(lat_el.get("content") or _txt(lat_el))
        if lon_el and (lon_el.get("content") or _txt(lon_el)):
            lon = float(lon_el.get("content") or _txt(lon_el))
    except (TypeError, ValueError):
        lat = lon = None

    img = ld.get("image")
    if not img:
        el = box.select_one(".image_prop_result[src], img.image_prop_result, img[src]")
        if el and el.get("src"):
            img = el["src"]
    thumb = urljoin(BASE + "/", img) if img else None

    snippet = ld.get("name") or _txt(box.select_one(".prop_title"))
    desc = _txt(box.select_one("[itemprop=description]"))

    raw = dict(priced["raw_fields"])
    raw["listing_id"] = ext
    if country:
        raw["country"] = country
    if ld.get("url"):
        raw["portal_url"] = ld["url"]

    return {
        "source": SOURCE,
        "external_id": ext,
        "id": ext,
        "url": canonical_detail_url(ext),
        "type": _type_from_ld(ld, box),
        "place": place,
        "region": region,
        "dept_nl": region,
        "lat": lat,
        "lon": lon,
        "price": priced["price"],
        "currency": priced["currency"],
        "beds": beds,
        "bedrooms": beds,
        "living_m2": living,
        "reference": ext.upper(),
        "thumb": thumb,
        "snippet": snippet,
        "description": desc,
        "promoted": False,
        "raw_fields": raw,
    }


def _total_pages(s: BeautifulSoup, n_cards: int) -> int:
    last = 1
    for a in s.select("a[href]"):
        m = PAGE_RE.search(a.get("href") or "")
        if m:
            last = max(last, int(m.group(1)))
    found_el = s.select_one("h2.stitle")
    blob = _txt(found_el) or ""
    m = FOUND_RE.search(blob) or FOUND_RE.search(s.get_text(" ", strip=True))
    if m:
        found = int(m.group(1).replace(",", ""))
        if found:
            last = max(last, max(1, math.ceil(found / PER_PAGE)))
    if last == 1 and n_cards >= PER_PAGE:
        last = 2
    return last


def parse_list(html: str, page_url: str) -> dict[str, Any]:
    s = _soup(html)
    listings, seen = [], set()
    for box in s.select(".searchrestable"):
        row = _card_row(box, page_url)
        if not row or row["external_id"] in seen:
            continue
        seen.add(row["external_id"])
        listings.append(row)
    total_pages = _total_pages(s, len(listings))
    current = _page_number(page_url)
    next_url = with_page(page_url, current + 1) if current < total_pages else None
    return {"listings": listings, "total_pages": total_pages, "next_url": next_url}


# --------------------------------------------------------------------------
# detail pages
# --------------------------------------------------------------------------

def _meta(s: BeautifulSoup, prop: str) -> str | None:
    el = s.select_one(f'meta[itemprop="{prop}"]')
    if el and el.get("content"):
        return el["content"]
    el = s.select_one(f'[itemprop="{prop}"]')
    if el is None:
        return None
    return el.get("content") or _txt(el)


def _fact_map(text: str) -> dict[str, str]:
    """'Type: Villa-House Bedrooms: 4 Floor area: 150 m2' → {type: …}."""
    out: dict[str, str] = {}
    parts = re.split(
        r"(Type|Bedrooms?|Bathrooms?|Floor area|Land area|Size|Plot|"
        r"Date Built|City|Region|Country|Area|Postcode|Address)\s*:\s*",
        text,
        flags=re.I,
    )
    for i in range(1, len(parts) - 1, 2):
        label = parts[i].strip().lower()
        value = re.sub(r"\s+", " ", parts[i + 1]).strip(" |,")
        if label and value:
            out[label] = value
    return out


def _photos(s: BeautifulSoup, page_url: str) -> list[str]:
    photos, seen = [], set()
    candidates = []
    meta_img = _meta(s, "image")
    if meta_img:
        candidates.append(meta_img)
    for img in s.select("img[src]"):
        src = (img.get("src") or "").strip()
        if not src:
            continue
        low = src.lower()
        if any(skip in low for skip in (
            "/images/icons/", "/images/fl/", "/images/logo",
            "imgavatar", "/users/agents/", "ads", "spinner",
            "flag", "favicon", "close.png", "bed.png", "bat.png",
        )):
            continue
        if any(ok in low for ok in ("cache-xml", "/photos/", "image_prop")):
            candidates.append(src)
    for src in candidates:
        abs_url = urljoin(page_url, src)
        key = re.sub(r"[?].*$", "", abs_url)
        if key in seen:
            continue
        seen.add(key)
        photos.append(abs_url)
    return photos


def parse_detail(html: str, url: str) -> dict[str, Any]:
    s = _soup(html)
    out: dict[str, Any] = {"url": url, "source": SOURCE, "agent": "Holprop"}
    raw: dict[str, Any] = {}

    ext = _listing_id(url)
    if ext:
        out["url"] = canonical_detail_url(ext)
        out["external_id"] = ext
        out["reference"] = ext.upper()
        raw["listing_id"] = ext

    facts = {}
    for blob in (
        _txt(s.select_one(".lefdetails")),
        _txt(s.select_one("span.address")),
        _txt(s.find(string=re.compile(r"Floor area:", re.I))),
        _txt(s.find(string=re.compile(r"Main Features:", re.I))),
        s.get_text(" ", strip=True),
    ):
        if blob:
            facts.update(_fact_map(blob))
    raw.update({f"fact_{k}": v for k, v in facts.items()})

    ptype = facts.get("type")
    if ptype:
        # "Villa-House Bedrooms" if a later split leaked — keep the first token.
        out["type"] = re.split(r"\s{2,}|\s+Bedrooms?\b", ptype, 1)[0].strip()

    if facts.get("bedrooms") or facts.get("bedroom"):
        out["bedrooms"] = _int(facts.get("bedrooms") or facts.get("bedroom"))
    if facts.get("bathrooms") or facts.get("bathroom"):
        out["baths"] = _int(facts.get("bathrooms") or facts.get("bathroom"))

    living = _area(facts.get("floor area") or facts.get("size"))
    land = _area(facts.get("land area") or facts.get("plot"))
    out["living_m2"] = living
    out["land_m2"] = land

    place = _txt(s.select_one("[itemprop=addressLocality]")) or facts.get("city")
    region = _txt(s.select_one("[itemprop=addressRegion]")) or facts.get("region")
    country = _txt(s.select_one("[itemprop=addressCountry]")) or facts.get("country")
    if place:
        out["place"] = place
    if region:
        out["region"] = region
        out["dept_nl"] = region
    if country:
        raw["country"] = country

    eur = gbp = usd = None
    meta_price = _meta(s, "price")
    meta_cur = (_meta(s, "priceCurrency") or "EUR").upper()
    if meta_price is not None:
        amount = _int(meta_price)
        if amount is not None and meta_cur == "EUR":
            eur = amount
    money = parse_money_blob(
        " ".join(x for x in (
            _txt(s.select_one(".advert-price")),
            _txt(s.select_one(".txt_gray_price")),
            _txt(s.select_one(".lefdetails")),
        ) if x)
    )
    eur = eur if eur is not None else money.get("EUR")
    gbp = money.get("GBP")
    usd = money.get("USD")
    priced = priced_row(eur, gbp, usd)
    out["price"] = priced["price"]
    out["currency"] = priced["currency"]
    raw.update(priced["raw_fields"])

    try:
        if _meta(s, "latitude"):
            out["lat"] = float(_meta(s, "latitude"))
        if _meta(s, "longitude"):
            out["lon"] = float(_meta(s, "longitude"))
    except (TypeError, ValueError):
        pass

    headline = _txt(s.select_one("h2.largetitle")) or _txt(s.h2) or _txt(s.h1)
    raw["headline"] = headline
    desc = None
    block = s.select_one(".descriptiontext.more") or s.select_one("[itemprop=description]")
    if block:
        desc = block.get_text("\n", strip=True)
        desc = re.sub(r"\n{3,}", "\n\n", desc)
        if desc.lower().startswith("description:"):
            desc = desc.split(":", 1)[1].strip()
    out["description"] = desc or headline
    out["snippet"] = headline

    owner = s.select_one(".cont_owner")
    if owner:
        agency = _txt(owner.select_one(".textblue"))
        person = _txt(owner.find("b"))
        if agency:
            out["agent_name"] = agency
            raw["agency"] = agency
        if person:
            raw["agent_person"] = person

    photos = _photos(s, url)
    out["photos"] = photos
    if photos:
        out["thumb"] = photos[0]

    out["raw_fields"] = raw
    return out
