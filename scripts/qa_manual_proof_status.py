#!/usr/bin/env python3
"""Audit manual proof gates for Volta Go.

This does not perform the physical field test or native VoiceOver traversal.
It verifies that the required checklists/candidate packet exist and, when a
manual evidence JSON file is supplied, determines whether TC-051/TC-056 can be
counted as passed, blocked, or failed-needs-defect.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = ROOT / "qa" / "manual-proof-evidence.json"
DEFAULT_OUT = ROOT / "qa" / "manual-proof-status.json"
MANUAL_TEMPLATE = ROOT / "qa" / "manual-proof-evidence-template.json"
FIELD_CHECKLIST = ROOT / "qa" / "field-test-checklist.md"
VOICEOVER_CHECKLIST = ROOT / "qa" / "voiceover-checklist.md"
FIELD_PACKET = ROOT / "qa" / "field-test-candidates.md"
CANONICAL_INTEL = ROOT / "data" / "free_intelligence.json"
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
from source_policy import recordset_sha256  # noqa: E402

FIELD_CHECKLIST_TOKENS = [
    "Default Trusted View",
    "Open Now",
    "Source Launch",
    "On-Site Reality",
    "Evidence To Capture",
    "Current Blocker",
]
VOICEOVER_CHECKLIST_TOKENS = [
    "Map, sheet, search, and filters",
    "Status and mode changes",
    "Charger Card Semantics",
    "Detail and Settings semantics",
    "Open Now And Empty Recovery",
    "VoiceOver Transcript Requirements",
    "Evidence To Capture",
    "Current Blocker",
]
FIELD_PACKET_TOKENS = [
    "Generated local:",
    "Expected local recordset SHA-256:",
    "Deployed recordset SHA-256:",
    "Candidate revision match:",
    "Candidate status:",
    "Live Route-Ready Candidates",
    "Trusted Backup Leads",
    "Field-Test Result Table",
    "Stop Conditions",
]


def rel_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


FIELD_REQUIRED_TEXT = [
    "tested_at_local",
    "candidate",
    "recordset_sha256",
    "device",
    "app_url",
    "provider_source_url",
    "volta_card_price_text",
    "volta_card_parking_text",
    "volta_card_source_text",
    "volta_card_live_or_lead_text",
    "onsite_notes",
]
FIELD_REQUIRED_EVIDENCE = [
    "volta_default_screenshot",
    "open_now_screenshot",
    "volta_card_screenshot",
    "volta_source_details_screenshot",
    "provider_source_screenshot",
    "arrival_or_address_evidence",
    "parking_terms_photo_or_note",
    "charge_session_screenshot",
]
FIELD_REQUIRED_BOOLS = [
    "volta_disclosed_live_or_lead_status",
    "volta_disclosed_parking_cost_or_risk",
    "volta_source_link_opened_from_card",
    "volta_card_matches_provider_before_departure",
    "provider_page_matched",
    "parking_free_or_acceptable",
    "parking_cost_disclosed_in_volta",
    "tesla_connector_fit",
    "tariff_within_cap",
    "charge_started",
]
FIELD_REQUIRED_NUMBERS = [
    "parking_cost_eur",
    "tariff_eur_per_kwh",
]
VOICEOVER_REQUIRED_TEXT = [
    "tested_at_local",
    "browser_or_device",
    "app_url",
    "viewport",
    "recording_or_transcript_evidence",
    "app_entry_transcript",
    "status_update_transcript",
    "first_card_announcement",
    "first_card_transcript",
    "card_actions_transcript",
    "source_details_notes",
    "source_details_transcript",
    "settings_notes",
    "settings_transcript",
    "open_now_notes",
    "open_now_transcript",
    "focus_order_notes",
]
VOICEOVER_REQUIRED_BOOLS = [
    "controls_reachable",
    "filter_state_announced",
    "status_update_announced_or_discoverable",
    "first_card_includes_price_parking_proof_source",
    "card_actions_station_named",
    "source_details_reachable",
    "detail_claims_separate",
    "connector_rows_understandable",
    "settings_reachable",
    "settings_preset_selected_state_announced",
    "settings_test_connection_reachable",
    "open_now_fail_closed",
    "recovery_actions_reachable",
    "no_focus_trap",
]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def artifact_status(path: Path, tokens: List[str]) -> Dict[str, Any]:
    if not path.exists():
        return {"path": rel_path(path), "ok": False, "missing": True, "missing_tokens": tokens}
    text = read_text(path)
    missing_tokens = [token for token in tokens if token not in text]
    return {
        "path": rel_path(path),
        "ok": not missing_tokens,
        "missing": False,
        "missing_tokens": missing_tokens,
    }


def manual_template_app_url(path: Path = MANUAL_TEMPLATE) -> str | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    field = payload.get("field_test")
    if not isinstance(field, dict):
        return None
    app_url = field.get("app_url")
    return app_url.strip() if isinstance(app_url, str) and app_url.strip() else None


def manual_template_voiceover_app_url(path: Path = MANUAL_TEMPLATE) -> str | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    voiceover = payload.get("voiceover")
    if not isinstance(voiceover, dict):
        return None
    app_url = voiceover.get("app_url")
    return app_url.strip() if isinstance(app_url, str) and app_url.strip() else None


def field_checklist_status(path: Path = FIELD_CHECKLIST, template_path: Path = MANUAL_TEMPLATE) -> Dict[str, Any]:
    status = artifact_status(path, FIELD_CHECKLIST_TOKENS)
    if status.get("missing"):
        return status

    expected_url = manual_template_app_url(template_path)
    missing_tokens = list(status["missing_tokens"])
    if not expected_url:
        missing_tokens.append("field_test.app_url")
    else:
        text = read_text(path)
        accepted_urls = {expected_url, expected_url.rstrip("/")}
        if not any(url and url in text for url in accepted_urls):
            missing_tokens.append(f"field_test.app_url {expected_url}")

    status["missing_tokens"] = missing_tokens
    status["ok"] = not missing_tokens
    return status


def voiceover_checklist_status(path: Path = VOICEOVER_CHECKLIST, template_path: Path = MANUAL_TEMPLATE) -> Dict[str, Any]:
    status = artifact_status(path, VOICEOVER_CHECKLIST_TOKENS)
    if status.get("missing"):
        return status

    expected_url = manual_template_voiceover_app_url(template_path)
    missing_tokens = list(status["missing_tokens"])
    if not expected_url:
        missing_tokens.append("voiceover.app_url")
    else:
        text = read_text(path)
        accepted_urls = {expected_url, expected_url.rstrip("/")}
        if not any(url and url in text for url in accepted_urls):
            missing_tokens.append(f"voiceover.app_url {expected_url}")

    status["missing_tokens"] = missing_tokens
    status["ok"] = not missing_tokens
    return status


def canonical_recordset_sha256(path: Path = CANONICAL_INTEL) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise ValueError(f"{path} records must be a list")
    computed = recordset_sha256(records)
    stored = payload.get("recordset_sha256")
    if stored and stored != computed:
        raise ValueError(f"{path} stored recordset_sha256 does not match its records")
    return computed


def field_packet_status(
    path: Path = FIELD_PACKET,
    template_path: Path = MANUAL_TEMPLATE,
    canonical_path: Path = CANONICAL_INTEL,
) -> Dict[str, Any]:
    status = artifact_status(path, FIELD_PACKET_TOKENS)
    if status.get("missing"):
        return status

    expected_url = manual_template_app_url(template_path)
    missing_tokens = list(status["missing_tokens"])
    text = read_text(path)
    match = re.search(r"(?m)^Base URL:\s*`([^`]+)`\s*$", text)
    if not expected_url:
        missing_tokens.append("field_test.app_url")
    elif not match:
        missing_tokens.append("Base URL")
    elif match.group(1).rstrip("/") != expected_url.rstrip("/"):
        missing_tokens.append(f"Base URL must match field_test.app_url ({expected_url})")

    expected_match = re.search(r"(?m)^Expected local recordset SHA-256:\s*`([^`]+)`\s*$", text)
    deployed_match = re.search(r"(?m)^Deployed recordset SHA-256:\s*`([^`]+)`\s*$", text)
    expected_sha = expected_match.group(1).strip() if expected_match else None
    deployed_sha = deployed_match.group(1).strip() if deployed_match else None
    try:
        canonical_sha = canonical_recordset_sha256(canonical_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        canonical_sha = None
        missing_tokens.append(f"current canonical recordset SHA-256 ({type(exc).__name__})")
    revision_claimed = bool(re.search(r"(?m)^Candidate revision match:\s*\*\*yes\*\*\s*$", text))
    revision_match = bool(
        revision_claimed
        and canonical_sha
        and expected_sha == canonical_sha
        and deployed_sha == canonical_sha
    )

    status["missing_tokens"] = missing_tokens
    status["ok"] = not missing_tokens
    status["candidate_revision_match"] = revision_match
    status["ready_for_field_test"] = status["ok"] and revision_match
    status["expected_recordset_sha256"] = expected_sha
    status["deployed_recordset_sha256"] = deployed_sha
    status["canonical_recordset_sha256"] = canonical_sha
    return status


def template_status(path: Path = MANUAL_TEMPLATE) -> Dict[str, Any]:
    rel_path = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
    if not path.exists():
        return {"path": rel_path, "ok": False, "missing": True, "missing_fields": ["field_test", "voiceover"]}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"path": rel_path, "ok": False, "missing": False, "json_error": str(exc), "missing_fields": []}
    if not isinstance(payload, dict):
        return {"path": rel_path, "ok": False, "missing": False, "missing_fields": ["<root object>"]}

    missing: List[str] = []
    field = payload.get("field_test")
    voiceover = payload.get("voiceover")
    if not isinstance(field, dict):
        missing.append("field_test")
    else:
        for key in FIELD_REQUIRED_TEXT + FIELD_REQUIRED_EVIDENCE + FIELD_REQUIRED_BOOLS + FIELD_REQUIRED_NUMBERS + ["mismatches"]:
            if key not in field:
                missing.append(f"field_test.{key}")
    if not isinstance(voiceover, dict):
        missing.append("voiceover")
    else:
        for key in VOICEOVER_REQUIRED_TEXT + VOICEOVER_REQUIRED_BOOLS + ["issues"]:
            if key not in voiceover:
                missing.append(f"voiceover.{key}")

    return {"path": rel_path, "ok": not missing, "missing": False, "missing_fields": missing}


def require_text(payload: Dict[str, Any], keys: List[str], missing: List[str]) -> None:
    for key in keys:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            missing.append(key)


def require_bools(payload: Dict[str, Any], keys: List[str], missing: List[str]) -> None:
    for key in keys:
        if not isinstance(payload.get(key), bool):
            missing.append(key)


def require_iso_datetime(payload: Dict[str, Any], key: str, missing: List[str], failures: List[str]) -> None:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        missing.append(key)
        return
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        failures.append(f"{key} is not ISO-8601 datetime")
        return
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        failures.append(f"{key} must include timezone offset")


def require_http_url(payload: Dict[str, Any], key: str, missing: List[str], failures: List[str]) -> None:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        missing.append(key)
        return
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        failures.append(f"{key} must be an http(s) URL")


def require_expected_app_url(payload: Dict[str, Any], expected: str | None, missing: List[str]) -> None:
    if not expected:
        return
    actual = payload.get("app_url")
    if isinstance(actual, str) and actual.strip() and actual.rstrip("/") != expected.rstrip("/"):
        missing.append(f"app_url must match manual template ({expected})")


def validate_field_evidence(payload: Dict[str, Any] | None, expected_app_url: str | None = None) -> Dict[str, Any]:
    if payload is None:
        return {"gate": "TC-051", "status": "blocked", "reason": "manual field-test evidence missing"}

    missing: List[str] = []
    failures: List[str] = []
    require_text(payload, FIELD_REQUIRED_TEXT, missing)
    require_text(payload, FIELD_REQUIRED_EVIDENCE, missing)
    require_bools(payload, FIELD_REQUIRED_BOOLS, missing)
    require_iso_datetime(payload, "tested_at_local", missing, failures)
    require_http_url(payload, "app_url", missing, failures)
    require_expected_app_url(payload, expected_app_url, missing)
    require_http_url(payload, "provider_source_url", missing, failures)

    tariff = payload.get("tariff_eur_per_kwh")
    if not isinstance(tariff, (int, float)):
        missing.append("tariff_eur_per_kwh")
    elif tariff > 0.25:
        failures.append(f"tariff_eur_per_kwh={tariff} exceeds 0.25")

    parking_cost = payload.get("parking_cost_eur")
    if not isinstance(parking_cost, (int, float)):
        missing.append("parking_cost_eur")
    elif parking_cost > 0 and payload.get("parking_cost_disclosed_in_volta") is not True:
        failures.append("paid parking was observed but not marked as disclosed in Volta")

    for key in FIELD_REQUIRED_BOOLS:
        if payload.get(key) is False:
            failures.append(f"{key}=false")

    mismatches = payload.get("mismatches", [])
    if not isinstance(mismatches, list):
        missing.append("mismatches")
    elif mismatches:
        failures.append("manual field test recorded mismatches that need a defect/data issue")

    if missing:
        return {"gate": "TC-051", "status": "blocked", "reason": "manual field-test evidence incomplete", "missing": missing}
    if failures:
        return {"gate": "TC-051", "status": "fail_needs_defect", "failures": failures}
    return {"gate": "TC-051", "status": "pass", "candidate": payload.get("candidate"), "tested_at_local": payload.get("tested_at_local")}


def validate_voiceover_evidence(payload: Dict[str, Any] | None, expected_app_url: str | None = None) -> Dict[str, Any]:
    if payload is None:
        return {"gate": "TC-056", "status": "blocked", "reason": "manual VoiceOver evidence missing"}

    missing: List[str] = []
    failures: List[str] = []
    require_text(payload, VOICEOVER_REQUIRED_TEXT, missing)
    require_bools(payload, VOICEOVER_REQUIRED_BOOLS, missing)
    require_iso_datetime(payload, "tested_at_local", missing, failures)
    require_http_url(payload, "app_url", missing, failures)
    require_expected_app_url(payload, expected_app_url, missing)

    for key in VOICEOVER_REQUIRED_BOOLS:
        if payload.get(key) is False:
            failures.append(f"{key}=false")

    issues = payload.get("issues", [])
    if not isinstance(issues, list):
        missing.append("issues")
    elif issues:
        failures.append("manual VoiceOver traversal recorded issues that need triage")

    if missing:
        return {"gate": "TC-056", "status": "blocked", "reason": "manual VoiceOver evidence incomplete", "missing": missing}
    if failures:
        return {"gate": "TC-056", "status": "fail_needs_defect", "failures": failures}
    return {"gate": "TC-056", "status": "pass", "tested_at_local": payload.get("tested_at_local")}


def load_evidence(path: Path) -> Tuple[Dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, f"{path}: invalid JSON: {exc}"
    if not isinstance(data, dict):
        return None, f"{path}: evidence root must be an object"
    return data, None


def build_report(evidence_path: Path = DEFAULT_EVIDENCE) -> Dict[str, Any]:
    artifacts = {
        "field_checklist": field_checklist_status(FIELD_CHECKLIST, MANUAL_TEMPLATE),
        "voiceover_checklist": voiceover_checklist_status(VOICEOVER_CHECKLIST, MANUAL_TEMPLATE),
        "field_candidate_packet": field_packet_status(FIELD_PACKET, MANUAL_TEMPLATE),
        "manual_evidence_template": template_status(MANUAL_TEMPLATE),
    }
    evidence, evidence_error = load_evidence(evidence_path)
    field_payload = evidence.get("field_test") if isinstance(evidence, dict) else None
    voiceover_payload = evidence.get("voiceover") if isinstance(evidence, dict) else None
    if field_payload is not None and not isinstance(field_payload, dict):
        field_payload = {}
    if voiceover_payload is not None and not isinstance(voiceover_payload, dict):
        voiceover_payload = {}

    gates = [
        validate_field_evidence(field_payload, manual_template_app_url(MANUAL_TEMPLATE)),
        validate_voiceover_evidence(voiceover_payload, manual_template_voiceover_app_url(MANUAL_TEMPLATE)),
    ]
    packet_status = artifacts["field_candidate_packet"]
    if gates[0]["status"] == "pass" and not packet_status.get("ready_for_field_test"):
        gates[0] = {
            "gate": "TC-051",
            "status": "blocked",
            "reason": "production field packet does not match the local canonical candidate",
        }
    elif gates[0]["status"] == "pass":
        field_sha = field_payload.get("recordset_sha256") if isinstance(field_payload, dict) else None
        expected_sha = packet_status.get("expected_recordset_sha256")
        deployed_sha = packet_status.get("deployed_recordset_sha256")
        if not field_sha or field_sha != expected_sha or expected_sha != deployed_sha:
            gates[0] = {
                "gate": "TC-051",
                "status": "blocked",
                "reason": "manual field evidence is not bound to the packet's matching recordset",
                "evidence_recordset_sha256": field_sha,
                "packet_recordset_sha256": expected_sha,
            }
    artifact_failures = [name for name, row in artifacts.items() if not row["ok"]]
    failed_gates = [gate["gate"] for gate in gates if gate["status"] == "fail_needs_defect"]
    blocked_gates = [gate["gate"] for gate in gates if gate["status"] == "blocked"]
    ready = not artifact_failures and not evidence_error and all(gate["status"] == "pass" for gate in gates)
    return {
        "ok": not artifact_failures and not evidence_error,
        "ready_to_complete_manual_gates": ready,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "evidence_path": str(evidence_path.relative_to(ROOT)) if evidence_path.is_relative_to(ROOT) else str(evidence_path),
        "evidence_present": evidence is not None,
        "evidence_error": evidence_error,
        "artifacts": artifacts,
        "gates": gates,
        "blocked_gates": blocked_gates,
        "failed_gates": failed_gates,
        "artifact_failures": artifact_failures,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--strict", action="store_true", help="Return non-zero unless all manual gates pass.")
    args = parser.parse_args(argv)

    report = build_report(Path(args.evidence))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": report["ok"],
        "ready_to_complete_manual_gates": report["ready_to_complete_manual_gates"],
        "blocked_gates": report["blocked_gates"],
        "failed_gates": report["failed_gates"],
        "out": str(out),
    }, ensure_ascii=False, indent=2))
    if not report["ok"]:
        return 1
    if args.strict and not report["ready_to_complete_manual_gates"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
