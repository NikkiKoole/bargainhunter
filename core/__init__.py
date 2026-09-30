"""Shared bargainhunter infrastructure.

HTTP cache, SQLite storage, the listing model, and the export/serve path.
Adapters (franimo, ok_bulgaria, akiyaportal, holprop, abruzzopropertyitaly, abruzzoruralproperty, centrarium, mubawab, homege, bulgarianproperties, domaza, lefigaro, greenacres, …) plug in via `core.adapter`.
`python3 -m franimo.scrape` / `export` / `serve` keep working.
`python3 -m core.probe` compares published counts to one live list page.
"""
