"""OK Bulgaria adapter: parsers, registry, searches, upserts. No live HTTP."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.adapter import ADAPTERS, get
from core.db import connect, now, upsert_from_list, update_from_detail
from core.searches import enabled_names, load_searches
from ok_bulgaria.fx import GBP_TO_EUR, gbp_to_eur
from ok_bulgaria.parse import parse_detail, parse_list, priced_row, with_page

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ok_bulgaria"
LIST_URL = (
    "https://www.cheap-bulgarian-house.co.uk/bulgaria_houses.php"
    "?stab=1&action=search&low_price=0&high_price=50000"
)
HOUSE_URL = "https://www.cheap-bulgarian-house.co.uk/houses_in_bulgaria_for_sale.php?id=30418"
LAND_URL = "https://www.cheap-bulgarian-house.co.uk/houses_in_bulgaria_for_sale.php?id=29839"


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class Registry(unittest.TestCase):
    def test_registered(self):
        import ok_bulgaria.adapter  # noqa: F401
        self.assertIn("ok_bulgaria", ADAPTERS)
        adapter = get("ok_bulgaria")
        self.assertEqual(adapter.source, "ok_bulgaria")
        self.assertEqual(adapter.base, "https://www.cheap-bulgarian-house.co.uk")


class Searches(unittest.TestCase):
    def test_bg_entries(self):
        searches = load_searches()
        self.assertEqual(searches["bg-houses-50k"]["source"], "ok_bulgaria")
        self.assertTrue(searches["bg-houses-50k"].get("enabled", True))
        self.assertIn("low_price=0", searches["bg-houses-50k"]["path"])
        self.assertIn("high_price=50000", searches["bg-houses-50k"]["path"])
        self.assertFalse(searches["bg-houses-100k"].get("enabled", True))
        self.assertFalse(searches["bg-houses-150k"].get("enabled", True))
        bg = enabled_names(searches, source="ok_bulgaria")
        self.assertEqual(bg, ["bg-houses-50k"])
        franimo = enabled_names(searches, source="franimo")
        self.assertIn("france-150k", franimo)
        self.assertNotIn("bg-houses-50k", franimo)


class Pagination(unittest.TestCase):
    def test_with_page_keeps_search_and_drops_session(self):
        url = LIST_URL
        nxt = with_page(url, 2)
        self.assertIn("page=2", nxt)
        self.assertIn("low_price=0", nxt)
        self.assertIn("high_price=50000", nxt)
        self.assertNotIn("use_session", nxt)
        sess = "https://www.cheap-bulgarian-house.co.uk/bulgaria_houses.php?page=3&use_session=yes"
        cleaned = with_page(sess, 4)
        self.assertIn("page=4", cleaned)
        self.assertNotIn("use_session", cleaned)


class ListParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = parse_list(_html("list.html"), LIST_URL)

    def test_page_meta(self):
        self.assertEqual(self.page["total_pages"], 66)
        self.assertEqual(self.page["next_url"], LIST_URL + "&page=2")
        self.assertGreaterEqual(len(self.page["listings"]), 10)
        self.assertEqual(len(self.page["listings"]), 20)

    def test_stable_ids_and_urls(self):
        ids = [r["external_id"] for r in self.page["listings"]]
        self.assertEqual(len(ids), len(set(ids)))
        first = self.page["listings"][0]
        self.assertEqual(first["external_id"], "29839")
        self.assertEqual(first["source"], "ok_bulgaria")
        self.assertEqual(
            first["url"],
            "https://www.cheap-bulgarian-house.co.uk/houses_in_bulgaria_for_sale.php?id=29839",
        )
        self.assertEqual(first["reference"], "KVP2028")
        self.assertEqual(first["type"], "land - agricultural")
        self.assertEqual(first["place"], "Nikolaevka")
        self.assertEqual(first["region"], "Varna")
        self.assertEqual(first["price"], 1500)
        self.assertEqual(first["currency"], "EUR")
        self.assertEqual(first["land_m2"], 707)
        self.assertEqual(first["raw_fields"]["price_gbp"], 1286)
        self.assertEqual(first["raw_fields"]["price_source"], "portal_eur")

    def test_house_card(self):
        house = next(r for r in self.page["listings"] if r["external_id"] == "30418")
        self.assertEqual(house["type"], "2 bedrooms")
        self.assertEqual(house["place"], "Chuchuligovo")
        self.assertEqual(house["beds"], 2)
        self.assertEqual(house["price"], 2700)
        self.assertTrue(house["thumb"].startswith("https://"))


class DetailParser(unittest.TestCase):
    def test_house(self):
        d = parse_detail(_html("detail_house.html"), HOUSE_URL)
        self.assertEqual(d["external_id"], "30418")
        self.assertEqual(d["price"], 2700)
        self.assertEqual(d["currency"], "EUR")
        self.assertEqual(d["living_m2"], 100)
        self.assertEqual(d["land_m2"], 2700)
        self.assertEqual(d["bedrooms"], 2)
        self.assertEqual(d["place"], "Chuchuligovo")
        self.assertEqual(d["region"], "Blagoevgrad")
        self.assertEqual(d["reference"], "KVP2157")
        self.assertEqual(d["agent"], "OK Bulgaria")
        self.assertGreaterEqual(len(d["photos"]), 6)
        self.assertIn("vineyards", d["description"])
        self.assertEqual(d["raw_fields"]["price_gbp"], 2315)
        self.assertEqual(d["raw_fields"]["km. to sea"], "100")

    def test_land_clears_living_area(self):
        d = parse_detail(_html("detail_land.html"), LAND_URL)
        self.assertEqual(d["external_id"], "29839")
        self.assertEqual(d["type"], "Land - agricultural")
        self.assertIsNone(d["living_m2"])
        self.assertEqual(d["land_m2"], 707)
        self.assertEqual(d["price"], 1500)
        self.assertGreaterEqual(len(d["photos"]), 3)


class Currency(unittest.TestCase):
    def test_portal_eur_wins(self):
        row = priced_row(1500, 1286)
        self.assertEqual(row["price"], 1500)
        self.assertEqual(row["currency"], "EUR")
        self.assertEqual(row["raw_fields"]["price_source"], "portal_eur")
        self.assertNotIn("fx_rate", row["raw_fields"])

    def test_gbp_only_converts(self):
        row = priced_row(None, 1000)
        self.assertEqual(row["price"], gbp_to_eur(1000))
        self.assertEqual(row["currency"], "EUR")
        self.assertEqual(row["raw_fields"]["price_source"], "gbp_converted")
        self.assertEqual(row["raw_fields"]["fx_rate"], GBP_TO_EUR)


class Store(unittest.TestCase):
    def test_list_upsert_then_detail(self):
        page = parse_list(_html("list.html"), LIST_URL)
        with tempfile.TemporaryDirectory() as tmp:
            con = connect(Path(tmp) / "t.db")
            ts = now()
            lids = []
            for row in page["listings"]:
                status, lid = upsert_from_list(con, row, "bg-houses-50k", ts,
                                              source="ok_bulgaria")
                self.assertEqual(status, "new")
                lids.append(lid)
            con.commit()
            n = con.execute("SELECT COUNT(*) c FROM listings WHERE source=?",
                            ("ok_bulgaria",)).fetchone()["c"]
            self.assertEqual(n, 20)
            house = next(r for r in page["listings"] if r["external_id"] == "30418")
            _, hid = upsert_from_list(con, house, "bg-houses-50k", ts, source="ok_bulgaria")
            detail = parse_detail(_html("detail_house.html"), HOUSE_URL)
            update_from_detail(con, hid, detail, ts)
            row = con.execute(
                "SELECT source, external_id, price, currency, living_m2, land_m2, bedrooms "
                "FROM listings WHERE id=?", (hid,)
            ).fetchone()
            self.assertEqual(row["source"], "ok_bulgaria")
            self.assertEqual(row["external_id"], "30418")
            self.assertEqual(row["price"], 2700)
            self.assertEqual(row["currency"], "EUR")
            self.assertEqual(row["living_m2"], 100)
            self.assertEqual(row["land_m2"], 2700)
            self.assertEqual(row["bedrooms"], 2)
            # same portal id from franimo must not collide
            from core.db import upsert_from_list as up
            _, fid = up(con, {"id": 30418, "place": "Dijon", "price": 90000},
                        "france-150k", ts, source="franimo")
            self.assertNotEqual(fid, hid)


class Cli(unittest.TestCase):
    def test_help(self):
        from ok_bulgaria.scrape import main
        with self.assertRaises(SystemExit) as e:
            main(["--help"])
        self.assertEqual(e.exception.code, 0)

    def test_rejects_franimo_search(self):
        from ok_bulgaria import scrape
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
