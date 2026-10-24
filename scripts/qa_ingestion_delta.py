#!/usr/bin/env python3
"""Compare a rebuilt intelligence DB with the committed/current baseline."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from source_policy import load_source_policy_exclusions, partition_records
from station_identity import identity_keys
from station_retirements import RetirementError, load_retirements
from json_recordset import load_json_recordset


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "data" / "free_intelligence.json"
POLICY_EXCLUSIONS = ROOT / "data" / "source_policy_exclusions.json"
RETIREMENTS = ROOT / "data" / "station_id_retirements.json"
BAD_STATION_NAME_RE = re.compile(r"\b(?:n\s*['’]?\s*existe\s+pas|nexiste\s+pas|no\s+existe|does\s+not\s+exist|nonexistent|removed|inexistant|supprim[ée]e?|d[ée]mont[ée]e?|demontee|doublon|duplicate)\b", re.I)
CORROBORATION_ONLY_SOURCE_REQUIREMENTS = {
    "Madrid public charger open data": ("source_madrid_official", 100),
    "Zaragoza municipal open data": ("source_zaragoza_official", 30),
}


@dataclass(frozen=True, slots=True)
class GateRecord:
    """Exact gate evidence, without retaining tariff/media/raw station trees."""
    id: str
    source: str
    reliability: str
    parking_kind: str
    identities: frozenset
    merged_ids: tuple
    missing_fields: tuple
    missing_source: bool
    weak_consum: bool
    bad_actionable: bool
    unpromoted_timestamp: bool

    def get(self, key: str, default: Any = None) -> Any:
        if key == "parking":
            return {"kind": self.parking_kind}
        return getattr(self, key, default)


def compact_gate_record(row: dict) -> GateRecord:
    """Evaluate every validation against the complete, policy-clean input row.

    Stable source identities are computed before dropping nested evidence, so
    merge proof stays identical even for carried or typed registry identifiers.
    Only fields used by the report survive; imported cached verdicts are ignored.
    """
    missing = missing_required_fields([row])
    return GateRecord(
        record_id(row), sys.intern(str(row.get("source") or "Unknown")),
        sys.intern(str(row.get("reliability") or "Unknown")),
        sys.intern(str((row.get("parking") or {}).get("kind") or "unknown")),
        frozenset(identity_keys(row)), tuple(row.get("merged_ids") or []),
        tuple(missing[0][1]) if missing else (), bool(missing_source_links([row])),
        bool(weak_consum_source_rows([row])), bool(bad_actionable_name_rows([row])),
        bool(unpromoted_source_timestamp_rows([row])),
    )


def load_partitioned(path: Path, policy: dict) -> tuple[dict, list, list]:
    """Stream policy evaluation and retain only compact, exact gate evidence."""
    excluded = []

    def clean(row: dict) -> GateRecord | None:
        allowed, blocked = partition_records([row], policy)
        excluded.extend({"id": record_id(item)} for item in blocked)
        return compact_gate_record(allowed[0]) if allowed else None

    payload = load_json_recordset(path, record_transform=clean)
    return payload, records(payload), excluded


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
        if isinstance(row, GateRecord):
            if row.missing_source:
                missing.append(row.id)
            continue
        sources = row.get("sources") or []
        has_source = bool(row.get("source_url")) or any(isinstance(src, dict) and src.get("url") for src in sources)
        if not has_source:
            missing.append(record_id(row))
    return missing


def missing_required_fields(rows: Iterable[Dict[str, Any]]) -> List[Tuple[str, List[str]]]:
    required = ["id", "name", "lat", "lon", "source", "sources", "parking", "reliability"]
    out: List[Tuple[str, List[str]]] = []
    for row in rows:
        if isinstance(row, GateRecord):
            if row.missing_fields:
                out.append((row.id, list(row.missing_fields)))
            continue
        missing = [key for key in required if row.get(key) in (None, "", [], {})]
        if missing:
            out.append((record_id(row), missing))
    return out


def weak_consum_source_rows(rows: Iterable[Dict[str, Any]]) -> List[str]:
    weak: List[str] = []
    for row in rows:
        if isinstance(row, GateRecord):
            if row.weak_consum:
                weak.append(row.id)
            continue
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
        if isinstance(row, GateRecord):
            if row.bad_actionable:
                bad.append(row.id)
            continue
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
        if isinstance(row, GateRecord):
            if row.unpromoted_timestamp:
                bad.append(row.id)
            continue
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
        old_keys = old.identities if isinstance(old, GateRecord) else identity_keys(old) if old is not None else set()
        new_keys = (rows[0].identities if isinstance(rows[0], GateRecord) else identity_keys(rows[0])) if len(rows) == 1 else set()
        if len(rows) == 1 and old is not None and old_keys & new_keys:
            proven[old_id] = record_id(rows[0])
    return proven


def honored_retirements(
    unmerged: Iterable[str],
    retirements: Dict[str, Dict[str, Any]],
    baseline_ids: set,
    rebuilt_ids: set,
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, str]]]:
    """Split reviewed retirements into those the data still supports and those it does not.

    An entry with a successor counts only when that successor is in the rebuilt data and
    is new, so a retirement cannot hide a loss behind a station the baseline already had.
    """
    honored: Dict[str, Dict[str, Any]] = {}
    unmet: List[Dict[str, str]] = []
    for old_id in unmerged:
        entry = retirements.get(old_id)
        if entry is None:
            continue
        successor = entry.get("successor_id")
        if successor is None:
            honored[old_id] = entry
        elif successor not in rebuilt_ids:
            unmet.append({"id": old_id, "reason": "successor_id is not in the rebuilt data"})
        elif successor in baseline_ids:
            unmet.append({"id": old_id, "reason": "successor_id already exists in the baseline"})
        else:
            honored[old_id] = entry
    return honored, unmet


def id_delta(
    baseline_rows: List[Dict[str, Any]],
    rebuilt_rows: List[Dict[str, Any]],
    retirements: Dict[str, Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    retirements = retirements or {}
    baseline_ids = {record_id(row) for row in baseline_rows if record_id(row)}
    rebuilt_ids = {record_id(row) for row in rebuilt_rows if record_id(row)}
    gone = sorted(baseline_ids - rebuilt_ids)
    merged = merged_into(baseline_rows, rebuilt_rows, gone)
    unmerged = [old_id for old_id in gone if old_id not in merged]
    retired, retirements_unmet = honored_retirements(unmerged, retirements, baseline_ids, rebuilt_ids)
    missing = [old_id for old_id in unmerged if old_id not in retired]
    added = sorted(rebuilt_ids - baseline_ids)
    return {
        "baseline_ids": len(baseline_ids),
        "rebuilt_ids": len(rebuilt_ids),
        "missing_ids": len(missing),
        "merged_ids": len(merged),
        "retired_ids": len(retired),
        "added_ids": len(added),
        "missing_sample": missing[:10],
        "merged_sample": [{"id": old_id, "into": merged[old_id]} for old_id in sorted(merged)[:10]],
        "retired_sample": [
            {"id": old_id, "successor_id": retired[old_id].get("successor_id")} for old_id in sorted(retired)[:10]
        ],
        "retirements_unmet": retirements_unmet,
        "retirements_stale": sorted(set(retirements) - set(gone)),
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
    parser.add_argument("--retirements", type=Path, default=RETIREMENTS,
                        help="reviewed station-ID retirements; a path given here must exist")
    args = parser.parse_args()

    failures: List[str] = []
    try:
        retirements = load_retirements(args.retirements, required=args.retirements != RETIREMENTS)
    except RetirementError as exc:
        # A broken list must not silently turn into "retire nothing": reject the candidate.
        retirements = {}
        failures.append(f"station retirement list rejected: {exc}")
    policy = load_source_policy_exclusions(args.policy_exclusions)
    baseline_payload, baseline_rows, baseline_excluded = load_partitioned(args.baseline, policy)
    rebuilt_payload, rebuilt_rows, rebuilt_excluded = load_partitioned(args.rebuilt, policy)

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

    delta = id_delta(baseline_rows, rebuilt_rows, retirements)
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
            "pre_policy_records": len(baseline_rows) + len(baseline_excluded),
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
            "pre_policy_records": len(rebuilt_rows) + len(rebuilt_excluded),
            "raw_records": rebuilt_payload.get("raw_records"),
            "errors": len(rebuilt_payload.get("errors") or []),
            "sources": rebuilt_sources.most_common(),
            "reliability": reliability_names(rebuilt_rows).most_common(),
            "parking": parking_names(rebuilt_rows).most_common(),
        },
        "delta": delta,
        "retirements": {"path": str(args.retirements), "entries": len(retirements)},
        "corroboration_only_source_losses": corroboration_only_losses,
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
