#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


class CorroborationTests(unittest.TestCase):
    def test_independent_families_raise_tier(self) -> None:
        st = {
            "source": "MapaREVE public live OCPI",
            "source_url": "https://www.mapareve.es/loc/1",
            "sources": [
                {"name": "MapaREVE", "url": "https://www.mapareve.es/loc/1"},
                {"name": "Electromaps public map", "url": "https://www.electromaps.com/x"},
            ],
        }
        server.apply_corroboration(st)
        self.assertGreaterEqual(st["corroboration"]["independent_sources"], 2)
        self.assertEqual(st["corroboration"]["tier"], "corroborated")

    def test_three_families_strongly_corroborated(self) -> None:
        st = {
            "sources": [
                {"name": "MapaREVE", "url": "https://www.mapareve.es/x"},
                {"name": "OpenChargeMap", "url": "https://map.openchargemap.io/?id=1"},
                {"name": "Chargemap public map", "url": "https://chargemap.com/x"},
            ],
        }
        server.apply_corroboration(st)
        self.assertEqual(st["corroboration"]["independent_sources"], 3)
        self.assertEqual(st["corroboration"]["tier"], "strongly_corroborated")

    def test_single_source_is_single_tier(self) -> None:
        st = {"source": "OpenStreetMap public tags", "sources": [{"name": "OpenStreetMap", "url": "https://www.openstreetmap.org/node/1"}]}
        server.apply_corroboration(st)
        self.assertEqual(st["corroboration"]["tier"], "single")

    def test_same_family_twice_is_not_corroboration(self) -> None:
        st = {"sources": [
            {"name": "Electromaps", "url": "https://www.electromaps.com/a"},
            {"name": "Electromaps detail", "url": "https://www.electromaps.com/b"},
        ]}
        server.apply_corroboration(st)
        self.assertEqual(st["corroboration"]["independent_sources"], 1)

    def test_corroboration_boosts_score_bounded(self) -> None:
        base = {"is_free": True, "corroboration": {"independent_sources": 1}}
        two = {"is_free": True, "corroboration": {"independent_sources": 2}}
        three = {"is_free": True, "corroboration": {"independent_sources": 3}}
        self.assertGreater(server.recommendation_score(two), server.recommendation_score(base))
        self.assertGreater(server.recommendation_score(three), server.recommendation_score(two))
        # bounded: boost never exceeds +12
        self.assertLessEqual(
            server.recommendation_score({"is_free": True, "corroboration": {"independent_sources": 9}})
            - server.recommendation_score(base),
            12.0001,
        )

    def test_corroboration_never_flips_live(self) -> None:
        # A heavily-corroborated directory/community station must NOT be marked live-verified.
        item = {
            "reliability": "directory-map-free",
            "corroboration": {"independent_sources": 3, "tier": "strongly_corroborated"},
        }
        v = server.verification_profile(item, live=False)
        self.assertFalse(v.get("live_verified"))


if __name__ == "__main__":
    unittest.main()
