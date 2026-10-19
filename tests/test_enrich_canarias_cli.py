"""The source-only CLI preserves its inputs and reports actual fetch provenance."""
import contextlib
import copy
import io
import itertools
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import enrich_canarias as cli


class EnrichCanariasCliTests(unittest.TestCase):
    def test_all_input_and_output_collisions_stop_before_collection(self):
        names = ("baseline", "map", "out", "report")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = {name: root / (name + ".json") for name in names}
            original["baseline"].write_text("baseline must survive")
            original["map"].write_text("map must survive")
            for first, second in itertools.combinations(names, 2):
                with self.subTest(first=first, second=second):
                    paths = {**original, second: original[first]}
                    args = ["enrich_canarias.py"]
                    for name in names:
                        args.extend(["--" + name, str(paths[name])])
                    with patch.object(sys, "argv", args), \
                         patch.object(cli, "enrich_records", side_effect=AssertionError("collection")), \
                         contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                        cli.main()
                    self.assertEqual(error.exception.code, 2)
                    self.assertEqual(original["baseline"].read_text(), "baseline must survive")
                    self.assertEqual(original["map"].read_text(), "map must survive")
                    self.assertFalse(original["out"].exists())
                    self.assertFalse(original["report"].exists())

    def test_symlink_output_cannot_overwrite_baseline(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            baseline, report = root / "baseline.json", root / "report.json"
            baseline.write_text("baseline must survive")
            report.symlink_to(baseline)
            args = ["enrich_canarias.py", "--baseline", str(baseline), "--out", str(root / "candidate.json"),
                    "--report", str(report)]
            with patch.object(sys, "argv", args), contextlib.redirect_stderr(io.StringIO()), \
                 self.assertRaises(SystemExit):
                cli.main()
            self.assertEqual(baseline.read_text(), "baseline must survive")

    def test_saved_map_keeps_its_original_age_and_staleness(self):
        for age, stale in ((3600, False), (2 * 24 * 3600, True)):
            with self.subTest(age=age), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                baseline, source_map = root / "baseline.json", root / "map.json"
                candidate, report = root / "candidate.json", root / "report.json"
                baseline.write_text(json.dumps({"records": []}))
                source_map.write_text('{"features": []}')
                original_time = time.time() - age
                os.utime(source_map, (original_time, original_time))
                observed = []

                def parse_map(payload, *, source_artifact):
                    observed.append(source_artifact)
                    return []

                output = {"records": [], "recordset_sha256": "fixture", "canarias_enrichment": {}}
                args = ["enrich_canarias.py", "--baseline", str(baseline), "--map", str(source_map),
                        "--out", str(candidate), "--report", str(report)]
                with patch.object(sys, "argv", args), \
                     patch.object(cli.ing, "parse_canarias_tecarga_geojson", side_effect=parse_map), \
                     patch.object(cli.ing, "FETCH_EVIDENCE", []), \
                     patch.object(cli.ing, "cached_fetch", side_effect=AssertionError("network")), \
                     patch.object(cli, "enrich_records", return_value={"pages": 0}), \
                     patch.object(cli, "overlay_payload", return_value=(output, {})), \
                     contextlib.redirect_stdout(io.StringIO()):
                    cli.main()
                artifact = observed[0]
                self.assertGreaterEqual(artifact.age_seconds, age - 1)
                self.assertLess(artifact.age_seconds, age + 10)
                self.assertEqual(artifact.stale, stale)
                self.assertTrue(artifact.hit)
                fetched = cli.datetime.fromisoformat(artifact.fetched_at.replace("Z", "+00:00"))
                self.assertAlmostEqual(fetched.timestamp(), original_time, places=5)
                evidence = json.loads(candidate.read_text())["fetch_evidence"]
                self.assertEqual(evidence["summary"]["stale"], int(stale))
                self.assertEqual(evidence["summary"]["cache_hits"], 1)

    def test_combined_evidence_keeps_history_and_deduplicates_map_registration(self):
        old = {"url": "https://example.org/catalogue", "fetched_at": "2026-10-01T00:00:00Z",
               "cache": {"hit": False, "fallback": False, "stale": False}}
        detail = {"url": "https://canariasterecarga.com/es/p1/", "fetched_at": "2026-10-02T00:00:00Z",
                  "cache": {"hit": True, "fallback": True, "stale": True}}
        current = {**detail, "fetched_at": "2026-10-19T00:00:00Z",
                   "cache": {"hit": True, "fallback": False, "stale": False}}
        previous = {"summary": {"total": 999}, "artifacts": [old], "historical_note": "retained"}
        before = copy.deepcopy(previous)
        merged = cli.combined_fetch_evidence(previous, [old, detail, detail, current])
        self.assertEqual(previous, before)
        self.assertEqual(merged["historical_note"], "retained")
        self.assertEqual(merged["artifacts"], [old, detail, current])
        self.assertEqual(merged["summary"], {"total": 3, "network": 1, "cache_hits": 2,
                         "fallbacks": 1, "stale": 1, "stale_fallbacks": 1})


if __name__ == "__main__":
    unittest.main()
