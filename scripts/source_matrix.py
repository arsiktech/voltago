"""Location × source matrix: which public sources list each charging location.

A cell shows source coverage, never availability, price or whether charging is free.
See docs/spec-location-source-matrix.md. Five states:

- listed: the source lists this location (a matched listing).
- not_listed: the source covers this area completely and its latest snapshot is current,
  and it does not list the location.
- unknown: absence is not proven (out of scope, network-only source, failed or stale fetch).
- uncertain: the source lists a separate location within 80 m that did not pass the
  identity checks.
- restricted: the source may only annotate a price (or only be linked), never a location.

Standard library only.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

UNCERTAIN_KM = 0.08
MAX_LOCATIONS = 150

SPAIN = (27.4, -18.4, 43.9, 4.5)
CANARIES = (27.4, -18.4, 29.5, -13.3)

# id, label, name prefixes in sources[].name or price_evidence[].source, coverage, scope.
# coverage "complete": absence inside scope is evidence; "network": the source lists only
# its own network, so absence proves nothing; "price": price annotations only.
COLUMNS: List[Dict[str, Any]] = [
    {"id": "dgt", "label": "DGT registry", "prefixes": ("DGT National Access Point",), "coverage": "complete", "scope": SPAIN},
    {"id": "osm", "label": "OpenStreetMap", "prefixes": ("OpenStreetMap",), "coverage": "complete", "scope": SPAIN},
    {"id": "ocm", "label": "OpenChargeMap", "prefixes": ("OpenChargeMap",), "coverage": "complete", "scope": SPAIN},
    {"id": "mapareve", "label": "MapaREVE snapshot", "prefixes": ("MapaREVE",), "coverage": "complete", "scope": (28.0, -17.0, 28.7, -15.9)},
    {"id": "canarias", "label": "Canarias te recarga", "prefixes": ("Canarias te recarga",), "coverage": "complete", "scope": CANARIES},
    {"id": "madrid", "label": "Madrid open data", "prefixes": ("Madrid public",), "coverage": "network", "scope": (40.31, -3.89, 40.64, -3.52)},
    {"id": "valencia", "label": "València open data", "prefixes": ("València OpenData",), "coverage": "network", "scope": (39.27, -0.43, 39.57, -0.27)},
    {"id": "zaragoza", "label": "Zaragoza open data", "prefixes": ("Zaragoza municipal",), "coverage": "network", "scope": (41.55, -1.05, 41.75, -0.75)},
    {"id": "endolla", "label": "Barcelona Endolla", "prefixes": ("Barcelona Endolla", "Endolla official"), "coverage": "network", "scope": (41.32, 2.05, 41.47, 2.23)},
    {"id": "fenie", "label": "Feníe Energía", "prefixes": ("Feníe Energía",), "coverage": "network", "scope": SPAIN},
    {"id": "lidl", "label": "Lidl", "prefixes": ("Lidl official",), "coverage": "network", "scope": SPAIN},
    {"id": "movem", "label": "Red MOVEM", "prefixes": ("Red MOVEM",), "coverage": "network", "scope": SPAIN},
    {"id": "acciona", "label": "ACCIONA", "prefixes": ("ACCIONA",), "coverage": "network", "scope": SPAIN},
    {"id": "moraleja", "label": "Moraleja Green", "prefixes": ("Moraleja Green",), "coverage": "network", "scope": SPAIN},
    {"id": "repsol", "label": "Repsol (price)", "prefixes": ("Repsol",), "coverage": "price", "scope": SPAIN},
    {"id": "chargemap", "label": "Chargemap (price)", "prefixes": ("Chargemap",), "coverage": "price", "scope": SPAIN},
    {"id": "electromaps", "label": "Electromaps (price)", "prefixes": ("Electromaps",), "coverage": "price", "scope": SPAIN},
]
# A build error from these adapters means that column's snapshot is partial.
ADAPTER_COLUMNS = {
    "source_dgt_nap": "dgt", "source_osm": "osm", "source_openchargemap": "ocm",
    "source_mapareve_public": "mapareve", "source_canarias_tecarga": "canarias",
    "source_madrid_official": "madrid", "source_valencia_municipal": "valencia",
    "source_zaragoza_official": "zaragoza", "source_barcelona_endolla": "endolla",
    "source_fenie": "fenie", "source_lidl": "lidl", "source_moraleja_green": "moraleja",
}
# Cache file name prefixes in the build's fetch_evidence, by column.
CACHE_COLUMNS = {
    "dgt_nap": "dgt", "osm_": "osm", "openchargemap": "ocm", "ocm": "ocm", "mapareve_public": "mapareve",
    "canarias": "canarias", "madrid": "madrid", "valencia": "valencia", "zaragoza": "zaragoza",
    "endolla": "endolla", "fenie": "fenie", "lidl": "lidl",
}
SOURCE_FIELDS = ("name", "url", "evidence", "attribution", "license", "license_url", "source_fetched_at",
                 "source_published_at", "source_last_updated", "source_cache_fallback")
PRICE_FIELDS = ("source", "label", "price_per_kwh", "currency", "source_url", "fetched_at", "source_updated_at")


def column_for(name: Optional[str]) -> Optional[str]:
    text = (name or "").strip()
    for column in COLUMNS:
        if any(text.startswith(prefix) for prefix in column["prefixes"]):
            return column["id"]
    return None


def _inside(lat: float, lon: float, box: Tuple[float, float, float, float]) -> bool:
    south, west, north, east = box
    return south <= lat <= north and west <= lon <= east


def _distance_km(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    dy = (a["lat"] - b["lat"]) * 111.32
    dx = (a["lon"] - b["lon"]) * 111.32 * math.cos(math.radians(a["lat"]))
    return math.hypot(dx, dy)


def _listed_columns(record: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    found: Dict[str, List[Dict[str, Any]]] = {}
    for source in record.get("sources") or []:
        column = column_for(source.get("name"))
        if column and next(c for c in COLUMNS if c["id"] == column)["coverage"] != "price":
            found.setdefault(column, []).append({k: source[k] for k in SOURCE_FIELDS if source.get(k) not in (None, "")})
    for price in record.get("price_evidence") or []:
        column = column_for(price.get("source"))
        if column:
            found.setdefault(column, []).append({k: price[k] for k in PRICE_FIELDS if price.get(k) not in (None, "")})
    return found


def degraded_columns(intel: Dict[str, Any], records: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    """Columns whose latest snapshot cannot prove absence, with the reason."""
    degraded: Dict[str, str] = {}
    for error in intel.get("errors") or []:
        column = ADAPTER_COLUMNS.get(error.get("source")) if isinstance(error, dict) else None
        if column:
            degraded[column] = "The latest build of this source failed or was partial."
    for artifact in (intel.get("fetch_evidence") or {}).get("artifacts") or []:
        if not isinstance(artifact, dict) or not (artifact.get("cache") or {}).get("fallback"):
            continue
        name = str(artifact.get("cache_name") or "").lower()
        column = next((cid for prefix, cid in CACHE_COLUMNS.items() if name.startswith(prefix)), None)
        if column and column not in degraded:
            degraded[column] = f"Latest fetch failed; cached copy from {str(artifact.get('fetched_at') or '')[:10]}."
    for record in records:
        for source in record.get("sources") or []:
            column = column_for(source.get("name"))
            if column and source.get("source_cache_fallback") and column not in degraded:
                degraded[column] = f"Latest fetch failed; cached copy from {str(source.get('source_fetched_at') or '')[:10]}."
    return degraded


def build_matrix(intel: Dict[str, Any], bbox: Dict[str, float], *, limit: int = MAX_LOCATIONS) -> Dict[str, Any]:
    records = [r for r in intel.get("records") or [] if isinstance(r.get("lat"), (int, float)) and isinstance(r.get("lon"), (int, float))]
    box = (bbox["latitude_sw"], bbox["longitude_sw"], bbox["latitude_ne"], bbox["longitude_ne"])
    margin = UNCERTAIN_KM / 111.32 * 2
    near = [r for r in records if _inside(r["lat"], r["lon"], (box[0] - margin, box[1] - margin * 2, box[2] + margin, box[3] + margin * 2))]
    inside = [r for r in near if _inside(r["lat"], r["lon"], box)]
    centre = {"lat": (box[0] + box[2]) / 2, "lon": (box[1] + box[3]) / 2}
    inside.sort(key=lambda r: _distance_km(centre, r))
    truncated = len(inside) > limit
    inside = inside[:limit]
    degraded = degraded_columns(intel, records)
    listed_near = [(r, _listed_columns(r)) for r in near]

    locations = []
    for record in inside:
        listed = _listed_columns(record)
        cells: Dict[str, Dict[str, Any]] = {}
        for column in COLUMNS:
            cid = column["id"]
            if cid in listed:
                state = "restricted" if column["coverage"] == "price" else "listed"
                cells[cid] = {"state": state, "entries": listed[cid]}
                continue
            neighbour = next((other for other, other_listed in listed_near
                              if other is not record and cid in other_listed and column["coverage"] != "price"
                              and _distance_km(record, other) <= UNCERTAIN_KM), None)
            if neighbour is not None:
                cells[cid] = {"state": "uncertain", "candidate": {
                    "id": neighbour.get("id"), "name": neighbour.get("name"),
                    "distance_m": round(_distance_km(record, neighbour) * 1000)}}
            elif column["coverage"] == "complete" and _inside(record["lat"], record["lon"], column["scope"]) and cid not in degraded:
                cells[cid] = {"state": "not_listed"}
            else:
                reason = (degraded.get(cid) if cid in degraded
                          else "Outside this source's area." if not _inside(record["lat"], record["lon"], column["scope"])
                          else "This source annotates prices only; it cannot show a location is missing." if column["coverage"] == "price"
                          else "This source lists only its own network, so absence proves nothing.")
                cells[cid] = {"state": "unknown", "reason": reason}
        locations.append({
            "id": record.get("id"), "name": record.get("name"), "address": record.get("address"),
            "city": record.get("city"), "lat": record["lat"], "lon": record["lon"],
            "listed_count": sum(1 for cell in cells.values() if cell["state"] == "listed"),
            "cells": cells,
        })
    return {
        "generated_at": intel.get("generated_at"),
        "columns": [{"id": c["id"], "label": c["label"], "coverage": c["coverage"],
                     "degraded": degraded.get(c["id"])} for c in COLUMNS],
        "locations": locations,
        "truncated": truncated,
        "note": "Cells show which sources list a location, not availability, price or whether charging is free.",
    }
