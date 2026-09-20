"""Green-Acres adapter: parsers, registry, searches, upserts.

Fixtures reconstruct live 2026-09-20 HTML cards + AdvertsListing JSON
(NL `/onroerend-goed`, `data-advertid` / base64 `data-o`, dollar paint
on a datacenter IP, euro on the listing API and detail `Prijs in euros`).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.adapter import ADAPTERS, get
from core.db import connect, now, upsert_from_list, update_from_detail
from core.searches import enabled_names, load_searches
from greenacres.fx import USD_TO_EUR, usd_to_eur
from greenacres.parse import (
    decode_data_o,
    is_blocked,
    listing_api_url,
    parse_detail,
    parse_list,
    parse_money,
    with_page,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "greenacres"
LIST_URL = (
    "https://www.green-acres.fr/onroerend-goed"
    "?searchQuery=lg-nl-cn-fr-hab_house-on-mx_p-150000"
)
API_URL = (
    "https://www.green-acres.fr/nl/AdvertListingActions/AdvertsListing"
    "?advertType=Property&p_n=2&order=price_i&hab_house=true&mx_p=150000"
    "&cn=fr&lg=nl"
)
HOUSE_URL = (
    "https://www.green-acres.fr/nl/properties/makelaar/"
    "la-souterraine/At19xxy8r4yirjpy.htm"
)
GARAGE_URL = (
    "https://www.green-acres.fr/nl/properties/makelaar/"
    "montpellier/A5740b8gsr8ktxc6.htm"
)


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class Registry(unittest.TestCase):
    def test_registered(self):
        import greenacres.adapter  # noqa: F401
        self.assertIn("greenacres", ADAPTERS)
        adapter = get("greenacres")
        self.assertEqual(adapter.source, "greenacres")
        self.assertEqual(adapter.base, "https://www.green-acres.fr")


class Searches(unittest.TestCase):
    def test_ga_entries(self):
        searches = load_searches()
        self.assertEqual(searches["ga-fr-houses-150k"]["source"], "greenacres")
        self.assertTrue(searches["ga-fr-houses-150k"].get("enabled", True))
        path = searches["ga-fr-houses-150k"]["path"]
        self.assertIn("/onroerend-goed", path)
        self.assertIn("hab_house-on", path)
        self.assertIn("mx_p-150000", path)
        self.assertNotIn("prc_max", path)
        self.assertEqual(searches["ga-fr-houses-150k"]["skip"]["above"], 150000)
        self.assertFalse(searches["ga-fr-houses"].get("enabled", True))
        self.assertIn("hab_house-on", searches["ga-fr-houses"]["path"])
        self.assertNotIn("mx_p", searches["ga-fr-houses"]["path"])
        self.assertFalse(searches["ga-23-houses-150k"].get("enabled", True))
        self.assertIn("creuse", searches["ga-23-houses-150k"]["path"])
        ga = enabled_names(searches, source="greenacres")
        self.assertEqual(ga, ["ga-fr-houses-150k"])
        franimo = enabled_names(searches, source="franimo")
        self.assertIn("france-150k", franimo)
        self.assertNotIn("ga-fr-houses-150k", franimo)
        lf = enabled_names(searches, source="lefigaro")
        self.assertEqual(lf, ["lf-23-houses-150k"])


class Pagination(unittest.TestCase):
    def test_html_page_1_stays_on_seo_path(self):
        page1 = with_page(LIST_URL, 1)
        self.assertIn("/onroerend-goed", page1)
        self.assertNotIn("AdvertListingActions", page1)
        self.assertIn("mx_p-150000", page1)

    def test_page_2_is_adverts_listing(self):
        nxt = with_page(LIST_URL, 2)
        self.assertIn("/AdvertListingActions/AdvertsListing", nxt)
        self.assertIn("p_n=2", nxt)
        self.assertIn("order=price_i", nxt)
        self.assertIn("hab_house=true", nxt)
        self.assertIn("mx_p=150000", nxt)
        self.assertNotIn("currency=", nxt)
        self.assertNotIn("prc_max", nxt)

    def test_api_url_from_creuse_path(self):
        url = listing_api_url(
            "https://www.green-acres.fr/onroerend-goed/creuse", page=3)
        self.assertIn("p_n=3", url)
        self.assertIn("city_id=dp_23", url)


class Money(unittest.TestCase):
    def test_euro_and_dollar(self):
        self.assertEqual(parse_money("93.500 €"), (93500, "EUR"))
        self.assertEqual(parse_money("18.000 &#x20AC;"), (18000, "EUR"))
        self.assertEqual(parse_money("107.417 $"), (107417, "USD"))
        self.assertEqual(parse_money("$ 56.029"), (56029, "USD"))

    def test_usd_to_eur_documented_rate(self):
        self.assertEqual(USD_TO_EUR, 0.87)
        self.assertEqual(usd_to_eur(107417), 93453)

    def test_cloudflare_block(self):
        self.assertTrue(is_blocked("<html>Sorry, you have been blocked</html>"))
        self.assertFalse(is_blocked(_html("list.html")))
        blocked = parse_list("<html>Sorry, you have been blocked</html>", LIST_URL)
        self.assertEqual(blocked["listings"], [])
        self.assertTrue(blocked.get("blocked"))

    def test_data_o_decodes_detail_url(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(_html("list.html"), "lxml")
        card = soup.select_one('[data-advertid="At19xxy8r4yirjpy"]')
        self.assertIsNotNone(card)
        url = decode_data_o(card.get("data-o"))
        self.assertEqual(url, HOUSE_URL)
        self.assertIsNone(decode_data_o("L25sL1NpZ25Jbg=="))  # /nl/SignIn


class ListParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = parse_list(_html("list.html"), LIST_URL)

    def test_page_meta(self):
        self.assertEqual(self.page["total_pages"], 144)
        self.assertIn("AdvertListingActions/AdvertsListing", self.page["next_url"])
        self.assertIn("p_n=2", self.page["next_url"])
        self.assertGreaterEqual(len(self.page["listings"]), 12)

    def test_unique_advert_ids(self):
        ids = [r["external_id"] for r in self.page["listings"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(i[0].isalpha() for i in ids))
        urls = [r["url"] for r in self.page["listings"]]
        self.assertTrue(all("/nl/properties/" in u for u in urls))
        self.assertFalse(any("/SignIn" in u for u in urls))

    def test_souterraine_house(self):
        house = next(r for r in self.page["listings"]
                     if r["external_id"] == "At19xxy8r4yirjpy")
        self.assertEqual(house["source"], "greenacres")
        self.assertEqual(house["url"], HOUSE_URL)
        self.assertEqual(house["place"], "La Souterraine")
        self.assertEqual(house["region"], "Creuse")
        self.assertEqual(house["dept_fr"], "Creuse")
        self.assertEqual(house["dept_nl"], "Creuse")
        self.assertEqual(house["price"], 93453)
        self.assertEqual(house["currency"], "EUR")
        self.assertEqual(house["raw_fields"]["price_original"], 107417)
        self.assertEqual(house["raw_fields"]["currency_original"], "USD")
        self.assertEqual(house["living_m2"], 77)
        self.assertEqual(house["land_m2"], 1220)
        self.assertEqual(house["rooms"], 3)
        self.assertEqual(house["type"], "Huis")
        self.assertEqual(house["raw_fields"]["country"], "France")
        self.assertTrue(house["thumb"].startswith("https://lb1.green-acres.com/"))

    def test_luxury_cards_over_cap(self):
        dear = next(r for r in self.page["listings"]
                    if r["external_id"] == "Afak69u9elwrqhfs")
        self.assertGreater(dear["price"], 150000)
        self.assertEqual(dear["place"], "Roquebrune-Cap-Martin")


class ApiListParser(unittest.TestCase):
    def test_json_unwrap_cheap_first(self):
        raw = _html("list_api.json")
        self.assertTrue(raw.lstrip().startswith("{"))
        page = parse_list(raw, API_URL)
        self.assertEqual(page["total_pages"], 195)
        self.assertIn("p_n=3", page["next_url"] or "")
        self.assertGreaterEqual(len(page["listings"]), 10)
        prices = [r["price"] for r in page["listings"] if r.get("price")]
        self.assertTrue(prices)
        self.assertLessEqual(min(prices), 20000)
        self.assertTrue(all(r["currency"] == "EUR" for r in page["listings"]))
        first = next(r for r in page["listings"]
                     if r["external_id"] == "A5740b8gsr8ktxc6")
        self.assertEqual(first["price"], 18000)
        self.assertEqual(first["place"], "Montpellier")


class DetailParser(unittest.TestCase):
    def test_house(self):
        d = parse_detail(_html("detail_house.html"), HOUSE_URL)
        self.assertEqual(d["external_id"], "At19xxy8r4yirjpy")
        self.assertEqual(d["source"], "greenacres")
        self.assertEqual(d["price"], 93500)
        self.assertEqual(d["currency"], "EUR")
        self.assertEqual(d["living_m2"], 77)
        self.assertEqual(d["land_m2"], 1220)
        self.assertEqual(d["rooms"], 3)
        self.assertEqual(d["bedrooms"], 2)
        self.assertEqual(d["baths"], 1)
        self.assertEqual(d["place"], "La Souterraine")
        self.assertEqual(d["region"], "Creuse")
        self.assertEqual(d["dept_fr"], "Creuse")
        self.assertEqual(d["energy_label"], "D")
        self.assertEqual(d["energy_kwh"], 196)
        self.assertEqual(d["gas_co2"], 7)
        self.assertEqual(d["agent"], "Lydie Rosier")
        self.assertEqual(d["reference"], "2026100-953")
        self.assertEqual(d["raw_fields"]["country"], "France")
        self.assertIn("1220", d["description"] or "")
        self.assertGreaterEqual(len(d["photos"] or []), 2)
        self.assertAlmostEqual(d["lat"], 46.23796, places=4)
        self.assertAlmostEqual(d["lon"], 1.48380, places=4)

    def test_garage_on_house_filter(self):
        d = parse_detail(_html("detail_garage.html"), GARAGE_URL)
        self.assertEqual(d["external_id"], "A5740b8gsr8ktxc6")
        self.assertEqual(d["place"], "Montpellier")
        self.assertEqual(d["region"], "Hérault")
        self.assertEqual(d["price"], 18000)
        self.assertEqual(d["living_m2"], 14)
        self.assertEqual(d["type"], "Garage")


class Store(unittest.TestCase):
    def test_list_upsert_then_detail(self):
        page = parse_list(_html("list.html"), LIST_URL)
        with tempfile.TemporaryDirectory() as tmp:
            con = connect(Path(tmp) / "t.db")
            ts = now()
            lids = []
            for row in page["listings"]:
                if row["price"] is None or row["price"] > 150000:
                    continue
                status, lid = upsert_from_list(
                    con, row, "ga-fr-houses-150k", ts, source="greenacres")
                self.assertEqual(status, "new")
                lids.append(lid)
            con.commit()
            n = con.execute(
                "SELECT COUNT(*) c FROM listings WHERE source=?",
                ("greenacres",),
            ).fetchone()["c"]
            self.assertGreaterEqual(n, 10)
            house = next(r for r in page["listings"]
                         if r["external_id"] == "At19xxy8r4yirjpy")
            _, hid = upsert_from_list(
                con, house, "ga-fr-houses-150k", ts, source="greenacres")
            detail = parse_detail(_html("detail_house.html"), HOUSE_URL)
            update_from_detail(con, hid, detail, ts)
            row = con.execute(
                "SELECT source, external_id, price, currency, living_m2, land_m2, "
                "bedrooms, place, lat, lon "
                "FROM listings WHERE id=?", (hid,)
            ).fetchone()
            self.assertEqual(row["source"], "greenacres")
            self.assertEqual(row["external_id"], "At19xxy8r4yirjpy")
            self.assertEqual(row["price"], 93500)
            self.assertEqual(row["currency"], "EUR")
            self.assertEqual(row["living_m2"], 77)
            self.assertEqual(row["land_m2"], 1220)
            self.assertEqual(row["bedrooms"], 2)
            self.assertEqual(row["place"], "La Souterraine")
            self.assertAlmostEqual(row["lat"], 46.23796, places=4)
            _, fid = upsert_from_list(
                con, {"id": "At19xxy8r4yirjpy", "place": "Dijon", "price": 90000},
                "france-150k", ts, source="franimo")
            self.assertNotEqual(fid, hid)


class Cli(unittest.TestCase):
    def test_help(self):
        from greenacres.scrape import main
        with self.assertRaises(SystemExit) as e:
            main(["--help"])
        self.assertEqual(e.exception.code, 0)

    def test_rejects_franimo_search(self):
        from greenacres import scrape
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
