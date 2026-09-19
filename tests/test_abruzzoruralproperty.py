"""Abruzzo Rural Property adapter: parsers, registry, searches, upserts. No live HTTP."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.adapter import ADAPTERS, get
from core.db import connect, now, upsert_from_list, update_from_detail
from core.searches import enabled_names, load_searches
from abruzzoruralproperty.parse import parse_detail, parse_list, with_page

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "abruzzoruralproperty"
LIST_URL = (
    "https://www.abruzzoruralproperty.com/find-a-property/for-sale"
    "?search[price_from]=0&search[price_to]=100000&fwrealestate_update_search=1"
)
HOUSE_URL = (
    "https://www.abruzzoruralproperty.com/find-a-property/for-sale/item/"
    "1792-charming-four-bedroom-stone-house-with-guest-annex-and-land-fossalto"
)
NOLAND_URL = (
    "https://www.abruzzoruralproperty.com/find-a-property/for-sale/item/"
    "183-petite-traditional-stone-house-with-fireplace-and-stone-cellar-carunchio"
)


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class Registry(unittest.TestCase):
    def test_registered(self):
        import abruzzoruralproperty.adapter  # noqa: F401
        self.assertIn("abruzzoruralproperty", ADAPTERS)
        adapter = get("abruzzoruralproperty")
        self.assertEqual(adapter.source, "abruzzoruralproperty")
        self.assertEqual(adapter.base, "https://www.abruzzoruralproperty.com")


class Searches(unittest.TestCase):
    def test_arp_entries(self):
        searches = load_searches()
        self.assertEqual(searches["arp-houses-100k"]["source"], "abruzzoruralproperty")
        self.assertTrue(searches["arp-houses-100k"].get("enabled", True))
        self.assertIn("search[price_to]=100000", searches["arp-houses-100k"]["path"])
        self.assertIn("/find-a-property/for-sale", searches["arp-houses-100k"]["path"])
        self.assertFalse(searches["arp-houses-150k"].get("enabled", True))
        self.assertIn("search[price_to]=150000", searches["arp-houses-150k"]["path"])
        arp = enabled_names(searches, source="abruzzoruralproperty")
        self.assertEqual(arp, ["arp-houses-100k"])
        franimo = enabled_names(searches, source="franimo")
        self.assertIn("france-150k", franimo)
        self.assertNotIn("arp-houses-100k", franimo)
        api = enabled_names(searches, source="abruzzopropertyitaly")
        self.assertEqual(api, ["api-houses-100k"])
        hp = enabled_names(searches, source="holprop")
        self.assertEqual(hp, ["hp-es-houses-100k"])
        bg = enabled_names(searches, source="ok_bulgaria")
        self.assertEqual(bg, ["bg-houses-50k"])
        jp = enabled_names(searches, source="akiyaportal")
        self.assertEqual(jp, ["jp-houses-10k"])


class Pagination(unittest.TestCase):
    def test_with_page_keeps_filter_and_steps_by_six(self):
        nxt = with_page(LIST_URL, 2)
        self.assertIn("start=6", nxt)
        self.assertIn("search[price_from]=0", nxt)
        self.assertIn("search[price_to]=100000", nxt)
        self.assertIn("fwrealestate_update_search=1", nxt)
        self.assertNotIn("#", nxt)
        page3 = with_page(nxt, 3)
        self.assertIn("start=12", page3)
        page1 = with_page(nxt, 1)
        self.assertNotIn("start=", page1)
        self.assertIn("search[price_to]=100000", page1)


class ListParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = parse_list(_html("list.html"), LIST_URL)

    def test_page_meta(self):
        self.assertEqual(self.page["total_pages"], 58)
        self.assertEqual(self.page["next_url"], LIST_URL + "&start=6")
        self.assertGreaterEqual(len(self.page["listings"]), 10)
        self.assertGreaterEqual(len(self.page["listings"]), 12)

    def test_stable_ids_and_urls(self):
        ids = [r["external_id"] for r in self.page["listings"]]
        self.assertEqual(len(ids), len(set(ids)))
        house = next(r for r in self.page["listings"] if r["external_id"] == "1792")
        self.assertEqual(house["source"], "abruzzoruralproperty")
        self.assertEqual(house["url"], HOUSE_URL)
        self.assertEqual(house["reference"], "FL4245")
        self.assertEqual(house["place"], "Fossalto")
        self.assertEqual(house["price"], 42000)
        self.assertEqual(house["currency"], "EUR")
        self.assertEqual(house["beds"], 4)
        self.assertEqual(house["type"], "House with garden")
        self.assertEqual(house["land_m2"], 4700)
        self.assertTrue(house["thumb"].startswith("https://"))
        self.assertIn("Fossalto", house["snippet"])

    def test_card_with_land_in_title(self):
        row = next(r for r in self.page["listings"] if r["external_id"] == "1789")
        self.assertEqual(row["price"], 50000)
        self.assertEqual(row["place"], "Trivento")
        self.assertEqual(row["land_m2"], 800)
        self.assertEqual(row["type"], "Country house with land")

    def test_under_offer_has_no_price(self):
        row = next(r for r in self.page["listings"] if "UNDER OFFER" in (r["raw_fields"].get("status_label") or ""))
        self.assertIsNone(row["price"])
        self.assertTrue(row["external_id"])


class DetailParser(unittest.TestCase):
    def test_house(self):
        d = parse_detail(_html("detail_house.html"), HOUSE_URL)
        self.assertEqual(d["external_id"], "1792")
        self.assertEqual(d["price"], 42000)
        self.assertEqual(d["currency"], "EUR")
        self.assertEqual(d["place"], "Fossalto")
        self.assertEqual(d["dept_nl"], "Molise")
        self.assertEqual(d["type"], "House with garden")
        self.assertEqual(d["bedrooms"], 4)
        self.assertEqual(d["baths"], 2)
        self.assertEqual(d["living_m2"], 146)
        self.assertEqual(d["land_m2"], 4700)
        self.assertEqual(d["reference"], "FL4245")
        self.assertEqual(d["agent"], "Abruzzo Rural Property")
        self.assertEqual(d["energy_label"], "G")
        self.assertGreaterEqual(len(d["photos"]), 3)
        self.assertTrue(d["photos"][0].endswith("/11be280a34ef28c2c75120b6624ed7b2.jpg"))
        self.assertNotIn("/med_", d["photos"][0])
        self.assertIn("Fossalto", d["description"])
        self.assertIn("Fireplace", d["features"])
        self.assertNotIn("lat", d)
        self.assertNotIn("lon", d)

    def test_no_land_and_stale_og_price(self):
        d = parse_detail(_html("detail_noland.html"), NOLAND_URL)
        self.assertEqual(d["external_id"], "183")
        self.assertEqual(d["price"], 12000)  # page price, not the stale €15k og tag
        self.assertEqual(d["currency"], "EUR")
        self.assertEqual(d["place"], "Carunchio")
        self.assertEqual(d["region"], "Chieti")
        self.assertEqual(d["dept_nl"], "Abruzzo")
        self.assertEqual(d["type"], "Stone house")
        self.assertEqual(d["bedrooms"], 1)
        self.assertIsNone(d["living_m2"])
        self.assertIsNone(d["land_m2"])
        self.assertGreaterEqual(len(d["photos"]), 2)


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
                    con, row, "arp-houses-100k", ts, source="abruzzoruralproperty")
                self.assertEqual(status, "new")
                lids.append(lid)
            con.commit()
            n = con.execute(
                "SELECT COUNT(*) c FROM listings WHERE source=?",
                ("abruzzoruralproperty",),
            ).fetchone()["c"]
            self.assertGreaterEqual(n, 10)
            house = next(r for r in page["listings"] if r["external_id"] == "1792")
            _, hid = upsert_from_list(
                con, house, "arp-houses-100k", ts, source="abruzzoruralproperty")
            detail = parse_detail(_html("detail_house.html"), HOUSE_URL)
            update_from_detail(con, hid, detail, ts)
            row = con.execute(
                "SELECT source, external_id, price, currency, bedrooms, baths, "
                "place, living_m2, land_m2, lat, lon "
                "FROM listings WHERE id=?", (hid,)
            ).fetchone()
            self.assertEqual(row["source"], "abruzzoruralproperty")
            self.assertEqual(row["external_id"], "1792")
            self.assertEqual(row["price"], 42000)
            self.assertEqual(row["currency"], "EUR")
            self.assertEqual(row["bedrooms"], 4)
            self.assertEqual(row["baths"], 2)
            self.assertEqual(row["place"], "Fossalto")
            self.assertEqual(row["living_m2"], 146)
            self.assertEqual(row["land_m2"], 4700)
            self.assertIsNone(row["lat"])
            _, fid = upsert_from_list(
                con, {"id": "1792", "place": "Dijon", "price": 90000},
                "france-150k", ts, source="franimo")
            self.assertNotEqual(fid, hid)


class Cli(unittest.TestCase):
    def test_help(self):
        from abruzzoruralproperty.scrape import main
        with self.assertRaises(SystemExit) as e:
            main(["--help"])
        self.assertEqual(e.exception.code, 0)

    def test_rejects_franimo_search(self):
        from abruzzoruralproperty import scrape
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
