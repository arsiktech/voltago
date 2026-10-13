import json
import hashlib
from pathlib import Path
import tempfile
import unittest

from scripts.json_recordset import interning_object_hook, load_json_recordset
from scripts.source_policy import recordset_sha256


class JsonRecordsetTests(unittest.TestCase):
    def read(self, text, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalogue.json"
            path.write_text(text, encoding="utf-8")
            return load_json_recordset(path, **kwargs)

    def test_chunk_boundaries_preserve_unicode_escapes_and_metadata(self):
        payload = {"before": 123456789, "records": [{"id": "日本", "text": 'quote" / slash\\ / }],', "nested": [True, None, 1.25]}], "after": [3, 4]}
        for chunk in (1, 2, 7, 1000):
            with self.subTest(chunk=chunk):
                self.assertEqual(self.read(json.dumps(payload, ensure_ascii=False), chunk_size=chunk), payload)

    def test_consumer_keeps_metadata_without_retaining_rows(self):
        seen = []
        result = self.read('{"records":[{"id":"a"},{"id":"b"}],"count":2}',
                           record_transform=lambda row: seen.append(row["id"]))
        self.assertEqual(seen, ["a", "b"])
        self.assertEqual(result, {"records": [], "count": 2})

    def test_hook_applies_inside_rows(self):
        result = self.read('{"records":[{"id":"a","nested":{"x":2}}]}',
                           object_pairs_hook=lambda pairs: {k: str(v) if k == "x" else v for k, v in pairs})
        self.assertEqual(result["records"][0]["nested"]["x"], "2")

    def test_keys_and_values_are_shared_between_decoded_records(self):
        rows = self.read('{"records":[{"repeated-long-key":"repeated text"},{"repeated-long-key":"repeated text"}]}',
                         object_pairs_hook=interning_object_hook(), chunk_size=8)["records"]
        self.assertIs(next(iter(rows[0])), next(iter(rows[1])))
        self.assertIs(rows[0]["repeated-long-key"], rows[1]["repeated-long-key"])

    def test_malformed_structure_is_rejected(self):
        for text in ('[]', '{"records":[1]}', '{"records":{}}', '{"records":[],"records":[]}',
                     '{"records":[{},]}', '{"records":[]', '{"records":[]} trailing', '{"x":1,}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.read(text, chunk_size=2)

    def test_oversize_value_fails_with_bounded_buffer(self):
        with self.assertRaisesRegex(ValueError, "size limit"):
            self.read(json.dumps({"records": [{"text": "x" * 1000}]}), chunk_size=8, max_value_chars=64)

    def test_streaming_fingerprint_preserves_canonical_bytes(self):
        for rows in ([], [{"id": "z", "city": "東京"}, {"id": "a", "values": [1, None]}]):
            expected = hashlib.sha256(json.dumps(sorted(rows, key=lambda row: row["id"]),
                ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            self.assertEqual(recordset_sha256(rows), expected)


if __name__ == "__main__":
    unittest.main()
