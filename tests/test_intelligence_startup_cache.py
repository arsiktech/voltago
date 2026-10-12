"""Single-flight local intelligence builds and compact overview copy isolation."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class IntelligenceStartupCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        source = json.loads((server.DATA / "free_intelligence.json").read_text())
        self.records = source["records"][:12]
        self.db = self.root / "intel.json"
        self.write(self.records)
        (self.root / "seed_leads.json").write_text("[]")
        for target, value in [("DATA", self.root), ("_INTEL_RAW", {"mtime": None, "data": None}),
                              ("_INTEL_ENRICHED", {"key": None, "intel": None})]:
            context = patch.object(server, target, value)
            context.start(); self.addCleanup(context.stop)
        context = patch.dict(os.environ, {"VOLTA_INTELLIGENCE_DB": str(self.db)})
        context.start(); self.addCleanup(context.stop)
        context = patch.object(server.community_store, "summaries", return_value={"stations": {}})
        context.start(); self.addCleanup(context.stop)

    def write(self, records, path=None):
        target = path or self.db
        replacement = target.with_suffix(".new")
        replacement.write_text(json.dumps({"records": records, "recordset_sha256": "untrusted"}))
        replacement.replace(target)

    def parallel(self, functions):
        gate = threading.Barrier(len(functions))
        def run(fn):
            gate.wait(timeout=5)
            return fn()
        with ThreadPoolExecutor(max_workers=len(functions)) as executor:
            futures = [executor.submit(run, fn) for fn in functions]
            return [f.result(timeout=20) for f in futures]

    def test_concurrent_raw_and_enriched_build_once_with_real_rows(self):
        with (patch.object(server, "_interning_object_hook", wraps=server._interning_object_hook) as parse,
              patch.object(server, "partition_records", wraps=server.partition_records) as partition,
              patch.object(server, "recordset_sha256", wraps=server.recordset_sha256) as digest,
              patch.object(server, "_normalize_lead", wraps=server._normalize_lead) as normalize):
            self.parallel([server.load_intelligence_meta, server._intel_enriched_pool] * 4)
            self.assertEqual(parse.call_count, 1)
            self.assertEqual(partition.call_count, 1)
            self.assertEqual(digest.call_count, 1)
            self.assertEqual(normalize.call_count, len(server.load_intelligence_meta()["records"]))
        meta = server.load_intelligence_meta()
        self.assertNotEqual(meta["recordset_sha256"], "untrusted")
        self.assertEqual(meta["recordset_sha256"], server.recordset_sha256(meta["records"]))

    def test_replacement_invalidates_raw_and_enriched(self):
        first = server._intel_enriched_pool()
        self.write(self.records[:1])
        second = server._intel_enriched_pool()
        self.assertIsNot(first[0], second[0])
        self.assertEqual(len(server.load_intelligence_meta()["records"]), 1)
        self.assertEqual(server._INTEL_ENRICHED["key"][0], server._INTEL_RAW["mtime"])

    def test_seed_replacement_invalidates_enriched_not_raw(self):
        first = server._intel_enriched_pool()
        raw = server.load_intelligence_meta()
        (self.root / "seed_leads.json").write_text(json.dumps(self.records[:1]))
        self.assertIsNot(first[0], server._intel_enriched_pool()[0])
        self.assertIs(raw, server.load_intelligence_meta())

    def test_change_during_hash_retries_before_publication(self):
        original = server.recordset_sha256
        changed = False
        def digest(rows):
            nonlocal changed
            if not changed:
                changed = True
                self.write(self.records[:1])
            return original(rows)
        with patch.object(server, "recordset_sha256", side_effect=digest):
            meta = server.load_intelligence_meta()
        self.assertEqual(len(meta["records"]), 1)
        self.assertEqual(meta["recordset_sha256"], original(meta["records"]))
        self.assertEqual(server._INTEL_RAW["mtime"], server._intel_file_identity(self.db))

    def test_symlink_swap_during_enrichment_retries_bound_snapshot(self):
        first = self.root / "first.json"
        second = self.root / "second.json"
        self.write(self.records, first); self.write(self.records[:1], second)
        link = self.root / "current.json"
        link.symlink_to(first)
        original = server._normalize_lead
        changed = False
        def normalize(*args):
            nonlocal changed
            if not changed:
                changed = True
                link.unlink(); link.symlink_to(second)
            return original(*args)
        with (patch.dict(os.environ, {"VOLTA_INTELLIGENCE_DB": str(link)}),
              patch.object(server, "_normalize_lead", side_effect=normalize)):
            rows = server._intel_enriched_pool()[0]
            self.assertLessEqual(len(rows), 1)
            self.assertEqual(server._INTEL_ENRICHED["key"][0], server._intel_file_identity(second))
            self.assertEqual(len(server.load_intelligence_meta()["records"]), 1)

    def test_failures_release_build_locks_without_publishing_partial_rows(self):
        for name, fn in [("partition_records", server.load_intelligence_meta),
                         ("recordset_sha256", server.load_intelligence_meta),
                         ("_normalize_lead", server._intel_enriched_pool)]:
            with self.subTest(stage=name):
                server._INTEL_RAW.clear(); server._INTEL_ENRICHED.clear()
                with patch.object(server, name, side_effect=ValueError("failed build")):
                    with self.assertRaises(ValueError): fn()
                self.assertIsNone(server._INTEL_ENRICHED.get("intel"))
                self.parallel([server.load_intelligence_meta, server._intel_enriched_pool])
                self.assertIsNotNone(server._INTEL_ENRICHED.get("intel"))

    def test_compact_matches_full_projection_and_never_mutates_pool(self):
        pool = server._intel_enriched_pool()
        snapshot = deepcopy(pool)
        stamp = "2026-01-01T00:00:00Z"
        for params, price, fast in [({}, 5.0, False), ({}, 0.25, False),
                                    ({"free_only": ["true"]}, 5.0, False),
                                    ({}, 5.0, True),
                                    ({"bbox": ["27,-19,44,5"]}, 5.0, False)]:
            with self.subTest(params=params, price=price, fast=fast), \
                 patch.object(server, "now_iso", return_value=stamp), \
                 patch.object(server, "age_minutes", return_value=10):
                full = server.overview_response(params, price, fast)[1]
                compact = server.overview_response({**params, "compact": ["true"]}, price, fast)[1]
                full["leads"] = [server.compact_station_for_ui(row) for row in full["leads"]]
                full["meta"]["compact"] = True
                self.assertEqual(full, compact)
                for row in compact["leads"]:
                    row.setdefault("navigation", {})["test_mutation"] = True
                    row.setdefault("sources", []).append({"test_mutation": True})
                self.assertEqual(pool, snapshot)

    def test_compact_projects_only_after_price_filter_and_cap(self):
        rows = [{"id": str(i), "lat": 39 + i / 10000, "lon": -1,
                 "score": 10, "price_min": None, "raw": {"bulk": [i]}}
                for i in range(2501)]
        rows.append({"id": "free", "lat": 40, "lon": -1, "score": -10, "price_min": 0})
        pool = (rows, server._coords_for(rows), [], [])
        with (patch.object(server, "_intel_enriched_pool", return_value=pool),
              patch.object(server, "compact_station_for_ui", wraps=server.compact_station_for_ui) as project):
            broad = server.overview_response({"compact": ["true"]}, 5.0, False)[1]
            self.assertEqual(len(broad["leads"]), 2500)
            self.assertEqual(project.call_count, 2500)
            self.assertTrue(all(row["price_min"] is None for row in broad["leads"]))
            project.reset_mock()
            free = server.overview_response({"compact": ["true"]}, 0, False)[1]
            self.assertEqual([r["id"] for r in free["leads"]], ["free"])
            self.assertEqual(project.call_count, 1)

    def test_compact_preserves_community_downgrade_and_nested_isolation(self):
        rows = [deepcopy(self.records[0])]
        rows[0].update(id="community-test", lat=39, lon=-1, lead=True, price_min=None,
                       is_free=False, decision={"level": "go"}, score=1,
                       raw={"operator": "Example", "nested": [1]}, operator="Example",
                       sources=[{"name": "OpenStreetMap", "url": "https://www.openstreetmap.org/node/1"}],
                       last_seen="2025-01-01T00:00:00Z", source_fetched_at="2024-01-01T00:00:00Z")
        pool = (rows, [(39, -1)], [], [])
        snapshot = deepcopy(pool)
        summary = {"recent_failure": True, "recent_failure_count": 10, "nested": [1]}
        with (patch.object(server, "_intel_enriched_pool", return_value=pool),
              patch.object(server.community_store, "summaries", return_value={"stations": {"community-test": summary}})):
            full = server.overview_response({}, 5.0, False)[1]["leads"]
            compact = server.overview_response({"compact": ["true"]}, 5.0, False)[1]["leads"]
        self.assertEqual(compact, [server.compact_station_for_ui(r) for r in full])
        self.assertEqual(compact[0]["decision"]["level"], "check")
        compact[0]["community"]["nested"].append(2)
        compact[0]["sources"][0]["name"] = "mutated"
        self.assertEqual(summary["nested"], [1])
        self.assertEqual(pool, snapshot)


if __name__ == "__main__":
    unittest.main()
