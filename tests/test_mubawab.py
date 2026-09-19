"""Mubawab adapter: parsers, registry, searches, upserts. No live HTTP."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.adapter import ADAPTERS, get
from core.db import connect, now, upsert_from_list, update_from_detail
from core.searches import enabled_names, load_searches
from mubawab.fx import MAD_PER_EUR, mad_to_eur
from mubawab.parse import (
    parse_detail,
    parse_list,
    parse_money,
    priced_row,
    with_page,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "mubawab"
LIST_URL = "https://www.mubawab.ma/en/sc/houses-for-sale:pr:0-1100000"
HOUSE_URL = "https://www.mubawab.ma/en/a/8418668/house-for-sale-sefrou"
EUR_URL = (
    "https://www.mubawab.ma/en/a/8158008/"
    "fabulous-villa-for-sale-area-of-96-m%C2%B2-balcony"
)


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class Registry(unittest.TestCase):
    def test_registered(self):
        import mubawab.adapter  # noqa: F401
        self.assertIn("mubawab", ADAPTERS)
        adapter = get("mubawab")
        self.assertEqual(adapter.source, "mubawab")
        self.assertEqual(adapter.base, "https://www.mubawab.ma")


class Searches(unittest.TestCase):
    def test_mw_entries(self):
        searches = load_searches()
        self.assertEqual(searches["mw-ma-houses-100k"]["source"], "mubawab")
        self.assertTrue(searches["mw-ma-houses-100k"].get("enabled", True))
        self.assertIn(":pr:0-1100000", searches["mw-ma-houses-100k"]["path"])
        self.assertEqual(searches["mw-ma-houses-100k"]["skip"]["above"], 100000)
        self.assertFalse(searches["mw-ma-houses-150k"].get("enabled", True))
        self.assertIn(":pr:0-1600000", searches["mw-ma-houses-150k"]["path"])
        self.assertFalse(searches["mw-ma-houses"].get("enabled", True))
        self.assertEqual(searches["mw-ma-houses"]["path"], "/en/sc/houses-for-sale")
        mw = enabled_names(searches, source="mubawab")
        self.assertEqual(mw, ["mw-ma-houses-100k"])
        franimo = enabled_names(searches, source="franimo")
        self.assertIn("france-150k", franimo)
        self.assertNotIn("mw-ma-houses-100k", franimo)
        ct = enabled_names(searches, source="centrarium")
        self.assertEqual(ct, ["ct-me-houses-100k"])


class Pagination(unittest.TestCase):
    def test_with_page_keeps_mad_filter(self):
        nxt = with_page(LIST_URL, 2)
        self.assertTrue(nxt.endswith(":pr:0-1100000:p:2"))
        self.assertIn("/en/sc/houses-for-sale", nxt)
        page1 = with_page(nxt, 1)
        self.assertNotIn(":p:", page1)
        self.assertTrue(page1.endswith(":pr:0-1100000"))


class Money(unittest.TestCase):
    def test_dh_and_eur(self):
        self.assertEqual(parse_money("350,000 DH"), (350000, "MAD"))
        self.assertEqual(parse_money("85,000 EUR"), (85000, "EUR"))
        self.assertEqual(parse_money("Price on request"), (None, None))

    def test_mad_to_eur_documented_rate(self):
        self.assertEqual(MAD_PER_EUR, 10.9)
        self.assertEqual(mad_to_eur(350000), 32110)
        self.assertEqual(mad_to_eur(1_100_000), 100917)
        row = priced_row(350000, "MAD")
        self.assertEqual(row["price"], 32110)
        self.assertEqual(row["currency"], "EUR")
        self.assertEqual(row["raw_fields"]["price_mad"], 350000)
        self.assertEqual(row["raw_fields"]["price_source"], "mad_converted")
        eur = priced_row(85000, "EUR")
        self.assertEqual(eur["price"], 85000)
        self.assertEqual(eur["raw_fields"]["price_source"], "portal_eur")


class ListParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = parse_list(_html("list.html"), LIST_URL)

    def test_page_meta(self):
        self.assertEqual(self.page["total_pages"], 12)
        self.assertEqual(self.page["next_url"], LIST_URL + ":p:2")
        self.assertGreaterEqual(len(self.page["listings"]), 6)

    def test_skips_project_boost_and_empty(self):
        ids = [r["external_id"] for r in self.page["listings"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertNotIn(None, ids)
        urls = [r["url"] for r in self.page["listings"]]
        self.assertTrue(all("/en/a/" in u for u in urls))
        self.assertFalse(any("/en/p/" in u for u in urls))

    def test_sefrou_mad_house(self):
        house = next(r for r in self.page["listings"] if r["external_id"] == "8418668")
        self.assertEqual(house["source"], "mubawab")
        self.assertEqual(house["url"], HOUSE_URL)
        self.assertEqual(house["place"], "Sefrou")
        self.assertEqual(house["region"], "Sefrou")
        self.assertEqual(house["price"], 32110)
        self.assertEqual(house["currency"], "EUR")
        self.assertEqual(house["living_m2"], 112)
        self.assertEqual(house["rooms"], 6)
        self.assertEqual(house["beds"], 3)
        self.assertEqual(house["baths"], 1)
        self.assertEqual(house["type"], "House")
        self.assertEqual(house["raw_fields"]["price_mad"], 350000)
        self.assertEqual(house["raw_fields"]["price_source"], "mad_converted")
        self.assertEqual(house["raw_fields"]["country"], "Morocco")
        self.assertTrue(house["thumb"].startswith("https://www.mubawab-media.com/"))

    def test_taza_portal_eur(self):
        row = next(r for r in self.page["listings"] if r["external_id"] == "8158008")
        self.assertEqual(row["price"], 85000)
        self.assertEqual(row["currency"], "EUR")
        self.assertEqual(row["raw_fields"]["price_source"], "portal_eur")
        self.assertEqual(row["place"], "Taza")
        self.assertEqual(row["living_m2"], 96)
        self.assertEqual(row["beds"], 4)
        self.assertIn("/en/a/8158008/", row["url"])

    def test_premium_flag_and_cap_card(self):
        prem = next(r for r in self.page["listings"] if r["external_id"] == "8328778")
        self.assertTrue(prem["promoted"])
        self.assertEqual(prem["place"], "Tiznit")
        cap = next(r for r in self.page["listings"] if r["external_id"] == "8417779")
        self.assertEqual(cap["raw_fields"]["price_mad"], 1100000)
        self.assertEqual(cap["price"], 100917)


class DetailParser(unittest.TestCase):
    def test_house_mad(self):
        d = parse_detail(_html("detail_house.html"), HOUSE_URL)
        self.assertEqual(d["external_id"], "8418668")
        self.assertEqual(d["price"], 32110)
        self.assertEqual(d["currency"], "EUR")
        self.assertEqual(d["living_m2"], 112)
        self.assertIsNone(d["land_m2"])
        self.assertEqual(d["bedrooms"], 3)
        self.assertEqual(d["rooms"], 6)
        self.assertEqual(d["baths"], 1)
        self.assertEqual(d["place"], "Sefrou")
        self.assertEqual(d["region"], "Sefrou")
        self.assertEqual(d["type"], "House")
        self.assertAlmostEqual(d["lat"], 33.828073507191476)
        self.assertAlmostEqual(d["lon"], -4.833040237426758)
        self.assertGreaterEqual(len(d["photos"]), 3)
        self.assertIn("Unregistered", d["description"])
        self.assertEqual(d["raw_fields"]["price_mad"], 350000)
        self.assertEqual(d["raw_fields"]["country"], "Morocco")
        self.assertIn("ownership_note", d["raw_fields"])
        self.assertNotEqual(d["living_m2"], d["land_m2"])

    def test_eur_villa_with_plot(self):
        d = parse_detail(_html("detail_eur.html"), EUR_URL)
        self.assertEqual(d["external_id"], "8158008")
        self.assertEqual(d["price"], 85000)
        self.assertEqual(d["currency"], "EUR")
        self.assertEqual(d["raw_fields"]["price_source"], "portal_eur")
        self.assertEqual(d["living_m2"], 96)
        self.assertEqual(d["land_m2"], 96)
        self.assertEqual(d["bedrooms"], 4)
        self.assertEqual(d["rooms"], 6)
        self.assertEqual(d["baths"], 2)
        self.assertEqual(d["place"], "Taza")
        self.assertEqual(d["type"], "House")
        self.assertEqual(d["old_price"], 90000)
        self.assertAlmostEqual(d["lat"], 34.22870247442505)
        self.assertAlmostEqual(d["lon"], -4.014765603898976)
        self.assertIn("Terrace", d["features"] or "")

    def test_rental_copy_is_flagged_not_dropped(self):
        html = """
        <html><head>
        <script type="application/ld+json">{"@type":"RealEstateListing","url":
        "https://www.mubawab.ma/en/a/8395905/villa-for-rent","name":"Villa for rent",
        "description":"LUXURY NEW VILLA FOR RENT AT L'ORANGE",
        "offers":{"price":100000,"priceCurrency":"MAD"},
        "itemOffered":{"address":{"addressLocality":"Rabat"},
        "numberOfRooms":10,"numberOfBedrooms":6,"numberOfBathroomsTotal":7,
        "floorSize":{"value":1000,"unitCode":"MTR"}}}</script>
        </head><body>
        <h1 class="searchTitle">Villa for rent</h1>
        <div class="mainInfoProp"><h3 class="orangeTit">100,000 DH</h3>
        <h3 class="greyTit">Agdal, Rabat</h3></div>
        <div class="blockProp"><h1 class="searchTitle">Villa for rent</h1>
        <p>LUXURY NEW VILLA FOR RENT AT L'ORANGE</p></div>
        </body></html>
        """
        d = parse_detail(
            html,
            "https://www.mubawab.ma/en/a/8395905/villa-for-rent",
        )
        self.assertEqual(d["external_id"], "8395905")
        self.assertEqual(d["price"], 9174)
        self.assertTrue(d["raw_fields"].get("maybe_rental"))


class Store(unittest.TestCase):
    def test_list_upsert_then_detail(self):
        page = parse_list(_html("list.html"), LIST_URL)
        with tempfile.TemporaryDirectory() as tmp:
            con = connect(Path(tmp) / "t.db")
            ts = now()
            lids = []
            for row in page["listings"]:
                if row["price"] is None or row["price"] > 100000:
                    continue
                status, lid = upsert_from_list(
                    con, row, "mw-ma-houses-100k", ts, source="mubawab")
                self.assertEqual(status, "new")
                lids.append(lid)
            con.commit()
            n = con.execute(
                "SELECT COUNT(*) c FROM listings WHERE source=?",
                ("mubawab",),
            ).fetchone()["c"]
            self.assertGreaterEqual(n, 5)
            house = next(r for r in page["listings"] if r["external_id"] == "8418668")
            _, hid = upsert_from_list(
                con, house, "mw-ma-houses-100k", ts, source="mubawab")
            detail = parse_detail(_html("detail_house.html"), HOUSE_URL)
            update_from_detail(con, hid, detail, ts)
            row = con.execute(
                "SELECT source, external_id, price, currency, living_m2, land_m2, "
                "bedrooms, place, lat, lon "
                "FROM listings WHERE id=?", (hid,)
            ).fetchone()
            self.assertEqual(row["source"], "mubawab")
            self.assertEqual(row["external_id"], "8418668")
            self.assertEqual(row["price"], 32110)
            self.assertEqual(row["currency"], "EUR")
            self.assertEqual(row["living_m2"], 112)
            self.assertIsNone(row["land_m2"])
            self.assertEqual(row["bedrooms"], 3)
            self.assertEqual(row["place"], "Sefrou")
            self.assertAlmostEqual(row["lat"], 33.828073507191476)
            _, fid = upsert_from_list(
                con, {"id": "8418668", "place": "Dijon", "price": 90000},
                "france-150k", ts, source="franimo")
            self.assertNotEqual(fid, hid)


class Cli(unittest.TestCase):
    def test_help(self):
        from mubawab.scrape import main
        with self.assertRaises(SystemExit) as e:
            main(["--help"])
        self.assertEqual(e.exception.code, 0)

    def test_rejects_franimo_search(self):
        from mubawab import scrape
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
