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
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


UNKNOWN_CONNECTOR_LABELS = {"", "unknown", "unknown connector", "connector", "other", "n/a", "none"}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def load_records(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    records = raw.get("records") if isinstance(raw, dict) else raw
    if not isinstance(records, list):
        raise SystemExit(f"{path} is not a Volta intelligence record list")
    return [r for r in records if isinstance(r, dict)]


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
    records = load_records(path)
    free = [r for r in records if r.get("is_free")]
    access_limited_free = [r for r in records if r.get("price_claim_type") == "access_limited_free"]
    by_source = Counter(str(r.get("source") or "Unknown") for r in free)
    by_photo_status = Counter(str((r.get("media") or {}).get("photo_status") or "missing_media") for r in free)
    coverage: dict[str, Any] = {}
    missing_samples: dict[str, Any] = {}
    gap_reasons: dict[str, Any] = {}
    failures: list[str] = []

    for key, predicate in REQUIRED.items():
        missing = [r for r in free if not predicate(r)]
        present = len(free) - len(missing)
        coverage[key] = {
            "present": present,
            "missing": len(missing),
            "coverage_pct": round((present / len(free) * 100) if free else 100.0, 2),
        }
        missing_samples[key] = sample(missing)
        reason_counts = Counter(gap_reason(r, key) for r in missing)
        gap_reasons[key] = {
            "counts": dict(reason_counts.most_common()),
            "examples": [
                {
                    **item,
                    "reason": gap_reason(row, key),
                }
                for row, item in zip(missing[:10], sample(missing))
            ],
        }
        if args.strict and missing:
            failures.append(f"{key}: {len(missing)} free records missing")

    source_gaps: dict[str, dict[str, int]] = defaultdict(dict)
    for source in by_source:
        rows = [r for r in free if str(r.get("source") or "Unknown") == source]
        for key, predicate in REQUIRED.items():
            source_gaps[source][key] = sum(1 for r in rows if not predicate(r))

    result = {
        "ok": not failures,
        "data": args.data_label or str(path),
        "data_sha256": sha256_file(path),
        "records": len(records),
        "free_records": len(free),
        "access_limited_free_records": len(access_limited_free),
        "access_limited_free_by_source": dict(Counter(str(r.get("source") or "Unknown") for r in access_limited_free).most_common()),
        "coverage": coverage,
        "by_source": dict(by_source.most_common()),
        "by_photo_status": dict(by_photo_status.most_common()),
        "source_gaps": dict(source_gaps),
        "missing_samples": missing_samples,
        "gap_reasons": gap_reasons,
        "failures": failures,
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
        "free_records": len(free),
        "access_limited_free_records": len(access_limited_free),
        "coverage": coverage,
        "top_sources": dict(by_source.most_common(8)),
        "failures": failures,
    }, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
