"""Parsers for mubawab.ma/en list and detail pages.

Public EN HTML. House list cards are `div.listingBox` with `input.adId`
and `linkref=/en/a/{id}/slug`. The site's own MAD price filter is a
colon path (`/en/sc/houses-for-sale:pr:0-1100000`); pagination is
`:p:N` (page 1 omits it). Query-string `?maxPrice=` is ignored.

Premium cards (`sPremium`) stay mixed through `PRICE_ASC` — do not treat
sort as cheap-first. Project teasers (`adBoostBox`, `/en/p/…`) and the
trailing `emptyBox` are not listings.

The portal prints DH (JSON-LD `priceCurrency: MAD`) and sometimes EUR.
We store euro and keep the original in `raw_fields`.

robots.txt `Disallow: /*:` is an indexer rule for the colon filter/pager
— the site's own pager is `:p:N`, which we follow. Login / cms /
backoffice / ads/b are never fetched.
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

from .fx import FX_SOURCE, MAD_PER_EUR, MAD_TO_EUR, mad_to_eur
from .http import BASE

SOURCE = "mubawab"
PER_PAGE = 32  # live pageSize, verified 2026-09-19

ID_RE = re.compile(r"/[a-z]{2}/a/(\d+)/", re.I)
PAGE_RE = re.compile(r":p:(\d+)(?=:|$)", re.I)
RESULTS_RE = re.compile(r"([\d\s.,]+)\s+results?\b", re.I)
AREA_RE = re.compile(r"([\d][\d\s.,]*)\s*(?:m²|m2)\b", re.I)
PIECES_RE = re.compile(r"([\d]+)\s*Pieces?\b", re.I)
ROOMS_RE = re.compile(r"([\d]+)\s*Rooms?\b", re.I)
BATHS_RE = re.compile(r"([\d]+)\s*Bathrooms?\b", re.I)
DH_RE = re.compile(r"([\d][\d\s.,]*)\s*(?:DH|MAD)\b", re.I)
EUR_RE = re.compile(r"([\d][\d\s.,]*)\s*EUR\b", re.I)
PRICE_ON_REQUEST_RE = re.compile(r"price\s+on\s+request|prix\s+à\s+consulter", re.I)
FROM_PRICE_RE = re.compile(r"^From\b", re.I)
DECREASED_RE = re.compile(
    r"Decreased by\s+([\d][\d\s.,]*)\s*(EUR|DH|MAD)\b", re.I
)
PLOT_LABEL_RE = re.compile(r"^(plot|land)\s*(surface|area)?$", re.I)
LAND_PHRASE_RE = re.compile(
    r"(?:plot|land(?:\s+area)?|lot(?:\s+area)?)\s*(?:of|is|:)?\s*"
    r"([\d][\d\s.,]*)\s*(?:m²|m2)\b",
    re.I,
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
    m = ID_RE.search(urlparse(href).path)
    return m.group(1) if m else None


def canonical_detail_url(ext: str, slug: str = "") -> str:
    tail = slug.strip("/") if slug else ext
    return f"{BASE}/en/a/{ext}/{tail}"


def _page_number(page_url: str) -> int:
    m = PAGE_RE.search(urlparse(page_url).path)
    if m:
        return max(1, int(m.group(1)))
    return 1


def with_page(page_url: str, page: int) -> str:
    """Keep the `:pr:` / `:st:` filter tokens and set `:p:N`.

    Page 1 on the live site omits `:p:1`, so we do too — cache keys stay
    a function of the URL the site actually serves.
    """
    parsed = urlparse(page_url)
    path = PAGE_RE.sub("", parsed.path)
    if page > 1:
        path = f"{path}:p:{page}"
    return urlunparse(parsed._replace(path=path))


def parse_money(text: str | None) -> tuple[int | None, str | None]:
    """Return (amount, currency) from a DH / EUR price blob."""
    if not text:
        return None, None
    blob = str(text).replace("\xa0", " ")
    if PRICE_ON_REQUEST_RE.search(blob):
        return None, None
    m = EUR_RE.search(blob)
    if m:
        amount = _int(m.group(1))
        return amount, "EUR" if amount is not None else None
    m = DH_RE.search(blob)
    if m:
        amount = _int(m.group(1))
        return amount, "MAD" if amount is not None else None
    return None, None


def priced_row(amount: int | None, currency: str | None) -> dict[str, Any]:
    """Prefer the portal's euro figure; convert MAD with the documented rate."""
    raw: dict[str, Any] = {"country": "Morocco"}
    if amount is None or not currency:
        return {"price": None, "currency": "EUR", "raw_fields": raw}
    if currency == "EUR":
        raw.update({
            "price_eur": amount,
            "price_source": "portal_eur",
        })
        return {"price": amount, "currency": "EUR", "raw_fields": raw}
    eur = mad_to_eur(amount)
    raw.update({
        "price_mad": amount,
        "price_eur": eur,
        "price_source": "mad_converted",
        "fx_rate": MAD_TO_EUR,
        "fx_mad_per_eur": MAD_PER_EUR,
        "fx_source": FX_SOURCE,
    })
    return {"price": eur, "currency": "EUR", "raw_fields": raw}


def _split_place(text: str | None) -> tuple[str | None, str | None]:
    """'Drissia, Tanger' → (Drissia, Tanger). A single token is both."""
    if not text:
        return None, None
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], parts[-1]


def _is_listing_card(box) -> bool:
    classes = box.get("class") or []
    if "adBoostBox" in classes or "emptyBox" in classes:
        return False
    href = box.get("linkref") or ""
    if "/p/" in href:
        return False
    ad = box.select_one("input.adId")
    return bool(ad and ad.get("value"))


def _card_features(box) -> dict[str, int | None]:
    living = rooms = beds = baths = None
    for feat in box.select(".adDetailFeature"):
        text = _txt(feat) or ""
        icon = " ".join((feat.select_one("i") or {}).get("class") or [])
        if "icon-triangle" in icon or AREA_RE.search(text):
            living = living or _area(text)
        if "icon-house-boxes" in icon or PIECES_RE.search(text):
            m = PIECES_RE.search(text)
            rooms = rooms or (int(m.group(1)) if m else None)
        if "icon-bed" in icon or (ROOMS_RE.search(text) and "icon-house" not in icon):
            m = ROOMS_RE.search(text)
            beds = beds or (int(m.group(1)) if m else None)
        if "icon-bath" in icon or BATHS_RE.search(text):
            m = BATHS_RE.search(text)
            baths = baths or (int(m.group(1)) if m else None)
    return {"living_m2": living, "rooms": rooms, "beds": beds, "baths": baths}


def _thumb(box) -> str | None:
    img = box.select_one("img.firstPicture") or box.select_one("img.sliderImage")
    if img is None:
        return None
    src = img.get("data-lazy") or img.get("src")
    if not src:
        return None
    return urljoin(BASE + "/", src)


def _parse_card(box) -> dict[str, Any] | None:
    ad = box.select_one("input.adId")
    ext = (ad.get("value") if ad else None) or listing_id(box.get("linkref"))
    if not ext:
        return None
    href = box.get("linkref") or ""
    if listing_id(href) != ext:
        # Keep the portal's own link when the id matches; otherwise rebuild.
        title_a = box.select_one("h2.listingTit a[href]")
        href = (title_a.get("href") if title_a else "") or href
    if not listing_id(href):
        href = canonical_detail_url(ext)
    url = urljoin(BASE + "/", href)

    price_el = box.select_one(".priceTag")
    amount, ccy = parse_money(_txt(price_el))
    priced = priced_row(amount, ccy)

    title = _txt(box.select_one("h2.listingTit"))
    place, region = _split_place(_txt(box.select_one("span.listingH3")))
    feats = _card_features(box)
    extras = [_txt(x) for x in box.select(".adFeature")]
    extras = [x for x in extras if x]
    snippet = _txt(box.select_one("p.listingP"))
    classes = box.get("class") or []

    row: dict[str, Any] = {
        "source": SOURCE,
        "external_id": str(ext),
        "url": url,
        "type": "House",
        "place": place,
        "region": region,
        "living_m2": feats["living_m2"],
        "rooms": feats["rooms"],
        "beds": feats["beds"],
        "baths": feats["baths"],
        "snippet": snippet,
        "features": ", ".join(extras) if extras else None,
        "thumb": _thumb(box),
        "promoted": "sPremium" in classes,
        **priced,
    }
    if title:
        row["raw_fields"] = {**(row.get("raw_fields") or {}), "title": title}
    if FROM_PRICE_RE.search(_txt(price_el) or ""):
        # Project "From X DH" teaser — not a house asking price.
        return None
    return row


def _total_from_page(soup, page_url: str) -> tuple[int | None, int]:
    """(result_count, total_pages). Prefer #numResults, then pageUrlMap."""
    count = None
    nres = soup.select_one("#numResults")
    if nres:
        m = RESULTS_RE.search(_txt(nres) or "")
        if m:
            count = _int(m.group(1))
    pages = 1
    page_map = soup.select_one("#pageUrlMap")
    if page_map and page_map.get("value"):
        data = _json_load(page_map["value"])
        if isinstance(data, dict) and data:
            try:
                pages = max(int(k) for k in data)
            except ValueError:
                pages = len(data)
    if count is not None:
        pages = max(pages, math.ceil(count / PER_PAGE) if count else 1)
    if pages < 1:
        pages = 1
    # A lone page still has a current number.
    _ = page_url
    return count, pages


def parse_list(html: str, page_url: str) -> dict[str, Any]:
    soup = _soup(html)
    listings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for box in soup.select(".listingBox"):
        if not _is_listing_card(box):
            continue
        row = _parse_card(box)
        if not row:
            continue
        ext = row["external_id"]
        if ext in seen:
            continue
        seen.add(ext)
        listings.append(row)

    _count, total_pages = _total_from_page(soup, page_url)
    current = _page_number(page_url)
    hidden_page = soup.select_one("#currentPage")
    if hidden_page and hidden_page.get("value"):
        try:
            current = max(1, int(hidden_page["value"]))
        except ValueError:
            pass
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


def _ld_listing(blocks: list[dict]) -> dict:
    for block in blocks:
        if block.get("@type") in ("RealEstateListing", "Product", "Offer"):
            return block
    return blocks[0] if blocks else {}


def _feature_map(soup) -> dict[str, str]:
    out: dict[str, str] = {}
    for feat in soup.select(".adMainFeature"):
        label = _txt(feat.select_one(".adMainFeatureContentLabel"))
        value = _txt(feat.select_one(".adMainFeatureContentValue"))
        if label and value:
            out[label] = value
    return out


def _amenity_list(soup) -> list[str]:
    names = []
    for feat in soup.select(".adFeatures .adFeature"):
        t = _txt(feat)
        if t:
            names.append(t)
    return names


def _photos(ld: dict, soup, page_url: str) -> list[str]:
    urls: list[str] = []
    for img in _as_list(ld.get("image")):
        if isinstance(img, str) and img.startswith("http"):
            urls.append(img)
        elif isinstance(img, dict) and img.get("url"):
            urls.append(img["url"])
    if not urls:
        for img in soup.select(".gallery img, img.sliderImage, img.firstPicture"):
            src = img.get("data-lazy") or img.get("src")
            if src and not src.startswith("data:"):
                urls.append(urljoin(page_url, src))
    # de-dupe, keep order
    seen: set[str] = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _coords(soup) -> tuple[float | None, float | None]:
    holder = soup.select_one(".prop-map-holder[lat][lon]")
    if holder is not None:
        try:
            return float(holder["lat"]), float(holder["lon"])
        except (TypeError, ValueError, KeyError):
            pass
    lat_el = soup.select_one("#latField")
    lon_el = soup.select_one("#lngField")
    if lat_el and lon_el and lat_el.get("value") and lon_el.get("value"):
        try:
            return float(lat_el["value"]), float(lon_el["value"])
        except ValueError:
            pass
    return None, None


def _land_from_features_or_text(features: dict[str, str],
                                description: str | None) -> int | None:
    for label, value in features.items():
        if PLOT_LABEL_RE.match(label.strip()):
            area = _area(value)
            if area:
                return area
    if description:
        m = LAND_PHRASE_RE.search(description)
        if m:
            return _int(m.group(1))
    return None


def _old_price(text: str | None, price: int | None,
               currency: str | None) -> int | None:
    if not text or price is None:
        return None
    m = DECREASED_RE.search(text)
    if not m:
        return None
    drop, unit = _int(m.group(1)), m.group(2).upper()
    if drop is None:
        return None
    if unit in ("DH", "MAD") and currency == "MAD":
        return price + drop
    if unit == "EUR" and currency == "EUR":
        return price + drop
    if unit in ("DH", "MAD") and currency == "EUR":
        converted = mad_to_eur(drop)
        return price + converted if converted is not None else None
    return None


def parse_detail(html: str, url: str) -> dict[str, Any]:
    soup = _soup(html)
    ld = _ld_listing(_ld_blocks(soup))
    offered = ld.get("itemOffered") if isinstance(ld.get("itemOffered"), dict) else {}
    offer = ld.get("offers") if isinstance(ld.get("offers"), dict) else {}

    ext = listing_id(url) or listing_id(ld.get("url") or "")
    if not ext:
        fav = soup.select_one(".favDivPickId[id]")
        if fav and str(fav.get("id", "")).isdigit():
            ext = str(fav["id"])

    amount = None
    ccy = None
    if offer.get("price") is not None:
        try:
            amount = int(round(float(offer["price"])))
        except (TypeError, ValueError):
            amount = _int(str(offer.get("price")))
        raw_ccy = (offer.get("priceCurrency") or "").upper()
        if raw_ccy in ("MAD", "DH"):
            ccy = "MAD"
        elif raw_ccy == "EUR":
            ccy = "EUR"
    if amount is None:
        amount, ccy = parse_money(_txt(soup.select_one("h3.orangeTit")))

    priced = priced_row(amount, ccy)
    main = soup.select_one(".mainInfoProp")
    main_text = _txt(main)
    old = _old_price(main_text, amount, ccy)
    if old is not None and ccy == "MAD":
        priced["old_price"] = mad_to_eur(old)
        priced["raw_fields"] = {**(priced.get("raw_fields") or {}), "old_price_mad": old}
    elif old is not None:
        priced["old_price"] = old

    features = _feature_map(soup)
    amenities = _amenity_list(soup)
    living = None
    floor = offered.get("floorSize") if isinstance(offered.get("floorSize"), dict) else {}
    if floor.get("value") is not None:
        try:
            living = int(round(float(floor["value"])))
        except (TypeError, ValueError):
            living = _int(str(floor.get("value")))
    if living is None and main is not None:
        living = _card_features(main).get("living_m2")

    rooms = offered.get("numberOfRooms")
    beds = offered.get("numberOfBedrooms")
    baths = offered.get("numberOfBathroomsTotal")
    try:
        rooms = int(rooms) if rooms is not None else None
    except (TypeError, ValueError):
        rooms = None
    try:
        beds = int(beds) if beds is not None else None
    except (TypeError, ValueError):
        beds = None
    try:
        baths = int(baths) if baths is not None else None
    except (TypeError, ValueError):
        baths = None

    address = offered.get("address") if isinstance(offered.get("address"), dict) else {}
    locality = address.get("addressLocality")
    grey = _txt(soup.select_one("h3.greyTit"))
    place = grey or locality
    region = locality or grey
    ptype = features.get("Type of property") or "House"

    desc = None
    title_block = soup.select_one(".blockProp h1.searchTitle")
    if title_block is not None:
        desc = _txt(title_block.find_next("p"))
    if not desc:
        desc = ld.get("description")

    land = _land_from_features_or_text(features, desc)
    lat, lon = _coords(soup)
    photos = _photos(ld, soup, url)
    raw = dict(priced.get("raw_fields") or {})
    raw["country"] = "Morocco"
    if features:
        raw["characteristics"] = features
    if desc and re.search(r"\b(unregistered|titled|titre|agricultur)", desc, re.I):
        raw["ownership_note"] = (
            "portal copy mentions title/registration — "
            "Morocco: titled urban/peri-urban only; never ag land without a lawyer"
        )
    blob = " ".join(x for x in (desc, ld.get("name"), features.get("Type of property")) if x)
    if re.search(r"\bfor rent\b|\bà louer\b", blob, re.I):
        raw["maybe_rental"] = True

    out: dict[str, Any] = {
        "source": SOURCE,
        "external_id": str(ext) if ext else None,
        "url": ld.get("url") or url,
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
        "agent": None,
        **priced,
        "raw_fields": raw,
    }
    if out.get("old_price") is None and priced.get("old_price") is not None:
        out["old_price"] = priced["old_price"]
    return out
