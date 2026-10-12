#!/usr/bin/env python3
"""Check that the durable field-test packet still matches current API state."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKET = ROOT / "qa" / "field-test-candidates.md"
CANONICAL_INTEL = ROOT / "data" / "free_intelligence.json"
FIELD_PACKET_SPEC = importlib.util.spec_from_file_location(
    "qa_field_test_packet",
    ROOT / "scripts" / "qa_field_test_packet.py",
)
qa_field_test_packet = importlib.util.module_from_spec(FIELD_PACKET_SPEC)
assert FIELD_PACKET_SPEC.loader is not None
FIELD_PACKET_SPEC.loader.exec_module(qa_field_test_packet)


def compact(value: Any) -> str:
    return qa_field_test_packet.compact(value)


def report_path(path: Path) -> str:
    """Keep repository evidence portable without disguising external inputs."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def canonical_recordset_sha256(path: Path = CANONICAL_INTEL) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise ValueError(f"{path} records must be a list")
    computed = qa_field_test_packet.recordset_sha256(records)
    stored = payload.get("recordset_sha256")
    if stored and stored != computed:
        raise ValueError(f"{path} stored recordset_sha256 does not match its records")
    return computed


def parse_packet(text: str) -> Dict[str, Any]:
    patterns = {
        "base_url": r"Base URL: `(?P<value>[^`]+)`",
        "expected_recordset_sha256": r"Expected local recordset SHA-256: `(?P<value>[^`]+)`",
        "deployed_recordset_sha256": r"Deployed recordset SHA-256: `(?P<value>[^`]+)`",
        "candidate_revision_match": r"Candidate revision match: \*\*(?P<value>yes|no)\*\*",
        "default": r"- Default response: (?P<stations>\d+) live stations, (?P<leads>\d+) source-backed leads\.",
        "open_now": r"- Open now response: (?P<stations>\d+) live stations, (?P<leads>\d+) leads\.",
        "default_warning": r"- Default warning: (?P<value>.*)",
        "open_warning": r"- Open now warning: (?P<value>.*)",
        "rows": r"- Total default rows inspected: (?P<value>\d+)",
        "supercharger_risk": r"- Tesla/Supercharger tariff-risk leads excluded from trusted backups: (?P<value>\d+)",
    }
    parsed: Dict[str, Any] = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if not match:
            raise ValueError(f"missing packet field {key}")
        if key in {"default", "open_now"}:
            parsed[key] = {
                "stations": int(match.group("stations")),
                "leads": int(match.group("leads")),
            }
        elif key in {"rows", "supercharger_risk"}:
            parsed[key] = int(match.group("value"))
        else:
            parsed[key] = compact(match.group("value"))
    parsed["candidate_revision_match"] = parsed["candidate_revision_match"] == "yes"
    parsed["has_no_live_route_ready_message"] = "No live route-ready candidate is confirmed right now" in text
    return parsed


def payload_warning(payload: Dict[str, Any]) -> str:
    return compact(payload.get("warning") or ((payload.get("meta") or {}).get("coverage") or {}).get("warning") or "none")


STALE_FAILURE_TOKENS = ("http error 429", "http error 502", "bad gateway", "derived from default payload", "serving stale cache")
SAFETY_WARNING_TOKENS = ("low live coverage", "refresh or widen", "provider-source verification", "verify")
RAW_TECHNICAL_WARNING_RE = re.compile(
    r"\b(?:HTTPError|HTTP Error|Traceback|TypeError|Exception|API \d{3}|Bad Gateway|Too Many Requests)\b",
    re.IGNORECASE,
)


def warning_has_stale_failure(value: str) -> bool:
    lowered = compact(value).lower()
    return any(token in lowered for token in STALE_FAILURE_TOKENS)


def warning_has_safety_signal(value: str) -> bool:
    lowered = compact(value).lower()
    return any(token in lowered for token in SAFETY_WARNING_TOKENS)


def warning_has_raw_technical_failure(value: str) -> bool:
    return bool(RAW_TECHNICAL_WARNING_RE.search(compact(value)))


def warning_for_report(value: str) -> str:
    """Return a driver/QA-safe warning string without raw exception copy."""
    text = compact(value)
    had_raw_failure = warning_has_raw_technical_failure(text)
    replacements = (
        (
            "Live refresh failed; serving stale cache from a broader filter and reapplying current route filters.",
            "Live refresh limited; stale cached data from a broader filter was rechecked against current route filters.",
        ),
        (
            "Live refresh failed; serving stale cache.",
            "Live refresh limited; stale cached data used.",
        ),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    text = re.sub(r"HTTPError:\s*HTTP Error \d{3}:\s*(?:Too Many Requests|Bad Gateway)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"HTTP Error \d{3}:\s*(?:Too Many Requests|Bad Gateway)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:Traceback|TypeError|Exception|Bad Gateway|Too Many Requests)\b:?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bAPI \d{3}\b", "live source failure", text, flags=re.IGNORECASE)
    text = compact(text.strip(" ;."))
    if had_raw_failure and not warning_has_safety_signal(text):
        text = compact(f"{text}. Verify source before driving." if text else "Live refresh limited; verify source before driving.")
    return text


def current_state(base_url: str, timeout: float) -> Dict[str, Any]:
    intel_payload = qa_field_test_packet.fetch_json(base_url, "/api/intel", timeout)
    deployed_recordset_sha256 = intel_payload.get("recordset_sha256")
    if not isinstance(deployed_recordset_sha256, str) or not deployed_recordset_sha256.strip():
        raise ValueError("/api/intel did not publish recordset_sha256")
    default_payload = qa_field_test_packet.fetch_json_with_fallback(
        base_url,
        "/api/chargers?max_price=0.25&include_leads=true&force=true",
        "/api/chargers?max_price=0.25&include_leads=true",
        timeout,
    )
    try:
        open_now_payload = qa_field_test_packet.fetch_json_with_fallback(
            base_url,
            "/api/chargers?max_price=0.25&include_leads=false&available=true&fast=false&force=true",
            "/api/chargers?max_price=0.25&include_leads=false&available=true&fast=false",
            timeout,
        )
        open_now_derived = False
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        open_now_payload = qa_field_test_packet.synthesize_open_now_payload(default_payload, exc)
        open_now_derived = True

    rows = qa_field_test_packet.collect_rows(default_payload)
    raw_default_warning = payload_warning(default_payload)
    raw_open_warning = payload_warning(open_now_payload)
    return {
        "deployed_recordset_sha256": deployed_recordset_sha256.strip(),
        "default": {
            "stations": len(default_payload.get("stations") or []),
            "leads": len(default_payload.get("leads") or []),
        },
        "open_now": {
            "stations": len(open_now_payload.get("stations") or []),
            "leads": len(open_now_payload.get("leads") or []),
        },
        "default_warning": warning_for_report(raw_default_warning),
        "open_warning": warning_for_report(raw_open_warning),
        "default_warning_had_technical_failure": warning_has_raw_technical_failure(raw_default_warning),
        "open_warning_had_technical_failure": warning_has_raw_technical_failure(raw_open_warning),
        "rows": len(rows),
        "supercharger_risk": sum(1 for row in rows if qa_field_test_packet.has_unofficial_supercharger_tariff_risk(row)),
        "open_now_derived": open_now_derived,
    }


def compare(
    packet: Dict[str, Any],
    current: Dict[str, Any],
    canonical_sha256: str | None = None,
) -> List[str]:
    failures: List[str] = []
    warning_drifts: List[str] = []
    if canonical_sha256 is not None:
        current_deployed = current.get("deployed_recordset_sha256")
        if packet.get("expected_recordset_sha256") != canonical_sha256:
            failures.append(
                "packet expected recordset is stale: "
                f"packet={packet.get('expected_recordset_sha256')} canonical={canonical_sha256}"
            )
        if packet.get("deployed_recordset_sha256") != canonical_sha256:
            failures.append(
                "packet deployed recordset does not match current canonical: "
                f"packet={packet.get('deployed_recordset_sha256')} canonical={canonical_sha256}"
            )
        if current_deployed != canonical_sha256:
            failures.append(
                "current deployed recordset does not match current canonical: "
                f"deployed={current_deployed} canonical={canonical_sha256}"
            )
        if packet.get("deployed_recordset_sha256") != current_deployed:
            failures.append(
                "packet deployed recordset is stale relative to current /api/intel: "
                f"packet={packet.get('deployed_recordset_sha256')} current={current_deployed}"
            )
        if packet.get("candidate_revision_match") is not True:
            failures.append("packet does not claim a matching candidate revision")
    for key in ("default", "open_now"):
        if packet[key] != current[key]:
            failures.append(f"{key} counts stale: packet={packet[key]} current={current[key]}")
    for key in ("default_warning", "open_warning"):
        packet_warning = compact(packet[key])
        current_warning = compact(current[key])
        if packet_warning != current_warning:
            if warning_has_stale_failure(packet_warning) and not warning_has_stale_failure(current_warning):
                failures.append(f"{key} stale: packet={packet_warning!r} current={current_warning!r}")
            elif not warning_has_safety_signal(packet_warning) and warning_has_safety_signal(current_warning):
                failures.append(f"{key} missing current safety warning: packet={packet_warning!r} current={current_warning!r}")
            else:
                warning_drifts.append(f"{key} drift tolerated: packet={packet_warning!r} current={current_warning!r}")
    for key in ("rows", "supercharger_risk"):
        if packet[key] != current[key]:
            failures.append(f"{key} stale: packet={packet[key]} current={current[key]}")
    if current["open_now"]["stations"] == 0 and not packet["has_no_live_route_ready_message"]:
        failures.append("packet missing no-live-route-ready message while current Open now has zero stations")
    packet["_warning_drifts"] = warning_drifts
    return failures


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, default=DEFAULT_PACKET)
    parser.add_argument("--base-url", default=None, help="Override packet Base URL")
    parser.add_argument("--canonical", type=Path, default=CANONICAL_INTEL)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    checked_at = datetime.now(timezone.utc).isoformat()
    try:
        text = args.packet.read_text(encoding="utf-8")
        packet = parse_packet(text)
        canonical_sha = canonical_recordset_sha256(args.canonical)
        base_url = (args.base_url or packet["base_url"]).rstrip("/")
    except Exception as exc:
        result = {
            "ok": False,
            "checked_at": checked_at,
            "packet": report_path(args.packet),
            "base_url": args.base_url,
            "canonical_data": report_path(args.canonical),
            "failures": [f"{type(exc).__name__}: {exc}"],
        }
    else:
        current_error = None
        try:
            current = current_state(base_url, args.timeout)
            failures = compare(packet, current, canonical_sha)
        except ValueError:
            current_error = "current deployment does not expose recordset_sha256"
            current = {
                "deployed_recordset_sha256": None,
                "error": current_error,
            }
            failures = [current_error]
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            current_error = "current deployment state is unavailable"
            current = {
                "deployed_recordset_sha256": None,
                "error": current_error,
            }
            failures = [current_error]
        result = {
            "ok": not failures,
            "checked_at": checked_at,
            "packet": report_path(args.packet),
            "packet_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "base_url": base_url,
            "canonical_data": report_path(args.canonical),
            "canonical_recordset_sha256": canonical_sha,
            "packet_state": packet,
            "current_state": current,
            "current_state_error": current_error,
            "warning_drifts": packet.get("_warning_drifts", []),
            "failures": failures,
        }

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
