"""Holprop adapter: parsers, registry, searches, upserts. No live HTTP."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.adapter import ADAPTERS, get
from core.db import connect, now, upsert_from_list, update_from_detail
from core.searches import enabled_names, load_searches
from holprop.parse import parse_detail, parse_list, priced_row, with_page

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "holprop"
LIST_URL = "https://www.holprop.com/sale/pt/villa-house/scr/bulgaria/price/100000/"
SPAIN_URL = "https://www.holprop.com/sale/pt/villa-house/scr/spain/price/100000/"
HOUSE_URL = "https://www.holprop.com/s/sale/bg62388419/?ctype=EUR"


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class Registry(unittest.TestCase):
    def test_registered(self):
        import holprop.adapter  # noqa: F401
        self.assertIn("holprop", ADAPTERS)
        adapter = get("holprop")
        self.assertEqual(adapter.source, "holprop")
        self.assertEqual(adapter.base, "https://www.holprop.com")


class Searches(unittest.TestCase):
    def test_hp_entries(self):
        searches = load_searches()
        self.assertEqual(searches["hp-es-houses-100k"]["source"], "holprop")
        self.assertTrue(searches["hp-es-houses-100k"].get("enabled", True))
        self.assertIn("/spain/price/100000/", searches["hp-es-houses-100k"]["path"])
        self.assertFalse(searches["hp-bg-houses-100k"].get("enabled", True))
        self.assertFalse(searches["hp-pt-houses-100k"].get("enabled", True))
        self.assertFalse(searches["hp-gr-houses-100k"].get("enabled", True))
        self.assertFalse(searches["hp-it-houses-100k"].get("enabled", True))
        self.assertIn("/italy/price/100000/", searches["hp-it-houses-100k"]["path"])
        hp = enabled_names(searches, source="holprop")
        self.assertEqual(hp, ["hp-es-houses-100k"])
        franimo = enabled_names(searches, source="franimo")
        self.assertIn("france-150k", franimo)
        self.assertNotIn("hp-es-houses-100k", franimo)
        bg = enabled_names(searches, source="ok_bulgaria")
        self.assertEqual(bg, ["bg-houses-50k"])
        jp = enabled_names(searches, source="akiyaportal")
        self.assertEqual(jp, ["jp-houses-10k"])


class Pagination(unittest.TestCase):
    def test_with_page_keeps_filter(self):
        nxt = with_page(LIST_URL, 2)
        self.assertTrue(nxt.endswith("/page/2/"))
        self.assertIn("/bulgaria/price/100000/", nxt)
        page1 = with_page(nxt, 1)
        self.assertNotIn("/page/", page1)
        self.assertTrue(page1.endswith("/bulgaria/price/100000/"))


class ListParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = parse_list(_html("list.html"), LIST_URL)

    def test_page_meta(self):
        self.assertEqual(self.page["total_pages"], 43)
        self.assertEqual(self.page["next_url"], LIST_URL + "page/2/")
        self.assertGreaterEqual(len(self.page["listings"]), 10)
        self.assertEqual(len(self.page["listings"]), 12)

    def test_stable_ids_and_urls(self):
        ids = [r["external_id"] for r in self.page["listings"]]
        self.assertEqual(len(ids), len(set(ids)))
        house = next(r for r in self.page["listings"] if r["external_id"] == "bg62388419")
        self.assertEqual(house["source"], "holprop")
        self.assertEqual(house["url"], HOUSE_URL)
        self.assertEqual(house["reference"], "BG62388419")
        self.assertEqual(house["place"], "Stezherovo")
        self.assertEqual(house["region"], "Pleven")
        self.assertEqual(house["price"], 9500)
        self.assertEqual(house["currency"], "EUR")
        self.assertEqual(house["beds"], 4)
        self.assertEqual(house["living_m2"], 150)
        self.assertEqual(house["raw_fields"]["price_eur"], 9500)
        self.assertEqual(house["raw_fields"]["price_source"], "portal_eur")
        self.assertEqual(house["raw_fields"]["country"], "Bulgaria")
        self.assertTrue(house["thumb"].startswith("https://"))


class SpainList(unittest.TestCase):
    def test_spain_cards(self):
        page = parse_list(_html("list_spain.html"), SPAIN_URL)
        self.assertGreaterEqual(len(page["listings"]), 2)
        self.assertEqual(page["total_pages"], 2)
        self.assertIn("/page/2/", page["next_url"])
        row = next(r for r in page["listings"] if r["external_id"] == "es62387047")
        self.assertEqual(row["source"], "holprop")
        self.assertEqual(row["place"], "Cuevas-Del-Becerro")
        self.assertEqual(row["raw_fields"]["country"], "Spain")
        self.assertEqual(row["price"], 87000)
        self.assertEqual(row["currency"], "EUR")
        self.assertEqual(
            row["url"],
            "https://www.holprop.com/s/sale/es62387047/?ctype=EUR",
        )
        ids = [r["external_id"] for r in page["listings"]]
        self.assertTrue(all(i.startswith("es") for i in ids))


class DetailParser(unittest.TestCase):
    def test_house(self):
        d = parse_detail(_html("detail_house.html"), HOUSE_URL)
        self.assertEqual(d["external_id"], "bg62388419")
        self.assertEqual(d["price"], 9500)
        self.assertEqual(d["currency"], "EUR")
        self.assertEqual(d["living_m2"], 150)
        self.assertEqual(d["land_m2"], 2000)
        self.assertEqual(d["bedrooms"], 4)
        self.assertEqual(d["baths"], 1)
        self.assertEqual(d["place"], "Stezherovo")
        self.assertEqual(d["region"], "Pleven")
        self.assertEqual(d["type"], "Villa-House")
        self.assertEqual(d["agent"], "Holprop")
        self.assertEqual(d["agent_name"], "Bulgarian House Ltd")
        self.assertGreaterEqual(len(d["photos"]), 3)
        self.assertIn("Danube", d["description"])
        self.assertEqual(d["raw_fields"]["price_eur"], 9500)
        self.assertEqual(d["raw_fields"]["price_gbp"], 8171)
        self.assertEqual(d["raw_fields"]["price_usd"], 10905)
        self.assertEqual(d["raw_fields"]["price_source"], "portal_eur")
        self.assertEqual(d["raw_fields"]["country"], "Bulgaria")


class Currency(unittest.TestCase):
    def test_portal_eur_wins(self):
        row = priced_row(9500, 8171, 10905)
        self.assertEqual(row["price"], 9500)
        self.assertEqual(row["currency"], "EUR")
        self.assertEqual(row["raw_fields"]["price_source"], "portal_eur")
        self.assertEqual(row["raw_fields"]["price_gbp"], 8171)
        self.assertEqual(row["raw_fields"]["price_usd"], 10905)
        self.assertNotIn("fx_rate", row["raw_fields"])

    def test_gbp_only_keeps_gbp(self):
        row = priced_row(None, 8171, None)
        self.assertEqual(row["price"], 8171)
        self.assertEqual(row["currency"], "GBP")
        self.assertEqual(row["raw_fields"]["price_source"], "portal_gbp")


class Store(unittest.TestCase):
    def test_list_upsert_then_detail(self):
        page = parse_list(_html("list.html"), LIST_URL)
        with tempfile.TemporaryDirectory() as tmp:
            con = connect(Path(tmp) / "t.db")
            ts = now()
            lids = []
            for row in page["listings"]:
                status, lid = upsert_from_list(con, row, "hp-bg-houses-100k", ts,
                                              source="holprop")
                self.assertEqual(status, "new")
                lids.append(lid)
            con.commit()
            n = con.execute("SELECT COUNT(*) c FROM listings WHERE source=?",
                            ("holprop",)).fetchone()["c"]
            self.assertEqual(n, 12)
            house = next(r for r in page["listings"] if r["external_id"] == "bg62388419")
            _, hid = upsert_from_list(con, house, "hp-bg-houses-100k", ts, source="holprop")
            detail = parse_detail(_html("detail_house.html"), HOUSE_URL)
            update_from_detail(con, hid, detail, ts)
            row = con.execute(
                "SELECT source, external_id, price, currency, living_m2, land_m2, bedrooms "
                "FROM listings WHERE id=?", (hid,)
            ).fetchone()
            self.assertEqual(row["source"], "holprop")
            self.assertEqual(row["external_id"], "bg62388419")
            self.assertEqual(row["price"], 9500)
            self.assertEqual(row["currency"], "EUR")
            self.assertEqual(row["living_m2"], 150)
            self.assertEqual(row["land_m2"], 2000)
            self.assertEqual(row["bedrooms"], 4)
            _, fid = upsert_from_list(con, {"id": "bg62388419", "place": "Dijon",
                                            "price": 90000},
                                      "france-150k", ts, source="franimo")
            self.assertNotEqual(fid, hid)


class Cli(unittest.TestCase):
    def test_help(self):
        from holprop.scrape import main
        with self.assertRaises(SystemExit) as e:
            main(["--help"])
        self.assertEqual(e.exception.code, 0)

    def test_rejects_franimo_search(self):
        from holprop import scrape
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
