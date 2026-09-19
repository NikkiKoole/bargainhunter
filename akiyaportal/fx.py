"""USD / JPY → EUR for Akiya Portal listings.

The live site displays asking prices in **US dollars** (JSON-LD
`priceCurrency: USD`, cards like `$3,208`). Some titles and similar-listing
cards also show yen (`500,000 yen`). We store `price` + `currency=EUR` so
the shared UI can rank France, Bulgaria and Japan on one scale, and keep
the portal's original amount in `raw_fields`.

These are documented fixed rates, not a live ECB feed. Rounded to easy
numbers near September 2026 market levels (~0.85 EUR/USD, ~170 JPY/EUR).
Revisit if either pair moves a long way.

    1 USD → 0.85 EUR
    1 JPY → 1/170 EUR
"""
from __future__ import annotations

USD_TO_EUR = 0.85
JPY_TO_EUR = 1 / 170
FX_SOURCE = (
    "documented fixed rates, Sep 2026 "
    "(0.85 EUR/USD, 170 JPY/EUR); not a live ECB feed"
)


def usd_to_eur(usd: int | float | None) -> int | None:
    if usd is None:
        return None
    return int(round(float(usd) * USD_TO_EUR))


def jpy_to_eur(jpy: int | float | None) -> int | None:
    if jpy is None:
        return None
    return int(round(float(jpy) * JPY_TO_EUR))
