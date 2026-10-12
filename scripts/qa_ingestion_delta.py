#!/usr/bin/env python3
"""Compare a rebuilt intelligence DB with the committed/current baseline."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from source_policy import load_source_policy_exclusions, partition_records
from station_identity import identity_keys


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "data" / "free_intelligence.json"
POLICY_EXCLUSIONS = ROOT / "data" / "source_policy_exclusions.json"
BAD_STATION_NAME_RE = re.compile(r"\b(?:n\s*['’]?\s*existe\s+pas|nexiste\s+pas|no\s+existe|does\s+not\s+exist|nonexistent|removed|inexistant|supprim[ée]e?|d[ée]mont[ée]e?|demontee|doublon|duplicate)\b", re.I)
CORROBORATION_ONLY_SOURCE_REQUIREMENTS = {
    "Madrid public charger open data": ("source_madrid_official", 100),
    "Zaragoza municipal open data": ("source_zaragoza_official", 30),
}


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def records(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = payload.get("records")
    if not isinstance(rows, list):
        raise AssertionError("payload.records is not a list")
    return rows


def record_id(row: Dict[str, Any]) -> str:
    return str(row.get("id") or "")


def source_names(rows: Iterable[Dict[str, Any]]) -> Counter[str]:
    return Counter(str(row.get("source") or "Unknown") for row in rows)


def reliability_names(rows: Iterable[Dict[str, Any]]) -> Counter[str]:
    return Counter(str(row.get("reliability") or "Unknown") for row in rows)


def parking_names(rows: Iterable[Dict[str, Any]]) -> Counter[str]:
    return Counter(str(((row.get("parking") or {}).get("kind")) or "unknown") for row in rows)


def missing_source_links(rows: Iterable[Dict[str, Any]]) -> List[str]:
    missing: List[str] = []
    for row in rows:
        sources = row.get("sources") or []
        has_source = bool(row.get("source_url")) or any(isinstance(src, dict) and src.get("url") for src in sources)
        if not has_source:
            missing.append(record_id(row))
    return missing


def missing_required_fields(rows: Iterable[Dict[str, Any]]) -> List[Tuple[str, List[str]]]:
    required = ["id", "name", "lat", "lon", "source", "sources", "parking", "reliability"]
    out: List[Tuple[str, List[str]]] = []
    for row in rows:
        missing = [key for key in required if row.get(key) in (None, "", [], {})]
        if missing:
            out.append((record_id(row), missing))
    return out


def weak_consum_source_rows(rows: Iterable[Dict[str, Any]]) -> List[str]:
    weak: List[str] = []
    for row in rows:
        if row.get("source") != "Consum store locator":
            continue
        source_url = str(row.get("source_url") or "")
        name = str(row.get("name") or "")
        if re.search(r"/supermercados/valencia/(?:\d+/)?$", source_url) or "Consum supermarket charger" in name:
            weak.append(record_id(row))
    return weak


def bad_actionable_name_rows(rows: Iterable[Dict[str, Any]]) -> List[str]:
    bad: List[str] = []
    for row in rows:
        if not (row.get("is_free") or row.get("is_cheap") or row.get("status") == "AVAILABLE"):
            continue
        sources = row.get("sources") or []
        text = " ".join(
            [str(row.get("name") or ""), str(row.get("insight") or ""), str(row.get("free_condition") or "")]
            + [str(src.get("evidence") or "") for src in sources if isinstance(src, dict)]
        )
        if BAD_STATION_NAME_RE.search(text):
            bad.append(record_id(row))
    return bad


def unpromoted_source_timestamp_rows(rows: Iterable[Dict[str, Any]]) -> List[str]:
    bad: List[str] = []
    for row in rows:
        raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
        raw_ts = raw.get("LastVerified") or raw.get("DateLastStatusUpdate") or raw.get("DateLastConfirmed") or raw.get("last_seen") or raw.get("lastSeen") or raw.get("last_used_time") or raw.get("lastUsedTime") or raw.get("updated_at") or raw.get("updatedAt") or raw.get("creation_date")
        if raw_ts and not row.get("last_seen"):
            bad.append(record_id(row))
    return bad


def merged_into(baseline_rows: List[Dict[str, Any]], rebuilt_rows: List[Dict[str, Any]], missing: Iterable[str]) -> Dict[str, str]:
    """Map a missing baseline id to the rebuilt pin that absorbed it.

    A merge counts only when exactly one rebuilt pin lists the id in `merged_ids` AND
    shares a stable source identifier with the old row (the same OCM/OSM/registry id).
    A listed id alone is a claim, not proof, so anything else stays missing.
    """
    baseline_by_id = {record_id(row): row for row in baseline_rows if record_id(row)}
    claims: Dict[str, List[Dict[str, Any]]] = {}
    for row in rebuilt_rows:
        for old_id in row.get("merged_ids") or []:
            claims.setdefault(str(old_id), []).append(row)
    proven: Dict[str, str] = {}
    for old_id in missing:
        rows = claims.get(old_id) or []
        old = baseline_by_id.get(old_id)
        if len(rows) == 1 and old is not None and identity_keys(old) & identity_keys(rows[0]):
            proven[old_id] = record_id(rows[0])
    return proven


def id_delta(baseline_rows: List[Dict[str, Any]], rebuilt_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    baseline_ids = {record_id(row) for row in baseline_rows if record_id(row)}
    rebuilt_ids = {record_id(row) for row in rebuilt_rows if record_id(row)}
    gone = sorted(baseline_ids - rebuilt_ids)
    merged = merged_into(baseline_rows, rebuilt_rows, gone)
    missing = [old_id for old_id in gone if old_id not in merged]
    added = sorted(rebuilt_ids - baseline_ids)
    return {
        "baseline_ids": len(baseline_ids),
        "rebuilt_ids": len(rebuilt_ids),
        "missing_ids": len(missing),
        "merged_ids": len(merged),
        "added_ids": len(added),
        "missing_sample": missing[:10],
        "merged_sample": [{"id": old_id, "into": merged[old_id]} for old_id in sorted(merged)[:10]],
        "added_sample": added[:10],
    }


def corroboration_only_source_loss_is_proven(
    source: str,
    baseline_rows: Iterable[Dict[str, Any]],
    rebuilt_payload: Dict[str, Any],
) -> bool:
    requirement = CORROBORATION_ONLY_SOURCE_REQUIREMENTS.get(source)
    if requirement is None or rebuilt_payload.get("errors"):
        return False
    source_rows = [row for row in baseline_rows if row.get("source") == source]
    if not source_rows or any(
        row.get("reliability") != "official-charger-location-price-unknown"
        for row in source_rows
    ):
        return False
    adapter_name, minimum_count = requirement
    counts = rebuilt_payload.get("source_adapter_counts")
    return bool(
        isinstance(counts, dict)
        and isinstance(counts.get(adapter_name), int)
        and counts[adapter_name] >= minimum_count
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and compare a rebuilt Volta intelligence DB.")
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--rebuilt", type=Path, required=True)
    parser.add_argument("--min-total-ratio", type=float, default=0.9)
    parser.add_argument("--max-missing-id-ratio", type=float, default=0.08)
    parser.add_argument("--policy-exclusions", type=Path, default=POLICY_EXCLUSIONS)
    args = parser.parse_args()

    failures: List[str] = []
    baseline_payload = load_json(args.baseline)
    rebuilt_payload = load_json(args.rebuilt)
    baseline_rows_all = records(baseline_payload)
    rebuilt_rows_all = records(rebuilt_payload)
    policy = load_source_policy_exclusions(args.policy_exclusions)
    baseline_rows, baseline_excluded = partition_records(baseline_rows_all, policy)
    rebuilt_rows, rebuilt_excluded = partition_records(rebuilt_rows_all, policy)

    if rebuilt_excluded:
        failures.append(
            f"rebuilt contains {len(rebuilt_excluded)} excluded source-policy records: "
            f"{sorted(record_id(row) for row in rebuilt_excluded)[:10]}"
        )

    baseline_total = len(baseline_rows)
    rebuilt_total = len(rebuilt_rows)
    if rebuilt_payload.get("errors"):
        failures.append(f"rebuilt payload has errors: {rebuilt_payload.get('errors')}")
    if baseline_total == 0:
        failures.append("baseline has no records")
    min_total = int(baseline_total * args.min_total_ratio)
    if rebuilt_total < min_total:
        failures.append(f"rebuilt record count {rebuilt_total} below minimum {min_total} ({args.min_total_ratio:.0%} of baseline)")

    required_missing = missing_required_fields(rebuilt_rows)
    if required_missing:
        failures.append(f"rebuilt rows missing required fields: {required_missing[:10]}")
    source_missing = missing_source_links(rebuilt_rows)
    if source_missing:
        failures.append(f"rebuilt rows missing source links: {source_missing[:10]}")
    weak_consum = weak_consum_source_rows(rebuilt_rows)
    if weak_consum:
        failures.append(f"Consum rows still use generic listing/source names: {weak_consum[:10]}")
    bad_actionable_names = bad_actionable_name_rows(rebuilt_rows)
    if bad_actionable_names:
        failures.append(f"Actionable rows still contain removed/duplicate/nonexistent name markers: {bad_actionable_names[:10]}")
    unpromoted_timestamps = unpromoted_source_timestamp_rows(rebuilt_rows)
    if unpromoted_timestamps:
        failures.append(f"Rows have raw source timestamps but missing top-level last_seen: {unpromoted_timestamps[:10]}")

    delta = id_delta(baseline_rows, rebuilt_rows)
    max_missing = int(max(1, delta["baseline_ids"] * args.max_missing_id_ratio))
    if delta["missing_ids"] > max_missing:
        failures.append(f"rebuilt missing {delta['missing_ids']} baseline ids, above allowed {max_missing}")

    baseline_sources = source_names(baseline_rows)
    rebuilt_sources = source_names(rebuilt_rows)
    missing_source_names = sorted(set(baseline_sources) - set(rebuilt_sources))
    corroboration_only_losses = [
        source for source in missing_source_names
        if corroboration_only_source_loss_is_proven(source, baseline_rows, rebuilt_payload)
    ]
    unexplained_missing_sources = sorted(set(missing_source_names) - set(corroboration_only_losses))
    if unexplained_missing_sources:
        failures.append(f"rebuilt lost entire source families: {unexplained_missing_sources}")

    result = {
        "ok": not failures,
        "policy_exclusions": {
            "path": str(args.policy_exclusions),
            "ids": [str(item.get("id")) for item in policy.get("exclusions") or []],
            "baseline": {
                "records": len(baseline_excluded),
                "ids": sorted(record_id(row) for row in baseline_excluded),
            },
            "rebuilt": {
                "records": len(rebuilt_excluded),
                "ids": sorted(record_id(row) for row in rebuilt_excluded),
            },
        },
        "baseline": {
            "path": str(args.baseline),
            "generated_at": baseline_payload.get("generated_at"),
            "records": baseline_total,
            "pre_policy_records": len(baseline_rows_all),
            "raw_records": baseline_payload.get("raw_records"),
            "errors": len(baseline_payload.get("errors") or []),
            "sources": baseline_sources.most_common(),
            "reliability": reliability_names(baseline_rows).most_common(),
            "parking": parking_names(baseline_rows).most_common(),
        },
        "rebuilt": {
            "path": str(args.rebuilt),
            "generated_at": rebuilt_payload.get("generated_at"),
            "records": rebuilt_total,
            "pre_policy_records": len(rebuilt_rows_all),
            "raw_records": rebuilt_payload.get("raw_records"),
            "errors": len(rebuilt_payload.get("errors") or []),
            "sources": rebuilt_sources.most_common(),
            "reliability": reliability_names(rebuilt_rows).most_common(),
            "parking": parking_names(rebuilt_rows).most_common(),
        },
        "delta": delta,
        "corroboration_only_source_losses": corroboration_only_losses,
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
