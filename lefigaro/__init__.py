"""Le Figaro Immobilier adapter (immobilier.lefigaro.fr — France houses).

Searches whose `"source"` is `"lefigaro"` are crawled by
`python3 -m lefigaro.scrape`. Franimo's CLI will refuse them.

Listings may overlap franimo.nl — they stay `source=lefigaro`. Do not
cross-source merge.
"""
