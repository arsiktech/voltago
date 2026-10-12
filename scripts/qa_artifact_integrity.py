#!/usr/bin/env python3
"""Validate Volta Go durable QA artifacts.

This checks the QA deliverables required by the active goal: CSV shape,
unique IDs, required fields/enums, cross references, current summary counts,
and JSON/JSONL evidence files.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from qa_revision import current_regression_revision  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "qa" / "artifact-integrity-latest.json"

FEATURE_FIELDS = [
    "Feature ID",
    "Feature Name",
    "Feature Type",
    "User Story",
    "Entry Points",
    "Source Evidence",
    "Observed Behaviour",
    "Inferred Intended Behaviour",
    "Validation Rules",
    "Edge Cases",
    "Dependencies",
    "Risk Level",
    "Priority",
    "Coverage Status",
    "Defect Count",
    "Notes",
    "Last Updated",
]
TEST_FIELDS = [
    "Test Case ID",
    "Feature ID",
    "Test Name",
    "Test Type",
    "Priority",
    "Preconditions",
    "Steps",
    "Expected Result",
    "Automation Status",
    "Evidence Link / Test File",
    "Last Result",
    "Last Run Date",
    "Notes",
]
DEFECT_FIELDS = [
    "Defect ID",
    "Feature ID",
    "Test Case ID",
    "Title",
    "Severity",
    "Status",
    "Reproduction Steps",
    "Expected Result",
    "Actual Result",
    "Evidence",
    "Root Cause Hypothesis",
    "Fix Plan",
    "Fix Commit / Files Changed",
    "Verification Evidence",
    "Notes",
]

FEATURE_TYPES = {"screen", "workflow", "API", "integration", "config", "background_job", "CLI", "data_model"}
TEST_TYPES = {
    "unit",
    "integration",
    "e2e",
    "manual",
    "accessibility",
    "performance",
    "security",
}
PRIORITIES = {"P0", "P1", "P2", "P3"}
RISKS = {"Critical", "High", "Medium", "Low"}
AUTOMATION_STATUSES = {"existing", "needs_automation", "manual_only", "blocked"}
LAST_RESULTS = {"not_run", "pass", "fail", "blocked"}
SEVERITIES = {"Critical", "High", "Medium", "Low"}
DEFECT_STATUSES = {"Open", "In Progress", "Fixed", "Verified", "Waived", "Blocked"}
# Historical one-off QA servers. The active ledger has one canonical app target (:8773),
# so bare phrases such as "against 8772" are just as stale as a full localhost URL.
STALE_LOCAL_APP_RE = re.compile(r"\b(?:8767|8768|8769|8770|8771|8772|8785)\b")
RETIRED_TEST_IDS = {"TC-016", "TC-036"}
CURRENT_FEATURE_UI_FIELDS = [
    "Feature Name",
    "User Story",
    "Entry Points",
    "Source Evidence",
    "Observed Behaviour",
    "Inferred Intended Behaviour",
    "Validation Rules",
    "Edge Cases",
    "Dependencies",
]
CURRENT_TEST_UI_FIELDS = [
    "Test Name",
    "Preconditions",
    "Steps",
    "Expected Result",
    "Evidence Link / Test File",
]
RETIRED_UI_SOURCE_RE = re.compile(
    r"\b(?:static/|scripts/qa_accessibility_static\.py)\b",
    re.IGNORECASE,
)
RETIRED_UI_TERM_RE = re.compile(
    r"(?:"
    r"\bLeaflet\b|"
    r"\bPlain HTML app shell\b|"
    r"\bstatic home\b|"
    r"\bstatic mobile shell\b|"
    r"\bstatic HTML\b|"
    r"\bstatic JS checks?\b|"
    r"\bstatic CSS/JS\b|"
    r"\bstatic contrast(?:/focus-order)?\b|"
    r"\baccessibility static\b|"
    r"\b(?:node|source|PWA)/static\b|"
    r"https?://unpkg\.com\b"
    r")",
    re.IGNORECASE,
)
STALE_REGRESSION_SUITE_RE = re.compile(
    r"\b(?:latest|current|final|finally|expanded|repeatable|bounded|regression-suite-latest\.json)"
    r"[^;\n]{0,220}"
    r"(?:\b19/19\b|\b27/27\b|\btotal=19\b|\btotal=27\b|\bpassed=19\b|\bpassed=27\b)",
    re.IGNORECASE,
)
RAW_FIELD_PACKET_TECHNICAL_RE = re.compile(
    r"\b(?:HTTPError|HTTP Error|Traceback|TypeError|Exception|API \d{3}|Too Many Requests|Bad Gateway)\b",
    re.IGNORECASE,
)
FIELD_PACKET_FRESHNESS_MAX_AGE_SECONDS = 24 * 60 * 60
# The 24-hour freshness limit is a release gate: a field packet older than a day must not
# ship. Routine CI runs (pull requests, pushes) report the age as a warning instead, so an
# unchanged candidate does not turn red merely because a day passed. Every content binding
# (packet hash, canonical recordset, base URL, ok/failures) still fails in both modes.
RELEASE_ENV = "VOLTA_QA_RELEASE"
# Always-required Python/API checks — these must be in every regression report.
REQUIRED_REGRESSION_CHECK_IDS = {
    "python-compile",
    "js-compile",
    "unit-tests",
    "spa-accessibility",
    "api-smoke",
    "pwa-shell",
    "source-strategy",
    "manual-proof-status",
    "field-packet-freshness",
    "qa-artifact-integrity",
    "completion-audit",
}
# Browser DOM contracts. The SPA port landed 2026-07-03 (plans/009 retired the
# static/ shell entirely, 2026-07-12) -- these may still be skipped, but ONLY
# when the report explicitly records include_browser=false (an honest,
# visible skip) — never silently.
BROWSER_REGRESSION_CHECK_IDS = {
    "first-render",
    "preview-to-live",
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
}
# Live-upstream checks; may be skipped only when include_external=false is recorded.
EXTERNAL_REGRESSION_CHECK_IDS = {
    "live-source-canary",
    "ingestion-canary",
    "source-link-sample",
    "source-link-history",
}
BOOTSTRAP_REGRESSION_CHECK_IDS = {
    "python-compile",
    "js-compile",
    "spa-accessibility",
    "pwa-shell",
    "source-strategy",
}


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def read_csv(path: Path, expected_fields: Sequence[str], failures: List[str]) -> List[Dict[str, str]]:
    if not path.exists():
        failures.append(f"{rel(path)} is missing")
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != list(expected_fields):
            failures.append(f"{rel(path)} header mismatch: {reader.fieldnames}")
            return []
        rows: List[Dict[str, str]] = []
        for idx, row in enumerate(reader, start=2):
            if None in row:
                failures.append(f"{rel(path)}:{idx} has extra columns: {row[None]}")
                continue
            missing_cols = [field for field in expected_fields if field not in row]
            if missing_cols:
                failures.append(f"{rel(path)}:{idx} missing columns: {missing_cols}")
                continue
            rows.append({field: (row.get(field) or "").strip() for field in expected_fields})
    return rows


def require_unique(rows: Sequence[Dict[str, str]], id_field: str, label: str, failures: List[str]) -> None:
    ids = [row[id_field] for row in rows]
    for value, count in sorted(Counter(ids).items()):
        if not value:
            failures.append(f"{label} contains blank {id_field}")
        if value and count > 1:
            failures.append(f"{label} contains duplicate {id_field} {value}")


def require_nonempty(rows: Sequence[Dict[str, str]], fields: Iterable[str], label: str, failures: List[str]) -> None:
    for row in rows:
        ident = row.get(next(iter(row)), "?")
        for field in fields:
            if not row.get(field):
                failures.append(f"{label} {ident} missing required field {field}")


def validate_id_pattern(rows: Sequence[Dict[str, str]], field: str, pattern: str, label: str, failures: List[str]) -> None:
    regex = re.compile(pattern)
    for row in rows:
        value = row.get(field, "")
        if value and not regex.fullmatch(value):
            failures.append(f"{label} has invalid {field}: {value}")


def feature_refs(value: str) -> List[str]:
    return [part.strip() for part in value.split(";") if part.strip()]


def validate_current_ui_claims(
    ident: str,
    row: Dict[str, str],
    fields: Sequence[str],
    failures: List[str],
) -> None:
    """Reject retired-shell sources/terms in current-truth fields, never history notes."""
    for field in fields:
        value = row[field]
        match = RETIRED_UI_SOURCE_RE.search(value) or RETIRED_UI_TERM_RE.search(value)
        if match:
            failures.append(
                f"{ident} {field} references retired UI source/claim {match.group(0)!r}"
            )


def validate_features(rows: Sequence[Dict[str, str]], failures: List[str]) -> None:
    require_unique(rows, "Feature ID", "features", failures)
    validate_id_pattern(rows, "Feature ID", r"F-\d{3}", "features", failures)
    require_nonempty(rows, FEATURE_FIELDS, "feature", failures)
    for row in rows:
        if row["Feature Type"] not in FEATURE_TYPES:
            failures.append(f"{row['Feature ID']} invalid Feature Type {row['Feature Type']!r}")
        if row["Risk Level"] not in RISKS:
            failures.append(f"{row['Feature ID']} invalid Risk Level {row['Risk Level']!r}")
        if row["Priority"] not in PRIORITIES:
            failures.append(f"{row['Feature ID']} invalid Priority {row['Priority']!r}")
        try:
            int(row["Defect Count"])
        except ValueError:
            failures.append(f"{row['Feature ID']} Defect Count is not an integer")
        validate_current_ui_claims(
            row["Feature ID"],
            row,
            CURRENT_FEATURE_UI_FIELDS,
            failures,
        )


def validate_feature_defect_counts(
    features: Sequence[Dict[str, str]],
    defects: Sequence[Dict[str, str]],
    failures: List[str],
) -> None:
    defect_refs = Counter()
    for defect in defects:
        for fid in feature_refs(defect["Feature ID"]):
            defect_refs[fid] += 1

    for feature in features:
        fid = feature["Feature ID"]
        try:
            declared = int(feature["Defect Count"])
        except ValueError:
            continue
        actual = defect_refs.get(fid, 0)
        if declared != actual:
            failures.append(f"{fid} Defect Count {declared} does not match defects.csv references {actual}")


def validate_tests(rows: Sequence[Dict[str, str]], feature_ids: set[str], failures: List[str]) -> None:
    require_unique(rows, "Test Case ID", "test cases", failures)
    validate_id_pattern(rows, "Test Case ID", r"TC-\d{3}", "test cases", failures)
    require_nonempty(rows, TEST_FIELDS, "test case", failures)
    for row in rows:
        tid = row["Test Case ID"]
        for fid in feature_refs(row["Feature ID"]):
            if fid not in feature_ids:
                failures.append(f"{tid} references unknown feature {fid}")
        if row["Test Type"] not in TEST_TYPES:
            failures.append(f"{tid} invalid Test Type {row['Test Type']!r}")
        if row["Priority"] not in PRIORITIES:
            failures.append(f"{tid} invalid Priority {row['Priority']!r}")
        if row["Automation Status"] not in AUTOMATION_STATUSES:
            failures.append(f"{tid} invalid Automation Status {row['Automation Status']!r}")
        if row["Last Result"] not in LAST_RESULTS:
            failures.append(f"{tid} invalid Last Result {row['Last Result']!r}")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["Last Run Date"]):
            failures.append(f"{tid} invalid Last Run Date {row['Last Run Date']!r}")
        for field in ["Preconditions", "Steps"]:
            match = STALE_LOCAL_APP_RE.search(row[field])
            if match:
                failures.append(f"{tid} {field} references stale local app port {match.group(0)}")
        if tid in RETIRED_TEST_IDS:
            notes = row["Notes"]
            has_supersession = re.search(
                r"\bsuperseded by TC-114\b",
                notes,
                re.IGNORECASE,
            )
            if "RETIRED" not in notes or not has_supersession:
                failures.append(
                    f"{tid} is an allowed retired-test exception but lacks the required "
                    "RETIRED and superseded by TC-114 retired-test marker"
                )
        else:
            validate_current_ui_claims(tid, row, CURRENT_TEST_UI_FIELDS, failures)
        for field in ["Preconditions", "Steps", "Expected Result", "Evidence Link / Test File", "Notes"]:
            match = STALE_REGRESSION_SUITE_RE.search(row[field])
            if match:
                failures.append(f"{tid} {field} contains stale current regression-suite evidence {match.group(0)!r}")


def validate_defects(rows: Sequence[Dict[str, str]], feature_ids: set[str], test_ids: set[str], failures: List[str]) -> None:
    require_unique(rows, "Defect ID", "defects", failures)
    validate_id_pattern(rows, "Defect ID", r"D-\d{3}", "defects", failures)
    require_nonempty(rows, DEFECT_FIELDS, "defect", failures)
    for row in rows:
        did = row["Defect ID"]
        for fid in feature_refs(row["Feature ID"]):
            if fid not in feature_ids:
                failures.append(f"{did} references unknown feature {fid}")
        for tid in feature_refs(row["Test Case ID"]):
            if tid not in test_ids:
                failures.append(f"{did} references unknown test case {tid}")
        if row["Severity"] not in SEVERITIES:
            failures.append(f"{did} invalid Severity {row['Severity']!r}")
        if row["Status"] not in DEFECT_STATUSES:
            failures.append(f"{did} invalid Status {row['Status']!r}")
        for field in ["Verification Evidence", "Notes"]:
            match = STALE_REGRESSION_SUITE_RE.search(row[field])
            if match:
                failures.append(f"{did} {field} contains stale current regression-suite evidence {match.group(0)!r}")


def validate_json(path: Path, failures: List[str]) -> bool:
    if not path.exists():
        failures.append(f"{rel(path)} is missing")
        return False
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        failures.append(f"{rel(path)} invalid JSON: {exc}")
        return False
    return True


def validate_data_snapshot_report(
    path: Path,
    data_path: Path,
    failures: List[str],
    *,
    kind: str,
) -> None:
    """Bind a current-named derived report to the exact canonical DB bytes."""
    if not validate_json(path, failures):
        return
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        database = json.loads(data_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(report, dict) or not isinstance(database, dict):
        failures.append(f"{rel(path)} and canonical database roots must be objects")
        return
    records = database.get("records")
    if not isinstance(records, list):
        failures.append(f"{rel(data_path)} records must be a list")
        return

    expected_hash = hashlib.sha256(data_path.read_bytes()).hexdigest()
    if report.get("data") != "data/free_intelligence.json":
        failures.append(f"{rel(path)} data must reference data/free_intelligence.json")
    if report.get("data_sha256") != expected_hash:
        failures.append(f"{rel(path)} data_sha256 does not match the current canonical database")
    if report.get("ok") is not True:
        failures.append(f"{rel(path)} ok must be true")

    if kind == "free-db":
        free_records = [row for row in records if isinstance(row, dict) and row.get("is_free")]
        access_limited = [
            row for row in records
            if isinstance(row, dict) and row.get("price_claim_type") == "access_limited_free"
        ]
        expected = {
            "records": len(records),
            "free_records": len(free_records),
            "access_limited_free_records": len(access_limited),
        }
        for field, value in expected.items():
            if report.get(field) != value:
                failures.append(f"{rel(path)} {field} does not match the current canonical database")
        if report.get("failures") != []:
            failures.append(f"{rel(path)} failures must be an empty list")
    elif kind == "valencia":
        bbox = report.get("bbox")
        if not isinstance(bbox, dict) or not all(
            isinstance(bbox.get(key), (int, float)) for key in ("south", "west", "north", "east")
        ):
            failures.append(f"{rel(path)} bbox must contain numeric south/west/north/east")
            return
        valencia_records = sum(
            1
            for row in records
            if isinstance(row, dict)
            and isinstance(row.get("lat"), (int, float))
            and isinstance(row.get("lon"), (int, float))
            and bbox["south"] <= row["lat"] <= bbox["north"]
            and bbox["west"] <= row["lon"] <= bbox["east"]
        )
        if report.get("total_records") != len(records):
            failures.append(f"{rel(path)} total_records does not match the current canonical database")
        if report.get("valencia_records") != valencia_records:
            failures.append(f"{rel(path)} valencia_records does not match the current canonical database")
    else:
        failures.append(f"unknown data snapshot report kind: {kind}")


def stable_recordset_sha256(records: Iterable[Dict[str, Any]]) -> str:
    ordered = sorted(
        (record for record in records if isinstance(record, dict)),
        key=lambda record: str(record.get("id") or ""),
    )
    encoded = json.dumps(
        ordered,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_field_packet_freshness_report(
    path: Path,
    failures: List[str],
    *,
    canonical_path: Path | None = None,
    packet_path: Path | None = None,
    max_age_seconds: float = FIELD_PACKET_FRESHNESS_MAX_AGE_SECONDS,
    enforce_age: bool = True,
    warnings: List[str] | None = None,
) -> None:
    if not validate_json(path, failures):
        return
    text = path.read_text(encoding="utf-8")
    match = RAW_FIELD_PACKET_TECHNICAL_RE.search(text)
    if match:
        failures.append(f"{rel(path)} contains raw technical warning token {match.group(0)!r}")
    if canonical_path is None or packet_path is None:
        return
    try:
        report = json.loads(text)
        canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
        packet_text = packet_path.read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(report, dict) or not isinstance(canonical, dict):
        failures.append(f"{rel(path)} and canonical database roots must be objects")
        return

    required = {
        "ok",
        "checked_at",
        "packet",
        "packet_sha256",
        "base_url",
        "canonical_data",
        "canonical_recordset_sha256",
        "packet_state",
        "current_state",
        "current_state_error",
        "warning_drifts",
        "failures",
    }
    missing = sorted(required - set(report))
    if missing:
        failures.append(f"{rel(path)} missing fields: {missing}")
        return

    try:
        checked_at = datetime.fromisoformat(str(report["checked_at"]).replace("Z", "+00:00"))
        if checked_at.tzinfo is None or checked_at.tzinfo.utcoffset(checked_at) is None:
            failures.append(f"{rel(path)} checked_at must include timezone offset")
        else:
            age = (datetime.now(timezone.utc) - checked_at.astimezone(timezone.utc)).total_seconds()
            if age < -300:
                failures.append(f"{rel(path)} checked_at is implausibly in the future")
            elif age > max_age_seconds:
                message = f"{rel(path)} is stale ({round(age)} seconds old)"
                if enforce_age:
                    failures.append(message)
                elif warnings is not None:
                    warnings.append(f"{message}; release runs (--release) fail on this")
    except ValueError:
        failures.append(f"{rel(path)} checked_at is not ISO-8601 datetime")

    records = canonical.get("records")
    if not isinstance(records, list):
        failures.append(f"{rel(canonical_path)} records must be a list")
        return
    canonical_sha = stable_recordset_sha256(records)
    if canonical.get("recordset_sha256") not in (None, canonical_sha):
        failures.append(f"{rel(canonical_path)} stored recordset_sha256 does not match its records")
    if report.get("canonical_recordset_sha256") != canonical_sha:
        failures.append(f"{rel(path)} canonical_recordset_sha256 does not match the current canonical database")

    reported_packet = Path(str(report.get("packet") or ""))
    if not reported_packet.is_absolute():
        reported_packet = ROOT / reported_packet
    if reported_packet.resolve() != packet_path.resolve():
        failures.append(f"{rel(path)} packet path does not reference the durable field packet")
    reported_canonical = Path(str(report.get("canonical_data") or ""))
    if not reported_canonical.is_absolute():
        reported_canonical = ROOT / reported_canonical
    if reported_canonical.resolve() != canonical_path.resolve():
        failures.append(f"{rel(path)} canonical_data does not reference the current canonical database")
    packet_sha = hashlib.sha256(packet_text.encode("utf-8")).hexdigest()
    if report.get("packet_sha256") != packet_sha:
        failures.append(f"{rel(path)} packet_sha256 does not match the durable field packet")

    packet_fields: Dict[str, str] = {}
    for key, pattern in {
        "base_url": r"(?m)^Base URL:\s*`([^`]+)`\s*$",
        "expected_recordset_sha256": r"(?m)^Expected local recordset SHA-256:\s*`([^`]+)`\s*$",
        "deployed_recordset_sha256": r"(?m)^Deployed recordset SHA-256:\s*`([^`]+)`\s*$",
    }.items():
        field_match = re.search(pattern, packet_text)
        if not field_match:
            failures.append(f"{rel(packet_path)} missing {key}")
        else:
            packet_fields[key] = field_match.group(1).strip()
    packet_claims_match = bool(re.search(r"(?m)^Candidate revision match:\s*\*\*yes\*\*\s*$", packet_text))
    packet_state = report.get("packet_state")
    if not isinstance(packet_state, dict):
        failures.append(f"{rel(path)} packet_state must be an object")
        return
    for key, value in packet_fields.items():
        if packet_state.get(key) != value:
            failures.append(f"{rel(path)} packet_state.{key} does not match the durable field packet")
    if packet_state.get("candidate_revision_match") is not packet_claims_match:
        failures.append(f"{rel(path)} packet_state.candidate_revision_match does not match the durable field packet")
    if report.get("base_url", "").rstrip("/") != packet_fields.get("base_url", "").rstrip("/"):
        failures.append(f"{rel(path)} base_url does not match the durable field packet")

    packet_matches_canonical = bool(
        packet_claims_match
        and packet_fields.get("expected_recordset_sha256") == canonical_sha
        and packet_fields.get("deployed_recordset_sha256") == canonical_sha
    )
    report_failures = report.get("failures")
    current_state = report.get("current_state")
    current_state_error = report.get("current_state_error")
    if not isinstance(current_state, dict):
        failures.append(f"{rel(path)} current_state must be an object")
        current_deployed_sha = None
    else:
        current_deployed_sha = current_state.get("deployed_recordset_sha256")
        if not isinstance(current_deployed_sha, str) or not current_deployed_sha:
            fail_closed_missing_sha = bool(
                report.get("ok") is False
                and isinstance(current_state_error, str)
                and current_state_error
                and current_state.get("error") == current_state_error
                and isinstance(report_failures, list)
                and current_state_error in report_failures
            )
            if not fail_closed_missing_sha:
                failures.append(f"{rel(path)} current_state.deployed_recordset_sha256 is missing")
        elif current_state_error not in (None, ""):
            failures.append(f"{rel(path)} current_state_error must be empty when a deployed recordset is present")
    if not isinstance(report_failures, list):
        failures.append(f"{rel(path)} failures must be a list")
    elif bool(report.get("ok")) == bool(report_failures):
        failures.append(f"{rel(path)} ok does not match failures")
    if report.get("ok") is True and not packet_matches_canonical:
        failures.append(f"{rel(path)} claims ok for a packet not bound to the current canonical revision")
    if report.get("ok") is True and current_deployed_sha != canonical_sha:
        failures.append(f"{rel(path)} claims ok while current deployed recordset differs from canonical")
    if (
        report.get("ok") is True
        and current_deployed_sha != packet_fields.get("deployed_recordset_sha256")
    ):
        failures.append(f"{rel(path)} claims ok while packet deployed SHA differs from current /api/intel")


def validate_manual_proof_status_report(path: Path, failures: List[str]) -> None:
    if not validate_json(path, failures):
        return
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    if not isinstance(report, dict):
        failures.append(f"{rel(path)} root must be an object")
        return

    required = {
        "ok",
        "ready_to_complete_manual_gates",
        "checked_at",
        "evidence_path",
        "evidence_present",
        "evidence_error",
        "artifacts",
        "gates",
        "blocked_gates",
        "failed_gates",
        "artifact_failures",
    }
    missing = sorted(required - set(report))
    if missing:
        failures.append(f"{rel(path)} missing fields: {missing}")
        return

    try:
        checked_at = datetime.fromisoformat(str(report["checked_at"]).replace("Z", "+00:00"))
        if checked_at.tzinfo is None or checked_at.tzinfo.utcoffset(checked_at) is None:
            failures.append(f"{rel(path)} checked_at must include timezone offset")
    except ValueError:
        failures.append(f"{rel(path)} checked_at is not ISO-8601 datetime")

    gates = report.get("gates")
    if not isinstance(gates, list):
        failures.append(f"{rel(path)} gates must be a list")
        return
    gate_by_id = {
        gate.get("gate"): gate
        for gate in gates
        if isinstance(gate, dict) and isinstance(gate.get("gate"), str)
    }
    if set(gate_by_id) != {"TC-051", "TC-056"}:
        failures.append(f"{rel(path)} gates must contain exactly TC-051 and TC-056")
        return

    valid_statuses = {"blocked", "fail_needs_defect", "pass"}
    for gate_id, gate in gate_by_id.items():
        status = gate.get("status")
        if status not in valid_statuses:
            failures.append(f"{rel(path)} {gate_id} invalid manual gate status {status!r}")

    blocked_expected = sorted(gid for gid, gate in gate_by_id.items() if gate.get("status") == "blocked")
    failed_expected = sorted(gid for gid, gate in gate_by_id.items() if gate.get("status") == "fail_needs_defect")
    if sorted(report.get("blocked_gates") or []) != blocked_expected:
        failures.append(f"{rel(path)} blocked_gates does not match gate statuses")
    if sorted(report.get("failed_gates") or []) != failed_expected:
        failures.append(f"{rel(path)} failed_gates does not match gate statuses")

    artifacts = report.get("artifacts")
    if not isinstance(artifacts, dict):
        failures.append(f"{rel(path)} artifacts must be an object")
        artifact_failures_expected: List[str] = []
    else:
        artifact_failures_expected = sorted(
            name for name, row in artifacts.items() if not (isinstance(row, dict) and row.get("ok") is True)
        )
    if sorted(report.get("artifact_failures") or []) != artifact_failures_expected:
        failures.append(f"{rel(path)} artifact_failures does not match artifact statuses")

    ready_expected = (
        not artifact_failures_expected
        and not report.get("evidence_error")
        and all(gate.get("status") == "pass" for gate in gate_by_id.values())
    )
    if report.get("ready_to_complete_manual_gates") is not ready_expected:
        failures.append(f"{rel(path)} ready_to_complete_manual_gates does not match gate/artifact state")
    if report.get("ok") is not (not artifact_failures_expected and not report.get("evidence_error")):
        failures.append(f"{rel(path)} ok does not match artifact/evidence error state")

    if report.get("evidence_present") is False:
        if report.get("ready_to_complete_manual_gates") is not False:
            failures.append(f"{rel(path)} cannot be ready when evidence_present=false")
        if sorted(report.get("blocked_gates") or []) != ["TC-051", "TC-056"]:
            failures.append(f"{rel(path)} missing evidence must block TC-051 and TC-056")


def validate_regression_suite_report(path: Path, failures: List[str]) -> None:
    if not validate_json(path, failures):
        return
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    if not isinstance(report, dict):
        failures.append(f"{rel(path)} root must be an object")
        return

    required = {
        "ok",
        "checked_at",
        "base_url",
        "input_sha256",
        "canonical_recordset_sha256",
        "qa_counts",
        "total",
        "passed",
        "failed",
        "failed_ids",
        "results",
    }
    missing = sorted(required - set(report))
    if missing:
        failures.append(f"{rel(path)} missing fields: {missing}")
        return

    if report.get("base_url") != "http://127.0.0.1:8773":
        failures.append(f"{rel(path)} base_url is not current 8773 app URL")
    try:
        current_revision = current_regression_revision()
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        failures.append(f"cannot compute current regression revision: {type(exc).__name__}")
    else:
        if report.get("input_sha256") != current_revision["input_sha256"]:
            failures.append(f"{rel(path)} input_sha256 does not match current regression inputs")
        if report.get("canonical_recordset_sha256") != current_revision["canonical_recordset_sha256"]:
            failures.append(f"{rel(path)} canonical_recordset_sha256 does not match current canonical data")
        if report.get("qa_counts") != current_revision["qa_counts"]:
            failures.append(f"{rel(path)} qa_counts does not match current QA ledgers")
    try:
        checked_at = datetime.fromisoformat(str(report["checked_at"]).replace("Z", "+00:00"))
        if checked_at.tzinfo is None or checked_at.tzinfo.utcoffset(checked_at) is None:
            failures.append(f"{rel(path)} checked_at must include timezone offset")
    except ValueError:
        failures.append(f"{rel(path)} checked_at is not ISO-8601 datetime")

    total = report.get("total")
    passed = report.get("passed")
    failed = report.get("failed")
    failed_ids = report.get("failed_ids")
    results = report.get("results")
    if not isinstance(total, int) or total <= 0:
        failures.append(f"{rel(path)} total must be a positive integer")
        return
    if not isinstance(passed, int) or not isinstance(failed, int):
        failures.append(f"{rel(path)} passed and failed must be integers")
        return
    if not isinstance(failed_ids, list) or not all(isinstance(item, str) for item in failed_ids):
        failures.append(f"{rel(path)} failed_ids must be a string list")
        return
    if not isinstance(results, list):
        failures.append(f"{rel(path)} results must be a list")
        return
    if len(results) != total:
        failures.append(f"{rel(path)} results length {len(results)} does not match total {total}")
    if passed + failed != total:
        failures.append(f"{rel(path)} passed + failed does not equal total")
    if failed != len(failed_ids):
        failures.append(f"{rel(path)} failed count does not match failed_ids length")
    if report.get("ok") is not (failed == 0 and not failed_ids and passed == total):
        failures.append(f"{rel(path)} ok does not match pass/fail counts")

    result_ids: List[str] = []
    result_failed_ids: List[str] = []
    for idx, result in enumerate(results):
        if not isinstance(result, dict):
            failures.append(f"{rel(path)} results[{idx}] must be an object")
            continue
        check_id = result.get("id")
        if not isinstance(check_id, str) or not check_id:
            failures.append(f"{rel(path)} results[{idx}] missing id")
            continue
        result_ids.append(check_id)
        if result.get("ok") is not True:
            result_failed_ids.append(check_id)
    duplicate_ids = [value for value, count in Counter(result_ids).items() if count > 1]
    if duplicate_ids:
        failures.append(f"{rel(path)} duplicate result ids: {sorted(duplicate_ids)}")
    # A bootstrap report is an explicit, structurally valid partial state used only to
    # break generated-artifact self-reference before one completion-grade run. It can
    # never satisfy completion because it omits the full required inventory.
    bootstrap_artifacts = report.get("bootstrap_artifacts") is True
    required_ids = set(BOOTSTRAP_REGRESSION_CHECK_IDS if bootstrap_artifacts else REQUIRED_REGRESSION_CHECK_IDS)
    if "include_browser" not in report:
        failures.append(f"{rel(path)} must record include_browser (browser checks run or explicitly skipped)")
    elif report.get("include_browser"):
        if bootstrap_artifacts:
            failures.append(f"{rel(path)} bootstrap_artifacts cannot include browser checks")
        required_ids |= BROWSER_REGRESSION_CHECK_IDS
    if "include_external" not in report:
        failures.append(f"{rel(path)} must record include_external (external checks run or explicitly skipped)")
    elif report.get("include_external"):
        if bootstrap_artifacts:
            failures.append(f"{rel(path)} bootstrap_artifacts cannot include external checks")
        required_ids |= EXTERNAL_REGRESSION_CHECK_IDS
    if bootstrap_artifacts:
        unexpected_bootstrap_ids = sorted(set(result_ids) - BOOTSTRAP_REGRESSION_CHECK_IDS)
        if unexpected_bootstrap_ids:
            failures.append(f"{rel(path)} bootstrap_artifacts has unexpected check ids: {unexpected_bootstrap_ids}")
    missing_required = sorted(required_ids - set(result_ids))
    if missing_required:
        failures.append(f"{rel(path)} missing required regression check ids: {missing_required}")
    if sorted(result_failed_ids) != sorted(failed_ids):
        failures.append(f"{rel(path)} failed_ids does not match non-ok result rows")


def validate_completion_audit_report(
    path: Path,
    failures: List[str],
    *,
    expected_total: int | None = None,
    expected_passed: int | None = None,
    expected_blocked: int | None = None,
    expected_defects: int | None = None,
) -> None:
    if not validate_json(path, failures):
        return
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    if not isinstance(report, dict):
        failures.append(f"{rel(path)} root must be an object")
        return

    required = {
        "ok",
        "complete",
        "checked_at",
        "qa_counts",
        "automated_evidence",
        "manual_gates",
        "required_artifacts",
        "completion_blockers",
        "failures",
    }
    missing = sorted(required - set(report))
    if missing:
        failures.append(f"{rel(path)} missing fields: {missing}")
        return

    try:
        checked_at = datetime.fromisoformat(str(report["checked_at"]).replace("Z", "+00:00"))
        if checked_at.tzinfo is None or checked_at.tzinfo.utcoffset(checked_at) is None:
            failures.append(f"{rel(path)} checked_at must include timezone offset")
    except ValueError:
        failures.append(f"{rel(path)} checked_at is not ISO-8601 datetime")

    completion_blockers = report.get("completion_blockers")
    failures_list = report.get("failures")
    if not isinstance(completion_blockers, list) or not all(isinstance(item, str) for item in completion_blockers):
        failures.append(f"{rel(path)} completion_blockers must be a string list")
        completion_blockers = []
    if not isinstance(failures_list, list) or not all(isinstance(item, str) for item in failures_list):
        failures.append(f"{rel(path)} failures must be a string list")
        failures_list = []

    if report.get("ok") is not (len(failures_list) == 0):
        failures.append(f"{rel(path)} ok does not match failures")
    derived_completion_blocked = bool(completion_blockers)

    automated = report.get("automated_evidence")
    if not isinstance(automated, dict):
        failures.append(f"{rel(path)} automated_evidence must be an object")
    else:
        regression_ok = automated.get("regression_suite_ok")
        regression_blockers = {
            "regression-suite-latest is not green",
            "full regression suite is not green (browser and external checks are required)",
        }
        has_regression_blocker = bool(regression_blockers.intersection(completion_blockers))
        if type(regression_ok) is not bool:
            failures.append(f"{rel(path)} automated_evidence.regression_suite_ok must be boolean")
        elif regression_ok is has_regression_blocker:
            failures.append(f"{rel(path)} automated_evidence.regression_suite_ok does not match completion blockers")

        artifact_ok = automated.get("artifact_integrity_ok")
        artifact_blocker = "artifact-integrity-latest is not green"
        if type(artifact_ok) is not bool:
            failures.append(f"{rel(path)} automated_evidence.artifact_integrity_ok must be boolean")
        elif artifact_ok is (artifact_blocker in completion_blockers):
            failures.append(f"{rel(path)} automated_evidence.artifact_integrity_ok does not match completion blockers")
        field_packet_ok = automated.get("field_packet_freshness_ok")
        field_packet_blocker = "field-packet-freshness-latest is not green"
        if field_packet_ok is not True and field_packet_blocker not in completion_blockers:
            failures.append(
                f"{rel(path)} automated_evidence.field_packet_freshness_ok is false "
                "without the matching completion blocker"
            )
        if field_packet_ok is True and field_packet_blocker in completion_blockers:
            failures.append(
                f"{rel(path)} completion_blockers claims stale field-packet freshness while "
                "automated evidence is green"
            )

    manual = report.get("manual_gates")
    if not isinstance(manual, dict):
        failures.append(f"{rel(path)} manual_gates must be an object")
    else:
        blocked = manual.get("blocked_gates")
        failed = manual.get("failed_gates")
        if not isinstance(blocked, list) or not all(isinstance(item, str) for item in blocked):
            failures.append(f"{rel(path)} manual_gates.blocked_gates must be a string list")
            blocked = []
        if not isinstance(failed, list) or not all(isinstance(item, str) for item in failed):
            failures.append(f"{rel(path)} manual_gates.failed_gates must be a string list")
            failed = []
        if sorted(blocked) == ["TC-051", "TC-056"]:
            derived_completion_blocked = True
            if report.get("complete") is not False:
                failures.append(f"{rel(path)} cannot be complete while TC-051 and TC-056 are blocked")
            blocker_text = " ".join(completion_blockers)
            for gate in ["TC-051", "TC-056"]:
                if gate not in blocker_text:
                    failures.append(f"{rel(path)} completion_blockers must mention blocked {gate}")
        if manual.get("ready_to_complete_manual_gates") is not (not blocked and not failed):
            failures.append(f"{rel(path)} ready_to_complete_manual_gates does not match blocked/failed gates")

    if report.get("complete") is not (report.get("ok") is True and not derived_completion_blocked):
        failures.append(f"{rel(path)} complete does not match ok/completion_blockers/gates")

    qa_counts = report.get("qa_counts")
    if not isinstance(qa_counts, dict):
        failures.append(f"{rel(path)} qa_counts must be an object")
    else:
        if expected_total is not None and qa_counts.get("test_cases") != expected_total:
            failures.append(f"{rel(path)} qa_counts.test_cases does not match current test count")
        if expected_defects is not None and qa_counts.get("defects") != expected_defects:
            failures.append(f"{rel(path)} qa_counts.defects does not match current defect count")
        test_results = qa_counts.get("test_results")
        if isinstance(test_results, dict):
            if expected_passed is not None and test_results.get("pass") != expected_passed:
                failures.append(f"{rel(path)} qa_counts.test_results.pass does not match current pass count")
            if expected_blocked is not None and test_results.get("blocked") != expected_blocked:
                failures.append(f"{rel(path)} qa_counts.test_results.blocked does not match current blocked count")
        else:
            failures.append(f"{rel(path)} qa_counts.test_results must be an object")

    required_artifacts = report.get("required_artifacts")
    expected_artifacts = {
        "features",
        "test_cases",
        "defects",
        "coverage_summary",
        "run_log",
        "manual_proof_status",
        "field_test_candidates",
        "voiceover_checklist",
    }
    if not isinstance(required_artifacts, dict):
        failures.append(f"{rel(path)} required_artifacts must be an object")
    else:
        missing_artifacts = sorted(expected_artifacts - set(required_artifacts))
        if missing_artifacts:
            failures.append(f"{rel(path)} required_artifacts missing keys: {missing_artifacts}")
        bad_artifacts: List[str] = []
        for name in sorted(expected_artifacts & set(required_artifacts)):
            row = required_artifacts.get(name)
            if not isinstance(row, dict):
                failures.append(f"{rel(path)} required_artifacts.{name} must be an object")
                continue
            if row.get("present") is not True or row.get("ok") is not True:
                bad_artifacts.append(name)
        if bad_artifacts:
            failures.append(f"{rel(path)} required_artifacts not ok: {bad_artifacts}")
            blocker_text = " ".join(completion_blockers)
            for name in bad_artifacts:
                if name not in blocker_text and name not in " ".join(failures_list):
                    failures.append(f"{rel(path)} completion audit does not mention bad required artifact {name}")


def validate_jsonl(path: Path, failures: List[str]) -> int:
    if not path.exists():
        failures.append(f"{rel(path)} is missing")
        return 0
    count = 0
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError as exc:
            failures.append(f"{rel(path)}:{lineno} invalid JSONL row: {exc}")
        count += 1
    return count


def validate_summary_counts(summary_path: Path, total: int, passed: int, blocked: int, defects: int, failures: List[str]) -> None:
    if not summary_path.exists():
        failures.append(f"{rel(summary_path)} is missing")
        return
    text = summary_path.read_text(encoding="utf-8")
    expected_fragments = [
        f"{total} test cases",
        f"{passed} are passing",
        f"{blocked} tests are blocked",
        f"{defects} defects",
    ]
    for fragment in expected_fragments:
        if fragment not in text:
            failures.append(f"{rel(summary_path)} missing current count fragment {fragment!r}")


def validate_required_markdown(paths: Sequence[Path], failures: List[str]) -> None:
    for path in paths:
        if not path.exists():
            failures.append(f"{rel(path)} is missing")
        elif not path.read_text(encoding="utf-8").strip():
            failures.append(f"{rel(path)} is empty")


def release_mode_from_env() -> bool:
    return os.environ.get(RELEASE_ENV, "").strip().lower() in {"1", "true", "yes"}


def build_report(*, release: bool | None = None) -> Dict[str, Any]:
    if release is None:
        release = release_mode_from_env()
    failures: List[str] = []
    warnings: List[str] = []
    features = read_csv(ROOT / "qa" / "features.csv", FEATURE_FIELDS, failures)
    tests = read_csv(ROOT / "qa" / "test-cases.csv", TEST_FIELDS, failures)
    defects = read_csv(ROOT / "qa" / "defects.csv", DEFECT_FIELDS, failures)

    feature_ids = {row["Feature ID"] for row in features}
    test_ids = {row["Test Case ID"] for row in tests}
    validate_features(features, failures)
    validate_tests(tests, feature_ids, failures)
    validate_defects(defects, feature_ids, test_ids, failures)
    validate_feature_defect_counts(features, defects, failures)

    result_counts = Counter(row["Last Result"] for row in tests)
    defect_status_counts = Counter(row["Status"] for row in defects)
    validate_summary_counts(
        ROOT / "qa" / "coverage-summary.md",
        total=len(tests),
        passed=result_counts.get("pass", 0),
        blocked=result_counts.get("blocked", 0),
        defects=len(defects),
        failures=failures,
    )
    validate_required_markdown(
        [
            ROOT / "qa" / "run-log.md",
            ROOT / "qa" / "coverage-summary.md",
            ROOT / "qa" / "field-test-checklist.md",
            ROOT / "qa" / "voiceover-checklist.md",
            ROOT / "qa" / "field-test-candidates.md",
        ],
        failures,
    )
    for path in [
        ROOT / "qa" / "manual-proof-evidence-template.json",
    ]:
        validate_json(path, failures)
    validate_regression_suite_report(ROOT / "qa" / "regression-suite-latest.json", failures)
    validate_data_snapshot_report(
        ROOT / "qa" / "free-db-completeness-latest.json",
        ROOT / "data" / "free_intelligence.json",
        failures,
        kind="free-db",
    )
    validate_data_snapshot_report(
        ROOT / "qa" / "valencia-source-audit-latest.json",
        ROOT / "data" / "free_intelligence.json",
        failures,
        kind="valencia",
    )
    validate_manual_proof_status_report(ROOT / "qa" / "manual-proof-status.json", failures)
    validate_field_packet_freshness_report(
        ROOT / "qa" / "field-packet-freshness-latest.json",
        failures,
        canonical_path=ROOT / "data" / "free_intelligence.json",
        packet_path=ROOT / "qa" / "field-test-candidates.md",
        enforce_age=release,
        warnings=warnings,
    )
    validate_completion_audit_report(
        ROOT / "qa" / "completion-audit-latest.json",
        failures,
        expected_total=len(tests),
        expected_passed=result_counts.get("pass", 0),
        expected_blocked=result_counts.get("blocked", 0),
        expected_defects=len(defects),
    )
    jsonl_counts = {
        rel(path): validate_jsonl(path, failures)
        for path in [
            ROOT / "qa" / "source-link-history.jsonl",
            ROOT / "qa" / "ingestion-canary-history.jsonl",
            ROOT / "qa" / "live-source-canary-history.jsonl",
        ]
    }

    return {
        "ok": not failures,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "release": release,
        "counts": {
            "features": len(features),
            "test_cases": len(tests),
            "test_results": dict(result_counts),
            "defects": len(defects),
            "defect_statuses": dict(defect_status_counts),
            "jsonl_rows": jsonl_counts,
        },
        "failures": failures,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument(
        "--release",
        action="store_true",
        help=f"Release gate: also fail on receipts older than 24 hours (or set {RELEASE_ENV}=1).",
    )
    args = parser.parse_args()

    report = build_report(release=True if args.release else None)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": report["ok"],
        "counts": report["counts"],
        "failures": report["failures"],
        "warnings": report["warnings"],
        "release": report["release"],
        "out": str(out),
    }, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
