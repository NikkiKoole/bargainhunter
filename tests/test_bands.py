"""Price-band splitting for portals that cap their pager."""
from __future__ import annotations

import unittest

from core.bands import MIN_BAND_WIDTH, describe, split_bands
from greenacres.scrape import PAGE_CEILING, seed_price_range, with_prices

SEED = "/onroerend-goed?searchQuery=lg-nl-cn-fr-hab_house-on-mx_p-150000"


def lopsided(total=4680, top=150000, per_page=24):
    """Green-Acres shape: most of the catalogue sits at the top of the range."""
    def pages_for(lo, hi):
        cum = lambda x: int(total * min(1.0, max(0.0, x / top)) ** 2.2)
        return max(1, (cum(hi) - cum(lo) + per_page - 1) // per_page)
    return pages_for


class Split(unittest.TestCase):
    def test_every_band_fits_under_the_ceiling(self):
        pages_for = lopsided()
        bands = split_bands(pages_for, 0, 150000, PAGE_CEILING)
        self.assertTrue(bands)
        for lo, hi in bands:
            self.assertLessEqual(pages_for(lo, hi), PAGE_CEILING, f"€{lo}-{hi}")

    def test_bands_cover_the_range_without_gaps(self):
        bands = split_bands(lopsided(), 0, 150000, PAGE_CEILING)
        self.assertEqual(bands[0][0], 0)
        self.assertEqual(bands[-1][1], 150000)
        for (_, prev_hi), (next_lo, _) in zip(bands, bands[1:]):
            self.assertEqual(next_lo, prev_hi + 1)      # touching, not overlapping

    def test_a_search_that_already_fits_is_left_alone(self):
        bands = split_bands(lambda lo, hi: 3, 0, 150000, PAGE_CEILING)
        self.assertEqual(bands, [[0, 150000]])

    def test_unmeasurable_range_is_crawled_rather_than_dropped(self):
        bands = split_bands(lambda lo, hi: None, 0, 150000, PAGE_CEILING)
        self.assertEqual(bands, [[0, 150000]])

    def test_halving_stops_at_a_sane_width(self):
        # a portal that always claims to overflow must not split forever
        bands = split_bands(lambda lo, hi: 999, 0, 150000, PAGE_CEILING)
        self.assertTrue(all(hi - lo >= 1 for lo, hi in bands))
        self.assertLess(len(bands), 300)
        self.assertTrue(any(hi - lo <= MIN_BAND_WIDTH * 2 for lo, hi in bands))

    def test_describe_is_readable(self):
        self.assertEqual(describe([[0, 50000]]), "€0–€50,000")


class GreenAcresUrls(unittest.TestCase):
    def test_sets_both_bounds(self):
        got = with_prices(SEED, 0, 50000)
        self.assertIn("mn_p-0", got)
        self.assertIn("mx_p-50000", got)
        self.assertNotIn("mx_p-150000", got)

    def test_rewriting_is_idempotent(self):
        once = with_prices(SEED, 0, 50000)
        twice = with_prices(once, 60000, 70000)
        self.assertEqual(twice.count("mn_p"), 1)
        self.assertEqual(twice.count("mx_p"), 1)
        self.assertIn("mn_p-60000", twice)

    def test_other_tokens_survive(self):
        got = with_prices(SEED, 0, 50000)
        for token in ("lg-nl", "cn-fr", "hab_house-on"):
            self.assertIn(token, got)

    def test_seed_range(self):
        self.assertEqual(seed_price_range(SEED, {"above": 150000}), (0, 150000))
        self.assertEqual(seed_price_range(with_prices(SEED, 10, 20), {}), (10, 20))


if __name__ == "__main__":
    unittest.main()


class CeilingWarning(unittest.TestCase):
    """A band of exactly PAGE_CEILING pages is complete, not truncated."""

    def test_exactly_at_the_cap_is_not_a_warning(self):
        import inspect
        from greenacres import scrape
        src = inspect.getsource(scrape.crawl_search)
        self.assertIn('page["total_pages"] > PAGE_CEILING', src,
                      "the ceiling warning must compare claimed pages, not pages crawled")
