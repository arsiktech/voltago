#!/usr/bin/env python3
"""Offline performance probe for the plans/007 request-path work.

Stdlib only (time.perf_counter), imports server directly -- no running HTTP
server needed. Times the three hot paths plan 007 targets:

  (a) load_all_leads(SPAIN_BBOX) twice (cold/warm) -- the intel-DB enrichment
      memo (step 2).
  (b) remove_duplicate_leads on the real in-view leads against a synthetic
      300-station list built deterministically from those leads' own
      coordinates/names (so every lead is guaranteed a plausible spatial
      neighbor) -- the spatial-bucket dedup rewrite (step 3).
  (c) overview_response(compact=true) -- the leads-only national overview
      (steps 2 + 5 both feed into this number).

Prints a plain table; numbers are RECORDED in qa/run-log.md, never asserted
as pass/fail here -- that's what tests/test_perf_invariants.py is for
(equality gates, not timing). Run directly:

    PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 scripts/perf_probe.py
"""
from __future__ import annotations

import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


def _ms(start: float, end: float) -> float:
    return round((end - start) * 1000, 1)


def build_synthetic_stations(leads: List[Dict[str, Any]], count: int = 300) -> List[Dict[str, Any]]:
    """Deterministic synthetic station list placed ON TOP of real lead coordinates.

    Every synthetic station reuses a real lead's lat/lon/name (cycling through
    the lead list if fewer than `count` leads have coordinates), so
    remove_duplicate_leads has real spatial neighbors to test against instead
    of an empty/no-op scan. This is deliberately synthetic data (not a live
    fetch) -- offline, deterministic, stdlib only.
    """
    with_coords = [l for l in leads if l.get("lat") is not None and l.get("lon") is not None]
    if not with_coords:
        return []
    stations: List[Dict[str, Any]] = []
    for i in range(count):
        src = with_coords[i % len(with_coords)]
        stations.append({
            "id": f"synthetic-{i}",
            "name": src.get("name"),
            "address": src.get("address"),
            "lat": src.get("lat"),
            "lon": src.get("lon"),
            "sources": [{"name": "synthetic", "url": f"https://example.com/synthetic/{i}"}],
        })
    return stations


def time_load_all_leads() -> List[Dict[str, Any]]:
    t0 = time.perf_counter()
    first = server.load_all_leads(server.SPAIN_BBOX)
    t1 = time.perf_counter()
    second = server.load_all_leads(server.SPAIN_BBOX)
    t2 = time.perf_counter()
    print(f"{'load_all_leads(SPAIN_BBOX) cold':<45} {_ms(t0, t1):>10.1f} ms   ({len(first)} leads)")
    print(f"{'load_all_leads(SPAIN_BBOX) warm':<45} {_ms(t1, t2):>10.1f} ms   ({len(second)} leads)")
    return second


def time_remove_duplicate_leads(leads: List[Dict[str, Any]]) -> None:
    stations = build_synthetic_stations(leads, count=300)
    leads_copy = deepcopy(leads)
    stations_copy = deepcopy(stations)
    t0 = time.perf_counter()
    remaining = server.remove_duplicate_leads(leads_copy, stations_copy)
    t1 = time.perf_counter()
    print(
        f"{'remove_duplicate_leads(%d leads x %d stations)' % (len(leads), len(stations)):<45} "
        f"{_ms(t0, t1):>10.1f} ms   ({len(remaining)} leads remain)"
    )


def time_overview_compact() -> None:
    params = {"compact": ["true"]}
    t0 = time.perf_counter()
    status, payload = server.overview_response(params, server.PRICE_MAX_DEFAULT, False)
    t1 = time.perf_counter()
    print(f"{'overview_response(compact=true)':<45} {_ms(t0, t1):>10.1f} ms   (status={status}, leads={len(payload.get('leads') or [])})")


def time_merge_gaia_stations(leads: List[Dict[str, Any]]) -> None:
    # Reuses the same synthetic-station trick to exercise merge_gaia_stations'
    # O(n*m) shape too (step 3 touches this helper as well as remove_duplicate_leads).
    stations = build_synthetic_stations(leads, count=300)
    gaia_like = deepcopy(stations[:150])
    for i, g in enumerate(gaia_like):
        g["id"] = f"gaia-synthetic-{i}"
    t0 = time.perf_counter()
    merged = server.merge_gaia_stations(deepcopy(stations), gaia_like)
    t1 = time.perf_counter()
    print(f"{'merge_gaia_stations(300 stations x 150 gaia)':<45} {_ms(t0, t1):>10.1f} ms   ({len(merged)} merged)")


def main() -> None:
    print("Volta plans/007 perf probe")
    print(f"python3 {sys.version.split()[0]}  server.py mtime-cached DB: {server.DATA / 'free_intelligence.json'}")
    print("-" * 80)
    leads = time_load_all_leads()
    time_remove_duplicate_leads(leads)
    time_merge_gaia_stations(leads)
    time_overview_compact()
    print("-" * 80)
    print("Numbers are machine-dependent; record real output in qa/run-log.md, never assert here.")


if __name__ == "__main__":
    main()
