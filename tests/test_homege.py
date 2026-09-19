"""home.ge adapter: parsers, registry, searches, upserts. No live HTTP."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.adapter import ADAPTERS, get
from core.db import connect, now, upsert_from_list, update_from_detail
from core.searches import enabled_names, load_searches
from homege.fx import GEL_PER_EUR, USD_TO_EUR, gel_to_eur, usd_to_eur
from homege.parse import (
    parse_detail,
    parse_list,
    parse_money,
    priced_row,
    with_page,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "homege"
LIST_URL = (
    "https://www.home.ge/en/saxlebi-agarakebi/search-results.html"
    "?action=search&post_form_key=saxlebi_agarakebi_quick"
    "&f[Category_ID]=88&f[price][from]=0&f[price][to]=100000"
    "&f[price][currency]=euro"
)
HOUSE_URL = (
    "https://www.home.ge/en/saxlebi-agarakebi/iyideba-saxlebi-agarakebi/"
    "house-for-sale-3-room-borjomi-timotesubani-478109.html"
)
PLOT_URL = (
    "https://www.home.ge/en/saxlebi-agarakebi/iyideba-saxlebi-agarakebi/"
    "house-for-sale-6-room-borjomi-tsikhisjvari-421096.html"
)
CHEAP_URL = (
    "https://www.home.ge/en/saxlebi-agarakebi/iyideba-saxlebi-agarakebi/"
    "house-for-sale-8-room-tsalka-bareti-456007.html"
)


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class Registry(unittest.TestCase):
    def test_registered(self):
        import homege.adapter  # noqa: F401
        self.assertIn("homege", ADAPTERS)
        adapter = get("homege")
        self.assertEqual(adapter.source, "homege")
        self.assertEqual(adapter.base, "https://www.home.ge")


class Searches(unittest.TestCase):
    def test_hg_entries(self):
        searches = load_searches()
        seed = searches["hg-ge-houses-100k"]
        self.assertEqual(seed["source"], "homege")
        self.assertTrue(seed.get("enabled", True))
        self.assertIn("f[Category_ID]=88", seed["path"])
        self.assertIn("f[price][to]=100000", seed["path"])
        self.assertIn("f[price][currency]=euro", seed["path"])
        self.assertEqual(seed["skip"]["above"], 100000)
        self.assertFalse(searches["hg-ge-houses-150k"].get("enabled", True))
        self.assertIn("f[price][to]=150000", searches["hg-ge-houses-150k"]["path"])
        self.assertFalse(searches["hg-ge-houses"].get("enabled", True))
        self.assertIn("/iyideba-saxlebi-agarakebi.html", searches["hg-ge-houses"]["path"])
        hg = enabled_names(searches, source="homege")
        self.assertEqual(hg, ["hg-ge-houses-100k"])
        franimo = enabled_names(searches, source="franimo")
        self.assertIn("france-150k", franimo)
        self.assertNotIn("hg-ge-houses-100k", franimo)
        mw = enabled_names(searches, source="mubawab")
        self.assertEqual(mw, ["mw-ma-houses-100k"])


class Pagination(unittest.TestCase):
    def test_with_page_keeps_euro_filter(self):
        nxt = with_page(LIST_URL, 2)
        self.assertIn("/search-results/index2.html?", nxt)
        self.assertIn("f[price][to]=100000", nxt)
        self.assertIn("f[price][currency]=euro", nxt)
        page1 = with_page(nxt, 1)
        self.assertNotIn("/index", page1)
        self.assertIn("/search-results.html?", page1)

    def test_category_pager(self):
        cat = "https://www.home.ge/en/saxlebi-agarakebi/iyideba-saxlebi-agarakebi.html"
        nxt = with_page(cat, 2)
        self.assertTrue(nxt.endswith("/iyideba-saxlebi-agarakebi/index2.html"))
        self.assertEqual(with_page(nxt, 1), cat)


class Money(unittest.TestCase):
    def test_usd_eur_gel(self):
        self.assertEqual(parse_money("70,000.00 $"), (70000, "USD"))
        self.assertEqual(parse_money("55.00 $"), (55, "USD"))
        self.assertEqual(parse_money("85,000 €"), (85000, "EUR"))
        self.assertEqual(parse_money("150,000.00 ₾"), (150000, "GEL"))
        self.assertEqual(parse_money("Price on request"), (None, None))

    def test_documented_rates(self):
        self.assertEqual(USD_TO_EUR, 0.85)
        self.assertEqual(GEL_PER_EUR, 3.03)
        self.assertEqual(usd_to_eur(70000), 59500)
        self.assertEqual(usd_to_eur(110000), 93500)
        self.assertEqual(gel_to_eur(150000), 49505)
        row = priced_row(70000, "USD")
        self.assertEqual(row["price"], 59500)
        self.assertEqual(row["currency"], "EUR")
        self.assertEqual(row["raw_fields"]["price_usd"], 70000)
        self.assertEqual(row["raw_fields"]["price_source"], "usd_converted")
        gel = priced_row(150000, "GEL")
        self.assertEqual(gel["price"], 49505)
        self.assertEqual(gel["raw_fields"]["price_source"], "gel_converted")
        eur = priced_row(85000, "EUR")
        self.assertEqual(eur["price"], 85000)
        self.assertEqual(eur["raw_fields"]["price_source"], "portal_eur")


class ListParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = parse_list(_html("list.html"), LIST_URL)

    def test_page_meta(self):
        self.assertEqual(self.page["total_pages"], 2)
        self.assertEqual(
            self.page["next_url"],
            with_page(LIST_URL, 2),
        )
        self.assertGreaterEqual(len(self.page["listings"]), 5)

    def test_unique_house_ids(self):
        ids = [r["external_id"] for r in self.page["listings"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertNotIn(None, ids)
        urls = [r["url"] for r in self.page["listings"]]
        self.assertTrue(all("/saxlebi-agarakebi/" in u for u in urls))
        self.assertFalse(any("/miwis-nakveti/" in u for u in urls))

    def test_borjomi_usd_house(self):
        house = next(r for r in self.page["listings"] if r["external_id"] == "478109")
        self.assertEqual(house["source"], "homege")
        self.assertEqual(house["url"], HOUSE_URL)
        self.assertEqual(house["place"], "Timotesubani")
        self.assertEqual(house["region"], "Borjomi")
        self.assertEqual(house["price"], 59500)
        self.assertEqual(house["currency"], "EUR")
        self.assertEqual(house["living_m2"], 47)
        self.assertIsNone(house["land_m2"])
        self.assertEqual(house["rooms"], 3)
        self.assertEqual(house["beds"], 2)
        self.assertEqual(house["baths"], 1)
        self.assertEqual(house["type"], "House For Sale")
        self.assertTrue(house["promoted"])
        self.assertEqual(house["raw_fields"]["price_usd"], 70000)
        self.assertEqual(house["raw_fields"]["price_source"], "usd_converted")
        self.assertEqual(house["raw_fields"]["country"], "Georgia")
        self.assertTrue(house["thumb"].startswith("https://fra1.digitaloceanspaces.com/"))

    def test_usd_110k_stays_under_eur_cap(self):
        row = next(r for r in self.page["listings"] if r["external_id"] == "476949")
        self.assertEqual(row["raw_fields"]["price_usd"], 110000)
        self.assertEqual(row["price"], 93500)
        self.assertLessEqual(row["price"], 100000)

    def test_priceless_card(self):
        row = next(r for r in self.page["listings"] if r["external_id"] == "477944")
        self.assertIsNone(row["price"])
        self.assertEqual(row["region"], "Tbilisi")
        self.assertEqual(row["living_m2"], 180)

    def test_plot_sized_area_moved_to_land(self):
        row = next(r for r in self.page["listings"] if r["external_id"] == "421096")
        self.assertIsNone(row["living_m2"])
        self.assertEqual(row["land_m2"], 1500)
        self.assertTrue(row["raw_fields"].get("area_looks_like_plot"))

    def test_absurd_usd_price_is_kept(self):
        row = next(r for r in self.page["listings"] if r["external_id"] == "456007")
        self.assertEqual(row["raw_fields"]["price_usd"], 55)
        self.assertEqual(row["price"], 47)
        self.assertEqual(row["place"], "Bareti")


class DetailParser(unittest.TestCase):
    def test_house_usd(self):
        d = parse_detail(_html("detail_house.html"), HOUSE_URL)
        self.assertEqual(d["external_id"], "478109")
        self.assertEqual(d["price"], 59500)
        self.assertEqual(d["currency"], "EUR")
        self.assertEqual(d["living_m2"], 47)
        self.assertEqual(d["land_m2"], 1000)
        self.assertNotEqual(d["living_m2"], d["land_m2"])
        self.assertEqual(d["bedrooms"], 2)
        self.assertEqual(d["rooms"], 3)
        self.assertEqual(d["baths"], 1)
        self.assertEqual(d["place"], "Timotesubani")
        self.assertEqual(d["region"], "Borjomi")
        self.assertEqual(d["type"], "House For Sale")
        self.assertEqual(d["reference"], "2366460")
        self.assertAlmostEqual(d["lat"], 41.8035748)
        self.assertAlmostEqual(d["lon"], 43.5145779)
        self.assertGreaterEqual(len(d["photos"]), 3)
        self.assertIn("ტიმოთესუბანში", d["description"])
        self.assertEqual(d["raw_fields"]["price_usd"], 70000)
        self.assertEqual(d["raw_fields"]["country"], "Georgia")
        self.assertIn("ownership_note", d["raw_fields"])
        self.assertIn("Water", d["features"] or "")

    def test_plot_area_without_yard(self):
        d = parse_detail(_html("detail_plot.html"), PLOT_URL)
        self.assertEqual(d["external_id"], "421096")
        self.assertEqual(d["price"], 51000)
        self.assertIsNone(d["living_m2"])
        self.assertEqual(d["land_m2"], 1500)
        self.assertTrue(d["raw_fields"].get("area_looks_like_plot"))
        self.assertEqual(d["place"], "Tsikhisjvari")
        self.assertEqual(d["region"], "Borjomi")
        self.assertEqual(d["raw_fields"]["cadastral_code"], "64.29.04.104")
        self.assertAlmostEqual(d["lat"], 41.7199075)
        self.assertAlmostEqual(d["lon"], 43.44377619999999)

    def test_cheap_household_plot_kept(self):
        d = parse_detail(_html("detail_cheap.html"), CHEAP_URL)
        self.assertEqual(d["external_id"], "456007")
        self.assertEqual(d["price"], 47)
        self.assertEqual(d["raw_fields"]["price_usd"], 55)
        self.assertEqual(d["living_m2"], 160)
        self.assertEqual(d["land_m2"], 3600)
        self.assertTrue(d["raw_fields"].get("household_plot"))
        self.assertIn("ownership_note", d["raw_fields"])
        self.assertEqual(d["place"], "Bareti")
        self.assertEqual(d["region"], "Tsalka")

    def test_gel_offer_converts(self):
        html = """
        <html><head>
        <script type="application/ld+json">{"@type":"Product","sku":"999001",
        "name":"House For Sale, 4 Room, Kutaisi",
        "offers":{"price":150000,"priceCurrency":"GEL"},
        "description":"სასოფლო მიწა next to the house"}</script>
        </head><body>
        <h1>House For Sale, 4 Room, Kutaisi</h1>
        <div class="price-tag"><span>150,000.00 ₾</span></div>
        <div class="listing-fields">
          <div class="table-cell" id="df_field_square_feet">
            <div class="name">Area</div><div class="value">90 m</div></div>
          <div class="table-cell" id="df_field_mdebareoba">
            <div class="name">City/Region</div><div class="value">Kutaisi</div></div>
        </div>
        </body></html>
        """
        d = parse_detail(
            html,
            "https://www.home.ge/en/saxlebi-agarakebi/iyideba-saxlebi-agarakebi/x-999001.html",
        )
        self.assertEqual(d["external_id"], "999001")
        self.assertEqual(d["price"], 49505)
        self.assertEqual(d["raw_fields"]["price_gel"], 150000)
        self.assertTrue(d["raw_fields"].get("agricultural_land"))
        self.assertEqual(d["living_m2"], 90)
        self.assertEqual(d["region"], "Kutaisi")


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
                    con, row, "hg-ge-houses-100k", ts, source="homege")
                self.assertEqual(status, "new")
                lids.append(lid)
            con.commit()
            n = con.execute(
                "SELECT COUNT(*) c FROM listings WHERE source=?",
                ("homege",),
            ).fetchone()["c"]
            self.assertGreaterEqual(n, 4)
            house = next(r for r in page["listings"] if r["external_id"] == "478109")
            _, hid = upsert_from_list(
                con, house, "hg-ge-houses-100k", ts, source="homege")
            detail = parse_detail(_html("detail_house.html"), HOUSE_URL)
            update_from_detail(con, hid, detail, ts)
            row = con.execute(
                "SELECT source, external_id, price, currency, living_m2, land_m2, "
                "bedrooms, place, lat, lon "
                "FROM listings WHERE id=?", (hid,)
            ).fetchone()
            self.assertEqual(row["source"], "homege")
            self.assertEqual(row["external_id"], "478109")
            self.assertEqual(row["price"], 59500)
            self.assertEqual(row["currency"], "EUR")
            self.assertEqual(row["living_m2"], 47)
            self.assertEqual(row["land_m2"], 1000)
            self.assertEqual(row["bedrooms"], 2)
            self.assertEqual(row["place"], "Timotesubani")
            self.assertAlmostEqual(row["lat"], 41.8035748)
            _, fid = upsert_from_list(
                con, {"id": "478109", "place": "Dijon", "price": 90000},
                "france-150k", ts, source="franimo")
            self.assertNotEqual(fid, hid)


class Cli(unittest.TestCase):
    def test_help(self):
        from homege.scrape import main
        with self.assertRaises(SystemExit) as e:
            main(["--help"])
        self.assertEqual(e.exception.code, 0)

    def test_rejects_franimo_search(self):
        from homege import scrape
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
