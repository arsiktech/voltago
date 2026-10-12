import importlib.util
import csv
import hashlib
import json
import pathlib
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timezone
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("qa_artifact_integrity", ROOT / "scripts" / "qa_artifact_integrity.py")
qa_artifact_integrity = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(qa_artifact_integrity)


def json_dump(payload):
    return json.dumps(payload, ensure_ascii=False, indent=2)


def valid_feature(**overrides):
    row = {
        "Feature ID": "F-999",
        "Feature Name": "Current feature",
        "Feature Type": "workflow",
        "User Story": "As a driver I can use the current app.",
        "Entry Points": "/; frontend/src/App.tsx",
        "Source Evidence": "frontend/src/App.tsx; tests/test_artifact_integrity.py",
        "Observed Behaviour": "The built React SPA renders the current workflow.",
        "Inferred Intended Behaviour": "The current workflow remains available.",
        "Validation Rules": "The current workflow is covered by tests.",
        "Edge Cases": "Network unavailable.",
        "Dependencies": "React SPA; /api/chargers.",
        "Risk Level": "High",
        "Priority": "P1",
        "Coverage Status": "pass",
        "Defect Count": "0",
        "Notes": "Historical notes may describe retired implementations.",
        "Last Updated": "2026-07-13",
    }
    row.update(overrides)
    return row


def valid_test_case(**overrides):
    row = {
        "Test Case ID": "TC-999",
        "Feature ID": "F-001",
        "Test Name": "Current contract",
        "Test Type": "e2e",
        "Priority": "P1",
        "Preconditions": "Local server running on http://127.0.0.1:8773.",
        "Steps": "Run the current browser contract against http://127.0.0.1:8773.",
        "Expected Result": "The built React SPA remains usable.",
        "Automation Status": "existing",
        "Evidence Link / Test File": "frontend/src/App.tsx; scripts/qa_mobile_ui.js",
        "Last Result": "pass",
        "Last Run Date": "2026-07-13",
        "Notes": "Historical notes may describe retired implementations.",
    }
    row.update(overrides)
    return row


# Generated QA receipts record machine paths and host details, so this public
# repository does not track them. Generate them with the README sequence; these
# checks then validate the local receipts instead of being skipped.
SKIP_WITHOUT_LOCAL_RECEIPTS = unittest.skipUnless(
    (ROOT / "qa" / "regression-suite-latest.json").exists(),
    "QA receipts are generated locally and not tracked in the public repository",
)


class ArtifactIntegrityTests(unittest.TestCase):
    @SKIP_WITHOUT_LOCAL_RECEIPTS
    def test_current_artifacts_pass_integrity(self):
        report = qa_artifact_integrity.build_report()
        with (ROOT / "qa" / "test-cases.csv").open(newline="", encoding="utf-8") as fh:
            expected_test_results = dict(Counter(row["Last Result"] for row in csv.DictReader(fh)))
        with (ROOT / "qa" / "defects.csv").open(newline="", encoding="utf-8") as fh:
            # Computed from the CSV rather than hardcoded, same as before D-149: this must
            # match reality exactly, whatever status mix the ledger actually has (plans/009
            # introduced the first non-Verified row -- D-149 is Waived, not Fixed, by design;
            # see qa/defects.csv).
            expected_statuses = dict(Counter(row["Status"] for row in csv.DictReader(fh)))

        self.assertTrue(report["ok"], report["failures"])
        self.assertEqual(report["counts"]["test_results"], expected_test_results)
        self.assertEqual(report["counts"]["defect_statuses"], expected_statuses)

    @SKIP_WITHOUT_LOCAL_RECEIPTS
    def test_current_named_data_reports_are_bound_to_exact_canonical_bytes(self):
        for filename, kind in (
            ("free-db-completeness-latest.json", "free-db"),
            ("valencia-source-audit-latest.json", "valencia"),
        ):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as tmp:
                source = ROOT / "qa" / filename
                payload = json.loads(source.read_text(encoding="utf-8"))
                payload["data_sha256"] = "0" * 64
                forged = pathlib.Path(tmp) / filename
                forged.write_text(json_dump(payload), encoding="utf-8")
                failures = []

                qa_artifact_integrity.validate_data_snapshot_report(
                    forged,
                    ROOT / "data" / "free_intelligence.json",
                    failures,
                    kind=kind,
                )

                self.assertTrue(any("data_sha256" in failure for failure in failures), failures)

    def test_read_csv_reports_extra_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "bad.csv"
            path.write_text("A,B\n1,2,3\n", encoding="utf-8")
            failures = []

            rows = qa_artifact_integrity.read_csv(path, ["A", "B"], failures)

        self.assertEqual(rows, [])
        self.assertTrue(any("extra columns" in item for item in failures))

    def test_validate_tests_rejects_bad_result_and_unknown_feature(self):
        rows = [{
            "Test Case ID": "TC-999",
            "Feature ID": "F-404",
            "Test Name": "Bad row",
            "Test Type": "static",
            "Priority": "P1",
            "Preconditions": "x",
            "Steps": "x",
            "Expected Result": "x",
            "Automation Status": "new",
            "Evidence Link / Test File": "x",
            "Last Result": "new",
            "Last Run Date": "2026-06-23",
            "Notes": "x",
        }]
        failures = []

        qa_artifact_integrity.validate_tests(rows, {"F-001"}, failures)

        self.assertTrue(any("unknown feature F-404" in item for item in failures))
        self.assertTrue(any("invalid Test Type 'static'" in item for item in failures))
        self.assertTrue(any("invalid Automation Status 'new'" in item for item in failures))
        self.assertTrue(any("invalid Last Result 'new'" in item for item in failures))

    def test_validate_tests_rejects_stale_localhost_in_active_instructions(self):
        rows = [valid_test_case(
            **{
                "Test Name": "Stale localhost",
                "Preconditions": "Local server running on http://127.0.0.1:8772.",
                "Steps": "Run the check against 8767.",
                "Notes": "Historical notes may mention old ports, but active instructions may not.",
            }
        )]
        failures = []

        qa_artifact_integrity.validate_tests(rows, {"F-001"}, failures)

        self.assertTrue(any("Preconditions references stale local app port 8772" in item for item in failures))
        self.assertTrue(any("Steps references stale local app port 8767" in item for item in failures))

    def test_validate_tests_rejects_every_known_stale_bare_port(self):
        for port in ("8767", "8768", "8769", "8770", "8771", "8772", "8785"):
            with self.subTest(port=port):
                failures = []
                qa_artifact_integrity.validate_tests(
                    [valid_test_case(**{"Steps": f"Run the browser contract against {port}."})],
                    {"F-001"},
                    failures,
                )
                self.assertTrue(
                    any(f"Steps references stale local app port {port}" in item for item in failures),
                    failures,
                )

    def test_validate_tests_rejects_retired_ui_sources_and_current_terms(self):
        cases = [
            ("Steps", "Run node --check static/app.js."),
            ("Evidence Link / Test File", "scripts/qa_accessibility_static.py"),
            ("Expected Result", "Leaflet remains a progressive enhancement."),
            ("Steps", "Run source/static/unit checks."),
            ("Test Name", "PWA and static mobile shell contract"),
            ("Steps", "Parse static HTML, then fetch the app shell."),
            ("Expected Result", "Blocking https://unpkg.com still renders the shell."),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                failures = []
                qa_artifact_integrity.validate_tests(
                    [valid_test_case(**{field: value})],
                    {"F-001"},
                    failures,
                )
                self.assertTrue(any("retired UI" in item for item in failures), failures)

    def test_validate_tests_allows_legacy_terms_only_in_notes_and_canonical_port(self):
        failures = []
        qa_artifact_integrity.validate_tests(
            [valid_test_case(**{
                "Notes": (
                    "Historical: static/app.js and scripts/qa_accessibility_static.py used "
                    "Leaflet from https://unpkg.com on 127.0.0.1:8769."
                ),
            })],
            {"F-001"},
            failures,
        )
        self.assertEqual([], failures)

    def test_validate_tests_allows_only_the_explicitly_retired_rows_with_markers(self):
        retired = valid_test_case(**{
            "Test Case ID": "TC-016",
            "Preconditions": "static/index.html exists.",
            "Evidence Link / Test File": "scripts/qa_accessibility_static.py; static/styles.css",
            "Notes": "RETIRED 2026-07-12; superseded by TC-114.",
        })
        failures = []
        qa_artifact_integrity.validate_tests([retired], {"F-001"}, failures)
        self.assertEqual([], failures)

        failures = []
        qa_artifact_integrity.validate_tests(
            [{**retired, "Notes": "Historical evidence only."}],
            {"F-001"},
            failures,
        )
        self.assertTrue(any("retired-test marker" in item for item in failures), failures)

    def test_validate_features_rejects_retired_ui_sources_and_current_terms(self):
        cases = [
            ("Entry Points", "/; static/index.html"),
            ("Source Evidence", "scripts/qa_accessibility_static.py"),
            ("Observed Behaviour", "Plain HTML app shell loads Leaflet."),
            ("Validation Rules", "Blocking https://unpkg.com keeps cards visible."),
            ("Dependencies", "Leaflet CDN; static CSS/JS."),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                failures = []
                qa_artifact_integrity.validate_features(
                    [valid_feature(**{field: value})],
                    failures,
                )
                self.assertTrue(any("retired UI" in item for item in failures), failures)

    def test_validate_features_allows_legacy_terms_in_historical_notes(self):
        failures = []
        qa_artifact_integrity.validate_features(
            [valid_feature(**{
                "Notes": (
                    "Historical: the Plain HTML app shell used static/app.js, Leaflet, "
                    "and scripts/qa_accessibility_static.py from https://unpkg.com."
                ),
            })],
            failures,
        )
        self.assertEqual([], failures)

    def test_validate_tests_rejects_stale_current_regression_suite_counts(self):
        rows = [{
            "Test Case ID": "TC-999",
            "Feature ID": "F-001",
            "Test Name": "Stale regression evidence",
            "Test Type": "integration",
            "Priority": "P1",
            "Preconditions": "Local server running on http://127.0.0.1:8773.",
            "Steps": "Run the regression suite.",
            "Expected Result": "x",
            "Automation Status": "existing",
            "Evidence Link / Test File": "qa/regression-suite-latest.json",
            "Last Result": "pass",
            "Last Run Date": "2026-06-23",
            "Notes": "Latest regression run passed with ok=true, total=19, passed=19, failed=0.",
        }]
        failures = []

        qa_artifact_integrity.validate_tests(rows, {"F-001"}, failures)

        self.assertTrue(any("stale current regression-suite evidence" in item for item in failures))

    def test_validate_defects_rejects_stale_final_regression_suite_counts(self):
        rows = [{
            "Defect ID": "D-999",
            "Feature ID": "F-001",
            "Test Case ID": "TC-001",
            "Title": "Stale final evidence",
            "Severity": "Medium",
            "Status": "Verified",
            "Reproduction Steps": "x",
            "Expected Result": "x",
            "Actual Result": "x",
            "Evidence": "Pre-fix suite had 19 checks.",
            "Root Cause Hypothesis": "x",
            "Fix Plan": "x",
            "Fix Commit / Files Changed": "x",
            "Verification Evidence": "Final scripts/qa_regression_suite.py --include-external passed 27/27.",
            "Notes": "Historical pre-fix total=19 can be prose, but final evidence must be current.",
        }]
        failures = []

        qa_artifact_integrity.validate_defects(rows, {"F-001"}, {"TC-001"}, failures)

        self.assertTrue(any("D-999 Verification Evidence contains stale current regression-suite evidence" in item for item in failures))

    def test_field_packet_freshness_rejects_raw_technical_warning_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "field-packet-freshness-latest.json"
            path.write_text('{"ok": true, "current_state": {"default_warning": "HTTPError: HTTP Error 429: Too Many Requests"}}', encoding="utf-8")
            failures = []

            qa_artifact_integrity.validate_field_packet_freshness_report(path, failures)

        self.assertTrue(any("raw technical warning token" in item for item in failures))

    def test_field_packet_freshness_allows_sanitized_technical_failure_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "field-packet-freshness-latest.json"
            path.write_text(
                '{"ok": true, "current_state": {"default_warning": "Live refresh limited; verify source before driving", '
                '"default_warning_had_technical_failure": true}}',
                encoding="utf-8",
            )
            failures = []

            qa_artifact_integrity.validate_field_packet_freshness_report(path, failures)

        self.assertEqual(failures, [])

    def test_field_packet_freshness_binds_report_to_current_canonical_and_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            canonical = root / "free_intelligence.json"
            packet = root / "field-test-candidates.md"
            report_path = root / "field-packet-freshness-latest.json"
            records = [{"id": "current", "source": "test"}]
            canonical_sha = qa_artifact_integrity.stable_recordset_sha256(records)
            canonical.write_text(
                json.dumps({"records": records, "recordset_sha256": canonical_sha}),
                encoding="utf-8",
            )
            packet_text = "\n".join([
                "Base URL: `https://voltago.app`",
                "Expected local recordset SHA-256: `prior`",
                "Deployed recordset SHA-256: `prior`",
                "Candidate revision match: **yes**",
            ])
            packet.write_text(packet_text, encoding="utf-8")
            report_path.write_text(
                json.dumps({
                    "ok": True,
                    "checked_at": "2020-01-01T00:00:00+00:00",
                    "packet": str(packet),
                    "packet_sha256": hashlib.sha256(packet_text.encode("utf-8")).hexdigest(),
                    "base_url": "https://wrong.example",
                    "canonical_data": str(canonical),
                    "canonical_recordset_sha256": "prior",
                    "packet_state": {
                        "base_url": "https://voltago.app",
                        "expected_recordset_sha256": "prior",
                        "deployed_recordset_sha256": "prior",
                        "candidate_revision_match": True,
                    },
                    "current_state": {"deployed_recordset_sha256": "prior"},
                    "current_state_error": None,
                    "warning_drifts": [],
                    "failures": [],
                }),
                encoding="utf-8",
            )
            failures = []

            qa_artifact_integrity.validate_field_packet_freshness_report(
                report_path,
                failures,
                canonical_path=canonical,
                packet_path=packet,
                max_age_seconds=60,
            )

        self.assertTrue(any("is stale" in failure for failure in failures))
        self.assertTrue(any("canonical_recordset_sha256" in failure for failure in failures))
        self.assertTrue(any("base_url does not match" in failure for failure in failures))
        self.assertTrue(any("claims ok" in failure for failure in failures))

    def test_field_packet_age_is_a_warning_outside_release_but_bindings_still_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            canonical = root / "free_intelligence.json"
            packet = root / "field-test-candidates.md"
            report_path = root / "field-packet-freshness-latest.json"
            records = [{"id": "current", "source": "test"}]
            canonical.write_text(json.dumps({"records": records}), encoding="utf-8")
            packet.write_text("Base URL: `https://voltago.app`", encoding="utf-8")
            report_path.write_text(
                json.dumps({
                    "ok": True,
                    "checked_at": "2020-01-01T00:00:00+00:00",
                    "packet": str(packet),
                    "packet_sha256": "x",
                    "base_url": "https://voltago.app",
                    "canonical_data": str(canonical),
                    "canonical_recordset_sha256": "prior",
                    "packet_state": {},
                    "current_state": {},
                    "current_state_error": None,
                    "warning_drifts": [],
                    "failures": [],
                }),
                encoding="utf-8",
            )
            routine_failures, routine_warnings = [], []
            qa_artifact_integrity.validate_field_packet_freshness_report(
                report_path, routine_failures, canonical_path=canonical, packet_path=packet,
                max_age_seconds=60, enforce_age=False, warnings=routine_warnings,
            )
            release_failures = []
            qa_artifact_integrity.validate_field_packet_freshness_report(
                report_path, release_failures, canonical_path=canonical, packet_path=packet,
                max_age_seconds=60,
            )

        # Routine run: age is reported, not failed ...
        self.assertFalse(any("is stale" in f for f in routine_failures))
        self.assertTrue(any("is stale" in w and "--release" in w for w in routine_warnings))
        # ... but content bindings still fail closed.
        self.assertTrue(any("canonical_recordset_sha256" in f for f in routine_failures))
        # Release run (the default): age fails.
        self.assertTrue(any("is stale" in f for f in release_failures))

    def test_release_mode_reads_environment(self):
        for value, expected in (("1", True), ("true", True), ("", False), ("0", False)):
            with mock.patch.dict("os.environ", {qa_artifact_integrity.RELEASE_ENV: value}):
                self.assertEqual(qa_artifact_integrity.release_mode_from_env(), expected, value)

    def test_field_packet_freshness_accepts_complete_fail_closed_legacy_deployment_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            canonical = root / "free_intelligence.json"
            packet = root / "field-test-candidates.md"
            report_path = root / "field-packet-freshness-latest.json"
            records = [{"id": "current", "source": "test"}]
            canonical_sha = qa_artifact_integrity.stable_recordset_sha256(records)
            canonical.write_text(
                json.dumps({"records": records, "recordset_sha256": canonical_sha}),
                encoding="utf-8",
            )
            packet_text = "\n".join([
                "Base URL: `https://voltago.app`",
                f"Expected local recordset SHA-256: `{canonical_sha}`",
                "Deployed recordset SHA-256: `unknown`",
                "Candidate revision match: **no**",
            ])
            packet.write_text(packet_text, encoding="utf-8")
            error = "current deployment does not expose recordset_sha256"
            report_path.write_text(
                json.dumps({
                    "ok": False,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "packet": str(packet),
                    "packet_sha256": hashlib.sha256(packet_text.encode("utf-8")).hexdigest(),
                    "base_url": "https://voltago.app",
                    "canonical_data": str(canonical),
                    "canonical_recordset_sha256": canonical_sha,
                    "packet_state": {
                        "base_url": "https://voltago.app",
                        "expected_recordset_sha256": canonical_sha,
                        "deployed_recordset_sha256": "unknown",
                        "candidate_revision_match": False,
                    },
                    "current_state": {
                        "deployed_recordset_sha256": None,
                        "error": error,
                    },
                    "current_state_error": error,
                    "warning_drifts": [],
                    "failures": [error],
                }),
                encoding="utf-8",
            )
            failures = []

            qa_artifact_integrity.validate_field_packet_freshness_report(
                report_path,
                failures,
                canonical_path=canonical,
                packet_path=packet,
                max_age_seconds=60,
            )

        self.assertEqual(failures, [])

    def test_manual_proof_status_rejects_ready_without_manual_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "manual-proof-status.json"
            path.write_text(
                """
{
  "ok": true,
  "ready_to_complete_manual_gates": true,
  "checked_at": "2026-06-23T03:34:55+00:00",
  "evidence_path": "qa/manual-proof-evidence.json",
  "evidence_present": false,
  "evidence_error": null,
  "artifacts": {
    "field_checklist": {"ok": true},
    "voiceover_checklist": {"ok": true},
    "field_candidate_packet": {"ok": true},
    "manual_evidence_template": {"ok": true}
  },
  "gates": [
    {"gate": "TC-051", "status": "blocked"},
    {"gate": "TC-056", "status": "blocked"}
  ],
  "blocked_gates": [],
  "failed_gates": [],
  "artifact_failures": []
}
""",
                encoding="utf-8",
            )
            failures = []

            qa_artifact_integrity.validate_manual_proof_status_report(path, failures)

        self.assertTrue(any("ready_to_complete_manual_gates does not match" in item for item in failures))
        self.assertTrue(any("blocked_gates does not match" in item for item in failures))
        self.assertTrue(any("cannot be ready when evidence_present=false" in item for item in failures))

    def test_manual_proof_status_accepts_current_missing_evidence_blocked_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "manual-proof-status.json"
            path.write_text(
                """
{
  "ok": true,
  "ready_to_complete_manual_gates": false,
  "checked_at": "2026-06-23T03:34:55+00:00",
  "evidence_path": "qa/manual-proof-evidence.json",
  "evidence_present": false,
  "evidence_error": null,
  "artifacts": {
    "field_checklist": {"ok": true},
    "voiceover_checklist": {"ok": true},
    "field_candidate_packet": {"ok": true},
    "manual_evidence_template": {"ok": true}
  },
  "gates": [
    {"gate": "TC-051", "status": "blocked"},
    {"gate": "TC-056", "status": "blocked"}
  ],
  "blocked_gates": ["TC-051", "TC-056"],
  "failed_gates": [],
  "artifact_failures": []
}
""",
                encoding="utf-8",
            )
            failures = []

            qa_artifact_integrity.validate_manual_proof_status_report(path, failures)

        self.assertEqual(failures, [])

    def test_regression_suite_report_rejects_contradictory_pass_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "regression-suite-latest.json"
            path.write_text(
                json_dump({
                    "ok": True,
                    "checked_at": "2026-06-23T03:41:11+00:00",
                    "base_url": "http://127.0.0.1:8773",
                    **qa_artifact_integrity.current_regression_revision(),
                    "total": 2,
                    "passed": 2,
                    "failed": 0,
                    "failed_ids": [],
                    "results": [
                        {"id": "api-smoke", "ok": True},
                        {"id": "driver-readiness", "ok": False},
                    ],
                }),
                encoding="utf-8",
            )
            failures = []

            qa_artifact_integrity.validate_regression_suite_report(path, failures)

        self.assertTrue(any("failed_ids does not match non-ok result rows" in item for item in failures))

    def test_regression_suite_report_rejects_missing_required_check_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = [
                {"id": check_id, "ok": True}
                for check_id in sorted(qa_artifact_integrity.REQUIRED_REGRESSION_CHECK_IDS - {"completion-audit"})
            ]
            path = pathlib.Path(tmp) / "regression-suite-latest.json"
            path.write_text(
                json_dump({
                    "ok": True,
                    "checked_at": "2026-06-23T03:41:11+00:00",
                    "base_url": "http://127.0.0.1:8773",
                    **qa_artifact_integrity.current_regression_revision(),
                    "total": len(results),
                    "passed": len(results),
                    "failed": 0,
                    "failed_ids": [],
                    "results": results,
                }),
                encoding="utf-8",
            )
            failures = []

            qa_artifact_integrity.validate_regression_suite_report(path, failures)

        self.assertTrue(any("missing required regression check ids" in item and "completion-audit" in item for item in failures))

    def test_regression_suite_report_accepts_current_green_shape(self):
        # Post-SPA-pivot shape: browser DOM contracts explicitly skipped (pending port),
        # external checks explicitly recorded, Python check family complete.
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "regression-suite-latest.json"
            check_ids = sorted(
                qa_artifact_integrity.REQUIRED_REGRESSION_CHECK_IDS
                | qa_artifact_integrity.EXTERNAL_REGRESSION_CHECK_IDS
            )
            path.write_text(
                json_dump({
                    "ok": True,
                    "checked_at": "2026-06-23T03:41:11+00:00",
                    "base_url": "http://127.0.0.1:8773",
                    **qa_artifact_integrity.current_regression_revision(),
                    "include_browser": False,
                    "include_external": True,
                    "total": len(check_ids),
                    "passed": len(check_ids),
                    "failed": 0,
                    "failed_ids": [],
                    "results": [{"id": check_id, "ok": True} for check_id in check_ids],
                }),
                encoding="utf-8",
            )
            failures = []

            qa_artifact_integrity.validate_regression_suite_report(path, failures)

        self.assertEqual(failures, [])

    def test_regression_suite_report_rejects_stale_workspace_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "regression-suite-latest.json"
            check_ids = sorted(qa_artifact_integrity.REQUIRED_REGRESSION_CHECK_IDS)
            revision = qa_artifact_integrity.current_regression_revision()
            path.write_text(
                json_dump({
                    "ok": True,
                    "checked_at": "2026-07-15T18:00:00+00:00",
                    "base_url": "http://127.0.0.1:8773",
                    **revision,
                    "input_sha256": "0" * 64,
                    "canonical_recordset_sha256": "1" * 64,
                    "qa_counts": {"test_cases": 1, "test_results": {"pass": 1}, "defects": 1, "defect_statuses": {"Verified": 1}},
                    "include_browser": False,
                    "include_external": False,
                    "total": len(check_ids),
                    "passed": len(check_ids),
                    "failed": 0,
                    "failed_ids": [],
                    "results": [{"id": check_id, "ok": True} for check_id in check_ids],
                }),
                encoding="utf-8",
            )
            failures = []

            qa_artifact_integrity.validate_regression_suite_report(path, failures)

        self.assertTrue(any("input_sha256 does not match" in item for item in failures))
        self.assertTrue(any("canonical_recordset_sha256 does not match" in item for item in failures))
        self.assertTrue(any("qa_counts does not match" in item for item in failures))

    def test_regression_suite_report_accepts_explicit_artifact_bootstrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "regression-suite-latest.json"
            check_ids = sorted(qa_artifact_integrity.BOOTSTRAP_REGRESSION_CHECK_IDS)
            path.write_text(
                json_dump({
                    "ok": True,
                    "checked_at": "2026-07-15T18:00:00+00:00",
                    "base_url": "http://127.0.0.1:8773",
                    **qa_artifact_integrity.current_regression_revision(),
                    "bootstrap_artifacts": True,
                    "include_browser": False,
                    "include_external": False,
                    "total": len(check_ids),
                    "passed": len(check_ids),
                    "failed": 0,
                    "failed_ids": [],
                    "results": [{"id": check_id, "ok": True} for check_id in check_ids],
                }),
                encoding="utf-8",
            )
            failures = []

            qa_artifact_integrity.validate_regression_suite_report(path, failures)

        self.assertEqual([], failures)

    def test_regression_suite_report_requires_explicit_skip_flags(self):
        # A report that neither runs nor explicitly skips the browser/external families
        # is a silent skip — the integrity audit must flag it.
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "regression-suite-latest.json"
            check_ids = sorted(qa_artifact_integrity.REQUIRED_REGRESSION_CHECK_IDS)
            path.write_text(
                json_dump({
                    "ok": True,
                    "checked_at": "2026-06-23T03:41:11+00:00",
                    "base_url": "http://127.0.0.1:8773",
                    **qa_artifact_integrity.current_regression_revision(),
                    "total": len(check_ids),
                    "passed": len(check_ids),
                    "failed": 0,
                    "failed_ids": [],
                    "results": [{"id": check_id, "ok": True} for check_id in check_ids],
                }),
                encoding="utf-8",
            )
            failures = []

            qa_artifact_integrity.validate_regression_suite_report(path, failures)

        self.assertTrue(any("include_browser" in item for item in failures))
        self.assertTrue(any("include_external" in item for item in failures))

    def test_completion_audit_rejects_false_complete_with_blocked_manual_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "completion-audit-latest.json"
            path.write_text(
                """
{
  "ok": true,
  "complete": true,
  "checked_at": "2026-06-23T03:51:11+00:00",
  "qa_counts": {"test_results": {}},
  "automated_evidence": {
    "regression_suite_ok": true,
    "artifact_integrity_ok": true,
    "field_packet_freshness_ok": true
  },
  "manual_gates": {
    "ready_to_complete_manual_gates": false,
    "blocked_gates": ["TC-051", "TC-056"],
    "failed_gates": []
  },
  "required_artifacts": {
    "features": {"present": true, "ok": true},
    "test_cases": {"present": true, "ok": true},
    "defects": {"present": true, "ok": true},
    "coverage_summary": {"present": true, "ok": true},
    "run_log": {"present": true, "ok": true},
    "manual_proof_status": {"present": true, "ok": true},
    "field_test_candidates": {"present": true, "ok": true},
    "voiceover_checklist": {"present": true, "ok": true}
  },
  "completion_blockers": [],
  "failures": []
}
""",
                encoding="utf-8",
            )
            failures = []

            qa_artifact_integrity.validate_completion_audit_report(path, failures)

        self.assertTrue(any("complete does not match" in item for item in failures))
        self.assertTrue(any("cannot be complete while TC-051 and TC-056 are blocked" in item for item in failures))

    def test_completion_audit_accepts_current_incomplete_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "completion-audit-latest.json"
            path.write_text(
                """
{
  "ok": true,
  "complete": false,
  "checked_at": "2026-06-23T03:51:11+00:00",
  "qa_counts": {"test_results": {}},
  "automated_evidence": {
    "regression_suite_ok": true,
    "artifact_integrity_ok": true,
    "field_packet_freshness_ok": true
  },
  "manual_gates": {
    "ready_to_complete_manual_gates": false,
    "blocked_gates": ["TC-051", "TC-056"],
    "failed_gates": []
  },
  "required_artifacts": {
    "features": {"present": true, "ok": true},
    "test_cases": {"present": true, "ok": true},
    "defects": {"present": true, "ok": true},
    "coverage_summary": {"present": true, "ok": true},
    "run_log": {"present": true, "ok": true},
    "manual_proof_status": {"present": true, "ok": true},
    "field_test_candidates": {"present": true, "ok": true},
    "voiceover_checklist": {"present": true, "ok": true}
  },
  "completion_blockers": [
    "manual proof gates blocked: TC-051, TC-056",
    "blocked test cases remain: TC-051, TC-056"
  ],
  "failures": []
}
""",
                encoding="utf-8",
            )
            failures = []

            qa_artifact_integrity.validate_completion_audit_report(path, failures)

        self.assertEqual(failures, [])

    def test_completion_audit_accepts_fail_closed_field_packet_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "completion-audit-latest.json"
            payload = {
                "ok": True,
                "complete": False,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "qa_counts": {"test_results": {}},
                "automated_evidence": {
                    "regression_suite_ok": True,
                    "artifact_integrity_ok": True,
                    "field_packet_freshness_ok": False,
                },
                "manual_gates": {
                    "ready_to_complete_manual_gates": False,
                    "blocked_gates": ["TC-051", "TC-056"],
                    "failed_gates": [],
                },
                "required_artifacts": {
                    name: {"present": True, "ok": True}
                    for name in {
                        "features",
                        "test_cases",
                        "defects",
                        "coverage_summary",
                        "run_log",
                        "manual_proof_status",
                        "field_test_candidates",
                        "voiceover_checklist",
                    }
                },
                "completion_blockers": [
                    "field-packet-freshness-latest is not green",
                    "manual proof gates blocked: TC-051, TC-056",
                    "blocked test cases remain: TC-051, TC-056",
                ],
                "failures": [],
            }
            path.write_text(json_dump(payload), encoding="utf-8")
            failures = []

            qa_artifact_integrity.validate_completion_audit_report(path, failures)

        self.assertEqual(failures, [])

    def test_completion_audit_rejects_stale_qa_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "completion-audit-latest.json"
            path.write_text(
                """
{
  "ok": true,
  "complete": false,
  "checked_at": "2026-06-23T03:51:11+00:00",
  "qa_counts": {
    "test_cases": 87,
    "test_results": {"pass": 85, "blocked": 2},
    "defects": 69,
    "defect_statuses": {"Verified": 69}
  },
  "automated_evidence": {
    "regression_suite_ok": true,
    "artifact_integrity_ok": true,
    "field_packet_freshness_ok": true
  },
  "manual_gates": {
    "ready_to_complete_manual_gates": false,
    "blocked_gates": ["TC-051", "TC-056"],
    "failed_gates": []
  },
  "required_artifacts": {
    "features": {"present": true, "ok": true},
    "test_cases": {"present": true, "ok": true},
    "defects": {"present": true, "ok": true},
    "coverage_summary": {"present": true, "ok": true},
    "run_log": {"present": true, "ok": true},
    "manual_proof_status": {"present": true, "ok": true},
    "field_test_candidates": {"present": true, "ok": true},
    "voiceover_checklist": {"present": true, "ok": true}
  },
  "completion_blockers": ["manual proof gates blocked: TC-051, TC-056"],
  "failures": []
}
""",
                encoding="utf-8",
            )
            failures = []

            qa_artifact_integrity.validate_completion_audit_report(
                path,
                failures,
                expected_total=88,
                expected_passed=86,
                expected_blocked=2,
                expected_defects=70,
            )

        self.assertTrue(any("qa_counts.test_cases" in item for item in failures))
        self.assertTrue(any("qa_counts.defects" in item for item in failures))

    def test_completion_audit_rejects_bad_required_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "completion-audit-latest.json"
            payload = {
                "ok": False,
                "complete": False,
                "checked_at": "2026-06-23T03:51:11+00:00",
                "qa_counts": {"test_results": {}},
                "automated_evidence": {
                    "regression_suite_ok": True,
                    "artifact_integrity_ok": True,
                    "field_packet_freshness_ok": True,
                },
                "manual_gates": {
                    "ready_to_complete_manual_gates": False,
                    "blocked_gates": ["TC-051", "TC-056"],
                    "failed_gates": [],
                },
                "required_artifacts": {
                    "features": {"present": True, "ok": True},
                    "test_cases": {"present": True, "ok": True},
                    "defects": {"present": True, "ok": True},
                    "coverage_summary": {"present": True, "ok": True},
                    "run_log": {"present": True, "ok": True},
                    "manual_proof_status": {"present": True, "ok": True},
                    "field_test_candidates": {"present": True, "ok": True},
                    "voiceover_checklist": {"present": False, "ok": False},
                },
                "completion_blockers": [
                    "required artifacts missing or empty: voiceover_checklist",
                    "manual proof gates blocked: TC-051, TC-056",
                ],
                "failures": ["required artifacts missing or empty: voiceover_checklist"],
            }
            path.write_text(json_dump(payload), encoding="utf-8")
            failures = []

            qa_artifact_integrity.validate_completion_audit_report(path, failures)

        self.assertTrue(any("required_artifacts not ok" in item for item in failures))

    def test_duplicate_ids_are_rejected(self):
        failures = []

        qa_artifact_integrity.require_unique(
            [{"Test Case ID": "TC-001"}, {"Test Case ID": "TC-001"}],
            "Test Case ID",
            "test cases",
            failures,
        )

        self.assertIn("test cases contains duplicate Test Case ID TC-001", failures)

    def test_summary_count_mismatch_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "coverage-summary.md"
            path.write_text("72 test cases; 70 are passing; 2 tests are blocked; 39 defects", encoding="utf-8")
            failures = []

            qa_artifact_integrity.validate_summary_counts(path, total=73, passed=70, blocked=2, defects=39, failures=failures)

        self.assertTrue(any("73 test cases" in item for item in failures))

    def test_summary_blocked_count_does_not_assume_manual_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "coverage-summary.md"
            path.write_text("73 test cases; 70 are passing; 3 tests are blocked; 39 defects", encoding="utf-8")
            failures = []

            qa_artifact_integrity.validate_summary_counts(
                path,
                total=73,
                passed=70,
                blocked=3,
                defects=39,
                failures=failures,
            )

        self.assertEqual([], failures)

    def test_feature_defect_count_mismatch_is_reported(self):
        features = [
            {"Feature ID": "F-001", "Defect Count": "0"},
            {"Feature ID": "F-002", "Defect Count": "1"},
            {"Feature ID": "F-003", "Defect Count": "0"},
        ]
        defects = [
            {"Defect ID": "D-001", "Feature ID": "F-001;F-002"},
            {"Defect ID": "D-002", "Feature ID": "F-001"},
        ]
        failures = []

        qa_artifact_integrity.validate_feature_defect_counts(features, defects, failures)

        self.assertIn("F-001 Defect Count 0 does not match defects.csv references 2", failures)
        self.assertFalse(any("F-002" in item for item in failures))

    @SKIP_WITHOUT_LOCAL_RECEIPTS
    def test_build_report_fails_when_jsonl_is_malformed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_root = pathlib.Path(tmp)
            (fake_root / "qa").mkdir()
            for rel in ["features.csv", "test-cases.csv", "defects.csv"]:
                (fake_root / "qa" / rel).write_text((ROOT / "qa" / rel).read_text(encoding="utf-8"), encoding="utf-8")
            for rel in [
                "coverage-summary.md",
                "run-log.md",
                "field-test-checklist.md",
                "voiceover-checklist.md",
                "field-test-candidates.md",
                "manual-proof-status.json",
                "manual-proof-evidence-template.json",
                "regression-suite-latest.json",
            ]:
                (fake_root / "qa" / rel).write_text((ROOT / "qa" / rel).read_text(encoding="utf-8"), encoding="utf-8")
            (fake_root / "qa" / "source-link-history.jsonl").write_text("{not-json\n", encoding="utf-8")
            (fake_root / "qa" / "ingestion-canary-history.jsonl").write_text("{}\n", encoding="utf-8")
            (fake_root / "qa" / "live-source-canary-history.jsonl").write_text("{}\n", encoding="utf-8")

            with mock.patch.object(qa_artifact_integrity, "ROOT", fake_root):
                report = qa_artifact_integrity.build_report()

        self.assertFalse(report["ok"])
        self.assertTrue(any("invalid JSONL row" in item for item in report["failures"]))


if __name__ == "__main__":
    unittest.main()
