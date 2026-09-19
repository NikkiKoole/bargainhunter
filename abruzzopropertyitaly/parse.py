"""Parsers for abruzzopropertyitaly.com list and detail pages.

Public EN HTML. The search CMS uses a tilde path, not a query string:
`/property-search~for=1,minprice=0,maxprice=100000,order=priceasc,do=search`.
GET of that URL returns the filtered list (verified 2026-09-19: 155 ≤€100k).
A `?minprice=` query on `/property-search` is ignored. The site's pager
links drop the price filter (`~page=N,for=1`), so we keep the filters
ourselves and set `page=` — the site is 0-based (page 1 omits it).

List cards are `.results-list-wrap`. Detail URLs are
`/property-search~action=detail,pid={pid}`. `external_id` is that pid.
Prices are euro asking prices; a leftover "PCM" label on sale pages is
ignored.
"""
from __future__ import annotations

import math
import re
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from .http import BASE

SOURCE = "abruzzopropertyitaly"
PER_PAGE = 50  # live search pages, verified 2026-09-19

PID_HREF_RE = re.compile(r"pid=(\d+)", re.I)
FOUND_RE = re.compile(r"Search results:\s*([\d,]+)\s+propert", re.I)
EUR_RE = re.compile(r"€\s*([\d.,\s]+)", re.I)
REF_RE = re.compile(r"Ref:\s*(\d+)", re.I)
PAGE_RE = re.compile(r"(?:^|[~,])page=(\d+)", re.I)
SQM_RE = re.compile(r"([\d]+(?:[.,]\d+)?)\s*(?:SQM|sqm|sq\.?\s*m|m²)", re.I)
LATLNG_RE = re.compile(
    r"google\.maps\.LatLng\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)"
)
ICON_COUNTS_RE = re.compile(
    r"(Studio|\d+)\s*x\s+(\d+)\s*x\s+(\d+)\s*x\s+"
    r"([\d.]+)\s*SQM\s+([\d.]+)\s*SQM",
    re.I,
)

# First-hit wins; longer phrases first.
TYPE_HINTS = (
    ("country house", "country house"),
    ("farmhouse", "farmhouse"),
    ("townhouse", "townhouse"),
    ("semi detached", "semi detached"),
    ("bed & breakfast", "bed & breakfast"),
    ("agriturismo", "agriturismo"),
    ("studio apartment", "studio"),
    ("studio", "studio"),
    ("apartment", "apartment"),
    ("restoration", "restoration"),
    ("villa", "villa"),
    ("ruins", "ruins"),
    ("ruin", "ruins"),
    ("borgo", "borgo"),
    ("hotel", "hotel"),
    ("restaurant", "restaurant"),
    ("land", "land"),
    ("farm", "farm"),
    ("house", "house"),
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


def _area_number(text: str | None) -> int | None:
    """'120.00 SQM' / '0.00 sqm' → 120 / None. Zero means 'not stated'."""
    if not text:
        return None
    m = SQM_RE.search(str(text).replace("\xa0", " "))
    if not m:
        return None
    try:
        value = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    if value <= 0:
        return None
    return int(round(value))


def parse_eur(text: str | None) -> int | None:
    if not text:
        return None
    m = EUR_RE.search(str(text).replace("\xa0", " "))
    return _int(m.group(1)) if m else None


def _listing_id(href: str | None) -> str | None:
    if not href:
        return None
    m = PID_HREF_RE.search(href)
    return m.group(1) if m else None


def canonical_detail_url(pid: str) -> str:
    return f"{BASE}/property-search~action=detail,pid={pid}"


def _tilde_parts(url: str) -> tuple[str, list[tuple[str, str]]]:
    """Split `/path~k=v,k=v` into (path, [(k, v), ...])."""
    parsed = urlparse(url)
    path = parsed.path
    if "~" not in path:
        return path or "/property-search", []
    base, rest = path.split("~", 1)
    pairs: list[tuple[str, str]] = []
    for part in rest.split(","):
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key:
            pairs.append((key, value))
    return base or "/property-search", pairs


def _tilde_url(base: str, pairs: list[tuple[str, str]]) -> str:
    if not pairs:
        return urljoin(BASE + "/", base.lstrip("/"))
    rest = ",".join(f"{k}={v}" for k, v in pairs)
    path = f"{base}~{rest}"
    return urlunparse(urlparse(BASE)._replace(path=path, query="", fragment=""))


def _page_index(page_url: str) -> int:
    """0-based page index as the site uses it."""
    _base, pairs = _tilde_parts(page_url)
    for key, value in pairs:
        if key == "page":
            try:
                return max(0, int(value))
            except ValueError:
                return 0
    return 0


def _page_number(page_url: str) -> int:
    """1-based page number for scraper pagination."""
    return _page_index(page_url) + 1


def with_page(page_url: str, page: int) -> str:
    """Keep the tilde filters and set the 0-based page.

    `page` is 1-based (same as the other adapters). Page 1 omits `page=`
    so the cache key matches the URL the site actually serves for a
    fresh search. Do not follow the site's `~page=N,for=1` pager — that
    drops minprice/maxprice.
    """
    base, pairs = _tilde_parts(page_url)
    pairs = [(k, v) for k, v in pairs if k != "page"]
    if page > 1:
        pairs.append(("page", str(page - 1)))
    if not base.startswith("/"):
        base = "/" + base
    return _tilde_url(base, pairs)


def _split_address(text: str | None) -> dict[str, str | None]:
    """'Prezza, L'Aquila, Abruzzo, AQ67030' → town / province / region / postcode."""
    out: dict[str, str | None] = {
        "place": None, "province": None, "region": None, "postcode": None,
    }
    if not text:
        return out
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        return out
    # Last token is often a postcode (AQ67030) or "AQ67030 For sale" if
    # a parent picked up the status badge.
    last = re.sub(r"\s+For sale\s*$", "", parts[-1], flags=re.I).strip()
    m = re.match(r"^([A-Z]{2}\d{5})$", last)
    if m:
        out["postcode"] = m.group(1)
        parts = parts[:-1]
    if parts:
        out["place"] = parts[0]
    if len(parts) >= 2:
        out["province"] = parts[1]
    if len(parts) >= 3:
        out["region"] = parts[2]
    return out


def _type_from_text(text: str | None) -> str | None:
    if not text:
        return None
    low = text.lower()
    for needle, label in TYPE_HINTS:
        if needle in low:
            return label
    return None


def _card_row(box, page_url: str) -> dict[str, Any] | None:
    link = box.select_one("a[href*='pid=']")
    href = urljoin(page_url, link.get("href") or "") if link else None
    ext = _listing_id(href)
    if not ext:
        return None

    price_el = box.select_one(".results-list-price")
    addr_el = box.select_one(".results-list-address")
    price_blob = _txt(price_el) or ""
    addr_blob = _txt(addr_el)
    # Address is nested inside the price heading; don't let "For sale"
    # or the town line pollute the euro parse.
    price = parse_eur(price_blob)
    if price is None:
        price = parse_eur(_txt(box))

    loc = _split_address(addr_blob)
    if not loc["place"] and price_blob:
        # "Ref: 3223 | €12,000 Prezza, L'Aquila, Abruzzo, AQ67030 For sale"
        m = re.search(r"€\s*[\d.,\s]+(.+?)(?:For sale)?$", price_blob)
        if m:
            loc = _split_address(m.group(1))

    beds = None
    bn = box.select_one(".bednumber")
    if bn:
        beds = _int(_txt(bn))  # "Studio" → None

    baths = living = land = None
    icons = box.select_one(".results-list-icons")
    icon_text = _txt(icons) or ""
    m = ICON_COUNTS_RE.search(icon_text)
    if m:
        if beds is None and m.group(1).isdigit():
            beds = int(m.group(1))
        baths = int(m.group(2))
        living = _area_number(m.group(4) + " SQM")
        land = _area_number(m.group(5) + " SQM")

    snippet = _txt(box.select_one(".results-list-description"))
    ptype = _type_from_text(snippet) or _type_from_text(icon_text)
    if bn and (_txt(bn) or "").lower() == "studio":
        ptype = "studio"

    img = box.select_one(".results-list-img img[src], img[src]")
    thumb = urljoin(BASE + "/", img["src"]) if img and img.get("src") else None

    raw: dict[str, Any] = {"listing_id": ext, "country": "Italy"}
    if loc["postcode"]:
        raw["postcode"] = loc["postcode"]
    if loc["region"]:
        raw["region"] = loc["region"]
    if loc["province"]:
        raw["province"] = loc["province"]
    ref_m = REF_RE.search(price_blob)
    reference = ref_m.group(1) if ref_m else ext

    return {
        "source": SOURCE,
        "external_id": ext,
        "id": ext,
        "url": canonical_detail_url(ext),
        "type": ptype,
        "place": loc["place"],
        "region": loc["province"],
        "dept_nl": loc["region"],
        "price": price,
        "currency": "EUR",
        "beds": beds,
        "bedrooms": beds,
        "baths": baths,
        "living_m2": living,
        "land_m2": land,
        "reference": reference,
        "thumb": thumb,
        "snippet": snippet,
        "promoted": False,
        "raw_fields": raw,
    }


def _total_pages(s: BeautifulSoup, n_cards: int) -> int:
    last = 1
    blob = _txt(s.select_one(".results-count")) or ""
    m = FOUND_RE.search(blob) or FOUND_RE.search(s.get_text(" ", strip=True))
    if m:
        found = int(m.group(1).replace(",", ""))
        if found:
            last = max(last, max(1, math.ceil(found / PER_PAGE)))
    for a in s.select(".results-pagination a[href], a[href]"):
        href = a.get("href") or ""
        pm = PAGE_RE.search(href)
        if pm:
            last = max(last, int(pm.group(1)) + 1)
    if last == 1 and n_cards >= PER_PAGE:
        last = 2
    return last


def parse_list(html: str, page_url: str) -> dict[str, Any]:
    s = _soup(html)
    listings, seen = [], set()
    for box in s.select(".results-list-wrap"):
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

def _icon_map(s: BeautifulSoup) -> dict[str, str]:
    """First `.details-icons` block → {bed, bath, type, living, land, …}."""
    out: dict[str, str] = {}
    block = s.select_one(".details-icons")
    if not block:
        return out
    rows = []
    for div in block.find_all("div", recursive=False):
        text = _txt(div)
        if text:
            rows.append((div, text))
    if not rows:
        return out
    # First icon-bed row is the property type ("Townhouse" / "Land").
    typed = False
    for div, text in rows:
        classes = []
        for span in div.find_all("span"):
            classes.extend(span.get("class") or [])
        low = text.lower()
        if "icon-bed" in classes and not typed and "bedroom" not in low:
            out["type"] = text
            typed = True
            continue
        if "bedroom" in low:
            out["bedrooms"] = text
        elif "bathroom" in low:
            out["baths"] = text
        elif "reception" in low:
            out["receptions"] = text
        elif "icon-size" in classes or (
            "sqm" in low and "living" not in out and "icon-tree" not in classes
        ):
            if "living" not in out:
                out["living"] = text
            else:
                out.setdefault("land", text)
        elif "icon-tree" in classes:
            out["garden"] = text
        elif "parking" in low:
            out["parking"] = text
        elif "sqm" in low:
            out.setdefault("land", text)
    return out


def _photos(s: BeautifulSoup, page_url: str) -> list[str]:
    photos, seen = [], set()
    for a in s.select(".fotorama a[href], #details-photo a[href]"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        abs_url = urljoin(page_url, href)
        key = re.sub(r"[?].*$", "", abs_url)
        if key in seen:
            continue
        seen.add(key)
        photos.append(abs_url)
    if photos:
        return photos
    for img in s.select(".fotorama img[src], #details-photo img[src]"):
        src = (img.get("src") or "").strip()
        if not src:
            continue
        abs_url = urljoin(page_url, src)
        # Prefer the large/ sibling of a thumbnail if that's all we have.
        abs_url = abs_url.replace("/thumbnails/", "/large/")
        key = re.sub(r"[?].*$", "", abs_url)
        if key in seen:
            continue
        seen.add(key)
        photos.append(abs_url)
    return photos


def _lat_lon(html: str) -> tuple[float | None, float | None]:
    m = LATLNG_RE.search(html)
    if not m:
        return None, None
    try:
        return float(m.group(1)), float(m.group(2))
    except ValueError:
        return None, None


def parse_detail(html: str, url: str) -> dict[str, Any]:
    s = _soup(html)
    out: dict[str, Any] = {
        "url": url, "source": SOURCE, "agent": "Abruzzo Property Italy",
        "currency": "EUR",
    }
    raw: dict[str, Any] = {"country": "Italy"}

    ext = _listing_id(url)
    addr_el = s.select_one(".details-address1")
    addr_blob = _txt(addr_el) or ""
    if not ext:
        ext = _listing_id(addr_blob)
    if not ext:
        m = REF_RE.search(addr_blob) or REF_RE.search(s.get_text(" ", strip=True))
        if m:
            ext = m.group(1)
    if ext:
        out["url"] = canonical_detail_url(ext)
        out["external_id"] = ext
        out["reference"] = ext
        raw["listing_id"] = ext

    price = parse_eur(addr_blob) or parse_eur(_txt(s.select_one(".details-address1")))
    if price is None:
        price = parse_eur(s.get_text(" ", strip=True)[:800])
    out["price"] = price
    if re.search(r"\bPCM\b", addr_blob):
        raw["pcm_label"] = True  # leftover rental template; still a sale

    place_el = s.select_one("h1.detail-price") or s.select_one("h1")
    loc = _split_address(_txt(place_el))
    if loc["place"]:
        out["place"] = loc["place"]
    if loc["province"]:
        out["region"] = loc["province"]
    if loc["region"]:
        out["dept_nl"] = loc["region"]
        raw["region"] = loc["region"]
    if loc["province"]:
        raw["province"] = loc["province"]

    icons = _icon_map(s)
    raw.update({f"icon_{k}": v for k, v in icons.items()})
    if icons.get("type"):
        out["type"] = icons["type"]
    else:
        title = _txt(s.title) or ""
        # "2 Bed Townhouse L'Aquila Prezza AQ67030 - Abruzzo Property Italy"
        out["type"] = _type_from_text(title)
    if icons.get("bedrooms"):
        if re.search(r"studio", icons["bedrooms"], re.I):
            out["bedrooms"] = None
            out["type"] = out.get("type") or "studio"
        else:
            out["bedrooms"] = _int(icons["bedrooms"])
    if icons.get("baths"):
        out["baths"] = _int(icons["baths"])
    out["living_m2"] = _area_number(icons.get("living"))
    out["land_m2"] = _area_number(icons.get("land"))

    lat, lon = _lat_lon(html)
    if lat is not None:
        out["lat"] = lat
    if lon is not None:
        out["lon"] = lon

    desc = None
    block = s.select_one(".details-description")
    if block:
        desc = block.get_text("\n", strip=True)
        desc = re.sub(r"\n{3,}", "\n\n", desc)
    feat = _txt(s.select_one(".details-features"))
    out["features"] = feat
    out["description"] = desc
    out["snippet"] = (desc.split("\n", 1)[0] if desc else None) or _txt(place_el)

    photos = _photos(s, url)
    out["photos"] = photos
    if photos:
        out["thumb"] = photos[0]

    epc = _txt(s.select_one(".details-epc"))
    if epc and "no epc" not in epc.lower():
        raw["epc"] = epc

    out["raw_fields"] = raw
    return out
