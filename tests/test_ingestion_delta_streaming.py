"""The compact delta gate retains every original validation and identity proof."""
import json
from pathlib import Path
import sys
import tempfile
import tracemalloc
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import qa_ingestion_delta as delta


def station(identifier="station", **changes):
    return dict({"id": identifier, "name": "Public charger", "lat": 39.4, "lon": -0.3,
                 "source": "Public registry", "source_url": "https://example.test/station",
                 "sources": [{"url": "https://example.test/station"}],
                 "parking": {"kind": "unknown"}, "reliability": "official-snapshot"}, **changes)


class StreamingDeltaTests(unittest.TestCase):
    def test_projection_keeps_all_gate_verdicts_and_reporting_counts(self):
        rows = [station(), station("missing", name="", sources=[], parking={}, lat=None, source_url=""),
                station("gone", name="Removed station", is_free=True),
                station("evidence", sources=[{"evidence": "charger does not exist"}], is_cheap=True),
                station("unpriced-removed", name="Removed station"),
                station("consum", source="Consum store locator", name="Consum supermarket charger",
                        source_url="https://www.consum.es/supermercados/valencia/")]
        timestamp_keys = ("LastVerified", "DateLastStatusUpdate", "DateLastConfirmed", "last_seen", "lastSeen",
                          "last_used_time", "lastUsedTime", "updated_at", "updatedAt", "creation_date")
        rows += [station(key, raw={key: "2026-10-13T00:00:00Z"}) for key in timestamp_keys]
        projected = [delta.compact_gate_record(row) for row in rows]
        for function in (delta.source_names, delta.reliability_names, delta.parking_names,
                         delta.missing_source_links, delta.missing_required_fields, delta.weak_consum_source_rows,
                         delta.bad_actionable_name_rows, delta.unpromoted_source_timestamp_rows):
            with self.subTest(function=function.__name__):
                self.assertEqual(function(projected), function(rows))

    def test_registry_and_carried_identity_merge_proofs_survive_projection(self):
        old = station("old", source="DGT National Access Point charging registry", raw={"nap_site_id": "nap-1"})
        keep = station("keep")
        new = station("new", merged_ids=["old"], merged_source_identifiers=[
            ["DGT National Access Point charging registry", "nap_site_id", "nap-1"]])
        for rebuilt in ([keep, new], [keep, dict(new, merged_source_identifiers=[])], [keep, new, dict(new, id="duplicate-claim")]):
            with self.subTest(rebuilt=[row["id"] for row in rebuilt]):
                self.assertEqual(delta.id_delta([delta.compact_gate_record(old), delta.compact_gate_record(keep)],
                                                 [delta.compact_gate_record(row) for row in rebuilt]),
                                 delta.id_delta([old, keep], rebuilt))

    def test_bulk_raw_evidence_is_not_retained_or_interned(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalogue.json"
            with path.open("w") as handle:
                handle.write('{"records":[')
                for index in range(40):
                    if index:
                        handle.write(",")
                    json.dump(station(str(index), raw={"irrelevant": str(index) + "x" * 300_000}), handle)
                handle.write('],"generated_at":"2026-10-13"}')
            tracemalloc.start()
            try:
                payload, rows, excluded = delta.load_partitioned(path, {"exclusions": []})
                peak = tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
            self.assertEqual(len(rows), 40)
            self.assertEqual(excluded, [])
            self.assertEqual(payload["generated_at"], "2026-10-13")
            self.assertTrue(all(isinstance(row, delta.GateRecord) for row in rows))
            self.assertLess(peak, path.stat().st_size // 2)


if __name__ == "__main__":
    unittest.main()
