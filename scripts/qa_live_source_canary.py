#!/usr/bin/env python3
"""Live/provider-source canary for Volta Go.

This checks the semantic live layer that route-ready decisions depend on:
MapaREVE response shape, Gaia public-page connector evidence, coverage metadata,
and the named-provider strategy contract. It writes compact JSONL history so
provider regressions are visible across runs.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "http://127.0.0.1:8773"
DEFAULT_HISTORY = ROOT / "qa" / "live-source-canary-history.jsonl"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fetch_json(url: str, timeout: float) -> Dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "Volta Go QA live-source canary"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def read_history(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def latest_ok(history: Iterable[Dict[str, Any]]) -> Dict[str, Any] | None:
    for row in reversed(list(history)):
        if row.get("ok"):
            return row
    return None


def append_history(path: Path, row: Dict[str, Any], keep: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = read_history(path)
    rows.append(row)
    if keep > 0:
        rows = rows[-keep:]
    with path.open("w", encoding="utf-8") as f:
        for item in rows:
            f.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def provider_by_name(sources: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    apps = ((sources.get("requested_source_strategy") or {}).get("requested_apps") or [])
    return {str(app.get("name") or ""): app for app in apps if isinstance(app, dict)}


def source_status(apps: Dict[str, Dict[str, Any]], needle: str) -> str:
    needle_l = needle.lower()
    for name, app in apps.items():
        if needle_l in name.lower():
            return str(app.get("status") or "")
    return ""


def is_rate_limited_warning(chargers: Dict[str, Any]) -> bool:
    warning = " ".join(
        str(value or "")
        for value in [
            chargers.get("warning"),
            ((chargers.get("meta") or {}).get("coverage") or {}).get("warning"),
        ]
    )
    return "429" in warning or "too many requests" in warning.lower()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--history-out", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--history-keep", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--min-raw-ratio", type=float, default=0.5)
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    failures: List[str] = []
    checked_at = utc_now()
    chargers_url = f"{base}/api/chargers?{urllib.parse.urlencode({'max_price': '0.25', 'include_leads': 'true', 'force': 'true'})}"
    sources_url = f"{base}/api/sources"

    try:
        chargers = fetch_json(chargers_url, args.timeout)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        chargers = {}
        failures.append(f"chargers API failed: {type(exc).__name__}: {exc}")

    try:
        sources = fetch_json(sources_url, args.timeout)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        sources = {}
        failures.append(f"sources API failed: {type(exc).__name__}: {exc}")

    raw_count = int(chargers.get("raw_count") or 0)
    stations = chargers.get("stations") or []
    leads = chargers.get("leads") or []
    gaia = chargers.get("gaia") or {}
    coverage = ((chargers.get("meta") or {}).get("coverage") or {})
    gaia_live_connectors = int(gaia.get("live_connectors") or 0)
    gaia_live_stations = int(gaia.get("live_stations") or 0)
    gaia_errors = gaia.get("errors") or []
    apps = provider_by_name(sources)

    if not coverage:
        failures.append("coverage metadata is missing from /api/chargers")
    if raw_count <= 0:
        failures.append("MapaREVE raw_count is zero; live source shape may be broken")
    if not stations:
        failures.append("no live stations returned from the live response")
    if gaia_live_stations <= 0:
        failures.append("Gaia direct public-page live station count is zero")
    if gaia_live_connectors <= 0:
        failures.append("Gaia direct public-page live connector count is zero")
    if gaia_errors and gaia_live_connectors <= 0:
        failures.append(f"Gaia errors with no usable connector evidence: {gaia_errors[:3]}")

    gaia_status = source_status(apps, "Gaia")
    letme_status = source_status(apps, "LetMeCharge")
    euro_status = source_status(apps, "EuroCharger")
    if not gaia_status.startswith("implemented"):
        failures.append(f"Gaia provider strategy no longer reports implemented status: {gaia_status or 'missing'}")
    if letme_status != "official-api-needed":
        failures.append(f"LetMeCharge strategy should remain official-api-needed unless a public API is implemented: {letme_status or 'missing'}")
    if euro_status != "official-api-needed":
        failures.append(f"EuroCharger strategy should remain official-api-needed unless a public API is implemented: {euro_status or 'missing'}")

    history = read_history(args.history_out)
    latest = latest_ok(history)
    raw_floor = None
    raw_floor_warning = None
    if latest:
        previous_raw = int(latest.get("raw_count") or 0)
        if previous_raw > 0:
            raw_floor = max(1, int(previous_raw * args.min_raw_ratio))
            if raw_count < raw_floor:
                has_safe_fallback_evidence = (
                    is_rate_limited_warning(chargers)
                    and len(stations) > 0
                    and len(leads) >= 300
                    and gaia_live_connectors > 0
                )
                message = f"MapaREVE raw_count dropped below live-source history floor: {raw_count} < {raw_floor} from previous {previous_raw}"
                if has_safe_fallback_evidence:
                    raw_floor_warning = (
                        f"{message}; tolerated because the API reported rate limiting while stale/live fallback, "
                        "Gaia connector evidence, and source-backed leads remained available."
                    )
                else:
                    failures.append(message)

    result = {
        "ok": not failures,
        "checked_at": checked_at,
        "base_url": base,
        "raw_count": raw_count,
        "stations": len(stations),
        "leads": len(leads),
        "coverage_mode": coverage.get("mode"),
        "coverage_warning": coverage.get("warning"),
        "warning": chargers.get("warning"),
        "gaia_live_stations": gaia_live_stations,
        "gaia_live_connectors": gaia_live_connectors,
        "gaia_errors": gaia_errors[:5],
        "gaia_cache": gaia.get("cache"),
        "provider_statuses": {
            "Gaia Charge": gaia_status,
            "LetMeCharge": letme_status,
            "EuroCharger": euro_status,
        },
        "history_entries_before": len(history),
        "raw_floor": raw_floor,
        "raw_floor_warning": raw_floor_warning,
        "failures": failures,
    }
    append_history(args.history_out, result, args.history_keep)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
