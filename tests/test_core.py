"""Phase 0: shared core stays importable and multi-source-ready."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.db import connect, now, update_from_detail, upsert_from_list
from core.export import pack
from core.http import Fetcher, cache_path, decode
from core.listing import Listing, identity
from core.searches import DEFAULT_SOURCE, enabled_names, load_searches


class ScrapeCli(unittest.TestCase):
    def test_rejects_non_franimo_search(self):
        from franimo.scrape import main
        with tempfile.TemporaryDirectory() as tmp:
            # load_searches reads the real file; just assert the CLI errors
            with self.assertRaises(SystemExit) as e:
                main(["--help"])
            self.assertEqual(e.exception.code, 0)
            # unknown name
            with self.assertRaises(SystemExit):
                main(["definitely-not-a-search"])

    def test_rejects_other_source(self):
        from franimo import scrape
        orig = scrape.load_searches
        scrape.load_searches = lambda: {
            "bg": {"source": "ok_bulgaria", "label": "x", "path": "/"},
        }
        try:
            with self.assertRaises(SystemExit):
                scrape.main(["bg"])
        finally:
            scrape.load_searches = orig


class Imports(unittest.TestCase):
    def test_franimo_modules_import(self):
        import franimo.compact  # noqa: F401
        import franimo.db
        import franimo.export
        import franimo.http
        import franimo.newsearch  # noqa: F401
        import franimo.parse
        import franimo.prune  # noqa: F401
        import franimo.scrape
        import franimo.serve
        self.assertEqual(franimo.http.BASE, "https://www.franimo.nl")
        self.assertTrue(callable(franimo.scrape.main))
        self.assertTrue(callable(franimo.export.main))
        self.assertTrue(callable(franimo.serve.main))
        self.assertIs(franimo.db.connect, connect)


class Searches(unittest.TestCase):
    def test_france_150k_is_franimo(self):
        searches = load_searches()
        self.assertIn("france-150k", searches)
        self.assertEqual(searches["france-150k"]["source"], "franimo")
        self.assertIn("bands", searches["france-150k"])
        self.assertTrue(searches["france-150k"].get("enabled", True))
        enabled = enabled_names(searches, source="franimo")
        self.assertIn("france-150k", enabled)
        self.assertIn("boerderijen-oost", enabled)
        self.assertNotIn("breed-oost", enabled)  # parked

    def test_omitted_source_defaults_to_franimo(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "searches.json"
            path.write_text(json.dumps({
                "legacy": {"label": "old", "path": "/woning/?x=1"},
                "other": {"source": "ok_bulgaria", "label": "bg", "path": "/x"},
            }), encoding="utf-8")
            data = load_searches(path)
            self.assertEqual(data["legacy"]["source"], DEFAULT_SOURCE)
            self.assertEqual(data["other"]["source"], "ok_bulgaria")
            self.assertEqual(enabled_names(data, source="franimo"), ["legacy"])


class Store(unittest.TestCase):
    def test_two_sources_same_external_id_do_not_collide(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = connect(Path(tmp) / "t.db")
            ts = now()
            st, a = upsert_from_list(con, {
                "id": 42, "url": "https://www.franimo.nl/x/42",
                "place": "Dijon", "price": 90000, "type": "huis",
            }, "france-150k", ts, source="franimo")
            st2, b = upsert_from_list(con, {
                "external_id": "42", "url": "https://example.com/42",
                "place": "Sofia", "price": 40000, "type": "къща",
                "currency": "EUR",
            }, "bulgaria-cheap", ts, source="ok_bulgaria")
            self.assertEqual(st, "new")
            self.assertEqual(st2, "new")
            self.assertNotEqual(a, b)
            rows = list(con.execute(
                "SELECT source, external_id, place, id FROM listings ORDER BY source"))
            self.assertEqual(len(rows), 2)
            self.assertEqual({r["source"] for r in rows}, {"franimo", "ok_bulgaria"})
            self.assertEqual({r["external_id"] for r in rows}, {"42"})

            # re-upsert franimo 42 updates the same internal id
            st3, a2 = upsert_from_list(con, {
                "id": 42, "url": "https://www.franimo.nl/x/42",
                "place": "Dijon", "price": 85000, "type": "huis",
            }, "france-150k", now(), source="franimo")
            self.assertEqual(a2, a)
            self.assertEqual(st3, "price_drop")
            n = con.execute("SELECT COUNT(*) c FROM listings").fetchone()["c"]
            self.assertEqual(n, 2)

    def test_legacy_db_backfills_source(self):
        """A pre-Phase-0 schema still opens and gets source/external_id."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            import sqlite3
            raw = sqlite3.connect(path)
            raw.executescript("""
                CREATE TABLE listings (
                    id INTEGER PRIMARY KEY,
                    url TEXT, place TEXT, price INTEGER,
                    first_seen TEXT, last_seen TEXT, gone_at TEXT
                );
                INSERT INTO listings (id, url, place, price)
                VALUES (99, 'https://www.franimo.nl/x/99', 'Lyon', 120000);
            """)
            raw.commit()
            raw.close()
            con = connect(path)
            row = con.execute("SELECT * FROM listings WHERE id=99").fetchone()
            self.assertEqual(row["source"], "franimo")
            self.assertEqual(row["external_id"], "99")
            self.assertEqual(row["currency"], "EUR")


class ClearLivingM2(unittest.TestCase):
    """A detail page that doesn't mention living area must not erase the value
    the list card already gave us.

    Regression: CLEARABLE was global, so every adapter cleared living_m2 when a
    detail page lacked it. akiyaportal's list cards carry m2 for 99% of rows;
    after its first 200 detail fetches only 2.5% still had it.
    """

    def _row(self, con, source, living):
        ts = now()
        _, lid = upsert_from_list(
            con,
            {"external_id": "1", "url": f"https://x/{source}", "price": 1000,
             "living_m2": living},
            "s", ts, source=source)
        return lid, ts

    def test_other_source_keeps_card_living_m2(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = connect(Path(tmp) / "t.db")
            lid, ts = self._row(con, "akiyaportal", 80)
            update_from_detail(con, lid, {"raw_fields": {"anything": "yes"}}, ts)
            kept = con.execute("SELECT living_m2 FROM listings WHERE id=?",
                               (lid,)).fetchone()["living_m2"]
            self.assertEqual(kept, 80)

    def test_franimo_still_clears(self):
        # franimo's table always states living area, so a missing value there
        # really does mean "no living space" (a land parcel).
        with tempfile.TemporaryDirectory() as tmp:
            con = connect(Path(tmp) / "t.db")
            lid, ts = self._row(con, "franimo", 80)
            update_from_detail(con, lid, {"raw_fields": {"terrein": "900 m2"}}, ts)
            kept = con.execute("SELECT living_m2 FROM listings WHERE id=?",
                               (lid,)).fetchone()["living_m2"]
            self.assertIsNone(kept)

    def test_detail_value_still_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = connect(Path(tmp) / "t.db")
            lid, ts = self._row(con, "akiyaportal", 80)
            update_from_detail(con, lid,
                               {"raw_fields": {"a": "b"}, "living_m2": 120}, ts)
            kept = con.execute("SELECT living_m2 FROM listings WHERE id=?",
                               (lid,)).fetchone()["living_m2"]
            self.assertEqual(kept, 120)


class Decode(unittest.TestCase):
    """Small portals serve UTF-8 while declaring a legacy codepage."""

    class _Resp:
        def __init__(self, content, encoding, apparent="windows-1251"):
            self.content, self.encoding, self.apparent_encoding = content, encoding, apparent

    def test_utf8_body_wins_over_wrong_declared_charset(self):
        r = self._Resp("Forest \u2013 70 km".encode("utf-8"), "windows-1251")
        self.assertEqual(decode(r), "Forest \u2013 70 km")

    def test_real_legacy_bytes_still_decode(self):
        # Cyrillic in cp1251 is not valid UTF-8, so the declared charset is used.
        word = "\u0411\u0443\u0440\u0433\u0430\u0441"          # Burgas
        r = self._Resp(word.encode("windows-1251"), "windows-1251")
        self.assertEqual(decode(r), word)

    def test_missing_charset_falls_back(self):
        r = self._Resp("plain".encode("utf-8"), None, None)
        self.assertEqual(decode(r), "plain")

    def test_a_few_stray_bytes_do_not_condemn_a_utf8_page(self):
        # Real case: cheap-bulgarian-house.co.uk is UTF-8 apart from ~14 bytes
        # inside a JavaScript string. Falling back to the declared cp1251 there
        # mangled every en-dash in the page.
        body = ("Forest \u2013 70 km from Sofia. " + "x" * 4000).encode("utf-8") + b"\x88"
        r = self._Resp(body, "windows-1251")
        out = decode(r)
        self.assertIn("\u2013", out)
        self.assertNotIn("\u0432\u0402", out)          # the "вЂ" mojibake

    def test_a_genuinely_legacy_page_still_falls_back(self):
        word = "\u0411\u0443\u0440\u0433\u0430\u0441 \u043a\u0440\u0430\u0439 \u043c\u043e\u0440\u0435\u0442\u043e"
        r = self._Resp(word.encode("windows-1251"), "windows-1251")
        self.assertEqual(decode(r), word)


class Pack(unittest.TestCase):
    def test_pack_keeps_source_and_leaves_foreign_urls(self):
        packed = pack([{
            "id": 1, "source": "ok_bulgaria", "external_id": "42",
            "url": "https://example.com/listing/42",
            "type": "house", "place": "Sofia", "price": 40000,
            "currency": "EUR", "snippet": "drop me",
        }, {
            "id": 2, "source": "franimo", "external_id": "7",
            "url": "https://www.franimo.nl/woning/7",
            "type": "huis", "place": "Dijon", "price": 90000,
            "currency": "EUR",
        }])
        self.assertIn("source", packed["cols"])
        self.assertIn("currency", packed["cols"])
        self.assertNotIn("snippet", packed["cols"])
        src = packed["cols"].index("source")
        url = packed["cols"].index("url")
        row0 = packed["rows"][0]
        self.assertEqual(row0[src], "ok_bulgaria")
        self.assertEqual(row0[url], "https://example.com/listing/42")
        row1 = packed["rows"][1]
        self.assertEqual(row1[src], "franimo")
        self.assertEqual(row1[url], "/woning/7")


class ListingModel(unittest.TestCase):
    def test_identity(self):
        self.assertEqual(identity({"id": 5}), ("franimo", "5"))
        self.assertEqual(
            identity(Listing(source="ok_bulgaria", external_id="ab")),
            ("ok_bulgaria", "ab"),
        )


class CacheKey(unittest.TestCase):
    def test_same_hash_as_full_url(self):
        import hashlib
        url = "https://www.franimo.nl/woning/?pricefrom=0"
        path = cache_path(Path("/tmp"), url)
        expect = hashlib.sha1(url.encode()).hexdigest() + ".html.gz"
        self.assertEqual(path.name, expect)
        f = Fetcher(cache_dir=Path("/tmp/no-such-cache-for-test"), base="https://www.franimo.nl")
        self.assertEqual(f.resolve("/woning/1"), "https://www.franimo.nl/woning/1")
        self.assertEqual(f.resolve(url), url)


class Export(unittest.TestCase):
    def test_export_writes_root_files(self):
        from core.export import main as export_main
        from core.serve import listings, meta

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "t.db"
            out = Path(tmp) / "site"
            con = connect(db_path)
            upsert_from_list(con, {
                "id": 1, "url": "https://www.franimo.nl/w/1",
                "place": "Dijon", "price": 80000, "type": "huis",
                "thumb": "https://cdn.example/a/b.jpg",
            }, "france-150k", now(), source="franimo")
            con.commit()
            self.assertEqual(len(listings(con)), 1)
            self.assertEqual(meta(con)["sources"][0]["source"], "franimo")
            self.assertEqual(export_main(["--db", str(db_path), "--out", str(out),
                                          "--no-photos"]), 0)
            self.assertTrue((out / "index.html").is_file())
            self.assertTrue((out / "app.js").is_file())
            self.assertTrue((out / "data" / "listings.json").is_file())
            self.assertTrue((out / "data" / "meta.json").is_file())
            html = (out / "index.html").read_text(encoding="utf-8")
            self.assertIn("FRANIMO_STATIC", html)
            self.assertIn("facet-source", html)
            packed = json.loads((out / "data" / "listings.json").read_text())
            self.assertIn("source", packed["cols"])
            js = (out / "app.js").read_text(encoding="utf-8")
            self.assertIn("prettySource", js)
            self.assertIn("OK Bulgaria", js)


class SourceLabels(unittest.TestCase):
    """Friendly portal names live in the UI, not the scrape path."""

    NAMES = {
        "franimo": "Franimo",
        "ok_bulgaria": "OK Bulgaria",
        "akiyaportal": "Akiya Portal",
        "holprop": "Holprop",
        "abruzzopropertyitaly": "Abruzzo Property Italy",
        "abruzzoruralproperty": "Abruzzo Rural",
        "centrarium": "Centrarium",
        "mubawab": "Mubawab",
        "homege": "home.ge",
        "bulgarianproperties": "Bulgarian Properties",
        "domaza": "Domaza",
        "lefigaro": "Le Figaro",
        "greenacres": "Green-Acres",
    }

    def test_app_js_maps_known_sources(self):
        from core.paths import WEB
        js = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("if (!r.source) r.source = 'franimo'", js)
        self.assertIn("function prettySource", js)
        for key, label in self.NAMES.items():
            self.assertIn(f"{key}: '{label}'", js)

    def test_prettify_fallback_matches_ui(self):
        import re
        def pretty(key):
            if key in self.NAMES:
                return self.NAMES[key]
            return re.sub(r"[_-]+", " ", key).title()
        self.assertEqual(pretty("franimo"), "Franimo")
        self.assertEqual(pretty("ok_bulgaria"), "OK Bulgaria")
        self.assertEqual(pretty("homege"), "home.ge")
        self.assertEqual(pretty("new_portal_x"), "New Portal X")
        self.assertEqual(pretty("foo-bar"), "Foo Bar")


if __name__ == "__main__":
    unittest.main()


class MarkGone(unittest.TestCase):
    """gone_at has to be trustworthy: a truncated crawl looks exactly like a
    sold-out portal, and calling a live listing sold is the worse error."""

    # now() is second-resolution, so tests must supply distinct timestamps or
    # "seen this run" and "seen last run" collapse into one.
    def _seed(self, con, n, search="s", source="franimo", ts="2026-09-01T00:00:00+00:00"):
        for i in range(n):
            upsert_from_list(con, {"external_id": str(i), "url": f"u{i}", "price": 1000},
                             search, ts, source=source)
        return ts

    def _see_again(self, con, ids, search="s", source="franimo",
                   ts="2026-09-20T00:00:00+00:00"):
        for i in ids:
            upsert_from_list(con, {"external_id": str(i), "url": f"u{i}", "price": 1000},
                             search, ts, source=source)
        return ts

    def test_truncated_run_marks_nothing(self):
        from core.db import mark_gone
        with tempfile.TemporaryDirectory() as tmp:
            con = connect(Path(tmp) / "t.db")
            self._seed(con, 20)
            ts = self._see_again(con, range(9))        # crawl stopped after page 1
            self.assertEqual(mark_gone(con, "s", ts), 0)
            still = con.execute("SELECT COUNT(*) c FROM listings WHERE gone_at IS NOT NULL")
            self.assertEqual(still.fetchone()["c"], 0)

    def test_run_that_found_nothing_marks_nothing(self):
        from core.db import mark_gone
        with tempfile.TemporaryDirectory() as tmp:
            con = connect(Path(tmp) / "t.db")
            self._seed(con, 20)
            self.assertEqual(mark_gone(con, "s", "2026-09-20T00:00:00+00:00"), 0)

    def test_ordinary_churn_is_still_detected(self):
        from core.db import mark_gone
        with tempfile.TemporaryDirectory() as tmp:
            con = connect(Path(tmp) / "t.db")
            self._seed(con, 1000)
            ts = self._see_again(con, range(970))      # 30 sold over two weeks
            self.assertEqual(mark_gone(con, "s", ts), 30)

    def test_a_few_gone_from_a_tiny_search_is_detected(self):
        from core.db import mark_gone
        with tempfile.TemporaryDirectory() as tmp:
            con = connect(Path(tmp) / "t.db")
            self._seed(con, 8)
            ts = self._see_again(con, range(5))        # 3 of 8, below SMALL_ATTRITION
            self.assertEqual(mark_gone(con, "s", ts), 3)

    def test_force_overrides_the_guard(self):
        from core.db import mark_gone
        with tempfile.TemporaryDirectory() as tmp:
            con = connect(Path(tmp) / "t.db")
            self._seed(con, 20)
            ts = self._see_again(con, range(2))
            self.assertEqual(mark_gone(con, "s", ts, force=True), 18)

    def test_reappearing_listing_is_unmarked(self):
        from core.db import mark_gone
        with tempfile.TemporaryDirectory() as tmp:
            con = connect(Path(tmp) / "t.db")
            self._seed(con, 8)
            ts = self._see_again(con, range(5))
            mark_gone(con, "s", ts)
            self._see_again(con, range(8))             # it was only delisted briefly
            back = con.execute("SELECT COUNT(*) c FROM listings WHERE gone_at IS NOT NULL")
            self.assertEqual(back.fetchone()["c"], 0)


class Coordinates(unittest.TestCase):
    """One impossible coordinate hid every pin on the map: abruzzopropertyitaly
    serves `google.maps.LatLng(42.0476654, 139256123)` for Sulmona, and
    fitBounds then zoomed to 0 somewhere past the dateline."""

    def test_out_of_range_longitude_is_dropped(self):
        from core.db import clean_latlon
        row = {"lat": 42.0476654, "lon": 139256123.0}
        clean_latlon(row)
        self.assertEqual(row["lat"], 42.0476654)   # the latitude was fine
        self.assertIsNone(row["lon"])              # and we don't guess the decimal

    def test_valid_coordinates_survive(self):
        from core.db import clean_latlon
        for lat, lon in [(48.85, 2.35), (-21.35, 55.73), (35.68, 139.69), (0, 0)]:
            row = {"lat": lat, "lon": lon}
            clean_latlon(row)
            self.assertEqual((row["lat"], row["lon"]), (lat, lon))

    def test_junk_becomes_none(self):
        from core.db import clean_latlon
        row = {"lat": "nonsense", "lon": None}
        clean_latlon(row)
        self.assertEqual((row["lat"], row["lon"]), (None, None))

    def test_stored_rows_are_clean(self):
        from core.db import connect, now, upsert_from_list
        with tempfile.TemporaryDirectory() as tmp:
            con = connect(Path(tmp) / "t.db")
            upsert_from_list(con, {"external_id": "1", "url": "u", "price": 1,
                                   "lat": 42.0476654, "lon": 139256123.0},
                             "s", now(), source="abruzzopropertyitaly")
            row = con.execute("SELECT lat, lon FROM listings").fetchone()
            self.assertIsNone(row["lon"])
