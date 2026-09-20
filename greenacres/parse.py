"""Parsers for green-acres.fr list and detail pages.

NL UI path `/onroerend-goed` (Dutch buyer). House filter is the
`searchQuery` token `hab_house-on`. The `prc_max` token is ignored —
verified 2026-09-20, same 56,805-house catalogue as the unfiltered
house list. `mx_p-150000` *is* honoured (3,435 houses). Featured /
relevance sort paints luxury first; paginate via
`/nl/AdvertListingActions/AdvertsListing` with `order=price_i`.

`robots.txt` Disallow `*/AdvertListingActions/AdvertsListing` is an
indexer rule — the site's own pager is that JSON endpoint (`html`,
`advertsCount`, cards). Crawl-delay is 1s. `/*currency=` is also an
indexer rule; we never put `currency=` on a URL (datacenter HTML may
still paint `$` — convert, prefer `Prijs in euros` on detail).

Detail URLs hide in base64 `data-o` on `.announce-card` (`data-advertid`
is the id). A `/makelaar/` slug is the agency-listing template, not an
agency profile — those are still properties.

Listings may overlap franimo.nl and Le Figaro Immobilier. Store them
as `source=greenacres`; do not cross-source merge.
"""
from __future__ import annotations

import base64
import json
import math
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from .fx import FX_SOURCE, usd_to_eur
from .http import BASE

SOURCE = "greenacres"
PER_PAGE = 24
API_PATH = "/nl/AdvertListingActions/AdvertsListing"

ID_RE = re.compile(r"/([A-Za-z0-9]{8,})\.htm", re.I)
ADVERT_ID_RE = re.compile(r"^[A-Za-z0-9]{8,}$")
PAGE_RE = re.compile(r"(?:[?&]p_n=|page-number[^>]*value=\")(\d+)", re.I)
COUNT_RE = re.compile(
    r"([\d.\s\u00a0]+)\s+(?:woningen|huizen|properties|annonces|biens)\b",
    re.I,
)
RANGE_RE = re.compile(
    r"([\d.\s\u00a0]+)\s*[-–]\s*([\d.\s\u00a0]+)\s+op\s+([\d.\s\u00a0]+)",
    re.I,
)
# Do not use re.I on EUR — it matches the word "euros" in "Prijs in euros".
EUR_RE = re.compile(
    r"(?:([\d][\d\s\u00a0.]*)\s*(?:€|&euro;|&#x20AC;|\bEUR\b))"
    r"|(?:(?:€|&euro;|&#x20AC;|\bEUR\b)\s*([\d][\d\s\u00a0.]*))",
)
USD_RE = re.compile(
    r"(?:([\d][\d\s\u00a0.]*)\s*\$)|(?:\$\s*([\d][\d\s\u00a0.]*))",
)
EURO_LINE_RE = re.compile(
    r"(?:prijs\s+in\s+euros?|price\s+in\s+euros?)\s*:\s*([\d\s\u00a0.]+)",
    re.I,
)
AREA_RE = re.compile(r"([\d][\d\s\u00a0.]*)\s*(?:m²|m2|m&#xB2;)\b", re.I)
KAMERS_RE = re.compile(r"([\d]+)\s*kamers?\b", re.I)
SLAAP_RE = re.compile(r"([\d]+)\s*slaapkamers?\b", re.I)
BAD_RE = re.compile(r"([\d]+)\s*badkamers?\b", re.I)
DPE_RE = re.compile(
    r"(?:DPE|energieklasse|energieprestatie|verbruik)\s*[:\s]*([A-G])\b",
    re.I,
)
KWH_RE = re.compile(r"([\d][\d\s.,]*)\s*kWh", re.I)
CO2_RE = re.compile(r"([\d][\d\s.,]*)\s*kg\s*CO", re.I)
POSTAL_RE = re.compile(r"\((\d{5})\)")
LOC_RE = re.compile(
    r"^\s*([A-ZÀ-Ÿ0-9][^()]{1,60}?)\s*\(([^)]+)\)\s*$",
)
REF_RE = re.compile(
    r"(?:referentie|r[eé]f(?:[eé]rence|\.)?)\s*[:\s]*([A-Za-z0-9_./-]+)",
    re.I,
)
LOCATION_RE = re.compile(
    r"\b(?:te huur|huur\b|location|louer|\bà louer\b)\b",
    re.I,
)
GARAGE_RE = re.compile(r"\b(?:garage|box|parking|parkeerplaats)\b", re.I)
SKIP_PATH_RE = re.compile(
    r"/SignIn|/Authentication|/deleted-adverts|/Content/",
    re.I,
)

DEPT_SLUG = {
    "creuse": ("Creuse", "23"),
    "nievre": ("Nièvre", "58"),
    "haute-vienne": ("Haute-Vienne", "87"),
    "allier": ("Allier", "03"),
    "cantal": ("Cantal", "15"),
    "indre": ("Indre", "36"),
    "cher": ("Cher", "18"),
    "dordogne": ("Dordogne", "24"),
    "gers": ("Gers", "32"),
    "herault": ("Hérault", "34"),
    "lot": ("Lot", "46"),
    "aude": ("Aude", "11"),
    "val-d-oise": ("Val-d'Oise", "95"),
}

TYPE_ALIASES = {
    "house": "Huis",
    "huis": "Huis",
    "woning": "Huis",
    "villa": "Huis",
    "hoeve": "Huis",
    "boerderij": "Huis",
    "appartement": "Appartement",
    "apartment": "Appartement",
    "garage": "Garage",
    "box": "Garage",
    "parking": "Garage",
    "terrain": "Terrein",
    "terrein": "Terrein",
    "land": "Terrein",
    "prestigieus-pand": "Prestigieus pand",
    "castle": "Prestigieus pand",
}

LIVING_LABEL_RE = re.compile(
    r"^(?:woonoppervlakte|living|surface\s+habitable|habitable)$",
    re.I,
)
LAND_LABEL_RE = re.compile(
    r"^(?:terrein|terrain|parcelle|land|grond)$",
    re.I,
)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _txt(node) -> str | None:
    if node is None:
        return None
    t = node.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", t) or None


def _int(text: str | None) -> int | None:
    """Parse a displayed integer. `.` is thousands (`107.417`, `1.220`)."""
    if text is None:
        return None
    raw = str(text).replace("\xa0", " ").replace("&#x20AC;", "").strip()
    if not raw:
        return None
    raw = re.sub(r"[€$£]", "", raw).strip()
    compact = raw.replace(" ", "")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", compact):
        return int(compact.replace(".", ""))
    if "," in compact and "." not in compact:
        try:
            return int(round(float(compact.replace(",", "."))))
        except ValueError:
            return None
    digits = re.sub(r"[^\d]", "", compact)
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


def decode_data_o(value: str | None) -> str | None:
    """Detail URLs are base64 in `data-o` (research 2026-09-19)."""
    if not value:
        return None
    s = str(value).strip()
    pad = "=" * ((4 - len(s) % 4) % 4)
    try:
        out = base64.b64decode(s + pad).decode("utf-8", "replace")
    except (ValueError, UnicodeError):
        return None
    if not out or out == "#":
        return None
    if SKIP_PATH_RE.search(out):
        return None
    return out


def listing_id(href: str | None) -> str | None:
    if not href:
        return None
    m = ID_RE.search(str(href))
    if m:
        return m.group(1)
    if ADVERT_ID_RE.match(str(href).strip()):
        return str(href).strip()
    return None


def canonical_detail_url(ext: str, slug: str = "huis", place: str = "") -> str:
    town = place.strip("/").replace(" ", "-").lower() or "france"
    return f"{BASE}/nl/properties/{slug}/{town}/{ext}.htm"


def is_blocked(html: str) -> bool:
    """Cloudflare / hard block — not a list page. Recaptcha on contact is fine."""
    if not html:
        return False
    blob = html[:8000]
    return (
        "cf-error-details" in blob
        or "Sorry, you have been blocked" in blob
        or "Just a moment" in blob
        or "cdn-cgi/challenge" in blob
    )


def parse_money(text: str | None) -> tuple[int | None, str | None]:
    """Return (amount, currency) from a € / $ price blob."""
    if not text:
        return None, None
    blob = str(text).replace("\xa0", " ")
    em = EURO_LINE_RE.search(blob)
    if em:
        amount = _int(em.group(1))
        if amount is not None:
            return amount, "EUR"
    m = EUR_RE.search(blob)
    if m:
        amount = _int(m.group(1) or m.group(2))
        return amount, "EUR" if amount is not None else None
    m = USD_RE.search(blob)
    if m:
        amount = _int(m.group(1) or m.group(2))
        return amount, "USD" if amount is not None else None
    return None, None


def priced_row(amount: int | None, currency: str | None) -> dict[str, Any]:
    """Store euro. Keep the original in raw extras when we convert."""
    raw: dict[str, Any] = {}
    if currency and currency != "EUR" and amount is not None:
        raw["price_original"] = amount
        raw["currency_original"] = currency
        raw["fx"] = FX_SOURCE
    if currency == "USD":
        return {"price": usd_to_eur(amount), "currency": "EUR", "raw": raw}
    return {"price": amount, "currency": "EUR" if amount is not None else None, "raw": raw}


def _pretty_type(raw: str | None, fallback: str = "Huis") -> str:
    if not raw:
        return fallback
    key = re.sub(r"\s+", "-", raw.strip().lower())
    return TYPE_ALIASES.get(key, raw.strip().capitalize() if raw else fallback)


def _dept_from_path(page_url: str) -> tuple[str | None, str | None]:
    path = urlparse(page_url).path.lower().strip("/")
    for slug, (name, code) in DEPT_SLUG.items():
        if path.endswith("/" + slug) or path.endswith("/" + slug + "/"):
            return name, code
    return None, None


def _place_region(text: str | None) -> tuple[str | None, str | None]:
    if not text:
        return None, None
    blob = re.sub(r"\s+", " ", text).strip()
    m = LOC_RE.match(blob)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return blob or None, None


def _search_tokens(page_url: str) -> dict[str, str]:
    """Tokens from `searchQuery=lg-nl-cn-fr-hab_house-on-mx_p-150000`."""
    parsed = urlparse(page_url)
    q = dict(parse_qsl(parsed.query, keep_blank_values=True))
    blob = q.get("searchQuery") or q.get("searchquery") or ""
    if "UrlConditions" in q and not blob:
        blob = q["UrlConditions"]
    tokens: dict[str, str] = {}
    parts = [p for p in blob.split("-") if p]
    i = 0
    while i < len(parts):
        key = parts[i].lower()
        if key in {"hab_house", "hab_appartement", "hab_castle"} and i + 1 < len(parts):
            tokens[key] = parts[i + 1]
            i += 2
            continue
        if key in {"lg", "cn", "mx_p", "mn_p", "city_id", "prc_max"} and i + 1 < len(parts):
            tokens[key] = parts[i + 1]
            i += 2
            continue
        if key == "currency":
            i += 2
            continue
        i += 1
    for k in ("hab_house", "mx_p", "mn_p", "cn", "lg", "city_id", "p_n", "order"):
        if q.get(k):
            tokens[k] = q[k]
    path = parsed.path.lower()
    if "creuse" in path and "city_id" not in tokens:
        tokens["city_id"] = "dp_23"
    if "/huis" in path or tokens.get("hab_house") == "on":
        tokens["hab_house"] = "true"
    return tokens


def listing_api_url(page_url: str, page: int = 1, order: str = "price_i") -> str:
    """AdvertsListing GET. Featured HTML `?page=` is a no-op — use this."""
    tokens = _search_tokens(page_url)
    parsed = urlparse(page_url)
    if "AdvertListingActions/AdvertsListing" in parsed.path:
        q = {k: v for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if k.lower() != "p_n"}
        q["p_n"] = str(max(1, page))
        q.setdefault("order", order)
        q.setdefault("advertType", "Property")
        return urlunparse(parsed._replace(query=urlencode(q)))

    q: list[tuple[str, str]] = [
        ("advertType", "Property"),
        ("p_n", str(max(1, page))),
        ("order", tokens.get("order") or order),
        ("cn", tokens.get("cn") or "fr"),
        ("lg", tokens.get("lg") or "nl"),
        ("type", "properties"),
    ]
    if tokens.get("hab_house") in {"on", "true", "1"}:
        q.append(("hab_house", "true"))
    if tokens.get("mx_p"):
        q.append(("mx_p", re.sub(r"[^\d]", "", tokens["mx_p"]) or tokens["mx_p"]))
    if tokens.get("mn_p"):
        q.append(("mn_p", re.sub(r"[^\d]", "", tokens["mn_p"]) or tokens["mn_p"]))
    if tokens.get("city_id"):
        q.append(("city_id", tokens["city_id"]))
    return f"{BASE}{API_PATH}?{urlencode(q)}"


def with_page(page_url: str, page: int) -> str:
    """Page 1 of an HTML search stays on the SEO path; page 2+ is the API.

    `?page=` on `/onroerend-goed` does not paginate. Cache keys stay a
    function of the URL the site actually serves.
    """
    parsed = urlparse(page_url)
    if page <= 1 and "AdvertListingActions" not in parsed.path:
        q = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if k.lower() not in {"page", "p_n"}]
        return urlunparse(parsed._replace(query=urlencode(q)))
    return listing_api_url(page_url, page)


def _page_number(page_url: str) -> int:
    parsed = urlparse(page_url)
    q = dict(parse_qsl(parsed.query))
    if q.get("p_n"):
        try:
            return max(1, int(q["p_n"]))
        except ValueError:
            return 1
    m = PAGE_RE.search(page_url)
    if m:
        return max(1, int(m.group(1)))
    return 1


def _unwrap(html: str) -> tuple[str, int | None]:
    """Accept AdvertsListing JSON (`html`, `advertsCount`) or raw HTML."""
    text = html.lstrip()
    if text.startswith("{") or text.startswith("["):
        data = _json_load(text)
        if isinstance(data, dict) and "html" in data:
            count = data.get("advertsCount")
            try:
                count = int(count) if count is not None else None
            except (TypeError, ValueError):
                count = None
            return str(data.get("html") or ""), count
    return html, None


def _area(text: str | None) -> int | None:
    if not text:
        return None
    m = AREA_RE.search(str(text).replace("\xa0", " "))
    if not m:
        return None
    return _int(m.group(1))


def _type_from_card(box, href: str | None, title: str | None, snippet: str | None
                    ) -> str:
    seo = _txt(box.select_one(".advert-seo-tag"))
    for blob in (seo, title, href or ""):
        if not blob:
            continue
        low = str(blob).lower()
        if "appartement" in low:
            return "Appartement"
        if "garage" in low or "/box" in low:
            return "Garage"
        if "terrein" in low or "terrain" in low:
            return "Terrein"
        if "huis" in low or "woning" in low or "/huis/" in low:
            return "Huis"
    if snippet and GARAGE_RE.search(snippet) and "huis" not in (title or "").lower():
        return "Garage"
    return "Huis"


def _parse_card(box, page_url: str) -> dict[str, Any] | None:
    if box.has_attr("class") and "skeleton" in box.get("class", []):
        return None
    ext = box.get("data-advertid")
    href = decode_data_o(box.get("data-o"))
    if not ext:
        ext = listing_id(href)
    if not ext:
        return None
    if href and SKIP_PATH_RE.search(href):
        return None
    url = href
    if url and url.startswith("/"):
        url = urljoin(BASE + "/", url)
    if not url:
        url = canonical_detail_url(ext)

    title = box.get("title") or (box.select_one(".announce-info") or {}).get("title")
    info = box.select_one(".announce-info")
    if info is not None and not title:
        title = info.get("title")
    loc_txt = _txt(box.select_one(".announce-localisation"))
    place, region = _place_region(loc_txt)
    if not region:
        region, _ = _dept_from_path(page_url)

    price_blob = _txt(box.select_one(".info-price")) or _txt(box)
    amount, cur = parse_money(price_blob)
    priced = priced_row(amount, cur)

    living = land = rooms = beds = None
    for tag in box.select(".info-tag"):
        label = tag.get("title") or ""
        val = _txt(tag)
        if LIVING_LABEL_RE.match(label.strip()):
            living = _area(val) or _int(val)
        elif LAND_LABEL_RE.match(label.strip()):
            land = _area(val) or _int(val)
        elif re.match(r"^kamers?$", label.strip(), re.I):
            rooms = _int(val)
        elif re.match(r"^slaapkamers?$", label.strip(), re.I):
            beds = _int(val)
    text = _txt(box) or ""
    if rooms is None:
        m = KAMERS_RE.search(text)
        if m:
            rooms = int(m.group(1))
    if beds is None:
        m = SLAAP_RE.search(text)
        if m:
            beds = int(m.group(1))
    if living is None:
        living = _area(_txt(box.select_one('.info-tag[title="Woonoppervlakte"]')))
    if land is None:
        tm = re.search(r"([\d.\s]+)\s*m[²2].{0,12}terrein", text, re.I)
        if tm:
            land = _int(tm.group(1))

    snippet = _txt(box.select_one(".description-details")) or title
    ptype = _type_from_card(box, url, title, snippet)
    img = box.select_one("img.announce-card-img[src], img.announce-card-img[data-lazy-src]")
    thumb = None
    if img is not None:
        src = img.get("src") or img.get("data-lazy-src")
        if src and not str(src).startswith("data:"):
            thumb = urljoin(BASE + "/", src)
    postal = None
    if title:
        pm = POSTAL_RE.search(title)
        if pm:
            postal = pm.group(1)
    agent = _txt(box.select_one(".company-name"))
    if snippet and LOCATION_RE.search(snippet) and "kopen" not in snippet.lower():
        # A leftover rental label — surface it, don't hide.
        pass

    raw: dict[str, Any] = {"country": "France"}
    raw.update(priced.get("raw") or {})
    if title:
        raw["title"] = title
    if postal:
        raw["postal_code"] = postal
    if region:
        raw["department"] = region
    if agent:
        raw["agent"] = agent

    return {
        "source": SOURCE,
        "external_id": str(ext),
        "url": url,
        "type": ptype,
        "place": place,
        "region": region,
        "dept_fr": region,
        "dept_nl": region,
        "price": priced["price"],
        "currency": "EUR",
        "living_m2": living,
        "land_m2": land,
        "rooms": rooms,
        "bedrooms": beds,
        "thumb": thumb,
        "snippet": snippet,
        "agent": agent,
        "raw_fields": raw,
    }


def _total_from_page(soup, json_count: int | None, listings: list) -> int:
    if json_count:
        return json_count
    info = _txt(soup.select_one(".pagination-info")) or ""
    m = RANGE_RE.search(info)
    if m:
        return _int(m.group(3)) or 0
    h1 = _txt(soup.select_one("h1")) or _txt(soup.title) or ""
    m = COUNT_RE.search(h1) or COUNT_RE.search(info)
    if m:
        return _int(m.group(1)) or 0
    for script in soup.find_all("script", type="application/ld+json"):
        data = _json_load((script.string or script.get_text() or "").strip())
        if not isinstance(data, dict):
            continue
        offers = data.get("offers")
        if isinstance(offers, dict) and offers.get("offerCount"):
            n = _int(offers.get("offerCount"))
            if n:
                return n
    return len(listings)


def parse_list(html: str, page_url: str) -> dict[str, Any]:
    if is_blocked(html):
        return {"listings": [], "total_pages": 1, "next_url": None, "blocked": True}
    body, json_count = _unwrap(html)
    soup = _soup(body)
    listings: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(row: dict[str, Any] | None) -> None:
        if not row:
            return
        ext = row.get("external_id")
        if not ext or ext in seen:
            return
        seen.add(str(ext))
        listings.append(row)

    for box in soup.select(".announce-card"):
        _add(_parse_card(box, page_url))

    count = _total_from_page(soup, json_count, listings)
    pages = math.ceil(count / PER_PAGE) if count else (1 if listings else 1)
    pages = max(1, pages)
    current = _page_number(page_url)
    next_url = with_page(page_url, current + 1) if current < pages else None
    return {
        "listings": listings,
        "total_pages": pages,
        "next_url": next_url,
    }


def _photos(soup, page_url: str) -> list[str]:
    urls: list[str] = []
    for img in soup.select("img[src], img[data-src], img[data-lazy-src]"):
        src = img.get("data-src") or img.get("data-lazy-src") or img.get("src")
        if not src or str(src).startswith("data:"):
            continue
        abs_u = urljoin(page_url, src)
        if "green-acres.com" in abs_u or "vizzit.com" in abs_u:
            # Prefer full Photos/ over miniPhotos/
            urls.append(abs_u)
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        key = re.sub(r"\?.*$", "", u).replace("/miniPhotos/", "/Photos/")
        if key not in seen:
            seen.add(key)
            out.append(u.replace("/miniPhotos/", "/Photos/"))
    return out


def _coords(html: str, soup) -> tuple[float | None, float | None]:
    m = re.search(
        r"window\.advert\s*=\s*\{[^}]*?latitude:\s*([0-9.]+)[^}]*?longitude:\s*([0-9.]+)",
        html,
        re.S,
    )
    if m:
        return _float(m.group(1)), _float(m.group(2))
    return None, None


def parse_detail(html: str, url: str) -> dict[str, Any]:
    if is_blocked(html):
        return {"source": SOURCE, "url": url, "raw_fields": {"blocked": True}}
    soup = _soup(html)
    ext = listing_id(url)
    hid = soup.select_one("#AdvertId, input[name=AdvertId]")
    if hid is not None and hid.get("value"):
        ext = hid.get("value") or ext

    euro_el = soup.select_one(".advert-currency-price")
    amount, cur = parse_money(_txt(euro_el))
    if amount is None:
        amount, cur = parse_money(_txt(soup.select_one(".price-detail, .sticky-price")))
    if amount is None:
        amount, cur = parse_money(_txt(soup.find("body")))
    # Prefer the explicit euro line even when $ is painted first.
    if euro_el is not None:
        e_amt, e_cur = parse_money(_txt(euro_el))
        if e_amt is not None:
            amount, cur = e_amt, e_cur or "EUR"
    priced = priced_row(amount, cur)

    loc_txt = _txt(soup.select_one(".announce-localisation, .location-text"))
    place, region = _place_region(loc_txt)
    if not place or not region:
        # 'Lokalisatie : La Souterraine (Creuse)'
        body_loc = re.search(
            r"Lokalisatie\s*:\s*([^<\n]+)",
            html,
            re.I,
        )
        if body_loc:
            place, region = _place_region(body_loc.group(1)) or (place, region)
    if not region:
        region, _ = _dept_from_path(url)

    living = land = rooms = beds = baths = None
    for tag in soup.select(".info-tag"):
        label = (tag.get("title") or "").strip()
        val = _txt(tag)
        if LIVING_LABEL_RE.match(label):
            living = _area(val) or _int(val)
        elif LAND_LABEL_RE.match(label):
            land = _area(val) or _int(val)
        elif re.match(r"^kamers?$", label, re.I):
            rooms = _int(val)
        elif re.match(r"^slaapkamers?$", label, re.I):
            beds = _int(val)
    for lab in soup.select(".tag__label"):
        t = _txt(lab) or ""
        if beds is None:
            m = SLAAP_RE.search(t)
            if m:
                beds = int(m.group(1))
        if baths is None:
            m = BAD_RE.search(t)
            if m:
                baths = int(m.group(1))
        if land is None and "m" in t.lower() and "terrein" not in t.lower():
            # '1.220 m²' next to a land icon — only if no living already claimed it
            pass
    if land is None:
        for lab in soup.select(".tag__label"):
            t = _txt(lab) or ""
            if AREA_RE.search(t) and living and _area(t) and _area(t) != living:
                land = _area(t)
                break
    desc = _txt(soup.select_one(".main-description, .description-text, .description"))
    if land is None and desc:
        m = re.search(r"([\d.\s]+)\s*m[²2]\s*terrein", desc, re.I)
        if m:
            land = _int(m.group(1))
    if living is None:
        living = _area(_txt(soup.select_one('.info-tag[title="Woonoppervlakte"]')))

    energy = None
    kwh = None
    gas_co2 = None
    grade = soup.select_one(".ValueTag__ValueTagGrade")
    if grade is not None:
        g = (_txt(grade) or "").strip().upper()
        if re.fullmatch(r"[A-G]", g):
            energy = g
    body = " ".join(x for x in (desc, _txt(soup.find("body"))) if x)
    if energy is None:
        dm = DPE_RE.search(body or "")
        if dm:
            energy = dm.group(1).upper()
    km = KWH_RE.search(body or "")
    if km:
        kwh = _int(km.group(1))
    cm = CO2_RE.search(body or "")
    if cm:
        gas_co2 = _int(cm.group(1))

    ref = None
    for lab in soup.select(".label-value"):
        label = _txt(lab.select_one("label"))
        value = _txt(lab.select_one("p"))
        if label and value and re.search(r"referentie|r[eé]f", label, re.I):
            ref = value
            break
    if not ref:
        rm = REF_RE.search(body or "")
        if rm:
            ref = rm.group(1).rstrip(".,;:")

    agent = _txt(soup.select_one(".company-name"))
    name = _txt(soup.select_one("h1.main-title, h1")) or _txt(soup.title)
    ptype = _pretty_type(None)
    for blob in (name, url, desc):
        if blob and "appartement" in str(blob).lower():
            ptype = "Appartement"
            break
        if blob and GARAGE_RE.search(str(blob)) and (living or 0) < 30:
            ptype = "Garage"
            break
        if blob and re.search(r"\bhuis\b|\bwoning\b", str(blob), re.I):
            ptype = "Huis"
            break
    if "/appartement/" in (url or ""):
        ptype = "Appartement"
    elif "/huis/" in (url or ""):
        ptype = "Huis"

    lat, lon = _coords(html, soup)
    photos = _photos(soup, url)
    postal = None
    if name:
        pm = POSTAL_RE.search(name)
        if pm:
            postal = pm.group(1)
    raw: dict[str, Any] = {"country": "France"}
    raw.update(priced.get("raw") or {})
    if postal:
        raw["postal_code"] = postal
    if region:
        raw["department"] = region
    if name:
        raw["title"] = name
    if LOCATION_RE.search(" ".join(x for x in (name, desc) if x) or ""):
        raw["maybe_rental"] = True

    return {
        "source": SOURCE,
        "external_id": str(ext) if ext else None,
        "url": url,
        "type": ptype,
        "place": place,
        "region": region,
        "dept_fr": region,
        "dept_nl": region,
        "price": priced["price"],
        "currency": "EUR",
        "living_m2": living,
        "land_m2": land,
        "rooms": rooms,
        "bedrooms": beds,
        "baths": baths,
        "energy_label": energy,
        "energy_kwh": kwh,
        "gas_co2": gas_co2,
        "reference": ref,
        "agent": agent,
        "description": desc,
        "photos": photos or None,
        "thumb": (photos[0] if photos else None),
        "lat": lat,
        "lon": lon,
        "raw_fields": raw,
    }
