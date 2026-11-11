"""Destination search regressions from the owner's 5 November 2026 Tenerife reports.

Nominatim responses are recorded, never fetched: the point is what Volta does when the
geocoder cannot name a destination, not whether OpenStreetMap has changed.
"""
from __future__ import annotations

import io
import json
import unittest
import urllib.parse
from copy import deepcopy
from unittest.mock import patch

import agent_mcp
import server
from tests import _support  # noqa: E402


def setUpModule():
    _support.isolate_community_store()


VITHAS = (28.4714743, -16.2591622)
SANTA_CRUZ = {
    "name": "Santa Cruz de Tenerife", "display_name": "Santa Cruz de Tenerife, Canarias, España",
    "lat": "28.4671780", "lon": "-16.2507843", "category": "boundary", "type": "administrative",
    "addresstype": "city", "place_rank": 16,
    "boundingbox": ["28.4097307", "28.6053951", "-16.3458288", "-16.1193833"],
}
SANTA_CRUZ_PROVINCE = {**SANTA_CRUZ, "addresstype": "province", "place_rank": 10,
                       "lat": "28.2935785", "lon": "-16.6214471"}
TENERIFE_ISLAND = {
    "name": "Tenerife", "display_name": "Tenerife, Canarias, España", "lat": "28.2935785", "lon": "-16.6214471",
    "category": "boundary", "type": "political", "addresstype": "island", "place_rank": 17,
}


class DestinationGeocoderTests(unittest.TestCase):
    def setUp(self) -> None:
        cache = deepcopy(server.GEOCODE_CACHE)
        server.GEOCODE_CACHE.clear()
        self.addCleanup(lambda: (server.GEOCODE_CACHE.clear(), server.GEOCODE_CACHE.update(cache)))
        self.responses = {}
        self.asked = []
        sleeper = patch("server.time.sleep")
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)

    def _upstream(self, request, **_kwargs):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)["q"][0]
        self.asked.append(query)
        return io.BytesIO(json.dumps(self.responses.get(query, [])).encode())

    def search(self, query, **kwargs):
        with patch("server.urllib.request.urlopen", side_effect=self._upstream):
            return server.geocode_search(query, **kwargs)

    def test_vithas_aliases_resolve_to_the_santa_cruz_hospital_without_a_network_call(self):
        for query in ("Hospital Vithas, Santa Cruz de Tenerife", "hospital vithas santa cruz de tenerife",
                      "HOSPITAL VITHAS TENERIFE", "Vithas Santa Cruz", "Vithas, Tenerife",
                      "Hospital Vithas Santa Cruz"):
            with self.subTest(query=query):
                [row] = self.search(query)
                self.assertEqual((row["lat"], row["lon"]), VITHAS)
                self.assertIn("Enrique Wolfson", row["label"])
                self.assertEqual(row["match"], "alias")
        self.assertEqual(self.asked, [])

    def test_alias_results_are_private_copies(self):
        [row] = self.search("Vithas Tenerife")
        row["lat"] = 0
        self.assertEqual(self.search("Vithas Tenerife")[0]["lat"], VITHAS[0])

    def test_chain_name_alone_stays_with_the_geocoder(self):
        madrid = {"name": "Hospital Vithas Madrid Aravaca", "display_name": "Hospital Vithas Madrid Aravaca, Madrid",
                  "lat": "40.4672771", "lon": "-3.783539", "type": "hospital"}
        self.responses["Hospital Vithas"] = [madrid]
        self.assertEqual(self.search("Hospital Vithas")[0]["name"], "Hospital Vithas Madrid Aravaca")

    def test_alias_does_not_catch_a_longer_query(self):
        self.search("Calle Vithas Santa Cruz 12")
        self.assertEqual(self.asked, ["Calle Vithas Santa Cruz 12"])

    def test_unknown_place_in_a_named_town_falls_back_to_that_town_and_says_so(self):
        self.responses["Santa Cruz de Tenerife"] = [SANTA_CRUZ_PROVINCE, SANTA_CRUZ]
        [row] = self.search("Clínica Inventada, Santa Cruz de Tenerife")
        self.assertAlmostEqual(row["lat"], 28.467178, places=5)
        self.assertEqual(row["match"], "town")
        self.assertIn("Clínica Inventada", row["label"])
        self.assertIn("not found", row["label"])
        self.sleep.assert_called_once()

    def test_fallback_never_lands_on_an_island_province_or_country(self):
        self.responses["Tenerife"] = [TENERIFE_ISLAND]
        self.responses["Santa Cruz de Tenerife"] = [SANTA_CRUZ_PROVINCE]
        self.assertEqual(self.search("Clínica Inventada, Tenerife"), [])
        self.assertEqual(self.search("Clínica Inventada, Santa Cruz de Tenerife"), [])

    def test_a_plain_miss_and_an_airport_miss_make_no_second_request(self):
        self.assertEqual(self.search("Nowhere Special"), [])
        self.assertEqual(self.search("Aeropuerto de Nada, Tenerife"), [])
        self.assertEqual(len(self.asked), 2)
        self.sleep.assert_not_called()

    def test_a_found_place_makes_no_fallback_request(self):
        self.responses["Puerto de la Cruz, Tenerife"] = [{
            "name": "Puerto de la Cruz", "display_name": "Puerto de la Cruz, Santa Cruz de Tenerife, Canarias, 38400, España",
            "lat": "28.4159024", "lon": "-16.5532956", "addresstype": "town"}]
        self.assertEqual(self.search("Puerto de la Cruz, Tenerife")[0]["name"], "Puerto de la Cruz")
        self.assertEqual(self.asked, ["Puerto de la Cruz, Tenerife"])
        self.sleep.assert_not_called()

    def test_fallback_upstream_failure_is_a_clean_miss(self):
        def upstream(request, **_kwargs):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)["q"][0]
            if query == "Santa Cruz de Tenerife":
                raise TimeoutError("nominatim")
            return io.BytesIO(b"[]")
        with patch("server.urllib.request.urlopen", side_effect=upstream):
            self.assertEqual(server.geocode_search("Clínica Inventada, Santa Cruz de Tenerife"), [])
        # A failed fallback is not an answer: the next search asks again and finds the town.
        self.responses["Santa Cruz de Tenerife"] = [SANTA_CRUZ]
        [row] = self.search("Clínica Inventada, Santa Cruz de Tenerife")
        self.assertEqual(row["match"], "town")

    def test_a_genuine_fallback_miss_is_cached(self):
        self.responses["Tenerife"] = [TENERIFE_ISLAND]
        self.assertEqual(self.search("Clínica Inventada, Tenerife"), [])
        self.assertEqual(self.search("Clínica Inventada, Tenerife"), [])
        self.assertEqual(self.asked, ["Clínica Inventada, Tenerife", "Tenerife"])

    def test_mcp_origin_uses_the_alias_and_reports_it(self):
        lat, lon, label, origin = agent_mcp._resolve_origin({"place": "Hospital Vithas, Santa Cruz de Tenerife"}, server)
        self.assertEqual((lat, lon), VITHAS)
        self.assertIn("Hospital Vithas Tenerife", label or "")
        self.assertEqual(origin, "place")


if __name__ == "__main__":
    unittest.main()
