"""Parsers for domaza.com list and detail pages.

Public EN HTML. Country house lists are stable SEO paths:

    /house_{country}-17-4340-{country_id}-0-0-0-sl/

`17` is the .com EN site id, `4340` is the House tag, `sl` is the sale
deal flag. Country ids verified 2026-09-19: Montenegro 146, Serbia 193,
Albania 3, Georgia 80. Pagination is `/_page/N/`; page 1 omits it.

The search form POSTs to `/properties_all/` and lands on an opaque
`/property/index/search/1/s/{sha1}/` or short `/r/s/{token}` URL. Those
HASH filters expire (a live `/s/{sha}` 301'd back to
`/real_estate_in_montenegro/` on 2026-09-19). We do not seed searches
on HASH URLs — country/list paths only. Path extras like
`_pricefrom/` / `?priceto=` are ignored by the site.

List cards are `div.single_property` (20 per full page; thin countries
show ~12 mixed cards and no pager). Detail URLs are
`/{type}_{place}_…-{site}-{id}-p/`. `external_id` is that numeric id.

Prices: the portal can print EUR or USD (session currency). We store
`price` + `currency=EUR`, preferring the euro figure on the page, and
keep `$` in `raw_fields`. See `domaza/fx.py`.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from .fx import FX_SOURCE, USD_TO_EUR, usd_to_eur
from .http import BASE

SOURCE = "domaza"
PER_PAGE = 20  # live Montenegro house pages, verified 2026-09-19
AGENT = "Domaza"

# /house_suscepan_herceg_novi_municipality_montenegro-17-8687837-p/
DETAIL_RE = re.compile(
    r"/[a-z0-9_]+-(\d+)-(\d+)-p/?",
    re.I,
)
ID_IN_TEXT_RE = re.compile(r"\bID\s+(\d+)\b", re.I)
PAGE_RE = re.compile(r"/_page/(\d+)/?")
# HASH search URLs — parse if handed one; never invent them as next_url.
HASH_RE = re.compile(
    r"/(?:property/index/search/\d+/s/|r/s/)([0-9a-zA-Z]+)",
)
EUR_RE = re.compile(
    r"(?:€\s*([\d][\d\s.,]*)|([\d][\d\s.,]*)\s*€)",
)
USD_RE = re.compile(r"\$\s*([\d][\d\s.,]*)")
# get_text(" ") turns <sup>2</sup> into "m 2".
AREA_RE = re.compile(
    r"([\d][\d\s.,]*)\s*(?:m2|m²|m\s*2|sq\.?\s*m|sqm)\b",
    re.I,
)
PLOT_RE = re.compile(
    r"(?:building plot|plot|yard|land)\s+(?:of\s+|around the house is\s+)?"
    r"([\d][\d\s.,]*)\s*(?:m2|m²)",
    re.I,
)
AGENCY_SLUG_RE = re.compile(r"/([a-z0-9_]+)-\d+-\d+-a/", re.I)
REF_RE = re.compile(r"\bRef\s*[-–]?\s*([A-Za-z0-9/-]+)", re.I)

# 0.000… on cards is "not stated", not the equator.
ZERO_COORD = 0.0001


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


def _listing_id(href: str | None) -> str | None:
    if not href:
        return None
    path = urlparse(href).path
    m = DETAIL_RE.search(path)
    if m:
        return m.group(2)
    m = re.search(r"/property/print/id/(\d+)/?", path, re.I)
    return m.group(1) if m else None


def canonical_detail_url(href: str | None, ext: str | None = None) -> str:
    """Keep the SEO slug the site served; drop `/_hasSearch/1/` junk."""
    if href:
        parsed = urlparse(urljoin(BASE + "/", href))
        path = re.sub(r"/_hasSearch/\d+/?$", "/", parsed.path)
        if not path.endswith("/"):
            path += "/"
        return urlunparse(("https", "www.domaza.com", path, "", "", ""))
    if ext:
        return f"{BASE}/property/print/id/{ext}/"
    return BASE + "/"


def _page_number(page_url: str) -> int:
    m = PAGE_RE.search(urlparse(page_url).path)
    if m:
        return max(1, int(m.group(1)))
    return 1


def with_page(page_url: str, page: int) -> str:
    """Keep the country/type path and set /_page/N/.

    Page 1 on the live site omits `/_page/1/`, so we do too — cache keys
    stay a function of the URL the site actually serves. HASH `/s/…`
    paths are left alone aside from the page segment (we still do not
    seed them).
    """
    parsed = urlparse(page_url)
    path = re.sub(r"/_page/\d+/?$", "/", parsed.path)
    if not path.endswith("/"):
        path += "/"
    if page > 1:
        path = f"{path}_page/{page}/"
    return urlunparse(parsed._replace(path=path))


def parse_money_blob(text: str | None) -> dict[str, int]:
    """Pull EUR / USD amounts out of a price block."""
    out: dict[str, int] = {}
    if not text:
        return out
    blob = text.replace("\xa0", " ")
    m = EUR_RE.search(blob)
    if m:
        amount = _int(m.group(1) or m.group(2))
        if amount is not None:
            out["EUR"] = amount
    m = USD_RE.search(blob)
    if m:
        amount = _int(m.group(1))
        if amount is not None:
            out["USD"] = amount
    return out


def priced_row(eur: int | None, usd: int | None = None) -> dict[str, Any]:
    """Prefer the portal's euro asking price; convert $ only as fallback."""
    raw: dict[str, Any] = {}
    if eur is not None:
        raw["price_eur"] = eur
    if usd is not None:
        raw["price_usd"] = usd
    if eur is not None:
        raw["price_source"] = "portal_eur"
        return {"price": eur, "currency": "EUR", "raw_fields": raw}
    if usd is not None:
        converted = usd_to_eur(usd)
        raw["price_source"] = "usd_fx"
        raw["fx_rate"] = USD_TO_EUR
        raw["fx_source"] = FX_SOURCE
        return {"price": converted, "currency": "EUR", "raw_fields": raw}
    return {"price": None, "currency": "EUR", "raw_fields": raw}


def _deal(text: str | None) -> str | None:
    if not text:
        return None
    low = text.lower()
    if re.search(r"\bto rent\b|\bfor rent\b|\brent\b", low):
        return "rent"
    if re.search(r"\bfor sale\b|\bsale\b", low):
        return "sale"
    return None


def _crumb_places(box) -> tuple[str | None, str | None, str | None]:
    """Breadcrumb: Country / Region / Place."""
    links = []
    for a in box.select(".oftitle a[href], .oftitle_location a[href]"):
        t = _txt(a)
        h = a.get("href") or ""
        if t and "properties_" in h:
            links.append(t)
    country = links[0] if links else None
    region = links[1] if len(links) > 1 else None
    place = links[2] if len(links) > 2 else (links[-1] if links else None)
    hidden = _txt(box.select_one(".location_hidden_data"))
    if hidden and not place:
        place = hidden
    return place, region, country


def _beds_baths(box) -> tuple[int | None, int | None]:
    """Counts from this listing's thumb/gallery only.

    Detail pages repeat bed icons on 'more offers' cards; walking the
    whole soup would keep the last similar listing's numbers.
    """
    beds = baths = None
    roots = [r for r in (
        box.select_one(".gallery-data-info"),
        box.select_one(".property_image_thumb .thumb-info"),
        box.select_one(".thumb-info"),
    ) if r is not None]
    if not roots and getattr(box, "select", None):
        # list card: the box *is* the card
        roots = [box]
    for root in roots:
        for li in root.select("li"):
            n = _int(_txt(li))
            if n is None:
                continue
            blob = str(li)
            if "bed_icon" in blob or "icon-bed" in blob:
                beds = n
            elif "shower_icon" in blob or "icon-shower" in blob:
                baths = n
        if beds is not None or baths is not None:
            return beds, baths
    return beds, baths


def _coord(text: str | None) -> float | None:
    if not text:
        return None
    try:
        val = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    if abs(val) < ZERO_COORD:
        return None
    return val


def _card_row(box, page_url: str) -> dict[str, Any] | None:
    ext = (
        (box.get("data-prop") or "").strip()
        or _txt(box.select_one(".prop_id"))
        or _listing_id(box.get("data-href"))
        or _listing_id(_txt(box.select_one("a.thumb-popup")))
    )
    if ext:
        ext = re.sub(r"\D", "", str(ext)) or None
    href = None
    link = box.select_one("a.thumb-popup[href], a[href*='-p/']")
    if link:
        href = link.get("href")
    href = href or box.get("data-href")
    if not ext:
        ext = _listing_id(href)
    if not ext:
        return None

    money = parse_money_blob(
        _txt(box.select_one(".prop_price"))
        or _txt(box.select_one(".property_price"))
        or _txt(box.select_one(".price_hidden_data"))
    )
    priced = priced_row(money.get("EUR"), money.get("USD"))

    place, region, country = _crumb_places(box)
    beds, baths = _beds_baths(box)
    living = _area(_txt(box.select_one(".area_hidden_data")))
    if living is None:
        # "47 m2" sits next to the price, labelled Total area.
        living = _area(_txt(box.select_one(".property_price")))

    ptype = _txt(box.select_one(".prop_title"))
    labels = _txt(box.select_one(".property_labels"))
    deal = _deal(labels) or _deal(_txt(box.select_one(".title_hidden_data")))

    lat = _coord(_txt(box.select_one(".prop_latitude")))
    lon = _coord(_txt(box.select_one(".prop_longitude")))

    img = None
    el = box.select_one(".property_image_thumb img[src], img.img-responsive[src]")
    if el and el.get("src") and "offer_default" not in (el.get("src") or ""):
        img = el["src"]
    thumb = urljoin(BASE + "/", img) if img else None

    agent_name = _txt(box.select_one(".property_agency_name"))
    id_line = _txt(box.select_one(".oftitle .id")) or _txt(box.select_one(".id"))
    reference = None
    if id_line:
        rm = REF_RE.search(id_line)
        if rm:
            reference = rm.group(1)

    raw = dict(priced["raw_fields"])
    raw["listing_id"] = ext
    if country:
        raw["country"] = country
    if deal:
        raw["deal"] = deal
    if labels:
        raw["labels"] = labels

    return {
        "source": SOURCE,
        "external_id": ext,
        "id": ext,
        "url": canonical_detail_url(href, ext),
        "type": ptype,
        "place": place,
        "region": region,
        "dept_nl": region,
        "lat": lat,
        "lon": lon,
        "price": priced["price"],
        "currency": priced["currency"],
        "beds": beds,
        "bedrooms": beds,
        "baths": baths,
        "living_m2": living,
        "reference": reference or ext,
        "agent": AGENT,
        "agent_name": agent_name,
        "thumb": thumb,
        "snippet": ptype,
        "promoted": False,
        "raw_fields": raw,
    }


def _total_pages(s: BeautifulSoup, n_cards: int) -> int:
    last = 1
    for a in s.select("a[href]"):
        m = PAGE_RE.search(a.get("href") or "")
        if m:
            last = max(last, int(m.group(1)))
    if last == 1 and n_cards >= PER_PAGE:
        last = 2
    return last


def parse_list(html: str, page_url: str) -> dict[str, Any]:
    s = _soup(html)
    listings, seen = [], set()
    for box in s.select(".single_property"):
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

def _meta(s: BeautifulSoup, attr: str, key: str) -> str | None:
    el = s.select_one(f'meta[{attr}="{key}"]')
    if el and el.get("content"):
        return el["content"]
    return None


def _fact_rows(s: BeautifulSoup) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in s.select(".property_important_data .data_row"):
        label = _txt(row.select_one(".single_data_label"))
        value = _txt(row.select_one(".single_data_value"))
        if label and value:
            out[label.lower()] = value
    return out


def _photos(s: BeautifulSoup, page_url: str) -> list[str]:
    photos, seen = [], set()
    candidates = []
    og = _meta(s, "property", "og:image")
    if og:
        candidates.append(og)
    for img in s.select("img[src]"):
        src = (img.get("src") or "").strip()
        if not src:
            continue
        low = src.lower()
        if any(skip in low for skip in (
            "/logos/", "offer_default", "favicon", "/public/images/front/",
            "icon-", "arrow", "flag",
        )):
            continue
        if "/upload/properties/" in low or "/photos/" in low:
            candidates.append(src)
    for src in candidates:
        abs_url = urljoin(page_url, src)
        key = re.sub(r"[?].*$", "", abs_url)
        if key in seen:
            continue
        seen.add(key)
        photos.append(abs_url)
    return photos


def _agency_from_slug(s: BeautifulSoup) -> str | None:
    named = _txt(s.select_one(".property_agency_name"))
    if named and named.lower() not in {"real estate agency", "agency"}:
        return named
    for a in s.select("a[href*='-a/']"):
        m = AGENCY_SLUG_RE.search(a.get("href") or "")
        if not m:
            continue
        slug = m.group(1).replace("_", " ").strip()
        # drop trailing city if the slug is "agency city"
        return slug.title() if slug else None
    return None


def parse_detail(html: str, url: str) -> dict[str, Any]:
    s = _soup(html)
    out: dict[str, Any] = {"url": url, "source": SOURCE, "agent": AGENT}
    raw: dict[str, Any] = {}

    ext = _listing_id(url)
    if not ext:
        id_line = _txt(s.select_one(".id")) or ""
        m = ID_IN_TEXT_RE.search(id_line) or ID_IN_TEXT_RE.search(
            s.get_text(" ", strip=True)[:2000]
        )
        if m:
            ext = m.group(1)
    if ext:
        out["url"] = canonical_detail_url(url, ext)
        out["external_id"] = ext
        out["reference"] = ext
        raw["listing_id"] = ext

    facts = _fact_rows(s)
    raw.update({f"fact_{k}": v for k, v in facts.items()})

    ptype = _txt(s.select_one(".property_title"))
    if not ptype:
        h1 = _txt(s.h1) or ""
        ptype = h1.split(",")[0].strip() or None
    if ptype:
        out["type"] = ptype

    place, region, country = _crumb_places(s)
    if not place:
        h1 = _txt(s.h1) or ""
        # "House, Suscepan, Herceg Novi Municipality, Montenegro"
        bits = [b.strip() for b in h1.split(",") if b.strip()]
        if len(bits) >= 2:
            place = bits[1]
        if len(bits) >= 3:
            region = region or bits[2]
        if len(bits) >= 4:
            country = country or bits[-1]
    if place:
        out["place"] = place
    if region:
        out["region"] = region
        out["dept_nl"] = region
    if country:
        raw["country"] = country

    labels = _txt(s.select_one(".property_labels"))
    deal = _deal(labels) or _deal(_txt(s.select_one(".title_hidden_data")))
    if deal:
        raw["deal"] = deal

    beds, baths = _beds_baths(s)
    if beds is not None:
        out["bedrooms"] = beds
        out["beds"] = beds
    if baths is not None:
        out["baths"] = baths

    living = _area(facts.get("floor area") or facts.get("total area")
                   or facts.get("living area"))
    if living is None:
        living = _area(_txt(s.select_one(".area_hidden_data")))
    land = _area(facts.get("building plot") or facts.get("plot")
                 or facts.get("land"))
    desc_block = _txt(s.select_one("#property_description .tagText"))
    if land is None and desc_block:
        pm = PLOT_RE.search(desc_block)
        if pm:
            land = _int(pm.group(1))
    out["living_m2"] = living
    out["land_m2"] = land

    money = parse_money_blob(
        " ".join(x for x in (
            facts.get("price"),
            _txt(s.select_one(".price_hidden_data")),
            _txt(s.select_one(".property_important_data")),
        ) if x)
    )
    priced = priced_row(money.get("EUR"), money.get("USD"))
    out["price"] = priced["price"]
    out["currency"] = priced["currency"]
    raw.update(priced["raw_fields"])

    desc = desc_block
    if not desc:
        desc = _meta(s, "property", "og:description") or _txt(s.h1)
    if desc:
        desc = re.sub(r"\n{3,}", "\n\n", desc)
    out["description"] = desc
    headline = _txt(s.select_one(".property_title")) or _txt(s.h1)
    out["snippet"] = headline
    raw["headline"] = headline

    agent_name = _agency_from_slug(s)
    if agent_name:
        out["agent_name"] = agent_name
        raw["agency"] = agent_name

    photos = _photos(s, url)
    out["photos"] = photos
    if photos:
        out["thumb"] = photos[0]

    out["raw_fields"] = raw
    return out
