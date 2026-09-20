"""Which country a listing is in.

The locator map in the detail panel has to draw the right country, so every
listing needs one. Three sources of truth, in order:

1. ``raw_fields.country`` — what the portal itself said (8 adapters provide it,
   and it is the only reliable answer for portals that span countries, such as
   Holprop and Domaza).
2. an explicit ``country`` on the search seed in searches.json.
3. the adapter's home country, for single-country portals.
"""
from __future__ import annotations

ISO_BY_NAME = {
    "france": "FR", "italy": "IT", "italia": "IT", "spain": "ES", "españa": "ES",
    "portugal": "PT", "greece": "GR", "bulgaria": "BG", "montenegro": "ME",
    "serbia": "RS", "albania": "AL", "morocco": "MA", "maroc": "MA",
    "georgia": "GE", "japan": "JP",
}

# Portals that only ever list one country.
SOURCE_COUNTRY = {
    "franimo": "FR",
    "greenacres": "FR",
    "lefigaro": "FR",
    "akiyaportal": "JP",
    "ok_bulgaria": "BG",
    "bulgarianproperties": "BG",
    "abruzzopropertyitaly": "IT",
    "abruzzoruralproperty": "IT",
    "centrarium": "ME",
    "mubawab": "MA",
    "homege": "GE",
}

NAME_BY_ISO = {
    "FR": "Frankrijk", "IT": "Italië", "ES": "Spanje", "PT": "Portugal",
    "GR": "Griekenland", "BG": "Bulgarije", "ME": "Montenegro", "RS": "Servië",
    "AL": "Albanië", "MA": "Marokko", "GE": "Georgië", "JP": "Japan",
}


def iso(name: str | None) -> str | None:
    if not name:
        return None
    key = str(name).strip().lower()
    if len(key) == 2 and key.upper() in NAME_BY_ISO:
        return key.upper()
    return ISO_BY_NAME.get(key)


def country_for(row: dict, source: str, seed_country: str | None = None) -> str | None:
    """Best available country for a listing, as an ISO-2 code."""
    raw = row.get("raw_fields")
    if isinstance(raw, dict):
        found = iso(raw.get("country"))
        if found:
            return found
    return iso(row.get("country")) or iso(seed_country) or SOURCE_COUNTRY.get(source)
