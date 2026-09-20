"""Parsers for cheap-bulgarian-house.co.uk list and detail pages.

The site is classic PHP HTML (windows-1251). List cards are
`table.table_prop_item`; detail facts live in `table.buttons_details`.
Selling prices are euro — the site says so on every listing — with a
pound figure next to them for UK readers.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from .fx import GBP_TO_EUR, FX_SOURCE, gbp_to_eur
from .http import BASE

SOURCE = "ok_bulgaria"
DETAIL_PATH = "/houses_in_bulgaria_for_sale.php"

# Pretty-URL slug on the card's "details »" link, plus the `tid` query.
TYPE_SLUGS = {
    "one_bedroom": "1 bedroom",
    "two_bedrooms": "2 bedrooms",
    "three_bedrooms": "3 bedrooms",
    "four_bedrooms": "4 bedrooms",
    "five_bedrooms": "5 bedrooms",
    "land_agrucultural": "land - agricultural",  # site's own spelling
    "land_agricultural": "land - agricultural",
    "land_for_building": "land for building",
    "commerce": "commerce",
    "hotel": "hotel",
    "renovation": "renovation",
}
TID_TYPES = {
    "11": "2 bedrooms",
    "12": "3 bedrooms",
    "14": "commerce",
    "16": "land for building",
    "17": "land - agricultural",
}
LAND_TYPES = {"land - agricultural", "land for building"}


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
    """'707 m2' / '2700 sq.m.' / '100 sqm' -> 2700."""
    if not text:
        return None
    m = re.search(r"([\d][\d\s.,]*)\s*(?:sq\.?\s*m|sqm|m2|m²)", text, re.I)
    if m:
        return _int(m.group(1))
    return _int(text)


def parse_money(text: str | None) -> tuple[int | None, str | None]:
    """'€ 2,700' / '£1,286' -> (2700, 'EUR') / (1286, 'GBP')."""
    if not text:
        return None, None
    m = re.search(r"([€£])\s*([\d.,\s]+)", text.replace("\xa0", " "))
    if not m:
        return None, None
    amount = _int(m.group(2))
    currency = "EUR" if m.group(1) == "€" else "GBP"
    return amount, currency


def _card_prices(box) -> tuple[int | None, int | None]:
    eur = gbp = None
    for el in box.select(".prop_price, .prop_price1"):
        amount, cur = parse_money(_txt(el))
        if amount is None:
            continue
        if cur == "EUR" and eur is None:
            eur = amount
        elif cur == "GBP" and gbp is None:
            gbp = amount
    # tooltip also has the euro figure on some cards
    if eur is None:
        tip = box.select_one(".tooltip")
        amount, cur = parse_money(_txt(tip))
        if cur == "EUR":
            eur = amount
    return eur, gbp


def priced_row(eur: int | None, gbp: int | None) -> dict[str, Any]:
    """Prefer the portal's euro selling price; convert only if that's missing."""
    raw: dict[str, Any] = {}
    if eur is not None:
        raw["price_eur"] = eur
    if gbp is not None:
        raw["price_gbp"] = gbp
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
    return {"price": None, "currency": "EUR", "raw_fields": raw}


# The headline is where this site states floor area, and it lists several areas
# in one line: "70 sqm derelict house, 30 sqm derelict barn, 1300 sqm plot".
# Only the dwelling counts, so match per comma-separated clause and require a
# dwelling noun in the same clause — a barn, a plot or a forest must not become
# living space.
_DWELLING = r"living|house|home|villa|bungalow|cottage|apartment|property"
_NOT_DWELLING = re.compile(r"\b(barn|plot|land|forest|garage|shed|outbuilding|"
                           r"annex|ruin(?:s)?|stable|orchard|meadow|yard)\b", re.I)
_AREA_THEN_DWELLING = re.compile(
    r"([\d][\d\s.,]*)\s*(?:sq\.?\s*m\.?|sqm|m2|m²)\s+"      # "70 sqm"
    r"(?:[a-z-]+\s+){0,2}"                                      # "derelict "
    r"(?:" + _DWELLING + r")\b",                                # "house"
    re.I,
)


def _living_from_text(blob: str | None) -> int | None:
    """First dwelling area stated in a headline, or None."""
    for clause in re.split(r"[,;]", blob or ""):
        if _NOT_DWELLING.search(clause):
            continue
        m = _AREA_THEN_DWELLING.search(clause)
        if m:
            return _int(m.group(1))
    return None


def _place_from_alt(alt: str | None) -> tuple[str | None, str | None]:
    """'property, house in NIKOLAEVKA, VARNA, Bulgaria' -> (village, province)."""
    if not alt:
        return None, None
    m = re.search(r"\bin\s+(.+?),\s*Bulgaria\b", alt, re.I)
    if not m:
        return None, None
    parts = [p.strip() for p in m.group(1).split(",") if p.strip()]
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0].title(), None
    return parts[0].title(), parts[-1].title()


def _type_from_card(box, href: str) -> str | None:
    details = box.select_one(".prop_item a[href]")
    slug_href = (details.get("href") if details else "") or ""
    m = re.match(r"([a-z0-9_]+)_id\d+", slug_href, re.I)
    if m:
        slug = m.group(1).lower()
        if slug in TYPE_SLUGS:
            return TYPE_SLUGS[slug]
        return slug.replace("_", " ")
    qs = parse_qs(urlparse(href).query)
    tid = (qs.get("tid") or [None])[0]
    return TID_TYPES.get(tid) if tid else None


def _label_value(box, label: str) -> str | None:
    for el in box.select(".prop_lbl"):
        if (_txt(el) or "").rstrip(":").strip().lower() == label.lower():
            nxt = el.find_next("span", class_="prop_txt")
            return _txt(nxt)
    return None


def _reference(box) -> str | None:
    el = box.select_one(".prop_price1")
    text = _txt(el) or ""
    m = re.search(r"ID#\s*(\S+)", text, re.I)
    return m.group(1) if m else None


def _thumb(box) -> str | None:
    td = box.find("td", style=re.compile(r"background-image", re.I))
    if td:
        m = re.search(r"url\(['\"]?([^'\")]+)['\"]?\)", td.get("style") or "")
        if m:
            return urljoin(BASE + "/", m.group(1))
    img = box.select_one("a.thumbnail span img[src]")
    if img:
        return urljoin(BASE + "/", img["src"])
    return None


def canonical_detail_url(listing_id: str) -> str:
    return f"{BASE}{DETAIL_PATH}?id={listing_id}"


def _page_number(page_url: str) -> int:
    qs = parse_qs(urlparse(page_url).query)
    raw = (qs.get("page") or ["1"])[0]
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def with_page(page_url: str, page: int) -> str:
    """Keep the search query (price band etc.) and set page=N.

    Do not follow the site's `use_session=yes` pager: that URL is not a
    stable cache key because it depends on a PHP session.
    """
    parsed = urlparse(page_url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs.pop("use_session", None)
    qs["page"] = [str(page)]
    # parse_qs gives lists; urlencode doseq keeps them
    query = urlencode({k: v[0] if len(v) == 1 else v for k, v in qs.items()}, doseq=True)
    return urlunparse(parsed._replace(query=query))


def parse_list(html: str, page_url: str) -> dict[str, Any]:
    s = _soup(html)

    total_pages = 1
    max_el = s.select_one(".pagination input[data-max-page]")
    if max_el and max_el.get("data-max-page"):
        try:
            total_pages = max(1, int(max_el["data-max-page"]))
        except ValueError:
            total_pages = 1

    current = _page_number(page_url)
    next_url = with_page(page_url, current + 1) if current < total_pages else None

    listings = []
    seen: set[str] = set()
    for box in s.select("table.table_prop_item"):
        link = box.select_one('a[href*="houses_in_bulgaria_for_sale.php"][href*="id="]')
        if not link:
            continue
        href = urljoin(page_url, link["href"])
        qs = parse_qs(urlparse(href).query)
        ext = (qs.get("id") or [None])[0]
        if not ext or ext in seen:
            continue
        seen.add(ext)

        alt = None
        for img in box.select("img[alt]"):
            if img.get("alt"):
                alt = img["alt"]
                break
        place, region = _place_from_alt(alt)
        eur, gbp = _card_prices(box)
        priced = priced_row(eur, gbp)
        land = _area(_label_value(box, "Land"))
        ptype = _type_from_card(box, href)
        snippet = None
        for span in box.select("span.prop_txt"):
            t = _txt(span)
            if t and not re.search(r"\d+\s*(?:m2|sq)", t, re.I) and not re.match(r"\d+\s*»", t):
                if not re.match(r"\d{1,2}-\d{1,2}-\d{4}$", t):
                    snippet = t
                    break
        beds = None
        if ptype:
            m = re.match(r"(\d+)\s+bedroom", ptype)
            if m:
                beds = int(m.group(1))

        listings.append({
            "source": SOURCE,
            "external_id": str(ext),
            "id": int(ext) if ext.isdigit() else ext,
            "url": canonical_detail_url(ext),
            "type": ptype,
            "place": place,
            "region": region,
            "dept_nl": region,
            "price": priced["price"],
            "currency": priced["currency"],
            "beds": beds,
            "bedrooms": beds,
            "land_m2": land,
            "reference": _reference(box),
            "thumb": _thumb(box),
            "snippet": snippet,
            "promoted": bool(box.find(string=re.compile(r"^\s*NEW\s*$"))),
            "raw_fields": priced["raw_fields"],
        })

    return {"listings": listings, "total_pages": total_pages, "next_url": next_url}


# --------------------------------------------------------------------------
# detail pages
# --------------------------------------------------------------------------

def _bold_fields(block) -> dict[str, str]:
    """'<b>Type:</b> 2 bedrooms, <b>Furnished</b>' -> {type: '2 bedrooms, Furnished', ...}."""
    out: dict[str, str] = {}
    if block is None:
        return out
    html = str(block)
    # split on <b>Label:</b>
    parts = re.split(r"<b>\s*([^<:][^<]*?):\s*</b>", html, flags=re.I)
    # parts[0] preamble, then label, value, label, value...
    for i in range(1, len(parts) - 1, 2):
        label = re.sub(r"\s+", " ", parts[i]).strip().lower()
        # one fact per <br> row — don't swallow the village/province block
        value_html = re.split(r"<br\s*/?>", parts[i + 1], 1, flags=re.I)[0]
        value = BeautifulSoup(value_html, "lxml").get_text(" ", strip=True)
        value = re.sub(r"\s+", " ", value).strip(" ,")
        if label and value:
            out[label] = value
    return out


def _bedrooms_from_type(ptype: str | None) -> int | None:
    if not ptype:
        return None
    m = re.search(r"(\d+)\s+bedroom", ptype, re.I)
    return int(m.group(1)) if m else None


def parse_detail(html: str, url: str) -> dict[str, Any]:
    s = _soup(html)
    out: dict[str, Any] = {"url": url, "source": SOURCE, "agent": "OK Bulgaria"}
    raw: dict[str, str] = {}

    qs = parse_qs(urlparse(url).query)
    if qs.get("id"):
        out["external_id"] = qs["id"][0]

    facts = s.select_one("table.buttons_details")
    facts_div = None
    if facts:
        for div in facts.select("div"):
            if div.find("b") and "Type:" in (div.get_text() or ""):
                facts_div = div
                break
    fields = _bold_fields(facts_div or facts or s)
    raw.update(fields)

    ptype = fields.get("type")
    if ptype:
        # "2 bedrooms, Furnished" — keep the full string; bedrooms from the leading number
        out["type"] = ptype
        out["bedrooms"] = _bedrooms_from_type(ptype)
        if "furnished" in ptype.lower():
            out["features"] = "Furnished"
    if fields.get("land"):
        out["land_m2"] = _area(fields["land"])

    # prices: first euro / pound in the facts table
    if facts:
        eur = gbp = None
        for span in facts.select("span"):
            amount, cur = parse_money(_txt(span))
            if amount is None:
                continue
            if cur == "EUR" and eur is None:
                eur = amount
            elif cur == "GBP" and gbp is None:
                gbp = amount
        priced = priced_row(eur, gbp)
        out["price"] = priced["price"]
        out["currency"] = priced["currency"]
        raw.update(priced["raw_fields"])

        id_m = re.search(r"ID:\s*(\S+)", facts.get_text(" ", strip=True), re.I)
        if id_m:
            out["reference"] = id_m.group(1)

        vill = facts.find(string=re.compile(r"village:", re.I))
        if vill:
            link = vill.find_parent("td").find("a") if vill.find_parent("td") else None
            village_txt = _txt(link) or ""
            # "CHUCHULIGOVO (pop. 178) - 3 properties"
            vm = re.match(r"([^(-]+)", village_txt)
            if vm:
                out["place"] = vm.group(1).strip().title()
        prov = facts.find("a", href=re.compile(r"houses_in_bulgaria_for_sale_county_[^_]+_\d+$"))
        if prov:
            pt = _txt(prov) or ""
            pt = re.sub(r"\s*Province.*$", "", pt, flags=re.I).strip()
            if pt:
                out["region"] = pt.title()
                out["dept_nl"] = out["region"]

    title = _txt(s.title) or ""
    h3 = _txt(s.select_one("h3"))
    headline = h3 or title
    raw["headline"] = headline
    if not out.get("place") or not out.get("region"):
        place, region = _place_from_alt(title)
        out.setdefault("place", place)
        if region:
            out.setdefault("region", region)
            out.setdefault("dept_nl", region)

    living = None
    for blob in (headline, title):
        living = _living_from_text(blob)
        if living is not None:
            break
    ptype_l = (out.get("type") or "").lower()
    is_land = ptype_l in LAND_TYPES or ptype_l.startswith("land")
    if is_land:
        out["living_m2"] = None
    elif living is not None:
        out["living_m2"] = living

    # first .p_text is the listing write-up; later ones are site-wide boilerplate
    desc = None
    for block in s.select(".details_holder .p_text"):
        t = _txt(block)
        if t and len(t) > 40 and "Selling price is Euro" not in t:
            desc = block.get_text("\n", strip=True)
            desc = re.sub(r"\n{3,}", "\n\n", desc)
            break
    out["description"] = desc

    photos, seen = [], set()
    for a in s.select("#galleria a[href]"):
        src = (a.get("href") or "").strip()
        if src and src not in seen and not src.startswith("#"):
            seen.add(src)
            photos.append(urljoin(BASE + "/", src))
    out["photos"] = photos
    if photos and not out.get("thumb"):
        out["thumb"] = photos[0]

    out["raw_fields"] = raw
    return out
