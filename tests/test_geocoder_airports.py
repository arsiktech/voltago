"""Airport lookup regressions replay recorded Nominatim responses, never the network."""
from __future__ import annotations

import io
import json
import unittest
import urllib.parse
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import agent_mcp
import server


FIXTURE = json.loads((Path(__file__).parent / "fixtures/geocoder_airports.json").read_text())


class AirportGeocoderTests(unittest.TestCase):
    def setUp(self):
        self.cache = deepcopy(server.GEOCODE_CACHE)
        server.GEOCODE_CACHE.clear()
        self.addCleanup(lambda: (server.GEOCODE_CACHE.clear(), server.GEOCODE_CACHE.update(self.cache)))
        self.requests = []

    def response(self, request, **kwargs):
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        self.requests.append(params)
        self.assertNotIn("countrycodes", params)
        return io.BytesIO(json.dumps(FIXTURE["queries"][params["q"][0]]).encode())

    def test_airport_names_resolve_to_the_main_aerodrome(self):
        with patch("server.urllib.request.urlopen", side_effect=self.response):
            for query in FIXTURE["queries"]:
                with self.subTest(query=query):
                    lat, lon, label, _ = agent_mcp._resolve_origin({"place": query}, server)
                    if query == "Barcelona airport":
                        self.assertIn("Barcelona-el Prat", label or "")
                        self.assertAlmostEqual(lat, 41.3015291, delta=0.1)
                    else:
                        self.assertIn("Manises", label or "")
                        self.assertAlmostEqual(lat, 39.4881796, places=5)
                        self.assertAlmostEqual(lon, -0.4813204, places=5)
        self.assertTrue(all(int(p["limit"][0]) > 1 for p in self.requests))

    def test_airport_query_rejects_shop_road_and_transit_matches(self):
        wrong = [FIXTURE["queries"]["Valencia airport"][0],
                 FIXTURE["queries"]["aeroport de Valencia"][0],
                 {"name": "Airport", "display_name": "Airport Road", "class": "highway",
                  "type": "residential", "lat": "39.4", "lon": "-0.4", "importance": 1}]
        with patch("server.urllib.request.urlopen", return_value=io.BytesIO(json.dumps(wrong).encode())):
            with self.assertRaises(agent_mcp.ToolInputError):
                agent_mcp._resolve_origin({"place": "Valencia airport"}, server)

    def test_cached_results_honor_each_callers_limit(self):
        with patch("server.urllib.request.urlopen", side_effect=self.response) as fetch:
            one = server.geocode_search("Valencia airport", limit=1)
            many = server.geocode_search("Valencia airport", limit=5)
            again = server.geocode_search("Valencia airport", limit=1)
        self.assertEqual(len(one), 1)
        self.assertGreater(len(many), 1)
        self.assertEqual(again, one)
        self.assertEqual(fetch.call_count, 1)

    def test_non_airport_query_preserves_provider_order(self):
        raw = FIXTURE["queries"]["Valencia airport"]
        with patch("server.urllib.request.urlopen", return_value=io.BytesIO(json.dumps(raw).encode())):
            rows = server.geocode_search("Valencia", limit=2)
        self.assertEqual([r["name"] for r in rows], [r["name"] for r in raw[:2]])

    def test_airport_ranking_accepts_legacy_class_field(self):
        raw = deepcopy(FIXTURE["queries"]["Valencia airport"])
        for row in raw:
            row["class"] = row.pop("category")
        with patch("server.urllib.request.urlopen", return_value=io.BytesIO(json.dumps(raw).encode())):
            rows = server.geocode_search("Valencia airport", limit=1)
        self.assertIn("Manises", rows[0]["label"])
