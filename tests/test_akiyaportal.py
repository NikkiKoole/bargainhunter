"""Akiya Portal adapter: parsers, registry, searches, upserts. No live HTTP."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.adapter import ADAPTERS, get
from core.db import connect, now, upsert_from_list, update_from_detail
from core.searches import enabled_names, load_searches
from akiyaportal.fx import JPY_TO_EUR, USD_TO_EUR, jpy_to_eur, usd_to_eur
from akiyaportal.parse import parse_detail, parse_list, priced_row, with_page

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "akiyaportal"
LIST_URL = "https://akiyaportal.com/listings?max_price=10000"
HOUSE_URL = (
    "https://akiyaportal.com/listings/"
    "kagoshima-city-harayoshi-4-chome-single-story-building-3dk-dztm"
)
HOMES_URL = (
    "https://akiyaportal.com/listings/"
    "used-single-family-home-minakami-town-kohinata-used-house-xjhc"
)


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class Registry(unittest.TestCase):
    def test_registered(self):
        import akiyaportal.adapter  # noqa: F401
        self.assertIn("akiyaportal", ADAPTERS)
        adapter = get("akiyaportal")
        self.assertEqual(adapter.source, "akiyaportal")
        self.assertEqual(adapter.base, "https://akiyaportal.com")


class Searches(unittest.TestCase):
    def test_jp_entries(self):
        searches = load_searches()
        self.assertEqual(searches["jp-houses-10k"]["source"], "akiyaportal")
        self.assertTrue(searches["jp-houses-10k"].get("enabled", True))
        self.assertIn("max_price=10000", searches["jp-houses-10k"]["path"])
        self.assertFalse(searches["jp-houses-25k"].get("enabled", True))
        self.assertFalse(searches["jp-houses-50k"].get("enabled", True))
        self.assertFalse(searches["jp-akita"].get("enabled", True))
        jp = enabled_names(searches, source="akiyaportal")
        self.assertEqual(jp, ["jp-houses-10k"])
        franimo = enabled_names(searches, source="franimo")
        self.assertIn("france-150k", franimo)
        self.assertNotIn("jp-houses-10k", franimo)
        bg = enabled_names(searches, source="ok_bulgaria")
        self.assertEqual(bg, ["bg-houses-50k"])


class Pagination(unittest.TestCase):
    def test_with_page_keeps_filter(self):
        nxt = with_page(LIST_URL, 2)
        self.assertIn("page=2", nxt)
        self.assertIn("max_price=10000", nxt)
        page1 = with_page(nxt, 1)
        self.assertNotIn("page=", page1)
        self.assertIn("max_price=10000", page1)


class ListParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = parse_list(_html("list.html"), LIST_URL)

    def test_page_meta(self):
        self.assertEqual(self.page["total_pages"], 232)
        self.assertEqual(self.page["next_url"], LIST_URL + "&page=2")
        self.assertGreaterEqual(len(self.page["listings"]), 10)
        self.assertEqual(len(self.page["listings"]), 12)

    def test_stable_ids_and_urls(self):
        ids = [r["external_id"] for r in self.page["listings"]]
        self.assertEqual(len(ids), len(set(ids)))
        house = next(r for r in self.page["listings"] if r["external_id"] == "69105")
        self.assertEqual(house["source"], "akiyaportal")
        self.assertEqual(house["url"], HOUSE_URL)
        self.assertEqual(house["reference"], "69105")
        self.assertEqual(house["place"], "Kagoshima City")
        self.assertEqual(house["region"], "Kagoshima")
        self.assertEqual(house["price"], usd_to_eur(3208))
        self.assertEqual(house["currency"], "EUR")
        self.assertEqual(house["beds"], 3)
        self.assertEqual(house["living_m2"], 67)
        self.assertEqual(house["raw_fields"]["price_usd"], 3208)
        self.assertEqual(house["raw_fields"]["price_source"], "portal_usd")
        self.assertTrue(house["thumb"].startswith("https://"))

    def test_homes_card(self):
        row = next(r for r in self.page["listings"] if r["external_id"] == "69151")
        self.assertEqual(row["place"], "Minakami Town")
        self.assertEqual(row["region"], "Gunma")
        self.assertEqual(row["price"], usd_to_eur(6417))
        self.assertEqual(
            row["url"],
            HOMES_URL,
        )


class HubCards(unittest.TestCase):
    def test_prefecture_hub_markup(self):
        html = """
        <html><body>
        <a class="group bg-white rounded-lg border" href="/listings/used-single-family-home-kazuno-city-hanawa-used-house-ppyq">
          <p class="text-lg font-bold text-amber-700 mb-1">$641</p>
          <p class="text-sm text-gray-700 truncate mb-2">Used single-family home Kazuno City Hanawa Used house</p>
          <span class="truncate">Akita Prefecture Kazuno City Hanawa</span>
          <span class="px-2 py-0.5 bg-gray-100 rounded-full">4DK</span>
          <span class="px-2 py-0.5 bg-gray-100 rounded-full">119.75m²</span>
        </a>
        <nav aria-label="Pagination">
          <a href="/akiya-in-akita?page=2">2</a>
          <a href="/akiya-in-akita?page=50">50</a>
        </nav>
        </body></html>
        """
        page = parse_list(html, "https://akiyaportal.com/akiya-in-akita")
        self.assertEqual(len(page["listings"]), 1)
        row = page["listings"][0]
        self.assertEqual(row["external_id"],
                         "used-single-family-home-kazuno-city-hanawa-used-house-ppyq")
        self.assertEqual(row["price"], usd_to_eur(641))
        self.assertEqual(row["beds"], 4)
        self.assertEqual(row["living_m2"], 120)
        self.assertEqual(page["total_pages"], 50)
        self.assertIn("page=2", page["next_url"])


class DetailParser(unittest.TestCase):
    def test_house(self):
        d = parse_detail(_html("detail_house.html"), HOUSE_URL)
        self.assertEqual(d["external_id"], "69105")
        self.assertEqual(d["price"], usd_to_eur(3208))
        self.assertEqual(d["currency"], "EUR")
        self.assertEqual(d["living_m2"], 67)
        self.assertEqual(d["land_m2"], 177)
        self.assertEqual(d["bedrooms"], 3)
        self.assertEqual(d["place"], "Kagoshima City")
        self.assertEqual(d["region"], "Kagoshima")
        self.assertEqual(d["year_built"], 1968)
        self.assertEqual(d["agent"], "AkiyaPortal")
        self.assertEqual(d["agent_name"], "athome")
        self.assertGreaterEqual(len(d["photos"]), 3)
        self.assertIn("Harayoshi", d["description"])
        self.assertEqual(d["raw_fields"]["price_usd"], 3208)
        self.assertEqual(d["raw_fields"]["layout"], "3DK")

    def test_homes_glued_layout(self):
        d = parse_detail(_html("detail_homes.html"), HOMES_URL)
        self.assertEqual(d["external_id"], "69151")
        self.assertEqual(d["type"], "Used single-family home")
        self.assertEqual(d["bedrooms"], 5)
        self.assertEqual(d["land_m2"], 113)
        self.assertIsNone(d["living_m2"])
        self.assertEqual(d["price"], usd_to_eur(6417))
        self.assertEqual(d["place"], "Minakami Town")
        self.assertEqual(d["region"], "Gunma")
        self.assertGreaterEqual(len(d["photos"]), 1)
        self.assertEqual(d["raw_fields"]["layout"], "5DK")


class Currency(unittest.TestCase):
    def test_usd_converts(self):
        row = priced_row(3208, None)
        self.assertEqual(row["price"], usd_to_eur(3208))
        self.assertEqual(row["currency"], "EUR")
        self.assertEqual(row["raw_fields"]["price_source"], "portal_usd")
        self.assertEqual(row["raw_fields"]["price_usd"], 3208)
        self.assertEqual(row["raw_fields"]["fx_rate"], USD_TO_EUR)

    def test_usd_wins_over_jpy(self):
        row = priced_row(3208, 500000)
        self.assertEqual(row["raw_fields"]["price_source"], "portal_usd")
        self.assertEqual(row["price"], usd_to_eur(3208))
        self.assertEqual(row["raw_fields"]["price_jpy"], 500000)

    def test_jpy_only_converts(self):
        row = priced_row(None, 500000)
        self.assertEqual(row["price"], jpy_to_eur(500000))
        self.assertEqual(row["currency"], "EUR")
        self.assertEqual(row["raw_fields"]["price_source"], "jpy_converted")
        self.assertEqual(row["raw_fields"]["fx_rate"], JPY_TO_EUR)


class Store(unittest.TestCase):
    def test_list_upsert_then_detail(self):
        page = parse_list(_html("list.html"), LIST_URL)
        with tempfile.TemporaryDirectory() as tmp:
            con = connect(Path(tmp) / "t.db")
            ts = now()
            lids = []
            for row in page["listings"]:
                status, lid = upsert_from_list(con, row, "jp-houses-10k", ts,
                                              source="akiyaportal")
                self.assertEqual(status, "new")
                lids.append(lid)
            con.commit()
            n = con.execute("SELECT COUNT(*) c FROM listings WHERE source=?",
                            ("akiyaportal",)).fetchone()["c"]
            self.assertEqual(n, 12)
            house = next(r for r in page["listings"] if r["external_id"] == "69105")
            _, hid = upsert_from_list(con, house, "jp-houses-10k", ts, source="akiyaportal")
            detail = parse_detail(_html("detail_house.html"), HOUSE_URL)
            update_from_detail(con, hid, detail, ts)
            row = con.execute(
                "SELECT source, external_id, price, currency, living_m2, land_m2, bedrooms "
                "FROM listings WHERE id=?", (hid,)
            ).fetchone()
            self.assertEqual(row["source"], "akiyaportal")
            self.assertEqual(row["external_id"], "69105")
            self.assertEqual(row["price"], usd_to_eur(3208))
            self.assertEqual(row["currency"], "EUR")
            self.assertEqual(row["living_m2"], 67)
            self.assertEqual(row["land_m2"], 177)
            self.assertEqual(row["bedrooms"], 3)
            _, fid = upsert_from_list(con, {"id": 69105, "place": "Dijon", "price": 90000},
                                      "france-150k", ts, source="franimo")
            self.assertNotEqual(fid, hid)


class Cli(unittest.TestCase):
    def test_help(self):
        from akiyaportal.scrape import main
        with self.assertRaises(SystemExit) as e:
            main(["--help"])
        self.assertEqual(e.exception.code, 0)

    def test_rejects_franimo_search(self):
        from akiyaportal import scrape
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


class Description(unittest.TestCase):
    """The real write-up is behind Akiya Portal's paid trial, so what we get is
    a location line plus a sales pitch. The pitch is identical across ~5,570
    listings and buried free-text search until it was stripped."""

    def test_pitch_is_stripped(self):
        from akiyaportal.parse import _clean_description as clean
        got = clean("$63 property for sale in Amada, Inashiki City, Japan. "
                    "Start a free trial for unlimited English property details.")
        self.assertEqual(got, "$63 property for sale in Amada, Inashiki City, Japan.")

    def test_pitch_only_becomes_nothing(self):
        from akiyaportal.parse import _clean_description as clean
        self.assertIsNone(clean("Start a free trial for unlimited English property details."))

    def test_ordinary_text_is_untouched(self):
        from akiyaportal.parse import _clean_description as clean
        self.assertEqual(clean("Daisen City, Kawaiwa, 2-story, 7K"),
                         "Daisen City, Kawaiwa, 2-story, 7K")
        self.assertIsNone(clean(None))
        self.assertIsNone(clean(""))
