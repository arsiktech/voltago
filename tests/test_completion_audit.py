import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
# Generated QA receipts stay local in the public repository (see tests/test_artifact_integrity.py).
SKIP_WITHOUT_LOCAL_RECEIPTS = unittest.skipUnless(
    (ROOT / "qa" / "regression-suite-latest.json").exists(),
    "QA receipts are generated locally and not tracked in the public repository",
)
SPEC = importlib.util.spec_from_file_location("qa_completion_audit", ROOT / "scripts" / "qa_completion_audit.py")
qa_completion_audit = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(qa_completion_audit)

ARTIFACT_SPEC = importlib.util.spec_from_file_location(
    "qa_artifact_integrity_contract",
    ROOT / "scripts" / "qa_artifact_integrity.py",
)
qa_artifact_integrity_contract = importlib.util.module_from_spec(ARTIFACT_SPEC)
assert ARTIFACT_SPEC.loader is not None
ARTIFACT_SPEC.loader.exec_module(qa_artifact_integrity_contract)


def json_dump(payload):
    return json.dumps(payload, ensure_ascii=False, indent=2)


def stale_self_reference_unit_output(
    marker,
    *,
    with_unrelated_failure=False,
    assertion_message=None,
    debug_marker=False,
):
    unrelated_status = ""
    unrelated_detail = ""
    failure_count = 1
    primary_assertion = assertion_message or f"False is not true : [{marker!r}]"
    debug_output = f"DEBUG: {marker}\n" if debug_marker else ""
    if with_unrelated_failure:
        unrelated_status = "test_route_honesty (test_honesty.RouteHonestyTests) ... FAIL\n"
        unrelated_detail = (
            "\n======================================================================"
            "\nFAIL: test_route_honesty (test_honesty.RouteHonestyTests)"
            "\n----------------------------------------------------------------------"
            "\nTraceback (most recent call last):"
            "\n  File \"tests/test_honesty.py\", line 1, in test_route_honesty"
            "\n    self.fail(\"stale station shown as live\")"
            "\nAssertionError: stale station shown as live\n"
        )
        failure_count = 2
    return (
        "test_current_artifacts_pass_integrity "
        "(test_artifact_integrity.ArtifactIntegrityTests) ... FAIL\n"
        f"{unrelated_status}"
        f"{debug_output}"
        "\n======================================================================"
        "\nFAIL: test_current_artifacts_pass_integrity "
        "(test_artifact_integrity.ArtifactIntegrityTests)"
        "\n----------------------------------------------------------------------"
        "\nTraceback (most recent call last):"
        "\n  File \"tests/test_artifact_integrity.py\", line 1, in test_current_artifacts_pass_integrity"
        "\n    self.assertTrue(report[\"ok\"], report[\"failures\"])"
        f"\nAssertionError: {primary_assertion}"
        f"{unrelated_detail}"
        "\n----------------------------------------------------------------------"
        "\nRan 354 tests in 1.000s"
        f"\n\nFAILED (failures={failure_count})\n"
    )


def green_regression_report(*, include_browser=True, include_external=True):
    check_ids = set(qa_artifact_integrity_contract.REQUIRED_REGRESSION_CHECK_IDS)
    if include_browser:
        check_ids |= qa_artifact_integrity_contract.BROWSER_REGRESSION_CHECK_IDS
    if include_external:
        check_ids |= qa_artifact_integrity_contract.EXTERNAL_REGRESSION_CHECK_IDS
    results = [{"id": check_id, "ok": True} for check_id in sorted(check_ids)]
    return {
        "ok": True,
        "include_browser": include_browser,
        "include_external": include_external,
        "total": len(results),
        "passed": len(results),
        "failed": 0,
        "failed_ids": [],
        "results": results,
    }


def stale_self_reference_regression_report(*, include_browser=True, include_external=True):
    report = green_regression_report(
        include_browser=include_browser,
        include_external=include_external,
    )
    marker = "qa/completion-audit-latest.json automated_evidence.regression_suite_ok must be true for current audit"
    report.update({
        "ok": False,
        "passed": report["total"] - 2,
        "failed": 2,
        "failed_ids": ["unit-tests", "qa-artifact-integrity"],
    })
    for result in report["results"]:
        if result["id"] == "unit-tests":
            result.update({
                "ok": False,
                "stderr": stale_self_reference_unit_output(marker),
            })
        elif result["id"] == "qa-artifact-integrity":
            result.update({"ok": False, "stdout": json_dump({"ok": False, "failures": [marker]})})
    return report


def write_completion_ready_workspace(root, regression_report):
    qa_dir = root / "qa"
    qa_dir.mkdir()
    (qa_dir / "test-cases.csv").write_text(
        "Test Case ID,Last Result\nTC-001,pass\n",
        encoding="utf-8",
    )
    (qa_dir / "defects.csv").write_text("Defect ID,Status\n", encoding="utf-8")
    (qa_dir / "regression-suite-latest.json").write_text(json_dump(regression_report), encoding="utf-8")
    (qa_dir / "artifact-integrity-latest.json").write_text(json_dump({"ok": True}), encoding="utf-8")
    (qa_dir / "field-packet-freshness-latest.json").write_text(json_dump({"ok": True}), encoding="utf-8")
    (qa_dir / "manual-proof-status.json").write_text(
        json_dump({
            "ok": True,
            "ready_to_complete_manual_gates": True,
            "blocked_gates": [],
            "failed_gates": [],
            "evidence_present": True,
        }),
        encoding="utf-8",
    )
    for name in [
        "features.csv",
        "coverage-summary.md",
        "run-log.md",
        "field-test-candidates.md",
        "voiceover-checklist.md",
    ]:
        (qa_dir / name).write_text("present\n", encoding="utf-8")


class CompletionAuditTests(unittest.TestCase):
    def test_green_full_regression_satisfies_completion_gate(self):
        report = green_regression_report()

        self.assertTrue(qa_completion_audit.regression_suite_full_green(report))

    def test_declared_green_with_a_failed_result_is_not_effectively_green(self):
        report = green_regression_report()
        report["results"][0]["ok"] = False

        self.assertFalse(qa_completion_audit.regression_suite_effectively_ok(report))
        self.assertFalse(qa_completion_audit.regression_suite_full_green(report))

    def test_declared_green_with_a_malformed_result_row_is_not_effectively_green(self):
        report = green_regression_report()
        report["results"][0] = {"id": "", "ok": True}

        self.assertFalse(qa_completion_audit.regression_suite_effectively_ok(report))
        self.assertFalse(qa_completion_audit.regression_suite_full_green(report))

    def test_declared_green_with_a_duplicate_result_id_is_not_effectively_green(self):
        report = green_regression_report()
        report["results"][1]["id"] = report["results"][0]["id"]

        self.assertFalse(qa_completion_audit.regression_suite_effectively_ok(report))
        self.assertFalse(qa_completion_audit.regression_suite_full_green(report))

    def test_declared_green_with_inconsistent_counts_is_not_effectively_green(self):
        report = green_regression_report()
        report["passed"] -= 1

        self.assertFalse(qa_completion_audit.regression_suite_effectively_ok(report))
        self.assertFalse(qa_completion_audit.regression_suite_full_green(report))

    def test_declared_green_with_failed_ids_is_not_effectively_green(self):
        report = green_regression_report()
        report["failed_ids"] = [report["results"][0]["id"]]

        self.assertFalse(qa_completion_audit.regression_suite_effectively_ok(report))
        self.assertFalse(qa_completion_audit.regression_suite_full_green(report))

    def test_green_partial_regression_blocks_completion_and_reports_evidence_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = pathlib.Path(tmp)
            write_completion_ready_workspace(
                tmp_root,
                green_regression_report(include_external=False),
            )
            with mock.patch.object(qa_completion_audit, "ROOT", tmp_root):
                report = qa_completion_audit.build_report()

        self.assertTrue(report["ok"], report["failures"])
        self.assertFalse(report["complete"])
        self.assertFalse(report["automated_evidence"]["regression_suite_ok"])
        self.assertTrue(any("full regression suite" in item for item in report["completion_blockers"]))

    def test_green_artifact_bootstrap_can_never_satisfy_completion_gate(self):
        check_ids = sorted(qa_artifact_integrity_contract.BOOTSTRAP_REGRESSION_CHECK_IDS)
        report = {
            "ok": True,
            "bootstrap_artifacts": True,
            "include_browser": False,
            "include_external": False,
            "total": len(check_ids),
            "passed": len(check_ids),
            "failed": 0,
            "failed_ids": [],
            "results": [{"id": check_id, "ok": True} for check_id in check_ids],
        }

        self.assertTrue(qa_completion_audit.regression_suite_effectively_ok(report))
        self.assertFalse(qa_completion_audit.regression_suite_full_green(report))

    def test_full_regression_stale_integrity_loop_satisfies_completion_gate(self):
        report = stale_self_reference_regression_report()

        self.assertTrue(qa_completion_audit.regression_suite_full_green(report))

    def test_stale_integrity_loop_with_an_unrelated_unit_failure_cannot_satisfy_completion_gate(self):
        report = stale_self_reference_regression_report()
        unit = next(result for result in report["results"] if result["id"] == "unit-tests")
        marker = (
            "qa/completion-audit-latest.json "
            "automated_evidence.regression_suite_ok must be true for current audit"
        )
        unit["stderr"] = stale_self_reference_unit_output(marker, with_unrelated_failure=True)

        self.assertFalse(qa_completion_audit.regression_suite_effectively_ok(report))
        self.assertFalse(qa_completion_audit.regression_suite_full_green(report))

    def test_stale_marker_in_debug_output_cannot_hide_an_unrelated_assertion(self):
        report = stale_self_reference_regression_report()
        unit = next(result for result in report["results"] if result["id"] == "unit-tests")
        marker = (
            "qa/completion-audit-latest.json "
            "automated_evidence.regression_suite_ok must be true for current audit"
        )
        unit["stderr"] = stale_self_reference_unit_output(
            marker,
            assertion_message="UNRELATED ledger corruption",
            debug_marker=True,
        )

        self.assertFalse(qa_completion_audit.regression_suite_effectively_ok(report))
        self.assertFalse(qa_completion_audit.regression_suite_full_green(report))

    def test_stale_integrity_loop_with_an_unrelated_artifact_failure_cannot_satisfy_completion_gate(self):
        report = stale_self_reference_regression_report()
        artifact = next(
            result for result in report["results"] if result["id"] == "qa-artifact-integrity"
        )
        marker = (
            "qa/completion-audit-latest.json "
            "automated_evidence.regression_suite_ok must be true for current audit"
        )
        artifact["stdout"] = json_dump({
            "ok": False,
            "failures": [
                marker,
                "qa/test-cases.csv contains duplicate Test Case ID TC-001",
            ],
        })

        self.assertFalse(qa_completion_audit.regression_suite_effectively_ok(report))
        self.assertFalse(qa_completion_audit.regression_suite_full_green(report))

    def test_stale_integrity_loop_with_a_hidden_third_failed_result_is_not_effectively_green(self):
        report = stale_self_reference_regression_report()
        hidden_failure = next(
            result
            for result in report["results"]
            if result["id"] not in {"unit-tests", "qa-artifact-integrity"}
        )
        hidden_failure["ok"] = False

        self.assertFalse(qa_completion_audit.regression_suite_effectively_ok(report))
        self.assertFalse(qa_completion_audit.regression_suite_full_green(report))

    def test_stale_integrity_loop_with_inconsistent_counts_is_not_effectively_green(self):
        report = stale_self_reference_regression_report()
        report["failed"] = 1

        self.assertFalse(qa_completion_audit.regression_suite_effectively_ok(report))
        self.assertFalse(qa_completion_audit.regression_suite_full_green(report))

    def test_stale_integrity_loop_with_inconsistent_failed_ids_is_not_effectively_green(self):
        report = stale_self_reference_regression_report()
        report["failed_ids"] = ["unit-tests"]

        self.assertFalse(qa_completion_audit.regression_suite_effectively_ok(report))
        self.assertFalse(qa_completion_audit.regression_suite_full_green(report))

    def test_partial_regression_stale_integrity_loop_cannot_satisfy_completion_gate(self):
        report = stale_self_reference_regression_report(include_external=False)

        self.assertFalse(qa_completion_audit.regression_suite_full_green(report))

    def test_green_regression_missing_mandatory_check_cannot_satisfy_completion_gate(self):
        report = green_regression_report()
        report["results"] = [
            result for result in report["results"] if result["id"] != "source-link-history"
        ]
        report["total"] -= 1
        report["passed"] -= 1

        self.assertFalse(qa_completion_audit.regression_suite_full_green(report))

    @SKIP_WITHOUT_LOCAL_RECEIPTS
    def test_current_goal_is_structurally_ok_but_not_complete(self):
        report = qa_completion_audit.build_report()

        self.assertTrue(report["ok"], report["failures"])
        self.assertFalse(report["complete"])
        self.assertIn("TC-051", report["manual_gates"]["blocked_gates"])
        self.assertIn("TC-056", report["manual_gates"]["blocked_gates"])
        self.assertTrue(any("manual proof gates blocked" in item for item in report["completion_blockers"]))

    @SKIP_WITHOUT_LOCAL_RECEIPTS
    def test_strict_mode_fails_when_manual_gates_are_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "completion.json"
            with mock.patch.object(qa_completion_audit, "DEFAULT_OUT", out):
                code = qa_completion_audit.main(["--out", str(out), "--strict"])

        self.assertEqual(code, 2)

    def test_artifact_integrity_self_reference_is_effectively_ok(self):
        report = {
            "ok": False,
            "failures": [
                "qa/completion-audit-latest.json automated_evidence.artifact_integrity_ok must be true for current audit"
            ],
        }

        self.assertTrue(qa_completion_audit.artifact_integrity_effectively_ok(report))

    def test_artifact_integrity_dual_self_reference_is_effectively_ok(self):
        report = {
            "ok": False,
            "failures": [
                "qa/completion-audit-latest.json automated_evidence.regression_suite_ok must be true for current audit",
                "qa/completion-audit-latest.json automated_evidence.artifact_integrity_ok must be true for current audit",
            ],
        }

        self.assertTrue(qa_completion_audit.artifact_integrity_effectively_ok(report))

    def test_artifact_integrity_real_failure_is_not_effectively_ok(self):
        report = {
            "ok": False,
            "failures": [
                "qa/regression-suite-latest.json missing required regression check ids: ['source-link-history']"
            ],
        }

        self.assertFalse(qa_completion_audit.artifact_integrity_effectively_ok(report))

    def test_regression_suite_stale_integrity_loop_is_effectively_ok(self):
        report = stale_self_reference_regression_report()

        self.assertTrue(qa_completion_audit.regression_suite_effectively_ok(report))

    def test_regression_suite_real_failure_is_not_effectively_ok(self):
        report = {
            "ok": False,
            "failed_ids": ["unit-tests", "focus-walk"],
            "results": [
                {"id": "focus-walk", "ok": False, "stdout": "focus order drifted"},
            ],
        }

        self.assertFalse(qa_completion_audit.regression_suite_effectively_ok(report))

    def test_missing_required_artifact_blocks_and_fails_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = pathlib.Path(tmp)
            qa_dir = tmp_root / "qa"
            qa_dir.mkdir()
            (qa_dir / "test-cases.csv").write_text(
                "Test Case ID,Last Result\nTC-051,blocked\nTC-056,blocked\n",
                encoding="utf-8",
            )
            (qa_dir / "defects.csv").write_text("Defect ID,Status\n", encoding="utf-8")
            for name in [
                "regression-suite-latest.json",
                "artifact-integrity-latest.json",
                "manual-proof-status.json",
                "field-packet-freshness-latest.json",
            ]:
                payload = {"ok": True}
                if name == "manual-proof-status.json":
                    payload = {
                        "ok": True,
                        "ready_to_complete_manual_gates": False,
                        "blocked_gates": ["TC-051", "TC-056"],
                        "failed_gates": [],
                        "evidence_present": False,
                    }
                (qa_dir / name).write_text(json_dump(payload), encoding="utf-8")
            for name in [
                "features.csv",
                "coverage-summary.md",
                "run-log.md",
                "manual-proof-status.json",
                "field-test-candidates.md",
            ]:
                path = qa_dir / name
                if not path.exists():
                    path.write_text("present\n", encoding="utf-8")
            # Deliberately omit voiceover-checklist.md.

            with mock.patch.object(qa_completion_audit, "ROOT", tmp_root):
                report = qa_completion_audit.build_report()

        self.assertFalse(report["ok"])
        self.assertFalse(report["complete"])
        self.assertIn("voiceover_checklist", " ".join(report["failures"]))
        self.assertTrue(any("required artifacts missing or empty" in item for item in report["completion_blockers"]))


if __name__ == "__main__":
    unittest.main()
