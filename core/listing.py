"""Normalized listing fields every source adapter should fill.

`source` + `external_id` is the natural key. `id` is an opaque internal
integer used by the UI, photo index, and foreign keys — it is *not* a
portal id, and two portals may reuse the same external_id.

Prices are stored as the portal states them. `currency` defaults to EUR;
conversion is a later concern, not this module's.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

DEFAULT_SOURCE = "franimo"
DEFAULT_CURRENCY = "EUR"


@dataclass
class Listing:
    source: str
    external_id: str
    url: str | None = None
    type: str | None = None
    place: str | None = None
    region: str | None = None
    dept_nl: str | None = None
    dept_fr: str | None = None
    lat: float | None = None
    lon: float | None = None
    price: int | None = None
    currency: str = DEFAULT_CURRENCY
    old_price: int | None = None
    rooms: int | None = None
    bedrooms: int | None = None
    baths: int | None = None
    living_m2: int | None = None
    land_m2: int | None = None
    year_built: int | None = None
    energy_label: str | None = None
    gas_label: str | None = None
    energy_kwh: int | None = None
    gas_co2: int | None = None
    reference: str | None = None
    agent: str | None = None
    agent_name: str | None = None
    agent_address: str | None = None
    snippet: str | None = None
    description: str | None = None
    features: str | None = None
    thumb: str | None = None
    photos: list[str] | None = None
    raw_fields: dict[str, Any] | None = None
    promoted: bool = False
    id: int | None = None  # assigned by the store

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def as_row(row: Listing | dict[str, Any]) -> dict[str, Any]:
    """Accept either a Listing or the dicts franimo's parser already returns."""
    if isinstance(row, Listing):
        return row.to_dict()
    return dict(row)


def identity(row: Listing | dict[str, Any], default_source: str = DEFAULT_SOURCE
             ) -> tuple[str, str]:
    """(source, external_id) for upsert / dedupe."""
    d = as_row(row)
    source = d.get("source") or default_source
    ext = d.get("external_id")
    if ext is None or ext == "":
        if d.get("id") is None:
            raise ValueError("listing needs external_id or id")
        ext = d["id"]
    return source, str(ext)
