#!/usr/bin/env python3
"""National ingestion scope tests.

Volta went national in June 2026, but several source functions kept fetching only
VALENCIA_BBOX, so Spain outside Valencia was served almost entirely by one crowdsourced
directory: 178 of 779 records sat in València while Madrid had 11. These tests pin the
national tiling that replaced source_osm's Valencia-only Overpass query.

The tiling is deliberately a grid rather than one national query: Overpass answers several
bounded queries far more reliably than one country-sized one, and a per-tile cache means a
single failed tile costs one tile instead of the whole pass.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ingest_sources as ing  # noqa: E402

MAJOR_CITIES = {
    "Madrid": (40.4168, -3.7038),
    "Barcelona": (41.3874, 2.1686),
    "Sevilla": (37.3891, -5.9845),
    "Bilbao": (43.2630, -2.9350),
    "València": (39.4699, -0.3763),
    "Zaragoza": (41.6488, -0.8891),
    "Málaga": (36.7213, -4.4213),
    "Palma": (39.5696, 2.6502),
    "A Coruña": (43.3623, -8.4115),
    "Las Palmas": (28.1235, -15.4363),
    "Santa Cruz de Tenerife": (28.4636, -16.2518),
    "Ceuta": (35.8894, -5.3213),
    "Melilla": (35.2923, -2.9381),
}

# The four the old Valencia-only Overpass query could never reach.
OUTSIDE_THE_OLD_BOX = ("Madrid", "Barcelona", "Sevilla", "Bilbao")


class SpainTilesTests(unittest.TestCase):
    def test_tiles_stay_inside_spain_and_cover_islands(self):
        tiles = ing.spain_tiles()
        south, west, north, east = ing.SPAIN_BBOX
        self.assertGreaterEqual(min(t[0] for t in tiles), south - 1e-9)
        self.assertGreaterEqual(min(t[1] for t in tiles), west - 1e-9)
        self.assertLessEqual(max(t[2] for t in tiles), north + 1e-9)
        self.assertLessEqual(max(t[3] for t in tiles), east + 1e-9)
        self.assertEqual(tiles[:16], ing._grid_tiles(ing.MAINLAND_TILE_BBOX, 4, 4))
        self.assertTrue(any(t[0] <= 28.12 <= t[2] and t[1] <= -15.43 <= t[3] for t in tiles))
        self.assertTrue(any(t[0] <= 35.29 <= t[2] and t[1] <= -2.94 <= t[3] for t in tiles))

    def test_mainland_tile_areas_sum_without_gaps(self):
        """Mainland/Balearic 4x4 stays gapless. Island tiles are extra, not a filled ocean rectangle."""
        tiles = ing.spain_tiles()[:16]
        south, west, north, east = ing.MAINLAND_TILE_BBOX
        self.assertAlmostEqual(
            sum((t[2] - t[0]) * (t[3] - t[1]) for t in tiles),
            (north - south) * (east - west),
        )

    def test_grid_shape_is_configurable(self):
        self.assertEqual(len(ing.spain_tiles()), 24)
        self.assertEqual(len(ing.spain_tiles(rows=2, cols=3)), 14)

    def test_every_major_city_falls_inside_a_tile(self):
        tiles = ing.spain_tiles()
        for name, (lat, lon) in MAJOR_CITIES.items():
            covered = [t for t in tiles if t[0] <= lat <= t[2] and t[1] <= lon <= t[3]]
            self.assertTrue(covered, f"{name} is covered by no tile")


class NationalScopeRegressionTests(unittest.TestCase):
    def test_the_old_valencia_box_really_did_exclude_the_big_cities(self):
        """Documents the defect this scope change fixes: these cities were unreachable while
        the Overpass query was pinned to VALENCIA_BBOX, so OSM contributed nothing there."""
        for name in OUTSIDE_THE_OLD_BOX:
            lat, lon = MAJOR_CITIES[name]
            self.assertFalse(
                ing.in_bbox(lat, lon, ing.VALENCIA_BBOX),
                f"{name} is inside VALENCIA_BBOX -- this test's premise is wrong",
            )

    def test_national_tiling_now_reaches_them(self):
        tiles = ing.spain_tiles()
        for name in OUTSIDE_THE_OLD_BOX:
            lat, lon = MAJOR_CITIES[name]
            self.assertTrue(
                any(t[0] <= lat <= t[2] and t[1] <= lon <= t[3] for t in tiles),
                f"{name} is still unreachable after the national scope change",
            )


if __name__ == "__main__":
    unittest.main()
