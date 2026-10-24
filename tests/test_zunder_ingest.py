#!/usr/bin/env python3
"""Zunder public location map: active sites only, honest counts, owner grant gate."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
import ingest_sources as ing  # noqa: E402


def marker(number: int, lat: float, lon: float, title: str, icon: str) -> str:
    return (f"var marker{number} = new google.maps.Marker({{\n position: {{ lat: {lat}, lng: {lon}}},\n map: map,\n"
            f" title: '{title}',\n icon: {{\n url: '/wp-content/plugins/easycharger/icons/{icon}.png',\n")


PAGE = "".join([
    marker(4, 42.71779934, -4.29173329, "Alar del Rey - Puerta de la Monta\\'ña", "icono-marino"),
    marker(5, 39.528921, -1.433511, "Under construction site", "icono-celeste"),
    marker(6, 38.72, -9.14, "Lisboa", "icono-marino"),
    marker(7, 40.0, -3.0, "No detail site", "icono-marino"),
])
DETAILS = {"4": {"id": 4, "status": {"id": 1, "name": "Activo"}, "connectors": [
    {"connector_type": "TYPE2", "max_electric_power": 22.0, "available": 0, "in_use": 0, "unavailable": 2},
    {"connector_type": "CCS", "max_electric_power": 150.0, "available": 4, "in_use": 1, "unavailable": 0},
]}}


class ZunderIngestTests(unittest.TestCase):
    def test_only_active_sites_are_kept(self) -> None:
        sites = ing.parse_zunder_markers(PAGE)
        self.assertEqual([site["id"] for site in sites], ["4", "6", "7"])
        self.assertEqual(sites[0]["name"], "Alar del Rey - Puerta de la Monta'ña")

    def test_rows_carry_hardware_and_fetch_time_counts_never_live(self) -> None:
        rows = ing.parse_zunder_sites(ing.parse_zunder_markers(PAGE), DETAILS)
        self.assertEqual(len(rows), 2)  # Lisboa is outside Spain
        site = next(row for row in rows if row["raw"]["zunder_location_id"] == "4")
        self.assertEqual(site["connector_labels"], ["CCS2", "Type 2"])
        self.assertEqual(site["max_power_kw"], 150.0)
        self.assertEqual(site["total_evse"], 7)
        self.assertIn("4 available, 1 in use, 2 unavailable", site["sources"][0]["evidence"])
        self.assertIn("not live availability", site["sources"][0]["evidence"])
        self.assertTrue(all(connector["status"] == "UNKNOWN" for connector in site["connectors"]))
        self.assertIsNone(site["price_min"])

    def test_a_partly_typed_site_keeps_its_port_count(self) -> None:
        details = {"4": {"status": {"name": "Activo"}, "connectors": [
            {"connector_type": "TYPE2", "max_electric_power": 22.0, "available": 2, "in_use": 0, "unavailable": 0},
            {"connector_type": "MYSTERY", "max_electric_power": 7.0, "available": 1, "in_use": 0, "unavailable": 0}]}}
        site = next(row for row in ing.parse_zunder_sites(ing.parse_zunder_markers(PAGE), details)
                    if row["raw"]["zunder_location_id"] == "4")
        self.assertEqual(site["connectors"], [])
        self.assertEqual(site["connector_labels"], ["Type 2"])
        self.assertEqual(site["total_evse"], 3)

    def test_a_site_without_detail_keeps_its_pin(self) -> None:
        rows = ing.parse_zunder_sites(ing.parse_zunder_markers(PAGE), DETAILS)
        bare = next(row for row in rows if row["raw"]["zunder_location_id"] == "7")
        self.assertIn("not available at this refresh", bare["sources"][0]["evidence"])
        self.assertEqual(bare["connector_labels"], [])

    def test_a_detail_that_is_not_active_drops_the_site(self) -> None:
        details = {"4": {"status": {"name": "Inactivo"}, "connectors": []}}
        rows = ing.parse_zunder_sites(ing.parse_zunder_markers(PAGE), details)
        self.assertNotIn("4", [row["raw"]["zunder_location_id"] for row in rows])

    def test_detail_requests_respect_the_budget_and_stop_at_a_rate_limit(self) -> None:
        import tempfile
        page = ing.FetchArtifact(body=PAGE.encode(), url=ing.ZUNDER_MAP_URL, cache_name="p", fetched_at="2026-10-24T00:00:00Z",
                                 age_seconds=0, hit=False, fallback=False, stale=False)
        many = "".join(marker(100 + i, 40.0 + i / 100, -3.0, f"Site {i}", "icono-marino") for i in range(12))
        page = ing.FetchArtifact(**{**page.__dict__, "body": many.encode()})
        calls = []

        def fetch(url, cache_name, **kwargs):
            if url == ing.ZUNDER_MAP_URL:
                return page
            calls.append(url)
            body = b'<!doctype html><title>429</title>429 Too Many Requests' if len(calls) == 3 else b'{"status": {"name": "Activo"}, "connectors": []}'
            return ing.FetchArtifact(**{**page.__dict__, "body": body})

        records: list = []
        with tempfile.TemporaryDirectory() as tmp, patch.object(ing, "CACHE", Path(tmp)), \
             patch.object(ing, "cached_fetch", side_effect=fetch), patch.object(ing.time, "sleep"):
            ing.source_zunder(records)
        self.assertEqual(len(calls), 3)  # stops at the first rate-limit page, within the budget of 5
        self.assertEqual(len(records), 12)  # every active site keeps its map pin

    def test_counts_carry_the_detail_clock_not_the_map_clock(self) -> None:
        page = ing.FetchArtifact(body=PAGE.encode(), url=ing.ZUNDER_MAP_URL, cache_name="p",
                                 fetched_at="2026-10-24T10:00:00Z", age_seconds=0, hit=False, fallback=False, stale=False)
        details = {"4": dict(DETAILS["4"], _fetched_at="2026-09-20T08:00:00Z", _cache_fallback=True)}
        site = next(row for row in ing.parse_zunder_sites(ing.parse_zunder_markers(PAGE), details, source_artifact=page)
                    if row["raw"]["zunder_location_id"] == "4")
        self.assertIn("at 2026-09-20 08:00 UTC", site["sources"][0]["evidence"])
        self.assertEqual(site["source_fetched_at"], "2026-09-20T08:00:00Z")
        self.assertTrue(site["sources"][0]["source_cache_fallback"])

    def test_a_rate_limit_absorbed_by_the_cache_fallback_stops_requests(self) -> None:
        import tempfile
        many = "".join(marker(100 + i, 40.0 + i / 100, -3.0, f"Site {i}", "icono-marino") for i in range(4))
        page = ing.FetchArtifact(body=many.encode(), url=ing.ZUNDER_MAP_URL, cache_name="p", fetched_at="2026-10-24T00:00:00Z",
                                 age_seconds=0, hit=False, fallback=False, stale=False)
        network = []

        def fetch(url, cache_name, ttl_hours=24, **kwargs):
            if url == ing.ZUNDER_MAP_URL:
                return page
            if ttl_hours != float("inf"):
                network.append(url)  # a request was allowed; the 429 falls back to the expired copy
            return ing.FetchArtifact(**{**page.__dict__, "body": b'{"status": {"name": "Activo"}, "connectors": []}',
                                        "fallback": ttl_hours != float("inf"), "error": "HTTPError"})

        records: list = []
        with tempfile.TemporaryDirectory() as tmp, patch.object(ing, "CACHE", Path(tmp)), \
             patch.object(ing, "cached_fetch", side_effect=fetch), patch.object(ing.time, "sleep"):
            for i in range(4):  # expired copies exist for every site
                path = Path(tmp) / f"zunder_location_{100 + i}.json"
                path.write_text("{}")
                import os
                os.utime(path, (0, 0))
            ing.source_zunder(records)
        self.assertEqual(len(network), 1)
        self.assertEqual(len(records), 4)

    def test_collection_stops_when_the_owner_grant_is_removed(self) -> None:
        records: list = []
        with patch.object(ing, "SOURCE_POLICY_EXCLUSIONS", {"granted_reuse": []}), \
             patch.object(ing, "cached_fetch") as fetch:
            ing.source_zunder(records)
        fetch.assert_not_called()
        self.assertEqual(records, [])


if __name__ == "__main__":
    unittest.main()
