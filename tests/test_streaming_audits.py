"""Reports preserve coverage and examples while consuming rows without a full load."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import qa_free_db_completeness as completeness
from scripts import qa_valencia_source_audit as valencia
from scripts.json_recordset import load_json_recordset


class StreamingAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "catalogue.json"
        self.rows = [{"id": str(index), "name": f"Station {index:02d}", "source": "Fixture",
                      "lat": 39.45, "lon": -0.4, "is_free": True, "price_min": 0,
                      "parking": {"kind": "unknown"}, "source_url": "https://example.org/station",
                      "price_claim_type": "access_limited_free" if index == 0 else "free"}
                     for index in range(20)]
        self.rows.append({"id": "outside", "lat": 40, "lon": -74, "is_free": False})
        self.path.write_text(json.dumps({"records": self.rows}))

    def test_completeness_keeps_counts_gaps_and_bounded_examples(self):
        result = completeness.audit_records(self.path, strict=True)
        self.assertEqual(result["records"], 21)
        self.assertEqual(result["free_records"], 20)
        self.assertEqual(result["access_limited_free_records"], 1)
        self.assertEqual(result["coverage"]["parking"], {"present": 0, "missing": 20, "coverage_pct": 0.0})
        self.assertEqual(result["source_gaps"]["Fixture"]["parking"], 20)
        self.assertEqual(len(result["missing_samples"]["parking"]), 10)
        self.assertEqual(result["missing_samples"]["parking"][0]["id"], "0")
        self.assertIn("parking: 20 free records missing", result["failures"])

    def test_valencia_counts_every_row_but_classifies_only_region(self):
        result = valencia.audit_records(self.path, valencia.VALENCIA_BBOX)
        self.assertEqual(result["total_records"], 21)
        self.assertEqual(result["valencia_records"], 20)
        self.assertEqual(result["counts"]["by_source"], {"Fixture": 20})
        self.assertEqual(result["counts"]["issues"]["parking_unknown"], 20)
        self.assertEqual(len(result["navigation_only_samples"]), 12)
        self.assertEqual(len(result["highest_gap_samples"]), 12)
        self.assertEqual(len(result["issue_samples"]["parking_unknown"]), 8)
        self.assertEqual(result["highest_gap_samples"][0]["id"], "0")

    def test_reports_and_hashes_do_not_read_whole_file(self):
        expected = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("whole-file read")), \
             mock.patch.object(Path, "read_bytes", side_effect=AssertionError("whole-file read")):
            self.assertEqual(completeness.audit_records(self.path)["records"], 21)
            self.assertEqual(valencia.audit_records(self.path, valencia.VALENCIA_BBOX)["total_records"], 21)
            self.assertEqual(completeness.sha256_file(self.path), expected)
            self.assertEqual(valencia.sha256_file(self.path), expected)

    def test_legacy_input_shapes_remain_supported(self):
        expected_free = completeness.audit_records(self.path)
        expected_local = valencia.audit_records(self.path, valencia.VALENCIA_BBOX)
        self.path.write_text(json.dumps(self.rows))
        self.assertEqual(completeness.audit_records(self.path), expected_free)
        self.assertEqual(valencia.audit_records(self.path, valencia.VALENCIA_BBOX), expected_local)
        self.path.write_text(json.dumps({"stations": self.rows}))
        self.assertEqual(valencia.audit_records(self.path, valencia.VALENCIA_BBOX), expected_local)

    def test_stream_reader_default_remains_strict(self):
        for value in ([{"id": "fixture"}], {"records": ["not an object"]}):
            self.path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                load_json_recordset(self.path)

    def test_optional_legacy_streaming_retains_no_records(self):
        count = 0
        def consume(row):
            nonlocal count
            count += 1
        metadata = load_json_recordset(self.path, record_transform=consume, chunk_size=11)
        self.assertEqual(count, 21)
        self.assertEqual(metadata["records"], [])


if __name__ == "__main__":
    unittest.main()
