"""Green-Acres adapter (green-acres.fr — France rural/lifestyle houses).

Searches whose `"source"` is `"greenacres"` are crawled by
`python3 -m greenacres.scrape`. Franimo's CLI will refuse them.

Listings may overlap franimo.nl and immobilier.lefigaro.fr — they stay
`source=greenacres`. Do not cross-source merge.
"""
