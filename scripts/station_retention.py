"""Keep a charger that left its source, so one disappearance cannot block every source.

A baseline station missing from a new build moves to `retained_records`, beside `records`
and outside it. Everything that reads `records` (map, search, matrix, canary floors, reports)
therefore sees only rows the sources list now. Lookups by ID still resolve a retained row.

A retained row is the baseline row unchanged, plus a marker with two build times. A row
absent for GRACE_DAYS without a break retires automatically and is noted in `auto_retired`,
with no Git change: the delta gate proves each retirement from the baseline's own marker.

The caps bound what a bad build can do. Above a cap the delta gate rejects the build, which
is what happens to every missing station without this module.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from source_matrix import column_for, unproven_columns
from source_policy import partition_records, value_is_excluded

RETAINED_KEY = "retained_records"
RETIRED_KEY = "auto_retired"
MARKER = "no_longer_listed"
GRACE_DAYS = 30
NEW_PER_RUN_RATIO, NEW_PER_RUN_MIN = 0.01, 5
RETAINED_RATIO, RETAINED_MIN = 0.10, 5


def build_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def per_run_cap(source_rows: int) -> int:
    return max(NEW_PER_RUN_MIN, math.ceil(source_rows * NEW_PER_RUN_RATIO))


def retained_cap(source_rows: int) -> int:
    return max(RETAINED_MIN, math.ceil(source_rows * RETAINED_RATIO))


def _source(row: dict) -> str:
    return str(row.get("source") or "Unknown")


def _unproven(row: dict, unproven: set[str]) -> bool:
    return column_for(row.get("source")) in unproven


def _expired(marker: Any, now: datetime) -> bool:
    absent = build_time((marker or {}).get("first_absent_at"))
    return bool(absent and now - absent >= timedelta(days=GRACE_DAYS))


def _identifiers(rows: Iterable[dict]) -> set[str]:
    found: set[str] = set()
    for row in rows:
        found.add(str(row.get("id") or ""))
        found.update(str(alias) for alias in row.get("merged_ids") or [])
    return found


def identity_baseline(baseline: dict) -> dict:
    """Baseline for ID preservation: a retained station that returns gets its old ID back."""
    return {"records": (baseline.get("records") or []) + (baseline.get(RETAINED_KEY) or [])}


def apply_retention(baseline: dict, candidate: dict, *, retirements: dict | None = None,
                    policy: dict | None = None) -> tuple[dict, dict]:
    """Return the candidate with missing baseline stations retained or retired, and a report.

    A row stays missing, so the delta gate rejects the build, when the build has no usable
    time or its source failed or fell back to a cached copy: absence is then not proven.
    """
    now = build_time(candidate.get("generated_at"))
    if now is None:
        return candidate, {"changed": False, "skipped": "build has no usable generated_at"}
    fresh = _identifiers(candidate.get("records") or [])
    unproven = unproven_columns(candidate)
    prior = baseline.get(RETAINED_KEY) or []
    reviewed = retirements or {}
    fresh_ids = {str(row.get("id")) for row in candidate.get("records") or []}

    def allowed(row: dict) -> bool:
        if policy is not None and not partition_records([row], policy)[0]:
            return False
        entry = reviewed.get(row.get("id"))
        # A reviewed retirement the gate can honor wins. An unmet successor retires nothing.
        return entry is None or entry.get("successor_id") not in (None, *fresh_ids)

    keep = [row for row in prior if str(row.get("id")) not in fresh and allowed(row)]
    returned = sorted(str(row.get("id")) for row in prior if str(row.get("id")) in fresh)
    new = []
    for row in baseline.get("records") or []:
        if str(row.get("id")) in fresh or _unproven(row, unproven) or not allowed(row):
            continue
        new.append({**row, MARKER: {"source": _source(row), "last_listed_at": baseline.get("generated_at"),
                                    "first_absent_at": candidate["generated_at"]}})

    # Oldest first, up to the per-run cap. The rest wait for a later build, so a large batch
    # that left together cannot retire together.
    source_rows = Counter(_source(row) for row in (baseline.get("records") or []) + prior)
    retiring: list[dict] = []
    used: Counter = Counter()
    for row in sorted(keep, key=lambda item: str(item[MARKER].get("first_absent_at"))):
        if (_expired(row[MARKER], now) and not _unproven(row, unproven)
                and used[_source(row)] < per_run_cap(source_rows[_source(row)])):
            used[_source(row)] += 1
            retiring.append(row)
    retiring_ids = {str(row["id"]) for row in retiring}
    retained = sorted([row for row in keep if str(row["id"]) not in retiring_ids] + new, key=lambda r: str(r["id"]))
    ledger = [entry for entry in baseline.get(RETIRED_KEY) or [] if str(entry.get("id")) not in fresh]
    ledger += [{**row[MARKER], "id": row["id"], "retired_at": candidate["generated_at"]} for row in retiring]

    result = dict(candidate)
    for key, value in ((RETAINED_KEY, retained), (RETIRED_KEY, ledger)):
        result.pop(key, None)
        if value:
            result[key] = value
    report = {
        "changed": bool(retained or ledger),
        "new": sorted(str(row["id"]) for row in new),
        "retained": len(retained),
        "returned": returned,
        "auto_retired": sorted(retiring_ids),
        "unproven_sources": sorted(unproven),
        "grace_days": GRACE_DAYS,
    }
    return result, report


def validate(baseline_meta: dict, captured: dict[str, dict], source_rows: Counter, rebuilt_meta: dict,
             rebuilt_ids: set[str], policy: dict) -> dict:
    """Check the build's retained rows and retirements against the baseline, independently.

    `captured` holds the full baseline rows whose IDs the build claims to retain.
    Returns failures, per-source counts and the proven retirements for the ID comparison.
    """
    failures: list[str] = []
    rows = rebuilt_meta.get(RETAINED_KEY) or []
    ledger = rebuilt_meta.get(RETIRED_KEY) or []
    prior = {str(row.get("id")): row for row in baseline_meta.get(RETAINED_KEY) or []}
    prior_ledger = {str(entry.get("id")): entry for entry in baseline_meta.get(RETIRED_KEY) or []}
    if not isinstance(rows, list) or not isinstance(ledger, list):
        return {"failures": ["retained rows or retirements are not lists"], "retired": {}, "new": 0, "retained": 0,
                "auto_retired": 0, "new_by_source": {}, "retained_by_source": {}}
    now = build_time(rebuilt_meta.get("generated_at"))
    unproven = unproven_columns(rebuilt_meta)
    new_by_source: Counter = Counter()
    by_source: Counter = Counter()
    seen: set[str] = set()
    base = source_rows + Counter(_source(row) for row in prior.values())
    for row in rows:
        row_id = str(row.get("id") or "")
        if row_id in seen or row_id in rebuilt_ids:
            failures.append(f"retained row duplicates a station: {row_id}")
        seen.add(row_id)
        by_source[_source(row)] += 1
        if value_is_excluded(row, policy):
            failures.append(f"retained row is excluded by source policy: {row_id}")
        marker = row.get(MARKER)
        if row_id in prior:
            if row != prior[row_id]:
                failures.append(f"retained row changed since the baseline: {row_id}")
            continue
        original = captured.get(row_id)
        if original is None:
            failures.append(f"retained row is not in the baseline: {row_id}")
            continue
        new_by_source[_source(row)] += 1
        if {key: value for key, value in row.items() if key != MARKER} != original:
            failures.append(f"retained row differs from its baseline row: {row_id}")
        if (not isinstance(marker, dict) or marker.get("first_absent_at") != rebuilt_meta.get("generated_at")
                or marker.get("last_listed_at") != baseline_meta.get("generated_at") or now is None):
            failures.append(f"retained row has an unproven marker: {row_id}")
        if _unproven(row, unproven):
            failures.append(f"retained row's source failed in this build, so absence is not proven: {row_id}")
    for source, count in sorted(new_by_source.items()):
        if count > per_run_cap(base[source]):
            failures.append(f"{source}: {count} stations left in one build, above the cap {per_run_cap(base[source])}")
    for source, count in sorted(by_source.items()):
        if count > retained_cap(base[source]):
            failures.append(f"{source}: {count} retained stations, above the cap {retained_cap(base[source])}")

    retired: dict[str, dict] = {}
    retired_by_source: Counter = Counter()
    ledger_ids = set()
    for entry in ledger:
        entry_id = str(entry.get("id") or "")
        ledger_ids.add(entry_id)
        if entry_id in prior_ledger:
            if entry != prior_ledger[entry_id]:
                failures.append(f"retirement record changed: {entry_id}")
            continue
        row = prior.get(entry_id)
        marker = (row or {}).get(MARKER)
        if (row is None or now is None or not _expired(marker, now) or _unproven(row, unproven)
                or entry_id in seen or entry_id in rebuilt_ids
                or entry != {**marker, "id": entry_id, "retired_at": rebuilt_meta.get("generated_at")}):
            failures.append(f"automatic retirement is not proven: {entry_id}")
            continue
        retired_by_source[_source(row)] += 1
        for identifier in [entry_id, *(row.get("merged_ids") or [])]:
            retired[str(identifier)] = {"id": str(identifier)}
    for source, count in sorted(retired_by_source.items()):
        if count > per_run_cap(base[source]):
            failures.append(f"{source}: {count} automatic retirements in one build, above the cap {per_run_cap(base[source])}")
    # A returned station leaves the record. Otherwise the record of a retirement is permanent.
    lost = sorted(set(prior_ledger) - ledger_ids - rebuilt_ids)
    if lost:
        failures.append(f"retirement records were dropped: {lost[:10]}")
    return {"failures": failures, "retired": retired, "new": sum(new_by_source.values()), "retained": len(rows),
            "auto_retired": sum(retired_by_source.values()), "new_by_source": dict(new_by_source),
            "retained_by_source": dict(by_source)}
