"""Parsers for centrarium.com list and detail pages.

Public EN HTML. The Montenegro house catalogue is
`/en/montenegro/sale/houses/` (826 listings / 36 per page on 2026-09-19).
`/en/montenegro/sale/houses/lowprice-montenegro/` is the same stock
sorted cheap-first — not a ≤€100k filter (page 23 is multi-million).
We cap at ingest with `skip.above`.

Cards are `div.j-item.g-item` with `data-id`. Detail URLs are
`/en/{place}/{slug}-{id}.html`. Prices on the EN lowprice list are euro
asking prices (`35 000 €`); JSON-LD on the detail page is
`priceCurrency: EUR`.

robots.txt Crawl-delay is 5s. `Disallow: /*?page=` is an indexer rule —
path forms `/page/2/` and `/2/` 404, so the site's own `?page=N` pager
is what we follow (page 1 omits it).
"""
from __future__ import annotations

import json
import math
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from .http import BASE

SOURCE = "centrarium"
PER_PAGE = 36  # live lowprice / houses pages, verified 2026-09-19
AGENT = "Centrarium"

ID_HREF_RE = re.compile(r"/en/[^/?#]+/[^/?#]+-(\d+)\.html", re.I)
ID_TAIL_RE = re.compile(r"-(\d+)\.html$", re.I)
PAGE_RE = re.compile(r"[?&]page=(\d+)", re.I)
TOTAL_RE = re.compile(r"([\d,\s]+)\s+listings\b", re.I)
EUR_RE = re.compile(r"€\s*([\d\s.,]+)|([\d\s.,]+)\s*€")
AREA_RE = re.compile(
    r"([\d][\d\s.,]*)\s*(?:m²|m2|sq\.?\s*m|sqm)\b", re.I
)
PLOT_RE = re.compile(
    r"(?:on\s+a\s+plot\s+of|"
    r"the\s+plot\s+of\s+land\s+is|"
    r"the\s+plot\s+is|"
    r"plot\s+is|"
    r"plot\s+of\s+land(?:\s+for\b.{0,80})?\s+(?:with\s+an\s+)?area\s+of|"
    r"land\s+plot\s+of|"
    r"plot\s+of\s+land\s+of|"
    r"plot\s+of(?:\s+land)?)\s*"
    r"([\d][\d\s.,]*)\s*(?:m²|m2|sq\.?\s*m|sqm)\b",
    re.I,
)
TYPE_RE = re.compile(
    r"\b(House|Villa|Cottage|Townhouse|Town house|Chalet|Maisonette)\b", re.I
)
# "House in Niksic Montenegro" / "by the sea in Bar (Susanj) Montenegro"
TITLE_PLACE_RE = re.compile(
    r"\bin\s+([A-Z][A-Za-z'’ -]+?)(?:\s*\(([^)]+)\))?\s+Montenegro\b"
)
SLUG_PLACE = {
    "bar-me": "Bar",
    "herceg-novi": "Herceg Novi",
    "central-region-me": "Central region",
    "northern-region": "Northern Region",
    "coastal-region": "Coastal Region",
}
LATIN_WORD_RE = re.compile(r"[A-Za-z]")
CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
COUNTRY_NAMES = {
    "montenegro", "serbia", "albania", "croatia", "bosnia",
    "bosnia and herzegovina", "north macedonia", "macedonia",
    "kosovo", "slovenia",
}

# Tooltip / dynprop labels after emoji strip.
LABEL_AREA = re.compile(r"total area|living area|floor area", re.I)
LABEL_BEDS = re.compile(r"bedrooms?", re.I)
LABEL_ROOMS = re.compile(r"\brooms?\b", re.I)
LABEL_BATHS = re.compile(r"bathrooms?", re.I)
LABEL_LAND = re.compile(r"\b(land|plot|garden|grounds?)\b", re.I)
LABEL_TYPE = re.compile(r"type of (?:house|property)|property type", re.I)
LABEL_OBJECT = re.compile(r"object id|listing id|id\b", re.I)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _txt(node) -> str | None:
    if node is None:
        return None
    t = node.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", t) or None


def _int(text: str | None) -> int | None:
    """Parse a displayed integer. Spaces/dots/commas are thousands.

    '35 000' / '35.000' / '35,000' → 35000. A leftover '21.5' becomes 22.
    """
    if not text:
        return None
    raw = str(text).replace("\xa0", " ").strip()
    m = re.search(r"[\d]+(?:[.\s,]\d+)*", raw)
    if not m:
        return None
    token = m.group(0).replace(" ", "")
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", token):
        return int(token.replace(".", ""))
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+", token):
        return int(token.replace(",", ""))
    digits = re.sub(r"\D", "", token)
    return int(digits) if digits else None


def parse_eur(text: str | None) -> int | None:
    if not text:
        return None
    blob = str(text).replace("\xa0", " ")
    m = EUR_RE.search(blob)
    if not m:
        return None
    return _int(m.group(1) or m.group(2))


def _listing_id(href: str | None) -> str | None:
    if not href:
        return None
    path = urlparse(href).path
    m = ID_HREF_RE.search(path) or ID_TAIL_RE.search(path)
    return m.group(1) if m else None


def _page_number(page_url: str) -> int:
    m = PAGE_RE.search(page_url)
    if m:
        return max(1, int(m.group(1)))
    return 1


def with_page(page_url: str, page: int) -> str:
    """Keep the filter path and set ?page=N.

    Page 1 on the live site omits `?page=`, so we do too — cache keys stay
    a function of the URL the site actually serves. Path forms /page/2/
    and /2/ 404'd on 2026-09-19.
    """
    parsed = urlparse(page_url)
    if not parsed.scheme:
        parsed = urlparse(urljoin(BASE + "/", page_url))
    pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if k != "page"]
    if page > 1:
        pairs.append(("page", str(page)))
    query = urlencode(pairs)
    return urlunparse(parsed._replace(query=query, fragment=""))


def _json_load(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _as_list(data: Any) -> list:
    if data is None:
        return []
    return data if isinstance(data, list) else [data]


def _clean_label(text: str | None) -> str:
    if not text:
        return ""
    # Drop emoji / dingbats / VS16 so "📐 Total area:" → "total area"
    text = re.sub(r"[\u2600-\u27BF\U0001F300-\U0001FAFF\uFE0F\u200D]", "", text)
    return re.sub(r"\s+", " ", text).strip(" :").lower()


def _place_from_title(title: str | None) -> tuple[str | None, str | None]:
    if not title:
        return None, None
    m = TITLE_PLACE_RE.search(title)
    if not m:
        return None, None
    town, extra = m.group(1).strip(), (m.group(2) or "").strip()
    if extra:
        return extra, town
    return town, town


def _place_from_url(url: str | None) -> str | None:
    if not url:
        return None
    m = re.search(r"/en/([^/]+)/", url)
    if not m:
        return None
    slug = m.group(1).lower()
    if slug in {"montenegro", "item", "search"}:
        return None
    if slug in SLUG_PLACE:
        return SLUG_PLACE[slug]
    return slug.replace("-", " ").title()


def _place_region(addr: str | None) -> tuple[str | None, str | None]:
    """English locality + region from a mixed EN/Cyrillic address line.

    List cards look like 'Bar, Susanj, Черногория, Бар' or just
    'Zabljak, Черногория, Жабляк'. Detail lines add the statistical
    region: '… Zabljak, Northern Region, Montenegro'.
    """
    if not addr:
        return None, None
    parts = [p.strip() for p in addr.split(",") if p.strip()]
    latin: list[str] = []
    for p in parts:
        if CYRILLIC_RE.search(p):
            continue
        if p.lower() in COUNTRY_NAMES:
            continue
        if LATIN_WORD_RE.search(p):
            latin.append(p)
    if not latin:
        return None, None
    region_part = next((p for p in latin if "region" in p.lower()), None)
    rest = [p for p in latin if "region" not in p.lower()]
    if region_part:
        place = rest[0] if rest else None
        return place, region_part
    if len(rest) >= 2:
        return rest[-1], rest[0]
    return rest[0], rest[0]


def _type_from_title(title: str | None) -> str | None:
    if not title:
        return None
    m = TYPE_RE.search(title)
    return m.group(1).title() if m else None


def _card_params(box) -> dict[str, str]:
    out: dict[str, str] = {}
    for sp in box.select(".rp-it-params .j-tooltip, .rp-it-params span[title]"):
        label = _clean_label(sp.get("title") or "")
        value = _txt(sp) or ""
        if label and value:
            out[label] = value
    return out


def _param_int(params: dict[str, str], pred) -> int | None:
    for key, value in params.items():
        if pred.search(key):
            return _int(value)
    return None


def _card_row(box, page_url: str) -> dict[str, Any] | None:
    ext = str(box.get("data-id") or "") or None
    href = None
    link = box.select_one("a.g-item-title[href], a.c-item-img-box[href], a[href*='.html']")
    if link:
        href = urljoin(page_url, link.get("href") or "")
    href_id = _listing_id(href)
    ext = ext or href_id
    if href_id and ext and href_id != ext:
        # Prefer the URL id — it is the portal's stable object number.
        ext = href_id
    if not ext:
        return None
    if href and not href_id:
        href = None

    title = _txt(box.select_one(".g-item-title, .jt-item-title"))
    addr = _txt(box.select_one(".g-item-address"))
    place, region = _place_region(addr)
    params = _card_params(box)

    living = _param_int(params, LABEL_AREA)
    if living is None:
        living = _int(params.get("📐 total area") or "")
    beds = _param_int(params, LABEL_BEDS)
    rooms = _param_int(params, LABEL_ROOMS)
    baths = _param_int(params, LABEL_BATHS)
    if beds is None:
        beds = rooms

    price = parse_eur(_txt(box.select_one(".c-item-price")))
    if price is None:
        price = parse_eur(_txt(box.select_one(".g-item-price-box")))

    img = box.select_one(".c-item-img[src], img.c-item-img, img[src]")
    thumb = None
    if img and img.get("src"):
        thumb = urljoin(BASE + "/", img["src"])

    owner = _txt(box.select_one(".rp-it-owner"))
    ptype = _type_from_title(title) or "House"

    raw: dict[str, Any] = {
        "listing_id": ext,
        "country": "Montenegro",
        "price_source": "portal_eur" if price is not None else None,
    }
    if title:
        raw["title"] = title
    if addr:
        raw["address"] = addr
    if owner:
        raw["owner"] = owner
    if rooms is not None:
        raw["rooms"] = rooms
    raw = {k: v for k, v in raw.items() if v is not None}

    return {
        "source": SOURCE,
        "external_id": ext,
        "id": ext,
        "url": href,
        "type": ptype,
        "place": place,
        "region": region,
        "dept_nl": region,
        "price": price,
        "currency": "EUR",
        "beds": beds,
        "bedrooms": beds,
        "baths": baths,
        "rooms": rooms,
        "living_m2": living,
        "reference": ext,
        "thumb": thumb,
        "snippet": title,
        "promoted": False,
        "raw_fields": raw,
        "agent": AGENT,
        "agent_name": owner,
    }


def _total_pages(s: BeautifulSoup, n_cards: int, page_url: str) -> int:
    last = _page_number(page_url)
    for a in s.select("a[href*='page=']"):
        m = PAGE_RE.search(a.get("href") or "")
        if m:
            last = max(last, int(m.group(1)))
    blob = _txt(s.select_one(".j-search-total, .it-sort-item-count")) or ""
    m = TOTAL_RE.search(blob) or TOTAL_RE.search(s.get_text(" ", strip=True))
    if m:
        found = _int(m.group(1))
        if found:
            last = max(last, max(1, math.ceil(found / PER_PAGE)))
    if last == _page_number(page_url) and n_cards >= PER_PAGE:
        last += 1
    return max(1, last)


def parse_list(html: str, page_url: str) -> dict[str, Any]:
    s = _soup(html)
    listings, seen = [], set()
    for box in s.select("div.j-item.g-item"):
        row = _card_row(box, page_url)
        if not row or row["external_id"] in seen:
            continue
        seen.add(row["external_id"])
        listings.append(row)
    total_pages = _total_pages(s, len(listings), page_url)
    current = _page_number(page_url)
    next_url = with_page(page_url, current + 1) if current < total_pages else None
    return {"listings": listings, "total_pages": total_pages, "next_url": next_url}


# --------------------------------------------------------------------------
# detail pages
# --------------------------------------------------------------------------

def _product_ld(s: BeautifulSoup) -> dict[str, Any]:
    for sc in s.select('script[type="application/ld+json"]'):
        data = _json_load(sc.string or sc.get_text() or "")
        for node in _as_list(data):
            if not isinstance(node, dict):
                continue
            types = _as_list(node.get("@type"))
            if "Product" in types or "Offer" in types or "Residence" in types:
                return node
            if node.get("offers"):
                return node
    return {}


def _dynprops(s: BeautifulSoup) -> dict[str, str]:
    out: dict[str, str] = {}
    for el in s.select(".vw-dynprops-item"):
        label = _clean_label(_txt(el.select_one(".vw-dynprops-item-attr")))
        value = _txt(el.select_one(".vw-dynprops-item-val"))
        if label and value:
            out[label] = value
    return out


def _dyn_int(props: dict[str, str], pred) -> int | None:
    for key, value in props.items():
        if pred.search(key):
            return _int(value)
    return None


def _dyn_text(props: dict[str, str], pred) -> str | None:
    for key, value in props.items():
        if pred.search(key):
            return value
    return None


def _land_from_text(text: str | None) -> int | None:
    if not text:
        return None
    m = PLOT_RE.search(str(text).replace("\xa0", " "))
    return _int(m.group(1)) if m else None


def _coords(html: str, s: BeautifulSoup) -> tuple[float | None, float | None]:
    m = re.search(
        r'"addr_lat"\s*:\s*(-?[\d.]+)\s*,\s*"addr_lon"\s*:\s*(-?[\d.]+)', html
    )
    if m:
        try:
            return float(m.group(1)), float(m.group(2))
        except ValueError:
            pass
    osm = s.select_one("a[href*='mlat=']")
    if osm and osm.get("href"):
        hm = re.search(r"mlat=(-?[\d.]+).*?mlon=(-?[\d.]+)", osm["href"])
        if hm:
            try:
                return float(hm.group(1)), float(hm.group(2))
            except ValueError:
                pass
    return None, None


def _photos(s: BeautifulSoup, ld: dict[str, Any], page_url: str) -> list[str]:
    photos, seen = [], set()
    candidates = list(_as_list(ld.get("image")))
    for img in s.select("img[src]"):
        src = (img.get("src") or "").strip()
        if not src:
            continue
        low = src.lower()
        if "cdn.centrarium.com" in low and not any(
            skip in low for skip in ("/files/images/", "icon", "logo", "flag")
        ):
            candidates.append(src)
    for src in candidates:
        if not isinstance(src, str) or not src.strip():
            continue
        abs_url = urljoin(page_url, src.strip())
        key = re.sub(r"[?].*$", "", abs_url)
        if key in seen:
            continue
        if not re.search(r"cdn\.centrarium\.com/[\w-]+\.jpe?g", key, re.I):
            continue
        seen.add(key)
        photos.append(abs_url)
    return photos


def parse_detail(html: str, url: str) -> dict[str, Any]:
    s = _soup(html)
    out: dict[str, Any] = {"url": url, "source": SOURCE, "agent": AGENT}
    raw: dict[str, Any] = {"country": "Montenegro"}

    ext = _listing_id(url)
    head_id = None
    hid = s.select_one(".rp-vw-head-info-item")
    if hid:
        m = re.search(r"№\s*(\d+)", _txt(hid) or "")
        if m:
            head_id = m.group(1)
    ext = ext or head_id
    if ext:
        out["external_id"] = ext
        out["reference"] = ext
        raw["listing_id"] = ext
        # Keep the live EN detail URL if we were handed a relative path.
        if url.startswith("/"):
            out["url"] = urljoin(BASE + "/", url)

    ld = _product_ld(s)
    props = _dynprops(s)
    raw.update({f"fact_{k}": v for k, v in props.items()})

    headline = _txt(s.select_one("h1")) or ld.get("name")
    raw["headline"] = headline
    out["snippet"] = headline

    ptype = _dyn_text(props, LABEL_TYPE) or _type_from_title(headline)
    out["type"] = ptype or "House"

    living = _dyn_int(props, LABEL_AREA)
    land = _dyn_int(props, LABEL_LAND)
    beds = _dyn_int(props, LABEL_BEDS)
    rooms = _dyn_int(props, LABEL_ROOMS)
    baths = _dyn_int(props, LABEL_BATHS)

    desc = None
    block = s.select_one(".vw-descr")
    if block:
        desc = block.get_text("\n", strip=True)
        desc = re.sub(r"\n{3,}", "\n\n", desc)
    if not desc:
        desc = ld.get("description")
    out["description"] = desc or headline

    if land is None:
        land = _land_from_text(desc)
    if land is not None and living is not None and land == living:
        # Parcel echoed into both fields — keep living, drop land.
        land = None
    out["living_m2"] = living
    out["land_m2"] = land
    if beds is None:
        beds = rooms
    out["bedrooms"] = beds
    out["rooms"] = rooms
    out["baths"] = baths

    object_id = _dyn_text(props, LABEL_OBJECT)
    if object_id and re.fullmatch(r"\d+", object_id):
        out["reference"] = object_id
        raw["object_id"] = object_id

    addr = (
        _txt(s.select_one(".rp-location-address"))
        or _txt(s.select_one(".rp-vw-gallery-addr"))
    )
    place, region = _place_region(addr)
    if not place or not region:
        t_place, t_region = _place_from_title(headline)
        place = place or t_place
        region = region or t_region
    if not place:
        place = _place_from_url(out.get("url") or url)
        region = region or place
    if place:
        out["place"] = place
    if region:
        out["region"] = region
        out["dept_nl"] = region
    if addr:
        raw["address"] = addr

    eur = None
    offers = ld.get("offers") if isinstance(ld.get("offers"), dict) else {}
    if offers.get("price") is not None:
        try:
            amount = int(round(float(offers["price"])))
        except (TypeError, ValueError):
            amount = None
        cur = str(offers.get("priceCurrency") or "EUR").upper()
        if amount is not None and cur == "EUR":
            eur = amount
    if eur is None:
        eur = parse_eur(
            _txt(s.select_one(".vw-price-num"))
            or _txt(s.select_one(".vw-price-box"))
            or _txt(s.select_one(".vw-top-sticky-nav-info-price"))
        )
    out["price"] = eur
    out["currency"] = "EUR"
    if eur is not None:
        raw["price_eur"] = eur
        raw["price_source"] = "portal_eur"

    lat, lon = _coords(html, s)
    if lat is not None:
        out["lat"] = lat
    if lon is not None:
        out["lon"] = lon

    photos = _photos(s, ld, url)
    out["photos"] = photos
    if photos:
        out["thumb"] = photos[0]

    features = []
    skip_feat = re.compile(
        r"total area|bedrooms?|bathrooms?|rooms?|object id|type of", re.I
    )
    for key, value in props.items():
        if skip_feat.search(key):
            continue
        features.append(f"{key}: {value}")
    if features:
        out["features"] = " | ".join(features)

    if ld.get("url"):
        raw["portal_url"] = ld["url"]
        if not out.get("url") or out["url"].startswith("/"):
            out["url"] = ld["url"]

    out["raw_fields"] = raw
    return out
