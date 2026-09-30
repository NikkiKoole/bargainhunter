"""Freshness probe: counts, thresholds, and one page per seed. No live HTTP."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from core.probe import (
    format_report,
    is_drift,
    live_count,
    looks_blocked,
    main,
    probe_one,
    published_counts,
    register_adapters,
    resolve_names,
    seed_url,
)
from franimo.parse import parse_list as franimo_parse_list

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class Thresholds(unittest.TestCase):
    def test_absolute_or_percent(self):
        self.assertFalse(is_drift(10129, 10140))          # 11, under both
        self.assertTrue(is_drift(10129, 10496))           # 367 >= 50
        self.assertTrue(is_drift(20, 22))                 # 10% of a small list
        self.assertTrue(is_drift(20, 21, pct=5, min_delta=50))  # 5% of 20 is 1
        self.assertFalse(is_drift(1000, 1040, pct=5, min_delta=50))  # 40 < 50 and 4%
        self.assertTrue(is_drift(1000, 1050, pct=5, min_delta=50))
        self.assertFalse(is_drift(None, 100))
        self.assertFalse(is_drift(100, None))

    def test_percent_of_small_list_uses_one_listing(self):
        # 5% of 11 is 0.55, so a single listing is a drift.
        self.assertTrue(is_drift(11, 12, pct=5, min_delta=50))


class Counts(unittest.TestCase):
    def test_exact_total_wins(self):
        n, kind = live_count(
            {"total": 826, "total_pages": 23, "listings": [{}]}, "centrarium")
        self.assertEqual((n, kind), (826, "exact"))

    def test_result_count_alias(self):
        n, kind = live_count(
            {"result_count": 12, "total_pages": 4, "listings": [{}]}, "domaza")
        self.assertEqual((n, kind), (12, "exact"))

    def test_single_page_is_exact(self):
        n, kind = live_count(
            {"total_pages": 1, "listings": [{}, {}]}, "domaza")
        self.assertEqual((n, kind), (2, "exact"))

    def test_pages_without_headline_are_an_estimate(self):
        # Abruzzo Rural Property: 6 per page. (58 - 1) * 6 + 3 cards.
        n, kind = live_count(
            {"total_pages": 58, "listings": [{}, {}, {}]},
            "abruzzoruralproperty",
        )
        self.assertEqual((n, kind), (57 * 6 + 3, "estimate"))


class FranimoHeadline(unittest.TestCase):
    def test_total_amount_and_title(self):
        html = """
        <html><head><title>woning te koop, gevonden: 10496 | Franimo</title></head>
        <body>
          <span class="total-amount">10.496 </span> woningen gevonden
          <p class="current-page">1 van 714</p>
          <div class="box1" data-id="42" data-latitude="46.1" data-longitude="2.2">
            <a itemprop="url" href="/woning/42"></a>
            <meta itemprop="name" content="huis te koop Prezza"/>
          </div>
        </body></html>
        """
        page = franimo_parse_list(html, "https://www.franimo.nl/woning/?priceto=150000")
        self.assertEqual(page["total"], 10496)
        self.assertEqual(page["total_pages"], 714)
        self.assertEqual(len(page["listings"]), 1)


class FixtureTotals(unittest.TestCase):
    def test_portals_that_state_a_count(self):
        cases = [
            ("ok_bulgaria", "list.html",
             "https://www.cheap-bulgarian-house.co.uk/bulgaria_houses.php", 1303),
            ("akiyaportal", "list.html", "https://akiyaportal.com/listings?max_price=10000", 5552),
            ("holprop", "list.html", "https://www.holprop.com/sale/pt/villa-house/scr/bulgaria/price/100000/", 1670),
            ("holprop", "list_spain.html", "https://www.holprop.com/sale/pt/villa-house/scr/spain/price/100000/", 68),
            ("abruzzopropertyitaly", "list.html",
             "https://www.abruzzopropertyitaly.com/property-search", 155),
            ("centrarium", "list.html",
             "https://centrarium.com/en/montenegro/sale/houses/lowprice-montenegro/", 826),
            ("mubawab", "list.html", "https://www.mubawab.ma/en/sc/houses-for-sale", 375),
            ("homege", "list.html", "https://www.home.ge/en/saxlebi-agarakebi/search-results.html", 84),
            ("bulgarianproperties", "list.html",
             "https://www.bulgarianproperties.com/properties-in-bulgaria-under-ten-thousand-pounds.html", 37),
            ("lefigaro", "list.html",
             "https://immobilier.lefigaro.fr/annonces/immobilier-vente-maison-creuse.html", 1125),
            ("greenacres", "list.html",
             "https://www.green-acres.fr/onroerend-goed?searchQuery=lg-nl-cn-fr-hab_house-on-mx_p-150000",
             3435),
        ]
        for package, name, url, expect in cases:
            html = (FIXTURES / package / name).read_text(encoding="utf-8")
            mod = __import__(f"{package}.parse", fromlist=["parse_list"])
            page = mod.parse_list(html, url)
            self.assertEqual(page.get("total"), expect, package)
            self.assertIn("listings", page)
            self.assertIn("total_pages", page)

    def test_greenacres_api_count(self):
        raw = (FIXTURES / "greenacres" / "list_api.json").read_text(encoding="utf-8")
        from greenacres.parse import parse_list
        page = parse_list(raw, "https://www.green-acres.fr/nl/AdvertListingActions/AdvertsListing?p_n=1")
        self.assertEqual(page["total"], 4670)
        self.assertEqual(page["total_pages"], 195)


class ProbeBehavior(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        register_adapters()

    def test_enabled_only_unless_named(self):
        searches = {
            "on": {"source": "franimo", "path": "/a"},
            "off": {"source": "franimo", "path": "/b", "enabled": False},
        }
        self.assertEqual(resolve_names(searches, []), ["on"])
        self.assertEqual(resolve_names(searches, ["off"]), ["off"])
        with self.assertRaises(KeyError):
            resolve_names(searches, ["missing"])

    def test_greenacres_seed_is_the_unbanded_api(self):
        url = seed_url("greenacres", {
            "path": "/onroerend-goed?searchQuery=lg-nl-cn-fr-hab_house-on-mx_p-150000",
            "bands": [[0, 50000], [50001, 150000]],
        })
        self.assertIn("AdvertListingActions/AdvertsListing", url)
        self.assertIn("mx_p=150000", url)
        self.assertIn("p_n=1", url)
        self.assertNotIn("mn_p=0", url)

    def test_blocked_fetch_does_not_drift(self):
        class Boom:
            def get(self, url):
                raise RuntimeError("failed to fetch %s: 403 Client Error: Forbidden" % url)

        row = probe_one(
            "hp-es-houses-100k",
            {"source": "holprop", "path": "/sale/pt/villa-house/scr/spain/price/100000/"},
            Boom(),
            20,
        )
        self.assertEqual(row["note"], "blocked")
        self.assertFalse(row["drift"])
        self.assertIsNone(row["live"])

    def test_challenge_page_is_blocked(self):
        self.assertTrue(looks_blocked("<html>Sorry, you have been blocked</html>"))

        class Page:
            def get(self, url):
                return "<html><title>Just a moment...</title>cf-browser-verification</html>"

        row = probe_one(
            "lf-23-houses-150k",
            {"source": "lefigaro", "path": "/annonces/immobilier-vente-maison-creuse.html"},
            Page(),
            100,
        )
        self.assertEqual(row["note"], "blocked")
        self.assertFalse(row["drift"])

    def test_headline_versus_published(self):
        html = (FIXTURES / "centrarium" / "list.html").read_text(encoding="utf-8")

        class Page:
            def get(self, url):
                return html

        row = probe_one(
            "ct-me-houses-100k",
            {"source": "centrarium", "path": "/en/montenegro/sale/houses/lowprice-montenegro/",
             "skip": {"above": 100000, "priceless": True}},
            Page(),
            43,
        )
        self.assertEqual(row["live"], 826)
        self.assertEqual(row["delta"], 783)
        self.assertTrue(row["drift"])
        self.assertIn("wider than skip", row["note"])

    def test_report_marks_drift(self):
        rows = [{
            "search": "france-150k", "source": "franimo",
            "ours": 10129, "live": 10496, "delta": 367,
            "note": "exact drift", "drift": True, "detail": "",
        }]
        text = format_report(rows, quiet=False, pct=5, min_delta=50)
        self.assertIn("france-150k", text)
        self.assertIn("10,496", text)
        self.assertIn("+367", text)
        self.assertIn("1 drifted", text)
        quiet = format_report(
            rows + [{
                "search": "bg", "source": "ok_bulgaria",
                "ours": 1300, "live": 1302, "delta": 2,
                "note": "exact", "drift": False, "detail": "",
            }],
            quiet=True, pct=5, min_delta=50,
        )
        self.assertIn("france-150k", quiet)
        self.assertNotIn("ok_bulgaria", quiet)

    def test_published_counts_from_meta(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "meta.json"
            path.write_text(json.dumps({
                "searches": [{"search": "france-150k", "n": 10129}],
            }), encoding="utf-8")
            self.assertEqual(published_counts(path), {"france-150k": 10129})

    def test_help_exits_zero(self):
        with self.assertRaises(SystemExit) as caught:
            main(["--help"])
        self.assertEqual(caught.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
