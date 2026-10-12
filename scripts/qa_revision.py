#!/usr/bin/env python3
"""Deterministic revision binding for the durable regression report.

The 30-check report is only current when its tested source/configuration, canonical
data, and QA ledgers still match the workspace. Generated evidence and mutable
network caches are intentionally excluded because the suite itself updates them.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator

from runtime_closure import RUNTIME_FILES


ROOT = Path(__file__).resolve().parents[1]

REVISION_FILES = (
    *RUNTIME_FILES,
    "package.json",
    "package-lock.json",
    ".node-version",
    ".python-version",
    "Dockerfile",
    "fly.toml",
    "DEPLOY.md",
    "frontend/index.html",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/tsconfig.app.json",
    "frontend/tsconfig.json",
    "frontend/tsconfig.node.json",
    "frontend/vite.config.ts",
    "ios/Volta/Volta.xcodeproj/project.pbxproj",
    "qa/features.csv",
    "qa/test-cases.csv",
    "qa/defects.csv",
)

REVISION_TREES = (
    "contracts",
    "scripts",
    "tests",
    "frontend/src",
    "frontend/public",
    "frontend/dist",
    "ios/Volta/Volta",
    "ios/Volta/VoltaTests",
    "ios/Volta/VoltaUITests",
    "ios/Volta/Volta.xcodeproj/xcshareddata",
    "docs/appstore",
    ".github/workflows",
)

IGNORED_NAMES = {".DS_Store"}
IGNORED_PARTS = {"__pycache__", "node_modules", "xcuserdata"}
IGNORED_SUFFIXES = {".pyc", ".xcuserstate"}


def _recordset_sha256(records: Iterable[Dict[str, Any]]) -> str:
    ordered = sorted(
        (record for record in records if isinstance(record, dict)),
        key=lambda record: str(record.get("id") or ""),
    )
    encoded = json.dumps(
        ordered,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_recordset_sha256(root: Path = ROOT) -> str:
    path = root / "data" / "free_intelligence.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise ValueError(f"{path} records must be a list")
    computed = _recordset_sha256(records)
    stored = payload.get("recordset_sha256")
    if stored not in (None, computed):
        raise ValueError(f"{path} stored recordset_sha256 does not match its records")
    return computed


def _csv_rows(path: Path) -> list[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def current_qa_counts(root: Path = ROOT) -> Dict[str, Any]:
    features = _csv_rows(root / "qa" / "features.csv")
    tests = _csv_rows(root / "qa" / "test-cases.csv")
    defects = _csv_rows(root / "qa" / "defects.csv")
    return {
        "features": len(features),
        "test_cases": len(tests),
        "test_results": dict(sorted(Counter(row.get("Last Result") or "" for row in tests).items())),
        "defects": len(defects),
        "defect_statuses": dict(sorted(Counter(row.get("Status") or "" for row in defects).items())),
    }


def _include_path(path: Path) -> bool:
    return (
        path.is_file()
        and path.name not in IGNORED_NAMES
        and path.suffix not in IGNORED_SUFFIXES
        and not any(part in IGNORED_PARTS for part in path.parts)
    )


def regression_input_paths(root: Path = ROOT) -> Iterator[Path]:
    paths = set()
    for relative in REVISION_FILES:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"required regression input is missing: {path}")
        paths.add(path)
    for relative in REVISION_TREES:
        directory = root / relative
        if not directory.exists():
            continue
        paths.update(path for path in directory.rglob("*") if _include_path(path))
    yield from sorted(paths, key=lambda path: path.relative_to(root).as_posix())


def regression_input_sha256(root: Path = ROOT) -> str:
    digest = hashlib.sha256()
    for path in regression_input_paths(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def current_regression_revision(root: Path = ROOT) -> Dict[str, Any]:
    return {
        "input_sha256": regression_input_sha256(root),
        "canonical_recordset_sha256": canonical_recordset_sha256(root),
        "qa_counts": current_qa_counts(root),
    }
