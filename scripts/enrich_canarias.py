#!/usr/bin/env python3
"""Build a Canarias-only candidate from the currently served full catalogue.

Output is a candidate, never an automatic production promotion. Adopt it through
runtime_refresh.py --candidate, which runs the existing delta and canary gates.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scripts import ingest_sources as ing
from scripts.canarias_enrichment import enrich_records, overlay_payload


def combined_fetch_evidence(previous: dict, new_artifacts: list[dict]) -> dict:
    """Keep historical evidence intact and count each new artifact only once."""
    artifacts = list(previous.get("artifacts") or [])
    seen = {json.dumps(item, sort_keys=True, separators=(",", ":")) for item in artifacts}
    for item in new_artifacts:
        key = json.dumps(item, sort_keys=True, separators=(",", ":"))
        if key not in seen:
            artifacts.append(item)
            seen.add(key)
    caches = [item.get("cache") or {} for item in artifacts]
    return {**previous, "artifacts": artifacts, "summary": {
        "total": len(artifacts),
        "network": sum(cache.get("hit") is False for cache in caches),
        "cache_hits": sum(cache.get("hit") is True for cache in caches),
        "fallbacks": sum(cache.get("fallback") is True for cache in caches),
        "stale": sum(cache.get("stale") is True for cache in caches),
        "stale_fallbacks": sum(cache.get("fallback") is True and cache.get("stale") is True
                               for cache in caches),
    }}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--map", type=Path, help="Already fetched public map; use its original modification time.")
    p.add_argument("--cache-dir", type=Path, default=ing.CACHE / "canarias_details")
    args = p.parse_args()
    paths = [args.baseline, args.out, args.report, *([args.map] if args.map else [])]
    if len({path.resolve() for path in paths}) != len(paths):
        p.error("Baseline, map, candidate and report paths must be distinct")
    baseline = json.loads(args.baseline.read_text())
    if args.map:
        fetched = datetime.fromtimestamp(args.map.stat().st_mtime, timezone.utc)
        fetched_at = fetched.isoformat().replace("+00:00", "Z")
        age_seconds = max(0., (datetime.now(timezone.utc) - fetched).total_seconds())
        artifact = ing.FetchArtifact(args.map.read_bytes(), ing.CANARIAS_TE_RECARGA_GEOJSON_URL,
                                    args.map.name, fetched_at, age_seconds, True, False,
                                    age_seconds >= 24 * 3600)
    else:
        artifact = ing.cached_fetch(ing.CANARIAS_TE_RECARGA_GEOJSON_URL,
                                   ing.CANARIAS_TE_RECARGA_CACHE_NAME, ttl_hours=24, timeout=45)
    records = ing.parse_canarias_tecarga_geojson(json.loads(artifact.body), source_artifact=artifact)
    collection = enrich_records(records, ing, cache_dir=args.cache_dir)
    candidate, overlay = overlay_payload(baseline, records, ing)
    candidate["canarias_enrichment"]["collection"] = collection
    candidate["fetch_evidence"] = combined_fetch_evidence(
        baseline.get("fetch_evidence") or {}, [artifact.as_evidence(), *ing.FETCH_EVIDENCE])
    report = {"collection": collection, "overlay": overlay,
              "baseline_recordset_sha256": ing.recordset_sha256(baseline["records"]),
              "candidate_recordset_sha256": candidate["recordset_sha256"]}
    for path, payload in ((args.out, candidate), (args.report, report)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
