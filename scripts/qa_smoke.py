#!/usr/bin/env python3
"""Volta Go QA smoke checks.

Requires a running local server, for example:
  PORT=8767 PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 -u server.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List


BASE_URL = os.environ.get("VOLTA_QA_BASE_URL", "http://127.0.0.1:8773").rstrip("/")


def fetch(path: str, timeout: int = 45) -> bytes:
    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=timeout) as response:
        if response.status != 200:
            raise AssertionError(f"{path} returned HTTP {response.status}")
        return response.read()


def fetch_json(path: str, timeout: int = 45) -> Dict[str, Any]:
    return json.loads(fetch(path, timeout=timeout).decode("utf-8"))


def check(condition: bool, message: str, failures: List[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: List[str] = []
    try:
        home = fetch("/", timeout=10).decode("utf-8", "replace")
        check("Volta" in home, "home page does not contain app title", failures)
        # New SPA shell mounts into #root; legacy vanilla shell used a station template.
        check(
            'id="root"' in home or "stationTemplate" in home,
            "home page missing SPA root / station template",
            failures,
        )

        health = fetch_json("/api/health", timeout=10)
        check(health.get("ok") is True, "/api/health ok is not true", failures)

        intel = fetch_json("/api/intel", timeout=20)
        intel_records = intel.get("records") or []
        check(len(intel_records) >= 50, "/api/intel has too few records", failures)
        check(not intel.get("errors"), f"/api/intel has source errors: {intel.get('errors')}", failures)
        check(all(r.get("sources") for r in intel_records), "/api/intel has records without sources", failures)

        sources = fetch_json("/api/sources", timeout=10)
        check("requested_source_strategy" in sources, "/api/sources missing requested_source_strategy", failures)
        check("source_health" in sources, "/api/sources missing source_health", failures)

        source_health = fetch_json("/api/source-health", timeout=15)
        health_intel = source_health.get("intelligence") or {}
        hidden_from_map = health_intel.get("hidden_from_map") or {}
        check(int(health_intel.get("deduped_leads") or 0) >= 50, "/api/source-health has too few source-backed leads", failures)
        check(int(health_intel.get("map_eligible_records") or 0) > 0, "/api/source-health has no map-eligible verified leads", failures)
        check("directory" in hidden_from_map, "/api/source-health missing hidden directory count", failures)
        check("community" in hidden_from_map, "/api/source-health missing hidden community count", failures)
        check(not health_intel.get("errors"), f"/api/source-health has source errors: {health_intel.get('errors')}", failures)

        chargers = fetch_json("/api/chargers?max_price=0.25&include_leads=true", timeout=45)
        rows = (chargers.get("stations") or []) + (chargers.get("leads") or [])
        coverage = ((chargers.get("meta") or {}).get("coverage") or {})
        check(len(rows) >= 50, "/api/chargers has too few stations+leads", failures)
        check(coverage.get("mode") in {"standard", "degraded"}, "/api/chargers meta.coverage missing mode", failures)
        check(isinstance(coverage.get("live_route_ready"), int), "/api/chargers meta.coverage missing live_route_ready count", failures)
        if coverage.get("is_degraded"):
            check(bool(coverage.get("warning")), "/api/chargers degraded coverage missing warning", failures)
            check(bool(chargers.get("warning")), "/api/chargers degraded coverage not promoted to payload warning", failures)
        required = ["id", "name", "lat", "lon", "parking", "verification", "decision", "sources", "primary_source", "charge_claim", "parking_claim", "navigation"]
        for row in rows[:100]:
            missing = [key for key in required if key not in row or row.get(key) in (None, [], {})]
            check(not missing, f"row {row.get('id')} missing required keys {missing}", failures)
        check(sum(1 for r in rows if not r.get("sources")) == 0, "/api/chargers rows missing source links", failures)
        check(sum(1 for r in rows if not (r.get("primary_source") or {}).get("url")) == 0, "/api/chargers rows missing primary_source.url", failures)
        check(sum(1 for r in rows if not (r.get("primary_source") or {}).get("kind")) == 0, "/api/chargers rows missing primary_source.kind", failures)
        check(sum(1 for r in rows if (r.get("parking") or {}).get("kind")) == len(rows), "/api/chargers rows missing parking kind", failures)
        check(sum(1 for r in rows if (r.get("verification") or {}).get("level")) == len(rows), "/api/chargers rows missing verification level", failures)

        print(json.dumps({
            "ok": not failures,
            "base_url": BASE_URL,
            "intel_records": len(intel_records),
            "stations": len(chargers.get("stations") or []),
            "leads": len(chargers.get("leads") or []),
            "map_eligible_records": health_intel.get("map_eligible_records"),
            "coverage_mode": coverage.get("mode"),
            "coverage_warning": coverage.get("warning"),
            "paid_parking": sum(1 for r in rows if (r.get("parking") or {}).get("kind") == "paid_or_metered"),
            "limited_parking": sum(1 for r in rows if (r.get("parking") or {}).get("kind") == "time_limited_or_validated"),
            "failures": failures,
        }, ensure_ascii=False, indent=2))
    except (AssertionError, OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "base_url": BASE_URL, "error": f"{type(exc).__name__}: {exc}", "failures": failures}, ensure_ascii=False, indent=2))
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
