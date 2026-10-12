"""Overview selection must not spend a capped response on ineligible prices."""

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


def lead(station_id, price=None, *, is_free=False, score=0):
    return {
        "id": station_id, "name": station_id, "lat": 28.49, "lon": -16.33,
        "price_min": price, "is_free": is_free, "lead": True,
        "recommendation_score": score,
    }


class OverviewCoverageTests(unittest.TestCase):
    def overview(self, rows, max_price=5.0, params=None):
        with (
            patch.object(server, "load_all_leads", return_value=deepcopy(rows)),
            patch.object(server, "apply_corroboration"),
            patch.object(server, "recommendation_score", side_effect=lambda row: row["recommendation_score"]),
        ):
            status, payload = server.overview_response(params or {}, max_price, False)
        self.assertEqual(status, 200)
        return payload

    def test_free_cap_filters_unknown_prices_before_truncating(self):
        rows = [lead(f"unknown-{i}", score=50) for i in range(2500)]
        rows += [lead("free-low-ranked", 0, is_free=True, score=-20)]
        result = self.overview(rows, 0)
        self.assertEqual([row["id"] for row in result["leads"]], ["free-low-ranked"])

    def test_explicit_caps_exclude_unknown_and_invalid_prices(self):
        rows = [lead("unknown"), lead("nan", float("nan")), lead("negative", -1),
                lead("free", 0), lead("cheap", 0.2), lead("paid", 0.75)]
        for cap, expected in [(0.25, ["free", "cheap"]), (1.0, ["free", "cheap", "paid"])]:
            with self.subTest(cap=cap):
                result = self.overview(rows, cap)
                self.assertEqual([row["id"] for row in result["leads"]], expected)

    def test_broad_overview_retains_unknown_price_locations(self):
        result = self.overview([lead("unknown"), lead("paid", 0.75)], 5.0)
        self.assertEqual([row["id"] for row in result["leads"]], ["unknown", "paid"])

    def test_free_flag_without_numeric_tariff_is_eligible(self):
        result = self.overview([lead("reported-free", is_free=True), lead("unknown")], 0)
        self.assertEqual([row["id"] for row in result["leads"]], ["reported-free"])

    def test_positive_tariff_overrides_a_conflicting_free_flag(self):
        result = self.overview([lead("conflict", 0.4, is_free=True)], 0)
        self.assertEqual(result["leads"], [])

    def test_free_only_applies_before_ranking_cap_even_with_broad_max_price(self):
        rows = [lead(f"paid-{i}", 0.4, score=50) for i in range(2500)]
        rows += [lead("free", 0, is_free=True, score=-20)]
        result = self.overview(rows, 5.0, {"free_only": ["true"]})
        self.assertEqual([row["id"] for row in result["leads"]], ["free"])

    def test_free_only_has_independent_serialized_cache_entries(self):
        for bbox in (None, "27.5,-18.3,29.5,-13.3"):
            with self.subTest(bbox=bbox):
                original_default = deepcopy(server.OVERVIEW_BYTES_CACHE)
                original_bbox = deepcopy(server.OVERVIEW_BBOX_BYTES_CACHE)
                server.OVERVIEW_BYTES_CACHE.clear()
                server.OVERVIEW_BBOX_BYTES_CACHE.clear()
                try:
                    params = {"bbox": [bbox]} if bbox else {}
                    def response(query, max_price, fast_only):
                        return 200, {"free_only": query.get("free_only") == ["true"]}
                    with patch.object(server, "overview_response", side_effect=response) as mocked:
                        all_body = server.overview_response_bytes(params, 5.0, False)[1]
                        free_body = server.overview_response_bytes({**params, "free_only": ["true"]}, 5.0, False)[1]
                        repeated = server.overview_response_bytes(params, 5.0, False)[1]
                    self.assertFalse(json.loads(all_body)["free_only"])
                    self.assertTrue(json.loads(free_body)["free_only"])
                    self.assertEqual(all_body, repeated)
                    self.assertEqual(mocked.call_count, 2)
                finally:
                    server.OVERVIEW_BYTES_CACHE.clear()
                    server.OVERVIEW_BYTES_CACHE.update(original_default)
                    server.OVERVIEW_BBOX_BYTES_CACHE.clear()
                    server.OVERVIEW_BBOX_BYTES_CACHE.update(original_bbox)


if __name__ == "__main__":
    unittest.main()
