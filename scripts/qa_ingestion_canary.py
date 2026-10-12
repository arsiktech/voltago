#!/usr/bin/env python3
"""Run a non-destructive public-source ingestion canary.

The canary rebuilds the intelligence DB into /tmp (or audits an exact existing
candidate), runs the existing delta audit against the canonical DB, and appends
a compact history entry. It never replaces data/free_intelligence.json.
"""
from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import hashlib
import json
import subprocess
import sys
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from source_policy import (
    load_source_policy_exclusions,
    source_family_is_excluded,
    value_is_excluded,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = Path("/tmp/volta-ingestion-canary.json")
DEFAULT_HISTORY = ROOT / "qa" / "ingestion-canary-history.jsonl"
DEFAULT_POLICY = ROOT / "data" / "source_policy_exclusions.json"
SOURCE_POLICY_EXCLUSIONS = load_source_policy_exclusions(DEFAULT_POLICY)


def run_json_command(cmd: List[str]) -> Dict[str, Any]:
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_last_json_object(text: str) -> Dict[str, Any]:
    start = text.rfind("\n{")
    if start >= 0:
        start += 1
    else:
        start = text.find("{")
    if start < 0:
        raise ValueError("command output did not contain a JSON object")
    return json.loads(text[start:])


def compact_summary(delta_result: Dict[str, Any], out_path: Path) -> Dict[str, Any]:
    rebuilt = delta_result.get("rebuilt") or {}
    delta = delta_result.get("delta") or {}
    return {
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "out": str(out_path),
        "ok": bool(delta_result.get("ok")),
        "records": rebuilt.get("records"),
        "raw_records": rebuilt.get("raw_records"),
        "errors": rebuilt.get("errors"),
        "missing_ids": delta.get("missing_ids"),
        "added_ids": delta.get("added_ids"),
        "missing_sample": delta.get("missing_sample") or [],
        "added_sample": delta.get("added_sample") or [],
        "sources": rebuilt.get("sources") or [],
        "policy_exclusions": delta_result.get("policy_exclusions") or {},
        "failures": delta_result.get("failures") or [],
    }


def source_count_map(sources: Optional[List[Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in sources or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        name = str(item[0])
        try:
            count = int(item[1])
        except (TypeError, ValueError):
            continue
        counts[name] = count
    return counts


def source_evidence_counts(
    path: Path,
    policy: Optional[Dict[str, Any]] = None,
) -> List[List[Any]]:
    """Count records carrying each source family, including merged secondary evidence.

    Primary-source counts fall when cross-source duplicates are safely merged even though
    every source reference remains in ``sources[]``. The canary protects evidence coverage,
    so one record contributes at most once to every family it actually carries.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    counts: Counter[str] = Counter()
    active_policy = policy or SOURCE_POLICY_EXCLUSIONS
    for record in payload.get("records") or []:
        if not isinstance(record, dict):
            continue
        if value_is_excluded(record, active_policy):
            continue
        names = {str(record.get("source") or "").strip()}
        names.update(
            str(source.get("name") or "").strip()
            for source in (record.get("sources") or [])
            if isinstance(source, dict)
        )
        counts.update(name for name in names if name)
    return [[name, count] for name, count in counts.most_common()]


def latest_ok_history_sources(
    path: Path,
    *,
    field: str = "sources",
    metric: Optional[str] = None,
    policy_ids: Optional[Iterable[str]] = None,
) -> List[Any]:
    if not path.exists():
        return []
    required_policy_ids = None if policy_ids is None else sorted(str(value) for value in policy_ids)
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if metric is not None and entry.get("source_family_metric") != metric:
            continue
        if required_policy_ids is not None:
            entry_policy = entry.get("policy_exclusions") or {}
            entry_policy_ids = sorted(str(value) for value in (entry_policy.get("ids") or []))
            if entry_policy_ids != required_policy_ids:
                continue
        if entry.get("ok") and entry.get(field):
            return entry.get(field) or []
    return []


def source_family_threshold_failures(
    current_sources: Optional[List[Any]],
    reference_sources: Optional[List[Any]],
    *,
    min_ratio: float,
    min_count: int,
    label: str,
    policy: Optional[Dict[str, Any]] = None,
) -> Tuple[List[str], Dict[str, Dict[str, Union[int, float]]]]:
    current = source_count_map(current_sources)
    reference = source_count_map(reference_sources)
    failures: List[str] = []
    checked: Dict[str, Dict[str, Union[int, float]]] = {}
    active_policy = policy or SOURCE_POLICY_EXCLUSIONS
    if min_ratio <= 0:
        return failures, checked
    for source, reference_count in sorted(reference.items()):
        if source_family_is_excluded(source, active_policy):
            continue
        if reference_count < min_count:
            continue
        floor = int(math.ceil(reference_count * min_ratio))
        current_count = current.get(source, 0)
        checked[source] = {
            "reference": reference_count,
            "current": current_count,
            "floor": floor,
            "ratio": min_ratio,
        }
        if current_count < floor:
            failures.append(
                f"source family {source!r} dropped to {current_count} records below {label} floor {floor} "
                f"({min_ratio:.0%} of {reference_count})"
            )
    return failures, checked


def canary_commands(
    *,
    python: str,
    out_path: Path,
    existing_candidate: bool,
    policy_path: Path,
    baseline_path: Path = ROOT / "data" / "free_intelligence.json",
) -> Tuple[Optional[List[str]], List[str]]:
    """Build commands without ever rebuilding over an existing candidate."""
    ingest_cmd = None if existing_candidate else [
        python,
        "scripts/ingest_sources.py",
        "--out",
        str(out_path),
    ]
    delta_cmd = [
        python,
        "scripts/qa_ingestion_delta.py",
        "--baseline",
        str(baseline_path),
        "--rebuilt",
        str(out_path),
        "--policy-exclusions",
        str(policy_path),
    ]
    return ingest_cmd, delta_cmd


def trim_history(path: Path, keep: int) -> None:
    if keep <= 0 or not path.exists():
        return
    rows = path.read_text(encoding="utf-8").splitlines()
    if len(rows) <= keep:
        return
    path.write_text("\n".join(rows[-keep:]) + "\n", encoding="utf-8")


def compact_command(run: Dict[str, Any] | None, limit: int = 800) -> Dict[str, Any] | None:
    if run is None:
        return None
    return {
        "cmd": run.get("cmd"),
        "returncode": run.get("returncode"),
        "stdout_tail": str(run.get("stdout") or "")[-limit:],
        "stderr_tail": str(run.get("stderr") or "")[-limit:],
    }


def history_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    compacted = dict(entry)
    commands = compacted.get("commands")
    if commands:
        compacted["commands"] = {
            "ingest": compact_command(commands.get("ingest")),
            "delta": compact_command(commands.get("delta")),
        }
    return compacted


def append_history(path: Path, entry: Dict[str, Any], keep: int = 30) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(history_entry(entry), ensure_ascii=False, sort_keys=True) + "\n")
    trim_history(path, keep)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run non-destructive Volta ingestion canary.")
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument("--out", type=Path)
    output_group.add_argument(
        "--candidate",
        "--existing-candidate",
        dest="candidate",
        type=Path,
        help="Audit this exact candidate without rebuilding or modifying it.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=ROOT / "data" / "free_intelligence.json",
        help="Canonical/baseline DB used by the delta and source-family gates.",
    )
    parser.add_argument("--history-out", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--history-keep", type=int, default=30)
    parser.add_argument("--min-source-family-ratio", type=float, default=0.85)
    parser.add_argument("--min-source-family-count", type=int, default=5)
    parser.add_argument("--policy-exclusions", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()

    out_path = (args.candidate or args.out or DEFAULT_OUT).expanduser()
    baseline_path = args.baseline.expanduser()
    history_path = args.history_out.expanduser()
    policy_path = args.policy_exclusions.expanduser()
    policy = load_source_policy_exclusions(policy_path)
    ingest_cmd, delta_cmd = canary_commands(
        python=args.python,
        out_path=out_path,
        existing_candidate=args.candidate is not None,
        policy_path=policy_path,
        baseline_path=baseline_path,
    )

    ingest_run = run_json_command(ingest_cmd) if ingest_cmd is not None else None
    failures: List[str] = []
    if args.candidate is not None and not out_path.is_file():
        failures.append(f"existing candidate does not exist: {out_path}")
    if ingest_run is not None and ingest_run["returncode"] != 0:
        failures.append(f"ingest command failed with {ingest_run['returncode']}")
    if not baseline_path.is_file():
        failures.append(f"baseline does not exist: {baseline_path}")

    candidate_sha_before: Optional[str] = None
    baseline_sha: Optional[str] = None
    if not failures:
        try:
            candidate_sha_before = sha256_file(out_path)
            baseline_sha = sha256_file(baseline_path)
        except OSError as exc:
            failures.append(f"could not hash candidate/baseline: {type(exc).__name__}")

    delta_run = None
    delta_result: Dict[str, Any] = {"ok": False, "failures": ["delta audit not run"]}
    if not failures:
        delta_run = run_json_command(delta_cmd)
        try:
            delta_result = parse_last_json_object(delta_run["stdout"])
        except Exception as exc:  # noqa: BLE001 - QA evidence should include parser failure.
            failures.append(f"could not parse delta JSON: {type(exc).__name__}: {exc}")
        if delta_run["returncode"] != 0:
            failures.append(f"delta command failed with {delta_run['returncode']}")
        if not delta_result.get("ok"):
            failures.extend(str(item) for item in (delta_result.get("failures") or []))

    summary = compact_summary(delta_result, out_path)
    summary["candidate"] = str(out_path)
    summary["baseline"] = str(baseline_path)
    summary["candidate_reused"] = args.candidate is not None
    summary["candidate_sha256"] = candidate_sha_before
    summary["baseline_sha256"] = baseline_sha
    source_family_metric = "record-evidence-v2"
    summary["source_family_metric"] = source_family_metric
    summary["source_evidence"] = source_evidence_counts(out_path, policy)
    baseline_source_evidence = source_evidence_counts(baseline_path, policy)
    baseline_family_failures, baseline_families = source_family_threshold_failures(
        summary.get("source_evidence"),
        baseline_source_evidence,
        min_ratio=args.min_source_family_ratio,
        min_count=args.min_source_family_count,
        label="baseline",
        policy=policy,
    )
    history_sources = latest_ok_history_sources(
        history_path,
        field="source_evidence",
        metric=source_family_metric,
        policy_ids=[str(item.get("id")) for item in (policy.get("exclusions") or []) if item.get("id")],
    )
    history_family_failures, history_families = source_family_threshold_failures(
        summary.get("source_evidence"),
        history_sources,
        min_ratio=args.min_source_family_ratio,
        min_count=args.min_source_family_count,
        label="latest history",
        policy=policy,
    )
    failures.extend(baseline_family_failures)
    failures.extend(history_family_failures)
    if candidate_sha_before is not None:
        try:
            candidate_sha_after = sha256_file(out_path)
        except OSError as exc:
            candidate_sha_after = None
            failures.append(f"could not re-hash candidate: {type(exc).__name__}")
        if candidate_sha_after != candidate_sha_before:
            failures.append("candidate changed while the canary was auditing it")
        summary["candidate_sha256_after"] = candidate_sha_after
    summary["source_family_threshold"] = {
        "min_ratio": args.min_source_family_ratio,
        "min_count": args.min_source_family_count,
        "baseline": baseline_families,
        "latest_history": history_families,
    }
    summary["ok"] = not failures
    summary["failures"] = failures
    summary["commands"] = {
        "ingest": ingest_run,
        "delta": delta_run,
    }
    append_history(history_path, summary, keep=args.history_keep)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
