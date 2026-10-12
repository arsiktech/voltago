import importlib.util
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("qa_ingestion_canary", ROOT / "scripts" / "qa_ingestion_canary.py")
qa_ingestion_canary = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(qa_ingestion_canary)


class IngestionCanaryTests(unittest.TestCase):
    def test_parse_last_json_object_ignores_command_chatter(self):
        text = "source_a: +10\nsource_b: +20\n{\"ok\": true, \"records\": 30}"

        parsed = qa_ingestion_canary.parse_last_json_object(text)

        self.assertEqual(parsed, {"ok": True, "records": 30})

    def test_compact_summary_keeps_delta_and_source_counts(self):
        delta = {
            "ok": True,
            "rebuilt": {
                "records": 390,
                "raw_records": 396,
                "errors": 0,
                "sources": [["Electromaps web-map API", 233]],
            },
            "delta": {
                "missing_ids": 0,
                "added_ids": 0,
                "missing_sample": [],
                "added_sample": [],
            },
            "failures": [],
        }

        summary = qa_ingestion_canary.compact_summary(delta, pathlib.Path("/tmp/rebuilt.json"))

        self.assertTrue(summary["ok"])
        self.assertEqual(summary["records"], 390)
        self.assertEqual(summary["sources"], [["Electromaps web-map API", 233]])
        self.assertEqual(summary["missing_ids"], 0)

    def test_append_history_writes_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "history.jsonl"

            qa_ingestion_canary.append_history(path, {"ok": True, "records": 390})
            qa_ingestion_canary.append_history(path, {"ok": False, "records": 10})

            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(rows, [{"ok": True, "records": 390}, {"ok": False, "records": 10}])

    def test_append_history_compacts_commands_and_retains_recent_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "history.jsonl"
            for i in range(4):
                qa_ingestion_canary.append_history(path, {
                    "ok": True,
                    "records": i,
                    "commands": {
                        "ingest": {"cmd": ["python", "ingest"], "returncode": 0, "stdout": "x" * 1200, "stderr": ""},
                        "delta": {"cmd": ["python", "delta"], "returncode": 0, "stdout": "y" * 1200, "stderr": ""},
                    },
                }, keep=2)

            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([row["records"] for row in rows], [2, 3])
            self.assertNotIn("stdout", rows[-1]["commands"]["ingest"])
            self.assertEqual(len(rows[-1]["commands"]["ingest"]["stdout_tail"]), 800)

    def test_source_family_threshold_fails_large_family_drop(self):
        failures, checked = qa_ingestion_canary.source_family_threshold_failures(
            [["OpenChargeMap public snapshot", 120], ["Lidl official free list", 8]],
            [["OpenChargeMap public snapshot", 233], ["Lidl official free list", 8], ["Tiny", 3]],
            min_ratio=0.85,
            min_count=5,
            label="baseline",
        )

        self.assertEqual(checked["OpenChargeMap public snapshot"]["floor"], 199)
        self.assertNotIn("Tiny", checked)
        self.assertTrue(any("OpenChargeMap public snapshot" in item for item in failures))
        self.assertFalse(any("Lidl official free list" in item for item in failures))

    def test_latest_ok_history_sources_skips_failed_and_malformed_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "history.jsonl"
            path.write_text(
                "\n".join([
                    json.dumps({"ok": True, "sources": [["old", 1]]}),
                    "{not-json",
                    json.dumps({"ok": False, "sources": [["failed", 9]]}),
                    json.dumps({"ok": True, "sources": [["latest", 2]]}),
                ]) + "\n",
                encoding="utf-8",
            )

            self.assertEqual(qa_ingestion_canary.latest_ok_history_sources(path), [["latest", 2]])

    def test_source_evidence_counts_survives_cross_source_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "db.json"
            path.write_text(json.dumps({"records": [{
                "source": "Primary",
                "sources": [
                    {"name": "Primary"},
                    {"name": "Merged family"},
                    {"name": "Merged family"},
                ],
            }]}), encoding="utf-8")

            self.assertEqual(
                dict(qa_ingestion_canary.source_evidence_counts(path)),
                {"Primary": 1, "Merged family": 1},
            )

    def test_source_evidence_counts_excludes_origin_evidence_but_keeps_operator_mentions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "db.json"
            path.write_text(json.dumps({"records": [
                {
                    "source": "Electromaps web-map API",
                    "sources": [{"name": "Electromaps web-map API"}],
                },
                {
                    "source": "OpenChargeMap public snapshot",
                    "name": "Feníe-operated site",
                    "owner": {"name": "FENIE ENERGIA"},
                    "sources": [{
                        "name": "OpenChargeMap public snapshot",
                        "url": "https://openchargemap.io/site/poi/details/1",
                    }],
                },
            ]}), encoding="utf-8")

            self.assertEqual(
                dict(qa_ingestion_canary.source_evidence_counts(path)),
                {"OpenChargeMap public snapshot": 1},
            )

    def test_source_family_threshold_ignores_explicit_policy_exclusions(self):
        failures, checked = qa_ingestion_canary.source_family_threshold_failures(
            [["OpenChargeMap public snapshot", 100]],
            [["Electromaps web-map API", 189], ["OpenChargeMap public snapshot", 100]],
            min_ratio=0.85,
            min_count=5,
            label="baseline",
        )

        self.assertEqual(failures, [])
        self.assertNotIn("Electromaps web-map API", checked)
        self.assertIn("OpenChargeMap public snapshot", checked)

    def test_existing_candidate_skips_rebuild_and_audits_exact_path(self):
        candidate = pathlib.Path("/tmp/exact-candidate.json")

        ingest_cmd, delta_cmd = qa_ingestion_canary.canary_commands(
            python="python3",
            out_path=candidate,
            existing_candidate=True,
            policy_path=pathlib.Path("/tmp/policy.json"),
            baseline_path=pathlib.Path("/tmp/baseline.json"),
        )

        self.assertIsNone(ingest_cmd)
        self.assertEqual(delta_cmd[-6:], [
            "--baseline",
            "/tmp/baseline.json",
            "--rebuilt",
            str(candidate),
            "--policy-exclusions",
            "/tmp/policy.json",
        ])

    def test_explicit_candidate_and_baseline_record_exact_hashes_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            candidate = tmp_path / "candidate.json"
            baseline = tmp_path / "baseline.json"
            history = tmp_path / "history.jsonl"
            canonical_bytes = (ROOT / "data" / "free_intelligence.json").read_bytes()
            candidate.write_bytes(canonical_bytes)
            baseline.write_bytes(canonical_bytes)
            before = hashlib.sha256(candidate.read_bytes()).hexdigest()

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "qa_ingestion_canary.py"),
                    "--candidate",
                    str(candidate),
                    "--baseline",
                    str(baseline),
                    "--history-out",
                    str(history),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            summary = json.loads(proc.stdout)
            after = hashlib.sha256(candidate.read_bytes()).hexdigest()
            baseline_sha = hashlib.sha256(baseline.read_bytes()).hexdigest()
            self.assertEqual(before, after)
            self.assertEqual(summary["candidate_sha256"], before)
            self.assertEqual(summary["baseline_sha256"], baseline_sha)
            self.assertEqual(summary["candidate"], str(candidate))
            self.assertEqual(summary["baseline"], str(baseline))
            self.assertTrue(summary["candidate_reused"])
            history_row = json.loads(history.read_text(encoding="utf-8").strip())
            self.assertEqual(history_row["candidate_sha256"], before)

    def test_metric_history_does_not_compare_legacy_primary_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "history.jsonl"
            path.write_text("\n".join([
                json.dumps({"ok": True, "sources": [["legacy", 99]]}),
                json.dumps({
                    "ok": True,
                    "source_family_metric": "record-evidence-v2",
                    "source_evidence": [["current", 7]],
                }),
            ]) + "\n", encoding="utf-8")

            self.assertEqual(
                qa_ingestion_canary.latest_ok_history_sources(
                    path,
                    field="source_evidence",
                    metric="record-evidence-v2",
                ),
                [["current", 7]],
            )

    def test_history_floor_uses_only_matching_source_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "history.jsonl"
            path.write_text("\n".join([
                json.dumps({
                    "ok": True,
                    "source_family_metric": "record-evidence-v2",
                    "source_evidence": [["OpenChargeMap public snapshot", 20]],
                }),
                json.dumps({
                    "ok": True,
                    "source_family_metric": "record-evidence-v2",
                    "policy_exclusions": {"ids": ["electromaps-reuse-rights"]},
                    "source_evidence": [["OpenChargeMap public snapshot", 13]],
                }),
            ]) + "\n", encoding="utf-8")

            self.assertEqual(
                qa_ingestion_canary.latest_ok_history_sources(
                    path,
                    field="source_evidence",
                    metric="record-evidence-v2",
                    policy_ids=["electromaps-reuse-rights"],
                ),
                [["OpenChargeMap public snapshot", 13]],
            )
            self.assertEqual(
                qa_ingestion_canary.latest_ok_history_sources(
                    path,
                    field="source_evidence",
                    metric="record-evidence-v2",
                    policy_ids=["different-policy"],
                ),
                [],
            )


if __name__ == "__main__":
    unittest.main()
