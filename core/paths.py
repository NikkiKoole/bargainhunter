"""Repo-root paths shared by every adapter.

The database stays `db/franimo.db` (not `data/`). `data/` is the published
JSON. The HTML cache stays at `cache/` — never delete it; the hash is of the
full URL, so existing files remain valid after this refactor.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
DB_PATH = ROOT / "db" / "franimo.db"
SEARCHES = ROOT / "searches.json"
WEB = ROOT / "franimo" / "web"
