"""Le Figaro Immobilier adapter: parsers, registry, searches, upserts.

No live HTTP — immobilier.lefigaro.fr Cloudflare-blocks datacenter IPs
(same class of wall as Holprop). Fixtures reconstruct the documented
Nuxt SSR ItemList + listing ld+json shape from an archive snapshot of
the Creuse house list (1 125 annonces, 2026).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.adapter import ADAPTERS, get
from core.db import connect, now, upsert_from_list, update_from_detail
from core.searches import enabled_names, load_searches
from lefigaro.parse import (
    is_blocked,
    parse_detail,
    parse_eur,
    parse_list,
    with_page,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "lefigaro"
LIST_URL = (
    "https://immobilier.lefigaro.fr/annonces/immobilier-vente-maison-creuse.html"
)
HOUSE_URL = "https://immobilier.lefigaro.fr/annonces/annonce-103112502.html"
LAND_URL = "https://immobilier.lefigaro.fr/annonces/annonce-103112508.html"


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class Registry(unittest.TestCase):
    def test_registered(self):
        import lefigaro.adapter  # noqa: F401
        self.assertIn("lefigaro", ADAPTERS)
        adapter = get("lefigaro")
        self.assertEqual(adapter.source, "lefigaro")
        self.assertEqual(adapter.base, "https://immobilier.lefigaro.fr")


class Searches(unittest.TestCase):
    def test_lf_entries(self):
        searches = load_searches()
        self.assertEqual(searches["lf-23-houses-150k"]["source"], "lefigaro")
        self.assertTrue(searches["lf-23-houses-150k"].get("enabled", True))
        self.assertIn(
            "/annonces/immobilier-vente-maison-creuse.html",
            searches["lf-23-houses-150k"]["path"],
        )
        self.assertNotIn("priceMax", searches["lf-23-houses-150k"]["path"])
        self.assertEqual(searches["lf-23-houses-150k"]["skip"]["above"], 150000)
        self.assertFalse(searches["lf-58-houses-150k"].get("enabled", True))
        self.assertIn("nievre", searches["lf-58-houses-150k"]["path"])
        self.assertFalse(searches["lf-23-petit-prix"].get("enabled", True))
        self.assertIn("option=petit_prix", searches["lf-23-petit-prix"]["path"])
        self.assertFalse(searches["lf-23-travaux"].get("enabled", True))
        self.assertIn("option=travaux", searches["lf-23-travaux"]["path"])
        self.assertFalse(searches["lf-france-houses"].get("enabled", True))
        self.assertIn("immobilier-vente-maison-france.html",
                      searches["lf-france-houses"]["path"])
        lf = enabled_names(searches, source="lefigaro")
        self.assertEqual(lf, ["lf-23-houses-150k"])
        franimo = enabled_names(searches, source="franimo")
        self.assertIn("france-150k", franimo)
        self.assertNotIn("lf-23-houses-150k", franimo)
        hp = enabled_names(searches, source="holprop")
        self.assertEqual(hp, ["hp-es-houses-100k"])


class Pagination(unittest.TestCase):
    def test_with_page_keeps_option(self):
        nxt = with_page(LIST_URL, 2)
        self.assertTrue(nxt.endswith("?page=2"))
        self.assertIn("/immobilier-vente-maison-creuse.html", nxt)
        page1 = with_page(nxt, 1)
        self.assertNotIn("page=", page1)
        self.assertTrue(page1.endswith("/immobilier-vente-maison-creuse.html"))
        facet = LIST_URL + "?option=petit_prix"
        self.assertEqual(
            with_page(facet, 3),
            LIST_URL + "?option=petit_prix&page=3",
        )


class Money(unittest.TestCase):
    def test_french_euro(self):
        self.assertEqual(parse_eur("37 500 €"), 37500)
        self.assertEqual(parse_eur("399 000 €"), 399000)
        self.assertEqual(parse_eur("€ 19 000"), 19000)
        self.assertIsNone(parse_eur("Prix sur demande"))

    def test_cloudflare_block(self):
        self.assertTrue(is_blocked("<html>Sorry, you have been blocked</html>"))
        self.assertFalse(is_blocked(_html("list.html")))
        blocked = parse_list("<html>Sorry, you have been blocked</html>", LIST_URL)
        self.assertEqual(blocked["listings"], [])
        self.assertTrue(blocked.get("blocked"))


class ListParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = parse_list(_html("list.html"), LIST_URL)

    def test_page_meta(self):
        self.assertEqual(self.page["total_pages"], 47)
        self.assertEqual(self.page["next_url"], LIST_URL + "?page=2")
        self.assertEqual(len(self.page["listings"]), 15)

    def test_unique_announce_ids(self):
        ids = [r["external_id"] for r in self.page["listings"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(i.isdigit() for i in ids))
        urls = [r["url"] for r in self.page["listings"]]
        self.assertTrue(all("/annonces/annonce-" in u for u in urls))
        self.assertFalse(any("/agence" in u for u in urls))

    def test_boussac_bargain(self):
        house = next(r for r in self.page["listings"] if r["external_id"] == "103112502")
        self.assertEqual(house["source"], "lefigaro")
        self.assertEqual(house["url"], HOUSE_URL)
        self.assertEqual(house["place"], "Boussac")
        self.assertEqual(house["region"], "Creuse")
        self.assertEqual(house["dept_fr"], "Creuse")
        self.assertEqual(house["dept_nl"], "Creuse")
        self.assertEqual(house["price"], 37500)
        self.assertEqual(house["currency"], "EUR")
        self.assertEqual(house["living_m2"], 71)
        self.assertEqual(house["land_m2"], 1600)
        self.assertEqual(house["rooms"], 4)
        self.assertEqual(house["bedrooms"], 2)
        self.assertEqual(house["type"], "Maison")
        self.assertEqual(house["raw_fields"]["country"], "France")
        self.assertTrue(house["thumb"].startswith("https://images.lefigaro.fr/"))

    def test_cheap_end_and_over_cap(self):
        cheap = next(r for r in self.page["listings"] if r["external_id"] == "103112506")
        self.assertEqual(cheap["place"], "Saint-Silvain-Bas-le-Roc")
        self.assertEqual(cheap["price"], 19000)
        dear = next(r for r in self.page["listings"] if r["external_id"] == "103112501")
        self.assertEqual(dear["price"], 399000)
        self.assertGreater(dear["price"], 150000)
        # Mixed prices on page 1 — not cheap-first. skip.above is the cap.
        prices = [r["price"] for r in self.page["listings"]]
        self.assertNotEqual(prices, sorted(prices))

    def test_propriete_type(self):
        row = next(r for r in self.page["listings"] if r["external_id"] == "103112504")
        self.assertEqual(row["type"], "Propriété")
        self.assertEqual(row["place"], "Saint-Marien")


class DetailParser(unittest.TestCase):
    def test_house(self):
        d = parse_detail(_html("detail_house.html"), HOUSE_URL)
        self.assertEqual(d["external_id"], "103112502")
        self.assertEqual(d["source"], "lefigaro")
        self.assertEqual(d["price"], 37500)
        self.assertEqual(d["currency"], "EUR")
        self.assertEqual(d["living_m2"], 71)
        self.assertEqual(d["land_m2"], 1600)
        self.assertEqual(d["rooms"], 4)
        self.assertEqual(d["bedrooms"], 2)
        self.assertEqual(d["baths"], 1)
        self.assertEqual(d["place"], "Boussac")
        self.assertEqual(d["region"], "Creuse")
        self.assertEqual(d["dept_fr"], "Creuse")
        self.assertEqual(d["energy_label"], "G")
        self.assertEqual(d["gas_label"], "C")
        self.assertEqual(d["energy_kwh"], 447)
        self.assertEqual(d["agent"], "Immo-Diffusion Boussac")
        self.assertEqual(d["reference"], "Id-EXP170081")
        self.assertEqual(d["raw_fields"]["country"], "France")
        self.assertIn("1600 m2", d["description"])
        self.assertEqual(len(d["photos"]), 2)
        self.assertAlmostEqual(d["lat"], 46.349)
        self.assertAlmostEqual(d["lon"], 2.215)

    def test_land_plot_on_house(self):
        d = parse_detail(_html("detail_land.html"), LAND_URL)
        self.assertEqual(d["external_id"], "103112508")
        self.assertEqual(d["place"], "Boussac-Bourg")
        self.assertEqual(d["price"], 39000)
        self.assertEqual(d["living_m2"], 77)
        self.assertEqual(d["land_m2"], 27495)
        self.assertEqual(d["rooms"], 3)
        self.assertIsNone(d["energy_label"])
        self.assertEqual(d["reference"], "Id-EXP175332")


class Store(unittest.TestCase):
    def test_list_upsert_then_detail(self):
        page = parse_list(_html("list.html"), LIST_URL)
        with tempfile.TemporaryDirectory() as tmp:
            con = connect(Path(tmp) / "t.db")
            ts = now()
            lids = []
            for row in page["listings"]:
                if row["price"] is None or row["price"] > 150000:
                    continue
                status, lid = upsert_from_list(
                    con, row, "lf-23-houses-150k", ts, source="lefigaro")
                self.assertEqual(status, "new")
                lids.append(lid)
            con.commit()
            n = con.execute(
                "SELECT COUNT(*) c FROM listings WHERE source=?",
                ("lefigaro",),
            ).fetchone()["c"]
            self.assertGreaterEqual(n, 10)
            house = next(r for r in page["listings"] if r["external_id"] == "103112502")
            _, hid = upsert_from_list(
                con, house, "lf-23-houses-150k", ts, source="lefigaro")
            detail = parse_detail(_html("detail_house.html"), HOUSE_URL)
            update_from_detail(con, hid, detail, ts)
            row = con.execute(
                "SELECT source, external_id, price, currency, living_m2, land_m2, "
                "bedrooms, place, lat, lon "
                "FROM listings WHERE id=?", (hid,)
            ).fetchone()
            self.assertEqual(row["source"], "lefigaro")
            self.assertEqual(row["external_id"], "103112502")
            self.assertEqual(row["price"], 37500)
            self.assertEqual(row["currency"], "EUR")
            self.assertEqual(row["living_m2"], 71)
            self.assertEqual(row["land_m2"], 1600)
            self.assertEqual(row["bedrooms"], 2)
            self.assertEqual(row["place"], "Boussac")
            self.assertAlmostEqual(row["lat"], 46.349)
            # Same portal id on franimo must not collide.
            _, fid = upsert_from_list(
                con, {"id": "103112502", "place": "Dijon", "price": 90000},
                "france-150k", ts, source="franimo")
            self.assertNotEqual(fid, hid)


class Cli(unittest.TestCase):
    def test_help(self):
        from lefigaro.scrape import main
        with self.assertRaises(SystemExit) as e:
            main(["--help"])
        self.assertEqual(e.exception.code, 0)

    def test_rejects_franimo_search(self):
        from lefigaro import scrape
        orig = scrape.load_searches
        scrape.load_searches = lambda: {
            "france-150k": {"source": "franimo", "label": "x", "path": "/"},
        }
        try:
            with self.assertRaises(SystemExit):
                scrape.main(["france-150k"])
        finally:
            scrape.load_searches = orig


if __name__ == "__main__":
    unittest.main()
