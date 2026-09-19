"""USD / GEL → EUR for home.ge Georgia listings.

The live site displays asking prices in **US dollars** by default
(`curConv_code=dollar`, cards like `70,000.00 $`, JSON-LD
`priceCurrency: USD`). Some ads are entered in GEL (₾) or already
show euro. We store `price` + `currency=EUR` so the shared UI can
rank France and Georgia on one scale, and keep the portal's original
amount in `raw_fields`.

These are documented fixed rates, not a live ECB / NBG feed. Rounded
to easy numbers near September 2026 market levels (~0.85 EUR/USD,
~3.03 GEL/EUR on the National Bank of Georgia print). Revisit if
either pair moves a long way.

    1 USD → 0.85 EUR
    1 GEL → 1/3.03 EUR
"""
from __future__ import annotations

USD_TO_EUR = 0.85
GEL_PER_EUR = 3.03
GEL_TO_EUR = 1 / GEL_PER_EUR
FX_SOURCE = (
    "documented fixed rates, Sep 2026 "
    "(0.85 EUR/USD, 3.03 GEL/EUR); not a live ECB / NBG feed"
)


def usd_to_eur(usd: int | float | None) -> int | None:
    if usd is None:
        return None
    return int(round(float(usd) * USD_TO_EUR))


def gel_to_eur(gel: int | float | None) -> int | None:
    if gel is None:
        return None
    return int(round(float(gel) / GEL_PER_EUR))
