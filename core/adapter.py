"""Source adapter contract.

Adapters (franimo, ok_bulgaria, akiyaportal, holprop, abruzzopropertyitaly, abruzzoruralproperty, centrarium, mubawab, homege, bulgarianproperties, domaza, lefigaro, greenacres, …) implement this and register themselves.
A search in searches.json points at an adapter via `"source"`. Existing
entries that omit it are treated as `"franimo"`.
"""
from __future__ import annotations

from typing import Any, Protocol

ADAPTERS: dict[str, "SourceAdapter"] = {}


class SourceAdapter(Protocol):
    source: str
    base: str

    def parse_list(self, html: str, page_url: str) -> dict[str, Any]:
        """Return {listings, total_pages, next_url}.

        Optional keys scrapers ignore: `total` (exact portal result count
        when the page states one) and `blocked` (a challenge page).
        """
        ...

    def parse_detail(self, html: str, url: str) -> dict[str, Any]:
        """Return normalized listing fields (see core.listing.Listing)."""
        ...


def register(adapter: SourceAdapter) -> SourceAdapter:
    ADAPTERS[adapter.source] = adapter
    return adapter


def get(name: str) -> SourceAdapter:
    try:
        return ADAPTERS[name]
    except KeyError as e:
        known = ", ".join(sorted(ADAPTERS)) or "(none registered)"
        raise KeyError(f"unknown source {name!r}; known: {known}") from e
