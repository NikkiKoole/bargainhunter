"""Parsers for bulgarianproperties.com list and detail pages.

A–B tier EN foreigner agency. Public browse/category HTML only.

robots.txt Disallow includes `/*search`, `*minprice=`, `*maxprice=`,
`*page=`, `*ID=`, `/*srtby=`, `/*hw_page`, `/*index0.html`. Those are
search-query / pager-query paths — we never write them into
`searches.json` and we never follow `/Search/index.php`. Pagination on
the allowed browse URLs is a **path suffix**:

    /rural_houses.html          page 1
    /rural_houses1.html         page 2
    /Houses_in_Bulgaria/index.html
    /Houses_in_Bulgaria/index1.html

`Disallow: /*index0.html` is why page 1 stays `index.html`, not
`index0`. Cards are `div.component-property-item` (30 per page).
Detail slugs are `ADxxxxxBG_….html` (verified 2026-09-19).

The portal prints **EUR** by default (`€ 10 900` / JSON-LD
`priceCurrency: EUR`) and a JS switcher adds GBP / USD. We store euro
and keep the extras in `raw_fields`. Complements ok_bulgaria (different
inventory).
"""
from __future__ import annotations

import json
import math
import re
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from .fx import FX_SOURCE, GBP_TO_EUR, USD_TO_EUR, gbp_to_eur, usd_to_eur
from .http import BASE

SOURCE = "bulgarianproperties"
PER_PAGE = 30  # live cards-per-page on browse lists, verified 2026-09-19
PLOT_AREA_MIN = 1000  # m²: Area this large with no garden is treated as land

ID_RE = re.compile(r"/AD(\d+)BG(?:_|\.|$)", re.I)
NUMERIC_ID_RE = re.compile(r"^(\d+)$")
INDEX_PAGE_RE = re.compile(r"/index(\d+)\.html$", re.I)
STEM_PAGE_RE = re.compile(r"(\d+)\.html$", re.I)
COUNT_RE = re.compile(r"([\d\s.,]+)\s+from\s+([\d\s.,]+)\s+results", re.I)
AREA_RE = re.compile(
    r"(?:Area|Living)[:\s]*([\d][\d\s.,]*)\s*(?:m²|m2|m\b)",
    re.I,
)
GARDEN_RE = re.compile(
    r"(?:Garden|Yard|Plot|Land)[:\s]*([\d][\d\s.,]*)\s*(?:m²|m2|m\b)",
    re.I,
)
SQM_RE = re.compile(r"([\d][\d\s.,]*)\s*(?:sq\.?\s*m|sqm|m²|m2)\b", re.I)
EUR_RE = re.compile(r"(?:€|&euro;)\s*([\d][\d\s.,]*)", re.I)
GBP_RE = re.compile(r"(?:£|&pound;)\s*([\d][\d\s.,]*)", re.I)
USD_RE = re.compile(r"(?:\$|USD)\s*([\d][\d\s.,]*)", re.I)
CH_EUR_RE = re.compile(
    r"ch_currency\(\s*['\"][^'\"]*?(?:€|&euro;)\s*([\d][\d\s.,]*)",
    re.I,
)
CH_GBP_RE = re.compile(
    r"ch_currency\(\s*['\"][^'\"]*?(?:£|&pound;)\s*([\d][\d\s.,]*)",
    re.I,
)
CH_USD_RE = re.compile(
    r"ch_currency\(\s*['\"][^'\"]*?(?:\$|USD)\s*([\d][\d\s.,]*)",
    re.I,
)
MAP_Q_RE = re.compile(
    r"maps\?q=(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)",
    re.I,
)
RENT_RE = re.compile(r"\b(?:/month|per month|for rent|to let|pcm)\b", re.I)
LAND_TYPES = {
    "regulated plot",
    "investment land",
    "agricultural land",
    "land",
    "plot",
    "building plot",
}

# Search/query paths robots.txt Disallow — never emit as next_url.
DISALLOWED_PATH_RE = re.compile(
    r"/Search/|search\?|[?&](?:page|minprice|maxprice|ID|srtby|hw_page)=",
    re.I,
)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _int(text: str | None) -> int | None:
    if not text:
        return None
    cleaned = str(text).replace("\xa0", " ").replace(",", "").replace(" ", "")
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
    blob = str(text).replace("\xa0", " ")
    m = SQM_RE.search(blob)
    if m:
        return _int(m.group(1))
    return _int(blob)


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
    """Return ADxxxxxBG from a detail URL, or None."""
    if not href:
        return None
    m = ID_RE.search(href)
    if m:
        return f"AD{m.group(1)}BG"
    return None


def numeric_id(ext: str | None) -> str | None:
    if not ext:
        return None
    m = re.search(r"AD(\d+)BG", ext, re.I)
    if m:
        return m.group(1)
    if NUMERIC_ID_RE.match(str(ext)):
        return str(ext)
    return None


def canonical_detail_url(ext: str, href: str | None = None) -> str:
    if href:
        joined = urljoin(BASE + "/", href)
        if listing_id(joined):
            return joined.split("#")[0]
    num = numeric_id(ext) or ext
    slug = ext if str(ext).upper().startswith("AD") else f"AD{num}BG"
    return f"{BASE}/Houses_in_Bulgaria/{slug}.html"


def _page_number(page_url: str) -> int:
    path = urlparse(page_url).path
    m = INDEX_PAGE_RE.search(path)
    if m:
        return max(1, int(m.group(1)) + 1)
    if path.endswith("/index.html") or path.endswith("index.html"):
        return 1
    m = STEM_PAGE_RE.search(path)
    if m:
        return max(1, int(m.group(1)) + 1)
    return 1


def with_page(page_url: str, page: int) -> str:
    """Keep the browse path and set the suffix page.

    Page 1 is the bare `.html` / `index.html` the site actually serves
    (never `index0.html` — robots Disallow). Page 2 is `stem1.html` or
    `index1.html`. Query-string `?page=` is Disallow'd — do not emit it.
    """
    parsed = urlparse(page_url)
    path = parsed.path
    if INDEX_PAGE_RE.search(path):
        path = INDEX_PAGE_RE.sub("/index.html", path)
    elif not path.endswith("/index.html") and STEM_PAGE_RE.search(path):
        path = STEM_PAGE_RE.sub(".html", path)
    if page <= 1:
        new_path = path
    elif path.endswith("/index.html"):
        new_path = path[:-10] + f"index{page - 1}.html"
    elif path.endswith("index.html"):
        new_path = path[:-10] + f"index{page - 1}.html"
    elif path.endswith(".html"):
        new_path = path[:-5] + f"{page - 1}.html"
    else:
        new_path = path.rstrip("/") + f"{page - 1}.html"
    if new_path.endswith("index0.html"):
        new_path = new_path[:-11] + "index.html"
    return urlunparse(parsed._replace(path=new_path, query=""))


def parse_money(text: str | None) -> tuple[int | None, str | None]:
    """'€ 10 900' / '£ 9 375' / '$ 12 512' → (amount, currency)."""
    if not text:
        return None, None
    blob = str(text).replace("\xa0", " ")
    blob = blob.replace("&euro;", "€").replace("&pound;", "£")
    for rx, ccy in ((EUR_RE, "EUR"), (GBP_RE, "GBP"), (USD_RE, "USD")):
        m = rx.search(blob)
        if m:
            amount = _int(m.group(1))
            return amount, ccy if amount is not None else None
    return None, None


def priced_row(eur: int | None, gbp: int | None = None,
               usd: int | None = None) -> dict[str, Any]:
    """Prefer the portal's euro figure; convert GBP/USD only if that's missing."""
    raw: dict[str, Any] = {"country": "Bulgaria"}
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
        converted = gbp_to_eur(gbp)
        raw.update({
            "price_source": "gbp_converted",
            "fx_rate": GBP_TO_EUR,
            "fx_source": FX_SOURCE,
            "price_eur": converted,
        })
        return {"price": converted, "currency": "EUR", "raw_fields": raw}
    if usd is not None:
        converted = usd_to_eur(usd)
        raw.update({
            "price_source": "usd_converted",
            "fx_rate": USD_TO_EUR,
            "fx_source": FX_SOURCE,
            "price_eur": converted,
        })
        return {"price": converted, "currency": "EUR", "raw_fields": raw}
    return {"price": None, "currency": "EUR", "raw_fields": raw}


def _card_prices(box) -> tuple[int | None, int | None, int | None, int | None]:
    """(asking_eur, old_eur, gbp, usd) from a list card."""
    asking = old = gbp = usd = None
    new_el = box.select_one(".new-price")
    old_el = box.select_one(".old-price")
    if new_el is not None:
        amount, ccy = parse_money(_txt(new_el))
        if ccy == "EUR":
            asking = amount
        elif ccy == "GBP":
            gbp = amount
        elif ccy == "USD":
            usd = amount
    if old_el is not None:
        amount, ccy = parse_money(_txt(old_el))
        if ccy == "EUR":
            old = amount
    if asking is None:
        for el in box.select(".regular-price, .property-prices"):
            amount, ccy = parse_money(_txt(el))
            if amount is None:
                continue
            if ccy == "EUR" and asking is None:
                asking = amount
            elif ccy == "GBP" and gbp is None:
                gbp = amount
            elif ccy == "USD" and usd is None:
                usd = amount
            if asking is not None:
                break
    return asking, old, gbp, usd


def _card_areas(box) -> tuple[int | None, int | None]:
    size = _txt(box.select_one(".size")) or ""
    living = land = None
    gm = GARDEN_RE.search(size)
    if gm:
        land = _int(gm.group(1))
    am = AREA_RE.search(size)
    if am:
        living = _int(am.group(1))
    return living, land


def _maybe_plot_area(living: int | None, land: int | None,
                     ptype: str | None) -> tuple[int | None, int | None, bool]:
    kind = (ptype or "").strip().lower()
    if kind in LAND_TYPES or kind.startswith("land"):
        if land is None and living is not None:
            return None, living, True
        return None, land, False
    if living is not None and living >= PLOT_AREA_MIN and land is None:
        return None, living, True
    return living, land, False


def _place_from_location(loc) -> tuple[str | None, str | None]:
    """Return (village/place, town/region).

    Cards mix two orders: ``Near Town, Village`` and ``Village, Town``.
    A ``Near …`` label (or `/Properties_near_` href) is always the town.
    """
    if loc is None:
        return None, None
    blob = _txt(loc) or ""
    near_name = None
    others: list[str] = []
    for a in loc.select("a"):
        text = _txt(a) or ""
        href = a.get("href") or ""
        stripped = re.sub(r"^\s*Near\s+", "", text, flags=re.I).strip()
        is_near = bool(re.match(r"^\s*Near\s+", text, re.I)) or "/Properties_near_" in href
        if is_near and stripped:
            near_name = near_name or stripped
        elif stripped:
            others.append(stripped)
    if near_name is None:
        m = re.search(r"\bNear\s+([^,]+)", blob, re.I)
        if m:
            near_name = m.group(1).strip()
    parts = [p.strip(" ,") for p in re.split(r"\s*,\s*", blob) if p.strip(" ,")]
    cleaned = [re.sub(r"^\s*Near\s+", "", p, flags=re.I).strip() for p in parts]
    cleaned = [p for p in cleaned if p]

    def _pair(names: list[str]) -> tuple[str | None, str | None]:
        if not names:
            return None, None
        if len(names) == 1:
            return names[0], names[0]
        last, first = names[-1], names[0]
        if re.match(r"^(Quarter|District|Area)\b", last, re.I):
            return last, first
        return first, last

    if near_name:
        place = next((c for c in others + cleaned if c.lower() != near_name.lower()), None)
        return place, near_name
    place, region = _pair(others)
    if place and region:
        return place, region
    return _pair(cleaned)


def _parse_card(box) -> dict[str, Any] | None:
    link = box.select_one("a.title[href], a.image[href], a[href*='/AD']")
    href = (link.get("href") if link else "") or ""
    ext = listing_id(href)
    num = box.get("data-preference-prop-id") or box.get("id") or box.get("data-id")
    if not ext and num and str(num).isdigit():
        ext = f"AD{num}BG"
    if not ext:
        return None
    url = canonical_detail_url(ext, href)

    title = _txt(box.select_one("a.title")) or _txt(link)
    place, region = _place_from_location(box.select_one(".location"))
    ptype = None
    type_el = box.select_one(".type a, .type")
    if type_el is not None:
        ptype = _txt(type_el.select_one("a")) or _txt(type_el)
        if ptype:
            ptype = re.sub(r"^Type of property:\s*", "", ptype, flags=re.I).strip()

    asking, old, gbp, usd = _card_prices(box)
    priced = priced_row(asking, gbp, usd)
    living, land = _card_areas(box)
    living, land, moved = _maybe_plot_area(living, land, ptype)

    thumb = None
    img = box.select_one("a.image img, img[data-src], img[src]")
    if img is not None:
        src = img.get("data-src") or img.get("src") or ""
        if src and not src.startswith("data:") and "blank" not in src:
            thumb = urljoin(BASE + "/", src)

    labels = [_txt(s) for s in box.select(".top-labels .label, .bottom-labels .label")]
    labels = [x for x in labels if x]
    snippet = _txt(box.select_one(".list-subtitle")) or _txt(box.select_one(".list-description"))
    agent = _txt(box.select_one(".broker-info .name"))

    raw = dict(priced.get("raw_fields") or {})
    raw["country"] = "Bulgaria"
    if title:
        raw["title"] = title
    if old is not None:
        raw["old_price_eur"] = old
    if moved:
        raw["area_looks_like_plot"] = True
    if any(re.search(r"reserved|sold", x, re.I) for x in labels):
        raw["reserved"] = True
    blob = " ".join(x for x in (title, ptype, snippet, " ".join(labels)) if x)
    if RENT_RE.search(blob):
        raw["maybe_rental"] = True
    if "best bargain" in " ".join(labels).lower():
        raw["best_bargain"] = True

    return {
        "source": SOURCE,
        "external_id": ext,
        "url": url,
        "type": ptype,
        "place": place,
        "region": region,
        "living_m2": living,
        "land_m2": land,
        "thumb": thumb,
        "snippet": snippet,
        "agent": agent,
        "promoted": any(re.search(r"best bargain|top offer|exclusive", x, re.I)
                        for x in labels),
        **priced,
        "raw_fields": raw,
    }


def _total_from_page(soup) -> tuple[int | None, int]:
    """(result_count, total_pages). Prefer `.pcount .count`, then pager."""
    count = None
    pages = 1
    count_el = soup.select_one(".pcount .count")
    if count_el is not None:
        m = COUNT_RE.search(_txt(count_el) or "")
        if m:
            count = _int(m.group(2))
    if count is None:
        blob = _txt(soup.select_one(".pcount")) or ""
        m = COUNT_RE.search(blob)
        if m:
            count = _int(m.group(2))
    max_page = 1
    for a in soup.select(".pcount a[data-preference-element-id]"):
        hid = a.get("data-preference-element-id") or ""
        m = re.search(r"pagination_(\d+)", hid)
        if m:
            max_page = max(max_page, int(m.group(1)))
        href = a.get("href") or ""
        max_page = max(max_page, _page_number(href))
    if count is not None:
        pages = max(1, math.ceil(count / PER_PAGE) if count else 1)
    pages = max(pages, max_page)
    return count, pages


def parse_list(html: str, page_url: str) -> dict[str, Any]:
    soup = _soup(html)
    listings: list[dict[str, Any]] = []
    seen: set[str] = set()
    root = soup.select_one(".component-list-properties-items") or soup
    for box in root.select(".component-property-item"):
        row = _parse_card(box)
        if not row:
            continue
        ext = row["external_id"]
        if ext in seen:
            continue
        seen.add(ext)
        listings.append(row)

    _count, total_pages = _total_from_page(soup)
    current = _page_number(page_url)
    next_url = None
    if current < total_pages:
        nxt = with_page(page_url, current + 1)
        if not DISALLOWED_PATH_RE.search(nxt):
            next_url = nxt
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
        if block.get("@type") in ("Product", "Offer", "RealEstateListing",
                                  "SingleFamilyResidence"):
            return block
    return blocks[0] if blocks else {}


def _field_map(soup) -> dict[str, str]:
    out: dict[str, str] = {}
    root = soup.select_one(".component-single-property-characteristic") or soup
    for cell in root.select(".characteristic"):
        label = _txt(cell.select_one(".label"))
        value = _txt(cell.select_one(".value"))
        if label and value:
            key = label.rstrip(":").strip()
            out[key] = value
    return out


def _photos(ld: dict, soup, page_url: str) -> list[str]:
    urls: list[str] = []
    for img in _as_list(ld.get("image")):
        if isinstance(img, str) and img.startswith("http"):
            urls.append(img)
        elif isinstance(img, dict) and img.get("url"):
            urls.append(img["url"])
    if not urls:
        for img in soup.select(
            ".component-single-property-head-gallery img, "
            ".component-single-property-content-gallery img, "
            ".property-gallery img"
        ):
            src = img.get("src") or img.get("data-src")
            if not src or src.startswith("data:"):
                continue
            if "property-images" not in src:
                continue
            urls.append(urljoin(page_url, src))
    seen: set[str] = set()
    out = []
    for u in urls:
        # collapse medium/thumbnail variants onto the big path when we can
        key = re.sub(r"/medium\d*/", "/big/", u)
        if key not in seen:
            seen.add(key)
            out.append(u)
    return out


def _coords(html: str) -> tuple[float | None, float | None]:
    m = MAP_Q_RE.search(html)
    if not m:
        return None, None
    try:
        lat, lon = float(m.group(1)), float(m.group(2))
    except ValueError:
        return None, None
    # Site-wide office pins are rare here; keep village-level coords.
    if abs(lat) < 0.1 and abs(lon) < 0.1:
        return None, None
    return lat, lon


def _switcher_amounts(html: str) -> tuple[int | None, int | None, int | None]:
    """EUR / GBP / USD from the detail-page ch_currency() switcher."""
    eur = gbp = usd = None
    m = CH_EUR_RE.search(html)
    if m:
        eur = _int(m.group(1))
    m = CH_GBP_RE.search(html)
    if m:
        gbp = _int(m.group(1))
    m = CH_USD_RE.search(html)
    if m:
        usd = _int(m.group(1))
    return eur, gbp, usd


def _features(fields: dict[str, str]) -> list[str]:
    names = []
    for label in ("Condition", "Furnishing", "Heating system",
                  "Type of building", "Air-conditioning system",
                  "Exposition", "Number of floors"):
        val = fields.get(label)
        if not val:
            continue
        if val.lower() in ("no", "none", "-", "no heating",
                           "no air-conditioning system"):
            continue
        names.append(f"{label}: {val}")
    return names


def parse_detail(html: str, url: str) -> dict[str, Any]:
    soup = _soup(html)
    ld = _ld_product(_ld_blocks(soup))
    offer = ld.get("offers") if isinstance(ld.get("offers"), dict) else {}

    ext = listing_id(url) or listing_id(offer.get("url") or "") or listing_id(ld.get("url") or "")
    if not ext:
        num = None
        pref = soup.select_one("[data-preference-prop-id]")
        if pref is not None:
            num = pref.get("data-preference-prop-id")
        if not num:
            m = re.search(r"\bAD(\d+)BG\b", html, re.I)
            if m:
                num = m.group(1)
        if num:
            ext = f"AD{num}BG"

    eur = gbp = usd = None
    if offer.get("price") is not None:
        try:
            eur = int(round(float(str(offer["price"]).replace(",", "").replace(" ", ""))))
        except (TypeError, ValueError):
            eur = _int(str(offer.get("price")))
        raw_ccy = (offer.get("priceCurrency") or "").upper()
        if raw_ccy in ("GBP", "GB"):
            gbp, eur = eur, None
        elif raw_ccy in ("USD", "US$"):
            usd, eur = eur, None
        elif raw_ccy and raw_ccy not in ("EUR", "EU"):
            eur = None
    sw_eur, sw_gbp, sw_usd = _switcher_amounts(html)
    if eur is None:
        eur = sw_eur
    gbp = gbp or sw_gbp
    usd = usd or sw_usd
    if eur is None:
        price_el = soup.select_one(
            ".component-single-property-price .regular-price, "
            ".component-single-property-price .new-price, "
            "#newprice"
        )
        amount, ccy = parse_money(_txt(price_el))
        if ccy == "EUR":
            eur = amount
        elif ccy == "GBP":
            gbp = gbp or amount
        elif ccy == "USD":
            usd = usd or amount
    priced = priced_row(eur, gbp, usd)

    fields = _field_map(soup)
    ptype = fields.get("Type of property")
    if ptype:
        ptype = ptype.split(",")[0].strip()
    living = _area(fields.get("Area"))
    land = _area(fields.get("Garden") or fields.get("Yard") or fields.get("Plot"))
    living, land, moved = _maybe_plot_area(living, land, ptype)

    place = region = None
    map_loc = soup.select_one(".component-single-property-map .location")
    if map_loc is not None:
        place, region = _place_from_location(map_loc)
    if not place or not region or place == region:
        gi_loc = soup.select_one("#generalInformation .location, h1 + .location")
        p2, r2 = _place_from_location(gi_loc)
        if place == region and p2 and r2 and p2 != r2:
            place, region = p2, r2
        else:
            place = place or p2
            region = region or r2

    desc = None
    text = soup.select_one("#generalInformation .text")
    if text is not None:
        desc = text.get_text("\n", strip=True)
        desc = re.sub(r"\n{3,}", "\n\n", desc)
    if not desc:
        desc = ld.get("description")

    lat, lon = _coords(html)
    photos = _photos(ld, soup, url)
    amenities = _features(fields)
    ref = fields.get("Ref. No.") or fields.get("Ref. No")
    if not ref:
        ref_el = soup.select_one(".refnom")
        blob = _txt(ref_el) or ""
        m = re.search(r"Reference number:\s*(\S+(?:\s+\S+)?)", blob, re.I)
        if m:
            ref = m.group(1).strip()
        elif blob:
            ref = re.sub(r"^Reference number:\s*", "", blob, flags=re.I).strip() or None

    agent = _txt(soup.select_one(".page-property .broker-info .name, "
                                 ".component-single-property-price .name"))
    if not agent:
        broker = soup.select_one(".page-property .broker-info, "
                                 ".component-single-property-price .broker-info")
        if broker is not None:
            agent = _txt(broker.select_one(".name")) or _txt(broker)

    raw = dict(priced.get("raw_fields") or {})
    raw["country"] = "Bulgaria"
    keep_keys = ("Condition", "Furnishing", "Heating system", "Type of building",
                 "Number of floors", "Air-conditioning system", "Exposition")
    keep = {k: fields[k] for k in keep_keys if k in fields}
    if keep:
        raw["characteristics"] = keep
    if moved:
        raw["area_looks_like_plot"] = True
    labels = [_txt(s) for s in soup.select(
        "#generalInformation .label, .component-single-property-general-information .label")]
    labels = [x for x in labels if x]
    if any(re.search(r"reserved|sold", x, re.I) for x in labels):
        raw["reserved"] = True
    avail = (offer.get("availability") or "")
    if "OutOfStock" in avail:
        raw["reserved"] = True
    blob = " ".join(x for x in (desc, ld.get("name"), ptype) if x)
    if RENT_RE.search(blob):
        raw["maybe_rental"] = True

    out: dict[str, Any] = {
        "source": SOURCE,
        "external_id": ext,
        "url": (offer.get("url") or ld.get("url") or url),
        "type": ptype,
        "place": place,
        "region": region,
        "living_m2": living,
        "land_m2": land,
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
