#!/usr/bin/env python3
"""Smoke-test Gaia public start-page parsing.

The sample fixture verifies the parser without network. The live check is best
effort because Codex/local CI can run with DNS/network disabled.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


SAMPLE_HTML = """
<!doctype html>
<html>
<head><title>Carrer dels Artesans</title></head>
<body>
  <main id="jump-page-connector"
    x-data="{ connector: {'status': 'Charging', 'status_icon': 'zap', 'changed_at': '2026-06-21T07:55:44.979938+00:00', 'jump_url': 'https://partner-next.gaiacharge.com/cpo/jump/fr-grn-ees-val-alb-artesans-1/CIRCUTOR-0786-1', 'connectorId': 'CIRCUTOR-0786-1', 'connector_type': 'Type 2', 'power': '11kW', 'physical_reference': 'H', 'price': 'Free', 'session': {'localized_total_energy': '4.2 kWh', 'localized_duration': '37 min', 'localized_instantaneous_power': '6.8 kW', 'session_start': '2026-06-21T07:18:44+00:00'}} }">
    <h1>Carrer dels Artesans</h1>
    <p>Type 2</p>
    <p>11kW</p>
    <p>Free</p>
    <p>You will not be charged for the energy consumed during this charging session.</p>
  </main>
</body>
</html>
"""


def check_sample() -> None:
    parsed = server.parse_gaia_public_page(server.GAIA_DEFAULT_JUMP_URL, SAMPLE_HTML)
    assert parsed, "sample did not produce a connector"
    assert parsed["connector_id"] == "CIRCUTOR-0786-1"
    assert parsed["status"] == "CHARGING"
    assert parsed["label"] == "Type 2"
    assert parsed["power_kw"] == 11
    assert parsed["physical_reference"] == "H"
    assert parsed["session_power_kw"] == 6.8
    assert parsed["is_free"] is True
    print("sample_ok", json.dumps({
        "connector_id": parsed["connector_id"],
        "status": parsed["status"],
        "power_kw": parsed["power_kw"],
        "session_power_kw": parsed["session_power_kw"],
    }, separators=(",", ":")))


def check_live() -> int:
    try:
        payload = server.fetch_gaia_live(force=True)
    except (urllib.error.URLError, OSError) as exc:
        print(f"live_skip network_unavailable {type(exc).__name__}: {exc}")
        return 0
    stations = payload.get("stations") or []
    if not stations:
        print("live_no_data", json.dumps({"errors": payload.get("errors", [])[:3]}, separators=(",", ":")))
        return 1 if os.environ.get("VOLTA_REQUIRE_GAIA_NETWORK") == "1" else 0
    first = stations[0]
    print("live_ok", json.dumps({
        "station": first.get("name"),
        "connectors": len(first.get("connectors") or []),
        "available": first.get("available"),
        "charging": first.get("charging"),
        "max_power_kw": first.get("max_power_kw"),
    }, ensure_ascii=False, separators=(",", ":")))
    return 0


def main() -> int:
    check_sample()
    return check_live()


if __name__ == "__main__":
    raise SystemExit(main())
