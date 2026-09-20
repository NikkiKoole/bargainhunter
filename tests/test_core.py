"""Phase 0: shared core stays importable and multi-source-ready."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.db import connect, now, upsert_from_list
from core.export import pack
from core.http import Fetcher, cache_path
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
