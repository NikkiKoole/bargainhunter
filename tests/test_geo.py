"""Every listing needs a country, because the detail panel draws one."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.db import connect, now, upsert_from_list
from core.geo import SOURCE_COUNTRY, country_for, iso


class Resolve(unittest.TestCase):
    def test_portal_country_wins(self):
        # Holprop and Domaza span several countries, so what the portal said
        # beats the adapter's default.
        self.assertEqual(country_for({"raw_fields": {"country": "Spain"}}, "holprop"), "ES")
        self.assertEqual(country_for({"raw_fields": {"country": "Montenegro"}}, "domaza"), "ME")

    def test_single_country_sources_fall_back_to_their_home(self):
        for source, expected in SOURCE_COUNTRY.items():
            self.assertEqual(country_for({}, source), expected, source)

    def test_unknown_source_has_no_country(self):
        self.assertIsNone(country_for({}, "something-new"))

    def test_iso_accepts_names_and_codes(self):
        self.assertEqual(iso("Japan"), "JP")
        self.assertEqual(iso("maroc"), "MA")
        self.assertEqual(iso("fr"), "FR")
        self.assertIsNone(iso("Atlantis"))
        self.assertIsNone(iso(None))


class Stored(unittest.TestCase):
    def test_upsert_sets_country(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = connect(Path(tmp) / "t.db")
            ts = now()
            upsert_from_list(con, {"external_id": "1", "url": "u", "price": 1},
                             "s", ts, source="akiyaportal")
            got = con.execute("SELECT country FROM listings").fetchone()["country"]
            self.assertEqual(got, "JP")

    def test_legacy_rows_are_backfilled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.db"
            con = connect(path)
            ts = now()
            upsert_from_list(con, {"external_id": "9", "url": "u", "price": 1,
                                   "raw_fields": {"country": "Italy"}},
                             "s", ts, source="abruzzopropertyitaly")
            con.execute("UPDATE listings SET country=NULL")
            con.commit()
            con.close()
            con = connect(path)          # reopening runs the migration
            got = con.execute("SELECT country FROM listings").fetchone()["country"]
            self.assertEqual(got, "IT")


class Maps(unittest.TestCase):
    def test_every_country_we_store_can_be_drawn(self):
        js = (Path(__file__).resolve().parent.parent
              / "franimo" / "web" / "maps.js").read_text(encoding="utf-8")
        maps = json.loads(js.split("=", 1)[1].strip().rstrip(";"))
        for code in set(SOURCE_COUNTRY.values()):
            self.assertIn(code, maps, f"no outline for {code}")
            self.assertTrue(maps[code]["path"].startswith("M"))
        self.assertIn("depts", maps["FR"])     # France is the only one with subdivisions
        self.assertGreater(len(maps["FR"]["depts"]), 90)


if __name__ == "__main__":
    unittest.main()
