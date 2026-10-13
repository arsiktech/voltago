import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from scripts.build_intelligence_index import build_index, finalize_index
from scripts.intelligence_index import IntelligenceIndex, file_sha256
from scripts.source_policy import recordset_sha256


class IntelligenceIndexTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.source = self.directory / "catalogue.json"
        self.output = self.directory / "runtime #世界.sqlite"
        self.seeds = self.directory / "seed_leads.json"
        self.seeds.write_text("[]", encoding="utf-8")
        self.rows = [
            {"id": "west", "lat": 4.0, "lon": 179.2, "price_min": None, "name": "世界", "source": "Public data"},
            {"id": "east", "lat": 5, "lon": -179.5, "is_free": True, "max_power_kw": 150, "source": "Public data", "merged_ids": ["old-east"]},
            {"id": "es", "lat": 40.2, "lon": -3.6, "country_code": "ES", "price_min": 0.2, "is_cheap": True, "source": "Public data"},
            {"id": "usd", "lat": 35.5, "lon": -120, "country_code": "US", "price_currency": "USD", "price_min": 0.1, "source": "Public data"},
            {"id": "bad", "lat": 999, "lon": 0, "source": "Public data"},
        ]

    def build(self, rows=None, **kwargs):
        self.source.write_text(json.dumps({"generated_at": "2026-10-13T00:00:00Z", "records": self.rows if rows is None else rows,
                                           "global_coverage": {"countries": 3}}, ensure_ascii=False), encoding="utf-8")
        self.receipt = build_index(self.source, self.output, seeds_path=self.seeds, **kwargs)
        return IntelligenceIndex(self.output)

    def test_lossless_roundtrip_preserves_order_and_canonical_fingerprint(self):
        index = self.build()
        self.assertEqual(list(index.records), self.rows)
        self.assertEqual(index.metadata()["recordset_sha256"], recordset_sha256(self.rows))
        self.assertEqual(index.metadata()["index_manifest"]["source_sha256"], file_sha256(self.source))
        self.assertEqual(self.receipt["index_sha256"], file_sha256(self.output))
        self.assertEqual(index.metadata()["global_coverage"], {"countries": 3})

    def test_sequence_index_slice_and_caller_mutation_isolation(self):
        index = self.build()
        self.assertEqual(len(index.records), 5)
        self.assertEqual(index.records[-1], self.rows[-1])
        self.assertEqual(index.records[1:3], self.rows[1:3])
        self.assertEqual(index.records[::-1], self.rows[::-1])
        index.records[0]["name"] = "changed"
        index.metadata()["global_coverage"]["countries"] = 999
        self.assertEqual(index.records[0]["name"], "世界")
        self.assertEqual(index.metadata()["global_coverage"]["countries"], {"countries": 3}["countries"])
        for position in (5, -6):
            with self.assertRaises(IndexError):
                index.records[position]

    def test_dateline_query_returns_both_sides_with_correct_count_and_pagination(self):
        index = self.build()
        bbox = {"latitude_sw": -10, "latitude_ne": 10, "longitude_sw": 170, "longitude_ne": -170}
        rows, count = index.query(bbox, offset=1, limit=1)
        self.assertEqual(count, 2)
        self.assertEqual([row["id"] for row in rows], ["east"])
        self.assertEqual([row["id"] for row in index.records.iter_bbox(bbox)], ["west", "east"])
        self.assertEqual(index.query(bbox, limit=0), ([], 2))

    def test_world_bbox_excludes_invalid_coordinates(self):
        index = self.build()
        rows, count = index.query({"latitude_sw": -90, "latitude_ne": 90, "longitude_sw": -180, "longitude_ne": 180})
        self.assertEqual(count, 4)
        self.assertNotIn("bad", [row["id"] for row in rows])

    def test_indexed_prices_keep_foreign_currency_and_unknown_out_of_euro_cap(self):
        index = self.build()
        rows, count = index.query(price_cap=0.25)
        self.assertEqual(count, 2)
        self.assertEqual([row["id"] for row in rows], ["east", "es"])
        self.assertEqual(index.query(price_cap=0)[0][0]["id"], "east")
        self.assertEqual(index.query(price_cap=5)[1], 5)
        self.assertEqual(index.query(fast_only=True)[1], 1)

    def test_metadata_count_and_bounded_query_never_decode_unselected_rows(self):
        index = self.build()
        with mock.patch.object(IntelligenceIndex, "decode", wraps=IntelligenceIndex.decode) as decode:
            reopened = IntelligenceIndex(self.output)
            self.assertEqual(len(reopened.records), 5)
            reopened.metadata()
            self.assertEqual(decode.call_count, 0)
            rows, matched = reopened.query(limit=1, overview=True)
            self.assertEqual(matched, 5)
            self.assertEqual(len(rows), 1)
            self.assertEqual(decode.call_count, 1)

    def test_overview_matches_free_cheap_distance_score_priority(self):
        index = self.build([
            {"id": "far", "lat": 20, "lon": 20, "score": 99},
            {"id": "near", "lat": 0, "lon": 0},
            {"id": "free", "lat": 60, "lon": 60, "is_free": True},
            {"id": "cheap", "lat": 30, "lon": 30, "is_cheap": True},
        ])
        rows, count = index.query(overview=True, origin=(0, 0), limit=3)
        self.assertEqual(count, 4)
        self.assertEqual([row["id"] for row in rows], ["free", "cheap", "near"])

    def test_distance_query_without_bbox_keeps_invalid_coordinates_last(self):
        index = self.build()
        rows, count = index.query(overview=True, origin=(0, 0))
        self.assertEqual(count, 5)
        self.assertEqual(rows[-1]["id"], "bad")

    def test_id_alias_lookup_and_duplicates_resolve_first_original_row(self):
        index = self.build(self.rows + [dict(self.rows[1], name="Duplicate")])
        self.assertEqual(index.get_by_id("old-east"), self.rows[1])
        self.assertEqual(index.get_by_id("east"), self.rows[1])
        self.assertIsNone(index.get_by_id("absent"))
        self.assertEqual(index.ids(), {row["id"] for row in self.rows})

    def test_source_policy_applies_before_index_and_metadata_hash(self):
        blocked = {"id": "blocked", "source": "Chargemap", "source_url": "https://chargemap.com/station"}
        index = self.build(self.rows + [blocked])
        self.assertEqual(len(index.records), 5)
        self.assertEqual(index.metadata()["source_policy_excluded_records"], 1)
        self.assertEqual(index.metadata()["recordset_sha256"], recordset_sha256(self.rows))
        self.assertIsNone(index.get_by_id("blocked"))

    def test_policy_mismatch_fails_closed(self):
        self.build()
        with self.assertRaisesRegex(ValueError, "source policy"):
            IntelligenceIndex(self.output, policy_sha256="different")

    def test_health_summary_is_bound_to_runtime_code_and_seed_content(self):
        import server
        index = self.build(health_summary=True)
        self.assertEqual(index.metadata()["index_manifest"]["health_code_sha256"], server.intelligence_health_fingerprint())
        self.assertEqual(index.metadata()["index_manifest"]["seeds_sha256"], file_sha256(self.seeds))
        self.assertEqual(index.summaries["intel_counts"]["free_records"], 1)
        self.assertEqual(index.summaries["intel_counts"]["cheap_records"], 1)
        self.assertIn("by_country", index.summaries["source_health_inventory"])

    def test_finalizer_refreshes_new_immutable_file_without_mutating_source(self):
        index = self.build()
        before = file_sha256(self.output)
        finalized = self.directory / "finalized.sqlite"
        receipt = finalize_index(self.output, finalized, seeds_path=self.seeds)
        final = IntelligenceIndex(finalized)
        self.assertEqual(file_sha256(self.output), before)
        self.assertEqual(list(index.records), list(final.records))
        self.assertEqual(final.metadata()["recordset_sha256"], receipt["recordset_sha256"])
        self.assertIn("health_code_sha256", final.metadata()["index_manifest"])
        self.assertIn("source_health_inventory", final.summaries)

    def test_non_index_or_inconsistent_manifest_is_rejected(self):
        self.build()
        with sqlite3.connect(self.output) as connection:
            connection.execute("DELETE FROM stations WHERE seq=0")
        with self.assertRaisesRegex(ValueError, "record count"):
            IntelligenceIndex(self.output)

    def test_runtime_connection_cannot_mutate_published_index(self):
        index = self.build()
        with index.connection() as connection, self.assertRaises(sqlite3.OperationalError):
            connection.execute("DELETE FROM stations")

    def test_failed_rebuild_keeps_previous_index_bytes(self):
        self.build()
        previous = self.output.read_bytes()
        self.source.write_text('{"records":[{"id":"new"},', encoding="utf-8")
        with self.assertRaises((ValueError, json.JSONDecodeError, FileExistsError)):
            build_index(self.source, self.output, seeds_path=self.seeds)
        self.assertEqual(self.output.read_bytes(), previous)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_malformed_source_never_publishes_a_partial_index(self):
        self.source.write_text('{"records":[{"id":"first"},', encoding="utf-8")
        with self.assertRaises((ValueError, json.JSONDecodeError)):
            build_index(self.source, self.output, seeds_path=self.seeds)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_rebuilding_existing_or_symlinked_index_is_rejected(self):
        index = self.build()
        self.source.write_text(json.dumps({"records": [{"id": "new"}]}), encoding="utf-8")
        for target in (self.output, self.directory / "current.sqlite"):
            if target != self.output:
                target.symlink_to(self.output.name)
            with self.subTest(target=target), self.assertRaises(FileExistsError):
                build_index(self.source, target, seeds_path=self.seeds)
        self.assertEqual(list(index.records), self.rows)

    def test_invalid_query_arguments_are_rejected(self):
        index = self.build()
        for kwargs in ({"offset": -1}, {"limit": -1}, {"price_cap": float("nan")}, {"price_cap": -1},
                       {"overview": True, "origin": (100, 0)},
                       {"bbox": {"latitude_sw": 20, "latitude_ne": 10, "longitude_sw": 0, "longitude_ne": 10}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                index.query(**kwargs)


if __name__ == "__main__":
    unittest.main()
