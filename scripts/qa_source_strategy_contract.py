#!/usr/bin/env python3
"""Contract check for Volta's named provider source strategy."""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List


BASE_URL = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8773"
ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PROVIDERS = {
    "Google Maps": "navigation-linkout",
    "Electromaps": "link-only-rights-blocked",
    "Gaia Charge": "implemented-direct-public-page",
    "Chargemap": "removed-rights-blocked",
    "Consum store locator": "link-only-rights-blocked",
    "Feníe Energía Recarga": "permitted-owner-attested-snapshot",
    "Movilidad Iberdrola": "covered-via-primary",
    "Acciona Recarga": "covered-via-primary",
    "GIC": "covered-via-primary",
    "Miio": "covered-via-primary",
    "LetMeCharge": "official-api-needed",
    "EuroCharger": "official-api-needed",
}


def fetch_json(path: str) -> Dict[str, Any]:
    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    failures: List[str] = []
    fetch_json("/api/chargers?max_price=0.25&include_leads=true&force=true")
    sources = fetch_json("/api/sources")
    strategy = fetch_json("/api/source-strategy")
    health = fetch_json("/api/source-health")
    embedded = sources.get("requested_source_strategy") or {}
    embedded_health = sources.get("source_health") or {}
    apps = strategy.get("requested_apps") or []
    embedded_apps = embedded.get("requested_apps") or []
    by_name = {app.get("name"): app for app in apps if isinstance(app, dict)}
    embedded_names = {app.get("name") for app in embedded_apps if isinstance(app, dict)}

    if set(by_name) != embedded_names:
        failures.append("/api/sources embedded strategy differs from /api/source-strategy names")
    if not embedded_health.get("intelligence"):
        failures.append("/api/sources missing embedded source_health intelligence block")
    policy = f"{sources.get('policy', '')} {strategy.get('policy', '')} {json.dumps(health.get('policy', {}), ensure_ascii=False)}".lower()
    for token in ("no login", "private-token", "mitm", "official"):
        if token not in policy:
            failures.append(f"source policy missing boundary token: {token}")

    health_intel = health.get("intelligence") or {}
    hidden = health_intel.get("hidden_from_map") or {}
    if int(health_intel.get("deduped_leads") or 0) <= 0:
        failures.append("/api/source-health intelligence.deduped_leads should be positive")
    if int(health_intel.get("map_eligible_records") or 0) <= 0:
        failures.append("/api/source-health intelligence.map_eligible_records should be positive")
    for key in ("official_price_unknown", "directory", "community", "unverified", "invalid_coordinates"):
        if key not in hidden:
            failures.append(f"/api/source-health hidden_from_map missing {key}")
    if not isinstance(health_intel.get("by_tier"), dict):
        failures.append("/api/source-health intelligence.by_tier must be an object")
    if not isinstance(health_intel.get("coordinate_issues"), dict):
        failures.append("/api/source-health intelligence.coordinate_issues must be an object")
    if health_intel.get("errors"):
        failures.append(f"/api/source-health reported source errors: {health_intel.get('errors')}")

    missing = sorted(set(EXPECTED_PROVIDERS) - set(by_name))
    if missing:
        failures.append(f"requested_source_strategy missing named providers: {missing}")

    for name, expected_status in EXPECTED_PROVIDERS.items():
        app = by_name.get(name)
        if not app:
            continue
        if app.get("status") != expected_status:
            failures.append(f"{name} status {app.get('status')!r}, expected {expected_status!r}")
        for field in ("method", "records_now", "source_url", "risk"):
            if not app.get(field):
                failures.append(f"{name} missing {field}")
        if not isinstance(app.get("records_now"), dict):
            failures.append(f"{name} records_now must be an object")

    for name in ("Electromaps", "Chargemap", "Consum store locator"):
        app = by_name.get(name) or {}
        records = app.get("records_now") or {}
        if records.get("direct_station_records") != 0 or records.get("nested_station_evidence_records") != 0:
            failures.append(f"{name} station evidence must remain zero until its written-permission gate is cleared")
    gaia = by_name.get("Gaia Charge") or {}
    if int((gaia.get("records_now") or {}).get("direct_live_connectors") or 0) <= 0:
        failures.append("Gaia Charge should expose direct_live_connectors from public start pages")
    fenie = by_name.get("Feníe Energía Recarga") or {}
    if fenie.get("source_url") != "https://recarga.fenieenergia.es/":
        failures.append("Feníe source_url must remain the Feníe home/attribution URL")
    for name in ("LetMeCharge", "EuroCharger"):
        records = (by_name.get(name) or {}).get("records_now") or {}
        if records.get("direct_public_records") != 0:
            failures.append(f"{name} direct_public_records should remain 0 without official API/export")

    # Retargeted 2026-07-12 (plans/009): the retired static/index.html's "Data honesty" caveat
    # named all 7 requested providers inline; the SPA's App.tsx HONESTY string states the same
    # public-pages-only/no-login/provider-wins boundary more generally instead, since each named
    # provider's status/method/records_now/source_url/risk is already checked structurally
    # above (lines 76-99) via the live /api/source-strategy contract -- a stronger, API-backed
    # guarantee than a plain substring match against UI copy.
    honesty = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    for token in ("Public pages only", "no logins", "session starts", "Provider apps", "onsite parking signs"):
        if token not in honesty:
            failures.append(f"Data honesty copy missing {token}")

    result = {
        "ok": not failures,
        "base_url": BASE_URL,
        "provider_count": len(apps),
        "providers": sorted(by_name),
        "map_eligible_records": health_intel.get("map_eligible_records"),
        "hidden_from_map": hidden,
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
