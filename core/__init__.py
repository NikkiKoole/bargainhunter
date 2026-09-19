"""Shared bargainhunter infrastructure.

HTTP cache, SQLite storage, the listing model, and the export/serve path.
Adapters (franimo, ok_bulgaria, akiyaportal, holprop, abruzzopropertyitaly, abruzzoruralproperty, centrarium, …) plug in via `core.adapter`.
`python3 -m franimo.scrape` / `export` / `serve` keep working.
"""
