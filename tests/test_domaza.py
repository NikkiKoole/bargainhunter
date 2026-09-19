"""Domaza adapter: parsers, registry, searches, upserts. No live HTTP."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.adapter import ADAPTERS, get
from core.db import connect, now, upsert_from_list, update_from_detail
from core.searches import enabled_names, load_searches
from domaza.fx import USD_TO_EUR, usd_to_eur
from domaza.parse import parse_detail, parse_list, priced_row, with_page

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "domaza"
LIST_URL = "https://www.domaza.com/house_montenegro-17-4340-146-0-0-0-sl/"
ALBANIA_URL = "https://www.domaza.com/house_albania-17-4340-3-0-0-0-sl/"
HOUSE_URL = (
    "https://www.domaza.com/house_suscepan_herceg_novi_municipality_montenegro"
    "-17-8687837-p/"
)
RENT_ID = "8701032"
SALE_ID = "8687837"


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class Registry(unittest.TestCase):
    def test_registered(self):
        import domaza.adapter  # noqa: F401
        self.assertIn("domaza", ADAPTERS)
        adapter = get("domaza")
        self.assertEqual(adapter.source, "domaza")
        self.assertEqual(adapter.base, "https://www.domaza.com")


class Searches(unittest.TestCase):
    def test_dz_entries(self):
        searches = load_searches()
        self.assertEqual(searches["dz-me-houses"]["source"], "domaza")
        self.assertTrue(searches["dz-me-houses"].get("enabled", True))
        self.assertIn("/house_montenegro-17-4340-146-0-0-0-sl/",
                      searches["dz-me-houses"]["path"])
        self.assertNotIn("/s/", searches["dz-me-houses"]["path"])
        self.assertFalse(searches["dz-al-houses"].get("enabled", True))
        self.assertIn("/house_albania-17-4340-3-0-0-0-sl/",
                      searches["dz-al-houses"]["path"])
        self.assertFalse(searches["dz-rs-houses"].get("enabled", True))
        self.assertIn("/house_serbia-17-4340-193-0-0-0-sl/",
                      searches["dz-rs-houses"]["path"])
        self.assertFalse(searches["dz-ge-houses"].get("enabled", True))
        self.assertIn("/house_georgia-17-4340-80-0-0-0-sl/",
                      searches["dz-ge-houses"]["path"])
        # HASH filter URLs are documented as fragile — none of the seeds use them.
        for spec in searches.values():
            if spec.get("source") == "domaza":
                self.assertNotRegex(spec["path"], r"/s/[0-9a-fA-F]{8,}")
                self.assertNotIn("/r/s/", spec["path"])
        dz = enabled_names(searches, source="domaza")
        self.assertEqual(dz, ["dz-me-houses"])
        franimo = enabled_names(searches, source="franimo")
        self.assertIn("france-150k", franimo)
        self.assertNotIn("dz-me-houses", franimo)
        hp = enabled_names(searches, source="holprop")
        self.assertEqual(hp, ["hp-es-houses-100k"])
        bg = enabled_names(searches, source="ok_bulgaria")
        self.assertEqual(bg, ["bg-houses-50k"])
        jp = enabled_names(searches, source="akiyaportal")
        self.assertEqual(jp, ["jp-houses-10k"])


class Pagination(unittest.TestCase):
    def test_with_page_keeps_filter(self):
        nxt = with_page(LIST_URL, 2)
        self.assertTrue(nxt.endswith("/_page/2/"))
        self.assertIn("/house_montenegro-17-4340-146-0-0-0-sl/", nxt)
        page1 = with_page(nxt, 1)
        self.assertNotIn("/_page/", page1)
        self.assertTrue(page1.endswith("/house_montenegro-17-4340-146-0-0-0-sl/"))


class ListParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = parse_list(_html("list.html"), LIST_URL)

    def test_page_meta(self):
        self.assertEqual(self.page["total_pages"], 5)
        self.assertEqual(self.page["next_url"], LIST_URL + "_page/2/")
        self.assertEqual(len(self.page["listings"]), 4)

    def test_stable_ids_and_urls(self):
        ids = [r["external_id"] for r in self.page["listings"]]
        self.assertEqual(len(ids), len(set(ids)))
        house = next(r for r in self.page["listings"] if r["external_id"] == SALE_ID)
        self.assertEqual(house["source"], "domaza")
        self.assertEqual(house["url"], HOUSE_URL)
        self.assertEqual(house["place"], "Suscepan")
        self.assertEqual(house["region"], "Herceg Novi Municipality")
        self.assertEqual(house["price"], 215000)
        self.assertEqual(house["currency"], "EUR")
        self.assertEqual(house["beds"], 2)
        self.assertEqual(house["living_m2"], 47)
        self.assertEqual(house["type"], "House")
        self.assertEqual(house["raw_fields"]["price_eur"], 215000)
        self.assertEqual(house["raw_fields"]["price_source"], "portal_eur")
        self.assertEqual(house["raw_fields"]["country"], "Montenegro")
        self.assertEqual(house["raw_fields"]["deal"], "sale")
        self.assertEqual(house["agent_name"], "RIVIJERA REAL ESTATE")
        self.assertTrue(house["thumb"].startswith("https://"))

    def test_rental_kept_with_deal(self):
        rent = next(r for r in self.page["listings"] if r["external_id"] == RENT_ID)
        self.assertEqual(rent["price"], 3000)
        self.assertEqual(rent["currency"], "EUR")
        self.assertEqual(rent["place"], "Zabjelo")
        self.assertEqual(rent["region"], "Podgorica")
        self.assertEqual(rent["raw_fields"]["deal"], "rent")
        self.assertEqual(rent["beds"], 4)


class AlbaniaList(unittest.TestCase):
    def test_mixed_types_still_parse(self):
        page = parse_list(_html("list_albania.html"), ALBANIA_URL)
        self.assertGreaterEqual(len(page["listings"]), 3)
        self.assertEqual(page["total_pages"], 1)
        self.assertIsNone(page["next_url"])
        house = next(r for r in page["listings"] if r["external_id"] == "8180744")
        self.assertEqual(house["source"], "domaza")
        self.assertEqual(house["type"], "House")
        self.assertEqual(house["price"], 580000)
        self.assertEqual(house["currency"], "EUR")
        # .com EN Albania house URL leaked leftover Greece cards (2026-09-19).
        self.assertEqual(house["raw_fields"]["country"], "Greece")
        self.assertEqual(house["place"], "Troizinia")
        plot = next(r for r in page["listings"] if r["type"] == "Building plot")
        self.assertEqual(plot["external_id"], "8109597")
        self.assertEqual(plot["price"], 55000)
        self.assertEqual(plot["raw_fields"]["country"], "Greece")
        ids = [r["external_id"] for r in page["listings"]]
        self.assertEqual(len(ids), len(set(ids)))


class DetailParser(unittest.TestCase):
    def test_house(self):
        d = parse_detail(_html("detail_house.html"), HOUSE_URL)
        self.assertEqual(d["external_id"], SALE_ID)
        self.assertEqual(d["price"], 215000)
        self.assertEqual(d["currency"], "EUR")
        self.assertEqual(d["living_m2"], 47)
        self.assertEqual(d["land_m2"], 406)
        self.assertEqual(d["bedrooms"], 2)
        self.assertEqual(d["baths"], 1)
        self.assertEqual(d["place"], "Suscepan")
        self.assertEqual(d["region"], "Herceg Novi Municipality")
        self.assertEqual(d["type"], "House")
        self.assertEqual(d["agent"], "Domaza")
        self.assertIn("Rivijera", d.get("agent_name") or "")
        self.assertGreaterEqual(len(d["photos"]), 3)
        self.assertIn("Suscepan", d["description"])
        self.assertEqual(d["raw_fields"]["price_eur"], 215000)
        self.assertEqual(d["raw_fields"]["price_source"], "portal_eur")
        self.assertEqual(d["raw_fields"]["country"], "Montenegro")
        self.assertEqual(d["raw_fields"]["deal"], "sale")
        self.assertTrue(d["url"].endswith(f"-17-{SALE_ID}-p/"))


class Currency(unittest.TestCase):
    def test_portal_eur_wins(self):
        row = priced_row(3000, 3446)
        self.assertEqual(row["price"], 3000)
        self.assertEqual(row["currency"], "EUR")
        self.assertEqual(row["raw_fields"]["price_source"], "portal_eur")
        self.assertEqual(row["raw_fields"]["price_usd"], 3446)
        self.assertNotIn("fx_rate", row["raw_fields"])

    def test_usd_only_converts(self):
        row = priced_row(None, 3446)
        self.assertEqual(row["price"], usd_to_eur(3446))
        self.assertEqual(row["currency"], "EUR")
        self.assertEqual(row["raw_fields"]["price_source"], "usd_fx")
        self.assertEqual(row["raw_fields"]["fx_rate"], USD_TO_EUR)
        self.assertAlmostEqual(usd_to_eur(3446), 3000, delta=2)


class Store(unittest.TestCase):
    def test_list_upsert_then_detail(self):
        page = parse_list(_html("list.html"), LIST_URL)
        with tempfile.TemporaryDirectory() as tmp:
            con = connect(Path(tmp) / "t.db")
            ts = now()
            lids = []
            for row in page["listings"]:
                status, lid = upsert_from_list(con, row, "dz-me-houses", ts,
                                              source="domaza")
                self.assertEqual(status, "new")
                lids.append(lid)
            con.commit()
            n = con.execute("SELECT COUNT(*) c FROM listings WHERE source=?",
                            ("domaza",)).fetchone()["c"]
            self.assertEqual(n, 4)
            house = next(r for r in page["listings"] if r["external_id"] == SALE_ID)
            _, hid = upsert_from_list(con, house, "dz-me-houses", ts, source="domaza")
            detail = parse_detail(_html("detail_house.html"), HOUSE_URL)
            update_from_detail(con, hid, detail, ts)
            row = con.execute(
                "SELECT source, external_id, price, currency, living_m2, land_m2, bedrooms "
                "FROM listings WHERE id=?", (hid,)
            ).fetchone()
            self.assertEqual(row["source"], "domaza")
            self.assertEqual(row["external_id"], SALE_ID)
            self.assertEqual(row["price"], 215000)
            self.assertEqual(row["currency"], "EUR")
            self.assertEqual(row["living_m2"], 47)
            self.assertEqual(row["land_m2"], 406)
            self.assertEqual(row["bedrooms"], 2)
            _, fid = upsert_from_list(con, {"id": SALE_ID, "place": "Dijon",
                                            "price": 90000},
                                      "france-150k", ts, source="franimo")
            self.assertNotEqual(fid, hid)


class Cli(unittest.TestCase):
    def test_help(self):
        from domaza.scrape import main
        with self.assertRaises(SystemExit) as e:
            main(["--help"])
        self.assertEqual(e.exception.code, 0)

    def test_rejects_franimo_search(self):
        from domaza import scrape
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
