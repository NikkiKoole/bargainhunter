"""Load searches.json, defaulting omitted `source` to franimo."""
from __future__ import annotations

import json
from pathlib import Path

from .listing import DEFAULT_SOURCE
from .paths import SEARCHES

__all__ = ["DEFAULT_SOURCE", "SEARCHES", "load_searches", "enabled_names"]


def load_searches(path: Path | str | None = None) -> dict:
    raw = Path(path or SEARCHES).read_text(encoding="utf-8")
    data = json.loads(raw)
    for spec in data.values():
        spec.setdefault("source", DEFAULT_SOURCE)
    return data


def enabled_names(searches: dict, source: str | None = None) -> list[str]:
    """Enabled searches, optionally restricted to one adapter."""
    names = [n for n, s in searches.items() if s.get("enabled", True)]
    if source is None:
        return names
    return [n for n in names if searches[n].get("source", DEFAULT_SOURCE) == source]
