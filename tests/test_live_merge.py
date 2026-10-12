#!/usr/bin/env python3
"""Characterization tests for the live merge/dedup path.

Pins the CURRENT behavior of likely_same_station, merge_gaia_stations,
remove_duplicate_leads, merge_nearby_source_stations, and source_rows_union as a
baseline for plans/007 (spatial-bucket dedup rewrite) — see plans/006. These
tests deliberately characterize oddities in the existing logic (the URL-match
branch ignoring distance; merge_nearby_source_stations using bare proximity instead
of likely_same_station's id/url/name-affinity rules) rather than "fixing"
them. Do not change product code to make one of these pass; if a real bug is
ever found here, log a qa/defects.csv row and mark the test
@unittest.expectedFailure per AGENTS.md §6/plan 006's STOP condition C.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


def station(id_, name="Station", address="", lat=39.4700, lon=-0.3700, sources=None, **extra):
    """Minimal station/lead dict — field shapes match tests/test_live_coverage.py fixtures."""
    row = {
        "id": id_,
        "name": name,
        "address": address,
        "lat": lat,
        "lon": lon,
        "sources": sources if sources is not None else [],
    }
    row.update(extra)
    return row


class LikelySameStationTests(unittest.TestCase):
    """server.likely_same_station: same-id OR shared source URL (distance-blind) OR
    (<=0.12km AND name/address token affinity)."""

    def test_same_id_matches_regardless_of_coordinates(self) -> None:
        a = station("st-1", "Carrefour Alfafar Parking", "Avenida Test 1", 39.4700, -0.3700)
        b = station("st-1", "Completely different name", "Nowhere near", 40.0, 1.0)
        self.assertTrue(server.likely_same_station(a, b))

    def test_shared_source_url_matches_even_5km_apart(self) -> None:
        # Characterizes an oddity: the source-URL branch is checked before any
        # distance gate, so two rows 5 km apart that happen to share a source
        # URL are still folded together. Real-world justification: MapaREVE
        # and Gaia can both cite the same operator page for one physical site,
        # but this branch has no independent distance sanity check.
        a = station("url-a", "Station A", "addr a", 39.4700, -0.3700,
                    sources=[{"name": "X", "url": "https://example.com/shared"}])
        b = station("url-b", "Station B totally different", "addr b", 39.5149661, -0.3700,
                    sources=[{"name": "Y", "url": "https://example.com/shared"}])
        self.assertTrue(server.likely_same_station(a, b))

    def test_close_with_shared_name_tokens_matches(self) -> None:
        a = station("c1", "Carrefour Alfafar Parking", "Avenida Test 1", 39.4700, -0.3700)
        b = station("c2", "Carrefour Alfafar Entrada", "Avenida Test 2", 39.4708993, -0.3700)  # 100 m
        self.assertTrue(server.likely_same_station(a, b))

    def test_close_with_unrelated_names_does_not_match(self) -> None:
        a = station("d1", "Carrefour Alfafar Parking", "Avenida Test 1", 39.4700, -0.3700)
        b = station("d2", "Iberdrola Sagunto Plaza", "Calle Otra 9", 39.4708993, -0.3700)  # 100 m
        self.assertFalse(server.likely_same_station(a, b))

    def test_same_name_but_beyond_distance_gate_does_not_match(self) -> None:
        # 200 m apart: name tokens are identical, but the 0.12 km distance
        # gate is checked before name affinity even gets a say.
        a = station("e1", "Carrefour Alfafar Parking", "Avenida Test 1", 39.4700, -0.3700)
        b = station("e2", "Carrefour Alfafar Parking", "Avenida Test 1", 39.4717986, -0.3700)  # 200 m
        self.assertFalse(server.likely_same_station(a, b))


class MergeGaiaStationsTests(unittest.TestCase):
    """server.merge_gaia_stations: on a match, the GAIA row replaces the slot and
    takes the union of both rows' sources; a non-matching gaia row is appended."""

    def test_matching_pair_gaia_wins_slot_and_sources_union(self) -> None:
        mapareve_row = station("same-1", "MapaREVE Station", "Addr", 39.47, -0.37,
                                sources=[{"name": "MapaREVE", "url": "https://mapareve.example/a"}])
        gaia_row = station("same-1", "Gaia Station", "Addr", 39.47, -0.37,
                            sources=[{"name": "Gaia", "url": "https://gaia.example/a"}])

        merged = server.merge_gaia_stations([mapareve_row], [gaia_row])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["name"], "Gaia Station")  # gaia row replaced the slot
        urls = {s["url"] for s in merged[0]["sources"]}
        self.assertEqual(urls, {"https://gaia.example/a", "https://mapareve.example/a"})
        self.assertEqual(merged[0]["source_count"], len(merged[0]["sources"]))

    def test_non_matching_gaia_row_is_appended(self) -> None:
        mapareve_row = station("b-1", "Other Station", "Addr", 40.0, 1.0,
                                sources=[{"name": "MapaREVE", "url": "https://mapareve.example/b"}])
        gaia_row = station("gaia-c", "Unrelated Gaia", "Addr far", 41.0, 2.0,
                            sources=[{"name": "Gaia", "url": "https://gaia.example/c"}])

        merged = server.merge_gaia_stations([mapareve_row], [gaia_row])

        self.assertEqual(len(merged), 2)
        self.assertIn(gaia_row, merged)


class RemoveDuplicateLeadsTests(unittest.TestCase):
    """server.remove_duplicate_leads: a matching lead is dropped from the returned
    list, but its sources are folded into the live station FIRST (not silently
    discarded) — a non-matching lead survives untouched."""

    def test_matching_lead_is_dropped_and_folds_source_into_station(self) -> None:
        live_stations = [station("x-1", "Station X", "Addr", 39.47, -0.37,
                                  sources=[{"name": "MapaREVE", "url": "https://mapareve.example/x"}])]
        lead = station("x-1", "Lead Y dup", "Addr", 39.47, -0.37,
                        sources=[{"name": "LeadSrc", "url": "https://lead.example/y"}])

        remaining = server.remove_duplicate_leads([lead], live_stations)

        self.assertEqual(remaining, [])
        urls = {s["url"] for s in live_stations[0]["sources"]}
        self.assertEqual(urls, {"https://mapareve.example/x", "https://lead.example/y"})
        self.assertEqual(live_stations[0]["source_count"], len(live_stations[0]["sources"]))

    def test_non_matching_lead_survives_untouched(self) -> None:
        live_stations = [station("x-1", "Station X", "Addr", 39.47, -0.37,
                                  sources=[{"name": "MapaREVE", "url": "https://mapareve.example/x"}])]
        lead_z = station("z-1", "Lead Z distinct", "Nowhere close", 10.0, 10.0,
                          sources=[{"name": "LeadSrc2", "url": "https://lead.example/z"}])

        remaining = server.remove_duplicate_leads([lead_z], live_stations)

        self.assertEqual(remaining, [lead_z])
        # The live station's sources are untouched by the non-matching lead.
        self.assertEqual(len(live_stations[0]["sources"]), 1)


class MergeNearbySourceStationsTests(unittest.TestCase):
    """server.merge_nearby_source_stations: DIFFERENT semantics from likely_same_station —
    bare ~80 m proximity, with NO id/url/name-affinity check at all. This is
    documented in the function's own docstring. It means
    two UNRELATED stations 79 m apart get silently folded together, where
    likely_same_station would have kept them separate. The helper is used for
    Electromaps promotion, so this asymmetry applies there."""

    def _pair(self, delta_lat: float):
        existing = station("mapareve-1", "Repsol Service Station", "Autovia A-7 km 12", 39.4700, -0.3700,
                            sources=[{"name": "MapaREVE", "url": "https://mapareve.example/1"}])
        nearby = station("electromaps-1", "Colegio Público Rafelbunyol", "Calle Deporte 8", 39.4700 + delta_lat, -0.3700,
                         sources=[{"name": "Electromaps", "url": "https://electromaps.example/1"}])
        return existing, nearby

    def test_just_inside_80m_merges_by_bare_proximity_even_with_unrelated_names(self) -> None:
        existing, nearby = self._pair(0.0007105)  # 0.079 km — just inside the 0.08 km threshold

        # Characterize the asymmetry directly: likely_same_station would NOT
        # consider this pair the same station (distance passes, but names
        # share no tokens) ...
        self.assertFalse(server.likely_same_station(existing, nearby))

        # ... yet merge_nearby_source_stations merges them anyway, purely on proximity.
        merged = server.merge_nearby_source_stations([dict(existing)], [dict(nearby)])
        self.assertEqual(len(merged), 1)
        urls = {s["url"] for s in merged[0]["sources"]}
        self.assertEqual(urls, {"https://mapareve.example/1", "https://electromaps.example/1"})
        self.assertEqual(merged[0]["source_count"], len(merged[0]["sources"]))

    def test_just_outside_80m_stays_separate(self) -> None:
        existing, nearby = self._pair(0.0007285)  # 0.081 km — just outside the 0.08 km threshold

        merged = server.merge_nearby_source_stations([dict(existing)], [dict(nearby)])

        self.assertEqual(len(merged), 2)


class SourceRowsUnionTests(unittest.TestCase):
    """server.source_rows_union: dedupes by the (name, url) pair and caps at 10 rows."""

    def test_dedupes_by_name_and_url_pair(self) -> None:
        primary = {"sources": [{"name": "Dup", "url": "https://ex.com/dup"}, {"name": "A", "url": "https://ex.com/a"}]}
        secondary = {"sources": [{"name": "Dup", "url": "https://ex.com/dup"}, {"name": "B", "url": "https://ex.com/b"}]}

        union = server.source_rows_union(primary, secondary)

        self.assertEqual(len(union), 3)
        self.assertEqual({u["name"] for u in union}, {"Dup", "A", "B"})

    def test_caps_at_ten_even_with_more_unique_sources(self) -> None:
        primary = {"sources": [{"name": f"P{i}", "url": f"https://ex.com/p{i}"} for i in range(6)]}
        secondary = {"sources": [{"name": f"Q{i}", "url": f"https://ex.com/q{i}"} for i in range(6)]}

        union = server.source_rows_union(primary, secondary)

        self.assertEqual(len(union), 10)  # 12 unique rows offered, capped to 10


if __name__ == "__main__":
    unittest.main()
