"""Reviewed station-ID retirements honored by the ingestion delta gate.

The gate requires every baseline station ID to survive a refresh, because saved
references and community history point at those IDs. An upstream source can still
legitimately change an ID: a coordinate correction changes a coordinate-derived ID,
and a station can be withdrawn. Such a case is recorded here, in Git, with its reason,
evidence and approval. The gate honors an entry only when the evidence it can check
still holds; see `qa_ingestion_delta.id_delta`.

The file is strict. Any malformed content raises RetirementError, and the caller must
treat that as a gate failure rather than ignoring the list.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

VERSION = 1
TOP_LEVEL_KEYS = {"version", "retirements"}
ENTRY_KEYS = {"id", "successor_id", "retired_on", "reason", "evidence", "approved_by"}
REQUIRED_TEXT = ("reason", "evidence", "approved_by")


class RetirementError(ValueError):
    """The retirement list is missing or malformed."""


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or any(c.isspace() for c in value):
        raise RetirementError(f"{label} must be a non-empty string without whitespace")
    return value


def _entry(item: Any, position: int) -> dict[str, Any]:
    where = f"retirements[{position}]"
    if not isinstance(item, dict):
        raise RetirementError(f"{where} must be an object")
    unknown = sorted(set(item) - ENTRY_KEYS)
    if unknown:
        raise RetirementError(f"{where} has unknown keys: {unknown}")
    entry: dict[str, Any] = {"id": _identifier(item.get("id"), f"{where}.id")}
    if "successor_id" in item:
        successor = _identifier(item["successor_id"], f"{where}.successor_id")
        if successor == entry["id"]:
            raise RetirementError(f"{where}.successor_id must differ from id")
        entry["successor_id"] = successor
    retired_on = item.get("retired_on")
    try:
        if not isinstance(retired_on, str) or len(retired_on) != 10:
            raise ValueError
        date.fromisoformat(retired_on)
    except ValueError:
        raise RetirementError(f"{where}.retired_on must be a YYYY-MM-DD date") from None
    entry["retired_on"] = retired_on
    for field in REQUIRED_TEXT:
        value = item.get(field)
        if not isinstance(value, str) or not value.strip():
            raise RetirementError(f"{where}.{field} must be a non-empty string")
        entry[field] = value.strip()
    return entry


def load_retirements(path: Path, *, required: bool = False) -> dict[str, dict[str, Any]]:
    """Return the validated retirements keyed by retired ID.

    A missing file is an empty list unless `required`; an explicitly named path that
    is absent is more likely a typo than an intent to retire nothing.
    """
    path = Path(path)
    if not path.is_file():
        if required:
            raise RetirementError(f"retirement list not found: {path}")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RetirementError(f"retirement list is not readable JSON: {exc}") from None
    if not isinstance(payload, dict) or set(payload) - TOP_LEVEL_KEYS:
        raise RetirementError(f"retirement list must be an object with only {sorted(TOP_LEVEL_KEYS)}")
    if payload.get("version") != VERSION:
        raise RetirementError(f"retirement list version must be {VERSION}")
    items = payload.get("retirements")
    if not isinstance(items, list):
        raise RetirementError("retirements must be a list")
    loaded: dict[str, dict[str, Any]] = {}
    for position, item in enumerate(items):
        entry = _entry(item, position)
        if entry["id"] in loaded:
            raise RetirementError(f"duplicate retired id: {entry['id']}")
        loaded[entry["id"]] = entry
    chained = sorted(e["successor_id"] for e in loaded.values() if e.get("successor_id") in loaded)
    if chained:
        raise RetirementError(f"a successor cannot itself be retired: {chained}")
    return loaded
