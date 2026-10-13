#!/usr/bin/env python3
"""Completeness audit for Volta free/cheap charger intelligence records.

The audit is descriptive by default: it measures coverage for the fields the
driver needs, without inventing missing facts. Use --strict only after the DB is
expected to be fully enriched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from .json_recordset import load_json_recordset
except ImportError:
    from json_recordset import load_json_recordset


UNKNOWN_CONNECTOR_LABELS = {"", "unknown", "unknown connector", "connector", "other", "n/a", "none"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def has_known_connector_label(record: dict[str, Any]) -> bool:
    labels = [str(x or "").strip().lower() for x in record.get("connector_labels") or []]
    for connector in record.get("connectors") or []:
        if isinstance(connector, dict):
            labels.append(str(connector.get("label") or connector.get("type") or "").strip().lower())
    return any(label and label not in UNKNOWN_CONNECTOR_LABELS for label in labels)


REQUIRED = {
    "parking": lambda r: bool((r.get("parking") or {}).get("kind")) and (r.get("parking") or {}).get("kind") != "unknown",
    "parking_claim": lambda r: bool((r.get("parking_claim") or {}).get("source"))
    and bool((r.get("parking_claim") or {}).get("url"))
    and bool((r.get("parking_claim") or {}).get("kind")),
    "access_context": lambda r: bool((r.get("access") or {}).get("level")),
    "access_claim": lambda r: bool((r.get("access_claim") or {}).get("source"))
    and bool((r.get("access_claim") or {}).get("url"))
    and bool((r.get("access_claim") or {}).get("level"))
    and bool((r.get("access_claim") or {}).get("parking_kind"))
    and (r.get("access_claim") or {}).get("parking_paid") is not None
    and (r.get("access_claim") or {}).get("private_or_restricted") is not None,
    "port_count": lambda r: bool(r.get("total_evse") or r.get("connector_count") or r.get("connectors")),
    "source_integration": lambda r: bool((r.get("source_integration") or {}).get("mode")),
    "station_status_evidence": lambda r: bool((r.get("source_integration") or {}).get("station_status_fields")),
    "connector_status_evidence": lambda r: bool((r.get("source_integration") or {}).get("connector_status_fields")),
    "connectors": lambda r: has_known_connector_label(r) and bool(r.get("connectors")),
    "power_kw": lambda r: r.get("max_power_kw") is not None or any(c.get("power_kw") is not None for c in r.get("connectors") or []),
    "connector_power_kw": lambda r: any(c.get("power_kw") is not None for c in r.get("connectors") or []),
    "hardware_claim": lambda r: bool((r.get("hardware_claim") or {}).get("source"))
    and bool((r.get("hardware_claim") or {}).get("url"))
    and bool((r.get("hardware_claim") or {}).get("port_count"))
    and bool((r.get("hardware_claim") or {}).get("connector_types"))
    and (r.get("hardware_claim") or {}).get("max_power_kw") is not None
    and bool((r.get("hardware_claim") or {}).get("connector_power_kw")),
    "exact_location": lambda r: isinstance(r.get("lat"), (int, float)) and isinstance(r.get("lon"), (int, float)) and bool(r.get("navigation")),
    "location_claim": lambda r: bool((r.get("location_claim") or {}).get("source"))
    and bool((r.get("location_claim") or {}).get("url"))
    and bool((r.get("location_claim") or {}).get("precision"))
    and bool((r.get("location_claim") or {}).get("method"))
    and isinstance((r.get("location_claim") or {}).get("lat"), (int, float))
    and isinstance((r.get("location_claim") or {}).get("lon"), (int, float)),
    "primary_source": lambda r: bool((r.get("primary_source") or {}).get("url"))
    and bool((r.get("primary_source") or {}).get("kind"))
    and bool((r.get("primary_source") or {}).get("quality"))
    and bool((r.get("primary_source") or {}).get("action")),
    "photo": lambda r: bool(((r.get("media") or {}).get("photos") or [])),
}


def sample(rows: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    return [
        {
            "id": r.get("id"),
            "name": r.get("name"),
            "source": r.get("source"),
            "source_url": r.get("source_url"),
        }
        for r in rows[:limit]
    ]


def gap_reason(record: dict[str, Any], key: str) -> str:
    source = str(record.get("source") or "").lower()
    raw = record.get("raw") or {}
    media = record.get("media") or {}

    if key == "photo":
        if media.get("photo_status") == "no_public_photo":
            return "current public source payload has no charger photo URL"
        return "no public media URL recorded"
    if key == "parking_claim":
        return "parking source/kind claim is missing or incomplete"
    if key == "access_claim":
        return "access/private/paid claim is missing or incomplete"
    if key == "hardware_claim":
        return "hardware source/port/connector/power claim is missing or incomplete"
    if key == "location_claim":
        return "location source/precision claim is missing or incomplete"
    if key == "primary_source":
        return "primary source handoff contract is missing or incomplete"

    if "lidl" in source and key in {
        "port_count",
        "station_status_evidence",
        "connector_status_evidence",
        "connectors",
        "power_kw",
        "connector_power_kw",
    }:
        if key == "port_count" and raw.get("listed_charger_count") is None:
            return "official Lidl page lists this store but omits charger count"
        if raw.get("connector_scope"):
            return "official Lidl page publishes network-level compatibility only, not store-specific connector/status/power"

    if key in {"station_status_evidence", "connector_status_evidence"}:
        return "source confirms existence/policy but publishes no status field"
    if key in {"connectors", "power_kw", "connector_power_kw"}:
        return "source confirms charger but publishes no exact connector/power field"
    if key == "port_count":
        return "source confirms charger but publishes no exact port count"
    return "missing required public-source evidence"


def audit_records(path: Path, *, strict: bool = False) -> dict:
    """Keep only aggregate counters and the same bounded examples as the report."""
    total = free_count = limited_count = 0
    by_source, by_photo_status, limited_by_source = Counter(), Counter(), Counter()
    missing_counts = Counter()
    reason_counts = {key: Counter() for key in REQUIRED}
    missing_samples = {key: [] for key in REQUIRED}
    reason_examples = {key: [] for key in REQUIRED}
    source_gaps = {}

    def consume(row):
        nonlocal total, free_count, limited_count
        if not isinstance(row, dict):
            return None
        total += 1
        source = str(row.get("source") or "Unknown")
        if row.get("price_claim_type") == "access_limited_free":
            limited_count += 1
            limited_by_source[source] += 1
        if not row.get("is_free"):
            return None
        free_count += 1
        by_source[source] += 1
        by_photo_status[str((row.get("media") or {}).get("photo_status") or "missing_media")] += 1
        source_gaps.setdefault(source, {key: 0 for key in REQUIRED})
        for key, predicate in REQUIRED.items():
            if predicate(row):
                continue
            missing_counts[key] += 1
            source_gaps[source][key] += 1
            reason = gap_reason(row, key)
            reason_counts[key][reason] += 1
            if len(missing_samples[key]) < 10:
                item = sample([row], 1)[0]
                missing_samples[key].append(item)
                reason_examples[key].append({**item, "reason": reason})
        return None

    metadata = load_json_recordset(path, record_transform=consume, allow_root_array=True, strict_records=False)
    if "records" not in metadata:
        raise SystemExit(f"{path} is not a Volta intelligence record list")
    coverage, gap_reasons, failures = {}, {}, []
    for key in REQUIRED:
        missing = missing_counts[key]
        present = free_count - missing
        coverage[key] = {"present": present, "missing": missing,
                         "coverage_pct": round(present / free_count * 100 if free_count else 100.0, 2)}
        gap_reasons[key] = {"counts": dict(reason_counts[key].most_common()), "examples": reason_examples[key]}
        if strict and missing:
            failures.append(f"{key}: {missing} free records missing")
    return {"records": total, "free_records": free_count, "access_limited_free_records": limited_count,
            "access_limited_free_by_source": dict(limited_by_source.most_common()),
            "coverage": coverage, "by_source": dict(by_source.most_common()),
            "by_photo_status": dict(by_photo_status.most_common()), "source_gaps": source_gaps,
            "missing_samples": missing_samples, "gap_reasons": gap_reasons, "failures": failures}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/free_intelligence.json")
    parser.add_argument(
        "--data-label",
        help="Stable path label to record when auditing an equivalent temporary candidate.",
    )
    parser.add_argument("--out", default="qa/free-db-completeness-latest.json")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    path = Path(args.data)
    audit = audit_records(path, strict=args.strict)
    failures = audit["failures"]

    result = {
        "ok": not failures,
        "data": args.data_label or str(path),
        "data_sha256": sha256_file(path),
        **audit,
        "policy": {
            "no_guessing": "Missing fields remain explicit gaps; this audit does not count placeholders as facts.",
            "photo": "Photo coverage requires a real public media URL in media.photos.",
            "live": "Offline intelligence records may expose source integration/status fields, but live availability remains false unless the runtime live parser proves it.",
        },
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": result["ok"],
        "out": str(out),
        "free_records": audit["free_records"],
        "access_limited_free_records": audit["access_limited_free_records"],
        "coverage": audit["coverage"],
        "top_sources": dict(list(audit["by_source"].items())[:8]),
        "failures": failures,
    }, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
