#!/usr/bin/env python3
"""Fail-closed unattended refresh for Volta's canonical intelligence database.

Real runs are deliberately restricted to a clean, dedicated checkout on ``main``.
Dry runs may execute in a dirty development checkout because they only build and audit
ignored candidates. The exact candidate that passes both gates is the one promoted.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = Path("data/free_intelligence.json")
DEFAULT_HISTORY = Path("qa/ingestion-canary-history.jsonl")
DEFAULT_FREE_REPORT = Path("qa/free-db-completeness-latest.json")
DEFAULT_VALENCIA_REPORT = Path("qa/valencia-source-audit-latest.json")
DEFAULT_POLICY = Path("data/source_policy_exclusions.json")
DEFAULT_MARKER = Path(".volta-auto-refresh-dedicated")
REEXEC_STATE_ENV = "VOLTA_AUTO_REFRESH_SYNC_REEXEC"
REEXEC_STATE_VERSION = 1
ALLOWED_GIT_PATHS = {
    str(DEFAULT_DB),
    str(DEFAULT_HISTORY),
    str(DEFAULT_FREE_REPORT),
    str(DEFAULT_VALENCIA_REPORT),
}
TEMPFAIL = 75


class RefreshFailure(RuntimeError):
    def __init__(self, stage: str, detail: str) -> None:
        super().__init__(detail)
        self.stage = stage
        self.detail = detail


class PromotionError(RuntimeError):
    def __init__(
        self,
        detail: str,
        *,
        rollback_performed: bool,
        artifacts: Optional[Dict[str, Dict[str, Any]]] = None,
        backup_cleanup_performed: Optional[bool] = None,
    ) -> None:
        super().__init__(detail)
        self.rollback_performed = rollback_performed
        self.artifacts = artifacts or {}
        self.backup_cleanup_performed = backup_cleanup_performed


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fsync_directory(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def copy_fsync(source: Path, destination: Path, *, mode: int = 0o600) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(destination), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with source.open("rb") as read_handle, os.fdopen(descriptor, "wb", closefd=False) as write_handle:
            shutil.copyfileobj(read_handle, write_handle, length=1024 * 1024)
            write_handle.flush()
            os.fsync(write_handle.fileno())
    finally:
        os.close(descriptor)
    os.chmod(destination, mode)


def atomic_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    os.chmod(temp, 0o600)
    os.replace(temp, path)
    os.chmod(path, 0o600)
    fsync_directory(path.parent)


def parse_last_json_object(text: str) -> Dict[str, Any]:
    start = text.rfind("\n{")
    if start >= 0:
        start += 1
    else:
        start = text.find("{")
    if start < 0:
        raise ValueError("command output did not contain a JSON object")
    value = json.loads(text[start:])
    if not isinstance(value, dict):
        raise ValueError("command output JSON was not an object")
    return value


def compact_run(proc: subprocess.CompletedProcess[str], *, limit: int = 1600) -> Dict[str, Any]:
    return {
        "returncode": proc.returncode,
        "stdout_tail": str(proc.stdout or "")[-limit:],
        "stderr_tail": str(proc.stderr or "")[-limit:],
    }


def run_command(
    command: Sequence[str],
    *,
    root: Path,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RefreshFailure("command", f"command timed out after {timeout:g}s") from exc
    except OSError as exc:
        raise RefreshFailure("command", f"could not execute command: {type(exc).__name__}") from exc


def require_command(
    stage: str,
    command: Sequence[str],
    *,
    root: Path,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    try:
        proc = run_command(command, root=root, timeout=timeout)
    except RefreshFailure as exc:
        raise RefreshFailure(stage, exc.detail) from exc
    if proc.returncode != 0:
        raise RefreshFailure(stage, f"{stage} command exited {proc.returncode}")
    return proc


def git_output(root: Path, timeout: float, *args: str) -> str:
    proc = require_command("preflight", ["git", *args], root=root, timeout=timeout)
    return proc.stdout.strip()


def git_state(root: Path, timeout: float) -> Tuple[str, str, str]:
    top = Path(git_output(root, timeout, "rev-parse", "--show-toplevel")).resolve()
    if top != root.resolve():
        raise RefreshFailure("preflight", "--root is not the exact Git worktree root")
    branch = git_output(root, timeout, "symbolic-ref", "--quiet", "--short", "HEAD")
    head = git_output(root, timeout, "rev-parse", "HEAD")
    status = git_output(root, timeout, "status", "--porcelain=v1", "--untracked-files=all")
    return branch, head, status


def status_paths(status: str) -> Set[str]:
    paths: Set[str] = set()
    for line in status.splitlines():
        if not line:
            continue
        if len(line) < 4:
            raise RefreshFailure("git-allowlist", "could not parse git status output")
        path = line[3:]
        if " -> " in path:
            raise RefreshFailure("git-allowlist", "renames are not allowed during auto-refresh")
        paths.add(path)
    return paths


def require_allowed_status(root: Path, timeout: float, *, stage: str) -> str:
    proc = require_command(
        stage,
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        root=root,
        timeout=timeout,
    )
    unexpected = status_paths(proc.stdout) - ALLOWED_GIT_PATHS
    if unexpected:
        raise RefreshFailure(stage, f"unexpected Git paths changed: {', '.join(sorted(unexpected))}")
    return proc.stdout.strip()


def git_blob_oid(root: Path, timeout: float, path: str, *, staged: bool) -> str:
    command = ["git", "rev-parse", f":{path}"] if staged else ["git", "hash-object", "--", path]
    proc = require_command("git-verify", command, root=root, timeout=timeout)
    oid = proc.stdout.strip()
    if not oid:
        raise RefreshFailure("git-verify", f"could not resolve Git blob for {path}")
    return oid


def verify_committed_artifacts(
    root: Path,
    timeout: float,
    *,
    expected_sha256: Dict[str, str],
    expected_blobs: Dict[str, str],
) -> None:
    working_matches = all(
        sha256_file(root / path) == expected_sha
        for path, expected_sha in expected_sha256.items()
    )
    head_matches = all(
        require_command(
            "git-verify",
            ["git", "rev-parse", f"HEAD:{path}"],
            root=root,
            timeout=timeout,
        ).stdout.strip() == expected_blob
        for path, expected_blob in expected_blobs.items()
    )
    if not working_matches or not head_matches:
        raise RefreshFailure("git-verify", "final working or HEAD artifacts do not match the gated bytes")


def acquire_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    os.chmod(path, 0o600)
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid={os.getpid()} acquired_at={utc_now()}\n")
    handle.flush()
    os.fsync(handle.fileno())
    return handle


def release_lock(handle: Any) -> None:
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def parse_reexec_state(
    raw: str,
    *,
    root: Path,
    lock_path: Path,
    dry_run: bool,
) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    if dry_run:
        raise ValueError("dry-run cannot consume a real-refresh reexec state")
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("reexec state is not valid JSON") from exc
    if not isinstance(state, dict) or state.get("version") != REEXEC_STATE_VERSION:
        raise ValueError("reexec state version is invalid")
    if state.get("root") != str(root) or state.get("lock_path") != str(lock_path):
        raise ValueError("reexec state root or lock path does not match")
    lock_fd = state.get("lock_fd")
    if not isinstance(lock_fd, int) or lock_fd < 0:
        raise ValueError("reexec state lock descriptor is invalid")
    expected_head = state.get("expected_head")
    if not isinstance(expected_head, str) or not expected_head:
        raise ValueError("reexec state expected HEAD is invalid")
    if not isinstance(state.get("sync"), dict):
        raise ValueError("reexec state sync evidence is invalid")
    return state


def inherit_lock(path: Path, descriptor: int):
    try:
        descriptor_stat = os.fstat(descriptor)
        path_stat = path.stat()
        if (descriptor_stat.st_dev, descriptor_stat.st_ino) != (path_stat.st_dev, path_stat.st_ino):
            return None
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.set_inheritable(descriptor, False)
        return os.fdopen(descriptor, "a+", encoding="utf-8", closefd=True)
    except (OSError, ValueError):
        return None


def restore_from_backup(backup: Path, target: Path, work_dir: Path, *, mode: int) -> None:
    restore_temp = work_dir / f"restore-{target.name}-{os.getpid()}-{time.time_ns()}"
    copy_fsync(backup, restore_temp, mode=mode)
    os.replace(restore_temp, target)
    fsync_directory(target.parent)


def cleanup_promotion_backups(promotion: Dict[str, Any]) -> bool:
    """Remove rollback snapshots only after they are no longer needed."""
    ok = True
    try:
        artifacts = promotion["artifacts"]
    except (KeyError, TypeError):
        promotion["backup_cleanup_performed"] = False
        return False
    for evidence in artifacts.values():
        try:
            Path(evidence["backup"]).unlink(missing_ok=True)
        except (OSError, KeyError, TypeError):
            ok = False
    promotion["backup_cleanup_performed"] = ok
    return ok


def promote_transaction(
    artifacts: Sequence[Tuple[str, Path, Path]],
    work_dir: Path,
) -> Dict[str, Any]:
    """Promote a gated artifact set and restore the full set on any replace failure."""
    if not artifacts:
        raise PromotionError("promotion artifact set is empty", rollback_performed=False)
    for _, source, target in artifacts:
        if not source.is_file():
            raise PromotionError(f"promotion source missing: {source}", rollback_performed=False)
        if not target.is_file():
            raise PromotionError(f"promotion target missing: {target}", rollback_performed=False)
    work_dir.mkdir(parents=True, exist_ok=True)
    devices = {
        work_dir.stat().st_dev,
        *(source.parent.stat().st_dev for _, source, _ in artifacts),
        *(target.parent.stat().st_dev for _, _, target in artifacts),
    }
    if len(devices) != 1:
        raise PromotionError("candidates/backups and targets are not on one filesystem", rollback_performed=False)

    token = f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-{os.getpid()}-{time.time_ns()}"
    artifact_evidence: Dict[str, Dict[str, Any]] = {}
    try:
        for index, (name, source, target) in enumerate(artifacts):
            backup = work_dir / f"rollback-{index}-{target.name}-{token}"
            mode = target.stat().st_mode & 0o777
            artifact_evidence[name] = {
                "target": str(target),
                "backup": str(backup),
                "backup_sha256": None,
                "mode": mode,
            }
            copy_fsync(target, backup)
            fsync_file(source)
            os.chmod(source, mode)
            artifact_evidence[name]["backup_sha256"] = sha256_file(backup)
    except OSError as exc:
        failed_preparation = {"artifacts": artifact_evidence}
        cleanup_ok = cleanup_promotion_backups(failed_preparation)
        raise PromotionError(
            f"promotion backup preparation failed: {type(exc).__name__}",
            rollback_performed=False,
            artifacts=artifact_evidence,
            backup_cleanup_performed=cleanup_ok,
        ) from exc

    try:
        for _, source, target in artifacts:
            os.replace(source, target)
            fsync_directory(target.parent)
    except OSError as exc:
        rollback_ok = True
        rollback_errors: List[str] = []
        for name, evidence in artifact_evidence.items():
            backup = Path(evidence["backup"])
            target = Path(evidence["target"])
            try:
                restore_from_backup(backup, target, work_dir, mode=int(evidence["mode"]))
            except OSError as rollback_exc:
                rollback_ok = False
                rollback_errors.append(f"{name}:{type(rollback_exc).__name__}")
        suffix = "" if not rollback_errors else f"; rollback errors: {', '.join(rollback_errors)}"
        failed_promotion = {
            "performed": False,
            "rollback_performed": rollback_ok,
            "artifacts": artifact_evidence,
        }
        backup_cleanup_ok: Optional[bool] = None
        if rollback_ok:
            backup_cleanup_ok = cleanup_promotion_backups(failed_promotion)
        raise PromotionError(
            f"promotion failed: {type(exc).__name__}{suffix}",
            rollback_performed=rollback_ok,
            artifacts=artifact_evidence,
            backup_cleanup_performed=backup_cleanup_ok,
        ) from exc

    return {
        "performed": True,
        "rollback_performed": False,
        "backup_cleanup_performed": False,
        "artifacts": artifact_evidence,
    }


def rollback_promotion(
    promotion: Dict[str, Any],
    work_dir: Path,
) -> bool:
    rollback_ok = True
    try:
        artifacts = promotion["artifacts"]
    except (KeyError, TypeError):
        promotion["rollback_performed"] = False
        return False
    for evidence in artifacts.values():
        try:
            restore_from_backup(
                Path(evidence["backup"]),
                Path(evidence["target"]),
                work_dir,
                mode=int(evidence["mode"]),
            )
        except (OSError, KeyError, TypeError, ValueError):
            rollback_ok = False
    if rollback_ok:
        promotion["rollback_performed"] = True
        cleanup_promotion_backups(promotion)
        return True
    promotion["rollback_performed"] = False
    promotion["backup_cleanup_performed"] = False
    return False


def rollback_uncommitted_promotion(
    promotion: Optional[Dict[str, Any]],
    *,
    committed: bool,
    work_dir: Path,
    root: Path,
    timeout: float,
) -> bool:
    """Restore promoted artifacts and unstage them after any pre-commit escape.

    This is intentionally called from the outer exception boundary as well as the
    narrow failure paths. A new command or verification step added between promote
    and commit must not be able to bypass rollback simply because it raises in an
    unexpected place. Once a commit exists, preserving it is safer than rewriting
    Git history or restoring working bytes behind that commit.
    """
    if promotion is None:
        return True
    if committed:
        # The local commit has already passed exact parent/path/blob verification;
        # publication or later operational failures must preserve it, but its rollback
        # snapshots are no longer useful and must not accumulate indefinitely.
        return cleanup_promotion_backups(promotion)

    rollback_ok = bool(promotion.get("rollback_performed"))
    if not rollback_ok:
        rollback_ok = rollback_promotion(promotion, work_dir)

    try:
        reset = run_command(
            ["git", "reset", "--quiet", "HEAD", "--", *sorted(ALLOWED_GIT_PATHS)],
            root=root,
            timeout=timeout,
        )
        index_reset_ok = reset.returncode == 0
    except RefreshFailure:
        index_reset_ok = False
    promotion["index_reset_performed"] = index_reset_ok
    promotion["rollback_performed"] = rollback_ok
    return rollback_ok and index_reset_ok


def notify(command_text: str, message: str, *, root: Path, timeout: float = 30.0) -> bool:
    if not command_text.strip():
        return False
    try:
        command = shlex.split(command_text)
    except ValueError:
        return False
    if not command:
        return False
    try:
        proc = subprocess.run(
            [*command, message],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            timeout=max(1.0, min(timeout, 60.0)),
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def env_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def cleanup(paths: Sequence[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def generate_reports(
    *,
    root: Path,
    python: str,
    data: Path,
    data_label: Optional[Path],
    free_report: Path,
    valencia_report: Path,
    expected_data_sha256: str,
    timeout: float,
) -> Dict[str, Any]:
    """Generate both descriptive reports and bind them to the exact input bytes."""
    specs = (
        (str(DEFAULT_FREE_REPORT), "scripts/qa_free_db_completeness.py", free_report),
        (str(DEFAULT_VALENCIA_REPORT), "scripts/qa_valencia_source_audit.py", valencia_report),
    )
    expected_label = str(data_label or data)
    receipts: Dict[str, Any] = {}
    for report_name, script, output in specs:
        command = [python, script, "--data", str(data)]
        if data_label is not None:
            command.extend(["--data-label", str(data_label)])
        command.extend(["--out", str(output)])
        report_run = require_command("reports", command, root=root, timeout=timeout)
        if not output.is_file() or output.stat().st_size == 0:
            raise RefreshFailure("reports", f"{report_name} was not generated")
        try:
            payload = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RefreshFailure("reports", f"{report_name} JSON evidence invalid: {type(exc).__name__}") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("ok") is not True
            or payload.get("data_sha256") != expected_data_sha256
            or payload.get("data") != expected_label
            or sha256_file(data if data.is_absolute() else root / data) != expected_data_sha256
        ):
            raise RefreshFailure("reports", f"{report_name} did not prove the exact unchanged database")
        receipts[report_name] = {
            **compact_run(report_run),
            "data_sha256": expected_data_sha256,
            "artifact_sha256": sha256_file(output),
        }
    return receipts


def require_clean_main_state(root: Path, timeout: float, *, stage: str) -> str:
    try:
        branch, head, status = git_state(root, timeout)
    except RefreshFailure as exc:
        raise RefreshFailure(stage, exc.detail) from exc
    if branch != "main":
        raise RefreshFailure(stage, f"real refresh requires branch main, found {branch or 'detached HEAD'}")
    if status:
        raise RefreshFailure(stage, "real refresh requires a completely clean dedicated checkout")
    return head


def write_pending_refresh(
    path: Path,
    *,
    commit_sha: str,
    parent_sha: str,
    artifact_sha256: Dict[str, str],
) -> None:
    atomic_json(path, {
        "version": 1,
        "created_at": utc_now(),
        "commit_sha": commit_sha,
        "parent_sha": parent_sha,
        "artifact_sha256": artifact_sha256,
    })


def clear_pending_refresh(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
        fsync_directory(path.parent)
        return True
    except OSError:
        return False


def pending_refresh_binds_local_ahead(
    *,
    root: Path,
    pending_path: Path,
    local_head: str,
    remote_head: str,
    timeout: float,
) -> bool:
    """Allow exactly one local-only commit only when durable pending evidence proves it safe."""
    try:
        if (pending_path.stat().st_mode & 0o777) != 0o600:
            return False
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if (
        not isinstance(pending, dict)
        or pending.get("version") != 1
        or pending.get("commit_sha") != local_head
        or pending.get("parent_sha") != remote_head
    ):
        return False
    parent = run_command(
        ["git", "rev-parse", f"{local_head}^"],
        root=root,
        timeout=timeout,
    )
    if parent.returncode != 0 or parent.stdout.strip() != remote_head:
        return False
    changed = run_command(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", local_head],
        root=root,
        timeout=timeout,
    )
    changed_paths = set(filter(None, changed.stdout.splitlines())) if changed.returncode == 0 else set()
    if not changed_paths or not changed_paths <= ALLOWED_GIT_PATHS:
        return False
    artifact_hashes = pending.get("artifact_sha256")
    if not isinstance(artifact_hashes, dict) or set(artifact_hashes) != ALLOWED_GIT_PATHS:
        return False
    try:
        if any(sha256_file(root / path) != artifact_hashes[path] for path in ALLOWED_GIT_PATHS):
            return False
    except (OSError, KeyError, TypeError):
        return False
    return True


def sync_real_checkout(
    *,
    root: Path,
    pending_path: Path,
    timeout: float,
    push_requested: bool,
) -> Dict[str, Any]:
    """Fetch origin/main and establish one clean, trusted main revision."""
    head_before = require_clean_main_state(root, timeout, stage="preflight")
    tracking_before_proc = run_command(
        ["git", "rev-parse", "--verify", "refs/remotes/origin/main"],
        root=root,
        timeout=timeout,
    )
    tracking_before = (
        tracking_before_proc.stdout.strip() if tracking_before_proc.returncode == 0 else None
    )
    fetch = run_command(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "fetch",
            "--no-tags",
            "origin",
            "+refs/heads/main:refs/remotes/origin/main",
        ],
        root=root,
        timeout=timeout,
    )
    if fetch.returncode != 0:
        raise RefreshFailure("sync", f"git fetch origin/main exited {fetch.returncode}")
    head_after_fetch = require_clean_main_state(root, timeout, stage="sync")
    if head_after_fetch != head_before:
        raise RefreshFailure("sync", "local HEAD changed while origin/main was fetched")
    remote = require_command(
        "sync",
        ["git", "rev-parse", "refs/remotes/origin/main"],
        root=root,
        timeout=timeout,
    ).stdout.strip()
    if tracking_before is not None and tracking_before != remote:
        prior_tracking_is_ancestor = run_command(
            ["git", "merge-base", "--is-ancestor", tracking_before, remote],
            root=root,
            timeout=timeout,
        )
        if prior_tracking_is_ancestor.returncode == 1:
            restored = run_command(
                [
                    "git",
                    "update-ref",
                    "refs/remotes/origin/main",
                    tracking_before,
                    remote,
                ],
                root=root,
                timeout=timeout,
            )
            if restored.returncode != 0:
                raise RefreshFailure("sync", "origin/main rewound and its prior tracking tip could not be restored")
            raise RefreshFailure("sync", "fetched origin/main rewound or diverged from its prior tracking tip")
        if prior_tracking_is_ancestor.returncode != 0:
            raise RefreshFailure("sync", "could not validate origin/main tracking continuity")
    evidence: Dict[str, Any] = {
        "requested": True,
        "fetched": True,
        "fast_forwarded": False,
        "local_ahead": False,
        "preserved_push_retry": False,
        "preserved_push_published": False,
        "reexec": False,
        "head_before": head_before,
        "tracking_before": tracking_before,
        "origin_main_before": remote,
        "origin_main": remote,
        "head_after": head_before,
    }
    if head_before == remote:
        return evidence

    local_is_ancestor = run_command(
        ["git", "merge-base", "--is-ancestor", head_before, remote],
        root=root,
        timeout=timeout,
    )
    if local_is_ancestor.returncode == 0:
        merge = run_command(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "merge",
                "--ff-only",
                "--no-edit",
                "refs/remotes/origin/main",
            ],
            root=root,
            timeout=timeout,
        )
        if merge.returncode != 0:
            raise RefreshFailure("sync", f"fast-forward to origin/main exited {merge.returncode}")
        synced_head = require_clean_main_state(root, timeout, stage="sync")
        if synced_head != remote:
            raise RefreshFailure("sync", "fast-forward did not end at the fetched origin/main")
        evidence["fast_forwarded"] = True
        evidence["head_after"] = synced_head
        return evidence
    if local_is_ancestor.returncode not in (0, 1):
        raise RefreshFailure("sync", "could not compare local main with origin/main")

    remote_is_ancestor = run_command(
        ["git", "merge-base", "--is-ancestor", remote, head_before],
        root=root,
        timeout=timeout,
    )
    if remote_is_ancestor.returncode == 0:
        if not pending_refresh_binds_local_ahead(
            root=root,
            pending_path=pending_path,
            local_head=head_before,
            remote_head=remote,
            timeout=timeout,
        ):
            raise RefreshFailure("sync", "local main is ahead of origin/main without a verified refresh receipt")
        if not push_requested:
            raise RefreshFailure("sync", "verified local-ahead refresh requires --push before another build")
        publish = run_command(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "push",
                "origin",
                f"{head_before}:refs/heads/main",
            ],
            root=root,
            timeout=timeout,
        )
        if publish.returncode != 0:
            raise RefreshFailure("sync", f"preserved refresh retry push exited {publish.returncode}")
        remote_check = require_command(
            "sync",
            ["git", "ls-remote", "origin", "refs/heads/main"],
            root=root,
            timeout=timeout,
        ).stdout.strip().split()
        if len(remote_check) < 2 or remote_check[0] != head_before:
            raise RefreshFailure("sync", "origin/main did not accept the pinned preserved refresh commit")
        if require_clean_main_state(root, timeout, stage="sync") != head_before:
            raise RefreshFailure("sync", "local HEAD changed while retrying the preserved refresh push")
        if not clear_pending_refresh(pending_path):
            raise RefreshFailure("sync", "published preserved refresh but could not clear its pending evidence")
        evidence.update({
            "local_ahead": True,
            "preserved_push_retry": True,
            "preserved_push_published": True,
            "origin_main": head_before,
            "head_after": head_before,
        })
        return evidence
    if remote_is_ancestor.returncode not in (0, 1):
        raise RefreshFailure("sync", "could not compare origin/main with local main")
    raise RefreshFailure("sync", "local main and origin/main have diverged")


def reexec_synced_script(
    *,
    args: argparse.Namespace,
    root: Path,
    lock_path: Path,
    lock: Any,
    sync: Dict[str, Any],
) -> None:
    descriptor = lock.fileno()
    lock.flush()
    os.fsync(descriptor)
    os.set_inheritable(descriptor, True)
    state = {
        "version": REEXEC_STATE_VERSION,
        "root": str(root),
        "lock_path": str(lock_path),
        "lock_fd": descriptor,
        "expected_head": sync["head_after"],
        "sync": sync,
    }
    env = os.environ.copy()
    env[REEXEC_STATE_ENV] = json.dumps(state, separators=(",", ":"))
    command = [
        args.python,
        str(root / "scripts" / "auto_refresh.py"),
        *list(args.reexec_argv),
    ]
    try:
        os.execvpe(args.python, command, env)
    except OSError as exc:
        os.set_inheritable(descriptor, False)
        raise RefreshFailure("sync", f"could not reexec fetched refresh script: {type(exc).__name__}") from exc


def execute(args: argparse.Namespace) -> int:
    root = args.root.expanduser().resolve()
    work_dir = root / "data" / "cache" / "auto-refresh"
    # A passing dry-run must never mask the last real promotion monitored by the
    # health timer. Keep its evidence alongside, but on a separate receipt.
    receipt_path = work_dir / ("dry-run-last.json" if args.dry_run else "last.json")
    pending_path = work_dir / "pending-push.json"
    lock_path = args.lock_file.expanduser().resolve() if args.lock_file else work_dir / "refresh.lock"
    work_dir.mkdir(parents=True, exist_ok=True)
    raw_reexec_state = os.environ.pop(REEXEC_STATE_ENV, "")
    try:
        reexec_state = parse_reexec_state(
            raw_reexec_state,
            root=root,
            lock_path=lock_path,
            dry_run=args.dry_run,
        )
    except ValueError as exc:
        print(f"auto-refresh rejected reexec state: {exc}", file=sys.stderr)
        return 1
    lock = (
        inherit_lock(lock_path, int(reexec_state["lock_fd"]))
        if reexec_state is not None
        else acquire_lock(lock_path)
    )
    if lock is None:
        print(f"auto-refresh already running (lock: {lock_path})", file=sys.stderr)
        return TEMPFAIL

    run_id = f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-{os.getpid()}"
    candidate = work_dir / f"candidate-{run_id}.json"
    next_history = work_dir / f"history-{run_id}.jsonl"
    next_free_report = work_dir / f"free-db-completeness-{run_id}.json"
    next_valencia_report = work_dir / f"valencia-source-audit-{run_id}.json"
    staged_free_report = work_dir / f"staged-free-db-completeness-{run_id}.json"
    staged_valencia_report = work_dir / f"staged-valencia-source-audit-{run_id}.json"
    canonical = root / DEFAULT_DB
    canonical_history = root / DEFAULT_HISTORY
    canonical_free_report = root / DEFAULT_FREE_REPORT
    canonical_valencia_report = root / DEFAULT_VALENCIA_REPORT
    canonical_artifacts = {
        str(DEFAULT_DB): canonical,
        str(DEFAULT_HISTORY): canonical_history,
        str(DEFAULT_FREE_REPORT): canonical_free_report,
        str(DEFAULT_VALENCIA_REPORT): canonical_valencia_report,
    }
    policy = root / DEFAULT_POLICY
    marker = root / args.dedicated_marker
    started = utc_now()
    receipt: Dict[str, Any] = {
        "version": 1,
        "run_id": run_id,
        "started_at": started,
        "finished_at": None,
        "mode": "dry-run" if args.dry_run else "real",
        "ok": False,
        "stage": "preflight",
        "root": str(root),
        "candidate_sha256": None,
        "baseline_sha256": None,
        "canonical_sha256": None,
        "history_baseline_sha256": None,
        "history_canonical_sha256": None,
        "delta": None,
        "canary": None,
        "reports": None,
        "sync": {
            "requested": not args.dry_run,
            "fetched": False,
            "fast_forwarded": False,
            "local_ahead": False,
            "preserved_push_retry": False,
            "preserved_push_published": False,
            "reexec": bool(reexec_state),
        },
        "promotion": {"performed": False, "rollback_performed": False},
        "commit": {"created": False, "preserved": False, "sha": None, "no_changes": False},
        "push": {"requested": bool(args.push), "ok": None},
        "notification_sent": False,
    }
    initial_head: Optional[str] = None
    baseline_sha: Optional[str] = None
    history_sha: Optional[str] = None
    baseline_artifact_sha256: Dict[str, str] = {}
    gated_artifact_sha256: Dict[str, str] = {}
    promotion: Optional[Dict[str, Any]] = None
    committed = False
    gated_head: Optional[str] = None

    try:
        if not root.is_dir():
            raise RefreshFailure("preflight", "root does not exist")
        missing = [path for path in canonical_artifacts.values() if not path.is_file()]
        if missing or not policy.is_file():
            raise RefreshFailure("preflight", "canonical DB, canary history, reports, or source policy is missing")
        if not args.dry_run:
            if not marker.is_file():
                raise RefreshFailure("preflight", f"dedicated-checkout marker is missing: {marker.name}")
            if reexec_state is not None:
                synced_head = require_clean_main_state(root, args.command_timeout, stage="sync")
                if synced_head != reexec_state["expected_head"]:
                    raise RefreshFailure("sync", "HEAD changed between fast-forward and fetched-script reexec")
                sync_evidence = dict(reexec_state["sync"])
                sync_evidence["reexec"] = True
            else:
                sync_evidence = sync_real_checkout(
                    root=root,
                    pending_path=pending_path,
                    timeout=args.command_timeout,
                    push_requested=bool(args.push),
                )
                if sync_evidence["fast_forwarded"]:
                    reexec_synced_script(
                        args=args,
                        root=root,
                        lock_path=lock_path,
                        lock=lock,
                        sync=sync_evidence,
                    )
                synced_head = require_clean_main_state(root, args.command_timeout, stage="sync")
                if synced_head != sync_evidence["head_after"]:
                    raise RefreshFailure("sync", "clean main changed after repository synchronization")
            initial_head = synced_head
            receipt["sync"] = sync_evidence
            receipt["branch"] = "main"
            receipt["head_initial"] = initial_head
        else:
            # Git metadata is informational only in dry-run mode; a shared checkout may
            # intentionally contain unrelated work.
            probe = run_command(["git", "rev-parse", "HEAD"], root=root, timeout=args.command_timeout)
            if probe.returncode == 0:
                receipt["head_initial"] = probe.stdout.strip()

        baseline_sha = sha256_file(canonical)
        history_sha = sha256_file(canonical_history)
        baseline_artifact_sha256 = {
            name: sha256_file(path) for name, path in canonical_artifacts.items()
        }
        receipt["baseline_sha256"] = baseline_sha
        receipt["history_baseline_sha256"] = history_sha
        receipt["artifact_baseline_sha256"] = baseline_artifact_sha256
        if any(
            work_dir.stat().st_dev != path.parent.stat().st_dev
            for path in canonical_artifacts.values()
        ):
            raise RefreshFailure("preflight", "ignored work area and canonical artifacts must share one filesystem")

        receipt["stage"] = "build"
        build = require_command(
            "build",
            [args.python, "scripts/ingest_sources.py", "--prior", str(canonical), "--out", str(candidate)],
            root=root,
            timeout=args.command_timeout,
        )
        receipt["build"] = compact_run(build)
        if not candidate.is_file() or candidate.stat().st_size == 0:
            raise RefreshFailure("build", "ingestion did not create a non-empty candidate")
        try:
            from scripts.station_identity import preserve_record_ids
        except ModuleNotFoundError:
            from station_identity import preserve_record_ids
        reconciled, reference_report = preserve_record_ids(
            json.loads(canonical.read_text()), json.loads(candidate.read_text()))
        receipt["identity_reconciliation"] = reference_report
        if reference_report["unresolved_missing_ids"] or reference_report["unresolved_alias_ids"]:
            raise RefreshFailure("references", "unresolved charger references block promotion")
        atomic_json(candidate, reconciled)
        candidate_sha = sha256_file(candidate)
        receipt["candidate_sha256"] = candidate_sha

        receipt["stage"] = "delta"
        delta = require_command(
            "delta",
            [
                args.python,
                "scripts/qa_ingestion_delta.py",
                "--baseline",
                str(canonical),
                "--rebuilt",
                str(candidate),
                "--policy-exclusions",
                str(policy),
            ],
            root=root,
            timeout=args.command_timeout,
        )
        try:
            delta_json = parse_last_json_object(delta.stdout)
        except (ValueError, json.JSONDecodeError) as exc:
            raise RefreshFailure("delta", f"delta JSON evidence invalid: {type(exc).__name__}") from exc
        missing_ids = ((delta_json.get("delta") or {}).get("missing_ids"))
        if delta_json.get("ok") is not True or missing_ids != 0:
            raise RefreshFailure("delta", f"delta gate rejected candidate (ok={delta_json.get('ok')!r}, missing_ids={missing_ids!r})")
        receipt["delta"] = {
            "returncode": delta.returncode,
            "ok": True,
            "missing_ids": missing_ids,
            "candidate_sha256": candidate_sha,
        }

        copy_fsync(canonical_history, next_history, mode=canonical_history.stat().st_mode & 0o777)
        receipt["stage"] = "canary"
        canary = require_command(
            "canary",
            [
                args.python,
                "scripts/qa_ingestion_canary.py",
                "--candidate",
                str(candidate),
                "--baseline",
                str(canonical),
                "--history-out",
                str(next_history),
                "--policy-exclusions",
                str(policy),
            ],
            root=root,
            timeout=args.command_timeout,
        )
        try:
            canary_json = parse_last_json_object(canary.stdout)
        except (ValueError, json.JSONDecodeError) as exc:
            raise RefreshFailure("canary", f"canary JSON evidence invalid: {type(exc).__name__}") from exc
        candidate_after = sha256_file(candidate)
        expected_evidence = (
            canary_json.get("ok") is True
            and canary_json.get("candidate_reused") is True
            and canary_json.get("candidate_sha256") == candidate_sha
            and canary_json.get("candidate_sha256_after") == candidate_sha
            and canary_json.get("baseline_sha256") == baseline_sha
            and candidate_after == candidate_sha
        )
        if not expected_evidence:
            raise RefreshFailure("canary", "canary did not prove the exact unchanged candidate and baseline hashes")
        next_history_sha = sha256_file(next_history)
        receipt["canary"] = {
            "returncode": canary.returncode,
            "ok": True,
            "candidate_reused": True,
            "candidate_sha256": candidate_sha,
            "baseline_sha256": baseline_sha,
            "history_sha256": next_history_sha,
        }

        if args.dry_run:
            receipt["stage"] = "reports"
            report_receipts = generate_reports(
                root=root,
                python=args.python,
                data=candidate,
                data_label=DEFAULT_DB,
                free_report=next_free_report,
                valencia_report=next_valencia_report,
                expected_data_sha256=candidate_sha,
                timeout=args.command_timeout,
            )
            receipt["reports"] = report_receipts
            gated_artifact_sha256 = {
                str(DEFAULT_DB): candidate_sha,
                str(DEFAULT_HISTORY): next_history_sha,
                str(DEFAULT_FREE_REPORT): report_receipts[str(DEFAULT_FREE_REPORT)]["artifact_sha256"],
                str(DEFAULT_VALENCIA_REPORT): report_receipts[str(DEFAULT_VALENCIA_REPORT)]["artifact_sha256"],
            }
            receipt["stage"] = "revalidate"
            if any(
                sha256_file(path) != baseline_artifact_sha256[name]
                for name, path in canonical_artifacts.items()
            ):
                raise RefreshFailure("revalidate", "canonical DB, history, or reports changed while dry-run gates were running")
            temporary_artifacts = {
                str(DEFAULT_DB): candidate,
                str(DEFAULT_HISTORY): next_history,
                str(DEFAULT_FREE_REPORT): next_free_report,
                str(DEFAULT_VALENCIA_REPORT): next_valencia_report,
            }
            if any(
                sha256_file(temporary_artifacts[name]) != expected_sha
                for name, expected_sha in gated_artifact_sha256.items()
            ):
                raise RefreshFailure("revalidate", "dry-run gated artifacts changed after report generation")
            receipt["ok"] = True
            receipt["stage"] = "complete"
            receipt["canonical_sha256"] = sha256_file(canonical)
            receipt["history_canonical_sha256"] = sha256_file(canonical_history)
            receipt["artifact_canonical_sha256"] = {
                name: sha256_file(path) for name, path in canonical_artifacts.items()
            }
            print("dry-run passed: exact candidate cleared delta, canary, and report gates; no promotion or Git mutation")
            return_code = 0
        else:
            receipt["stage"] = "revalidate"
            branch, current_head, status = git_state(root, args.command_timeout)
            if branch != "main" or current_head != initial_head or status:
                raise RefreshFailure("revalidate", "branch, HEAD, or clean worktree changed while gates were running")
            if any(
                sha256_file(path) != baseline_artifact_sha256[name]
                for name, path in canonical_artifacts.items()
            ):
                raise RefreshFailure("revalidate", "canonical DB, history, or reports changed while gates were running")
            if sha256_file(candidate) != candidate_sha or sha256_file(next_history) != next_history_sha:
                raise RefreshFailure("revalidate", "gated DB or history changed before promotion")

            # Back up all four canonical artifacts in one transaction before exposing
            # the new DB. The reports are initially replaced byte-for-byte, then
            # regenerated from the promoted canonical path while those backups remain
            # live. Any generator/replace/pre-commit escape restores the full set.
            copy_fsync(
                canonical_free_report,
                staged_free_report,
                mode=canonical_free_report.stat().st_mode & 0o777,
            )
            copy_fsync(
                canonical_valencia_report,
                staged_valencia_report,
                mode=canonical_valencia_report.stat().st_mode & 0o777,
            )
            initial_promotion_artifacts = {
                str(DEFAULT_DB): candidate,
                str(DEFAULT_HISTORY): next_history,
                str(DEFAULT_FREE_REPORT): staged_free_report,
                str(DEFAULT_VALENCIA_REPORT): staged_valencia_report,
            }

            receipt["stage"] = "promote"
            try:
                promotion = promote_transaction(
                    [
                        (name, initial_promotion_artifacts[name], canonical_artifacts[name])
                        for name in sorted(ALLOWED_GIT_PATHS)
                    ],
                    work_dir,
                )
            except PromotionError as exc:
                receipt["promotion"] = {
                    "performed": False,
                    "rollback_performed": exc.rollback_performed,
                    "backup_cleanup_performed": exc.backup_cleanup_performed,
                    "artifacts": exc.artifacts,
                }
                raise RefreshFailure("promote", str(exc)) from exc
            receipt["promotion"] = promotion
            if sha256_file(canonical) != candidate_sha or sha256_file(canonical_history) != next_history_sha:
                rollback_promotion(promotion, work_dir)
                raise RefreshFailure("promote", "promoted DB/history hashes did not match the gated artifacts")

            receipt["stage"] = "reports"
            report_receipts = generate_reports(
                root=root,
                python=args.python,
                data=DEFAULT_DB,
                data_label=None,
                free_report=next_free_report,
                valencia_report=next_valencia_report,
                expected_data_sha256=candidate_sha,
                timeout=args.command_timeout,
            )
            if sha256_file(canonical) != candidate_sha or sha256_file(canonical_history) != next_history_sha:
                raise RefreshFailure("reports", "promoted DB/history changed during report generation")
            os.replace(next_free_report, canonical_free_report)
            fsync_directory(canonical_free_report.parent)
            os.replace(next_valencia_report, canonical_valencia_report)
            fsync_directory(canonical_valencia_report.parent)
            receipt["reports"] = report_receipts
            gated_artifact_sha256 = {
                str(DEFAULT_DB): candidate_sha,
                str(DEFAULT_HISTORY): next_history_sha,
                str(DEFAULT_FREE_REPORT): report_receipts[str(DEFAULT_FREE_REPORT)]["artifact_sha256"],
                str(DEFAULT_VALENCIA_REPORT): report_receipts[str(DEFAULT_VALENCIA_REPORT)]["artifact_sha256"],
            }
            if any(
                sha256_file(canonical_artifacts[name]) != expected_sha
                for name, expected_sha in gated_artifact_sha256.items()
            ):
                raise RefreshFailure("reports", "promoted report hashes did not match the exact generated artifacts")

            receipt["stage"] = "git-allowlist"
            try:
                require_allowed_status(root, args.command_timeout, stage="git-allowlist")
            except RefreshFailure:
                rollback_promotion(promotion, work_dir)
                raise

            # Capture the Git blob identities only after the promoted bytes have been
            # matched to the exact gated SHA-256 values. These become the post-add index
            # contract, closing the otherwise-allowed race on all four allowlisted paths.
            receipt["stage"] = "git-verify"
            if any(
                sha256_file(canonical_artifacts[name]) != expected_sha
                for name, expected_sha in gated_artifact_sha256.items()
            ):
                rollback_promotion(promotion, work_dir)
                raise RefreshFailure("git-verify", "allowlisted artifacts changed before staging")
            expected_blobs = {
                path: git_blob_oid(root, args.command_timeout, path, staged=False)
                for path in sorted(ALLOWED_GIT_PATHS)
            }

            add = run_command(
                ["git", "add", "--", *sorted(ALLOWED_GIT_PATHS)],
                root=root,
                timeout=args.command_timeout,
            )
            if add.returncode != 0:
                rollback_promotion(promotion, work_dir)
                run_command(["git", "reset", "--quiet", "HEAD", "--", *sorted(ALLOWED_GIT_PATHS)], root=root, timeout=args.command_timeout)
                raise RefreshFailure("commit", f"git add exited {add.returncode}")
            try:
                require_allowed_status(root, args.command_timeout, stage="git-allowlist")
            except RefreshFailure:
                rollback_promotion(promotion, work_dir)
                run_command(["git", "reset", "--quiet", "HEAD", "--", *sorted(ALLOWED_GIT_PATHS)], root=root, timeout=args.command_timeout)
                raise
            working_hashes_match = all(
                sha256_file(canonical_artifacts[name]) == expected_sha
                for name, expected_sha in gated_artifact_sha256.items()
            )
            staged_blobs_match = all(
                git_blob_oid(root, args.command_timeout, path, staged=True) == expected_blob
                for path, expected_blob in expected_blobs.items()
            )
            if not working_hashes_match or not staged_blobs_match:
                rollback_promotion(promotion, work_dir)
                run_command(["git", "reset", "--quiet", "HEAD", "--", *sorted(ALLOWED_GIT_PATHS)], root=root, timeout=args.command_timeout)
                raise RefreshFailure("git-verify", "working or staged artifacts no longer match the gated bytes")

            staged = run_command(["git", "diff", "--cached", "--quiet", "--", *sorted(ALLOWED_GIT_PATHS)], root=root, timeout=args.command_timeout)
            if staged.returncode not in (0, 1):
                rollback_promotion(promotion, work_dir)
                run_command(["git", "reset", "--quiet", "HEAD", "--", *sorted(ALLOWED_GIT_PATHS)], root=root, timeout=args.command_timeout)
                raise RefreshFailure("commit", f"git diff --cached exited {staged.returncode}")
            if staged.returncode == 1:
                receipt["stage"] = "commit"
                message = f"data: weekly intelligence refresh ({dt.datetime.now(dt.timezone.utc):%Y-%m-%d}, delta+canary+reports gated)"
                # Commit the already-verified index. Passing paths here would make Git
                # re-read working-tree bytes and reopen the allowlisted-path race.
                commit_proc = run_command(
                    ["git", "-c", "core.hooksPath=/dev/null", "commit", "-m", message],
                    root=root,
                    timeout=args.command_timeout,
                )
                post_commit_head = git_output(root, args.command_timeout, "rev-parse", "HEAD")
                if commit_proc.returncode != 0:
                    raise RefreshFailure("commit", f"git commit exited {commit_proc.returncode}")
                if post_commit_head == initial_head:
                    raise RefreshFailure("commit", "git commit reported success without creating the gated commit")
                parent_head = require_command(
                    "commit",
                    ["git", "rev-parse", f"{post_commit_head}^"],
                    root=root,
                    timeout=args.command_timeout,
                ).stdout.strip()
                changed_paths_proc = require_command(
                    "commit",
                    ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", post_commit_head],
                    root=root,
                    timeout=args.command_timeout,
                )
                changed_paths = set(filter(None, changed_paths_proc.stdout.splitlines()))
                if parent_head != initial_head or not changed_paths or not changed_paths <= ALLOWED_GIT_PATHS:
                    raise RefreshFailure("commit", "created HEAD was not the exclusive allowlisted refresh commit")
                verify_committed_artifacts(
                    root,
                    args.command_timeout,
                    expected_sha256=gated_artifact_sha256,
                    expected_blobs=expected_blobs,
                )
                # Only a successful command whose exact parent, changed paths, working
                # bytes, and HEAD blobs all match the gate may suppress rollback.
                committed = True
                receipt["commit"] = {
                    "created": True,
                    "preserved": False,
                    "sha": post_commit_head,
                }
            else:
                receipt["commit"]["no_changes"] = True

            verify_committed_artifacts(
                root,
                args.command_timeout,
                expected_sha256=gated_artifact_sha256,
                expected_blobs=expected_blobs,
            )

            gated_head = str(receipt["commit"].get("sha") or initial_head or "")
            current_head = git_output(root, args.command_timeout, "rev-parse", "HEAD")
            if not gated_head or current_head != gated_head:
                raise RefreshFailure("git-verify", "HEAD diverged from the gated commit before publication")
            if receipt["commit"].get("created"):
                parent_head = git_output(root, args.command_timeout, "rev-parse", f"{gated_head}^")
                changed_paths_proc = require_command(
                    "git-verify",
                    ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", gated_head],
                    root=root,
                    timeout=args.command_timeout,
                )
                changed_paths = set(filter(None, changed_paths_proc.stdout.splitlines()))
                if parent_head != initial_head or not changed_paths or not changed_paths <= ALLOWED_GIT_PATHS:
                    raise RefreshFailure("git-verify", "gated commit topology or changed paths were not exclusive to the verified refresh")
            receipt["commit"]["verified_sha"] = gated_head

            if args.push:
                receipt["stage"] = "push"
                if git_output(root, args.command_timeout, "rev-parse", "HEAD") != gated_head:
                    raise RefreshFailure("push", "HEAD diverged from the gated commit immediately before publication")
                push_proc = run_command(
                    [
                        "git",
                        "-c",
                        "core.hooksPath=/dev/null",
                        "push",
                        "origin",
                        f"{gated_head}:refs/heads/main",
                    ],
                    root=root,
                    timeout=args.command_timeout,
                )
                receipt["push"]["ok"] = push_proc.returncode == 0
                if push_proc.returncode != 0:
                    receipt["commit"]["preserved"] = bool(receipt["commit"]["created"])
                    if receipt["commit"]["created"]:
                        try:
                            write_pending_refresh(
                                pending_path,
                                commit_sha=gated_head,
                                parent_sha=str(initial_head),
                                artifact_sha256=gated_artifact_sha256,
                            )
                        except OSError as exc:
                            raise RefreshFailure(
                                "push",
                                f"git push exited {push_proc.returncode} and pending evidence could not be written: {type(exc).__name__}",
                            ) from exc
                    raise RefreshFailure("push", f"git push exited {push_proc.returncode}; local commit was preserved")
                receipt["push"]["published_sha"] = gated_head
                if git_output(root, args.command_timeout, "rev-parse", "HEAD") != gated_head:
                    raise RefreshFailure("push", "local HEAD diverged while the pinned gated commit was published")
                remote = require_command(
                    "push",
                    ["git", "ls-remote", "origin", "refs/heads/main"],
                    root=root,
                    timeout=args.command_timeout,
                )
                remote_fields = remote.stdout.strip().split()
                if len(remote_fields) < 2 or remote_fields[0] != gated_head:
                    raise RefreshFailure("push", "origin/main does not resolve to the gated commit after publication")
                verify_committed_artifacts(
                    root,
                    args.command_timeout,
                    expected_sha256=gated_artifact_sha256,
                    expected_blobs=expected_blobs,
                )

            if receipt["commit"].get("created") and not args.push:
                receipt["stage"] = "pending-push"
                try:
                    write_pending_refresh(
                        pending_path,
                        commit_sha=gated_head,
                        parent_sha=str(initial_head),
                        artifact_sha256=gated_artifact_sha256,
                    )
                except OSError as exc:
                    raise RefreshFailure("pending-push", f"could not record verified local commit: {type(exc).__name__}") from exc
            elif args.push and not clear_pending_refresh(pending_path):
                raise RefreshFailure("push", "published refresh but could not clear pending evidence")

            receipt["stage"] = "cleanup"
            if promotion is None or not cleanup_promotion_backups(promotion):
                raise RefreshFailure("cleanup", "verified refresh rollback backups could not be removed")
            receipt["canonical_sha256"] = sha256_file(canonical)
            receipt["history_canonical_sha256"] = sha256_file(canonical_history)
            receipt["artifact_canonical_sha256"] = {
                name: sha256_file(path) for name, path in canonical_artifacts.items()
            }
            receipt["head_final"] = git_output(root, args.command_timeout, "rev-parse", "HEAD")
            receipt["ok"] = True
            receipt["stage"] = "complete"
            print(f"refresh complete: promoted {candidate_sha[:12]} and committed locally")
            return_code = 0

    except RefreshFailure as exc:
        if not args.dry_run:
            rollback_uncommitted_promotion(
                promotion,
                committed=committed,
                work_dir=work_dir,
                root=root,
                timeout=args.command_timeout,
            )
        receipt["ok"] = False
        receipt["stage"] = exc.stage
        receipt["detail"] = exc.detail
        if baseline_sha is not None and canonical.is_file():
            receipt["canonical_sha256"] = sha256_file(canonical)
        if history_sha is not None and canonical_history.is_file():
            receipt["history_canonical_sha256"] = sha256_file(canonical_history)
        if baseline_artifact_sha256:
            receipt["artifact_canonical_sha256"] = {
                name: sha256_file(path)
                for name, path in canonical_artifacts.items()
                if path.is_file()
            }
        if not args.dry_run:
            message = f"Volta auto-refresh failed at {exc.stage}: {exc.detail}"
            receipt["notification_sent"] = notify(os.environ.get("VOLTA_NOTIFY_CMD", ""), message, root=root)
        print(f"auto-refresh failed at {exc.stage}: {exc.detail}", file=sys.stderr)
        return_code = 1
    except Exception as exc:  # fail closed and leave a minimal class-only public detail
        if not args.dry_run:
            rollback_uncommitted_promotion(
                promotion,
                committed=committed,
                work_dir=work_dir,
                root=root,
                timeout=args.command_timeout,
            )
        receipt["ok"] = False
        receipt["stage"] = receipt.get("stage") or "internal"
        receipt["detail"] = f"unexpected {type(exc).__name__}"
        if baseline_artifact_sha256:
            receipt["artifact_canonical_sha256"] = {
                name: sha256_file(path)
                for name, path in canonical_artifacts.items()
                if path.is_file()
            }
        if not args.dry_run:
            message = f"Volta auto-refresh failed at {receipt['stage']}: {type(exc).__name__}"
            receipt["notification_sent"] = notify(os.environ.get("VOLTA_NOTIFY_CMD", ""), message, root=root)
        print(f"auto-refresh failed at {receipt['stage']}: {type(exc).__name__}", file=sys.stderr)
        return_code = 1
    finally:
        receipt["finished_at"] = utc_now()
        cleanup([
            candidate,
            next_history,
            next_free_report,
            next_valencia_report,
            staged_free_report,
            staged_valencia_report,
        ])
        try:
            atomic_json(receipt_path, receipt)
        except OSError as exc:
            print(f"could not write auto-refresh receipt: {type(exc).__name__}", file=sys.stderr)
            return_code = 1
        release_lock(lock)

    return return_code


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description="Build, gate, and transactionally promote Volta intelligence data.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--dry-run", action="store_true", help="Run build and gates without promote, commit, push, or notify.")
    push_group = parser.add_mutually_exclusive_group()
    push_group.add_argument("--push", dest="push", action="store_true", help="Push the successful local commit to origin/main.")
    push_group.add_argument("--no-push", dest="push", action="store_false")
    parser.set_defaults(push=None)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--command-timeout", type=float, default=1800.0)
    parser.add_argument("--lock-file", type=Path)
    parser.add_argument("--dedicated-marker", type=Path, default=DEFAULT_MARKER)
    args = parser.parse_args(raw_argv)
    args.reexec_argv = raw_argv
    if args.command_timeout <= 0:
        parser.error("--command-timeout must be positive")
    if args.push is None:
        args.push = env_true("VOLTA_REFRESH_PUSH")
    if args.dry_run:
        args.push = False
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    return execute(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
