"""Operator feed coordinates can be mapped without claiming a station/start link.

Trimmed public payload shapes exercise the real parsers and runtime normalization.
No source fetching, corroborating directory pages, or invented pin geometry.
"""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import ingest_sources as ing  # noqa: E402
import server  # noqa: E402
from station_truth import classify_source_resource, primary_source_for, source_candidates  # noqa: E402


def operator_records():
    etecnic = ing.parse_etecnic_chargers([{
        "id": 1, "name": "Pescador Sud - EdRSR 1", "show_map": True,
        "lat": "41.064699", "lon": "1.0576201", "address": "Carrer Robert Gerhard, 2",
        "charger_sockets": [{"id": 1, "status": 0, "connector_type_id": 2}],
    }])[0]
    atlante = ing.parse_atlante_locations([{
        "id": "ES00056", "party_id": "ATE", "country": "ES", "site_status": "OPERATIONAL",
        "name": "Atlante - Abanto Gallarta", "city": "Abanto Zierbena", "postal_code": "48500",
        "evses": [{"evse_id": "ES*ATE*E00026*1", "status": "AVAILABLE",
                   "coordinates": {"latitude": 43.316551, "longitude": -3.073291},
                   "connectors": [{"id": "1", "standard": "IEC_62196_T2_COMBO",
                                   "power_type": "DC", "max_electric_power": 60000}]}],
    }])[0]
    zunder = ing.parse_zunder_map_page("""
        new google.maps.Marker({position: {lat: 42.71779934, lng: -4.29173329},
        title: 'Alar del Rey - Puerta de la Montaña'});
        xmlhttp.open('GET', '/wp-content/plugins/easycharger/location.php?id=4');
    """)[0]
    repsol = ing.parse_repsol_charging_points([{
        "id": "10005", "nombre": "Repsol ES, Cerdeira", "x": -3.611191, "y": 40.424397,
        "pais": "España", "localidad": "Madrid", "velocidad": ["Rápida"],
    }])[0]
    return [etecnic, atlante, zunder, repsol]


class OperatorMapVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = operator_records()
        cls.id_fields = {
            ing.ETECNIC_SOURCE: "etecnic_charger_id",
            ing.ATLANTE_SOURCE: "atlante_location_id",
            ing.ZUNDER_SOURCE: "zunder_location_id",
            ing.REPSOL_SOURCE: "repsol_point_id",
        }

    def normalize(self, record):
        return server._normalize_lead(deepcopy(record), record["source"])

    def assert_ineligible(self, record):
        self.assertNotEqual((primary_source_for(record) or {}).get("kind"), "official_data")
        normalized = self.normalize(record)
        self.assertFalse(server.source_health_map_eligible(normalized))
        self.assertFalse(normalized["verification"]["live_verified"])
        self.assertNotEqual(normalized["decision"]["level"], "go")

    def test_operator_only_parsed_records_are_visible_but_not_live_or_route_ready(self):
        self.assertEqual(len(self.records), 4)
        for record in self.records:
            with self.subTest(source=record["source"]):
                self.assertTrue(record["raw"][self.id_fields[record["source"]]])
                row = self.normalize(record)
                primary = row["primary_source"]
                self.assertEqual(primary["kind"], "official_data")
                self.assertEqual(primary["quality"], "official")
                self.assertEqual(primary["label"], "Operator map")
                self.assertEqual(primary["action"], "Open operator map")
                self.assertEqual(primary["name"], record["source"])
                self.assertEqual(primary["url"], record["source_url"])
                self.assertIn("operator map", primary["warning"])
                self.assertIn("not an exact station/start page", primary["warning"])
                self.assertIn(record["sources"][0]["evidence"], primary["evidence"])
                self.assertNotIn("score", primary)
                self.assertTrue(server.source_health_map_eligible(row))
                self.assertEqual(row["reliability"], "directory-page")
                self.assertEqual(server.source_health_tier(row), "directory")
                self.assertEqual(row["verification"]["level"], "directory")
                self.assertTrue(row["verification"]["location_verified"])
                for flag in ("live_verified", "price_verified", "route_checked"):
                    self.assertFalse(row["verification"][flag], flag)
                self.assertFalse(server.route_ready_now(row))
                self.assertNotEqual(row["decision"]["level"], "go")
                self.assertIsNone(row["price_min"])
                self.assertEqual((row["lat"], row["lon"]), (record["lat"], record["lon"]))

    def test_missing_empty_or_untyped_raw_ids_stay_hidden(self):
        for record in self.records:
            wrong_type = "1" if record["source"] == ing.ETECNIC_SOURCE else 1
            for value in (None, "", "  ", False, True, [], {}, 1.5, wrong_type):
                with self.subTest(source=record["source"], value=value):
                    row = deepcopy(record)
                    row["raw"][self.id_fields[record["source"]]] = value
                    self.assert_ineligible(row)
            for raw in ({}, None, {"id": "10005"}):
                row = deepcopy(record)
                row["raw"] = raw
                self.assert_ineligible(row)

    def test_wrong_primary_source_or_raw_identifier_family_stays_hidden(self):
        for record in self.records:
            with self.subTest(source=record["source"]):
                row = deepcopy(record)
                row["source"] = "Unrelated directory"
                self.assert_ineligible(row)
                row = deepcopy(record)
                row["raw"] = {"unrelated_location_id": "10005"}
                self.assert_ineligible(row)
                row = deepcopy(record)
                row["source"] = next(source for source in self.id_fields if source != record["source"])
                self.assert_ineligible(row)

    def test_altered_operator_urls_stay_hidden_even_with_matching_child_evidence(self):
        for record in self.records:
            for url in (record["source_url"] + "?station=1", record["source_url"] + "other/",
                        record["source_url"] + "#station", record["source_url"].replace("https:", "http:")):
                with self.subTest(source=record["source"], url=url):
                    row = deepcopy(record)
                    row["source_url"] = url
                    self.assert_ineligible(row)

    def test_invalid_or_out_of_range_coordinates_cannot_create_operator_candidate(self):
        for record in self.records:
            for field, values in (("lat", (None, "bad", float("nan"), float("inf"), 90.01, -90.01, True)),
                                  ("lon", (None, "bad", float("nan"), float("-inf"), 180.01, -180.01, False))):
                for value in values:
                    with self.subTest(source=record["source"], field=field, value=value):
                        row = deepcopy(record)
                        row[field] = value
                        self.assert_ineligible(row)

    def test_child_operator_evidence_without_matching_record_provenance_stays_hidden(self):
        for record in self.records:
            row = deepcopy(record)
            row["source"] = "Unrelated directory"
            row["source_url"] = "https://example.org/chargers/"
            row["raw"] = {}
            self.assert_ineligible(row)

    def test_generic_url_classification_and_source_candidate_contract_are_unchanged(self):
        for record in self.records:
            candidate = source_candidates(record)[0]
            self.assertEqual(set(candidate), {"name", "url", "evidence"})
            self.assertEqual(candidate["name"], record["source"])
            self.assertEqual(classify_source_resource(candidate)["quality"], "generic")
            self.assertEqual(len(source_candidates(record)), 1)
            original = deepcopy(record)
            primary_source_for(record)
            self.assertEqual(record, original)

    def test_actual_station_page_outranks_operator_map(self):
        for record in self.records:
            for name, url in (("OpenChargeMap", "https://openchargemap.org/site/poi/details/123"),
                              ("Electromaps", "https://map.electromaps.com/es/p/123"),
                              ("Gaia", "https://gaiacharge.com/cpo/jump?station=123")):
                with self.subTest(source=record["source"], station_source=name):
                    row = deepcopy(record)
                    row["sources"].append({"name": name, "url": url, "evidence": "Station-specific page."})
                    primary = self.normalize(row)["primary_source"]
                    self.assertIn(primary["kind"], {"station_page", "start_page"})
                    self.assertEqual(primary["quality"], "exact")
                    self.assertEqual(primary["name"], name)


if __name__ == "__main__":
    unittest.main()
