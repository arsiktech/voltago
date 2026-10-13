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
from station_identity import preserve_record_ids

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = {
    "free_intelligence.json": "data/free_intelligence.json",
    "ingestion-canary-history.jsonl": "qa/ingestion-canary-history.jsonl",
    "free-db-completeness-latest.json": "qa/free-db-completeness-latest.json",
    "valencia-source-audit-latest.json": "qa/valencia-source-audit-latest.json",
}


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
        paths = {name: resolved / name for name in BUNDLE}
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
    for name in BUNDLE:
        if not (run / name).is_file():
            raise RuntimeError(f"candidate bundle missing {name}")
        shared.fsync_file(run / name)
    hashes = {name: shared.sha256_file(run / name) for name in BUNDLE}
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
        if {name: shared.sha256_file(current / name) for name in BUNDLE} != hashes:
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
        payload = json.loads((run / "free_intelligence.json").read_text())
        if not isinstance(payload.get("records"), list) or not payload["records"]:
            raise RuntimeError("cannot initialize an empty dataset")
        publish_bundle(state, run, hashes)
        receipt = {"run_id": run_id, "initialization": True, "ok": True, "promoted": True,
                   "generated_at": payload.get("generated_at"), "records": len(payload["records"]),
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


def refresh(root: Path, state: Path, timeout: float, *, dry_run: bool = False) -> int:
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
            run_stage(root, run, "build", [sys.executable, "scripts/ingest_sources.py", "--out", str(candidate)], timeout)
            payload, identity_report = preserve_record_ids(
                json.loads(baseline["free_intelligence.json"].read_text()), json.loads(candidate.read_text()))
            receipt["identity_reconciliation"] = identity_report
            if identity_report["preserved_ids"] or identity_report.get("historical_aliases_added", 0):
                shared.atomic_json(candidate, payload)
            candidate_sha = shared.sha256_file(candidate)
            receipt["candidate_sha256"] = candidate_sha
            payload = json.loads(candidate.read_text())
            receipt["candidate_records"] = len(payload.get("records") or [])
            receipt["source_adapter_counts"] = payload.get("source_adapter_counts", {})
            receipt["collector_errors"] = payload.get("errors", [])
            receipt["fetch_evidence_summary"] = (payload.get("fetch_evidence") or {}).get("summary", {})
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
            bundle_hashes = {name: shared.sha256_file(run / name) for name in BUNDLE}
            receipt["bundle_sha256"] = bundle_hashes
            receipt["stage"] = "revalidate"
            if source_identity(root) != identity:
                raise RuntimeError("source code or policy changed during refresh")
            current_baseline = baseline_paths(root, state)
            if current_baseline != baseline or any(shared.sha256_file(p) != old_hashes[n] for n, p in baseline.items()):
                raise RuntimeError("runtime baseline changed during refresh")
            if shared.sha256_file(candidate) != candidate_sha:
                raise RuntimeError("gated candidate changed before promotion")
            payload = json.loads(candidate.read_text())
            receipt["generated_at"] = payload.get("generated_at")
            receipt["records"] = len(payload["records"])
            receipt["source_adapter_counts"] = payload.get("source_adapter_counts", {})
            receipt["stage"] = "publish"
            if not dry_run:
                # All four files switch together. Previous bundles remain intact
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
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--initialize", action="store_true", help="Copy the existing dataset unchanged before configuring the server override.")
    args = parser.parse_args()
    if not 0 < args.command_timeout <= 3600:
        parser.error("--command-timeout must be in (0, 3600]")
    os.umask(0o077)
    if args.initialize:
        return initialize(ROOT, args.state_dir)
    return refresh(ROOT, args.state_dir, args.command_timeout, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
