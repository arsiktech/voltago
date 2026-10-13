#!/usr/bin/env python3
"""Build and gate charger data outside Git; atomically publish a runtime bundle.

Unlike auto_refresh.py this command never fetches, commits, pushes, changes code,
or restarts the server. The server reads VOLTA_INTELLIGENCE_DB in current/.
Failed candidates and their audit output remain in runs/ for diagnosis.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import re
import shutil
from pathlib import Path
import subprocess
import sys
import uuid

import auto_refresh as shared
from json_recordset import load_json_recordset
from station_identity import preserve_record_ids

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = {
    "free_intelligence.json": "data/free_intelligence.json",
    "ingestion-canary-history.jsonl": "qa/ingestion-canary-history.jsonl",
    "free-db-completeness-latest.json": "qa/free-db-completeness-latest.json",
    "valencia-source-audit-latest.json": "qa/valencia-source-audit-latest.json",
}
INDEX_NAME = "free_intelligence.sqlite"


def dataset_summary(path: Path) -> dict:
    """Read metadata and an actual count without retaining the global catalogue."""
    count = 0

    def consume(row: dict) -> None:
        nonlocal count
        count += 1

    payload = load_json_recordset(path, record_transform=consume)
    if "records" not in payload:
        raise ValueError("dataset has no records array")
    payload.pop("records")
    payload["record_count"] = count
    return payload


def is_global_dataset(metadata: dict) -> bool:
    return metadata.get("geographic_scope") == "global" or isinstance(metadata.get("global_coverage"), dict)


def resolve_strategy(strategy: str, baseline: dict, candidate: dict | None = None) -> str:
    if strategy not in {"auto", "spain", "global"}:
        raise ValueError("refresh strategy must be auto, spain or global")
    has_global = is_global_dataset(baseline) or is_global_dataset(candidate or {})
    if strategy == "spain" and has_global:
        raise ValueError("Spain-only refresh cannot replace a global runtime dataset")
    return ("global" if has_global else "spain") if strategy == "auto" else strategy


def bundle_names(run: Path) -> list[str]:
    names = list(BUNDLE)
    if (run / INDEX_NAME).exists():
        names.append(INDEX_NAME)
    return names


def build_runtime_index(root: Path, run: Path, candidate: Path, candidate_sha: str,
                        records: int, timeout: float) -> dict:
    """Derive the serving index only from the exact JSON which passed the gates."""
    from intelligence_index import IntelligenceIndex

    policy = root / "data/source_policy_exclusions.json"
    result = run_stage(root, run, "index", [sys.executable, "scripts/build_intelligence_index.py",
        "--source", str(candidate), "--output", str(run / INDEX_NAME), "--policy", str(policy),
        "--seeds", str(root / "data/seed_leads.json"), "--health-summary"], timeout)
    receipt = shared.parse_last_json_object(result.stdout)
    index = IntelligenceIndex(run / INDEX_NAME, policy_sha256=shared.sha256_file(policy))
    manifest = index.metadata().get("index_manifest") or {}
    if (receipt.get("source_sha256") != candidate_sha or manifest.get("source_sha256") != candidate_sha
            or receipt.get("records") != records or index.count != records
            or receipt.get("excluded_records") != 0
            or receipt.get("index_sha256") != shared.sha256_file(run / INDEX_NAME)):
        raise RuntimeError("runtime index does not match the exact gated candidate")
    return receipt


def source_identity(root: Path) -> dict:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    files = [root / "server.py", root / "station_truth.py", root / "community.py",
             root / "data/source_policy_exclusions.json", *sorted((root / "scripts").glob("*.py"))]
    return {"head": head, "sha256": {str(p.relative_to(root)): shared.sha256_file(p) for p in files}}


def baseline_paths(root: Path, state: Path) -> dict[str, Path]:
    current = state / "current"
    if current.exists() or current.is_symlink():
        if not current.is_symlink():
            raise RuntimeError("runtime current must be a symlink to a completed bundle")
        resolved = current.resolve(strict=True)
        if resolved.parent != (state / "runs").resolve():
            raise RuntimeError("runtime current points outside owned runs")
        paths = {name: resolved / name for name in bundle_names(resolved)}
    else:
        paths = {name: root / relative for name, relative in BUNDLE.items()}
    if not all(p.is_file() for p in paths.values()):
        raise RuntimeError("baseline data, history or reports missing")
    return paths


def validate_gate_receipts(delta: dict, canary: dict, candidate_sha: str, baseline_sha: str) -> None:
    if delta.get("ok") is not True or (delta.get("delta") or {}).get("missing_ids") != 0:
        raise RuntimeError("delta rejected candidate: require ok=true and zero missing IDs")
    if not (
        canary.get("ok") is True and canary.get("candidate_reused") is True
        and canary.get("candidate_sha256") == candidate_sha
        and canary.get("candidate_sha256_after") == candidate_sha
        and canary.get("baseline_sha256") == baseline_sha
    ):
        raise RuntimeError("canary did not prove exact candidate and baseline")


def publish_bundle(state: Path, run: Path, expected_hashes: dict | None = None) -> None:
    if run.parent.resolve() != (state / "runs").resolve():
        raise RuntimeError("candidate bundle outside owned runs")
    names = bundle_names(run)
    for name in names:
        if not (run / name).is_file():
            raise RuntimeError(f"candidate bundle missing {name}")
        shared.fsync_file(run / name)
    hashes = {name: shared.sha256_file(run / name) for name in names}
    if expected_hashes is not None and hashes != expected_hashes:
        raise RuntimeError("gated bundle changed before promotion")
    shared.fsync_directory(run)
    current = state / "current"
    previous = os.readlink(current) if current.is_symlink() else None
    if current.exists() and previous is None:
        raise RuntimeError("refusing to replace a non-symlink runtime current")
    link = state / f".current-{uuid.uuid4().hex}"
    switched = False
    try:
        link.symlink_to(Path("runs") / run.name, target_is_directory=True)
        os.replace(link, current)
        switched = True
        shared.fsync_directory(state)
        if {name: shared.sha256_file(current / name) for name in names} != hashes:
            raise RuntimeError("published bundle readback failed")
    except Exception:
        if switched:
            if previous is None:
                current.unlink()
            else:
                link.symlink_to(previous, target_is_directory=True)
                os.replace(link, current)
            shared.fsync_directory(state)
        raise
    finally:
        link.unlink(missing_ok=True)


def initialize(root: Path, state: Path) -> int:
    """Copy the existing baseline byte-for-byte; never label it newly collected."""
    root, state = root.resolve(), state.expanduser().resolve()
    if state == root or root in state.parents:
        raise ValueError("runtime state must be outside the Git checkout")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / "refresh.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (state / "current").exists() or (state / "current").is_symlink():
            raise RuntimeError("runtime data already initialized; refusing to rewind it")
        baseline = baseline_paths(root, state)
        hashes = {name: shared.sha256_file(path) for name, path in baseline.items()}
        run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        run = state / "runs" / run_id
        run.mkdir(parents=True, mode=0o700)
        for name, path in baseline.items():
            shared.copy_fsync(path, run / name)
        payload = dataset_summary(run / "free_intelligence.json")
        if not payload["record_count"]:
            raise RuntimeError("cannot initialize an empty dataset")
        if is_global_dataset(payload):
            build_runtime_index(root, run, run / "free_intelligence.json",
                                hashes["free_intelligence.json"], payload["record_count"], 3600)
            hashes[INDEX_NAME] = shared.sha256_file(run / INDEX_NAME)
        publish_bundle(state, run, hashes)
        receipt = {"run_id": run_id, "initialization": True, "ok": True, "promoted": True,
                   "generated_at": payload.get("generated_at"), "records": payload["record_count"],
                   "initialized_at": shared.utc_now(), "bundle_sha256": hashes}
        shared.atomic_json(run / "receipt.json", receipt)
        shared.atomic_json(state / "initialization.json", receipt)
        print(json.dumps(receipt))
    return 0


def prune_runs(state: Path, *, keep_recent: int = 12) -> list[str]:
    """Bound owned completed-run storage; retain current and last rollback bundle."""
    runs = state / "runs"
    entries = []
    for path in runs.iterdir():
        if path.is_symlink() or not path.is_dir() or not re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}", path.name):
            continue
        receipt = path / "receipt.json"
        if receipt.is_symlink():
            continue
        try:
            data = json.loads(receipt.read_text())
        except (OSError, ValueError):
            continue
        if data.get("run_id") == path.name:
            entries.append((path, data))
    entries.sort(key=lambda item: item[0].name, reverse=True)
    protected = {path for path, _ in entries[:keep_recent]}
    current = (state / "current").resolve() if (state / "current").is_symlink() else None
    if current is not None:
        protected.add(current)
    successful = [p for p, d in entries if d.get("ok") and d.get("promoted") and p != current]
    protected.update(successful[:1])
    removed = []
    for path, _ in entries:
        if path not in protected:
            shutil.rmtree(path)
            removed.append(path.name)
    return removed


def run_stage(root: Path, run: Path, stage: str, argv: list[str], timeout: float) -> subprocess.CompletedProcess:
    # Preserve complete command output, including failures; never hide a failed
    # gate behind a later successful shell command.
    stdout_path, stderr_path = run / f"{stage}.stdout.log", run / f"{stage}.stderr.log"
    with stdout_path.open("w") as out, stderr_path.open("w") as err:
        result = subprocess.run(argv, cwd=root, text=True, stdout=out, stderr=err, timeout=timeout,
                                env={**os.environ, "PYTHONUNBUFFERED": "1"})
    result.stdout = stdout_path.read_text()
    result.stderr = stderr_path.read_text()
    if result.returncode:
        raise RuntimeError(f"{stage} exited {result.returncode}; see retained run logs")
    return result


def refresh(root: Path, state: Path, timeout: float, *, dry_run: bool = False,
            strategy: str = "auto", candidate_path: Path | None = None,
            sources: str = "ocm", ocm_revision: str | None = None) -> int:
    root = root.resolve()
    state = state.expanduser().resolve()
    if state == root or root in state.parents:
        raise ValueError("runtime state must be outside the Git checkout")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / "refresh.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another runtime refresh is active", file=sys.stderr)
            return 75
        run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        run = state / "runs" / run_id
        run.mkdir(parents=True, mode=0o700)
        receipt = {"run_id": run_id, "started_at": shared.utc_now(), "ok": False,
                   "promoted": False, "dry_run": dry_run, "stage": "preflight"}
        exit_code = 1
        try:
            identity = source_identity(root)
            receipt["source"] = identity
            baseline = baseline_paths(root, state)
            old_hashes = {name: shared.sha256_file(path) for name, path in baseline.items()}
            receipt["baseline_sha256"] = old_hashes
            candidate = run / "free_intelligence.json"
            receipt["stage"] = "build"
            baseline_meta = dataset_summary(baseline["free_intelligence.json"])
            imported_meta = dataset_summary(candidate_path) if candidate_path is not None else None
            selected = resolve_strategy(strategy, baseline_meta, imported_meta)
            receipt["strategy"] = selected
            if candidate_path is not None:
                imported_sha = shared.sha256_file(candidate_path)
                shared.copy_fsync(candidate_path, candidate)
                if shared.sha256_file(candidate) != imported_sha or shared.sha256_file(candidate_path) != imported_sha:
                    raise RuntimeError("imported candidate changed during copy")
                receipt["imported_candidate_sha256"] = imported_sha
            elif selected == "global":
                from global_sources import OCM_EXPORT_REVISION
                revision = ocm_revision or OCM_EXPORT_REVISION
                if not re.fullmatch(r"[0-9a-f]{40}", revision):
                    raise ValueError("OCM revision must be an immutable 40-character Git commit SHA")
                receipt["ocm_revision"] = revision
                receipt["global_sources"] = sources.split(",")
                output = run / "free_intelligence.candidate.json"
                run_stage(root, run, "build", [sys.executable, "scripts/build_global_intelligence.py",
                    "--all-countries", "--sources", sources, "--baseline", str(baseline["free_intelligence.json"]),
                    "--out", str(output), "--manifest", str(run / "global-coverage.manifest.json"),
                    "--ocm-revision", revision], timeout)
                os.replace(output, candidate)
            else:
                run_stage(root, run, "build", [sys.executable, "scripts/ingest_sources.py", "--out", str(candidate)], timeout)
                payload, identity_report = preserve_record_ids(
                    json.loads(baseline["free_intelligence.json"].read_text()), json.loads(candidate.read_text()))
                receipt["identity_reconciliation"] = identity_report
                if identity_report["preserved_ids"]:
                    shared.atomic_json(candidate, payload)
                del payload
            candidate_sha = shared.sha256_file(candidate)
            receipt["candidate_sha256"] = candidate_sha
            payload = imported_meta if imported_meta is not None else dataset_summary(candidate)
            if selected == "global" and not is_global_dataset(payload):
                raise RuntimeError("global refresh requires a candidate with explicit global provenance")
            receipt["candidate_records"] = payload["record_count"]
            receipt["source_adapter_counts"] = payload.get("source_adapter_counts", {})
            receipt["collector_errors"] = payload.get("errors", [])
            receipt["fetch_evidence_summary"] = (payload.get("fetch_evidence") or {}).get("summary", {})
            if selected == "global":
                receipt["global_coverage"] = payload.get("global_coverage", {})
                receipt["refresh_semantics"] = "append-only expansion; retained rows keep their original evidence age"
                revisions = {item.get("snapshot", {}).get("revision")
                             for item in receipt["global_coverage"].get("source_manifests", [])
                             if item.get("source") == "ocm"}
                receipt["observed_ocm_revisions"] = sorted(value for value in revisions if value)
            policy = root / "data/source_policy_exclusions.json"
            receipt["stage"] = "delta"
            result = run_stage(root, run, "delta", [sys.executable, "scripts/qa_ingestion_delta.py", "--baseline",
                str(baseline["free_intelligence.json"]), "--rebuilt", str(candidate), "--policy-exclusions", str(policy)], timeout)
            delta = shared.parse_last_json_object(result.stdout)
            # Enforce zero loss before canary and again with bound evidence below.
            if delta.get("ok") is not True or (delta.get("delta") or {}).get("missing_ids") != 0:
                raise RuntimeError("delta rejected candidate: require ok=true and zero missing IDs")
            receipt["delta"] = delta
            history = run / "ingestion-canary-history.jsonl"
            shared.copy_fsync(baseline[history.name], history)
            receipt["stage"] = "canary"
            result = run_stage(root, run, "canary", [sys.executable, "scripts/qa_ingestion_canary.py", "--candidate",
                str(candidate), "--baseline", str(baseline["free_intelligence.json"]), "--history-out", str(history),
                "--policy-exclusions", str(policy)], timeout)
            canary = shared.parse_last_json_object(result.stdout)
            validate_gate_receipts(delta, canary, candidate_sha, old_hashes["free_intelligence.json"])
            receipt["canary"] = canary
            receipt["stage"] = "reports"
            receipt["reports"] = shared.generate_reports(root=root, python=sys.executable, data=candidate,
                data_label=Path("data/free_intelligence.json"), free_report=run / "free-db-completeness-latest.json",
                valencia_report=run / "valencia-source-audit-latest.json", expected_data_sha256=candidate_sha,
                timeout=timeout)
            if selected == "global" or INDEX_NAME in baseline:
                receipt["stage"] = "index"
                receipt["runtime_index"] = build_runtime_index(root, run, candidate, candidate_sha,
                                                              payload["record_count"], timeout)
            bundle_hashes = {name: shared.sha256_file(run / name) for name in bundle_names(run)}
            receipt["bundle_sha256"] = bundle_hashes
            receipt["stage"] = "revalidate"
            if source_identity(root) != identity:
                raise RuntimeError("source code or policy changed during refresh")
            current_baseline = baseline_paths(root, state)
            if current_baseline != baseline or any(shared.sha256_file(p) != old_hashes[n] for n, p in baseline.items()):
                raise RuntimeError("runtime baseline changed during refresh")
            if shared.sha256_file(candidate) != candidate_sha:
                raise RuntimeError("gated candidate changed before promotion")
            receipt["generated_at"] = payload.get("generated_at")
            receipt["records"] = payload["record_count"]
            receipt["source_adapter_counts"] = payload.get("source_adapter_counts", {})
            receipt["stage"] = "publish"
            if not dry_run:
                # Data, reports and any derived index switch together. Previous bundles remain intact
                # for rollback; the web process notices the new file on its next read.
                publish_bundle(state, run, bundle_hashes)
                receipt["promoted"] = True
            receipt["ok"] = True
            receipt["stage"] = "complete"
            exit_code = 0
        except Exception as exc:
            receipt["error"] = f"{type(exc).__name__}: {exc}"
            message = f"Volta data refresh failed at {receipt['stage']}; previous data retained unless promoted=true. Run {run_id}."
            receipt["notification_handler"] = "systemd ExecStopPost hook; no per-attempt direct notification"
            print(message, file=sys.stderr)
        finally:
            receipt["finished_at"] = shared.utc_now()
            shared.atomic_json(run / "receipt.json", receipt)
            try:
                receipt["pruned_runs"] = prune_runs(state)
            except OSError as exc:
                receipt["retention_error"] = type(exc).__name__
            shared.atomic_json(state / ("dry-run-last.json" if dry_run else "last.json"), receipt)
            print(json.dumps({k: receipt.get(k) for k in ("run_id", "ok", "promoted", "stage", "records", "error")}))
        return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--command-timeout", type=float, default=1800)
    parser.add_argument("--strategy", choices=("auto", "spain", "global"),
                        default=os.environ.get("VOLTA_REFRESH_STRATEGY", "auto"))
    parser.add_argument("--candidate", type=Path,
                        help="Gate and adopt an existing JSON candidate without collecting or altering its bytes.")
    parser.add_argument("--sources", default=os.environ.get("VOLTA_GLOBAL_SOURCES", "ocm"),
                        help="Sources used by the global builder; defaults to ocm.")
    parser.add_argument("--ocm-revision", default=os.environ.get("VOLTA_OCM_REVISION"),
                        help="Immutable OCM export Git commit; defaults to the reviewed revision in source.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--initialize", action="store_true", help="Copy the existing dataset unchanged before configuring the server override.")
    args = parser.parse_args()
    if not 0 < args.command_timeout <= 3600:
        parser.error("--command-timeout must be in (0, 3600]")
    if args.initialize and args.candidate:
        parser.error("--candidate cannot be combined with --initialize")
    os.umask(0o077)
    if args.initialize:
        return initialize(ROOT, args.state_dir)
    return refresh(ROOT, args.state_dir, args.command_timeout, dry_run=args.dry_run,
                   strategy=args.strategy, candidate_path=args.candidate,
                   sources=args.sources, ocm_revision=args.ocm_revision)


if __name__ == "__main__":
    raise SystemExit(main())
