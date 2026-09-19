"""MAD → EUR for Mubawab Morocco listings.

The live site displays asking prices in **Moroccan dirhams** (JSON-LD
`priceCurrency: MAD`, cards like `350,000 DH`). Some cards and detail
pages already show euro (`85,000 EUR` / `priceCurrency: EUR`). We store
`price` + `currency=EUR` so the shared UI can rank France and Morocco on
one scale, and keep the portal's original amount in `raw_fields`.

This is a documented fixed rate, not a live ECB / Bank Al-Maghrib feed.
Rounded to one decimal near September 2026 market levels (~10.91 MAD/EUR
on 2026-09-19). Revisit if the pair moves a long way.

    1 EUR → 10.9 MAD
    1 MAD → 1/10.9 EUR
"""
from __future__ import annotations

MAD_PER_EUR = 10.9
MAD_TO_EUR = 1 / MAD_PER_EUR
FX_SOURCE = (
    "documented fixed rate, Sep 2026 "
    "(10.9 MAD/EUR); not a live ECB / BAM feed"
)


def mad_to_eur(mad: int | float | None) -> int | None:
    if mad is None:
        return None
    return int(round(float(mad) / MAD_PER_EUR))
