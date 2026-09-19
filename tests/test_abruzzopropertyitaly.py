"""Abruzzo Property Italy adapter: parsers, registry, searches, upserts. No live HTTP."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.adapter import ADAPTERS, get
from core.db import connect, now, upsert_from_list, update_from_detail
from core.searches import enabled_names, load_searches
from abruzzopropertyitaly.parse import parse_detail, parse_list, with_page

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "abruzzopropertyitaly"
LIST_URL = (
    "https://www.abruzzopropertyitaly.com/property-search"
    "~for=1,minprice=0,maxprice=100000,order=priceasc,do=search"
)
HOUSE_URL = (
    "https://www.abruzzopropertyitaly.com/property-search~action=detail,pid=3223"
)
LAND_URL = (
    "https://www.abruzzopropertyitaly.com/property-search~action=detail,pid=1507"
)


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class Registry(unittest.TestCase):
    def test_registered(self):
        import abruzzopropertyitaly.adapter  # noqa: F401
        self.assertIn("abruzzopropertyitaly", ADAPTERS)
        adapter = get("abruzzopropertyitaly")
        self.assertEqual(adapter.source, "abruzzopropertyitaly")
        self.assertEqual(adapter.base, "https://www.abruzzopropertyitaly.com")


class Searches(unittest.TestCase):
    def test_api_entries(self):
        searches = load_searches()
        self.assertEqual(searches["api-houses-100k"]["source"], "abruzzopropertyitaly")
        self.assertTrue(searches["api-houses-100k"].get("enabled", True))
        self.assertIn("maxprice=100000", searches["api-houses-100k"]["path"])
        self.assertIn("order=priceasc", searches["api-houses-100k"]["path"])
        self.assertFalse(searches["api-houses-50k"].get("enabled", True))
        self.assertFalse(searches["api-houses-150k"].get("enabled", True))
        self.assertIn("maxprice=50000", searches["api-houses-50k"]["path"])
        self.assertIn("maxprice=150000", searches["api-houses-150k"]["path"])
        api = enabled_names(searches, source="abruzzopropertyitaly")
        self.assertEqual(api, ["api-houses-100k"])
        franimo = enabled_names(searches, source="franimo")
        self.assertIn("france-150k", franimo)
        self.assertNotIn("api-houses-100k", franimo)
        hp = enabled_names(searches, source="holprop")
        self.assertEqual(hp, ["hp-es-houses-100k"])
        bg = enabled_names(searches, source="ok_bulgaria")
        self.assertEqual(bg, ["bg-houses-50k"])
        jp = enabled_names(searches, source="akiyaportal")
        self.assertEqual(jp, ["jp-houses-10k"])


class Pagination(unittest.TestCase):
    def test_with_page_keeps_filter_and_is_zero_based(self):
        nxt = with_page(LIST_URL, 2)
        self.assertTrue(nxt.endswith(",page=1") or ",page=1," in nxt)
        self.assertIn("minprice=0", nxt)
        self.assertIn("maxprice=100000", nxt)
        self.assertIn("order=priceasc", nxt)
        self.assertIn("do=search", nxt)
        self.assertNotIn("#", nxt)
        page1 = with_page(nxt, 1)
        self.assertNotIn("page=", page1)
        self.assertIn("maxprice=100000", page1)
        self.assertTrue(page1.endswith("do=search") or "do=search," in page1)


class ListParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = parse_list(_html("list.html"), LIST_URL)

    def test_page_meta(self):
        self.assertEqual(self.page["total_pages"], 4)
        self.assertEqual(self.page["next_url"], LIST_URL + ",page=1")
        self.assertGreaterEqual(len(self.page["listings"]), 10)
        self.assertGreaterEqual(len(self.page["listings"]), 12)

    def test_stable_ids_and_urls(self):
        ids = [r["external_id"] for r in self.page["listings"]]
        self.assertEqual(len(ids), len(set(ids)))
        house = next(r for r in self.page["listings"] if r["external_id"] == "3223")
        self.assertEqual(house["source"], "abruzzopropertyitaly")
        self.assertEqual(house["url"], HOUSE_URL)
        self.assertEqual(house["reference"], "3223")
        self.assertEqual(house["place"], "Prezza")
        self.assertEqual(house["region"], "L'Aquila")
        self.assertEqual(house["dept_nl"], "Abruzzo")
        self.assertEqual(house["price"], 12000)
        self.assertEqual(house["currency"], "EUR")
        self.assertEqual(house["beds"], 2)
        self.assertEqual(house["baths"], 1)
        self.assertIsNone(house["living_m2"])  # portal shows 0.00 SQM
        self.assertEqual(house["type"], "townhouse")
        self.assertTrue(house["thumb"].startswith("https://"))
        self.assertIn("PREZZA", house["snippet"])

    def test_card_with_areas(self):
        row = next(r for r in self.page["listings"] if r["external_id"] == "3246")
        self.assertEqual(row["price"], 25000)
        self.assertEqual(row["place"], "Roccafinadamo")
        self.assertEqual(row["living_m2"], 120)
        self.assertEqual(row["land_m2"], 2000)
        self.assertEqual(row["beds"], 3)

    def test_studio_beds_are_not_an_int(self):
        row = next(r for r in self.page["listings"] if r["external_id"] == "2961")
        self.assertIsNone(row["beds"])
        self.assertEqual(row["type"], "studio")
        self.assertEqual(row["living_m2"], 45)
        self.assertEqual(row["place"], "Castel di Ieri")


class DetailParser(unittest.TestCase):
    def test_house(self):
        d = parse_detail(_html("detail_house.html"), HOUSE_URL)
        self.assertEqual(d["external_id"], "3223")
        self.assertEqual(d["price"], 12000)
        self.assertEqual(d["currency"], "EUR")
        self.assertEqual(d["place"], "Prezza")
        self.assertEqual(d["region"], "L'Aquila")
        self.assertEqual(d["dept_nl"], "Abruzzo")
        self.assertEqual(d["type"], "Townhouse")
        self.assertEqual(d["bedrooms"], 2)
        self.assertEqual(d["baths"], 1)
        self.assertIsNone(d["living_m2"])
        self.assertIsNone(d["land_m2"])
        self.assertEqual(d["agent"], "Abruzzo Property Italy")
        self.assertGreaterEqual(len(d["photos"]), 3)
        self.assertTrue(d["photos"][0].endswith("/large/69121.jpg"))
        self.assertIn("Prezza", d["description"])
        self.assertIn("Balcony", d["features"])
        self.assertAlmostEqual(d["lat"], 42.0578995)
        self.assertAlmostEqual(d["lon"], 13.83422357)
        self.assertTrue(d["raw_fields"].get("pcm_label"))

    def test_land(self):
        d = parse_detail(_html("detail_land.html"), LAND_URL)
        self.assertEqual(d["external_id"], "1507")
        self.assertEqual(d["price"], 51000)
        self.assertEqual(d["currency"], "EUR")
        self.assertEqual(d["place"], "Ripalimosani")
        self.assertEqual(d["region"], "Campobasso")
        self.assertEqual(d["dept_nl"], "Molise")
        self.assertEqual(d["type"], "Land")
        self.assertIsNone(d["living_m2"])
        self.assertEqual(d["land_m2"], 3000)
        self.assertGreaterEqual(len(d["photos"]), 2)


class Store(unittest.TestCase):
    def test_list_upsert_then_detail(self):
        page = parse_list(_html("list.html"), LIST_URL)
        with tempfile.TemporaryDirectory() as tmp:
            con = connect(Path(tmp) / "t.db")
            ts = now()
            lids = []
            for row in page["listings"]:
                status, lid = upsert_from_list(
                    con, row, "api-houses-100k", ts, source="abruzzopropertyitaly")
                self.assertEqual(status, "new")
                lids.append(lid)
            con.commit()
            n = con.execute(
                "SELECT COUNT(*) c FROM listings WHERE source=?",
                ("abruzzopropertyitaly",),
            ).fetchone()["c"]
            self.assertGreaterEqual(n, 10)
            house = next(r for r in page["listings"] if r["external_id"] == "3223")
            _, hid = upsert_from_list(
                con, house, "api-houses-100k", ts, source="abruzzopropertyitaly")
            detail = parse_detail(_html("detail_house.html"), HOUSE_URL)
            update_from_detail(con, hid, detail, ts)
            row = con.execute(
                "SELECT source, external_id, price, currency, bedrooms, baths, place, lat, lon "
                "FROM listings WHERE id=?", (hid,)
            ).fetchone()
            self.assertEqual(row["source"], "abruzzopropertyitaly")
            self.assertEqual(row["external_id"], "3223")
            self.assertEqual(row["price"], 12000)
            self.assertEqual(row["currency"], "EUR")
            self.assertEqual(row["bedrooms"], 2)
            self.assertEqual(row["baths"], 1)
            self.assertEqual(row["place"], "Prezza")
            self.assertAlmostEqual(row["lat"], 42.0578995)
            self.assertAlmostEqual(row["lon"], 13.83422357)
            _, fid = upsert_from_list(
                con, {"id": "3223", "place": "Dijon", "price": 90000},
                "france-150k", ts, source="franimo")
            self.assertNotEqual(fid, hid)


class Cli(unittest.TestCase):
    def test_help(self):
        from abruzzopropertyitaly.scrape import main
        with self.assertRaises(SystemExit) as e:
            main(["--help"])
        self.assertEqual(e.exception.code, 0)

    def test_rejects_franimo_search(self):
        from abruzzopropertyitaly import scrape
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
