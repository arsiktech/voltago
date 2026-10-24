#!/usr/bin/env python3
"""Location × source matrix: five honest cell states."""
from __future__ import annotations

import json
import sys
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import source_matrix as sm  # noqa: E402

LAT, LON = 41.6100, 0.6200
METRE = 1 / 111_320
BBOX = {"latitude_sw": 41.60, "longitude_sw": 0.61, "latitude_ne": 41.62, "longitude_ne": 0.63}


def row(id_: str, *sources: str, north_m: float = 0, price_sources=(), fallback: bool = False) -> dict:
    return {
        "id": id_, "name": f"Station {id_}", "address": "Av. Test 1", "city": "Lleida",
        "lat": LAT + north_m * METRE, "lon": LON,
        "sources": [{"name": s, "url": f"https://example.test/{id_}", "evidence": "e",
                     **({"source_cache_fallback": True, "source_fetched_at": "2026-10-20T00:00:00Z"} if fallback else {})}
                    for s in sources],
        "price_evidence": [{"source": s, "price_per_kwh": 0.49, "source_url": "https://example.test/p"} for s in price_sources],
    }


def cells(intel: dict, id_: str) -> dict:
    matrix = sm.build_matrix(intel, BBOX)
    location = next(item for item in matrix["locations"] if item["id"] == id_)
    return location["cells"]


class SourceMatrixTests(unittest.TestCase):
    def test_listed_and_not_listed_for_complete_national_sources(self) -> None:
        intel = {"records": [row("a", "DGT National Access Point charging registry", "OpenStreetMap public tags")]}
        result = cells(intel, "a")
        self.assertEqual(result["dgt"]["state"], "listed")
        self.assertEqual(result["osm"]["state"], "listed")
        self.assertEqual(result["ocm"]["state"], "not_listed")
        self.assertEqual(result["dgt"]["entries"][0]["url"], "https://example.test/a")

    def test_network_and_out_of_scope_sources_never_claim_absence(self) -> None:
        result = cells({"records": [row("a", "DGT National Access Point charging registry")]}, "a")
        self.assertEqual(result["fenie"]["state"], "unknown")
        self.assertIn("own network", result["fenie"]["reason"])
        self.assertEqual(result["canarias"]["state"], "unknown")
        self.assertIn("Outside", result["canarias"]["reason"])

    def test_price_only_sources_are_restricted_or_unknown(self) -> None:
        intel = {"records": [row("a", "DGT National Access Point charging registry", price_sources=("Repsol",))]}
        result = cells(intel, "a")
        self.assertEqual(result["repsol"]["state"], "restricted")
        self.assertEqual(result["repsol"]["entries"][0]["price_per_kwh"], 0.49)
        self.assertEqual(result["chargemap"]["state"], "unknown")

    def test_a_nearby_unmatched_listing_is_uncertain_not_absent(self) -> None:
        intel = {"records": [row("a", "DGT National Access Point charging registry"),
                             row("b", "OpenStreetMap public tags", north_m=50)]}
        result = cells(intel, "a")
        self.assertEqual(result["osm"]["state"], "uncertain")
        self.assertEqual(result["osm"]["candidate"]["id"], "b")
        self.assertEqual(result["osm"]["candidate"]["distance_m"], 50)

    def test_a_listing_beyond_80_m_does_not_make_a_cell_uncertain(self) -> None:
        intel = {"records": [row("a", "DGT National Access Point charging registry"),
                             row("b", "OpenStreetMap public tags", north_m=120)]}
        self.assertEqual(cells(intel, "a")["osm"]["state"], "not_listed")

    def test_fallback_or_failed_sources_cannot_prove_absence(self) -> None:
        intel = {"records": [row("a", "DGT National Access Point charging registry"),
                             row("far", "OpenStreetMap public tags", north_m=5000, fallback=True)],
                 "errors": [{"source": "source_openchargemap", "error": "RuntimeError: x"}]}
        result = cells(intel, "a")
        self.assertEqual(result["osm"]["state"], "unknown")
        self.assertIn("cached copy from 2026-10-20", result["osm"]["reason"])
        self.assertEqual(result["ocm"]["state"], "unknown")
        self.assertIn("failed", result["ocm"]["reason"])

    def test_fetch_evidence_fallback_degrades_its_column(self) -> None:
        intel = {"records": [row("a", "DGT National Access Point charging registry")],
                 "fetch_evidence": {"artifacts": [{"cache_name": "openchargemap_es.json", "fetched_at": "2026-10-19T00:00:00Z",
                                                   "cache": {"fallback": True}}]}}
        self.assertEqual(cells(intel, "a")["ocm"]["state"], "unknown")

    def test_locations_outside_the_area_are_excluded_and_the_limit_is_reported(self) -> None:
        intel = {"records": [row(str(i), "DGT National Access Point charging registry", north_m=i) for i in range(5)]
                 + [row("outside", "DGT National Access Point charging registry", north_m=50_000)]}
        matrix = sm.build_matrix(intel, BBOX, limit=3)
        self.assertEqual(len(matrix["locations"]), 3)
        self.assertTrue(matrix["truncated"])
        self.assertNotIn("outside", [item["id"] for item in matrix["locations"]])
        self.assertEqual(len(matrix["columns"]), len(sm.COLUMNS))


class LocationSourcesEndpointTests(unittest.TestCase):
    def test_endpoint_requires_a_small_bbox(self) -> None:
        import server
        intel = {"records": [row("a", "DGT National Access Point charging registry")]}
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        import threading
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}/api/location-sources"
        try:
            with patch("server.load_intelligence_meta", return_value=intel):
                with urllib.request.urlopen(f"{base}?bbox=41.60,0.61,41.62,0.63") as response:
                    payload = json.loads(response.read())
                self.assertEqual(payload["locations"][0]["id"], "a")
                for query in ("", "?bbox=40,0,42,2"):
                    with self.subTest(query=query), self.assertRaises(urllib.error.HTTPError) as caught:
                        urllib.request.urlopen(base + query)
                    self.assertEqual(caught.exception.code, 400)
                    caught.exception.close()
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
