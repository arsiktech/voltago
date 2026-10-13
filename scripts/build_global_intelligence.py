#!/usr/bin/env python3
"""Build a reviewable worldwide candidate without changing canonical data.

Examples:
  python3 scripts/build_global_intelligence.py --all-countries --sources ocm
  python3 scripts/build_global_intelligence.py --countries FR,DE --sources ocm,osm
  python3 scripts/build_global_intelligence.py --countries US --sources osm --bbox 37,-123,38,-122

Repeat runs reuse fresh per-query caches; --osm-offset continues a bounded batch.
The manifest lists deferred/failed scopes explicitly. No command promotes output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.parse
from collections import Counter
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from . import global_sources as gs
    from .json_recordset import load_json_recordset
except ImportError:
    import global_sources as gs
    from json_recordset import load_json_recordset

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "data" / "free_intelligence.json"
DEFAULT_OUTPUT = ROOT / "data" / "global_intelligence.candidate.json"


def national_module():
    try:
        if __package__:
            from . import national_sources
        else:
            import national_sources
        return national_sources
    except ImportError:
        return None


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    os.replace(temp, path)


def write_candidate(path: Path, metadata: dict, store: gs.CandidateStore) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        # Serialise one station at a time; never rebuild the global list in RAM.
        handle.write(json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), allow_nan=False)[:-1])
        handle.write(',"records":[')
        first = True
        for row in store.rows():
            if not first:
                handle.write(",")
            json.dump(row, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            first = False
        handle.write("]}\n")
    os.replace(temp, path)


def source_manifest(source_id: str, scope: str) -> dict:
    return {"source": source_id, "scope": scope, "status": "pending", "seen": 0, "accepted": 0,
            "rejected": 0, "duplicates": 0, "errors": [], "fetches": []}


def run_ocm(args: argparse.Namespace, countries: set[str] | None, store: gs.CandidateStore) -> dict:
    manifest = source_manifest("ocm", "all-countries" if countries is None else ",".join(sorted(countries)))
    provider_counts: Counter = Counter()
    rejection_counts: Counter = Counter()
    try:
        revision = getattr(args, "ocm_revision", gs.OCM_EXPORT_REVISION)
        source_url = f"https://codeload.github.com/openchargemap/ocm-export/tar.gz/{revision}"
        if args.ocm_archive:
            path = args.ocm_archive.resolve()
            age = max(0, time.time() - path.stat().st_mtime)
            artifact = gs.FetchArtifact(b"", source_url, path.name, gs.iso_timestamp(path.stat().st_mtime),
                                        age, True, args.offline, age >= 168 * 3600)
            fetched = gs.FileArtifact(path, artifact)
        else:
            fetched = gs.fetch_cached_file(source_url, args.cache_dir, f"ocm-export-{revision}.tar.gz", offline=args.offline)
        actual_revision = gs.ocm_export_revision(fetched.path)
        if actual_revision != revision:
            raise ValueError("OCM archive revision differs from --ocm-revision")
        manifest["fetches"].append(fetched.artifact.as_evidence())
        manifest["snapshot"] = {"repository": "openchargemap/ocm-export", "revision": actual_revision,
                                "sha256": gs.file_sha256(fetched.path), "bytes": fetched.path.stat().st_size}
        for poi in gs.iter_ocm_export(fetched.path, countries=countries):
            manifest["seen"] += 1
            provider = poi.get("DataProvider") or {}
            provider_id = str(provider.get("ID") or poi.get("DataProviderID") or "unknown") if isinstance(provider, dict) else "malformed"
            try:
                row = gs.normalize_ocm(poi, fetched.artifact)
            except (TypeError, ValueError, AttributeError, KeyError):
                row = None
            if row is None:
                manifest["rejected"] += 1
                rejection_counts["provider_license_unapproved" if not gs.provider_license_allowed(provider) else "invalid_or_excluded_record"] += 1
                continue
            result = store.add(row)
            manifest["accepted"] += result == "added"
            if result == "added":
                provider_counts[provider_id] += 1
            manifest["duplicates"] += result == "duplicate"
            manifest["rejected"] += result in {"excluded", "invalid"}
        manifest["status"] = "stale" if fetched.artifact.stale else "ok"
    except Exception as exc:
        manifest["status"] = "partial" if manifest["accepted"] else "failed"
        manifest["errors"].append(type(exc).__name__ + ": " + str(exc)[:240])
    manifest["accepted_by_provider_id"] = dict(provider_counts)
    manifest["rejection_reasons"] = dict(rejection_counts)
    return manifest


def run_osm(args: argparse.Namespace, countries: set[str] | None, store: gs.CandidateStore) -> list[dict]:
    manifests = []
    codes = sorted(countries or gs.ISO_COUNTRIES)
    if args.bbox:
        if len(codes) != 1:
            raise ValueError("--bbox requires exactly one --countries value to attribute untagged rows")
        tasks = [(codes[0], tuple(map(float, text.split(",")))) for text in args.bbox]
    else:
        tasks = [(code, None) for code in codes]
    requests = 0
    for index, (country, bbox) in enumerate(tasks):
        query = gs.osm_query(country, bbox)
        digest = hashlib.sha256(query.encode()).hexdigest()[:20]
        scope = country + (":" + ",".join(map(str, bbox)) if bbox else "")
        manifest = source_manifest("osm", scope)
        manifests.append(manifest)
        if index < args.osm_offset:
            manifest["status"] = "deferred_offset"
            continue
        cache_name = f"osm-{country}-{digest}.json"
        path = args.cache_dir / cache_name
        fresh = path.is_file() and path.stat().st_size > 0 and time.time() - path.stat().st_mtime < 24 * 3600
        if not fresh and not args.offline and requests >= args.osm_max_requests:
            manifest["status"] = "deferred_budget"
            continue
        try:
            if not fresh and not args.offline:
                if requests:
                    time.sleep(args.osm_pause_seconds)
                requests += 1
            fetched = gs.fetch_cached_file(gs.OVERPASS_URL + "?" + urllib.parse.urlencode({"data": query}),
                                          args.cache_dir, cache_name, ttl_hours=24,
                                          max_bytes=32 * 1024 * 1024, timeout=55, offline=args.offline)
            manifest["fetches"].append(fetched.artifact.as_evidence())
            payload = json.loads(fetched.path.read_text(encoding="utf-8"))
            elements = gs.parse_osm_payload(payload)
            for element in elements:
                manifest["seen"] += 1
                try:
                    row = gs.normalize_osm(element, fetched.artifact, country)
                except (TypeError, ValueError, AttributeError, KeyError):
                    row = None
                if row is None:
                    manifest["rejected"] += 1
                    continue
                result = store.add(row)
                manifest["accepted"] += result == "added"
                manifest["duplicates"] += result == "duplicate"
                manifest["rejected"] += result in {"excluded", "invalid"}
            manifest["status"] = "stale" if fetched.artifact.stale else "ok"
        except Exception as exc:
            manifest["status"] = "failed"
            manifest["errors"].append(type(exc).__name__ + ": " + str(exc)[:240])
    return manifests


def build(args: argparse.Namespace) -> dict:
    output, baseline_path = args.out.resolve(), args.baseline.resolve()
    if output == baseline_path or output == CANONICAL.resolve() or "candidate" not in output.name.lower():
        raise ValueError("output must have 'candidate' in its filename and must not overwrite the baseline/canonical DB")
    manifest_path = args.manifest.resolve() if args.manifest else output.with_suffix(".manifest.json")
    if manifest_path in {output, baseline_path, CANONICAL.resolve()}:
        raise ValueError("manifest must not overwrite candidate or canonical data")
    revision = getattr(args, "ocm_revision", gs.OCM_EXPORT_REVISION)
    if len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision):
        raise ValueError("--ocm-revision must be an immutable 40-character Git commit SHA")
    countries = None if args.all_countries else set(args.countries.upper().split(","))
    if countries is not None and any(not gs.valid_country(code) for code in countries):
        raise ValueError("--countries must contain ISO 3166-1 alpha-2 codes")
    sources = list(dict.fromkeys(args.sources.split(",")))
    national = national_module()
    known = {"ocm", "osm"} | (set(national.SOURCES) if national else set())
    if any(source not in known for source in sources):
        raise ValueError("unknown source; available: " + ",".join(sorted(known)))
    if args.osm_max_requests < 0 or args.osm_max_requests > len(gs.ISO_COUNTRIES) or args.osm_offset < 0 or not 1 <= args.osm_pause_seconds <= 60:
        raise ValueError("OSM request limits or pause are outside supported bounds")
    if "osm" in sources and args.bbox:
        if countries is None or len(countries) != 1:
            raise ValueError("--bbox requires exactly one --countries value")
        for text in args.bbox:
            gs.osm_query(next(iter(countries)), tuple(map(float, text.split(","))))
    baseline_sha256 = gs.file_sha256(baseline_path)
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="global-stage-", dir=args.cache_dir) as stage_dir:
        store = gs.CandidateStore(Path(stage_dir) / "stations.sqlite")
        try:
            baseline_count = 0

            def preserve_baseline(row: dict) -> None:
                nonlocal baseline_count
                if store.add(row, baseline=True) != "added":
                    raise ValueError("baseline contains excluded or invalid rows; resolve with the existing policy/delta workflow first")
                baseline_count += 1

            # The SQLite staging store owns the rows. Do not retain a second
            # world catalogue or an interning table of every source string.
            baseline = load_json_recordset(baseline_path, record_transform=preserve_baseline)
            if "records" not in baseline:
                raise ValueError("baseline must be a canonical payload with a records array")
            if gs.file_sha256(baseline_path) != baseline_sha256:
                raise ValueError("baseline changed while staging its records")
            manifests = []
            for source in sources:
                if source == "ocm":
                    manifests.append(run_ocm(args, countries, store))
                elif source == "osm":
                    manifests.extend(run_osm(args, countries, store))
                else:
                    try:
                        manifest = national.run_source(source, args.cache_dir, countries, args.offline, store.add)
                    except Exception as exc:
                        manifest = source_manifest(source, "selected countries")
                        manifest.update(status="failed", errors=[type(exc).__name__ + ": " + str(exc)[:240]])
                    manifests.append(manifest)
            stats = store.stats()
            issues = [item for item in manifests if item.get("status") not in {"ok", "not_applicable"}]
            report = {
                "schema_version": 1, "generated_at": gs.iso_timestamp(), "artifact_role": "candidate",
                "promotion_allowed": False, "promotion_gates": ["baseline/delta review", "source canaries", "currency and provenance review", "runtime/client validation", "owner deployment decision"],
                "requested_countries": "all" if countries is None else sorted(countries),
                "requested_sources": sources, "baseline_records": baseline_count,
                "baseline_preserved": stats.get("baseline", 0) == baseline_count,
                "baseline_sha256": baseline_sha256,
                "coverage_complete": False, "requested_sources_complete": not issues,
                "coverage_status": "partial" if issues else "selected sources processed; geographic completeness unproven",
                "stats": stats, "source_manifests": manifests,
                "notes": ["Counts describe directory records, not live chargers or complete world coverage.",
                          "Foreign and unstructured prices retain original evidence and are not compared to a euro price cap.",
                          "Stable-source duplicate rows are retained from the baseline without modifying its evidence; new evidence is not promoted automatically.",
                          "OSM work is bounded and resumable through cache reuse and --osm-offset; deferred countries remain explicitly uncovered."],
            }
            metadata = {k: v for k, v in baseline.items() if k not in {"records", "sample", "total_records", "raw_records", "errors", "recordset_sha256"}}
            metadata.update(generated_at=report["generated_at"], total_records=stats["total_records"],
                            raw_records=stats["total_records"] + stats.get("duplicate", 0), candidate=True, geographic_scope="global",
                            recordset_sha256=store.fingerprint(),
                            global_coverage=report, errors=[{"source": i["source"], "scope": i.get("scope"), "status": i.get("status"), "errors": i.get("errors", [])} for i in issues],
                            license_notes=list(baseline.get("license_notes") or []) + ["OpenChargeMap: per-provider attribution and compatible open licences required.", "OpenStreetMap contributors: ODbL 1.0 attribution/share-alike obligations."])
            write_candidate(output, metadata, store)
            atomic_json(manifest_path, report)
            return report
        finally:
            store.close()


def main() -> int:
    national = national_module()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--countries", help="comma-separated ISO alpha-2 countries, e.g. ES,FR,US,JP")
    selection.add_argument("--all-countries", action="store_true")
    parser.add_argument("--sources", default="ocm", help="comma-separated: ocm,osm" + ("," + ",".join(national.SOURCES) if national else ""))
    parser.add_argument("--baseline", type=Path, default=CANONICAL)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "data" / "cache" / "global")
    parser.add_argument("--offline", action="store_true", help="only use cached evidence, retaining its original age")
    parser.add_argument("--ocm-archive", type=Path, help="existing OCM export tar.gz, read in place without extracting")
    parser.add_argument("--ocm-revision", default=gs.OCM_EXPORT_REVISION, help="immutable OCM export Git commit; advance explicitly for a newer snapshot")
    parser.add_argument("--bbox", action="append", help="bounded OSM south,west,north,east viewport; may repeat")
    parser.add_argument("--osm-max-requests", type=int, default=8)
    parser.add_argument("--osm-offset", type=int, default=0)
    parser.add_argument("--osm-pause-seconds", type=float, default=3)
    args = parser.parse_args()
    try:
        report = build(args)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps({"candidate_records": report["stats"]["total_records"], "countries": len(report["stats"]["countries"]),
                      "baseline_preserved": report["baseline_preserved"], "coverage_status": report["coverage_status"],
                      "source_statuses": Counter(item.get("status") for item in report["source_manifests"])}, ensure_ascii=False))
    # A partial candidate is useful for review but cannot masquerade as a clean scheduled build.
    return 2 if report["coverage_status"] == "partial" else 0


if __name__ == "__main__":
    raise SystemExit(main())
