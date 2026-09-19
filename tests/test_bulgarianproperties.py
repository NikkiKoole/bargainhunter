"""Bulgarian Properties adapter: parsers, registry, searches, upserts. No live HTTP."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.adapter import ADAPTERS, get
from core.db import connect, now, upsert_from_list, update_from_detail
from core.searches import enabled_names, load_searches
from bulgarianproperties.fx import GBP_TO_EUR, USD_TO_EUR, gbp_to_eur, usd_to_eur
from bulgarianproperties.parse import (
    listing_id,
    parse_detail,
    parse_list,
    parse_money,
    priced_row,
    with_page,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "bulgarianproperties"
LIST_URL = (
    "https://www.bulgarianproperties.com/"
    "properties-in-bulgaria-under-ten-thousand-pounds.html"
)
HOUSE_URL = (
    "https://www.bulgarianproperties.com/Houses_in_Bulgaria/"
    "AD91080BG_House_for_sale_near_Pavlikeni.html"
)
RESERVED_URL = (
    "https://www.bulgarianproperties.com/Houses_in_Bulgaria/"
    "AD88772BG_House_for_sale_near_Veliko_Tarnovo.html"
)


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class Registry(unittest.TestCase):
    def test_registered(self):
        import bulgarianproperties.adapter  # noqa: F401
        self.assertIn("bulgarianproperties", ADAPTERS)
        adapter = get("bulgarianproperties")
        self.assertEqual(adapter.source, "bulgarianproperties")
        self.assertEqual(adapter.base, "https://www.bulgarianproperties.com")


class Searches(unittest.TestCase):
    def test_bp_entries(self):
        searches = load_searches()
        seed = searches["bp-bg-under-10k"]
        self.assertEqual(seed["source"], "bulgarianproperties")
        self.assertTrue(seed.get("enabled", True))
        self.assertIn("under-ten-thousand-pounds.html", seed["path"])
        self.assertNotIn("Search/", seed["path"])
        self.assertNotIn("minprice", seed["path"])
        self.assertNotIn("maxprice", seed["path"])
        self.assertNotIn("page=", seed["path"])
        self.assertEqual(seed["skip"]["above"], 15000)
        self.assertFalse(searches["bp-bg-rural-houses"].get("enabled", True))
        self.assertIn("/rural_houses.html", searches["bp-bg-rural-houses"]["path"])
        self.assertFalse(searches["bp-bg-houses"].get("enabled", True))
        self.assertIn("/Houses_in_Bulgaria/index.html", searches["bp-bg-houses"]["path"])
        for name in ("bp-bg-under-10k", "bp-bg-rural-houses", "bp-bg-houses"):
            path = searches[name]["path"]
            self.assertNotIn("Search/", path)
            self.assertNotRegex(path, r"[?&](page|minprice|maxprice|ID)=")
        bp = enabled_names(searches, source="bulgarianproperties")
        self.assertEqual(bp, ["bp-bg-under-10k"])
        franimo = enabled_names(searches, source="franimo")
        self.assertIn("france-150k", franimo)
        self.assertNotIn("bp-bg-under-10k", franimo)
        bg = enabled_names(searches, source="ok_bulgaria")
        self.assertEqual(bg, ["bg-houses-50k"])


class Pagination(unittest.TestCase):
    def test_browse_suffix(self):
        nxt = with_page(LIST_URL, 2)
        self.assertEqual(
            nxt,
            "https://www.bulgarianproperties.com/"
            "properties-in-bulgaria-under-ten-thousand-pounds1.html",
        )
        self.assertEqual(with_page(nxt, 1), LIST_URL)
        self.assertNotIn("page=", nxt)
        self.assertNotIn("index0", with_page(LIST_URL, 1))

    def test_index_pager(self):
        cat = "https://www.bulgarianproperties.com/Houses_in_Bulgaria/index.html"
        nxt = with_page(cat, 2)
        self.assertTrue(nxt.endswith("/Houses_in_Bulgaria/index1.html"))
        self.assertEqual(with_page(nxt, 1), cat)
        self.assertNotIn("index0", with_page(cat, 1))
        self.assertTrue(with_page(cat, 3).endswith("/index2.html"))

    def test_rural_houses_suffix(self):
        url = "https://www.bulgarianproperties.com/rural_houses.html"
        self.assertTrue(with_page(url, 2).endswith("/rural_houses1.html"))
        self.assertTrue(with_page(url, 28).endswith("/rural_houses27.html"))
        self.assertEqual(with_page(with_page(url, 28), 1), url)


class Money(unittest.TestCase):
    def test_parse_money(self):
        self.assertEqual(parse_money("€ 10 900"), (10900, "EUR"))
        self.assertEqual(parse_money("£ 9 375"), (9375, "GBP"))
        self.assertEqual(parse_money("$ 12 512"), (12512, "USD"))
        self.assertEqual(parse_money("Price on request"), (None, None))

    def test_portal_eur_wins(self):
        row = priced_row(10900, 9375, 12512)
        self.assertEqual(row["price"], 10900)
        self.assertEqual(row["currency"], "EUR")
        self.assertEqual(row["raw_fields"]["price_source"], "portal_eur")
        self.assertEqual(row["raw_fields"]["price_gbp"], 9375)
        self.assertNotIn("fx_rate", row["raw_fields"])

    def test_gbp_only_converts(self):
        row = priced_row(None, 1000)
        self.assertEqual(row["price"], gbp_to_eur(1000))
        self.assertEqual(row["currency"], "EUR")
        self.assertEqual(row["raw_fields"]["price_source"], "gbp_converted")
        self.assertEqual(row["raw_fields"]["fx_rate"], GBP_TO_EUR)

    def test_documented_rates(self):
        self.assertEqual(GBP_TO_EUR, 1.163)
        self.assertEqual(USD_TO_EUR, 0.87)
        self.assertEqual(gbp_to_eur(9375), 10903)
        self.assertEqual(usd_to_eur(12512), 10885)


class Ids(unittest.TestCase):
    def test_ad_pattern(self):
        self.assertEqual(
            listing_id(HOUSE_URL),
            "AD91080BG",
        )
        self.assertEqual(
            listing_id("/Houses_in_Bulgaria/AD88772BG_House_for_sale_near_Veliko_Tarnovo.html"),
            "AD88772BG",
        )
        self.assertIsNone(listing_id("/Houses_in_Bulgaria/index.html"))


class ListParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = parse_list(_html("list.html"), LIST_URL)

    def test_page_meta(self):
        self.assertEqual(self.page["total_pages"], 2)
        self.assertEqual(self.page["next_url"], with_page(LIST_URL, 2))
        self.assertGreaterEqual(len(self.page["listings"]), 10)
        self.assertNotIn("page=", self.page["next_url"] or "")

    def test_unique_ad_ids(self):
        ids = [r["external_id"] for r in self.page["listings"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertNotIn(None, ids)
        self.assertTrue(all(i.startswith("AD") and i.endswith("BG") for i in ids))
        urls = [r["url"] for r in self.page["listings"]]
        self.assertTrue(all("/AD" in u and "BG_" in u for u in urls))
        self.assertFalse(any("/Search/" in u for u in urls))

    def test_cheap_house_card(self):
        house = next(r for r in self.page["listings"] if r["external_id"] == "AD91080BG")
        self.assertEqual(house["source"], "bulgarianproperties")
        self.assertEqual(house["url"], HOUSE_URL)
        self.assertEqual(house["place"], "Dolna Lipnitsa")
        self.assertEqual(house["region"], "Pavlikeni")
        self.assertEqual(house["price"], 5900)
        self.assertEqual(house["currency"], "EUR")
        self.assertEqual(house["living_m2"], 60)
        self.assertEqual(house["land_m2"], 1260)
        self.assertEqual(house["type"], "House")
        self.assertEqual(house["raw_fields"]["price_source"], "portal_eur")
        self.assertEqual(house["raw_fields"]["country"], "Bulgaria")
        self.assertTrue(house["thumb"].startswith("https://static.bulgarianproperties.com/"))

    def test_reserved_bargain(self):
        row = next(r for r in self.page["listings"] if r["external_id"] == "AD88772BG")
        self.assertEqual(row["price"], 10900)
        self.assertEqual(row["place"], "Dragomirovo")
        self.assertEqual(row["region"], "Veliko Tarnovo")
        self.assertEqual(row["living_m2"], 80)
        self.assertEqual(row["land_m2"], 1500)
        self.assertTrue(row["raw_fields"].get("reserved"))
        self.assertTrue(row["promoted"])

    def test_discounted_keeps_asking(self):
        row = next(r for r in self.page["listings"] if r["external_id"] == "AD83628BG")
        self.assertEqual(row["price"], 9900)
        self.assertEqual(row["raw_fields"]["old_price_eur"], 14000)
        self.assertEqual(row["place"], "Mihaltsi")

    def test_land_clears_living_area(self):
        row = next(r for r in self.page["listings"] if r["type"]
                   and "plot" in r["type"].lower())
        self.assertIsNone(row["living_m2"])
        self.assertIsNotNone(row["land_m2"])
        self.assertTrue(row["external_id"].startswith("AD"))


class DetailParser(unittest.TestCase):
    def test_house(self):
        d = parse_detail(_html("detail_house.html"), HOUSE_URL)
        self.assertEqual(d["external_id"], "AD91080BG")
        self.assertEqual(d["price"], 5900)
        self.assertEqual(d["currency"], "EUR")
        self.assertEqual(d["living_m2"], 60)
        self.assertEqual(d["land_m2"], 1260)
        self.assertNotEqual(d["living_m2"], d["land_m2"])
        self.assertEqual(d["place"], "Dolna Lipnitsa")
        self.assertEqual(d["region"], "Pavlikeni")
        self.assertEqual(d["type"], "House")
        self.assertEqual(d["reference"], "VT 91080")
        self.assertAlmostEqual(d["lat"], 43.314737810298)
        self.assertAlmostEqual(d["lon"], 25.462804164138)
        self.assertGreaterEqual(len(d["photos"] or []), 3)
        self.assertIn("Dolna Lipnitsa", d["description"] or "")
        self.assertEqual(d["raw_fields"]["price_gbp"], 5075)
        self.assertEqual(d["raw_fields"]["price_source"], "portal_eur")
        self.assertEqual(d["raw_fields"]["country"], "Bulgaria")
        self.assertIn("Condition:", d["features"] or "")

    def test_reserved_dual_currency(self):
        d = parse_detail(_html("detail_reserved.html"), RESERVED_URL)
        self.assertEqual(d["external_id"], "AD88772BG")
        self.assertEqual(d["price"], 10900)
        self.assertEqual(d["raw_fields"]["price_gbp"], 9375)
        self.assertEqual(d["raw_fields"]["price_usd"], 12512)
        self.assertEqual(d["living_m2"], 80)
        self.assertEqual(d["land_m2"], 1500)
        self.assertEqual(d["place"], "Dragomirovo")
        self.assertEqual(d["region"], "Veliko Tarnovo")
        self.assertEqual(d["reference"], "VT 88772")
        self.assertTrue(d["raw_fields"].get("reserved"))
        self.assertAlmostEqual(d["lat"], 43.514619005233)
        self.assertAlmostEqual(d["lon"], 25.257092241842)
        self.assertGreaterEqual(len(d["photos"] or []), 3)

    def test_live_ld_newlines_and_org_url_ignored(self):
        """Live Product JSON-LD has raw newlines; Organization url is the homepage."""
        html = """
        <html><head>
        <script type="application/ld+json">{
          "@type": "Product",
          "name": "House",
          "description": "line one
line two",
          "offers": {"price": 5900, "priceCurrency": "EUR",
            "url": "https://www.bulgarianproperties.com/Houses_in_Bulgaria/AD91080BG_x.html"}
        }</script>
        <script type="application/ld+json">{
          "@type": "Organization",
          "url": "https://www.bulgarianproperties.com/"
        }</script>
        </head><body>
        <div class="component-single-property-price"><span class="regular-price">€ 5 900</span></div>
        </body></html>
        """
        d = parse_detail(html, HOUSE_URL)
        self.assertEqual(d["external_id"], "AD91080BG")
        self.assertEqual(d["price"], 5900)
        self.assertIn("/AD91080BG", d["url"])
        self.assertNotEqual(d["url"].rstrip("/"), "https://www.bulgarianproperties.com")

    def test_gbp_only_ld_converts(self):
        html = """
        <html><head>
        <script type="application/ld+json">{"@type":"Product","name":"House",
        "offers":{"price":1000,"priceCurrency":"GBP",
        "url":"https://www.bulgarianproperties.com/Houses_in_Bulgaria/AD999001BG_x.html"}}</script>
        </head><body>
        <div class="component-single-property-characteristic">
          <div class="characteristic"><span class="label">Type of property</span>
          <span class="value">Regulated plot</span></div>
          <div class="characteristic"><span class="label">Area</span>
          <span class="value">2000 m<sup>2</sup></span></div>
        </div>
        </body></html>
        """
        d = parse_detail(
            html,
            "https://www.bulgarianproperties.com/Houses_in_Bulgaria/AD999001BG_x.html",
        )
        self.assertEqual(d["external_id"], "AD999001BG")
        self.assertEqual(d["price"], gbp_to_eur(1000))
        self.assertEqual(d["raw_fields"]["price_gbp"], 1000)
        self.assertEqual(d["raw_fields"]["price_source"], "gbp_converted")
        self.assertIsNone(d["living_m2"])
        self.assertEqual(d["land_m2"], 2000)
        self.assertTrue(d["raw_fields"].get("area_looks_like_plot"))


class Store(unittest.TestCase):
    def test_list_upsert_then_detail(self):
        page = parse_list(_html("list.html"), LIST_URL)
        with tempfile.TemporaryDirectory() as tmp:
            con = connect(Path(tmp) / "t.db")
            ts = now()
            lids = []
            for row in page["listings"]:
                if row["price"] is None or row["price"] > 15000:
                    continue
                status, lid = upsert_from_list(
                    con, row, "bp-bg-under-10k", ts, source="bulgarianproperties")
                self.assertEqual(status, "new")
                lids.append(lid)
            con.commit()
            n = con.execute(
                "SELECT COUNT(*) c FROM listings WHERE source=?",
                ("bulgarianproperties",),
            ).fetchone()["c"]
            self.assertGreaterEqual(n, 8)
            house = next(r for r in page["listings"] if r["external_id"] == "AD91080BG")
            _, hid = upsert_from_list(
                con, house, "bp-bg-under-10k", ts, source="bulgarianproperties")
            detail = parse_detail(_html("detail_house.html"), HOUSE_URL)
            update_from_detail(con, hid, detail, ts)
            row = con.execute(
                "SELECT source, external_id, price, currency, living_m2, land_m2, "
                "place, lat, lon "
                "FROM listings WHERE id=?", (hid,)
            ).fetchone()
            self.assertEqual(row["source"], "bulgarianproperties")
            self.assertEqual(row["external_id"], "AD91080BG")
            self.assertEqual(row["price"], 5900)
            self.assertEqual(row["currency"], "EUR")
            self.assertEqual(row["living_m2"], 60)
            self.assertEqual(row["land_m2"], 1260)
            self.assertEqual(row["place"], "Dolna Lipnitsa")
            self.assertAlmostEqual(row["lat"], 43.314737810298)
            _, fid = upsert_from_list(
                con, {"id": "91080", "place": "Dijon", "price": 90000},
                "france-150k", ts, source="franimo")
            self.assertNotEqual(fid, hid)
            _, oid = upsert_from_list(
                con, {"external_id": "91080", "place": "Varna", "price": 2700},
                "bg-houses-50k", ts, source="ok_bulgaria")
            self.assertNotEqual(oid, hid)


class Cli(unittest.TestCase):
    def test_help(self):
        from bulgarianproperties.scrape import main
        with self.assertRaises(SystemExit) as e:
            main(["--help"])
        self.assertEqual(e.exception.code, 0)

    def test_rejects_franimo_search(self):
        from bulgarianproperties import scrape
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
