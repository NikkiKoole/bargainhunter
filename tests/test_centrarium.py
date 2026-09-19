"""Centrarium adapter: parsers, registry, searches, upserts. No live HTTP."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.adapter import ADAPTERS, get
from core.db import connect, now, upsert_from_list, update_from_detail
from core.searches import enabled_names, load_searches
from centrarium.http import DEFAULT_GAP
from centrarium.parse import parse_detail, parse_eur, parse_list, with_page

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "centrarium"
LIST_URL = "https://centrarium.com/en/montenegro/sale/houses/lowprice-montenegro/"
HOUSE_URL = (
    "https://centrarium.com/en/zabljak/"
    "dom-v-lesu-v-zhablake-ploshhad-21m2-1-etazhnyj-64071.html"
)
BEDS_URL = (
    "https://centrarium.com/en/niksic/"
    "v-nikshiche-ploshhad-122m2-1-etazhnyj-64028.html"
)


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class Registry(unittest.TestCase):
    def test_registered(self):
        import centrarium.adapter  # noqa: F401
        self.assertIn("centrarium", ADAPTERS)
        adapter = get("centrarium")
        self.assertEqual(adapter.source, "centrarium")
        self.assertEqual(adapter.base, "https://centrarium.com")

    def test_gap_respects_robots_crawl_delay(self):
        self.assertGreaterEqual(DEFAULT_GAP, 5.0)


class Searches(unittest.TestCase):
    def test_ct_entries(self):
        searches = load_searches()
        self.assertEqual(searches["ct-me-houses-100k"]["source"], "centrarium")
        self.assertTrue(searches["ct-me-houses-100k"].get("enabled", True))
        self.assertIn("/lowprice-montenegro/", searches["ct-me-houses-100k"]["path"])
        self.assertEqual(searches["ct-me-houses-100k"]["skip"]["above"], 100000)
        self.assertFalse(searches["ct-me-houses"].get("enabled", True))
        self.assertIn("/en/montenegro/sale/houses/", searches["ct-me-houses"]["path"])
        ct = enabled_names(searches, source="centrarium")
        self.assertEqual(ct, ["ct-me-houses-100k"])
        franimo = enabled_names(searches, source="franimo")
        self.assertIn("france-150k", franimo)
        self.assertNotIn("ct-me-houses-100k", franimo)
        arp = enabled_names(searches, source="abruzzoruralproperty")
        self.assertEqual(arp, ["arp-houses-100k"])
        hp = enabled_names(searches, source="holprop")
        self.assertEqual(hp, ["hp-es-houses-100k"])
        bg = enabled_names(searches, source="ok_bulgaria")
        self.assertEqual(bg, ["bg-houses-50k"])
        jp = enabled_names(searches, source="akiyaportal")
        self.assertEqual(jp, ["jp-houses-10k"])


class Pagination(unittest.TestCase):
    def test_with_page_keeps_filter(self):
        nxt = with_page(LIST_URL, 2)
        self.assertTrue(nxt.endswith("?page=2"))
        self.assertIn("/lowprice-montenegro/", nxt)
        page1 = with_page(nxt, 1)
        self.assertNotIn("page=", page1)
        self.assertTrue(page1.endswith("/lowprice-montenegro/"))


class Money(unittest.TestCase):
    def test_spaced_thousands(self):
        self.assertEqual(parse_eur("35 000 €"), 35000)
        self.assertEqual(parse_eur("49 999 €"), 49999)
        self.assertIsNone(parse_eur("172 294.97$"))


class ListParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = parse_list(_html("list.html"), LIST_URL)

    def test_page_meta(self):
        self.assertEqual(self.page["total_pages"], 23)
        self.assertEqual(self.page["next_url"], LIST_URL + "?page=2")
        self.assertGreaterEqual(len(self.page["listings"]), 10)
        self.assertEqual(len(self.page["listings"]), 15)

    def test_stable_ids_and_urls(self):
        ids = [r["external_id"] for r in self.page["listings"]]
        self.assertEqual(len(ids), len(set(ids)))
        house = next(r for r in self.page["listings"] if r["external_id"] == "64071")
        self.assertEqual(house["source"], "centrarium")
        self.assertEqual(house["url"], HOUSE_URL)
        self.assertEqual(house["place"], "Zabljak")
        self.assertEqual(house["region"], "Zabljak")
        self.assertEqual(house["price"], 35000)
        self.assertEqual(house["currency"], "EUR")
        self.assertEqual(house["living_m2"], 21)
        self.assertEqual(house["beds"], 1)
        self.assertEqual(house["type"], "House")
        self.assertEqual(house["raw_fields"]["country"], "Montenegro")
        self.assertEqual(house["raw_fields"]["price_source"], "portal_eur")
        self.assertTrue(house["thumb"].startswith("https://cdn.centrarium.com/"))

    def test_bar_susanj_and_bedrooms(self):
        row = next(r for r in self.page["listings"] if r["external_id"] == "64043")
        self.assertEqual(row["place"], "Susanj")
        self.assertEqual(row["region"], "Bar")
        self.assertEqual(row["price"], 45000)
        self.assertEqual(row["beds"], 1)
        self.assertEqual(row["living_m2"], 26)

    def test_niksic_rooms_vs_beds(self):
        row = next(r for r in self.page["listings"] if r["external_id"] == "64028")
        self.assertEqual(row["place"], "Niksic")
        self.assertEqual(row["price"], 49999)
        self.assertEqual(row["beds"], 2)
        self.assertEqual(row["rooms"], 5)
        self.assertEqual(row["living_m2"], 122)
        self.assertEqual(row["url"], BEDS_URL)

    def test_villa_type_from_title(self):
        row = next(r for r in self.page["listings"] if r["external_id"] == "64011")
        self.assertEqual(row["type"], "Villa")
        self.assertEqual(row["price"], 69000)


class DetailParser(unittest.TestCase):
    def test_house(self):
        d = parse_detail(_html("detail_house.html"), HOUSE_URL)
        self.assertEqual(d["external_id"], "64071")
        self.assertEqual(d["price"], 35000)
        self.assertEqual(d["currency"], "EUR")
        self.assertEqual(d["living_m2"], 21)
        self.assertEqual(d["land_m2"], 300)
        self.assertEqual(d["bedrooms"], 1)
        self.assertEqual(d["baths"], 1)
        self.assertEqual(d["place"], "Zabljak")
        self.assertEqual(d["region"], "Northern Region")
        self.assertEqual(d["type"], "House")
        self.assertEqual(d["agent"], "Centrarium")
        self.assertEqual(d["reference"], "8013")
        self.assertAlmostEqual(d["lat"], 43.155514)
        self.assertAlmostEqual(d["lon"], 19.122602)
        self.assertGreaterEqual(len(d["photos"]), 3)
        self.assertIn("pine forest", d["description"])
        self.assertEqual(d["raw_fields"]["price_eur"], 35000)
        self.assertEqual(d["raw_fields"]["country"], "Montenegro")
        # Living area must not be copied from the plot.
        self.assertNotEqual(d["living_m2"], d["land_m2"])

    def test_house_with_bedrooms_and_plot(self):
        d = parse_detail(_html("detail_beds.html"), BEDS_URL)
        self.assertEqual(d["external_id"], "64028")
        self.assertEqual(d["price"], 49999)
        self.assertEqual(d["currency"], "EUR")
        self.assertEqual(d["living_m2"], 122)
        self.assertEqual(d["land_m2"], 540)
        self.assertEqual(d["bedrooms"], 2)
        self.assertEqual(d["rooms"], 5)
        self.assertEqual(d["baths"], 1)
        self.assertEqual(d["place"], "Niksic")
        self.assertEqual(d["region"], "Central region")
        self.assertEqual(d["reference"], "7076")
        self.assertAlmostEqual(d["lat"], 42.780472)
        self.assertAlmostEqual(d["lon"], 18.956165)
        self.assertIn("Niksic", d["description"])


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
                    con, row, "ct-me-houses-100k", ts, source="centrarium")
                self.assertEqual(status, "new")
                lids.append(lid)
            con.commit()
            n = con.execute(
                "SELECT COUNT(*) c FROM listings WHERE source=?",
                ("centrarium",),
            ).fetchone()["c"]
            self.assertGreaterEqual(n, 10)
            house = next(r for r in page["listings"] if r["external_id"] == "64071")
            _, hid = upsert_from_list(
                con, house, "ct-me-houses-100k", ts, source="centrarium")
            detail = parse_detail(_html("detail_house.html"), HOUSE_URL)
            update_from_detail(con, hid, detail, ts)
            row = con.execute(
                "SELECT source, external_id, price, currency, living_m2, land_m2, "
                "bedrooms, place, lat, lon "
                "FROM listings WHERE id=?", (hid,)
            ).fetchone()
            self.assertEqual(row["source"], "centrarium")
            self.assertEqual(row["external_id"], "64071")
            self.assertEqual(row["price"], 35000)
            self.assertEqual(row["currency"], "EUR")
            self.assertEqual(row["living_m2"], 21)
            self.assertEqual(row["land_m2"], 300)
            self.assertEqual(row["bedrooms"], 1)
            self.assertEqual(row["place"], "Zabljak")
            self.assertAlmostEqual(row["lat"], 43.155514)
            _, fid = upsert_from_list(
                con, {"id": "64071", "place": "Dijon", "price": 90000},
                "france-150k", ts, source="franimo")
            self.assertNotEqual(fid, hid)


class Cli(unittest.TestCase):
    def test_help(self):
        from centrarium.scrape import main
        with self.assertRaises(SystemExit) as e:
            main(["--help"])
        self.assertEqual(e.exception.code, 0)

    def test_rejects_franimo_search(self):
        from centrarium import scrape
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
