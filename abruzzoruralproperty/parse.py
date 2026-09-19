"""Parsers for abruzzoruralproperty.com list and detail pages.

Public EN HTML (Joomla FW Real Estate). The for-sale list is
`/find-a-property/for-sale?start=0` (then start=6, 12, …; 6 cards per
page). A GET price filter works:

`/find-a-property/for-sale?search[price_from]=0&search[price_to]=100000&fwrealestate_update_search=1`

Verified 2026-09-19: unfiltered End is start=372 (~375 cards); the
≤€100k filter End is start=342 (~345 cards, including SOLD / UNDER
OFFER with no euro figure). The site's pager keeps the search[] params.

Detail URLs are `/find-a-property/for-sale/item/{id}-{slug}`.
`external_id` is that numeric CMS id (e.g. 1792 Fossalto). Agency refs
like FL4245 sit in `reference`. Prices are euro asking prices
(€42.000). Site-wide geo.position is the San Salvo office — not a
listing coordinate.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from .http import BASE

SOURCE = "abruzzoruralproperty"
PER_PAGE = 6  # live for-sale pages, verified 2026-09-19
AGENT = "Abruzzo Rural Property"

ITEM_HREF_RE = re.compile(
    r"/find-a-property/for-sale/item/(\d+)-([^/?#]+)", re.I
)
# Featured / modal links we must not treat as the card's detail URL.
SKIP_ITEM_RE = re.compile(
    r"/(?:request_info|email_to_friend|video)/", re.I
)
EUR_RE = re.compile(r"€\s*([\d.,\s]+)", re.I)
START_RE = re.compile(r"(?:[?&]|/)start=(\d+)", re.I)
SQM_RE = re.compile(
    r"([\d][\d.,]*)\s*(?:sqm|sq\.?\s*m|m²)", re.I
)
LAND_SQM_RE = re.compile(
    r"([\d][\d.,]*)\s*(?:sqm|sq\.?\s*m|m²)\s+"
    r"(?:of\s+)?(?:[\w'-]+\s+){0,4}(?:land|garden|grounds?)\b",
    re.I,
)
HA_RE = re.compile(r"([\d]+(?:[.,]\d+)?)\s*hectares?\b", re.I)
BED_RE = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s*-?\s*bedrooms?\b",
    re.I,
)
CADASTRAL_RE = re.compile(
    r"Total\s+cadastral\s+space:\s*([\d][\d.,]*)\s*(?:sqm|sq\.?\s*m|m²)",
    re.I,
)
PROVINCE_RE = re.compile(r"province of\s+([A-Za-zÀ-ÖØ-öø-ÿ'’\s-]+?)(?:\s+in\b|[.,;]|$)", re.I)
REGION_RE = re.compile(r"\b(Abruzzo|Molise)\b", re.I)
ITALY_LOC_RE = re.compile(
    r"Italy\s*\|\s*([^|]+?)\s*\|\s*([^|.]+)", re.I
)
STATUS_PRICE_RE = re.compile(r"\b(sold|under offer|reserved)\b", re.I)

BED_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _txt(node) -> str | None:
    if node is None:
        return None
    t = node.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", t) or None


def _european_int(text: str | None) -> int | None:
    """Parse a European/English integer: 4.700 / 4,700 / 4700 → 4700.

    Dotted or comma thousands (exactly three digits) win over a decimal
    reading, so land '4.700' is 4700 m² not 4.7. A leftover '7,36 sqm'
    room size becomes 7.
    """
    if not text:
        return None
    m = re.search(r"[\d]+(?:[.,]\d+)*", str(text).replace("\xa0", " "))
    if not m:
        return None
    raw = m.group(0)
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
        return int(raw.replace(".", ""))
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+", raw):
        return int(raw.replace(",", ""))
    if "," in raw and "." not in raw:
        return int(round(float(raw.replace(",", "."))))
    if "." in raw and "," not in raw:
        return int(round(float(raw)))
    digits = re.sub(r"\D", "", raw)
    return int(digits) if digits else None


def parse_eur(text: str | None) -> int | None:
    if not text:
        return None
    blob = str(text).replace("\xa0", " ")
    if STATUS_PRICE_RE.search(blob) and "€" not in blob:
        return None
    m = EUR_RE.search(blob)
    return _european_int(m.group(1)) if m else None


def _listing_id(href: str | None) -> str | None:
    if not href or SKIP_ITEM_RE.search(href):
        return None
    m = ITEM_HREF_RE.search(href)
    return m.group(1) if m else None


def _item_href(href: str | None) -> str | None:
    if not href or SKIP_ITEM_RE.search(href):
        return None
    m = ITEM_HREF_RE.search(href)
    return m.group(0) if m else None


def canonical_detail_url(href_or_id: str, slug: str | None = None) -> str:
    """Absolute detail URL. Prefer the live `/item/{id}-{slug}` path."""
    path = _item_href(href_or_id)
    if path:
        return urljoin(BASE + "/", path.lstrip("/"))
    if slug:
        return f"{BASE}/find-a-property/for-sale/item/{href_or_id}-{slug}"
    return f"{BASE}/find-a-property/for-sale/item/{href_or_id}"


def _start_index(page_url: str) -> int:
    parsed = urlparse(page_url)
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key == "start":
            try:
                return max(0, int(value))
            except ValueError:
                return 0
    m = START_RE.search(page_url)
    return int(m.group(1)) if m else 0


def _page_number(page_url: str) -> int:
    return _start_index(page_url) // PER_PAGE + 1


def with_page(page_url: str, page: int) -> str:
    """Keep search[] filters and set start= (page-1)*6.

    `page` is 1-based (same as the other adapters). Page 1 omits `start=`
    so the cache key matches the seed URL in searches.json.
    """
    parsed = urlparse(page_url)
    if not parsed.scheme:
        parsed = urlparse(urljoin(BASE + "/", page_url))
    pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if k != "start"]
    if page > 1:
        pairs.append(("start", str((page - 1) * PER_PAGE)))
    query = urlencode(pairs, safe="[]")
    return urlunparse(parsed._replace(query=query, fragment=""))


def _beds_from_text(text: str | None) -> int | None:
    if not text:
        return None
    m = BED_RE.search(text)
    if not m:
        return None
    token = m.group(1).lower()
    if token.isdigit():
        return int(token)
    return BED_WORDS.get(token)


def _land_from_text(text: str | None) -> int | None:
    if not text:
        return None
    blob = str(text).replace("\xa0", " ")
    m = LAND_SQM_RE.search(blob)
    if m:
        return _european_int(m.group(1))
    hm = HA_RE.search(blob)
    if hm:
        raw = hm.group(1).replace(",", ".")
        try:
            ha = float(raw)
        except ValueError:
            return None
        if ha <= 0:
            return None
        return int(round(ha * 10_000))
    return None


def _labeled_blocks(box) -> dict[str, str]:
    out: dict[str, str] = {}
    for div in box.select(".property-id, .category, .type"):
        label = _txt(div.select_one(".text-bold")) or ""
        full = _txt(div) or ""
        value = full
        if label and full.lower().startswith(label.lower()):
            value = full[len(label):].strip(" :")
        key = re.sub(r"\s+", " ", label).strip(" :").lower()
        if key and value:
            out[key] = value
    return out


def _card_row(box, page_url: str) -> dict[str, Any] | None:
    href = None
    link = box.select_one(".fw-link-details a[href], a.fw-link-details[href]")
    if link:
        href = link.get("href")
    if not _listing_id(href):
        for a in box.select("a[href*='/item/']"):
            cand = a.get("href") or ""
            if _listing_id(cand):
                href = cand
                break
    ext = _listing_id(href)
    if not ext:
        return None

    path = _item_href(href) or f"/find-a-property/for-sale/item/{ext}"
    url = urljoin(page_url, path)

    title = _txt(box.select_one("h2, .fw-list-propery-line, .contentheading"))
    price_el = box.select_one("span.bold")
    price_blob = _txt(price_el) or ""
    price = parse_eur(price_blob)
    if price is None and "€" in (_txt(box) or ""):
        price = parse_eur(_txt(box))

    fields = _labeled_blocks(box)
    place = fields.get("city")
    ptype = fields.get("property type")
    reference = fields.get("reference number") or ext
    snippet = _txt(box.select_one(".description"))
    if snippet and snippet.lower().startswith("property short description"):
        snippet = snippet.split(":", 1)[-1].strip()

    blob = " ".join(x for x in (title, snippet) if x)
    beds = _beds_from_text(blob)
    land = _land_from_text(blob)

    img = box.select_one(".fw-list-propery-image img[src], img[src]")
    thumb = urljoin(BASE + "/", img["src"]) if img and img.get("src") else None

    raw: dict[str, Any] = {"listing_id": ext, "country": "Italy"}
    if fields.get("category"):
        raw["category"] = fields["category"]
    if title:
        raw["title"] = title
    if STATUS_PRICE_RE.search(price_blob):
        raw["status_label"] = price_blob

    return {
        "source": SOURCE,
        "external_id": ext,
        "id": ext,
        "url": url,
        "type": ptype,
        "place": place,
        "price": price,
        "currency": "EUR",
        "beds": beds,
        "bedrooms": beds,
        "living_m2": None,
        "land_m2": land,
        "reference": reference,
        "thumb": thumb,
        "snippet": snippet or title,
        "promoted": False,
        "raw_fields": raw,
    }


def _total_pages(s: BeautifulSoup, n_cards: int, page_url: str) -> int:
    last_start = _start_index(page_url)
    for a in s.select("a[href*='start=']"):
        href = a.get("href") or ""
        m = START_RE.search(href)
        if m:
            last_start = max(last_start, int(m.group(1)))
    pages = last_start // PER_PAGE + 1
    current = _page_number(page_url)
    if n_cards >= PER_PAGE:
        pages = max(pages, current + 1)
    return max(1, pages)


def parse_list(html: str, page_url: str) -> dict[str, Any]:
    s = _soup(html)
    listings, seen = [], set()
    for box in s.select(".fw-list-property"):
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

def _detail_fields(s: BeautifulSoup) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in s.select(".fw-property-detail-row, .fw-property-detail-row-odd"):
        label = _txt(row.select_one(".text-bold")) or ""
        key = re.sub(r"\s+", " ", label).strip(" :").lower()
        if not key:
            continue
        val_el = row.select_one("[itemprop=value]")
        if val_el:
            value = _txt(val_el)
        else:
            full = _txt(row) or ""
            value = full
            if full.lower().startswith(key):
                value = full[len(key):].strip(" :")
        if value:
            out[key] = value
    return out


def _photos(s: BeautifulSoup, page_url: str) -> list[str]:
    photos, seen = [], set()
    nodes = s.select(
        ".fwre-gallery img[src], .fw-property-gallery img[src], "
        ".photo_gallery img[src], .fwre-gallery a[href], "
        ".fw-property-gallery a[href]"
    )
    for node in nodes:
        raw = (node.get("href") or node.get("src") or "").strip()
        if not raw or raw.startswith("#"):
            continue
        if "request_info" in raw or "email_to_friend" in raw:
            continue
        abs_url = urljoin(page_url, raw)
        abs_url = re.sub(r"/med_", "/", abs_url)
        key = re.sub(r"[?].*$", "", abs_url)
        if "/images/properties/" not in key:
            continue
        if key in seen:
            continue
        seen.add(key)
        photos.append(abs_url)
    return photos


def _italy_loc(html: str, s: BeautifulSoup) -> dict[str, str | None]:
    out: dict[str, str | None] = {"region": None, "place": None, "province": None}
    for meta in s.select("meta[name='description'], meta[property='og:description']"):
        content = meta.get("content") or ""
        m = ITALY_LOC_RE.search(content)
        if m:
            out["region"] = m.group(1).strip()
            out["place"] = m.group(2).strip(" .")
            break
    blob = s.get_text(" ", strip=True)
    pm = PROVINCE_RE.search(blob)
    if pm:
        out["province"] = re.sub(r"\s+", " ", pm.group(1)).strip()
    if not out["region"]:
        rm = REGION_RE.search(blob)
        if rm:
            out["region"] = rm.group(1)
    return out


def _living_m2(fields: dict[str, str], desc: str | None) -> int | None:
    for key in ("total cadastral space", "cadastral space", "cadastral area"):
        if fields.get(key):
            n = _european_int(fields[key])
            if n:
                return n
    if desc:
        m = CADASTRAL_RE.search(desc)
        if m:
            return _european_int(m.group(1))
    return None


def _land_m2(fields: dict[str, str], desc: str | None) -> int | None:
    raw = fields.get("total land")
    if raw:
        if re.search(r"\bno land\b", raw, re.I):
            return None
        n = _european_int(raw)
        if n:
            return n
    return _land_from_text(desc)


def parse_detail(html: str, url: str) -> dict[str, Any]:
    s = _soup(html)
    out: dict[str, Any] = {
        "url": url, "source": SOURCE, "agent": AGENT, "currency": "EUR",
    }
    raw: dict[str, Any] = {"country": "Italy"}

    ext = _listing_id(url)
    if not ext:
        for a in s.select("a[href*='/item/']"):
            ext = _listing_id(a.get("href"))
            if ext:
                break
    if ext:
        path = _item_href(url)
        out["url"] = canonical_detail_url(path or ext)
        out["external_id"] = ext
        raw["listing_id"] = ext

    fields = _detail_fields(s)
    raw.update({f"field_{k}": v for k, v in fields.items() if len(v) < 200})

    price = parse_eur(_txt(s.select_one(".bold-price, .text-price")))
    if price is None:
        price = parse_eur(fields.get("price"))
    if price is None:
        price = parse_eur(_txt(s.h1) if s.h1 else None)
    out["price"] = price

    place = _txt(s.select_one("[itemprop=addressLocality]")) or fields.get("city")
    loc = _italy_loc(html, s)
    if not place:
        place = loc["place"]
    if place:
        out["place"] = place
    # province → region; Abruzzo/Molise → dept_nl (same split as API).
    if loc["province"]:
        out["region"] = loc["province"]
        raw["province"] = loc["province"]
    elif loc["region"]:
        out["region"] = loc["region"]
    if loc["region"]:
        out["dept_nl"] = loc["region"]
        raw["region"] = loc["region"]

    if fields.get("property type"):
        out["type"] = fields["property type"]
    elif s.h1:
        out["type"] = None

    if fields.get("reference number"):
        out["reference"] = fields["reference number"]
    elif ext:
        out["reference"] = ext

    if fields.get("bedrooms"):
        out["bedrooms"] = _european_int(fields["bedrooms"])
    if fields.get("bathrooms"):
        out["baths"] = _european_int(fields["bathrooms"])

    desc = None
    block = None
    for row in s.select("article.fw-property-detail-row, .fw-property-detail-row"):
        label = (_txt(row.select_one(".text-bold")) or "").lower()
        if "description" in label:
            block = row
            break
    if block is None:
        block = s.select_one(".fw-property-details")
    if block:
        desc = block.get_text("\n", strip=True)
        desc = re.sub(r"\n{3,}", "\n\n", desc)
        # Drop the "Property Description" heading if it's the first line.
        lines = desc.split("\n")
        if lines and re.search(r"property description", lines[0], re.I):
            desc = "\n".join(lines[1:]).strip()
    out["description"] = desc or None
    out["snippet"] = (desc.split("\n", 1)[0] if desc else None) or _txt(s.h1)

    out["living_m2"] = _living_m2(fields, desc)
    out["land_m2"] = _land_m2(fields, desc)

    if fields.get("amenities"):
        out["features"] = fields["amenities"]
    if fields.get("energy class"):
        label = fields["energy class"].strip()
        if label and label.lower() not in {"n/a", "na", "-", "not available"}:
            out["energy_label"] = label[0].upper() if len(label) <= 3 else label
            raw["energy_class"] = label

    # Do not use site-wide geo.position / place:location — those are the
    # San Salvo office (verified 2026-09-19 on every detail page).
    raw["agency_geo"] = "San Salvo office; not listing coords"

    photos = _photos(s, url)
    out["photos"] = photos
    if photos:
        out["thumb"] = photos[0]
    elif s.select_one("meta[property='og:image']"):
        og = s.select_one("meta[property='og:image']")
        out["thumb"] = og.get("content")

    title = _txt(s.h1) or _txt(s.title)
    if title:
        raw["title"] = title
        if out.get("bedrooms") is None:
            out["bedrooms"] = _beds_from_text(title)

    out["raw_fields"] = raw
    return out
