#!/usr/bin/env python3
"""Analyze Volta Go source-link history for provider regressions."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


def load_history(path: Path) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno} is not valid JSON: {exc}") from exc
    return entries


def bucket_rate(bucket: Dict[str, Any]) -> float:
    sampled = int(bucket.get("sampled") or 0)
    failed = int(bucket.get("failed") or 0)
    if sampled <= 0:
        return 0.0
    return failed / sampled


def aggregate_previous(entries: List[Dict[str, Any]], scope: str) -> Dict[str, Dict[str, int]]:
    totals: Dict[str, Dict[str, int]] = {}
    for entry in entries:
        for name, bucket in (entry.get(scope) or {}).items():
            totals.setdefault(name, {"sampled": 0, "failed": 0, "reachable": 0})
            totals[name]["sampled"] += int(bucket.get("sampled") or 0)
            totals[name]["failed"] += int(bucket.get("failed") or 0)
            totals[name]["reachable"] += int(bucket.get("reachable") or 0)
    return totals


def analyze(
    entries: List[Dict[str, Any]],
    *,
    min_entries: int = 1,
    max_latest_failed: int = 0,
    max_regression_delta: float = 0.34,
    min_provider_samples: int = 1,
) -> Dict[str, Any]:
    failures: List[str] = []
    if len(entries) < min_entries:
        failures.append(f"history has {len(entries)} entries, expected at least {min_entries}")
        latest = entries[-1] if entries else {}
        return {"ok": False, "entries": len(entries), "latest": latest, "failures": failures, "regressions": []}

    latest = entries[-1]
    latest_failed = int(latest.get("failed") or 0)
    if latest_failed > max_latest_failed:
        failures.append(f"latest source-link run has {latest_failed} failed links, expected <= {max_latest_failed}")

    previous = entries[:-1]
    previous_provider = aggregate_previous(previous, "by_provider")
    regressions: List[Dict[str, Any]] = []
    for provider, bucket in (latest.get("by_provider") or {}).items():
        sampled = int(bucket.get("sampled") or 0)
        if sampled < min_provider_samples:
            continue
        latest_rate = bucket_rate(bucket)
        prior_bucket = previous_provider.get(provider)
        prior_rate = bucket_rate(prior_bucket or {})
        if not prior_bucket:
            continue
        delta = latest_rate - prior_rate
        if delta > max_regression_delta:
            regressions.append({
                "provider": provider,
                "latest_failed_rate": round(latest_rate, 4),
                "previous_failed_rate": round(prior_rate, 4),
                "delta": round(delta, 4),
                "latest": bucket,
                "previous": prior_bucket,
            })
    for row in regressions:
        failures.append(
            f"{row['provider']} source-link failure rate regressed from "
            f"{row['previous_failed_rate']:.2%} to {row['latest_failed_rate']:.2%}"
        )

    return {
        "ok": not failures,
        "entries": len(entries),
        "latest_checked_at": latest.get("checked_at"),
        "latest_sampled": latest.get("sampled"),
        "latest_reachable": latest.get("reachable"),
        "latest_failed": latest_failed,
        "regressions": regressions,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", default="qa/source-link-history.jsonl")
    parser.add_argument("--min-entries", type=int, default=1)
    parser.add_argument("--max-latest-failed", type=int, default=0)
    parser.add_argument("--max-regression-delta", type=float, default=0.34)
    parser.add_argument("--min-provider-samples", type=int, default=1)
    args = parser.parse_args()

    entries = load_history(Path(args.history))
    result = analyze(
        entries,
        min_entries=args.min_entries,
        max_latest_failed=args.max_latest_failed,
        max_regression_delta=args.max_regression_delta,
        min_provider_samples=args.min_provider_samples,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
