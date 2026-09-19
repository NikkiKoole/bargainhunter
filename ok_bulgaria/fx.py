"""GBP ↔ EUR for listings that only show one currency.

OK Bulgaria's own copy says the selling price is euro; the pound figure is
"for reference only and can vary daily." When a card already has both, we
keep the portal's euro number and do not convert.

This fallback is only used if a card has £ and no €. It is a rounded
stand-in for the portal's own dual display (~€1,500 / £1,286 ≈ 1.166),
not a live ECB feed. Revisit if sterling moves a long way.
"""
from __future__ import annotations

# 1 GBP → EUR. Documented fixed rate; see module docstring.
GBP_TO_EUR = 1.166
FX_SOURCE = "ok_bulgaria dual-display (~1.166); not a live ECB rate"


def gbp_to_eur(gbp: int | None) -> int | None:
    if gbp is None:
        return None
    return int(round(gbp * GBP_TO_EUR))
