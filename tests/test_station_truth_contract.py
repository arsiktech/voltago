"""Executable Python side of the language-neutral station-truth corpus."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import ingest_sources as ingestion  # noqa: E402
import server  # noqa: E402
import station_truth  # noqa: E402


CORPUS = json.loads((ROOT / "contracts" / "station_truth_cases.json").read_text(encoding="utf-8"))


class StationTruthContractTests(unittest.TestCase):
    def test_connector_aliases_execute_against_one_python_implementation(self) -> None:
        self.assertIs(server.normalize_connector_label, station_truth.normalize_connector_label)
        self.assertIs(ingestion.connector_label_from_token, station_truth.normalize_connector_label)
        for case in CORPUS["connector_labels"]:
            with self.subTest(token=case["token"]):
                self.assertEqual(station_truth.normalize_connector_label(case["token"]), case["expected"])

    def test_source_classification_executes_against_one_python_implementation(self) -> None:
        self.assertIs(server.classify_source_resource, station_truth.classify_source_resource)
        self.assertIs(ingestion.classify_source_resource, station_truth.classify_source_resource)
        for case in CORPUS["source_resources"]:
            with self.subTest(source=case["source"]):
                actual = station_truth.classify_source_resource(case["source"])
                self.assertEqual(actual["kind"], case["expected"]["kind"])
                self.assertEqual(actual["quality"], case["expected"]["quality"])

    def test_backend_route_ready_rule_executes_shared_cases(self) -> None:
        for case in CORPUS["route_ready"]:
            with self.subTest(case=case["id"]):
                self.assertEqual(server.route_ready_now(case["station"]), case["expected"])


if __name__ == "__main__":
    unittest.main()
