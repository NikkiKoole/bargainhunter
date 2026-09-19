"""Parsers for immobilier.lefigaro.fr list and detail pages.

Nuxt SSR. Department SEO paths such as
`/annonces/immobilier-vente-maison-creuse.html` are the seeds — price
query params (`?priceMax=`) were unreliable on a 2026-09-19 probe.
Facets that robots.txt names (`?option=petit_prix`, `?option=travaux`)
ride on the same path. Pagination is `?page=N` (page 1 omits it) and
stops at page 100.

List pages ship an `ItemList` JSON-LD with per-item prices plus a
`.cartouche-liste` card (often with its own Residence / House ld+json).
Detail pages are SSR `RealEstateListing` / `Product` / `House`. Currency
is EUR. Language is FR.

A datacenter GET is Cloudflare-blocked (same class of wall as Holprop).
Home IP + the HTML cache is the live path; fixtures reconstruct the
documented ld+json shape from an archive snapshot of the Creuse house
list (1 125 annonces, 2026).

`robots.txt` disallows `/api/`, `/rest/`, `/recherche/` and most query
strings under `/annonces/` except `page` and a named option list — stay
on the SEO path. Never hit those API routes.

Listings may overlap franimo.nl. Store them as `source=lefigaro`; do
not cross-source merge.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from .http import BASE

SOURCE = "lefigaro"
PER_PAGE = 24  # typical house-list page; total comes from "N annonces" / ItemList
PAGE_CEILING = 100  # portal hard stop

ID_RE = re.compile(r"/annonces/annonce-(\d+)\.html", re.I)
PAGE_RE = re.compile(r"(?:[?&])page=(\d+)", re.I)
ANNONCES_RE = re.compile(r"([\d\s\u00a0]+)\s+annonces?\b", re.I)
EUR_RE = re.compile(
    r"(?:([\d\s\u00a0.,]+)\s*€)|(?:€\s*([\d\s\u00a0.,]+))",
)
AREA_RE = re.compile(
    r"([\d][\d\s\u00a0.,]*)\s*(?:m²|m2)\b",
    re.I,
)
PIECES_RE = re.compile(r"([\d]+)\s*pi[eè]ces?\b", re.I)
CHAMBRES_RE = re.compile(r"([\d]+)\s*chambres?\b", re.I)
TERRAIN_RE = re.compile(
    r"(?:terrain|parcelle|jardin)\s*(?:de|attenant(?:e)?\s+de)?\s*"
    r"([\d][\d\s\u00a0.,]*)\s*(?:m²|m2)\b",
    re.I,
)
TERRAIN_LABEL_RE = re.compile(
    r"^(?:terrain|surface\s+(?:du\s+)?terrain|parcelle|jardin)$",
    re.I,
)
SURFACE_LABEL_RE = re.compile(
    r"^(?:surface|surface\s+habitable|habitable)$",
    re.I,
)
DPE_RE = re.compile(r"\b(?:classe[-\s]?[eé]nergie|DPE)\s*[:\s]*([A-G])\b", re.I)
GES_RE = re.compile(r"\bGES\s*[:\s]*([A-G])\b", re.I)
KWH_RE = re.compile(r"([\d][\d\s.,]*)\s*kWh", re.I)
POSTAL_RE = re.compile(r"\b(\d{5})\b")
DEPT_CODE_RE = re.compile(r"\((\d{2,3})\)")
REF_RE = re.compile(
    r"(?:r[eé]f(?:[eé]rence|\.)?|fiche\s*n[°o])\s*[:\s]*([A-Za-z0-9_./-]+)",
    re.I,
)
LOCATION_RE = re.compile(r"\b(?:location|louer|\bà louer\b|\blouer\b)\b", re.I)
AGENCY_PATH_RE = re.compile(r"/annonces/agence", re.I)
PRIX_SUR_DEMANDE_RE = re.compile(
    r"prix\s+(?:sur\s+demande|nous\s+consulter)|nous\s+consulter",
    re.I,
)

# SEO slug → (French name, INSEE-style dept code). Enough for the seeds.
DEPT_SLUG = {
    "creuse": ("Creuse", "23"),
    "nievre": ("Nièvre", "58"),
    "haute-vienne": ("Haute-Vienne", "87"),
    "allier": ("Allier", "03"),
    "cantal": ("Cantal", "15"),
    "indre": ("Indre", "36"),
    "cher": ("Cher", "18"),
    "france": (None, None),
}

TYPE_ALIASES = {
    "house": "Maison",
    "singlefamilyresidence": "Maison",
    "residence": "Maison",
    "accommodation": "Maison",
    "maison": "Maison",
    "propriete": "Propriété",
    "propriété": "Propriété",
    "apartment": "Appartement",
    "appartement": "Appartement",
    "land": "Terrain",
    "terrain": "Terrain",
}


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _txt(node) -> str | None:
    if node is None:
        return None
    t = node.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", t) or None


def _int(text: str | None) -> int | None:
    """Parse a displayed integer. Spaces/nbsp are thousands; ',' is decimal.

    '37 500' / '37.500' / '1 125' → 37500 / 37500 / 1125.
    A leftover '73,5' becomes 74.
    """
    if text is None:
        return None
    raw = str(text).replace("\xa0", " ").strip()
    if not raw:
        return None
    if "," in raw and "." not in raw:
        raw = raw.replace(" ", "").replace(",", ".")
        try:
            return int(round(float(raw)))
        except ValueError:
            return None
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else None


def _float(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        return float(str(text).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def _json_load(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _as_list(data: Any) -> list:
    if data is None:
        return []
    return data if isinstance(data, list) else [data]


def _types_of(block: dict) -> set[str]:
    raw = block.get("@type")
    if raw is None:
        return set()
    if isinstance(raw, list):
        return {str(t).lower() for t in raw}
    return {str(raw).lower()}


def listing_id(href: str | None) -> str | None:
    if not href:
        return None
    m = ID_RE.search(str(href))
    return m.group(1) if m else None


def canonical_detail_url(ext: str) -> str:
    return f"{BASE}/annonces/annonce-{ext}.html"


def _page_number(page_url: str) -> int:
    m = PAGE_RE.search(page_url)
    if m:
        return max(1, int(m.group(1)))
    return 1


def with_page(page_url: str, page: int) -> str:
    """Keep the SEO path and `option=` facet; set `page=N`.

    Page 1 on the live site omits `page=`, so we do too — cache keys stay
    a function of the URL the site actually serves.
    """
    parsed = urlparse(page_url)
    q = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
         if k.lower() != "page"]
    if page > 1:
        q.append(("page", str(page)))
    return urlunparse(parsed._replace(query=urlencode(q)))


def is_blocked(html: str) -> bool:
    """Cloudflare challenge / hard block — not a list page."""
    if not html:
        return False
    blob = html[:8000]
    return (
        "cf-error-details" in blob
        or "Sorry, you have been blocked" in blob
        or "Just a moment" in blob
        or "cdn-cgi/challenge" in blob
    )


def parse_eur(text: str | None) -> int | None:
    if not text:
        return None
    blob = str(text).replace("\xa0", " ")
    if PRIX_SUR_DEMANDE_RE.search(blob):
        return None
    m = EUR_RE.search(blob)
    if not m:
        return None
    return _int(m.group(1) or m.group(2))


def _area(text: str | None) -> int | None:
    if not text:
        return None
    m = AREA_RE.search(str(text).replace("\xa0", " "))
    if not m:
        return None
    return _int(m.group(1))


def _ld_blocks(soup) -> list[dict]:
    out: list[dict] = []
    for script in soup.find_all("script", type="application/ld+json"):
        data = _json_load((script.string or script.get_text() or "").strip())
        for item in _as_list(data):
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("@graph"), list):
                out.extend(x for x in item["@graph"] if isinstance(x, dict))
            else:
                out.append(item)
    return out


def _qty(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return _int(value.get("value"))
    return _int(value)


def _offer_price(block: dict) -> int | None:
    offers = block.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if not isinstance(offers, dict):
        offers = {}
    if offers.get("price") is not None:
        return _int(offers.get("price"))
    if block.get("price") is not None:
        return _int(block.get("price"))
    return None


def _pretty_type(raw: str | None, fallback: str = "Maison") -> str:
    if not raw:
        return fallback
    key = re.sub(r"\s+", "", raw).lower()
    return TYPE_ALIASES.get(key, raw.strip().capitalize() if raw else fallback)


def _dept_from_url(page_url: str) -> tuple[str | None, str | None]:
    path = urlparse(page_url).path.lower()
    m = re.search(r"immobilier-vente-[^-]+-([a-z0-9+-]+)\.html", path)
    if not m:
        return None, None
    slug = m.group(1).split("+", 1)[0]
    name, code = DEPT_SLUG.get(slug, (None, None))
    if name:
        return name, code
    return slug.replace("-", " ").title(), None


def _place_region(address: dict | None, title: str | None
                  ) -> tuple[str | None, str | None, str | None]:
    """(place, region/dept, postal)."""
    address = address if isinstance(address, dict) else {}
    locality = address.get("addressLocality") or address.get("addressLocality")
    region = address.get("addressRegion")
    postal = address.get("postalCode")
    if not locality and title:
        # 'Maison 4 pièces 71 m² Boussac (23)' or 'Boussac (23)'
        m = re.search(
            r"\b([A-ZÀ-Ÿ][A-Za-zÀ-ÿ'’ -]+?)\s*\((\d{2,3})\)\s*$",
            title,
        )
        if m:
            locality = m.group(1).strip()
            postal = postal or None
    return locality, region, postal


def _skip_href(href: str | None) -> bool:
    if not href:
        return True
    if AGENCY_PATH_RE.search(href):
        return True
    if "/prix-immobilier/" in href:
        return True
    return listing_id(href) is None


def _row_from_ld(item: dict, page_url: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    # ListItem wrapper
    if "item" in item and isinstance(item["item"], dict):
        item = item["item"]
    href = item.get("url") or item.get("@id")
    ext = listing_id(href)
    if not ext:
        return None
    if _skip_href(href):
        return None
    types = _types_of(item)
    if types & {"realestateagent", "organization", "webpage", "breadcrumblist"}:
        return None
    name = item.get("name")
    if name and LOCATION_RE.search(str(name)) and "vente" not in str(name).lower():
        return None

    address = item.get("address") if isinstance(item.get("address"), dict) else {}
    place, region, postal = _place_region(address, name)
    geo = item.get("geo") if isinstance(item.get("geo"), dict) else {}
    lat = _float(geo.get("latitude"))
    lon = _float(geo.get("longitude"))
    living = _qty(item.get("floorSize"))
    rooms = _int(item.get("numberOfRooms"))
    beds = _int(item.get("numberOfBedrooms"))
    baths = _int(item.get("numberOfBathroomsTotal") or item.get("numberOfBathrooms"))
    land = _qty(item.get("landArea") or item.get("lotSize"))
    price = _offer_price(item)
    ptype = None
    for t in ("house", "singlefamilyresidence", "apartment", "land"):
        if t in types:
            ptype = _pretty_type(t)
            break
    if ptype is None and name:
        first = str(name).split()[0]
        ptype = _pretty_type(first, "Maison")
    ptype = ptype or "Maison"

    image = item.get("image")
    thumb = None
    if isinstance(image, str) and image.startswith("http"):
        thumb = image
    elif isinstance(image, list) and image:
        first = image[0]
        thumb = first if isinstance(first, str) else (first or {}).get("url")
    elif isinstance(image, dict):
        thumb = image.get("url")

    url = urljoin(BASE + "/", href) if href else canonical_detail_url(ext)
    dept_name, dept_code = region, None
    if not dept_name:
        dept_name, dept_code = _dept_from_url(page_url)
    raw: dict[str, Any] = {"country": "France"}
    if postal:
        raw["postal_code"] = str(postal)
    if dept_code or address.get("addressRegion"):
        raw["department"] = dept_name
    code_m = DEPT_CODE_RE.search(name or "")
    if code_m and not raw.get("department_code"):
        raw["department_code"] = code_m.group(1)
    if postal and len(str(postal)) >= 2:
        raw.setdefault("department_code", str(postal)[:2])

    row: dict[str, Any] = {
        "source": SOURCE,
        "external_id": str(ext),
        "url": url,
        "type": ptype,
        "place": place,
        "region": dept_name or region,
        "dept_fr": dept_name,
        "dept_nl": dept_name,
        "price": price,
        "currency": "EUR",
        "living_m2": living,
        "land_m2": land,
        "rooms": rooms,
        "bedrooms": beds,
        "baths": baths,
        "lat": lat,
        "lon": lon,
        "thumb": thumb,
        "snippet": item.get("description"),
        "raw_fields": raw,
    }
    if name:
        row["raw_fields"] = {**raw, "title": name}
    return row


def _parse_card(box, page_url: str) -> dict[str, Any] | None:
    """Fallback when a card has no usable ItemList entry."""
    href = None
    for a in box.select("a[href]"):
        if listing_id(a.get("href")):
            href = a.get("href")
            break
    ext = listing_id(href)
    if not ext:
        # Some cards embed the announce JSON in the text.
        blob = _txt(box) or ""
        m = ID_RE.search(blob) or re.search(r'"id"\s*:\s*"?(\d{5,})"?', blob)
        if m:
            ext = m.group(1) if m.lastindex else m.group(0)
            if ext and ext.isdigit() is False:
                ext = listing_id(ext)
        if not ext:
            return None
    url = urljoin(BASE + "/", href) if href else canonical_detail_url(ext)
    text = _txt(box) or ""
    if LOCATION_RE.search(text) and "vente" not in text.lower():
        return None

    # Prefer a nested ld+json on the card (Residence / House).
    for script in box.find_all("script", type="application/ld+json"):
        data = _json_load((script.string or script.get_text() or "").strip())
        for item in _as_list(data):
            if isinstance(item, dict):
                row = _row_from_ld(item, page_url)
                if row:
                    return row

    price = parse_eur(text)
    living = None
    land = None
    rooms = None
    beds = None
    # '7 pièces • 131m2 • 2 chambres' / 'Terrain 660 m²'
    m = PIECES_RE.search(text)
    if m:
        rooms = int(m.group(1))
    m = CHAMBRES_RE.search(text)
    if m:
        beds = int(m.group(1))
    areas = AREA_RE.findall(text.replace("\xa0", " "))
    if areas:
        living = _int(areas[0])
    tm = TERRAIN_RE.search(text)
    if tm:
        land = _int(tm.group(1))
    elif "terrain" in text.lower() and len(areas) > 1:
        land = _int(areas[-1])

    place = None
    region = None
    title = _txt(box.select_one("h2, h3, .cartouche-liste__title, [class*=title]"))
    loc = re.search(
        r"\b([A-ZÀ-Ÿ][A-Za-zÀ-ÿ'’ -]+?)\s*\((\d{2,3})\)",
        text,
    )
    if loc:
        place = loc.group(1).strip()
    region, _ = _dept_from_url(page_url)
    ptype = "Maison"
    type_m = re.search(
        r"\b(Maison|Propriété|Appartement|Terrain|Immeuble|Ferme|Longère)\b",
        text,
    )
    if type_m:
        ptype = type_m.group(1)
    img = box.select_one("img[src], img[data-src]")
    thumb = None
    if img is not None:
        src = img.get("data-src") or img.get("src")
        if src and not str(src).startswith("data:"):
            thumb = urljoin(BASE + "/", src)

    return {
        "source": SOURCE,
        "external_id": str(ext),
        "url": url,
        "type": ptype,
        "place": place,
        "region": region,
        "dept_fr": region,
        "dept_nl": region,
        "price": price,
        "currency": "EUR",
        "living_m2": living,
        "land_m2": land,
        "rooms": rooms,
        "bedrooms": beds,
        "thumb": thumb,
        "snippet": title,
        "raw_fields": {"country": "France", "title": title},
    }


def _total_from_page(soup, blocks: list[dict], listings: list) -> int:
    for block in blocks:
        if "itemlist" in _types_of(block):
            n = _int(block.get("numberOfItems") or block.get("numberOfItems"))
            if n:
                return n
            elems = block.get("itemListElement") or []
            if isinstance(elems, list) and elems:
                return max(len(elems), len(listings))
    h1 = _txt(soup.select_one("h1")) or _txt(soup.title)
    if h1:
        m = ANNONCES_RE.search(h1)
        if m:
            return _int(m.group(1)) or 0
    body = _txt(soup.find("body")) or ""
    m = ANNONCES_RE.search(body)
    if m:
        return _int(m.group(1)) or 0
    return len(listings)


def parse_list(html: str, page_url: str) -> dict[str, Any]:
    if is_blocked(html):
        return {"listings": [], "total_pages": 1, "next_url": None, "blocked": True}
    soup = _soup(html)
    blocks = _ld_blocks(soup)
    listings: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(row: dict[str, Any] | None) -> None:
        if not row:
            return
        ext = row.get("external_id")
        if not ext or ext in seen:
            return
        seen.add(ext)
        listings.append(row)

    for block in blocks:
        if "itemlist" not in _types_of(block):
            continue
        for el in _as_list(block.get("itemListElement")):
            if isinstance(el, dict):
                _add(_row_from_ld(el, page_url))

    # Card-level ld+json / HTML fallback (cards the ItemList omitted).
    for box in soup.select(".cartouche-liste, .cartouche-liste--polpo"):
        _add(_parse_card(box, page_url))

    if not listings:
        # Last resort: any announce-* link with a nearby price.
        for a in soup.select("a[href]"):
            href = a.get("href") or ""
            if _skip_href(href):
                continue
            ext = listing_id(href)
            if not ext or ext in seen:
                continue
            parent = a.find_parent(["article", "li", "div"]) or a
            _add(_parse_card(parent, page_url))

    count = _total_from_page(soup, blocks, listings)
    pages = math.ceil(count / PER_PAGE) if count else (1 if listings else 1)
    pages = max(1, min(pages, PAGE_CEILING))
    current = _page_number(page_url)
    next_url = with_page(page_url, current + 1) if current < pages else None
    return {
        "listings": listings,
        "total_pages": pages,
        "next_url": next_url,
    }


def _ld_listing(blocks: list[dict]) -> dict:
    prefer = {
        "realestatelisting", "product", "house",
        "singlefamilyresidence", "residence", "offer",
    }
    for block in blocks:
        if _types_of(block) & prefer:
            return block
    return blocks[0] if blocks else {}


def _feature_map(soup) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in soup.select("li, tr, .caracteristique, [class*=caract]"):
        label = None
        value = None
        lab_el = row.select_one("span, th, dt, .label, [class*=label]")
        val_el = row.select_one("strong, td, dd, .value, [class*=value]")
        if lab_el is not None and val_el is not None and lab_el is not val_el:
            label = _txt(lab_el)
            value = _txt(val_el)
        else:
            text = _txt(row) or ""
            if ":" in text and len(text) < 80:
                label, value = [p.strip() for p in text.split(":", 1)]
        if label and value and label != value:
            out[label] = value
    return out


def _photos(ld: dict, soup, page_url: str) -> list[str]:
    urls: list[str] = []
    for img in _as_list(ld.get("image")):
        if isinstance(img, str) and img.startswith("http"):
            urls.append(img)
        elif isinstance(img, dict) and img.get("url"):
            urls.append(img["url"])
    if not urls:
        for img in soup.select("img[src], img[data-src]"):
            src = img.get("data-src") or img.get("src")
            if not src or str(src).startswith("data:"):
                continue
            abs_u = urljoin(page_url, src)
            if "lefigaro" in abs_u or "figaro" in abs_u or "/photos" in abs_u:
                urls.append(abs_u)
    seen: set[str] = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _coords(ld: dict, soup) -> tuple[float | None, float | None]:
    geo = ld.get("geo") if isinstance(ld.get("geo"), dict) else {}
    lat = _float(geo.get("latitude"))
    lon = _float(geo.get("longitude"))
    if lat is not None and lon is not None:
        return lat, lon
    for key in ("data-lat", "data-latitude", "lat"):
        el = soup.select_one(f"[{key}]")
        if el is None:
            continue
        lat = _float(el.get(key))
        lon = _float(el.get("data-lng") or el.get("data-longitude") or el.get("lng"))
        if lat is not None and lon is not None:
            return lat, lon
    return None, None


def _land_from_features(features: dict[str, str], description: str | None
                       ) -> int | None:
    for label, value in features.items():
        if TERRAIN_LABEL_RE.match(label.strip()):
            area = _area(value) or _int(value)
            if area:
                return area
    if description:
        m = TERRAIN_RE.search(description)
        if m:
            return _int(m.group(1))
    return None


def _living_from_features(features: dict[str, str]) -> int | None:
    for label, value in features.items():
        if SURFACE_LABEL_RE.match(label.strip()):
            area = _area(value) or _int(value)
            if area:
                return area
    return None


def parse_detail(html: str, url: str) -> dict[str, Any]:
    if is_blocked(html):
        return {"source": SOURCE, "url": url, "raw_fields": {"blocked": True}}
    soup = _soup(html)
    blocks = _ld_blocks(soup)
    ld = _ld_listing(blocks)
    offered = ld.get("itemOffered") if isinstance(ld.get("itemOffered"), dict) else {}
    house = offered or ld

    ext = listing_id(url) or listing_id(ld.get("url") or "")
    price = _offer_price(ld) or _offer_price(house)
    if price is None:
        price = parse_eur(_txt(soup.select_one("h1, .price, [class*=price]"))
                          or _txt(soup.find("body")))

    address = house.get("address") if isinstance(house.get("address"), dict) else {}
    if not address and isinstance(ld.get("address"), dict):
        address = ld["address"]
    name = house.get("name") or ld.get("name") or _txt(soup.select_one("h1"))
    place, region, postal = _place_region(address, name)
    if not region:
        region, _ = _dept_from_url(url)
        # Detail URLs have no dept slug — keep address region only.
        if region and "annonce-" in urlparse(url).path:
            region = address.get("addressRegion")
    dept_name = region or address.get("addressRegion")

    living = _qty(house.get("floorSize") or ld.get("floorSize"))
    rooms = _int(house.get("numberOfRooms") or ld.get("numberOfRooms"))
    beds = _int(house.get("numberOfBedrooms") or ld.get("numberOfBedrooms"))
    baths = _int(
        house.get("numberOfBathroomsTotal")
        or house.get("numberOfBathrooms")
        or ld.get("numberOfBathroomsTotal")
    )
    features = _feature_map(soup)
    if living is None:
        living = _living_from_features(features)
    land = _qty(house.get("landArea") or house.get("lotSize") or ld.get("landArea"))
    if land is None:
        land = _land_from_features(features, house.get("description") or ld.get("description"))

    desc = house.get("description") or ld.get("description")
    if not desc:
        desc = _txt(soup.select_one("[class*=description], .description, article p"))

    body = " ".join(x for x in (desc, name, _txt(soup.find("body"))) if x)
    energy = None
    gas = None
    kwh = None
    dm = DPE_RE.search(body or "")
    if dm:
        energy = dm.group(1).upper()
    gm = GES_RE.search(body or "")
    if gm:
        gas = gm.group(1).upper()
    km = KWH_RE.search(body or "")
    if km:
        kwh = _int(km.group(1))
    for label, value in features.items():
        if re.search(r"dpe|[eé]nergie", label, re.I) and re.match(r"^[A-G]$", value or ""):
            energy = energy or value.upper()
        if re.search(r"ges|ges\b|gaz", label, re.I) and re.match(r"^[A-G]$", value or ""):
            gas = gas or value.upper()

    ref = None
    for label, value in features.items():
        if re.search(r"r[eé]f", label, re.I):
            ref = (value or "").rstrip(".,;:") or None
    if not ref:
        rm = REF_RE.search(body or "")
        if rm:
            ref = rm.group(1).rstrip(".,;:")

    agent = None
    seller = ld.get("seller") or house.get("seller") or ld.get("brand")
    if isinstance(seller, dict):
        agent = seller.get("name")
    elif isinstance(seller, str):
        agent = seller
    if not agent:
        agent = _txt(soup.select_one("[class*=agence] [class*=name], .agency-name"))

    types = _types_of(house) | _types_of(ld)
    ptype = "Maison"
    for t in ("house", "singlefamilyresidence", "apartment", "land"):
        if t in types:
            ptype = _pretty_type(t)
            break
    if name:
        first = str(name).split()[0]
        if first.lower() in TYPE_ALIASES or first in (
            "Maison", "Propriété", "Appartement", "Terrain",
        ):
            ptype = _pretty_type(first, ptype)

    lat, lon = _coords(house if house.get("geo") else ld, soup)
    photos = _photos(ld, soup, url)
    raw: dict[str, Any] = {"country": "France"}
    if postal:
        raw["postal_code"] = str(postal)
    if dept_name:
        raw["department"] = dept_name
    if postal and len(str(postal)) >= 2:
        raw["department_code"] = str(postal)[:2]
    if features:
        raw["characteristics"] = features
    if name:
        raw["title"] = name
    if LOCATION_RE.search(" ".join(x for x in (name, desc) if x) or ""):
        raw["maybe_rental"] = True

    return {
        "source": SOURCE,
        "external_id": str(ext) if ext else None,
        "url": ld.get("url") or url,
        "type": ptype,
        "place": place,
        "region": dept_name,
        "dept_fr": dept_name,
        "dept_nl": dept_name,
        "price": price,
        "currency": "EUR",
        "living_m2": living,
        "land_m2": land,
        "rooms": rooms,
        "bedrooms": beds,
        "baths": baths,
        "energy_label": energy,
        "gas_label": gas,
        "energy_kwh": kwh,
        "reference": ref,
        "agent": agent,
        "description": desc,
        "photos": photos or None,
        "thumb": (photos[0] if photos else None),
        "lat": lat,
        "lon": lon,
        "raw_fields": raw,
    }
