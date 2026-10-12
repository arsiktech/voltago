import importlib.util
import pathlib
import subprocess
import sys
import unittest
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("qa_regression_suite", ROOT / "scripts" / "qa_regression_suite.py")
qa_regression_suite = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = qa_regression_suite
SPEC.loader.exec_module(qa_regression_suite)


class RegressionSuiteTests(unittest.TestCase):
    def test_workspace_revision_binds_source_data_and_qa_ledgers(self):
        revision = qa_regression_suite.current_regression_revision()

        self.assertRegex(revision["input_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(revision["canonical_recordset_sha256"], r"^[0-9a-f]{64}$")
        self.assertGreater(revision["qa_counts"]["test_cases"], 0)
        self.assertGreater(revision["qa_counts"]["defects"], 0)
        self.assertEqual(
            revision["qa_counts"]["test_cases"],
            sum(revision["qa_counts"]["test_results"].values()),
        )
        self.assertEqual(
            revision["qa_counts"]["defects"],
            sum(revision["qa_counts"]["defect_statuses"].values()),
        )

    def test_artifact_bootstrap_is_small_explicit_and_non_browser(self):
        checks = qa_regression_suite.suite_checks(
            "http://127.0.0.1:8773",
            "node",
            include_browser=False,
            include_external=False,
        )
        bootstrap = [check for check in checks if check.check_id in qa_regression_suite.BOOTSTRAP_CHECK_IDS]

        self.assertEqual(qa_regression_suite.BOOTSTRAP_CHECK_IDS, {check.check_id for check in bootstrap})
        self.assertTrue(all(not check.browser and not check.external for check in bootstrap))
        self.assertLess(len(bootstrap), len(checks))

    def test_full_unittest_inventory_has_current_runtime_budget(self):
        checks = qa_regression_suite.suite_checks(
            "http://127.0.0.1:8773",
            "node",
            include_browser=True,
            include_external=True,
        )

        by_id = {check.check_id: check for check in checks}

        self.assertGreaterEqual(by_id["unit-tests"].timeout, 180)

    def test_driver_readiness_has_browser_contention_timeout_budget(self):
        checks = qa_regression_suite.suite_checks(
            "http://127.0.0.1:8773",
            "node",
            include_browser=True,
            include_external=True,
        )

        by_id = {check.check_id: check for check in checks}

        self.assertGreaterEqual(by_id["driver-readiness"].timeout, 180)
        self.assertGreaterEqual(by_id["empty-state-keyboard"].timeout, 120)

    def test_browser_harnesses_use_the_shared_error_classifier(self):
        for relative in [
            "scripts/qa_empty_state_keyboard.js",
            "scripts/qa_ax_tree_contract.js",
            "scripts/qa_failure_ui.js",
        ]:
            with self.subTest(script=relative):
                script = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn('require("./qa_browser_harness")', script)
                self.assertIn("installDeterministicBasemap(page)", script)
                self.assertIn("unexpectedBrowserErrors(", script)
                self.assertNotIn("AJAXError: Failed to fetch", script)
                self.assertNotIn("TypeError: Failed to fetch", script)

    def test_driver_readiness_waits_for_settled_near_you_label_before_collect(self):
        script = (ROOT / "scripts" / "qa_driver_readiness.js").read_text(encoding="utf-8")
        phase_four = script.split("/* ---------------- Phase 4: geolocation -> live mode ---------------- */", 1)[1]
        before_collect = phase_four.split("const liveState = await collect(page);", 1)[0]

        self.assertIn(
            'const modeLabel = (modeLabelWrap && modeLabelWrap.textContent ? modeLabelWrap.textContent : "").replace(/\\s+/g, " ").trim();',
            before_collect,
        )
        self.assertIn('modeLabel === "Near you"', before_collect)

    def test_regression_suite_runs_core_browser_contracts(self):
        checks = qa_regression_suite.suite_checks(
            "http://127.0.0.1:8773",
            "node",
            include_browser=True,
            include_external=True,
        )

        check_ids = {check.check_id for check in checks}
        required = {
            "mobile-ui",
            "driver-readiness",
            "link-handoff",
            "failure-ui",
            "cdn-blocked",
            "focus-walk",
            "screen-reader",
            "ax-tree",
            "source-detail-a11y",
            "empty-state-keyboard",
            "geolocation-distance",
            "near-home-first-use",
            "realtime-refresh",
            "price-slider",
            "ingestion-canary",
            "completion-audit",
        }

        self.assertEqual(set(), required - check_ids)

    def test_browser_contracts_use_route_ready_badges_without_requiring_route_ready_live(self):
        preview = (ROOT / "scripts" / "qa_preview_to_live_refresh.js").read_text(encoding="utf-8")
        mobile = (ROOT / "scripts" / "qa_mobile_ui.js").read_text(encoding="utf-8")
        details = (ROOT / "scripts" / "qa_source_detail_transition_a11y.js").read_text(encoding="utf-8")

        self.assertIn(r"\s*leads\s*·\s*not live", preview)
        self.assertIn(r"\d+\s*leads\s*·\s*not live", mobile)
        self.assertNotIn("liveBadge.live <= 0", preview)
        self.assertNotIn("liveReadyTile > 0", preview)
        self.assertIn("firstCardNotLive", details)
        self.assertNotIn("(sample.liveCount || 0) >= 1", details)

    def test_failure_contract_bypasses_the_offline_service_worker(self):
        script = (ROOT / "scripts" / "qa_failure_ui.js").read_text(encoding="utf-8")

        self.assertIn('serviceWorkers: "block"', script)

    def test_full_suite_has_31_checks_and_compiles_near_home_contract(self):
        checks = qa_regression_suite.suite_checks(
            "http://127.0.0.1:8773",
            "node",
            include_browser=True,
            include_external=True,
        )

        by_id = {check.check_id: check for check in checks}

        self.assertEqual(31, len(checks))
        self.assertIn("near-home-first-use", by_id)
        self.assertTrue(by_id["near-home-first-use"].browser)
        self.assertGreaterEqual(by_id["near-home-first-use"].timeout, 180)
        self.assertEqual(
            ["node", "scripts/qa_near_home_first_use.js", "http://127.0.0.1:8773"],
            list(by_id["near-home-first-use"].command),
        )
        self.assertIn("scripts/qa_near_home_first_use.js", qa_regression_suite.JS_FILES)

    def test_qa_script_defaults_target_current_local_server(self):
        stale_ports = ("127.0.0.1:8767", "127.0.0.1:8768", "127.0.0.1:8770", "127.0.0.1:8772")
        offenders = []
        for path in (ROOT / "scripts").glob("qa_*"):
            if path.suffix not in {".py", ".js"}:
                continue
            text = path.read_text(encoding="utf-8")
            for port in stale_ports:
                if port in text:
                    offenders.append(f"{path.relative_to(ROOT)} contains stale default {port}")

        self.assertEqual([], offenders)

    def test_field_packet_freshness_rebuilds_packet_atomically(self):
        checks = qa_regression_suite.suite_checks(
            "http://127.0.0.1:8773",
            "node",
            include_browser=True,
            include_external=True,
        )

        by_id = {check.check_id: check for check in checks}
        command_text = "\n".join(by_id["field-packet-freshness"].command)

        self.assertIn("qa_field_test_packet.py", command_text)
        self.assertIn("qa_field_packet_freshness.py", command_text)
        self.assertIn("TemporaryDirectory", command_text)
        self.assertIn('"--packet"', command_text)
        self.assertIn('"--require-candidate-match"', command_text)
        self.assertNotIn('"qa/field-test-candidates.md"', command_text)
        self.assertNotIn('"qa/field-packet-freshness-latest.json"', command_text)
        self.assertGreaterEqual(by_id["field-packet-freshness"].timeout, 180)

    def test_field_packet_probes_provider_links_only_in_external_mode(self):
        base_url = "http://127.0.0.1:8773"
        for include_external in (False, True):
            with self.subTest(include_external=include_external):
                checks = qa_regression_suite.suite_checks(
                    base_url, "node", include_browser=False, include_external=include_external,
                )
                check = next(row for row in checks if row.check_id == "field-packet-freshness")
                command = list(check.command)
                with (
                    patch.object(sys, "argv", ["-c", *command[3:]]),
                    patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as runner,
                ):
                    exec(compile(command[2], "<field-packet-refresh>", "exec"), {})

                self.assertEqual(runner.call_count, 2)
                packet_command, freshness_command = [call.args[0] for call in runner.call_args_list]
                self.assertEqual("--check-links" in packet_command, include_external)
                self.assertIn("--require-candidate-match", packet_command)
                self.assertEqual(packet_command[packet_command.index("--base-url") + 1], base_url)
                self.assertEqual(freshness_command[freshness_command.index("--base-url") + 1], base_url)
                self.assertEqual(
                    packet_command[packet_command.index("--out") + 1],
                    freshness_command[freshness_command.index("--packet") + 1],
                )


if __name__ == "__main__":
    unittest.main()
