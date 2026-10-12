#!/usr/bin/env python3
"""Mapa REVE external API client (plan 013 slice C) — PREPARED, key-gated, inactive.

Mapa REVE (mapareve.es, MITECO + Red Eléctrica) is Spain's official real-time
charging map. Its DOCUMENTED external API (Swagger at /docs/api/external/v1,
verified 2026-07-17) exposes GET /connectors/tariffs (current tariffs — a 0 €
tariff is the authoritative FREE signal) and GET /evses/operational_status,
authenticated with an `x-api-key` requested through the public form at
https://www.mapareve.es/api-contacto and rate-limited to 5 requests/hour.

This module stays INACTIVE until the owner obtains a key (set
VOLTA_REVE_API_KEY). It is not in ACTIVE_SOURCE_ADAPTERS yet: activation is a
separate evidence-gated step — run `--probe` first, save the output under
data/source_probe/, then wire the tariff map into ingestion. The 5 req/hour
budget is respected via a per-run page budget and a persistent cursor, so a
full backfill happens incrementally across runs and later runs use date_from
deltas. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "cache" / "reve_external_state.json"

BASE_URL = "https://www.mapareve.es/api/external/v1"
TARIFFS_ENDPOINT = f"{BASE_URL}/connectors/tariffs"
STATUS_ENDPOINT = f"{BASE_URL}/evses/operational_status"
PAGE_LIMIT = 100  # spec maximum
# 5 requests/hour is the documented cap; default budget leaves headroom for a retry.
PAGE_BUDGET = max(1, int(os.environ.get("VOLTA_REVE_PAGE_BUDGET", "4")))


def api_key() -> str:
    return os.environ.get("VOLTA_REVE_API_KEY", "").strip()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tariff_is_free(tariff: Dict[str, Any]) -> Optional[bool]:
    """True = explicitly free (every price component is 0). False = a component
    costs money. None = no price components published (UNKNOWN, never free)."""
    elements = tariff.get("elements")
    if isinstance(elements, dict):
        elements = [elements]
    components: List[Dict[str, Any]] = []
    for element in elements or []:
        if not isinstance(element, dict):
            continue
        for component in element.get("price_components") or []:
            if isinstance(component, dict):
                components.append(component)
    if not components:
        return None
    for component in components:
        price = component.get("price")
        try:
            if price is None or float(price) > 0:
                return False
        except (TypeError, ValueError):
            return False
    return True


def connector_free_verdict(connector_row: Dict[str, Any]) -> Optional[bool]:
    """Free only if the row has tariffs and EVERY published tariff is free."""
    tariffs = connector_row.get("tariffs") or []
    verdicts = [tariff_is_free(t) for t in tariffs if isinstance(t, dict)]
    if not verdicts:
        return None
    if all(v is True for v in verdicts):
        return True
    if any(v is False for v in verdicts):
        return False
    return None


def load_state() -> Dict[str, Any]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"tariffs": {"page": 1, "date_from": None, "connectors": {}}, "updated_at": None}


def save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = now_iso()
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def fetch_page(endpoint: str, *, page: int, date_from: Optional[str] = None, timeout: int = 45) -> Any:
    params = {"page": page, "limit": PAGE_LIMIT}
    if date_from:
        params["date_from"] = date_from
    request = urllib.request.Request(
        f"{endpoint}?{urllib.parse.urlencode(params)}",
        headers={"x-api-key": api_key(), "Accept": "application/json", "User-Agent": "Volta EV app (GET-only public API client)"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def rows_from_payload(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "items", "results"):
            if isinstance(payload.get(key), list):
                return [row for row in payload[key] if isinstance(row, dict)]
    return []


def sync_tariffs(budget: int = PAGE_BUDGET) -> Dict[str, Any]:
    """Advance the incremental tariff mirror by up to `budget` requests.

    First runs page through the full backfill; once a run returns a short page,
    the cursor flips to date_from-delta mode. The accumulated map is
    {evse_id/connector_id: {free, currency, last_tariff_updated}} and is the
    input a future ingestion adapter will join against NAP evse_ids.
    """
    if not api_key():
        raise SystemExit("VOLTA_REVE_API_KEY is not set — the REVE external client is inactive by design until the owner obtains a key via https://www.mapareve.es/api-contacto")
    state = load_state()
    tariff_state = state.setdefault("tariffs", {"page": 1, "date_from": None, "connectors": {}})
    connectors: Dict[str, Any] = tariff_state.setdefault("connectors", {})
    requests_done = 0
    new_rows = 0
    delta_start = now_iso()
    while requests_done < budget:
        page = int(tariff_state.get("page") or 1)
        payload = fetch_page(TARIFFS_ENDPOINT, page=page, date_from=tariff_state.get("date_from"))
        requests_done += 1
        rows = rows_from_payload(payload)
        for row in rows:
            key = str(row.get("evse_id") or row.get("connector_id") or "").strip()
            if not key:
                continue
            connectors[key] = {
                "free": connector_free_verdict(row),
                "connector_id": row.get("connector_id"),
                "last_tariff_updated": row.get("last_tariff_updated"),
            }
            new_rows += 1
        if len(rows) < PAGE_LIMIT:
            # Backfill (or delta window) exhausted: switch to delta mode from this run.
            tariff_state["page"] = 1
            tariff_state["date_from"] = delta_start
            break
        tariff_state["page"] = page + 1
    save_state(state)
    free_count = sum(1 for value in connectors.values() if value.get("free") is True)
    return {
        "requests": requests_done,
        "rows_seen": new_rows,
        "connectors_tracked": len(connectors),
        "free_connectors": free_count,
        "mode": "delta" if tariff_state.get("date_from") else f"backfill@page{tariff_state.get('page')}",
    }


def probe() -> Dict[str, Any]:
    """One bounded request to prove access; save the output under data/source_probe/."""
    if not api_key():
        raise SystemExit("VOLTA_REVE_API_KEY is not set — request a key first (public form: https://www.mapareve.es/api-contacto)")
    payload = fetch_page(TARIFFS_ENDPOINT, page=1)
    rows = rows_from_payload(payload)
    sample = rows[:3]
    verdicts = {}
    for row in sample:
        key = str(row.get("evse_id") or row.get("connector_id") or "?")
        verdicts[key] = connector_free_verdict(row)
    return {"rows_on_page_1": len(rows), "sample_free_verdicts": verdicts, "fetched_at": now_iso()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Mapa REVE external API client (key-gated, 5 req/h budgeted).")
    parser.add_argument("--probe", action="store_true", help="Single bounded request; prints evidence JSON.")
    parser.add_argument("--sync", action="store_true", help="Advance the incremental tariff mirror by the page budget.")
    args = parser.parse_args()
    if args.probe:
        print(json.dumps(probe(), ensure_ascii=False, indent=2))
    elif args.sync:
        print(json.dumps(sync_tariffs(), ensure_ascii=False, indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
