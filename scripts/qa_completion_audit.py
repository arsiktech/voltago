#!/usr/bin/env python3
"""Build a completion audit for the active Volta Go QA goal.

The audit can be healthy while still not complete. That distinction matters:
automated evidence can be green, but the full goal remains unfinished until the
physical Valencia/Tesla field test and native VoiceOver traversal have manual
evidence.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "qa" / "completion-audit-latest.json"
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from qa_artifact_integrity import (  # noqa: E402
    BROWSER_REGRESSION_CHECK_IDS,
    EXTERNAL_REGRESSION_CHECK_IDS,
    REQUIRED_REGRESSION_CHECK_IDS,
)

FULL_REGRESSION_CHECK_IDS = (
    REQUIRED_REGRESSION_CHECK_IDS
    | BROWSER_REGRESSION_CHECK_IDS
    | EXTERNAL_REGRESSION_CHECK_IDS
)

_STALE_REGRESSION_MARKER = (
    "qa/completion-audit-latest.json "
    "automated_evidence.regression_suite_ok must be true for current audit"
)
_STALE_ARTIFACT_MARKER = (
    "qa/completion-audit-latest.json "
    "automated_evidence.artifact_integrity_ok must be true for current audit"
)
_STALE_UNIT_TEST = "test_current_artifacts_pass_integrity"
_STALE_UNIT_ASSERTION = (
    f"AssertionError: False is not true : [{_STALE_REGRESSION_MARKER!r}]"
)
_UNITTEST_PROBLEM_HEADER = re.compile(
    r"^(FAIL|ERROR):\s+([^\s(]+)(?:\s+\([^\n]*\))?\s*$",
    re.MULTILINE,
)
_UNITTEST_FAILED_SUMMARY = re.compile(r"^FAILED\s+\(([^)]+)\)$")


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def read_json(path: Path, failures: List[str]) -> Dict[str, Any] | None:
    if not path.exists():
        failures.append(f"{rel(path)} is missing")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        failures.append(f"{rel(path)} invalid JSON: {exc}")
        return None
    if not isinstance(payload, dict):
        failures.append(f"{rel(path)} root must be an object")
        return None
    return payload


def read_csv_rows(path: Path, failures: List[str]) -> List[Dict[str, str]]:
    if not path.exists():
        failures.append(f"{rel(path)} is missing")
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        try:
            return [{key: (value or "").strip() for key, value in row.items()} for row in csv.DictReader(fh)]
        except csv.Error as exc:
            failures.append(f"{rel(path)} invalid CSV: {exc}")
            return []


def artifact_row(path: Path) -> Dict[str, Any]:
    return {"path": rel(path), "present": path.exists(), "ok": path.exists() and path.stat().st_size > 0}


def bool_field(payload: Dict[str, Any] | None, key: str) -> bool:
    return bool(isinstance(payload, dict) and payload.get(key) is True)


def artifact_integrity_effectively_ok(payload: Dict[str, Any] | None) -> bool:
    if bool_field(payload, "ok"):
        return True
    if not isinstance(payload, dict):
        return False
    failures = payload.get("failures")
    if not isinstance(failures, list) or not failures:
        return False
    allowed_markers = {
        _STALE_ARTIFACT_MARKER,
        _STALE_REGRESSION_MARKER,
    }
    return all(isinstance(failure, str) and failure in allowed_markers for failure in failures)


def _unittest_failure_counts(output: str) -> Dict[str, int] | None:
    """Parse unittest's final FAILED summary without trusting incidental text."""
    final_line = next((line.strip() for line in reversed(output.splitlines()) if line.strip()), "")
    match = _UNITTEST_FAILED_SUMMARY.fullmatch(final_line)
    if not match:
        return None
    counts: Dict[str, int] = {}
    for field in match.group(1).split(","):
        name, separator, raw_value = field.strip().partition("=")
        if not separator or not name or not raw_value.isdigit() or name in counts:
            return None
        counts[name] = int(raw_value)
    return counts


def _known_stale_unit_failure_only(output: str) -> bool:
    counts = _unittest_failure_counts(output)
    if counts is None or counts.get("failures") != 1 or counts.get("errors", 0) != 0:
        return False
    unexpected_nonzero = {
        name: count
        for name, count in counts.items()
        if count and name not in {"failures", "skipped", "expected failures"}
    }
    if unexpected_nonzero:
        return False
    problems = _UNITTEST_PROBLEM_HEADER.findall(output)
    assertion_lines = [
        line.strip()
        for line in output.splitlines()
        if line.strip().startswith("AssertionError:")
    ]
    return (
        problems == [("FAIL", _STALE_UNIT_TEST)]
        and assertion_lines == [_STALE_UNIT_ASSERTION]
    )


def _known_stale_artifact_failure_only(result: Dict[str, Any]) -> bool:
    if result.get("ok") is not False or str(result.get("stderr") or "").strip():
        return False
    try:
        report = json.loads(str(result.get("stdout") or ""))
    except (json.JSONDecodeError, TypeError):
        return False
    return (
        isinstance(report, dict)
        and report.get("ok") is False
        and report.get("failures") == [_STALE_REGRESSION_MARKER]
    )


def _coherent_regression_results(
    payload: Dict[str, Any] | None,
) -> Tuple[List[Dict[str, Any]], List[str]] | None:
    """Validate the report's own result rows and pass/fail accounting."""
    if not isinstance(payload, dict) or type(payload.get("ok")) is not bool:
        return None
    results = payload.get("results")
    failed_ids = payload.get("failed_ids")
    if not isinstance(results, list) or not results or not isinstance(failed_ids, list):
        return None

    result_ids: List[str] = []
    result_failed_ids: List[str] = []
    normalized_results: List[Dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("id"), str):
            return None
        check_id = result["id"]
        if not check_id.strip() or type(result.get("ok")) is not bool:
            return None
        result_ids.append(check_id)
        normalized_results.append(result)
        if result["ok"] is False:
            result_failed_ids.append(check_id)
    if len(result_ids) != len(set(result_ids)):
        return None

    if (
        not all(isinstance(check_id, str) and check_id.strip() for check_id in failed_ids)
        or len(failed_ids) != len(set(failed_ids))
        or sorted(failed_ids) != sorted(result_failed_ids)
    ):
        return None

    total = payload.get("total")
    passed = payload.get("passed")
    failed = payload.get("failed")
    if (
        type(total) is not int
        or type(passed) is not int
        or type(failed) is not int
        or total != len(results)
        or failed != len(result_failed_ids)
        or passed != total - failed
        or payload["ok"] is not (failed == 0)
    ):
        return None
    return normalized_results, result_failed_ids


def regression_suite_effectively_ok(payload: Dict[str, Any] | None) -> bool:
    coherent = _coherent_regression_results(payload)
    if coherent is None:
        return False
    results, failed_result_ids = coherent
    if payload.get("ok") is True:
        return not failed_result_ids

    failed_ids = set(failed_result_ids)
    if failed_ids != {"unit-tests", "qa-artifact-integrity"}:
        return False
    results_by_id = {
        item.get("id"): item
        for item in results
        if item.get("id") in failed_ids
    }
    unit = results_by_id.get("unit-tests", {})
    artifact = results_by_id.get("qa-artifact-integrity", {})
    unit_stderr = str(unit.get("stderr") or "")
    return (
        unit.get("ok") is False
        and _known_stale_unit_failure_only(unit_stderr)
        and _known_stale_artifact_failure_only(artifact)
    )


def regression_suite_full_green(payload: Dict[str, Any] | None) -> bool:
    """Whether a regression report is green and complete enough to close the goal."""
    if not regression_suite_effectively_ok(payload):
        return False
    if not bool_field(payload, "include_browser") or not bool_field(payload, "include_external"):
        return False
    result_ids = {
        item.get("id")
        for item in payload.get("results", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    return FULL_REGRESSION_CHECK_IDS <= result_ids


def test_case_summary(rows: List[Dict[str, str]]) -> Tuple[Dict[str, int], List[str]]:
    result_counts = Counter(row.get("Last Result", "") for row in rows)
    blocked = [
        row.get("Test Case ID", "")
        for row in rows
        if row.get("Last Result") == "blocked"
    ]
    return dict(result_counts), blocked


def defect_summary(rows: List[Dict[str, str]]) -> Tuple[Dict[str, int], List[str]]:
    status_counts = Counter(row.get("Status", "") for row in rows)
    openish = [
        row.get("Defect ID", "")
        for row in rows
        if row.get("Status") in {"Open", "In Progress", "Fixed", "Blocked"}
    ]
    return dict(status_counts), openish


def required_artifacts() -> Dict[str, Dict[str, Any]]:
    return {
        "features": artifact_row(ROOT / "qa" / "features.csv"),
        "test_cases": artifact_row(ROOT / "qa" / "test-cases.csv"),
        "defects": artifact_row(ROOT / "qa" / "defects.csv"),
        "coverage_summary": artifact_row(ROOT / "qa" / "coverage-summary.md"),
        "run_log": artifact_row(ROOT / "qa" / "run-log.md"),
        "manual_proof_status": artifact_row(ROOT / "qa" / "manual-proof-status.json"),
        "field_test_candidates": artifact_row(ROOT / "qa" / "field-test-candidates.md"),
        "voiceover_checklist": artifact_row(ROOT / "qa" / "voiceover-checklist.md"),
    }


def build_report() -> Dict[str, Any]:
    failures: List[str] = []
    tests = read_csv_rows(ROOT / "qa" / "test-cases.csv", failures)
    defects = read_csv_rows(ROOT / "qa" / "defects.csv", failures)
    regression = read_json(ROOT / "qa" / "regression-suite-latest.json", failures)
    artifact_integrity = read_json(ROOT / "qa" / "artifact-integrity-latest.json", failures)
    manual = read_json(ROOT / "qa" / "manual-proof-status.json", failures)
    field_freshness = read_json(ROOT / "qa" / "field-packet-freshness-latest.json", failures)

    result_counts, blocked_tests = test_case_summary(tests)
    defect_counts, open_defects = defect_summary(defects)
    manual_blocked = list(manual.get("blocked_gates", [])) if isinstance(manual, dict) else []
    manual_failed = list(manual.get("failed_gates", [])) if isinstance(manual, dict) else []
    artifacts = required_artifacts()
    artifact_failures = [name for name, row in artifacts.items() if not row.get("ok")]
    regression_effectively_green = regression_suite_effectively_ok(regression)
    regression_full_green = regression_suite_full_green(regression)

    completion_blockers: List[str] = []
    if artifact_failures:
        completion_blockers.append(f"required artifacts missing or empty: {', '.join(artifact_failures)}")
        failures.append(f"required artifacts missing or empty: {', '.join(artifact_failures)}")
    if not regression_effectively_green:
        completion_blockers.append("regression-suite-latest is not green")
    elif not regression_full_green:
        completion_blockers.append("full regression suite is not green (browser and external checks are required)")
    if not bool_field(artifact_integrity, "ok"):
        completion_blockers.append("artifact-integrity-latest is not green")
    if not bool_field(field_freshness, "ok"):
        completion_blockers.append("field-packet-freshness-latest is not green")
    if open_defects:
        completion_blockers.append(f"open defect statuses remain: {', '.join(open_defects)}")
    if manual_failed:
        completion_blockers.append(f"manual proof gates failed and need defects: {', '.join(manual_failed)}")
    if manual_blocked:
        completion_blockers.append(f"manual proof gates blocked: {', '.join(manual_blocked)}")
    if blocked_tests:
        completion_blockers.append(f"blocked test cases remain: {', '.join(blocked_tests)}")
    if not bool_field(manual, "ready_to_complete_manual_gates"):
        completion_blockers.append("manual proof status is not ready_to_complete_manual_gates")

    expected_manual_gates = {"TC-051", "TC-056"}
    if set(manual_blocked + manual_failed) - expected_manual_gates:
        failures.append("manual proof report contains an unexpected blocked/failed gate")

    structurally_ok = not failures
    complete = structurally_ok and not completion_blockers
    return {
        "ok": structurally_ok,
        "complete": complete,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "qa_counts": {
            "test_cases": len(tests),
            "test_results": result_counts,
            "defects": len(defects),
            "defect_statuses": defect_counts,
        },
        "automated_evidence": {
            "regression_suite_ok": regression_full_green,
            "regression_suite_effectively_green": regression_effectively_green,
            "regression_suite_full_green": regression_full_green,
            "regression_include_browser": bool_field(regression, "include_browser"),
            "regression_include_external": bool_field(regression, "include_external"),
            "regression_total": regression.get("total") if isinstance(regression, dict) else None,
            "regression_failed_ids": regression.get("failed_ids") if isinstance(regression, dict) else None,
            "artifact_integrity_ok": artifact_integrity_effectively_ok(artifact_integrity),
            "field_packet_freshness_ok": bool_field(field_freshness, "ok"),
        },
        "manual_gates": {
            "ready_to_complete_manual_gates": bool_field(manual, "ready_to_complete_manual_gates"),
            "blocked_gates": manual_blocked,
            "failed_gates": manual_failed,
            "evidence_present": bool(manual.get("evidence_present")) if isinstance(manual, dict) else False,
        },
        "required_artifacts": artifacts,
        "completion_blockers": completion_blockers,
        "failures": failures,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--strict", action="store_true", help="Return non-zero unless the full goal is complete.")
    args = parser.parse_args(argv)

    report = build_report()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": report["ok"],
        "complete": report["complete"],
        "completion_blockers": report["completion_blockers"],
        "failures": report["failures"],
        "out": str(out),
    }, ensure_ascii=False, indent=2))
    if not report["ok"]:
        return 1
    if args.strict and not report["complete"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
