"""Parsers for home.ge/en list and detail pages.

Public EN HTML on Flynax. House-for-sale cards are `article.item`
inside `section#listings`, with `data-listing-id` on
`.compare-grid-icon`. Detail slugs end in `-{id}.html`.

The site's own price filter is a GET form on
`/en/saxlebi-agarakebi/search-results.html` (`f[Category_ID]=88`
House For Sale, `f[price][to]` + `f[price][currency]=euro`).
Pagination is `/search-results/indexN.html?…` (page 1 omits
`/indexN`). Category lists use the same `/indexN.html` shape.

robots.txt Allow: / — no crawl-delay. `?sort_by=` is disallowed
(indexer rule); we do not sort via query string. Featured /
TOP VIP / Super VIP cards are real listings — keep them, dedupe
by id. Project banners (`.banner-in-grid`) are not listings.

The portal prints USD by default (`70,000.00 $` / JSON-LD
`priceCurrency: USD`) and sometimes GEL / EUR. We store euro
and keep the original in `raw_fields`.

First anonymous GET is an empty 200 + session cookie (see
`homege.http.Fetcher`). Georgia: foreigners can own
non-agricultural freehold; agricultural land is restricted.
"""
from __future__ import annotations

import json
import math
import re
import warnings
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from .fx import FX_SOURCE, GEL_PER_EUR, GEL_TO_EUR, USD_TO_EUR, gel_to_eur, usd_to_eur
from .http import BASE

SOURCE = "homege"
PER_PAGE = 48  # live pageSize on the house search, verified 2026-09-19
PLOT_AREA_MIN = 1000  # m²: Area this large with no yard is treated as land

ID_RE = re.compile(r"(?:-|/ad)(\d+)(?:\.html)?$", re.I)
INDEX_RE = re.compile(r"/index(\d+)\.html$", re.I)
ADS_FOUND_RE = re.compile(r"([\d\s.,]+)\s+ad\(s\)\s+found", re.I)
AREA_RE = re.compile(r"([\d][\d\s.,]*)\s*(?:m²|m2|m)\b", re.I)
ROOMS_RE = re.compile(r"([\d]+)\s*Rooms?\b", re.I)
USD_RE = re.compile(r"([\d][\d\s.,]*)\s*(?:\$|USD\b|US\$)", re.I)
EUR_RE = re.compile(r"([\d][\d\s.,]*)\s*(?:€|EUR\b)", re.I)
GEL_RE = re.compile(r"([\d][\d\s.,]*)\s*(?:₾|GEL\b)", re.I)
PREFIX_USD_RE = re.compile(r"\$\s*([\d][\d\s.,]*)", re.I)
PREFIX_EUR_RE = re.compile(r"€\s*([\d][\d\s.,]*)", re.I)
PREFIX_GEL_RE = re.compile(r"₾\s*([\d][\d\s.,]*)", re.I)
LATLNG_RE = re.compile(
    r"latLng['\"]?\s*:\s*['\"]\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)",
    re.I,
)
AG_LAND_RE = re.compile(
    r"\b(?:agricultural(?:\s+land)?|ag[\s-]?land|სასოფლო(?:[- ]სამეურნეო)?)\b",
    re.I,
)
HOUSEHOLD_PLOT_RE = re.compile(r"საკარმიდამო", re.I)
RENT_RE = re.compile(r"\b(?:for rent|house for rent|daily rent|lease)\b", re.I)
LAND_PATH_RE = re.compile(r"/miwis-nakveti/", re.I)
APT_PATH_RE = re.compile(r"/binebi/", re.I)
HOUSE_PATH_RE = re.compile(r"/saxlebi-agarakebi/", re.I)

OWNERSHIP_NOTE = (
    "Georgia: foreigners can own non-agricultural freehold "
    "(houses, household plots). Agricultural land is generally "
    "restricted — do not buy ag land without a Georgian lawyer"
)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _int(text: str | None) -> int | None:
    if not text:
        return None
    cleaned = str(text).replace("\xa0", " ").replace(",", "")
    # keep the integer part of "70,000.00" / "1,489.36"
    m = re.search(r"(\d+(?:\.\d+)?)", cleaned)
    if not m:
        return None
    try:
        return int(round(float(m.group(1))))
    except ValueError:
        return None


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


def listing_id(href: str | None) -> str | None:
    if not href:
        return None
    path = urlparse(href).path.rstrip("/")
    m = ID_RE.search(path)
    return m.group(1) if m else None


def canonical_detail_url(ext: str, slug: str = "") -> str:
    tail = slug.strip("/") if slug else f"listing-{ext}"
    if not tail.endswith(f"-{ext}.html"):
        tail = f"{tail}-{ext}.html" if not tail.endswith(".html") else tail
    return f"{BASE}/en/saxlebi-agarakebi/iyideba-saxlebi-agarakebi/{tail}"


def _page_number(page_url: str) -> int:
    m = INDEX_RE.search(urlparse(page_url).path)
    if m:
        return max(1, int(m.group(1)))
    return 1


def with_page(page_url: str, page: int) -> str:
    """Keep the search query string and set `/indexN.html`.

    Page 1 on the live site is `search-results.html` (no `/index1`);
    page 2 is `search-results/index2.html`. Category lists use the
    same shape (`iyideba-saxlebi-agarakebi/index2.html`).
    """
    parsed = urlparse(page_url)
    path = INDEX_RE.sub(".html", parsed.path)
    if page > 1:
        if path.endswith(".html"):
            path = path[:-5] + f"/index{page}.html"
        else:
            path = path.rstrip("/") + f"/index{page}.html"
    return urlunparse(parsed._replace(path=path))


def parse_money(text: str | None) -> tuple[int | None, str | None]:
    """Return (amount, currency) from a $ / € / ₾ price blob."""
    if not text:
        return None, None
    blob = str(text).replace("\xa0", " ")
    # Prefer the unit that actually sits next to the number.
    for rx, ccy in (
        (EUR_RE, "EUR"), (PREFIX_EUR_RE, "EUR"),
        (GEL_RE, "GEL"), (PREFIX_GEL_RE, "GEL"),
        (USD_RE, "USD"), (PREFIX_USD_RE, "USD"),
    ):
        m = rx.search(blob)
        if m:
            amount = _int(m.group(1))
            return amount, ccy if amount is not None else None
    return None, None


def priced_row(amount: int | None, currency: str | None) -> dict[str, Any]:
    """Prefer the portal's euro figure; convert USD/GEL with documented rates."""
    raw: dict[str, Any] = {"country": "Georgia"}
    if amount is None or not currency:
        return {"price": None, "currency": "EUR", "raw_fields": raw}
    if currency == "EUR":
        raw.update({
            "price_eur": amount,
            "price_source": "portal_eur",
        })
        return {"price": amount, "currency": "EUR", "raw_fields": raw}
    if currency == "USD":
        eur = usd_to_eur(amount)
        raw.update({
            "price_usd": amount,
            "price_eur": eur,
            "price_source": "usd_converted",
            "fx_rate": USD_TO_EUR,
            "fx_source": FX_SOURCE,
        })
        return {"price": eur, "currency": "EUR", "raw_fields": raw}
    if currency == "GEL":
        eur = gel_to_eur(amount)
        raw.update({
            "price_gel": amount,
            "price_eur": eur,
            "price_source": "gel_converted",
            "fx_rate": GEL_TO_EUR,
            "fx_gel_per_eur": GEL_PER_EUR,
            "fx_source": FX_SOURCE,
        })
        return {"price": eur, "currency": "EUR", "raw_fields": raw}
    raw["price_unknown_currency"] = currency
    return {"price": None, "currency": "EUR", "raw_fields": raw}


def _split_title(title: str | None) -> tuple[str | None, str | None, int | None]:
    """'House For Sale, 3 Room, Borjomi , Timotesubani' → (place, region, rooms)."""
    if not title:
        return None, None, None
    parts = [p.strip(" ,") for p in title.split(",") if p.strip(" ,")]
    rooms = None
    loc: list[str] = []
    for part in parts:
        m = ROOMS_RE.search(part)
        if m and rooms is None:
            rooms = int(m.group(1))
            continue
        if re.search(r"for sale|for rent|lease", part, re.I):
            continue
        loc.append(part)
    if not loc:
        return None, None, rooms
    if len(loc) == 1:
        return loc[0], loc[0], rooms
    return loc[-1], loc[0], rooms


def _maybe_plot_area(living: int | None, land: int | None) -> tuple[int | None, int | None, bool]:
    """Cottage cards sometimes echo the parcel into Area with no Yard."""
    if living is not None and living >= PLOT_AREA_MIN and land is None:
        return None, living, True
    return living, land, False


def _parse_card(art) -> dict[str, Any] | None:
    cmp = art.select_one(".compare-grid-icon")
    href = ""
    title_a = art.select_one("li.title a[href]") or art.select_one("a.link-large[href]")
    if title_a is not None:
        href = title_a.get("href") or ""
    if cmp and cmp.get("data-listing-url"):
        href = href or cmp["data-listing-url"]
    ext = (cmp.get("data-listing-id") if cmp else None) or listing_id(href)
    slider = art.select_one(".listing-picture-slider[data-id]")
    if not ext and slider is not None:
        ext = slider.get("data-id")
    if not ext:
        return None
    if href and LAND_PATH_RE.search(href):
        return None
    if href and APT_PATH_RE.search(href) and not HOUSE_PATH_RE.search(href):
        return None
    if not listing_id(href):
        href = canonical_detail_url(str(ext))
    url = urljoin(BASE + "/", href)

    title = _txt(title_a) or (cmp.get("data-listing-title") if cmp else None)
    place, region, rooms = _split_title(title)
    field_spans = [_txt(s) for s in art.select("li.fields span")]
    field_spans = [s for s in field_spans if s]
    ptype = next((s for s in field_spans if re.search(r"sale|rent|house|land", s, re.I)),
                 "House For Sale")
    if field_spans:
        # City/Region is the last non-type span on the card.
        city = next((s for s in reversed(field_spans)
                     if s != ptype and not re.search(r"^house for sale$", s, re.I)), None)
        if city:
            region = city
            if place is None:
                place = city

    price_el = art.select_one(".price-tag")
    amount, ccy = parse_money(_txt(price_el))
    priced = priced_row(amount, ccy)

    beds = baths = living = None
    services = art.select_one("li.services")
    if services is not None:
        bed_el = services.select_one(".badrooms, .bedrooms")
        bath_el = services.select_one(".bathrooms")
        area_el = services.select_one(".square_feet")
        if bed_el is not None:
            beds = _int(_txt(bed_el))
        if bath_el is not None:
            baths = _int(_txt(bath_el))
        if area_el is not None:
            living = _area(_txt(area_el))
    living, land, moved = _maybe_plot_area(living, None)

    thumb = None
    img = art.select_one(".listing-picture-slider img[src]")
    if img is not None:
        src = img.get("src") or ""
        if src and "blank" not in src and not src.startswith("data:"):
            thumb = urljoin(BASE + "/", src)
    if thumb is None and cmp is not None:
        pic = cmp.get("data-listing-picture")
        if pic:
            thumb = urljoin(BASE + "/", pic)

    classes = art.get("class") or []
    raw = dict(priced.get("raw_fields") or {})
    raw["country"] = "Georgia"
    if title:
        raw["title"] = title
    if moved:
        raw["area_looks_like_plot"] = True
    blob = " ".join(x for x in (title, ptype, href) if x)
    if RENT_RE.search(blob):
        raw["maybe_rental"] = True

    return {
        "source": SOURCE,
        "external_id": str(ext),
        "url": url,
        "type": ptype,
        "place": place,
        "region": region,
        "living_m2": living,
        "land_m2": land,
        "rooms": rooms,
        "beds": beds,
        "baths": baths,
        "thumb": thumb,
        "promoted": "featured" in classes or "topvip_status" in classes
                    or "supervip" in classes,
        **priced,
        "raw_fields": raw,
    }


def _total_from_page(soup, page_url: str) -> tuple[int | None, int]:
    """(result_count, total_pages). Prefer h1 ads-found, then pager stats."""
    count = None
    h1 = soup.find("h1")
    if h1:
        m = ADS_FOUND_RE.search(_txt(h1) or "")
        if m:
            count = _int(m.group(1))
    pages = 1
    stats = soup.select_one("ul.pagination input[name=stats]")
    if stats and stats.get("value") and "|" in stats["value"]:
        try:
            pages = max(1, int(stats["value"].split("|")[-1]))
        except ValueError:
            pass
    else:
        of = soup.select_one("ul.pagination")
        if of:
            m = re.search(r"of\s+(\d+)", _txt(of) or "", re.I)
            if m:
                pages = max(1, int(m.group(1)))
    if count is not None:
        pages = max(pages, math.ceil(count / PER_PAGE) if count else 1)
    if pages < 1:
        pages = 1
    _ = page_url
    return count, pages


def parse_list(html: str, page_url: str) -> dict[str, Any]:
    soup = _soup(html)
    listings: list[dict[str, Any]] = []
    seen: set[str] = set()
    root = soup.select_one("section#listings") or soup
    for art in root.select("article.item"):
        row = _parse_card(art)
        if not row:
            continue
        ext = row["external_id"]
        if ext in seen:
            continue
        seen.add(ext)
        listings.append(row)

    _count, total_pages = _total_from_page(soup, page_url)
    current = _page_number(page_url)
    pag = soup.select_one("ul.pagination input[type=text][value]")
    if pag and str(pag.get("value", "")).isdigit():
        current = max(1, int(pag["value"]))
    next_url = with_page(page_url, current + 1) if current < total_pages else None
    return {
        "listings": listings,
        "total_pages": total_pages,
        "next_url": next_url,
    }


def _ld_blocks(soup) -> list[dict]:
    out = []
    for script in soup.find_all("script", type="application/ld+json"):
        data = _json_load((script.string or "").strip())
        for item in _as_list(data):
            if isinstance(item, dict):
                out.append(item)
    return out


def _ld_product(blocks: list[dict]) -> dict:
    for block in blocks:
        if block.get("@type") in ("Product", "Offer", "RealEstateListing"):
            return block
    return blocks[0] if blocks else {}


def _field_map(soup) -> dict[str, str]:
    out: dict[str, str] = {}
    for cell in soup.select(".listing-fields .table-cell"):
        label = _txt(cell.select_one(".name"))
        value = _txt(cell.select_one(".value"))
        cid = (cell.get("id") or "").replace("df_field_", "")
        if label and value:
            out[label] = value
        if cid and cid not in out and value:
            out[cid] = value
    return out


def _field_by_id(soup, field_id: str) -> str | None:
    cell = soup.select_one(f"#df_field_{field_id}")
    if cell is None:
        return None
    return _txt(cell.select_one(".value"))


def _photos(ld: dict, soup, page_url: str) -> list[str]:
    urls: list[str] = []
    for img in _as_list(ld.get("image")):
        if isinstance(img, str) and img.startswith("http"):
            urls.append(img)
        elif isinstance(img, dict) and img.get("url"):
            urls.append(img["url"])
    if not urls:
        for img in soup.select(".gallery img, .listing-picture-slider img, img"):
            src = img.get("src") or img.get("data-src")
            if not src or src.startswith("data:") or "blank" in src:
                continue
            if "listings" not in src and "digitaloceanspaces" not in src:
                continue
            urls.append(urljoin(page_url, src))
    seen: set[str] = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _coords(html: str) -> tuple[float | None, float | None]:
    m = LATLNG_RE.search(html)
    if not m:
        return None, None
    try:
        return float(m.group(1)), float(m.group(2))
    except ValueError:
        return None, None


def _yes_features(fields: dict[str, str]) -> list[str]:
    names = []
    for label in ("Water", "Drainage", "Electricity", "Gas", "Fireplace",
                  "Sauna", "Pool", "Loggia", "Furniture Appliances",
                  "Condition", "Status", "Storeroom"):
        val = fields.get(label)
        if not val:
            continue
        if val.lower() in ("no", "none", "-"):
            continue
        if val.lower() == "yes":
            names.append(label)
        else:
            names.append(f"{label}: {val}")
    return names


def parse_detail(html: str, url: str) -> dict[str, Any]:
    soup = _soup(html)
    ld = _ld_product(_ld_blocks(soup))
    offer = ld.get("offers") if isinstance(ld.get("offers"), dict) else {}

    ext = listing_id(url) or listing_id(ld.get("url") or "") or ld.get("sku") or ld.get("mpn")
    fav = soup.select_one("[id^=fav_]")
    if not ext and fav is not None:
        m = re.search(r"(\d+)", fav.get("id") or "")
        if m:
            ext = m.group(1)

    amount = None
    ccy = None
    if offer.get("price") is not None:
        try:
            amount = int(round(float(str(offer["price"]).replace(",", ""))))
        except (TypeError, ValueError):
            amount = _int(str(offer.get("price")))
        raw_ccy = (offer.get("priceCurrency") or "").upper()
        if raw_ccy in ("USD", "US$"):
            ccy = "USD"
        elif raw_ccy in ("EUR", "EU"):
            ccy = "EUR"
        elif raw_ccy in ("GEL", "GEL"):
            ccy = "GEL"
    if amount is None:
        amount, ccy = parse_money(_txt(soup.select_one(".price-tag, #df_field_price")))

    priced = priced_row(amount, ccy)
    fields = _field_map(soup)
    living = _area(fields.get("Area") or _field_by_id(soup, "square_feet"))
    land = _area(fields.get("Yard Area") or _field_by_id(soup, "yard_square_feet"))
    living, land, moved = _maybe_plot_area(living, land)

    rooms = None
    rm = ROOMS_RE.search(fields.get("Rooms") or "")
    if rm:
        rooms = int(rm.group(1))
    beds = _int(fields.get("Bedrooms"))
    baths = _int(fields.get("Bathrooms"))

    place = fields.get("District / Village") or fields.get("mdebareoba_level2")
    region = fields.get("City/Region") or fields.get("mdebareoba")
    if place:
        place = place.strip()
    if region:
        region = region.strip()
    if not place or not region:
        t_place, t_region, t_rooms = _split_title(
            _txt(soup.find("h1")) or ld.get("name")
        )
        place = place or t_place
        region = region or t_region
        rooms = rooms or t_rooms

    ptype = (ld.get("brand") or {}).get("name") if isinstance(ld.get("brand"), dict) else None
    ptype = ptype or fields.get("Type of property") or "House For Sale"

    desc = fields.get("Description") or fields.get("additional_information") or ld.get("description")
    title_ka = fields.get("Title")
    if title_ka and desc and title_ka not in desc:
        desc = f"{title_ka}\n{desc}"
    elif title_ka and not desc:
        desc = title_ka

    lat, lon = _coords(html)
    photos = _photos(ld, soup, url)
    amenities = _yes_features(fields)
    seller = _txt(soup.select_one(".seller-info"))
    agent = None
    if seller:
        agent = re.sub(r"\s+", " ", seller).strip(" -") or None

    raw = dict(priced.get("raw_fields") or {})
    raw["country"] = "Georgia"
    if fields:
        keep = {k: v for k, v in fields.items()
                if k in ("Status", "Condition", "Cadastral code", "Reference Number",
                         "Title", "Kitchen Type", "Building material",
                         "Number of Floors", "Ceiling height", "Posted")}
        if keep:
            raw["characteristics"] = keep
    if moved:
        raw["area_looks_like_plot"] = True
    ref = fields.get("Reference Number")
    cadastral = fields.get("Cadastral code")
    if cadastral:
        raw["cadastral_code"] = cadastral
    blob = " ".join(x for x in (desc, title_ka, ld.get("name"), ptype) if x)
    if AG_LAND_RE.search(blob) or (fields.get("land_type_data") or "").lower().find("agricultural") >= 0:
        raw["agricultural_land"] = True
        raw["ownership_note"] = OWNERSHIP_NOTE
    elif HOUSEHOLD_PLOT_RE.search(blob):
        raw["household_plot"] = True
        raw["ownership_note"] = OWNERSHIP_NOTE
    else:
        raw["ownership_note"] = OWNERSHIP_NOTE
    if RENT_RE.search(blob):
        raw["maybe_rental"] = True

    out: dict[str, Any] = {
        "source": SOURCE,
        "external_id": str(ext) if ext else None,
        "url": (offer.get("url") or ld.get("url") or url),
        "type": ptype,
        "place": place,
        "region": region,
        "living_m2": living,
        "land_m2": land,
        "rooms": rooms,
        "bedrooms": beds,
        "baths": baths,
        "description": desc,
        "features": ", ".join(amenities) if amenities else None,
        "photos": photos or None,
        "lat": lat,
        "lon": lon,
        "reference": ref,
        "agent": agent,
        **priced,
        "raw_fields": raw,
    }
    return out
