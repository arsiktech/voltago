"""Fixture tests for the Place to Plug, MOT (Reus/Tarragona), PlugShare and P-APP connectors.

Fixtures under tests/fixtures/three_connectors are trimmed copies of the real public
payloads fetched on 2026-10-12 (Place to Plug GraphQL page, Reus GeoJSON layer, Tarragona
CSV, PlugShare nearby/detail, Interparking finder car parks). Stdlib unittest only, no
network: the only socket used is a loopback server on 127.0.0.1.
"""

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import ingest_sources as ing  # noqa: E402
import source_transport as transport  # noqa: E402
from station_identity import identity_keys  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "three_connectors"


def load_json(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def fake_artifact(cache_name, fetched_at="2026-10-12T08:00:00Z"):
    return ing.FetchArtifact(body=b"{}", url="https://example.invalid/" + cache_name, cache_name=cache_name,
                             fetched_at=fetched_at, age_seconds=0.0, hit=False, fallback=False, stale=False)


# --------------------------------------------------------------------------------------
# Place to Plug
# --------------------------------------------------------------------------------------
class PlaceToPlugParseTests(unittest.TestCase):
    def setUp(self):
        self.zones = load_json("ptp_page.json")["data"]["findChargingZones"]["chargingZones"]

    def test_coordinates_are_lon_lat_and_identity_is_the_zone_id(self):
        rows = ing.parse_place_to_plug_zones(self.zones)
        by_id = {r["raw"]["ptp_zone_id"]: r for r in rows}
        bellvitge = by_id["654384e37194eb2747da322e"]
        self.assertAlmostEqual(bellvitge["lat"], 41.35182905577839)
        self.assertAlmostEqual(bellvitge["lon"], 2.105032813490526)
        self.assertEqual(bellvitge["source"], ing.PTP_SOURCE)
        self.assertIn((ing.PTP_SOURCE, "ptp_zone_id", "654384e37194eb2747da322e"), identity_keys(bellvitge))

    def test_a_lat_lon_swapped_pair_never_becomes_a_spanish_pin(self):
        swapped = [{**self.zones[0], "coordinates": [41.35182905577839, 2.105032813490526]}]
        self.assertEqual(ing.parse_place_to_plug_zones(swapped), [])

    def test_power_range_status_and_unknown_price(self):
        rows = {r["raw"]["ptp_zone_id"]: r for r in ing.parse_place_to_plug_zones(self.zones)}
        a = rows["654384e37194eb2747da322e"]
        self.assertEqual(a["max_power_kw"], 90)
        self.assertEqual(a["raw"]["power_min_kw"], 22)
        self.assertIsNone(a["price_min"])
        self.assertFalse(a["is_free"])
        self.assertEqual(a["price_claim_type"], "price_unknown_lead")
        self.assertEqual(a["connector_labels"], [])
        self.assertEqual(a["raw"]["availability"], "available")
        self.assertEqual(a["raw"]["ptp_status_raw"], "AVAILABLE")
        self.assertEqual(a["status"], "VERIFY", "a directory snapshot is never live availability")
        self.assertEqual(rows["634e0963f59d740025d74f5e"]["raw"]["availability"], "unknown")
        belmonte = rows["654384e37194eb2747da3230"]
        self.assertIsNone(belmonte["max_power_kw"])
        self.assertFalse(belmonte["fast"])

    def test_unrecognised_status_passes_through_as_unknown_availability(self):
        zone = {**self.zones[0], "id": "x1", "status": "SOMETHING_NEW"}
        row = ing.parse_place_to_plug_zones([zone])[0]
        self.assertEqual(row["raw"]["availability"], "unknown")
        self.assertEqual(row["raw"]["ptp_status_raw"], "SOMETHING_NEW")
        self.assertNotIn("source_reported_unavailable", row)

    def test_source_reported_outage_is_retained_not_dropped(self):
        zone = {**self.zones[0], "id": "x2", "status": "OUTOFORDER"}
        row = ing.parse_place_to_plug_zones([zone])[0]
        self.assertEqual(row["source_reported_unavailable"]["field"], "status")
        self.assertEqual(row["source_reported_unavailable"]["value"], "OUTOFORDER")
        self.assertEqual(row["raw"]["availability"], "unknown")

    def test_other_countries_and_bad_rows_are_skipped(self):
        portugal = {**self.zones[0], "id": "p1", "coordinates": [-9.14, 38.72], "address": {**self.zones[0]["address"], "country": "Portugal"}}
        france = {**self.zones[0], "id": "f1", "coordinates": [2.35, 48.85], "address": {**self.zones[0]["address"], "country": "France"}}
        bad = [None, "x", {"id": "n1"}, {**self.zones[0], "id": "n2", "coordinates": [1]}, {**self.zones[0], "id": None}]
        self.assertEqual(ing.parse_place_to_plug_zones([portugal, france] + bad), [])

    def test_row_has_no_station_page_invented(self):
        row = ing.parse_place_to_plug_zones(self.zones)[0]
        self.assertEqual(row["source_url"], ing.PTP_MAP_PAGE_URL)


class PlaceToPlugWalkTests(unittest.TestCase):
    def _zone(self, zid, lon=-3.7, lat=40.4):
        return {"id": zid, "name": f"Z{zid}", "coordinates": [lon, lat], "address": {"street": "C/ X", "city": "Madrid", "country": "Spain"},
                "powerRange": {"min": 7, "max": 22}, "status": "UNKNOWN"}

    def _run(self, pages, *, budget):
        """pages: {(box_index, page): zones}; records every POST body."""
        bodies = []

        def fake_cached_json(url, cache_name, ttl_hours=24, *, with_artifact=False, **kw):
            body = json.loads(kw["data"].decode())
            bodies.append((cache_name, body))
            q = body["variables"]["query"]
            idx = len(set(c for c, _ in bodies if c.split("_p")[0] == cache_name.split("_p")[0])) - 1
            box_index = int(cache_name.split("box")[1].split("_")[0])
            zones = pages.get((box_index, q["page"]), [])
            payload = {"data": {"findChargingZones": {"totalCount": 99, "hasNextPage": (box_index, q["page"] + 1) in pages, "chargingZones": zones}}}
            artifact = fake_artifact(cache_name)
            return (payload, artifact) if with_artifact else payload

        records = []
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ing, "CACHE", Path(tmp)), \
                mock.patch.object(ing, "cached_json", fake_cached_json), \
                mock.patch.object(ing, "PTP_MIN_ROWS", 1), \
                mock.patch.object(ing, "PTP_PAGE_PAUSE_SECONDS", 0), \
                mock.patch.dict(os.environ, {"VOLTA_PTP_PAGE_BUDGET": str(budget)}):
            ing.ADAPTER_STATUS.clear()
            ing.source_place_to_plug(records)
        return records, bodies

    def test_walk_is_paginated_at_limit_50_with_lon_lat_bounds_and_dedupes_ids(self):
        pages = {(0, 1): [self._zone("a"), self._zone("b")], (0, 2): [self._zone("b"), self._zone("c")], (1, 1): [self._zone("d", lon=-16.2, lat=28.5)]}
        records, bodies = self._run(pages, budget=50)
        self.assertEqual(sorted(r["raw"]["ptp_zone_id"] for r in records), ["a", "b", "c", "d"])
        for _name, body in bodies:
            q = body["variables"]["query"]
            self.assertEqual(q["limit"], 50, "the API silently falls back to 10 rows above 50")
            self.assertEqual(body["query"], transport.PLACETOPLUG_QUERY)
        south, west, north, east = ing.DIRECTORY_SPAIN_LIST_BOXES[0]
        first = bodies[0][1]["variables"]["query"]["filters"]["bounds"]
        self.assertEqual(first, {"minLat": south, "maxLat": north, "minLon": west, "maxLon": east})
        status = ing.ADAPTER_STATUS["source_place_to_plug"]
        self.assertTrue(status["complete"])
        self.assertEqual(status["rows"], 4)

    def test_budget_exhaustion_leaves_a_visible_partial_walk(self):
        pages = {(0, p): [self._zone(f"z{p}")] for p in range(1, 6)}
        pages[(1, 1)] = [self._zone("c1", lon=-16.2, lat=28.5)]
        records, bodies = self._run(pages, budget=2)
        self.assertEqual(len(bodies), 2)
        status = ing.ADAPTER_STATUS["source_place_to_plug"]
        self.assertFalse(status["complete"])
        self.assertEqual(status["network_fetches"], 2)


class PlaceToPlugTransportTests(unittest.TestCase):
    def setUp(self):
        transport.reset_budgets()

    def _request(self, body=None, url=transport.PLACETOPLUG_URL, headers=None):
        payload = body if body is not None else {
            "operationName": transport.PLACETOPLUG_OPERATION,
            "variables": {"query": {"filters": {"bounds": {"minLat": 41.3, "maxLat": 41.4, "minLon": 2.0, "maxLon": 2.2}},
                                    "params": {"location": {"latitude": 41.35, "longitude": 2.1}}, "limit": 50, "page": 1}},
            "query": transport.PLACETOPLUG_QUERY,
        }
        data = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
        h = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "x"}
        h.update(headers or {})
        return urllib.request.Request(url, data=data, headers=h, method="POST")

    def test_exact_graphql_query_is_the_only_allowed_post(self):
        self.assertEqual(transport.classify_post_exception(self._request()), "placetoplug-zones-query")

    def test_exact_host_path_scheme_and_no_query_string(self):
        for url in (
            "https://api.placetoplug.com/v2/graphql?x=1",
            "https://api.placetoplug.com/v2/other",
            "https://api.placetoplug.com/v2/graphql/",
            "http://api.placetoplug.com/v2/graphql",
            "https://placetoplug.com/v2/graphql",
            "https://api.placetoplug.com.evil.test/v2/graphql",
            "https://api.placetoplug.com:8443/v2/graphql",
            "https://user:pw@api.placetoplug.com/v2/graphql",
        ):
            with self.assertRaises(transport.SourcePolicyBlocked, msg=url):
                transport.classify_post_exception(self._request(url=url))

    def test_a_different_query_or_mutation_is_refused(self):
        base = json.loads(self._request().data.decode())
        for query in ("mutation { deleteEverything }", transport.PLACETOPLUG_QUERY + " ", "query { me { id } }"):
            with self.assertRaises(transport.SourcePolicyBlocked, msg=query):
                transport.classify_post_exception(self._request({**base, "query": query}))

    def test_extra_body_keys_and_unreviewed_variables_are_refused(self):
        base = json.loads(self._request().data.decode())
        variants = [
            {**base, "extensions": {}},
            {**base, "operationName": "other"},
            {**base, "variables": {"query": {**base["variables"]["query"], "limit": 51}}},
            {**base, "variables": {"query": {**base["variables"]["query"], "limit": 0}}},
            {**base, "variables": {"query": {**base["variables"]["query"], "page": 0}}},
            {**base, "variables": {"query": {**base["variables"]["query"], "extra": 1}}},
            {**base, "variables": {"query": {**base["variables"]["query"], "filters": {"bounds": {"minLat": "x"}}}}},
            {**base, "variables": {"other": 1}},
        ]
        for body in variants:
            with self.assertRaises(transport.SourcePolicyBlocked, msg=str(body)[:120]):
                transport.classify_post_exception(self._request(body))
        for raw in (b"", b"not json", b"[]", b"x" * 5000):
            with self.assertRaises(transport.SourcePolicyBlocked):
                transport.classify_post_exception(self._request(raw))

    def test_no_credentials_and_no_unreviewed_headers(self):
        for header in ("Authorization", "Cookie", "X-Api-Key", "Proxy-Authorization"):
            with self.assertRaises(transport.SourcePolicyBlocked, msg=header):
                transport.classify_post_exception(self._request(headers={header: "secret"}))
        with self.assertRaises(transport.SourcePolicyBlocked):
            transport.classify_post_exception(self._request(headers={"X-Custom-Thing": "1"}))
        with self.assertRaises(transport.SourcePolicyBlocked):
            transport.classify_post_exception(self._request(headers={"Content-Type": "text/plain"}))
        transport.classify_post_exception(self._request(headers={"Platform": "WEB"}))

    def test_get_and_other_methods_on_the_endpoint_are_not_exceptions(self):
        with self.assertRaises(transport.SourcePolicyBlocked):
            transport.classify_post_exception(urllib.request.Request(transport.PLACETOPLUG_URL, method="GET"))
        with self.assertRaises(transport.SourcePolicyBlocked):
            transport.classify_post_exception(urllib.request.Request(transport.PLACETOPLUG_URL, data=b"{}", method="PUT"))

    def test_budget_is_bounded_and_the_family_has_its_own_entry(self):
        self.assertIn("placetoplug-zones-query", transport.POST_BUDGETS)
        window, limit = transport.POST_BUDGETS["placetoplug-zones-query"]
        self.assertGreater(limit, 600, "a national walk needs ~580 pages")
        for _ in range(limit):
            transport._charge_budget("placetoplug-zones-query", now=1.0)
        with self.assertRaises(transport.SourcePolicyBlocked):
            transport._charge_budget("placetoplug-zones-query", now=1.0)
        transport.reset_budgets()

    def test_four_post_families_are_documented(self):
        self.assertEqual(len(transport.POST_BUDGETS), 4)
        self.assertIn("exactly four", (transport.__doc__ or ""))
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("four owner-approved read-only data queries", agents.replace("\n  ", " "))
        self.assertNotIn("three owner-approved read-only data queries", agents.replace("\n  ", " "))


# --------------------------------------------------------------------------------------
# MOT (Aparcar): Reus + Tarragona
# --------------------------------------------------------------------------------------
class MotReusTests(unittest.TestCase):
    def setUp(self):
        self.payload = load_json("reus_carregadors_municipals.geojson")

    def test_thirteen_municipal_features_become_thirteen_rows(self):
        rows = ing.parse_mot_reus_geojson(self.payload)
        self.assertEqual(len(rows), 13)
        self.assertTrue(all(r["source"] == ing.MOT_REUS_SOURCE for r in rows))

    def test_endolls_count_is_the_plug_count_and_power_is_unknown(self):
        rows = {r["raw"]["reus_feature_id"]: r for r in ing.parse_mot_reus_geojson(self.payload)}
        pavello = rows["1345"]
        self.assertEqual(pavello["name"], "Pàrquing superfície PAVELLO")
        self.assertEqual(pavello["total_evse"], 2)
        self.assertEqual(pavello["connector_count"], 2)
        self.assertEqual(pavello["raw"]["plugs"], 2)
        self.assertIsNone(pavello["max_power_kw"], "endolls count only: no power may be invented")
        self.assertFalse(pavello["fast"])
        self.assertEqual(pavello["connector_labels"], [])
        pastoreta = rows["1347"]
        self.assertEqual(pastoreta["total_evse"], 6)
        self.assertEqual(pastoreta["source_url"], "https://reusmobilitat.cat/aparcament/aparcament-pastoreta/")
        self.assertTrue(all(r["source_url"].startswith("https://") and "\n" not in r["source_url"] for r in rows.values()))

    def test_coordinates_are_lon_lat_and_identity_key_exists(self):
        row = ing.parse_mot_reus_geojson(self.payload)[0]
        self.assertAlmostEqual(row["lon"], 1.112125)
        self.assertAlmostEqual(row["lat"], 41.139994)
        self.assertIn((ing.MOT_REUS_SOURCE, "reus_feature_id", "1345"), identity_keys(row))

    def test_price_is_unknown_and_reliability_is_official_location_lead(self):
        for row in ing.parse_mot_reus_geojson(self.payload):
            self.assertIsNone(row["price_min"])
            self.assertFalse(row["is_free"])
            self.assertEqual(row["reliability"], "official-charger-location-price-unknown")

    def test_only_the_municipal_layer_belongs_to_mot(self):
        privat = json.loads(json.dumps(self.payload["features"][0]))
        privat["properties"]["layer"] = "Carregadors privats"
        privat["properties"]["name"] = "Third party"
        privat["id"] = privat["properties"]["id"] = 9999
        mixed = {**self.payload, "features": self.payload["features"] + [privat]}
        rows = ing.parse_mot_reus_geojson(mixed)
        self.assertEqual(len(rows), 13)
        self.assertNotIn("Third party", [r["name"] for r in rows])

    def test_missing_plug_count_stays_unknown(self):
        feature = json.loads(json.dumps(self.payload["features"][0]))
        feature["properties"]["info"] = None
        feature["id"] = feature["properties"]["id"] = 4242
        row = ing.parse_mot_reus_geojson({"features": [feature]})[0]
        self.assertIsNone(row["total_evse"])
        self.assertEqual(row["connector_labels"], [])

    def test_attribution_and_provenance(self):
        row = ing.parse_mot_reus_geojson(self.payload)[0]
        self.assertIn("Reus", row["source"])
        self.assertIn("MOT", row["source"])
        self.assertIn("no licence", row["sources"][0]["evidence"].lower())


TGN_COORDS = {  # (lat, lon) of the six real Tarragona CSV rows; all are Etecnic feed ids too.
    "Tarragona - SELBA EdRSR 1 Anella Olímpica E": (41.1221615, 1.2069723, 88),
    "Tarragona - SELBA EdRSR 2 Anella Olímpica D": (41.1221246, 1.2070494, 89),
    "Tarragona - Selba EdRSR 3 Tabacalera": (41.113861, 1.241051, 195),
    "Tarragona - Selba EdRSR 4 Gavarres": (41.129986, 1.200249, 224),
    "Tarragona - LAFON EdRR 1 Arrabassada": (41.120799, 1.272016, 227),
    "Tarragona - LAFON EdRR 2 Imperial": (41.119168, 1.246108, 241),
}


def etecnic_rows_for_tarragona(names=None):
    """Etecnic feed rows at the very same coordinates; names differ for LAFON (EdRR 5/6)."""
    rows = []
    for name, (lat, lon, eid) in TGN_COORDS.items():
        shown = name.replace("LAFON EdRR 1", "LAFON EdRR 5").replace("LAFON EdRR 2", "LAFON EdRR 6")
        rows.append({"id": eid, "name": shown, "lat": str(lat), "lon": str(lon), "show_map": True,
                     "charger_sockets": [{"id": eid * 10, "status": 0, "connector_type_id": 2}]})
    return rows


class MotTarragonaTests(unittest.TestCase):
    def setUp(self):
        self.csv_bytes = (FIX / "tarragona_03303.csv").read_bytes()

    def test_csv_parses_six_rows_with_status_and_unknown_power(self):
        rows = ing.parse_mot_tarragona_csv(self.csv_bytes)
        self.assertEqual(len(rows), 6)
        by_name = {r["name"]: r for r in rows}
        olimpica = by_name["Tarragona - SELBA EdRSR 1 Anella Olímpica E"]
        self.assertAlmostEqual(olimpica["lat"], 41.1221615)
        self.assertAlmostEqual(olimpica["lon"], 1.2069723)
        self.assertIsNone(olimpica["max_power_kw"], "AMPERATGE is amperes, never kW")
        self.assertEqual(olimpica["raw"]["amperage_raw"], "32.0")
        self.assertEqual(olimpica["raw"]["status_raw"], "Out of service")
        self.assertEqual(olimpica["source_reported_unavailable"]["value"], "Out of service")
        self.assertNotIn("source_reported_unavailable", by_name["Tarragona - SELBA EdRSR 2 Anella Olímpica D"])
        self.assertEqual(by_name["Tarragona - SELBA EdRSR 2 Anella Olímpica D"]["raw"]["status_raw"], "Available")
        self.assertEqual(olimpica["source"], ing.MOT_TARRAGONA_SOURCE)
        self.assertIn("Ajuntament de Tarragona open data", olimpica["source"])
        self.assertTrue(all(r["price_min"] is None for r in rows))

    def test_rows_outside_spain_or_without_coordinates_are_dropped(self):
        bad = self.csv_bytes + b'"Nowhere","Available","32.0","x","00000","0.0","0.0","1","X"\r\n"Blank","Available","","x","1","","","1","X"\r\n'
        self.assertEqual(len(ing.parse_mot_tarragona_csv(bad)), 6)

    def test_existing_etecnic_rows_get_a_cross_reference_not_a_duplicate(self):
        etecnic = ing.parse_etecnic_chargers(etecnic_rows_for_tarragona())
        self.assertEqual(len(etecnic), 6)
        before_ids = [r["id"] for r in etecnic]
        records = list(etecnic)
        added, crossref = ing.apply_mot_tarragona(records, ing.parse_mot_tarragona_csv(self.csv_bytes))
        self.assertEqual((added, crossref), (0, 6))
        self.assertEqual(len(records), 6, "no duplicate Tarragona record may be created")
        self.assertEqual([r["id"] for r in records], before_ids, "existing station ids are never re-keyed")
        for row in records:
            names = [s["name"] for s in row["sources"]]
            self.assertIn(ing.MOT_TARRAGONA_SOURCE, names)
            self.assertEqual(row["source"], ing.ETECNIC_SOURCE)
            self.assertIn("mot_crossref", row["raw"])
        out_of_service = [r for r in records if r.get("source_reported_unavailable")]
        self.assertEqual(len(out_of_service), 2)
        self.assertEqual({r["raw"]["etecnic_charger_id"] for r in out_of_service}, {88, 227})

    def test_a_tarragona_row_with_no_etecnic_neighbour_is_added_on_its_own(self):
        records = []
        added, crossref = ing.apply_mot_tarragona(records, ing.parse_mot_tarragona_csv(self.csv_bytes))
        self.assertEqual((added, crossref), (6, 0))
        self.assertEqual(len(records), 6)

    def test_nearest_etecnic_row_wins_the_cross_reference(self):
        etecnic = ing.parse_etecnic_chargers(etecnic_rows_for_tarragona())
        records = list(etecnic)
        ing.apply_mot_tarragona(records, ing.parse_mot_tarragona_csv(self.csv_bytes))
        d = next(r for r in records if r["raw"]["etecnic_charger_id"] == 89)
        self.assertEqual(d["raw"]["mot_crossref"]["nom"], "Tarragona - SELBA EdRSR 2 Anella Olímpica D")

    def test_existing_dedupe_collapses_ungated_mot_rows_into_the_etecnic_pins(self):
        etecnic = ing.parse_etecnic_chargers(etecnic_rows_for_tarragona())
        mot = ing.parse_mot_tarragona_csv(self.csv_bytes)
        merged, _ = ing.merge_cross_source_duplicates(ing.dedupe(etecnic + mot))
        self.assertEqual(len(merged), 6, "exact-coordinate merge already prevents double counting")
        for pin in merged:
            keys = identity_keys(pin)
            self.assertTrue(any(k[1] == "etecnic_charger_id" for k in keys), pin["id"])
            self.assertTrue(any(k[1] == "tarragona_nom" for k in keys), pin["id"])


# --------------------------------------------------------------------------------------
# PlugShare
# --------------------------------------------------------------------------------------
class PlugShareParseTests(unittest.TestCase):
    def setUp(self):
        self.nearby = load_json("plugshare_nearby.json")
        self.detail = load_json("plugshare_location_226433.json")
        # The nearby record that pairs with the detail fixture (same location id and coordinates).
        self.paired = {"id": 226433, "name": "X-Madrid", "latitude": self.detail["latitude"], "longitude": self.detail["longitude"],
                       "connector_types": ["Type 2"], "stations": [], "score": 7.0}

    def test_nearby_record_keeps_named_connectors_and_unknown_price(self):
        item = self.nearby[0]
        row = ing.parse_plugshare_location(item, None)[0]
        self.assertEqual(row["source"], ing.PLUGSHARE_SOURCE)
        self.assertEqual(row["raw"]["plugshare_location_id"], str(item["id"]))
        self.assertAlmostEqual(row["lat"], item["latitude"])
        self.assertAlmostEqual(row["lon"], item["longitude"])
        self.assertIsNone(row["price_min"])
        self.assertFalse(row["is_free"])
        self.assertEqual(row["reliability"], "directory-page")
        self.assertEqual(row["source_url"], f"https://www.plugshare.com/location/{item['id']}")
        self.assertIn((ing.PLUGSHARE_SOURCE, "plugshare_location_id", str(item["id"])), identity_keys(row))
        for label in row["connector_labels"]:
            self.assertIn(label, {"Type 2", "CCS2", "CHAdeMO", "Schuko", "Type 1", "Tesla"})

    def test_named_connector_types_map_without_inventing_counts(self):
        item = {"id": 58641, "name": "EdRR Passeig de Gràcia", "address": "Passeig de Gràcia 1", "latitude": 41.388248, "longitude": 2.169679,
                "connector_types": ["CCS2", "CHAdeMO", "Type 2", "Wall (Euro)", "J-1772", "Mystery"], "station_count": 1, "is_fast_charger": True,
                "under_repair": False, "coming_soon": False, "score": 6.2,
                "stations": [{"id": 1, "outlets": [{"connector": 7, "kilowatts": 50.0}, {"connector": 20, "kilowatts": 50.0}, {"connector": 3, "kilowatts": None}]}]}
        row = ing.parse_plugshare_location(item, None)[0]
        self.assertEqual(row["connector_labels"], ["CCS2", "CHAdeMO", "Schuko", "Type 1", "Type 2"])
        self.assertEqual(row["connectors"], [], "nearby gives type names, not per-plug counts")
        self.assertIsNone(row["total_evse"])
        self.assertEqual(row["raw"]["connector_types"], item["connector_types"])
        self.assertEqual(row["max_power_kw"], 50.0)
        self.assertTrue(row["fast"])

    def test_cost_false_is_a_community_free_claim_never_an_official_tariff(self):
        row = ing.parse_plugshare_location(self.paired, self.detail)[0]
        self.assertEqual(self.detail["cost"], False)
        self.assertEqual(row["price_min"], 0.0)
        self.assertEqual(row["reliability"], "community-free")
        self.assertEqual(row["raw"]["cost_flag"], False)
        self.assertIn("community", row["free_condition"].lower())
        self.assertIn("not an official tariff", row["free_condition"].lower())
        self.assertEqual(row["charge_claim"]["type"], "free")
        self.assertEqual(row["raw"]["community_free_claim"], True)

    def test_cost_true_or_missing_never_makes_a_price(self):
        paid = {**self.detail, "cost": True, "cost_description": "0,35 €/kWh"}
        row = ing.parse_plugshare_location(self.paired, paid)[0]
        self.assertIsNone(row["price_min"])
        self.assertEqual(row["raw"]["cost_description"], "0,35 €/kWh")
        self.assertEqual(row["reliability"], "directory-page")
        unknown = {k: v for k, v in self.detail.items() if k != "cost"}
        self.assertIsNone(ing.parse_plugshare_location(self.paired, unknown)[0]["price_min"])

    def test_detail_gives_named_plugs_and_power_and_its_own_provenance(self):
        list_art = fake_artifact("plugshare_nearby/a.json", "2026-10-12T08:00:00Z")
        detail_art = fake_artifact("plugshare_locations/226433.json", "2026-10-01T08:00:00Z")
        row = ing.parse_plugshare_location(self.paired, self.detail, source_artifact=list_art, detail_artifact=detail_art)[0]
        self.assertEqual(row["source_fetched_at"], "2026-10-01T08:00:00Z")
        self.assertEqual(row["raw"]["list_fetched_at"], "2026-10-12T08:00:00Z")
        self.assertEqual(row["raw"]["detail_fetched_at"], "2026-10-01T08:00:00Z")
        self.assertTrue(row["raw"]["detail_fetched"])
        self.assertTrue(row["connectors"])
        self.assertTrue(all(c["label"] for c in row["connectors"]))
        self.assertTrue(all(c["status"] == "UNKNOWN" for c in row["connectors"]))

    def test_under_repair_is_retained_as_source_reported_unavailable(self):
        item = {**self.paired, "under_repair": True}
        row = ing.parse_plugshare_location(item, {**self.detail, "under_repair": True})[0]
        self.assertEqual(row["source_reported_unavailable"]["field"], "under_repair")
        self.assertFalse(row["is_free"], "an out-of-order charger is never a free recommendation")
        self.assertFalse(row["is_cheap"])

    def test_a_detail_for_another_location_never_informs_the_row(self):
        row = ing.parse_plugshare_location({**self.paired, "id": 1}, self.detail)[0]
        self.assertFalse(row["raw"]["detail_fetched"])
        self.assertIsNone(row["price_min"])

    def test_not_yet_built_disabled_and_foreign_locations_are_skipped(self):
        base = self.paired
        self.assertEqual(ing.parse_plugshare_location({**base, "coming_soon": True}, None), [])
        self.assertEqual(ing.parse_plugshare_location(base, {**self.detail, "enabled": False}), [])
        self.assertEqual(ing.parse_plugshare_location({**base, "latitude": 38.72, "longitude": -9.14}, None), [])
        self.assertEqual(ing.parse_plugshare_location({"name": "no id", "latitude": 40.4, "longitude": -3.7}, None), [])

    def test_all_six_real_madrid_records_parse(self):
        rows = []
        for item in self.nearby:
            ing.parse_plugshare_location(item, None, out=rows)
        self.assertEqual(len(rows), 6)


class PlugShareGridTests(unittest.TestCase):
    def test_grid_cells_cover_spain_only_and_are_unique(self):
        cells = ing.plugshare_grid_cells()
        self.assertEqual(len(cells), len(set(cells)))
        self.assertGreater(len(cells), 250)
        self.assertLess(len(cells), 700)
        from spain_geography import point_in_spain
        self.assertTrue(any(point_in_spain(lat, lon) for lat, lon in cells))
        self.assertFalse([c for c in cells if c[0] > 45 or c[1] > 5.5])

    def test_nearby_url_has_polite_exact_parameters(self):
        url = ing.plugshare_nearby_url(41.39, 2.17)
        self.assertTrue(url.startswith("https://api.plugshare.com/v3/locations/nearby?"))
        for part in ("access=1", "count=1000", "radius=30000", "latitude=41.39", "longitude=2.17"):
            self.assertIn(part, url)

    def test_overlapping_cells_are_deduped_by_id_and_foreign_records_dropped(self):
        a = {"id": 1, "name": "A", "latitude": 40.4, "longitude": -3.7, "connector_types": ["Type 2"], "stations": []}
        b = {"id": 2, "name": "B", "latitude": 40.41, "longitude": -3.71, "connector_types": ["CCS2"], "stations": []}
        lisbon = {"id": 3, "name": "PT", "latitude": 38.72, "longitude": -9.14, "connector_types": ["Type 2"], "stations": []}
        payloads = {}
        cells = ing.plugshare_grid_cells()[:3]
        payloads[ing.plugshare_cell_cache_name(*cells[0])] = [a, b]
        payloads[ing.plugshare_cell_cache_name(*cells[1])] = [b, lisbon]
        payloads[ing.plugshare_cell_cache_name(*cells[2])] = []
        fetched = []

        def fake_cached_json(url, cache_name, ttl_hours=24, *, with_artifact=False, **kw):
            fetched.append((url, cache_name, kw))
            artifact = fake_artifact(cache_name)
            payload = payloads.get(cache_name, [])
            return (payload, artifact) if with_artifact else payload

        records = []
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ing, "CACHE", Path(tmp)), \
                mock.patch.object(ing, "cached_json", fake_cached_json), \
                mock.patch.object(ing, "plugshare_grid_cells", lambda: cells), \
                mock.patch.object(ing, "PLUGSHARE_MIN_ROWS", 1), \
                mock.patch.object(ing, "PLUGSHARE_PAUSE_SECONDS", 0), \
                mock.patch.dict(os.environ, {"VOLTA_PLUGSHARE_NEARBY_BUDGET": "10", "VOLTA_PLUGSHARE_DETAIL_BUDGET": "0"}):
            ing.ADAPTER_STATUS.clear()
            ing.source_plugshare(records)
        self.assertEqual(sorted(r["raw"]["plugshare_location_id"] for r in records), ["1", "2"])
        self.assertEqual(len(fetched), 3)
        for _url, _name, kw in fetched:
            self.assertTrue(kw["headers"]["Authorization"].startswith("Basic "))
        status = ing.ADAPTER_STATUS["source_plugshare"]
        self.assertTrue(status["complete"])
        self.assertEqual(status["cells"], 3)

    def test_nearby_budget_limits_network_and_marks_partial(self):
        cells = ing.plugshare_grid_cells()[:4]
        fetched = []

        def fake_cached_json(url, cache_name, ttl_hours=24, *, with_artifact=False, **kw):
            fetched.append(cache_name)
            payload = [{"id": len(fetched), "name": "A", "latitude": 40.4, "longitude": -3.7, "connector_types": [], "stations": []}]
            return (payload, fake_artifact(cache_name)) if with_artifact else payload

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ing, "CACHE", Path(tmp)), \
                mock.patch.object(ing, "cached_json", fake_cached_json), \
                mock.patch.object(ing, "plugshare_grid_cells", lambda: cells), \
                mock.patch.object(ing, "PLUGSHARE_MIN_ROWS", 1), \
                mock.patch.object(ing, "PLUGSHARE_PAUSE_SECONDS", 0), \
                mock.patch.dict(os.environ, {"VOLTA_PLUGSHARE_NEARBY_BUDGET": "2", "VOLTA_PLUGSHARE_DETAIL_BUDGET": "0"}):
            ing.ADAPTER_STATUS.clear()
            ing.source_plugshare([])
        self.assertEqual(len(fetched), 2)
        self.assertFalse(ing.ADAPTER_STATUS["source_plugshare"]["complete"])
        self.assertEqual(ing.ADAPTER_STATUS["source_plugshare"]["cells_without_data"], 2)


class _Recorder(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen = []

    def do_GET(self):  # noqa: N802
        _Recorder.seen.append({"version": self.request_version, "auth": self.headers.get("Authorization"), "ua": self.headers.get("User-Agent")})
        body = b"[]"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: D401
        pass


class PlugShareTransportTests(unittest.TestCase):
    def test_default_credential_is_the_sites_public_web_client_value_and_env_overrides(self):
        self.assertEqual(ing.PLUGSHARE_DEFAULT_BASIC_AUTH, "d2ViX3YyOkVOanNuUE54NHhXeHVkODU=")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VOLTA_PLUGSHARE_BASIC_AUTH", None)
            self.assertEqual(ing.plugshare_headers()["Authorization"], "Basic d2ViX3YyOkVOanNuUE54NHhXeHVkODU=")
        with mock.patch.dict(os.environ, {"VOLTA_PLUGSHARE_BASIC_AUTH": "b3ZlcnJpZGU="}):
            self.assertEqual(ing.plugshare_headers()["Authorization"], "Basic b3ZlcnJpZGU=")

    def test_headers_are_browser_like_json_without_cookies(self):
        headers = ing.plugshare_headers()
        self.assertIn("Mozilla/5.0", headers["User-Agent"])
        self.assertEqual(headers["Accept"], "application/json")
        self.assertNotIn("Cookie", headers)

    def test_request_goes_out_as_http_1_1_with_the_basic_header(self):
        server = HTTPServer(("127.0.0.1", 0), _Recorder)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        _Recorder.seen = []
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/v3/locations/nearby"
            self.assertEqual(json.loads(ing.fetch(url, headers=ing.plugshare_headers())), [])
        finally:
            server.shutdown()
        self.assertEqual(_Recorder.seen[0]["version"], "HTTP/1.1", "h2 is WAF-blocked on this host; urllib speaks HTTP/1.1 only")
        self.assertTrue(_Recorder.seen[0]["auth"].startswith("Basic "))

    def test_plugshare_is_a_get_only_source_needing_no_post_exception(self):
        req = urllib.request.Request(ing.plugshare_nearby_url(40.4, -3.7), headers=ing.plugshare_headers())
        self.assertEqual(req.get_method(), "GET")
        with self.assertRaises(transport.SourcePolicyBlocked):
            transport.classify_post_exception(urllib.request.Request(ing.plugshare_nearby_url(40.4, -3.7), data=b"", method="POST"))


# --------------------------------------------------------------------------------------
# P-APP (Interparking)
# --------------------------------------------------------------------------------------
def flight_html(car_parks, *, split=3):
    """Mimic Next.js RSC flight data: JSON strings pushed through self.__next_f.push."""
    flight = '5e:["$","section",null,{"className":"x","children":["$","$L60",null,{"carParks":' + json.dumps(car_parks, ensure_ascii=False) + ',"other":1}]}]\n'
    size = max(1, len(flight) // split)
    chunks = [flight[i:i + size] for i in range(0, len(flight), size)]
    scripts = "".join(f"<script>self.__next_f.push([1,{json.dumps(chunk, ensure_ascii=False)}])</script>" for chunk in chunks)
    return f"<html><head></head><body><script>self.__next_f.push([0])</script>{scripts}</body></html>"


class InterparkingTests(unittest.TestCase):
    def setUp(self):
        self.parks = load_json("interparking_carparks.json")
        self.html = flight_html(self.parks)

    def test_embedded_flight_json_is_extracted_across_chunks(self):
        parsed = ing.interparking_car_parks_from_page(self.html)
        self.assertEqual([p["externalId"] for p in parsed], [p["externalId"] for p in self.parks])

    def test_only_spanish_ev_flagged_car_parks_become_rows(self):
        rows = ing.parse_interparking_page(self.html)
        self.assertEqual(sorted(r["raw"]["interparking_external_id"] for r in rows), ["es-6215", "es-6246", "es-6259"])
        ids = {r["raw"]["interparking_external_id"] for r in rows}
        self.assertNotIn("es-7372", ids, "unflagged Spanish car park has no charger claim")
        self.assertNotIn("saba-es-374", ids)

    def test_parking_price_is_parking_never_charging(self):
        row = next(r for r in ing.parse_interparking_page(self.html) if r["raw"]["interparking_external_id"] == "es-6259")
        self.assertIsNone(row["price_min"])
        self.assertEqual(row["price_claim_type"], "price_unknown_lead")
        self.assertFalse(row["is_free"])
        self.assertEqual(row["charge_claim"]["type"], "price_unknown")
        self.assertEqual(row["raw"]["parking_price"], "3,20 €", "non-breaking space is normalised")
        self.assertEqual(row["parking"]["kind"], "paid_or_metered")
        self.assertEqual(row["parking"]["cost_hint"], "3,20 €")
        self.assertIn("parking price", row["free_condition"].lower())
        self.assertEqual(row["connector_labels"], [])
        self.assertIsNone(row["max_power_kw"])

    def test_identity_url_and_coordinates(self):
        row = next(r for r in ing.parse_interparking_page(self.html) if r["raw"]["interparking_external_id"] == "es-6259")
        self.assertEqual(row["name"], "Alfonso X (Murcia)")
        self.assertAlmostEqual(row["lat"], 37.990742)
        self.assertAlmostEqual(row["lon"], -1.130104)
        self.assertEqual(row["source_url"], "https://www.interparking.es/es/aparcamientos/murcia/alfonsox/")
        self.assertIn((ing.INTERPARKING_SOURCE, "interparking_external_id", "es-6259"), identity_keys(row))
        self.assertEqual(row["reliability"], "directory-page")

    def test_only_the_exact_robots_allowed_page_is_fetched(self):
        self.assertEqual(ing.INTERPARKING_PAGE_URL, "https://www.interparking.es/es/encuentra-un-aparcamiento/")
        self.assertNotIn("?", ing.INTERPARKING_PAGE_URL)
        self.assertNotIn("/api/", ing.INTERPARKING_PAGE_URL)

    def test_missing_or_malformed_payload_fails_loudly_not_silently_empty(self):
        with self.assertRaises(RuntimeError):
            ing.interparking_car_parks_from_page("<html>no flight data</html>")
        with self.assertRaises(RuntimeError):
            ing.interparking_car_parks_from_page(flight_html([]))

    def test_adapter_uses_one_cached_get(self):
        calls = []

        def fake_cached_fetch(url, cache_name, ttl_hours=24, **kw):
            calls.append((url, cache_name, ttl_hours, kw))
            art = fake_artifact(cache_name)
            return ing.FetchArtifact(body=self.html.encode(), url=url, cache_name=cache_name, fetched_at=art.fetched_at,
                                     age_seconds=0.0, hit=False, fallback=False, stale=False)

        records = []
        with mock.patch.object(ing, "cached_fetch", fake_cached_fetch), mock.patch.object(ing, "INTERPARKING_MIN_ROWS", 1):
            ing.ADAPTER_STATUS.clear()
            ing.source_interparking_papp(records)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], ing.INTERPARKING_PAGE_URL)
        self.assertEqual(calls[0][2], 24)
        self.assertNotIn("data", calls[0][3], "GET only")
        self.assertEqual(len(records), 3)
        status = ing.ADAPTER_STATUS["source_interparking_papp"]
        self.assertEqual(status["spanish_car_parks"], 4)
        self.assertEqual(status["ev_flagged_rows"], 3)

    def test_adapter_floor_blocks_an_upstream_shape_change(self):
        def fake_cached_fetch(url, cache_name, ttl_hours=24, **kw):
            return ing.FetchArtifact(body=self.html.encode(), url=url, cache_name=cache_name, fetched_at="2026-10-12T00:00:00Z",
                                     age_seconds=0.0, hit=False, fallback=False, stale=False)
        with mock.patch.object(ing, "cached_fetch", fake_cached_fetch):
            with self.assertRaises(RuntimeError):
                ing.source_interparking_papp([])

    def test_proximity_outliers_are_not_auto_merged_with_dgt_rows(self):
        """Documented: P-APP vs DGT agree <=100 m on average, but e.g. Jerez Hospital is 4.2 km off."""
        park = ing.parse_interparking_page(flight_html([{**self.parks[0], "externalId": "es-9999", "name": "Jerez Hospital (Jerez)",
                                                         "location": {"lat": 36.70, "lng": -6.13}}]))[0]
        dgt_like = {**park, "id": "dgt-1", "source": "DGT National Access Point charging registry",
                    "source_url": "https://nap.dgt.es/", "lat": 36.7378, "lon": -6.13, "raw": {"nap_site_id": "ES*IPK*E9999"},
                    "sources": [{"name": "DGT National Access Point charging registry", "url": "https://nap.dgt.es/", "evidence": ""}]}
        _, merged = ing.merge_cross_source_duplicates([park, dgt_like])
        self.assertEqual(merged, 0)


# --------------------------------------------------------------------------------------
# Registration, wording, provenance
# --------------------------------------------------------------------------------------
class RegistrationTests(unittest.TestCase):
    def test_new_adapters_are_active_in_dependency_order(self):
        names = [fn.__name__ for fn in ing.ACTIVE_SOURCE_ADAPTERS]
        for name in ("source_place_to_plug", "source_mot_aparcar", "source_plugshare", "source_interparking_papp"):
            self.assertIn(name, names)
        self.assertLess(names.index("source_etecnic"), names.index("source_mot_aparcar"), "MOT cross-references Etecnic rows already collected")

    def test_directory_lifecycle_includes_the_new_community_directories(self):
        row = {"source": ing.PLUGSHARE_SOURCE, "name": "Charger (removed)", "raw": {}, "is_free": True, "is_cheap": True}
        ing.apply_directory_lifecycle(row)
        self.assertIn("source_reported_unavailable", row)
        self.assertFalse(row["is_free"])

    def test_data_readme_carries_the_provenance_lines(self):
        text = (ROOT / "data" / "README.md").read_text(encoding="utf-8")
        for needle in ("Place to Plug: anonymous GraphQL, verified 2026-10-12.", "Reus: municipal public GIS (no licence stated).",
                       "Tarragona: Ajuntament de Tarragona open data (CKAN).", "free flags are community claims",
                       "P-APP", "VOLTA_PLUGSHARE_BASIC_AUTH"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
