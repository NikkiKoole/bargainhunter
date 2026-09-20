"""USD → EUR when a Green-Acres card has no euro figure.

green-acres.fr prints **EUR** for a European visitor and **USD** from a
US/datacenter IP (VisitorCountry cookie). Detail pages still carry
`Prijs in euros : 93.500 €` next to the dollar paint.

We prefer that portal euro number when the HTML has one. If a card is
dollars-only we convert with a documented fixed rate matching the dual
display checked live on 2026-09-20 (`$107,417` → `93.500 €` ≈ 0.8704;
we use 0.87 like Domaza). Not a live ECB feed. Revisit if the dollar
moves a long way.

    1 USD → 0.87 EUR
"""
from __future__ import annotations

USD_TO_EUR = 0.87
FX_SOURCE = (
    "documented fixed rate, Sep 2026 "
    "(0.87 EUR/USD, matching Green-Acres dual display "
    "$107,417 → €93,500); not a live ECB feed"
)


def usd_to_eur(usd: int | float | None) -> int | None:
    if usd is None:
        return None
    return int(round(float(usd) * USD_TO_EUR))
