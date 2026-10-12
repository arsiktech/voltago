"""Runtime refresh safety fixtures. No upstream or production operations."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import runtime_refresh as refresh
import auto_refresh as shared


class RuntimeRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # Resolve: macOS temp dirs live under the /var -> /private/var symlink.
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "repo"
        self.state = self.base / "runtime"
        for name, rel in refresh.BUNDLE.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("old " + name)
        self.run = self.state / "runs" / "fixture"
        self.run.mkdir(parents=True)
        for name in refresh.BUNDLE:
            (self.run / name).write_text("new " + name)

    def tearDown(self):
        self.temp.cleanup()

    def test_first_run_uses_repository_baseline(self):
        self.assertEqual(refresh.baseline_paths(self.root, self.state)["free_intelligence.json"],
                         self.root / "data/free_intelligence.json")

    def test_switches_all_four_files_and_retains_old_bundle(self):
        refresh.publish_bundle(self.state, self.run)
        next_run = self.state / "runs" / "next"
        next_run.mkdir()
        for name in refresh.BUNDLE:
            (next_run / name).write_text("next " + name)
        refresh.publish_bundle(self.state, next_run)
        self.assertEqual(refresh.baseline_paths(self.root, self.state)["free_intelligence.json"],
                         next_run / "free_intelligence.json")
        for name in refresh.BUNDLE:
            self.assertEqual((self.state / "current" / name).read_text(), "next " + name)
            self.assertEqual((self.run / name).read_text(), "new " + name)

    def test_incomplete_bundle_does_not_change_current(self):
        refresh.publish_bundle(self.state, self.run)
        bad = self.state / "runs" / "incomplete"
        bad.mkdir()
        with self.assertRaisesRegex(RuntimeError, "missing"):
            refresh.publish_bundle(self.state, bad)
        self.assertEqual((self.state / "current").resolve(), self.run)

    def test_atomic_switch_failure_preserves_previous(self):
        refresh.publish_bundle(self.state, self.run)
        with mock.patch.object(refresh.os, "replace", side_effect=OSError("injected failure")):
            with self.assertRaises(OSError):
                refresh.publish_bundle(self.state, self.run)
        self.assertEqual((self.state / "current").resolve(), self.run)
        self.assertEqual(list(self.state.glob(".current-*")), [])

    def test_current_outside_owned_runs_is_rejected(self):
        (self.state / "current").symlink_to(self.root)
        with self.assertRaisesRegex(RuntimeError, "outside"):
            refresh.baseline_paths(self.root, self.state)

    def test_missing_id_rejected_even_when_delta_says_ok(self):
        with self.assertRaisesRegex(RuntimeError, "zero missing"):
            refresh.validate_gate_receipts({"ok": True, "delta": {"missing_ids": 1}}, {}, "candidate", "baseline")

    def test_gate_binds_exact_hashes(self):
        delta = {"ok": True, "delta": {"missing_ids": 0}}
        canary = {"ok": True, "candidate_reused": True, "candidate_sha256": "candidate",
                  "candidate_sha256_after": "candidate", "baseline_sha256": "baseline"}
        refresh.validate_gate_receipts(delta, canary, "candidate", "baseline")
        for field in canary:
            altered = {**canary, field: None}
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                refresh.validate_gate_receipts(delta, altered, "candidate", "baseline")

    def test_failed_command_is_not_masked(self):
        with self.assertRaisesRegex(RuntimeError, "exited 9"):
            refresh.run_stage(self.root, self.run, "build", [sys.executable, "-c", "print('diagnostic'); raise SystemExit(9)"], 5)
        self.assertEqual((self.run / "build.stdout.log").read_text().strip(), "diagnostic")

    def test_initialize_preserves_exact_bytes_and_original_date(self):
        db = self.root / 'data/free_intelligence.json'
        db.write_text(json.dumps({'records': [{'id': 'fixture'}], 'generated_at': '2026-09-17T00:00:00Z'}))
        before = db.read_bytes()
        self.assertEqual(refresh.initialize(self.root, self.state), 0)
        self.assertEqual((self.state / 'current/free_intelligence.json').read_bytes(), before)
        receipt = json.loads((self.state / 'initialization.json').read_text())
        self.assertEqual(receipt['generated_at'], '2026-09-17T00:00:00Z')
        self.assertFalse((self.state / 'last.json').exists())
        with self.assertRaisesRegex(RuntimeError, 'already initialized'):
            refresh.initialize(self.root, self.state)

    def test_pruning_retains_current_rollback_recent_and_unknown(self):
        paths = []
        for index in range(18):
            name = f'20261006T{index:06d}Z-00000000'
            p = self.state / 'runs' / name
            p.mkdir()
            (p / 'receipt.json').write_text(json.dumps({'run_id': name, 'ok': index in (0, 1), 'promoted': index in (0, 1)}))
            paths.append(p)
        (self.state / 'current').symlink_to(paths[0])
        unknown = self.state / 'runs/owner-notes'
        unknown.mkdir()
        linked = self.state / 'runs/20261006T999999Z-00000000'
        linked.symlink_to(unknown)
        removed = refresh.prune_runs(self.state, keep_recent=12)
        self.assertEqual(len(removed), 4)
        self.assertTrue(paths[0].exists())
        self.assertTrue(paths[1].exists())
        self.assertTrue(unknown.exists())
        self.assertTrue(linked.is_symlink())
        self.assertTrue(self.run.exists())

    def test_runtime_state_inside_checkout_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            refresh.refresh(self.root, self.root / "data/runtime", 1)

    def test_failed_build_keeps_current_and_records_failure(self):
        refresh.publish_bundle(self.state, self.run)
        with mock.patch.object(refresh, "source_identity", return_value={"head": "fixture"}), \
             mock.patch.object(refresh, "run_stage", side_effect=RuntimeError("fixture upstream failure")), \
             mock.patch.object(refresh.shared, "notify", return_value=False):
            self.assertEqual(refresh.refresh(self.root, self.state, 1), 1)
        self.assertEqual((self.state / "current").resolve(), self.run)
        receipt = json.loads((self.state / "last.json").read_text())
        self.assertFalse(receipt["ok"])
        self.assertFalse(receipt["promoted"])
        self.assertEqual(receipt["stage"], "build")
        self.assertIn("fixture upstream failure", receipt["error"])

    def test_successful_fixture_pipeline_publishes_bound_bundle(self):
        (self.root / "data/free_intelligence.json").write_text(json.dumps({"records": [{"id": "fixture-only"}]}))
        identity = {"head": "fixture"}
        def stage(root, run, name, argv, timeout):
            candidate = run / "free_intelligence.json"
            baseline = self.root / "data/free_intelligence.json"
            if name == "build":
                candidate.write_text(json.dumps({"records": [{"id": "fixture-only"}], "generated_at": "fixture"}))
                payload = {}
            elif name == "delta":
                payload = {"ok": True, "delta": {"missing_ids": 0}}
            else:
                sha = shared.sha256_file(candidate)
                payload = {"ok": True, "candidate_reused": True, "candidate_sha256": sha,
                           "candidate_sha256_after": sha, "baseline_sha256": shared.sha256_file(baseline)}
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
        def reports(**kwargs):
            kwargs["free_report"].write_text('{}')
            kwargs["valencia_report"].write_text('{}')
            return {"fixture": True}
        with mock.patch.object(refresh, "source_identity", return_value=identity), \
             mock.patch.object(refresh, "run_stage", side_effect=stage), \
             mock.patch.object(refresh.shared, "generate_reports", side_effect=reports):
            self.assertEqual(refresh.refresh(self.root, self.state, 1), 0)
        receipt = json.loads((self.state / "last.json").read_text())
        self.assertTrue(receipt["ok"])
        self.assertTrue(receipt["promoted"])
        self.assertEqual(receipt["records"], 1)
        self.assertEqual(json.loads((self.state / "current/free_intelligence.json").read_text())["records"][0]["id"], "fixture-only")

    def test_bundle_hash_mismatch_cannot_publish(self):
        refresh.publish_bundle(self.state, self.run)
        with self.assertRaisesRegex(RuntimeError, "changed"):
            refresh.publish_bundle(self.state, self.run, {name: "wrong" for name in refresh.BUNDLE})
        self.assertEqual((self.state / "current").resolve(), self.run)

    def test_post_switch_readback_failure_rolls_back(self):
        refresh.publish_bundle(self.state, self.run)
        next_run = self.state / "runs" / "next"
        next_run.mkdir()
        for name in refresh.BUNDLE:
            (next_run / name).write_text("next " + name)
        original = shared.sha256_file
        def hashed(path):
            if path.parent.name == "current":
                return "injected wrong hash"
            return original(path)
        with mock.patch.object(refresh.shared, "sha256_file", side_effect=hashed):
            with self.assertRaisesRegex(RuntimeError, "readback failed"):
                refresh.publish_bundle(self.state, next_run)
        self.assertEqual((self.state / "current").resolve(), self.run)

    def test_dry_run_does_not_overwrite_real_receipt(self):
        self.state.mkdir(exist_ok=True)
        (self.state / "last.json").write_text('{"real":true}')
        with mock.patch.object(refresh, "source_identity", side_effect=RuntimeError("fixture")), \
             mock.patch.object(refresh.shared, "notify", return_value=False):
            self.assertEqual(refresh.refresh(self.root, self.state, 1, dry_run=True), 1)
        self.assertEqual(json.loads((self.state / "last.json").read_text()), {"real": True})
        self.assertFalse((self.state / "current").exists())


class RuntimeAlertTests(unittest.TestCase):
    def test_three_failures_alert_once_and_success_resets(self):
        import runtime_refresh_alert as alerts
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            result = subprocess.CompletedProcess([], 0, '{"success":true,"message_id":123}', '')
            sequence = [False, False, False, False, True, False, False, False]
            with mock.patch.object(alerts.subprocess, 'run', return_value=result) as send:
                for index, ok in enumerate(sequence):
                    (state / 'last.json').write_text(json.dumps({'ok': ok, 'run_id': str(index)}))
                    self.assertEqual(alerts.check(state, 'telegram:fixture', '/fixture/hermes'), 0)
                    self.assertEqual(send.call_count, (1 if index >= 2 else 0) + (1 if index >= 7 else 0))
            self.assertEqual(json.loads((state / 'alert-state.json').read_text())['delivery'], 'confirmed')

    def test_ambiguous_send_is_not_retried(self):
        import runtime_refresh_alert as alerts
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            (state / 'alert-state.json').write_text('{"streak":2}')
            (state / 'last.json').write_text('{"ok":false,"run_id":"fixture-3"}')
            with mock.patch.object(alerts.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixture', 45)) as send:
                self.assertEqual(alerts.check(state, 'telegram:fixture', '/fixture/hermes'), 1)
                (state / 'last.json').write_text('{"ok":false,"run_id":"fixture-4"}')
                self.assertEqual(alerts.check(state, 'telegram:fixture', '/fixture/hermes'), 0)
                self.assertEqual(send.call_count, 1)


class RuntimeIntelligencePathTests(unittest.TestCase):
    def test_bundle_switch_invalidates_same_timestamp_raw_cache(self):
        import server
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('old', 'new'):
                p = root / name
                p.mkdir()
                db = p / 'free_intelligence.json'
                db.write_text(json.dumps({'records': [{'id': name}], 'total_records': 1}))
                os.utime(db, (1000000000, 1000000000))
            current = root / 'current'
            current.symlink_to('old')
            with mock.patch.dict(os.environ, {'VOLTA_INTELLIGENCE_DB': str(current / 'free_intelligence.json')}), \
                 mock.patch.dict(server._INTEL_RAW, {'mtime': None, 'data': None}, clear=True):
                self.assertEqual(server.load_intelligence_meta()['records'][0]['id'], 'old')
                replacement = root / 'replacement'
                replacement.symlink_to('new')
                os.replace(replacement, current)
                self.assertEqual(server.load_intelligence_meta()['records'][0]['id'], 'new')

    def test_default_tracks_patched_data_directory(self):
        import server
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(server, "DATA", Path("/fixture/data")):
            os.environ.pop("VOLTA_INTELLIGENCE_DB", None)
            self.assertEqual(server.intelligence_path(), Path("/fixture/data/free_intelligence.json"))

    def test_explicit_runtime_path_used(self):
        import server
        with mock.patch.dict(os.environ, {"VOLTA_INTELLIGENCE_DB": "/fixture/runtime/current/free_intelligence.json"}):
            self.assertEqual(server.intelligence_path(), Path("/fixture/runtime/current/free_intelligence.json"))


if __name__ == "__main__":
    unittest.main()
