"""Keep a charger that left its source, so one disappearance cannot block every source.

A baseline station missing from a new build moves to `retained_records`, beside `records`
and outside it. Everything that reads `records` (map, search, matrix, canary floors, reports)
therefore sees only rows the sources list now. Lookups by ID still resolve a retained row.

A retained row is the baseline row unchanged, plus a marker. The marker's `clock_since` is the
only field that can change: any published build that could not prove the absence restarts it,
and so does a long gap between published builds. A row retires automatically once `clock_since`
is GRACE_DAYS old, with no Git change: the delta gate proves each retirement from the baseline.

Some sources publish no stable identifier, so a rename changes the ID with nothing to link the
two rows. There, one new row within RENAME_RADIUS_M of one missing row in the same source
becomes its successor, and the old ID stays as an alias. A retained row of such a source with
any other row nearby never retires automatically: only a reviewed retirement can decide it.

The caps bound what a bad build can do. Above a cap the delta gate rejects the build.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from source_matrix import FUTURE_TOLERANCE, absence_proof, absence_unproven
from source_policy import partition_records, recordset_sha256, value_is_excluded
from station_identity import distance_m, identity_keys

RETAINED_KEY = "retained_records"
RETIRED_KEY = "auto_retired"
MARKER = "no_longer_listed"
GRACE_DAYS = 30
MAX_PROOF_GAP = timedelta(hours=48)
RENAME_RADIUS_M = 50


def build_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def per_run_cap(source_rows: int) -> int:
    """1% of a source, or up to 5 rows if that stays within 10% of a small source."""
    return max(math.ceil(source_rows * 0.01), min(5, math.ceil(source_rows * 0.10)))


def retained_cap(source_rows: int) -> int:
    """10% of a source, or up to 5 rows if that stays within 25% of a small source."""
    return max(math.ceil(source_rows * 0.10), min(5, math.ceil(source_rows * 0.25)))


def _source(row: Any) -> str:
    return str(row.get("source") or "Unknown")


def _keys(row: Any) -> Any:
    return row.identities if hasattr(row, "identities") else identity_keys(row)


def _near(a: Any, b: Any) -> bool:
    try:
        first, second = {"lat": a.get("lat"), "lon": a.get("lon")}, {"lat": b.get("lat"), "lon": b.get("lon")}
        return distance_m(first, second) <= RENAME_RADIUS_M
    except TypeError:
        return False


def keyless_renames(missing: Iterable[Any], rebuilt: Iterable[Any], baseline_ids: set) -> dict[str, str]:
    """Missing stations of keyless sources with exactly one possible successor, and it only theirs."""
    old_rows = [row for row in missing if not _keys(row)]
    if not old_rows:
        return {}
    new_rows = [row for row in rebuilt if str(row.get("id")) not in baseline_ids and not _keys(row)]
    options = {str(old.get("id")): [str(new.get("id")) for new in new_rows
                                    if _source(new) == _source(old) and _near(old, new)]
               for old in old_rows}
    claims = Counter(new_id for found in options.values() for new_id in found)
    return {old_id: found[0] for old_id, found in options.items() if len(found) == 1 and claims[found[0]] == 1}


def has_keyless_neighbour(row: dict, rebuilt: Iterable[Any]) -> bool:
    """A nearby row might be this station under a new name, so only a person can retire it."""
    return not _keys(row) and any(
        _source(other) == _source(row) and not _keys(other) and _near(row, other) for other in rebuilt)


def _expired(marker: Any, now: datetime) -> bool:
    since = build_time((marker or {}).get("clock_since"))
    return bool(since and now - since >= timedelta(days=GRACE_DAYS))


def _carried(row: dict, proof: dict, now: datetime, generated_at: str, previous_at: Any) -> dict:
    """The row as this build must carry it: its clock restarts when continuity is not proven."""
    previous = build_time(previous_at)
    if not absence_unproven(proof, row) and previous is not None and now - previous <= MAX_PROOF_GAP:
        return row
    return {**row, MARKER: {**row[MARKER], "clock_since": generated_at}}


def _identifiers(rows: Iterable[Any]) -> set[str]:
    found: set[str] = set()
    for row in rows:
        found.add(str(row.get("id") or ""))
        found.update(str(alias) for alias in row.get("merged_ids") or [])
    return found


def _ledger_entry(row: dict, retired_at: Any) -> dict:
    entry = {**row[MARKER], "id": row["id"], "retired_at": retired_at}
    if row.get("merged_ids"):
        entry["aliases"] = list(row["merged_ids"])
    return entry


def identity_baseline(baseline: dict) -> dict:
    """Baseline for ID preservation: a retained station that returns gets its old ID back."""
    return {"records": (baseline.get("records") or []) + (baseline.get(RETAINED_KEY) or [])}


def apply_retention(baseline: dict, candidate: dict, *, retirements: dict | None = None,
                    policy: dict | None = None) -> tuple[dict, dict]:
    """Return the candidate with missing baseline stations retained or retired, and a report.

    A row stays missing, so the refresh rejects the build, when the build has no usable time or
    absence is not proven where that row is: see `source_matrix.absence_unproven`.
    """
    now = build_time(candidate.get("generated_at"))
    if now is None:
        return candidate, {"changed": False, "skipped": "build has no usable generated_at"}
    records = candidate.get("records") or []
    prior = baseline.get(RETAINED_KEY) or []
    baseline_rows = baseline.get("records") or []
    baseline_ids = {str(row.get("id")) for row in baseline_rows + prior}
    fresh = _identifiers(records)

    renames = keyless_renames([row for row in baseline_rows + prior if str(row.get("id")) not in fresh],
                              records, baseline_ids)
    if renames:
        by_id = {str(row.get("id")): row for row in baseline_rows + prior}
        inherit: dict[str, list] = {}
        for old_id, new_id in renames.items():
            inherit.setdefault(new_id, []).extend([old_id, *(by_id[old_id].get("merged_ids") or [])])
        records = [{**row, "merged_ids": [*(row.get("merged_ids") or []), *inherit[str(row.get("id"))]]}
                   if str(row.get("id")) in inherit else row for row in records]
        fresh = _identifiers(records)

    proof = absence_proof(candidate, now)
    reviewed = retirements or {}
    fresh_ids = {str(row.get("id")) for row in records}

    def allowed(row: dict) -> bool:
        if policy is not None and not partition_records([row], policy)[0]:
            return False
        entry = reviewed.get(row.get("id"))
        # A reviewed retirement the gate can honor wins. An unmet successor retires nothing.
        return entry is None or (entry.get("successor_id") is not None and entry["successor_id"] not in fresh_ids)

    generated_at = candidate["generated_at"]
    keep = [_carried(row, proof, now, generated_at, baseline.get("generated_at"))
            for row in prior if str(row.get("id")) not in fresh and allowed(row)]
    returned = sorted(str(row.get("id")) for row in prior if str(row.get("id")) in fresh)
    new = []
    for row in baseline_rows:
        if str(row.get("id")) in fresh or absence_unproven(proof, row) or not allowed(row):
            continue
        new.append({**row, MARKER: {"source": _source(row), "last_listed_at": baseline.get("generated_at"),
                                    "first_absent_at": generated_at, "clock_since": generated_at}})

    # Oldest first, up to the per-run cap. The rest wait for a later build, so a large batch
    # that left together cannot retire together.
    source_rows = Counter(_source(row) for row in baseline_rows + prior)
    retiring: list[dict] = []
    needs_review: list[str] = []
    used: Counter = Counter()
    for row in sorted(keep, key=lambda item: str(item[MARKER].get("clock_since"))):
        if not _expired(row[MARKER], now):
            continue
        if has_keyless_neighbour(row, records):
            needs_review.append(str(row["id"]))
        elif used[_source(row)] < per_run_cap(source_rows[_source(row)]):
            used[_source(row)] += 1
            retiring.append(row)
    retiring_ids = {str(row["id"]) for row in retiring}
    retained = sorted([row for row in keep if str(row["id"]) not in retiring_ids] + new, key=lambda r: str(r["id"]))
    ledger = [entry for entry in baseline.get(RETIRED_KEY) or [] if str(entry.get("id")) not in fresh]
    ledger += [_ledger_entry(row, generated_at) for row in retiring]

    result = dict(candidate)
    if renames:
        result["records"] = records
        if "recordset_sha256" in result:
            result["recordset_sha256"] = recordset_sha256(records)
    for key, value in ((RETAINED_KEY, retained), (RETIRED_KEY, ledger)):
        result.pop(key, None)
        if value:
            result[key] = value
    report = {
        "changed": bool(retained or ledger or renames),
        "new": sorted(str(row["id"]) for row in new),
        "retained": len(retained),
        "returned": returned,
        "keyless_renames": renames,
        "auto_retired": sorted(retiring_ids),
        "needs_review": sorted(needs_review),
        "unproven_sources": sorted(proof["columns"]),
        "failed_areas": sum(len(areas) for areas in proof["areas"].values()),
        "grace_days": GRACE_DAYS,
    }
    return result, report


def validate(baseline_meta: dict, captured: dict[str, dict], source_rows: Counter, rebuilt_meta: dict,
             rebuilt_rows: list, policy: dict, real_now: datetime | None = None) -> dict:
    """Check the build's retained rows and retirements against the baseline, independently.

    `captured` holds the full baseline rows whose IDs the build claims to retain.
    Returns failures, per-source counts and the proven retirements for the ID comparison.
    """
    failures: list[str] = []
    rows = rebuilt_meta.get(RETAINED_KEY) or []
    ledger = rebuilt_meta.get(RETIRED_KEY) or []
    prior = {str(row.get("id")): row for row in baseline_meta.get(RETAINED_KEY) or []}
    prior_ledger = {str(entry.get("id")): entry for entry in baseline_meta.get(RETIRED_KEY) or []}
    generated_at = rebuilt_meta.get("generated_at")
    now = build_time(generated_at)
    if now is not None and now > (real_now or datetime.now(timezone.utc)) + FUTURE_TOLERANCE:
        # A build's own clock decides every age here, so it must not run ahead of real time.
        failures.append(f"build time {generated_at} is in the future")
    if not isinstance(rows, list) or not isinstance(ledger, list):
        failures.append("retained rows or retirements are not lists")
        rows, ledger = [], []
    baseline_time = build_time(baseline_meta.get("generated_at"))
    if (rows or ledger) and now is not None and baseline_time is not None and now < baseline_time:
        failures.append(f"build time {generated_at} is before the baseline")
    proof = absence_proof(rebuilt_meta, now or datetime.now(timezone.utc))
    identifiers = _identifiers(rebuilt_rows)
    new_by_source: Counter = Counter()
    by_source: Counter = Counter()
    seen: set[str] = set()
    base = source_rows + Counter(_source(row) for row in prior.values())
    for row in rows:
        row_id = str(row.get("id") or "")
        if row_id in seen or row_id in identifiers:
            failures.append(f"retained row duplicates a station: {row_id}")
        seen.add(row_id)
        by_source[_source(row)] += 1
        if value_is_excluded(row, policy):
            failures.append(f"retained row is excluded by source policy: {row_id}")
        marker = row.get(MARKER)
        if now is None:
            failures.append(f"retained row has no usable build time: {row_id}")
            continue
        if row_id in prior:
            if row != _carried(prior[row_id], proof, now, generated_at, baseline_meta.get("generated_at")):
                failures.append(f"retained row changed since the baseline: {row_id}")
            continue
        original = captured.get(row_id)
        if original is None:
            failures.append(f"retained row is not in the baseline: {row_id}")
            continue
        new_by_source[_source(row)] += 1
        if {key: value for key, value in row.items() if key != MARKER} != original:
            failures.append(f"retained row differs from its baseline row: {row_id}")
        if marker != {"source": _source(original), "last_listed_at": baseline_meta.get("generated_at"),
                      "first_absent_at": generated_at, "clock_since": generated_at}:
            failures.append(f"retained row has an unproven marker: {row_id}")
        if absence_unproven(proof, original):
            failures.append(f"absence is not proven for a retained row: {row_id}")
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
        if (row is None or now is None or entry_id in seen or entry_id in identifiers
                or absence_unproven(proof, row)
                or not _expired(_carried(row, proof, now, generated_at, baseline_meta.get("generated_at"))[MARKER], now)
                or has_keyless_neighbour(row, rebuilt_rows)
                or entry != _ledger_entry(row, generated_at)):
            failures.append(f"automatic retirement is not proven: {entry_id}")
            continue
        retired_by_source[_source(row)] += 1
        for identifier in [entry_id, *(row.get("merged_ids") or [])]:
            retired[str(identifier)] = {"id": str(identifier)}
    for source, count in sorted(retired_by_source.items()):
        if count > per_run_cap(base[source]):
            failures.append(f"{source}: {count} automatic retirements in one build, above the cap {per_run_cap(base[source])}")
    # A returned station leaves the record, as a row or an alias. Otherwise the record is permanent.
    lost = sorted(set(prior_ledger) - ledger_ids - identifiers)
    if lost:
        failures.append(f"retirement records were dropped: {lost[:10]}")
    return {"failures": failures, "retired": retired, "new": sum(new_by_source.values()), "retained": len(rows),
            "auto_retired": sum(retired_by_source.values()), "new_by_source": dict(new_by_source),
            "retained_by_source": dict(by_source)}
