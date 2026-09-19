"""GBP / USD → EUR for Bulgarian Properties listings.

The live site displays asking prices in **euro** by default (`€ 10 900`,
JSON-LD `priceCurrency: EUR`). A JS currency switcher on the detail page
also prints pounds and dollars (`£ 9 375` / `$ 12 512` for the same
€10,900 card). We store `price` + `currency=EUR` as the portal states
them, and keep £ / $ in `raw_fields`.

This fallback is only used if a card has £ (or $) and no €. It is a
rounded stand-in for the portal's own dual display
(€10,900 / £9,375 ≈ 1.163), not a live ECB feed. Revisit if sterling
moves a long way.

    1 GBP → 1.163 EUR
    1 USD → 0.87 EUR
"""
from __future__ import annotations

# 1 GBP → EUR. Documented fixed rate; see module docstring.
GBP_TO_EUR = 1.163
USD_TO_EUR = 0.87
FX_SOURCE = (
    "bulgarianproperties dual-display (~1.163 EUR/GBP, 0.87 EUR/USD); "
    "not a live ECB rate"
)


def gbp_to_eur(gbp: int | float | None) -> int | None:
    if gbp is None:
        return None
    return int(round(float(gbp) * GBP_TO_EUR))


def usd_to_eur(usd: int | float | None) -> int | None:
    if usd is None:
        return None
    return int(round(float(usd) * USD_TO_EUR))
