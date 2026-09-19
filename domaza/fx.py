"""USD → EUR fallback when a Domaza card has no euro figure.

domaza.com defaults to **USD**. A session GET of
`/ajaxfeeds/currency/currency/EUR` switches the printed prices to euro
(the portal's own conversion — not a public ECB feed). We prefer that
portal euro number when the HTML has one.

If a cached page (or a fetch that never flipped the session) only shows
`$`, we convert with a documented fixed rate matching the dual display
checked live on 2026-09-19 (`$3,446` → `3 000 €` ≈ 0.87). Revisit if
the dollar moves a long way.

    1 USD → 0.87 EUR
"""
from __future__ import annotations

USD_TO_EUR = 0.87
FX_SOURCE = (
    "documented fixed rate, Sep 2026 "
    "(0.87 EUR/USD, matching Domaza dual display $3,446 → €3,000); "
    "not a live ECB feed"
)


def usd_to_eur(usd: int | float | None) -> int | None:
    if usd is None:
        return None
    return int(round(float(usd) * USD_TO_EUR))
