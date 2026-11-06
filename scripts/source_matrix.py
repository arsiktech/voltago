"""Location × source matrix: which public sources list each charging location.

A cell shows source coverage, never availability, price or whether charging is free.
See docs/spec-location-source-matrix.md. Five states:

- listed: the source lists this location (a matched listing).
- not_listed: the source covers this area completely, its latest snapshot was fetched inside
  the source's freshness window, and it does not list the location.
- unknown: absence is not proven (out of scope, network-only source, failed or stale fetch).
- uncertain: the source lists a separate location within 80 m that did not pass the
  identity checks.
- restricted: the source may only annotate a price (or only be linked), never a location.

Standard library only.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

UNCERTAIN_KM = 0.08
MAX_LOCATIONS = 150

# A listing file is refetched by the first gated refresh after its cache TTL ends. A run just
# before the TTL ends still hits the cache, so a healthy file reaches TTL + one refresh interval.
# The margin covers the timer's jitter and the run's own time until it publishes.
REFRESH_HOURS = 6
WINDOW_MARGIN_HOURS = 1
FUTURE_TOLERANCE = timedelta(minutes=5)

SPAIN = (27.4, -18.4, 43.9, 4.5)
CANARIES = (27.4, -18.4, 29.5, -13.3)

# id, label, name prefixes in sources[].name or price_evidence[].source, coverage, scope.
# coverage "complete": absence inside scope is evidence; "network": the source lists only
# its own network, so absence proves nothing; "price": price annotations only.
# A complete column also names the cache files that decide what it lists, and their cache TTL
# in scripts/ingest_sources.py. Other files of that source (Canarias detail pages) do not count.
COLUMNS: List[Dict[str, Any]] = [
    {"id": "dgt", "label": "DGT registry", "prefixes": ("DGT National Access Point",), "coverage": "complete", "scope": SPAIN, "listing_files": "dgt_nap_electrolineras", "ttl_hours": 24},
    {"id": "osm", "label": "OpenStreetMap", "prefixes": ("OpenStreetMap",), "coverage": "complete", "scope": SPAIN, "listing_files": "osm_es_area_chargers_", "ttl_hours": 12},
    {"id": "ocm", "label": "OpenChargeMap", "prefixes": ("OpenChargeMap",), "coverage": "complete", "scope": SPAIN, "listing_files": "ocm_poi", "ttl_hours": 7 * 24},
    {"id": "mapareve", "label": "MapaREVE snapshot", "prefixes": ("MapaREVE",), "coverage": "complete", "scope": (28.0, -17.0, 28.7, -15.9), "listing_files": "mapareve_public_tenerife_p", "ttl_hours": 12},
    {"id": "canarias", "label": "Canarias te recarga", "prefixes": ("Canarias te recarga",), "coverage": "complete", "scope": CANARIES, "listing_files": "canarias_tecarga_map", "ttl_hours": 24},
    # Official regional registries; completeness is not verified, so absence proves nothing.
    {"id": "catalonia", "label": "Catalonia open data", "prefixes": ("Catalonia ICAEN",), "coverage": "network", "scope": (40.5, 0.15, 42.9, 3.4)},
    {"id": "navarra", "label": "Navarra open data", "prefixes": ("Navarra IDENA",), "coverage": "network", "scope": (41.9, -2.5, 43.35, -0.7)},
    {"id": "castilla_leon", "label": "Castilla y León open data", "prefixes": ("Castilla y León charging",), "coverage": "network", "scope": (40.0, -7.1, 43.3, -1.7)},
    {"id": "etecnic", "label": "Etecnic map", "prefixes": ("Etecnic public charger map",), "coverage": "network", "scope": SPAIN},
    {"id": "zunder", "label": "Zunder map", "prefixes": ("Zunder public location map",), "coverage": "network", "scope": SPAIN},
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
    "source_catalonia_icaen": "catalonia", "source_navarra_idena": "navarra", "source_castilla_leon": "castilla_leon",
    "source_etecnic": "etecnic", "source_zunder": "zunder",
}
# Cache file name prefixes in the build's fetch_evidence, by column.
CACHE_COLUMNS = {
    "dgt_nap": "dgt", "osm_": "osm", "openchargemap": "ocm", "ocm": "ocm", "mapareve_public": "mapareve",
    "canarias": "canarias", "madrid": "madrid", "valencia": "valencia", "zaragoza": "zaragoza",
    "endolla": "endolla", "barcelona_endolla": "endolla", "fenie": "fenie", "lidl": "lidl", "etecnic": "etecnic",
    "zunder": "zunder", "catalonia_icaen": "catalonia", "navarra_idena": "navarra", "castilla_leon": "castilla_leon",
    "red_movem": "movem", "acciona": "acciona", "moraleja": "moraleja", "electromaps": "electromaps",
    "chargemap": "chargemap",
}
SOURCE_FIELDS = ("name", "url", "evidence", "attribution", "license", "license_url", "source_cache_fallback")
PRICE_FIELDS = ("source", "label", "price_per_kwh", "currency", "source_url")


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


def clock(value: Any) -> Optional[str]:
    """Canonical timestamp text, or None when absent or unreadable. Never substitutes a time.

    Aware values become UTC with a Z. A value with no zone stays without one, and a bare date
    stays a date, because the source did not say more.
    """
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.year < 2000:
            return None
        if len(text) == 10:
            return text
        if parsed.tzinfo is None:
            return parsed.strftime("%Y-%m-%dT%H:%M:%S")
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError):
        return None


def _listed_columns(record: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    found: Dict[str, List[Dict[str, Any]]] = {}
    for source in record.get("sources") or []:
        column = column_for(source.get("name"))
        if column and next(c for c in COLUMNS if c["id"] == column)["coverage"] != "price":
            found.setdefault(column, []).append({
                **{k: source[k] for k in SOURCE_FIELDS if source.get(k) not in (None, "")},
                "clocks": {"fetched_at": clock(source.get("source_fetched_at")),
                           "published_at": clock(source.get("source_published_at")),
                           "source_last_updated": clock(source.get("source_last_updated"))},
            })
    for price in record.get("price_evidence") or []:
        column = column_for(price.get("source"))
        if column:
            found.setdefault(column, []).append({
                **{k: price[k] for k in PRICE_FIELDS if price.get(k) not in (None, "")},
                "clocks": {"fetched_at": clock(price.get("fetched_at")),
                           "published_at": None,
                           "source_last_updated": clock(price.get("source_updated_at"))},
            })
    return found


def _cache_column(cache_name: Any) -> Optional[str]:
    name = str(cache_name or "").lower()
    return next((cid for prefix, cid in CACHE_COLUMNS.items() if name.startswith(prefix)), None)


def _cached_copy_reason(fetched_at: Any) -> str:
    stamp = clock(fetched_at)
    return f"Latest fetch failed; cached copy from {stamp[:10] if stamp else 'an unknown date'}."


def _price_fetch_clocks(intel: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Usable per-source clocks from the price collection report, by price column."""
    report = (intel.get("price_evidence_collection") or {}).get("fetch_clocks") or {}
    price_columns = {c["id"] for c in COLUMNS if c["coverage"] == "price"}
    usable = {}
    for column, summary in report.items():
        if column in price_columns and isinstance(summary, dict):
            newest, oldest = clock(summary.get("at")), clock(summary.get("oldest_part_at"))
            if newest and oldest and newest.endswith("Z") and oldest.endswith("Z"):
                usable[column] = {**summary, "at": newest, "oldest_part_at": oldest}
    return usable


def column_fetches(intel: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Each column's last successful fetch, from the build's fetch evidence.

    An artifact's fetched_at is when its bytes were last retrieved, including for a cached copy
    used after a failed fetch. A column with no usable evidence is absent here, so it reads as
    unknown. A column with several files also reports its oldest file and its failed fetches,
    so one fresh file cannot make the whole source look fresh.
    """
    parts: Dict[str, Dict[str, Tuple[str, bool]]] = {}
    for artifact in (intel.get("fetch_evidence") or {}).get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        column = _cache_column(artifact.get("cache_name"))
        stamp = clock(artifact.get("fetched_at"))
        if not column or not stamp or not stamp.endswith("Z"):
            continue
        files = parts.setdefault(column, {})
        name = str(artifact.get("cache_name")).lower()
        if name not in files or files[name][0] <= stamp:
            files[name] = (stamp, bool((artifact.get("cache") or {}).get("fallback")))
    fetches = {column: {"at": max(stamp for stamp, _ in files.values()),
                        "oldest_part_at": min(stamp for stamp, _ in files.values()),
                        "parts": len(files),
                        "failed_parts": sum(failed for _, failed in files.values())}
               for column, files in parts.items()}
    # Price sources report their clocks separately; source policy keeps them out of fetch_evidence.
    for column, summary in _price_fetch_clocks(intel).items():
        fetches[column] = {key: summary[key] for key in ("at", "oldest_part_at", "parts", "failed_parts")}
    return fetches


def freshness_window_hours(column: Dict[str, Any]) -> int:
    return column["ttl_hours"] + REFRESH_HOURS + WINDOW_MARGIN_HOURS


def stale_columns(intel: Dict[str, Any], now: datetime) -> Dict[str, str]:
    """Complete columns whose listing is not proven fresh at `now`, with the reason.

    Judged on the oldest listing file. Evidence does not say which file covers which point, so
    one fresh file cannot vouch for another: absence needs every file inside the window.
    An unusable time on any listing file fails closed, because that file could be the oldest.
    """
    stale: Dict[str, str] = {}
    artifacts = [a for a in (intel.get("fetch_evidence") or {}).get("artifacts") or [] if isinstance(a, dict)]
    for column in COLUMNS:
        if column["coverage"] != "complete":
            continue
        prefix = column.get("listing_files")
        files = [a for a in artifacts if prefix and str(a.get("cache_name") or "").lower().startswith(prefix)]
        stamps = [clock(a.get("fetched_at")) for a in files]
        if not files:
            stale[column["id"]] = "No fetch evidence for this source's listing, so absence is not proven."
        elif not all(stamp and stamp.endswith("Z") for stamp in stamps):
            stale[column["id"]] = "A listing file has no usable fetch time, so absence is not proven."
        else:
            # A build can carry several records of one file; only its newest fetch counts.
            newest: Dict[str, str] = {}
            for artifact, stamp in zip(files, stamps):
                name = str(artifact["cache_name"]).lower()
                newest[name] = max(stamp, newest.get(name, stamp))
            oldest = min(newest.values())
            fetched = datetime.fromisoformat(oldest.replace("Z", "+00:00"))
            window = freshness_window_hours(column)
            if fetched > now + FUTURE_TOLERANCE:
                stale[column["id"]] = f"A listing file's fetch time ({oldest}) is in the future, so absence is not proven."
            elif now - fetched > timedelta(hours=window):
                which = f"Oldest of {len(newest)} listing files" if len(newest) > 1 else "Listing"
                stale[column["id"]] = f"{which} fetched {oldest}, outside this source's {window} h freshness window."
    return stale


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
        column = _cache_column(artifact.get("cache_name"))
        if column and column not in degraded:
            degraded[column] = _cached_copy_reason(artifact.get("fetched_at"))
    for column, summary in _price_fetch_clocks(intel).items():
        if summary.get("failed_parts") and column not in degraded:
            degraded[column] = _cached_copy_reason(summary.get("oldest_failed_at"))
    for record in records:
        for source in record.get("sources") or []:
            column = column_for(source.get("name"))
            if column and source.get("source_cache_fallback") and column not in degraded:
                degraded[column] = _cached_copy_reason(source.get("source_fetched_at"))
    return degraded


def build_matrix(intel: Dict[str, Any], bbox: Dict[str, float], *, limit: int = MAX_LOCATIONS,
                 now: Optional[datetime] = None) -> Dict[str, Any]:
    """`now` defaults to the current time: a snapshot ages after its build, while it is served."""
    records = [r for r in intel.get("records") or [] if isinstance(r.get("lat"), (int, float)) and isinstance(r.get("lon"), (int, float))]
    box = (bbox["latitude_sw"], bbox["longitude_sw"], bbox["latitude_ne"], bbox["longitude_ne"])
    margin = UNCERTAIN_KM / 111.32 * 2
    near = [r for r in records if _inside(r["lat"], r["lon"], (box[0] - margin, box[1] - margin * 2, box[2] + margin, box[3] + margin * 2))]
    inside = [r for r in near if _inside(r["lat"], r["lon"], box)]
    centre = {"lat": (box[0] + box[2]) / 2, "lon": (box[1] + box[3]) / 2}
    inside.sort(key=lambda r: _distance_km(centre, r))
    truncated = len(inside) > limit
    inside = inside[:limit]
    # A failed fetch is the more specific reason, so it wins over age.
    degraded = {**stale_columns(intel, now or datetime.now(timezone.utc)), **degraded_columns(intel, records)}
    fetches = column_fetches(intel)
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
                reason = ("Outside this source's area." if not _inside(record["lat"], record["lon"], column["scope"])
                          else degraded[cid] if cid in degraded
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
                     "degraded": degraded.get(c["id"]), "last_fetch": fetches.get(c["id"])} for c in COLUMNS],
        "locations": locations,
        "truncated": truncated,
        "note": "Cells show which sources list a location, not availability, price or whether charging is free.",
    }
