"""OSM socket output power: bare watts become kW, ambiguous values stay unknown."""
from __future__ import annotations

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import global_sources  # noqa: E402
import ingest_sources as ingestion  # noqa: E402
import server  # noqa: E402
from station_truth import parse_osm_socket_output, repair_osm_connector_power  # noqa: E402

LAS_PALMERAS_ID = "intel_openstreetmap-public-tags_avenida-las-palmeras_28.48046_-16.43325"


def ingest(*tag_sets: Dict[str, str]) -> List[Dict[str, Any]]:
    elements = [
        {"type": "node", "id": 9200000000 + index, "lat": 28.48 + index / 100, "lon": -16.43,
         "tags": {"amenity": "charging_station", "name": f"Fixture {index}", **tags}}
        for index, tags in enumerate(tag_sets)
    ]
    body = json.dumps({"version": 0.6, "elements": elements}).encode("utf-8")
    originals = ingestion.cached_overpass, ingestion.OVERPASS_TILE_PAUSE_SECONDS
    ingestion.cached_overpass = lambda query, cache_name, ttl_hours=12: body
    ingestion.OVERPASS_TILE_PAUSE_SECONDS = 0
    try:
        records: List[Dict[str, Any]] = []
        ingestion.source_osm(records)
    finally:
        ingestion.cached_overpass, ingestion.OVERPASS_TILE_PAUSE_SECONDS = originals
    return sorted(records, key=lambda row: row["raw"]["osm_id"])


class ParseOutputTests(unittest.TestCase):
    def assert_reads(self, label: str, cases: Dict[str, Any]) -> None:
        for raw, expected in cases.items():
            with self.subTest(label=label, raw=raw):
                self.assertEqual(parse_osm_socket_output(raw, label)["power_kw"], expected)

    def test_explicit_units(self) -> None:
        self.assert_reads("Type 2", {"22 kW": 22.0, "22kW": 22.0, "7,4 kW": 7.4, "7400 W": 7.4, "7400W": 7.4,
                                     "22000 w": 22.0, "150 kW": 150.0})
        self.assert_reads("Schuko", {"2300 W": 2.3, "500 W": 0.5, "3.7 kW": 3.7})
        self.assert_reads("CCS2", {"1000 kW": 1000.0, "50000 W": 50.0})

    def test_bare_numbers_impossible_as_kw_are_watts(self) -> None:
        self.assert_reads("Type 2", {"7400": 7.4, "22000": 22.0, "22144": 22.144, "3700": 3.7, "43000": 43.0})
        self.assert_reads("Schuko", {"2300": 2.3, "3700": 3.7, "1840": 1.84})
        self.assert_reads("CCS2", {"50000": 50.0, "250000": 250.0})
        self.assert_reads("CHAdeMO", {"51200": 51.2})

    def test_bare_numbers_possible_as_kw_stay_kw(self) -> None:
        self.assert_reads("Type 2", {"22": 22.0, "11": 11.0, "7.4": 7.4, "7,4": 7.4, "43": 43.0, "120": 120.0})
        self.assert_reads("Schuko", {"3.7": 3.7, "2,3": 2.3})
        self.assert_reads("CCS2", {"50": 50.0, "300": 300.0, "920": 920.0})

    def test_ambiguous_or_implausible_values_are_unknown_never_guessed(self) -> None:
        self.assert_reads("Type 2", {
            "32": None, "16": None, "63": None,  # amps
            "230": None, "400": None,  # volts, or too high for this socket
            "1": None, "2": None, "0": None,  # socket counts and zero
            "1000": None,  # neither 1000 kW nor 1 kW is a Type 2 rating
            "50000": None,  # 50 kW is above the AC range, so watts proves nothing
            "7,400": None,  # 7.4 or 7400, the comma decides
            "7400 kW": None,  # a stated unit is never reinterpreted
            "22;11": None, "7400;2300": None, "32 A": None, "22 kVA": None, "22 kWh": None, "yes": None, "": None,
        })
        self.assert_reads("Schuko", {"16": None, "230": None, "22": None, "11": None, "700": None, "4000": None, "22 kW": None})
        self.assert_reads("CCS2", {"1": None, "2": None, "4": None, "1500": None, "9999999": None})

    def test_unknown_values_say_why(self) -> None:
        for raw, label, basis in (("32", "Type 2", "ambiguous_current_rating"), ("1", "Type 2", "ambiguous_low_value"),
                                  ("230", "Type 2", "implausible_for_connector"), ("7,400", "Type 2", "ambiguous_decimal_separator"),
                                  ("yes", "Type 2", "unparsed_text"), ("7400", None, "bare_without_connector_family")):
            with self.subTest(raw=raw):
                result = parse_osm_socket_output(raw, label)
                self.assertEqual((result["status"], result["basis"], result["raw_value"]), ("unknown", basis, raw))

    def test_existing_text_shapes_keep_their_first_kw_value(self) -> None:
        self.assert_reads("CCS2", {"300 kW;150 kW": 300.0, "1x 50kW;1x 70 kW": 50.0, "50 kW DC": 50.0})
        self.assert_reads("Type 2", {"11 - 22 kW": 22.0, "2x22kW": 22.0, "11 kW3": 11.0})


class IngestionTests(unittest.TestCase):
    def test_real_las_palmeras_tag_shape(self) -> None:
        row = ingest({"capacity": "2", "socket:schuko": "1", "socket:schuko:output": "2300",
                      "socket:type2": "1", "socket:type2:output": "7400"})[0]
        self.assertEqual({c["label"]: c["power_kw"] for c in row["connectors"]}, {"Type 2": 7.4, "Schuko": 2.3})
        self.assertEqual(row["max_power_kw"], 7.4)
        self.assertFalse(row["fast"])
        self.assertEqual(row["hardware_claim"]["connector_power_kw"], [2.3, 7.4])
        # Raw evidence is untouched and the reading is traceable.
        by_label = {c["label"]: c["raw"] for c in row["connectors"]}
        self.assertEqual(by_label["Type 2"]["output"], "7400")
        self.assertEqual(by_label["Type 2"]["output_reading"], "bare_w")
        self.assertEqual(row["raw"]["socket:type2:output"], "7400")

    def test_rejected_output_is_unknown_and_not_rescued_by_station_fallback(self) -> None:
        row = ingest({"socket:type2": "1", "socket:type2:output": "32", "voltage": "230", "amperage": "32",
                      "description": "Punto 22 kW"})[0]
        self.assertIsNone(row["connectors"][0]["power_kw"])
        self.assertEqual(row["connectors"][0]["raw"]["output_reading"], "ambiguous_current_rating")
        self.assertIsNone(row["max_power_kw"])

    def test_missing_output_keeps_the_station_fallback(self) -> None:
        row = ingest({"socket:type2": "1", "description": "Punto 22 kW"})[0]
        self.assertEqual(row["max_power_kw"], 22.0)
        self.assertEqual(row["connectors"][0]["power_kw"], 22.0)

    def test_bare_or_rejected_output_does_not_unlock_the_red_movem_tariff(self) -> None:
        movem = {"name": "Red Movem Talarrubias", "operator": "Diputación de Badajoz",
                 "operator:website": "https://movembadajoz.es/"}
        original = ingestion.red_movem_tariff_review
        ingestion.red_movem_tariff_review = lambda: {
            "status": "page-verified", "checked_at": "2026-10-20T09:00:00Z",
            "url": ingestion.RED_MOVEM_TARIFF_PAGE_URL, "detail": "fixture"}
        self.addCleanup(setattr, ingestion, "red_movem_tariff_review", original)
        explicit = ingest({**movem, "socket:type2": "2", "socket:type2:output": "22 kW"})
        self.assertEqual(explicit[0]["price_min"], 0.25)
        for output in ("22", "22000"):
            with self.subTest(output=output):
                self.assertEqual(ingest({**movem, "socket:type2": "2", "socket:type2:output": output}), [])
        self.assertEqual(ingest({**movem, "socket:type2": "1", "socket:type2:output": "22 kW",
                                 "socket:schuko": "1", "socket:schuko:output": "16"}), [])

    def test_global_adapter_reads_the_same_way(self) -> None:
        element = {"type": "node", "id": 1234, "lat": 40.7, "lon": -74.0, "tags": {
            "amenity": "charging_station", "addr:country": "US", "socket:type2": "1", "socket:type2:output": "7400",
            "socket:schuko": "1", "socket:schuko:output": "16"}}
        captured: Dict[str, Any] = {}
        original = global_sources.make_record
        global_sources.make_record = lambda **kwargs: captured.update(kwargs) or kwargs
        self.addCleanup(setattr, global_sources, "make_record", original)
        global_sources.normalize_osm(element, None)
        self.assertEqual({c["label"]: c["power_kw"] for c in captured["connectors"]}, {"Type 2": 7.4, "Schuko": None})


class ServedRowTests(unittest.TestCase):
    @staticmethod
    def snapshot_row() -> Dict[str, Any]:
        payload = json.loads((ROOT / "data" / "free_intelligence.json").read_text(encoding="utf-8"))
        return next(row for row in payload["records"] if row["id"] == LAS_PALMERAS_ID)

    def test_committed_snapshot_row_is_repaired_on_read_without_new_ingestion(self) -> None:
        row = self.snapshot_row()
        self.assertEqual({c["label"]: c["power_kw"] for c in row["connectors"]}, {"Type 2": 7400.0, "Schuko": 2300.0})
        served = server._normalize_lead(deepcopy(row), row["source"])
        self.assertEqual({c["label"]: c["power_kw"] for c in served["connectors"]}, {"Type 2": 7.4, "Schuko": 2.3})
        self.assertEqual(served["max_power_kw"], 7.4)
        self.assertEqual(served["connector_summary"]["max_power_kw"], 7.4)
        self.assertFalse(served["fast"])
        self.assertEqual(served["id"], row["id"])
        self.assertEqual(sum(c["count"] for c in served["connectors"]), sum(c["count"] for c in row["connectors"]))

    def test_rejected_snapshot_output_serves_unknown_power(self) -> None:
        row = {"id": "x", "lat": 28.4, "lon": -16.4, "name": "x", "source": "OpenStreetMap public tags", "fast": True,
               "max_power_kw": 230.0, "hardware_claim": {"max_power_kw": 230.0, "connector_power_kw": [230.0]},
               "connectors": [{"label": "Type 2", "power_kw": 230.0, "count": 1, "connector_id": "socket:type2",
                               "raw": {"osm_key": "socket:type2", "output": "230"}}]}
        self.assertTrue(repair_osm_connector_power(row))
        self.assertIsNone(row["connectors"][0]["power_kw"])
        self.assertIsNone(row["max_power_kw"])
        self.assertFalse(row["fast"])
        self.assertEqual(row["hardware_claim"], {"max_power_kw": None, "connector_power_kw": []})

    def test_repair_leaves_other_evidence_alone_and_is_idempotent(self) -> None:
        official = {"max_power_kw": 50.0, "fast": True, "connectors": [
            {"label": "CCS2", "power_kw": 50.0, "connector_id": 7, "raw": {"output": "9999"}},
            {"label": "Type 2", "power_kw": 22.0, "connector_id": "socket:type2",
             "raw": {"osm_key": "socket:type2", "output": "yes", "power_basis": "OSM charge/description text"}}]}
        before = deepcopy(official)
        self.assertFalse(repair_osm_connector_power(official))
        self.assertEqual(official, before)
        merged = {"max_power_kw": 50.0, "fast": True, "connectors": [
            {"label": "CCS2", "power_kw": 50.0, "connector_id": "dgt-1"},
            {"label": "Type 2", "power_kw": 7400.0, "connector_id": "socket:type2",
             "raw": {"osm_key": "socket:type2", "output": "7400"}}]}
        self.assertTrue(repair_osm_connector_power(merged))
        self.assertEqual((merged["max_power_kw"], merged["fast"]), (50.0, True))
        self.assertEqual(merged["connectors"][1]["power_kw"], 7.4)
        self.assertFalse(repair_osm_connector_power(merged))


if __name__ == "__main__":
    unittest.main()
