#!/usr/bin/env python3
"""Read-only MCP endpoint for AI agents (``POST /mcp``).

Agents get the same charger evidence the Volta Go apps show, with the same
honesty rules: live, official, directory and community evidence stay distinct,
leads never become live, and unknown prices or connectors are never treated as
a match. Every tool is read-only; nothing here writes community data or starts a
charging session.

Transport: Streamable HTTP, JSON responses only, stateless. Both protocol eras
are served on the same endpoint:

* modern (2026-07-28): per-request ``_meta`` plus mirrored HTTP headers,
  validated as the specification requires;
* legacy (2025-11-25 and earlier): an ``initialize`` handshake. No session id is
  minted, so every request stands alone.

The module is standard library only. ``server.py`` passes itself in as
``backend`` so this file never imports the server (which may be running as
``__main__``) and works against the exact caches and data that serve the apps.
"""
from __future__ import annotations

import base64
import binascii
import json
import math
import re
import threading
import time
import urllib.parse
from copy import deepcopy
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

MCP_PATH = "/mcp"
SERVER_NAME = "voltago"
SERVER_TITLE = "Volta Go"
SERVER_VERSION = "1.0.0"
MAX_BODY_BYTES = 65_536

MODERN_VERSIONS = ("2026-07-28",)
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SUPPORTED_VERSIONS = MODERN_VERSIONS + LEGACY_VERSIONS
# Streamable HTTP clients older than 2025-06-18 send no version header.
LEGACY_HEADERLESS_VERSION = "2025-03-26"

META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
HEADER_MISMATCH = -32020
UNSUPPORTED_VERSION = -32022

CONNECTOR_TYPES = ("Type 2", "CCS2", "Type 1", "CCS1", "NACS", "GB/T AC", "GB/T DC", "CHAdeMO", "Tesla", "Schuko")
_CONNECTOR_ALIASES = {
    "ccs1": "ccs1", "ccs combo 1": "ccs1", "combo 1": "ccs1",
    "type 1": "type 1", "type1": "type 1", "j1772": "type 1",
    "nacs": "nacs", "j3400": "nacs",
    "gb/t ac": "gb/t ac", "gb/t dc": "gb/t dc", "gbt ac": "gb/t ac", "gbt dc": "gb/t dc",
    "type 2": "type 2",
    "type2": "type 2",
    "mennekes": "type 2",
    "ccs2": "ccs2",
    "ccs": "ccs2",
    "ccs combo 2": "ccs2",
    "chademo": "chademo",
    "tesla": "tesla",
    "tesla ccs": "tesla",
    "supercharger": "tesla",
    "schuko": "schuko",
}
PRICE_ANY = 5.0  # the clients' broad-discovery sentinel: unknown tariffs stay in
DEFAULT_RADIUS_KM = 5.0
MAX_RADIUS_KM = 25.0
DEFAULT_LIMIT = 10
MAX_LIMIT = 30
LOOKUP_RADIUS_KM = 1.0
STATUS_CACHE_SECONDS = 120.0
# server.decision_for treats live status older than this as needing a refresh.
LIVE_STATUS_STALE_MINUTES = 180

INSTRUCTIONS = (
    "Volta Go finds source-backed EV chargers worldwide, free ones first. "
    "Coverage varies by country; live feeds are currently regional (Spain). Every charger carries a `decision`: "
    "`go` means live data shows it free or cheap, compatible and available now; "
    "`check` and `verify` mean it looks good but needs confirming; `lead` means a "
    "known place whose price or status is unconfirmed; `avoid` means do not route "
    "there. `evidence.level` says where the facts come from (live, official, "
    "directory, community, unverified). Only rows with `evidence.live_verified` "
    "true have live availability, and only `evidence.route_ready_now` means usable "
    "right now; `evidence.live_status_stale` marks a live-feed status older than "
    "three hours. Never describe any other row as available now. "
    "Unknown prices are unknown, not free. The provider's own app and signs at the "
    "site override this data. Use find_chargers to search, get_charger for one "
    "charger's full evidence, and get_data_status for coverage and freshness."
)

_BASE64_SENTINEL = re.compile(r"^=\?base64\?(.*)\?=$")
_HEADER_SAFE = re.compile(r"^[\x21-\x7e]([\x20-\x7e]*[\x21-\x7e])?$")


class ToolInputError(ValueError):
    """Bad tool arguments. Reported as a tool result so the model can correct itself."""


# --------------------------------------------------------------------------- tools

_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "find_chargers",
        "title": "Find EV chargers",
        "description": (
            "Search EV chargers worldwide near a place name or coordinates. Results are ordered "
            "by honesty first (route-ready live chargers before unconfirmed leads), then by "
            "distance. Filters never guess: a charger whose price, connector or power is unknown "
            "is excluded by that filter and counted in `excluded`."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "place": {
                    "type": "string",
                    "description": "Place worldwide, e.g. 'Tokyo' or 'Valencia airport'. Use this or lat/lon.",
                    "maxLength": 120,
                },
                "lat": {"type": "number", "minimum": -90.0, "maximum": 90.0, "description": "Latitude (WGS 84)."},
                "lon": {"type": "number", "minimum": -180.0, "maximum": 180.0, "description": "Longitude (WGS 84)."},
                "radius_km": {"type": "number", "minimum": 0.5, "maximum": MAX_RADIUS_KM, "default": DEFAULT_RADIUS_KM},
                "free_only": {
                    "type": "boolean",
                    "default": False,
                    "description": "Only chargers with an explicit free claim. Unknown prices are excluded.",
                },
                "max_price_eur_kwh": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": PRICE_ANY,
                    "description": "Only chargers whose price valid now is at most this. Omit to include unknown prices.",
                },
                "connector": {
                    "type": "string",
                    "enum": list(CONNECTOR_TYPES),
                    "description": "Only chargers whose source lists this connector type.",
                },
                "min_power_kw": {"type": "number", "minimum": 0, "maximum": 400, "description": "Minimum published power."},
                "available_now": {
                    "type": "boolean",
                    "default": False,
                    "description": "Only route-ready chargers: live-verified, decision go, with a free connector now.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": DEFAULT_LIMIT},
            },
            "additionalProperties": False,
        },
        "annotations": {"title": "Find EV chargers", **_READ_ONLY},
    },
    {
        "name": "get_charger",
        "title": "Get one charger",
        "description": (
            "Full evidence for one charger: every source with its claim, tariff windows, "
            "connectors, access, parking and community check-ins. Pass the `id` from "
            "find_chargers. Also pass its `lat` and `lon` to refresh live status; they are "
            "required for chargers known only from a live feed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "minLength": 1, "maxLength": 200},
                "lat": {"type": "number", "minimum": -90.0, "maximum": 90.0},
                "lon": {"type": "number", "minimum": -180.0, "maximum": 180.0},
            },
            "required": ["id"],
            "additionalProperties": False,
        },
        "annotations": {"title": "Get one charger", **_READ_ONLY},
    },
    {
        "name": "get_data_status",
        "title": "Data coverage and freshness",
        "description": (
            "How current and complete Volta Go's data is: dataset build time, record counts by "
            "evidence tier, live-source cache state, coverage area and collection policy. Use it "
            "before claiming coverage or freshness."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"title": "Data coverage and freshness", **_READ_ONLY},
    },
]
_TOOL_NAMES = {tool["name"] for tool in TOOLS}


def _number(args: Mapping[str, Any], key: str, *, low: float, high: float, default: Optional[float] = None) -> Optional[float]:
    value = args.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ToolInputError(f"`{key}` must be a number.")
    value = float(value)
    if not low <= value <= high:
        raise ToolInputError(f"`{key}` must be between {low:g} and {high:g}.")
    return value


def _flag(args: Mapping[str, Any], key: str) -> bool:
    value = args.get(key, False)
    if not isinstance(value, bool):
        raise ToolInputError(f"`{key}` must be true or false.")
    return value


def _text(args: Mapping[str, Any], key: str, *, max_len: int) -> Optional[str]:
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ToolInputError(f"`{key}` must be a non-empty string.")
    if len(value) > max_len:
        raise ToolInputError(f"`{key}` must be at most {max_len} characters.")
    return value.strip()


def _reject_unknown(args: Mapping[str, Any], tool: str) -> None:
    allowed = set(next(t for t in TOOLS if t["name"] == tool)["inputSchema"]["properties"])
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise ToolInputError(f"Unknown argument(s) for {tool}: {', '.join(unknown)}.")


def _connector_key(label: Any) -> Optional[str]:
    text = " ".join(str(label or "").lower().replace("_", " ").split())
    if not text or "unspecified" in text or "unknown" in text:
        return None
    if text in _CONNECTOR_ALIASES:
        return _CONNECTOR_ALIASES[text]
    for alias, key in _CONNECTOR_ALIASES.items():
        if alias in text:
            return key
    return None


def _row_connector_keys(row: Mapping[str, Any]) -> set:
    labels = list(row.get("connector_labels") or [])
    labels += [c.get("label") or c.get("type") for c in row.get("connectors") or [] if isinstance(c, dict)]
    return {key for key in (_connector_key(label) for label in labels) if key}


def _row_power_kw(row: Mapping[str, Any], backend: Any) -> Optional[float]:
    values = [backend.safe_float(row.get("max_power_kw"))]
    values += [backend.safe_float(c.get("power_kw")) for c in row.get("connectors") or [] if isinstance(c, dict)]
    known = [v for v in values if v is not None and v > 0]
    return max(known) if known else None


def _price_now(row: Mapping[str, Any], backend: Any) -> Optional[float]:
    if backend.station_currency(dict(row)) != "EUR":
        return 0.0 if row.get("is_free") is True else None
    current = backend.safe_float(row.get("current_price_min"))
    if current is not None:
        return current
    if row.get("is_free") is True:
        return 0.0
    return backend.safe_float(row.get("price_min"))


def _round(value: Any, digits: int = 2) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


def charger_view(row: Mapping[str, Any], backend: Any, *, detail: bool = False) -> Dict[str, Any]:
    """Agent-facing projection of an enriched row. Copies evidence; never upgrades it."""
    verification = row.get("verification") or {}
    decision = row.get("decision") or {}
    is_lead = bool(row.get("lead"))
    live_verified = verification.get("live_verified") is True and not is_lead and not row.get("cache_stale")
    status_age = backend.age_minutes(backend.freshness_timestamp(row)) if live_verified else None
    live_status_stale = bool(status_age is not None and status_age > LIVE_STATUS_STALE_MINUTES)
    charge_claim = row.get("charge_claim") or {}
    summary = row.get("connector_summary") or {}
    parking = row.get("parking") or {}
    access = row.get("access") or {}
    primary = row.get("primary_source") or {}

    connectors = []
    for connector in (row.get("connectors") or [])[: (40 if detail else 12)]:
        if not isinstance(connector, dict):
            continue
        item = {
            "type": connector.get("label") or connector.get("type"),
            "power_kw": _round(connector.get("power_kw"), 1),
            "count": connector.get("count"),
            "status": connector.get("status") if live_verified else None,
        }
        connectors.append({k: v for k, v in item.items() if v is not None})

    sources = []
    for source in row.get("sources") or []:
        if not isinstance(source, dict) or not source.get("name"):
            continue
        item = {"name": source.get("name"), "url": source.get("url")}
        item.update({key: source.get(key) for key in (
            "license", "license_url", "attribution", "transformation",
            "source_fetched_at", "source_published_at", "source_last_updated",
        )})
        if detail:
            item.update({
                "evidence": source.get("evidence"),
                "license": source.get("license"),
                "last_updated": source.get("source_last_updated"),
            })
        sources.append({k: v for k, v in item.items() if v})
    if not detail:
        sources = sources[:5]

    view: Dict[str, Any] = {
        "id": row.get("id"),
        "name": row.get("name"),
        "address": row.get("address"),
        "city": row.get("city"),
        "country_code": row.get("country_code") or row.get("country"),
        "lat": _round(row.get("lat"), 6),
        "lon": _round(row.get("lon"), 6),
        "distance_km": _round(row.get("distance_km"), 2),
        "decision": {
            "level": decision.get("level") or "lead",
            "label": decision.get("label"),
            "reason": decision.get("reason"),
        },
        "evidence": {
            "level": verification.get("level") or "unverified",
            "label": verification.get("label"),
            "live_verified": live_verified,
            "live_status_age_minutes": status_age,
            "live_status_stale": live_status_stale,
            "route_ready_now": bool(backend.route_ready_now(dict(row))),
            "is_lead": is_lead,
            "cache_stale": bool(row.get("cache_stale")),
            "freshness_minutes": row.get("freshness_minutes"),
            "last_seen": row.get("last_seen"),
            "independent_sources": (row.get("corroboration") or {}).get("independent_sources"),
            "note": verification.get("note"),
        },
        "price": {
            "is_free": row.get("is_free") is True,
            "currency": backend.station_currency(dict(row)),
            "price_native_min": _round(row.get("price_native_min"), 3),
            "native_unit": (row.get("tariff") or {}).get("unit") or row.get("price_native_unit"),
            "price_now_eur_kwh": _round(backend.euro_price(dict(row), "current_price_min"), 3),
            "lowest_published_eur_kwh": _round(backend.euro_price(dict(row)), 3),
            "label": row.get("price_label"),
            "note": charge_claim.get("summary") or row.get("free_condition"),
            "source": charge_claim.get("source"),
            "source_url": charge_claim.get("url"),
        },
        "hardware": {
            "max_power_kw": _round(_row_power_kw(row, backend), 1),
            "connectors": connectors,
            "available_connectors": summary.get("available") if live_verified else None,
            "total_connectors": summary.get("total") if summary.get("total") is not None else row.get("total_evse"),
        },
        "access": {"level": access.get("level"), "label": access.get("label"), "note": access.get("note")},
        "parking": {"label": parking.get("label"), "paid": parking.get("paid"), "note": parking.get("note")},
        "primary_source": {"name": primary.get("name"), "url": primary.get("url"), "label": primary.get("label")},
        "sources": sources,
        "navigation": row.get("navigation") or {},
    }
    if row.get("operator"):
        view["operator"] = row.get("operator")
    community = row.get("community")
    if isinstance(community, dict) and community:
        checkins = community.get("checkins") or {}
        view["community"] = {
            "checkins": checkins.get("total", 0),
            "last_success_at": checkins.get("last_success_at"),
            "last_failure_at": checkins.get("last_failure_at"),
            "recent_success": bool(community.get("recent_success")),
            "recent_failure": bool(community.get("recent_failure")),
            "photos": len(community.get("photos") or []),
            "note": "Community reports annotate or downgrade; they never make a charger live.",
        }
    if detail:
        view["price"]["windows"] = [
            {k: window.get(k) for k in ("label", "price", "unit", "valid_from", "valid_until", "days", "is_currently_valid") if window.get(k) is not None}
            for window in row.get("pricing") or []
            if isinstance(window, dict)
        ][:24]
        view["price"]["conflict"] = row.get("claim_conflict")
        view["insight"] = row.get("insight")
        view["merged_ids"] = list(row.get("merged_ids") or [])[:20]
        view["confidence"] = row.get("confidence")
        view["media"] = {"photo_status": (row.get("media") or {}).get("photo_status")}
    return view


def _age_text(minutes: Any) -> str:
    minutes = int(minutes or 0)
    if minutes < 120:
        return f"{minutes} min"
    if minutes < 48 * 60:
        return f"{minutes // 60} h"
    return f"{minutes // 1440} days"


def _summary_line(index: int, view: Mapping[str, Any]) -> str:
    parts = [f"{index}. {view.get('name') or view.get('id')}"]
    if view.get("distance_km") is not None:
        parts[0] += f" ({view['distance_km']} km)"
    decision = view["decision"]
    parts.append(str(decision.get("label") or decision.get("level")))
    price = view["price"]
    if price["is_free"]:
        parts.append("free")
    elif price.get("price_now_eur_kwh") is not None:
        parts.append(f"€{price['price_now_eur_kwh']:.2f}/kWh now")
    else:
        parts.append("price unknown")
    evidence = view["evidence"]
    if not evidence["live_verified"]:
        parts.append(f"{evidence.get('label') or evidence['level']}, not live")
    elif evidence["live_status_stale"]:
        parts.append(f"live feed, status {_age_text(evidence['live_status_age_minutes'])} old")
    else:
        parts.append("live")
    if view["hardware"].get("max_power_kw"):
        parts.append(f"{view['hardware']['max_power_kw']:g} kW")
    return " · ".join(parts) + f" · id={view.get('id')}"


def _resolve_origin(args: Mapping[str, Any], backend: Any) -> Tuple[float, float, Optional[str]]:
    place = _text(args, "place", max_len=120)
    lat = _number(args, "lat", low=-90.0, high=90.0)
    lon = _number(args, "lon", low=-180.0, high=180.0)
    if (lat is None) != (lon is None):
        raise ToolInputError("Pass both `lat` and `lon`, or a `place`.")
    if lat is not None and lon is not None:
        return lat, lon, None
    if not place:
        raise ToolInputError("Pass a `place` or `lat` and `lon`.")
    results = backend.geocode_search(place, limit=1)
    if not results:
        raise ToolInputError(f"No place matched {place!r}. Try a town name or pass lat/lon.")
    best = results[0]
    return float(best["lat"]), float(best["lon"]), str(best.get("label") or place)


def find_chargers(args: Mapping[str, Any], backend: Any) -> Dict[str, Any]:
    _reject_unknown(args, "find_chargers")
    lat, lon, place_label = _resolve_origin(args, backend)
    radius = _number(args, "radius_km", low=0.5, high=MAX_RADIUS_KM, default=DEFAULT_RADIUS_KM)
    free_only = _flag(args, "free_only")
    max_price = _number(args, "max_price_eur_kwh", low=0.0, high=PRICE_ANY)
    min_power = _number(args, "min_power_kw", low=0.0, high=400.0)
    available_now = _flag(args, "available_now")
    connector = args.get("connector")
    if connector is not None and connector not in CONNECTOR_TYPES:
        raise ToolInputError(f"`connector` must be one of: {', '.join(CONNECTOR_TYPES)}.")
    limit = args.get("limit", DEFAULT_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ToolInputError(f"`limit` must be an integer from 1 to {MAX_LIMIT}.")

    params = {
        "lat": [f"{lat:.6f}"],
        "lon": [f"{lon:.6f}"],
        "radius_km": [f"{radius:g}"],
        "include_leads": ["true"],
        "max_price": [f"{PRICE_ANY:g}"],
    }
    status, payload = backend.live_response(params)
    if status != 200:
        raise RuntimeError(f"charger search returned HTTP {status}")
    stations = payload.get("stations") or []
    backend.attach_community_rows(stations)
    rows = list(stations) + list(payload.get("leads") or [])

    want_connector = _connector_key(connector) if connector else None
    excluded = {"outside_radius": 0, "not_free": 0, "price_unknown": 0, "over_price": 0,
                "connector_unknown": 0, "connector_mismatch": 0, "power_unknown": 0,
                "under_power": 0, "not_route_ready": 0}
    kept = []
    for row in rows:
        distance = backend.safe_float(row.get("distance_km"))
        if distance is None or distance > radius:
            excluded["outside_radius"] += 1
            continue
        if free_only and not backend.free_row(row):
            excluded["not_free"] += 1
            continue
        if max_price is not None:
            price = _price_now(row, backend)
            if price is None:
                excluded["price_unknown"] += 1
                continue
            if price > max_price:
                excluded["over_price"] += 1
                continue
        if want_connector:
            keys = _row_connector_keys(row)
            if not keys:
                excluded["connector_unknown"] += 1
                continue
            if want_connector not in keys:
                excluded["connector_mismatch"] += 1
                continue
        if min_power is not None and min_power > 0:
            power = _row_power_kw(row, backend)
            if power is None:
                excluded["power_unknown"] += 1
                continue
            if power < min_power:
                excluded["under_power"] += 1
                continue
        if available_now and not backend.route_ready_now(row):
            excluded["not_route_ready"] += 1
            continue
        kept.append(row)
    kept.sort(key=backend.nearby_sort_key)
    views = [charger_view(row, backend) for row in kept[:limit]]

    meta = payload.get("meta") or {}
    coverage = meta.get("coverage") or {}
    cache = payload.get("cache") or {}
    result = {
        "query": {
            "place": place_label,
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "radius_km": radius,
            "filters": {k: v for k, v in {
                "free_only": free_only or None,
                "max_price_eur_kwh": max_price,
                "connector": connector,
                "min_power_kw": min_power,
                "available_now": available_now or None,
            }.items() if v is not None},
        },
        "count": len(views),
        "total_matches": len(kept),
        "total_matches_is_complete": not bool(meta.get("truncated")),
        "truncated": bool(meta.get("truncated")),
        "candidate_records": meta.get("candidate_records"),
        "chargers": views,
        "excluded": {k: v for k, v in excluded.items() if v},
        "live": {
            "supported": coverage.get("supported", True),
            "fresh_live_data": bool(cache.get("fresh_live_authority")),
            "live_source_failed": bool(cache.get("live_failed")),
            "stale_live_rows": bool(cache.get("stale")),
            "route_ready_nearby": sum(1 for row in kept if backend.route_ready_now(row)),
        },
        "coverage_note": coverage.get("summary"),
        "warning": payload.get("warning"),
        "as_of": backend.now_iso(),
        "scope": "worldwide",
        "caveat": "The provider app and signs at the site override this data. Only live_verified rows have live availability.",
    }
    header = f"{len(views)} of {len(kept)} chargers within {radius:g} km"
    header += f" of {place_label}" if place_label else f" of {lat:.4f},{lon:.4f}"
    header += "." if views else ". None matched; see `excluded` for what the filters removed."
    lines = [header] + [_summary_line(i, view) for i, view in enumerate(views, 1)]
    if payload.get("warning"):
        lines.append(f"Warning: {payload['warning']}")
    lines.append(result["caveat"])
    return {"text": "\n".join(lines), "structured": result}


_ID_INDEX: Dict[str, Any] = {"pool": None, "index": {}}
_ID_INDEX_LOCK = threading.Lock()


def _stored_row(charger_id: str, backend: Any) -> Optional[Dict[str, Any]]:
    """Find an intelligence/seed row by id or merged id, returning a private copy."""
    raw = backend.load_intelligence_meta()
    intel = raw.get("records") or []
    seeds = backend.load_seed_leads()
    if isinstance(intel, backend.IndexedRecords):
        row = intel.index.get_by_id(charger_id)
        if row is not None:
            row = backend._normalize_lead(row, "free/cheap intelligence DB")
        else:
            row = next((deepcopy(seed) for seed in seeds
                        if charger_id in [str(seed.get("id"))] + list(seed.get("merged_ids") or [])), None)
        if row is not None:
            backend.apply_corroboration(row)
            backend.attach_community_rows([row])
            backend._refresh_time_derived_fields([row])
        return row
    with _ID_INDEX_LOCK:
        if _ID_INDEX["pool"] is not intel:
            index: Dict[str, Tuple[int, int]] = {}
            for table, rows in enumerate((intel, seeds)):
                for position, row in enumerate(rows):
                    for key in [row.get("id")] + list(row.get("merged_ids") or []):
                        if key:
                            index.setdefault(str(key), (table, position))
            _ID_INDEX["pool"] = intel
            _ID_INDEX["index"] = index
        hit = _ID_INDEX["index"].get(charger_id)
    if hit is None:
        return None
    table, position = hit
    row = deepcopy((intel, seeds)[table][position])
    if table == 0:
        row = backend._normalize_lead(row, "free/cheap intelligence DB")
    backend.apply_corroboration(row)
    backend.attach_community_rows([row])
    backend._refresh_time_derived_fields([row])
    return row


def _matches(candidate: Mapping[str, Any], charger_id: str, stored: Optional[Mapping[str, Any]], backend: Any) -> bool:
    if str(candidate.get("id")) == charger_id or charger_id in (candidate.get("merged_ids") or []):
        return True
    if stored is None:
        return False
    if stored.get("id") and stored["id"] in (candidate.get("merged_ids") or []):
        return True
    return bool(backend.likely_same_station(dict(candidate), dict(stored)))


def get_charger(args: Mapping[str, Any], backend: Any) -> Dict[str, Any]:
    _reject_unknown(args, "get_charger")
    charger_id = _text(args, "id", max_len=200)
    if not charger_id:
        raise ToolInputError("Pass the charger `id` from find_chargers.")
    lat = _number(args, "lat", low=-90.0, high=90.0)
    lon = _number(args, "lon", low=-180.0, high=180.0)
    if (lat is None) != (lon is None):
        raise ToolInputError("Pass both `lat` and `lon`, or neither.")

    stored = _stored_row(charger_id, backend)
    if lat is None and stored is not None:
        lat, lon = backend.safe_float(stored.get("lat")), backend.safe_float(stored.get("lon"))
    live_row = None
    live_note = None
    if lat is not None and lon is not None:
        status, payload = backend.live_response({
            "lat": [f"{lat:.6f}"],
            "lon": [f"{lon:.6f}"],
            "radius_km": [f"{LOOKUP_RADIUS_KM:g}"],
            "include_leads": ["true"],
            "max_price": [f"{PRICE_ANY:g}"],
        })
        if status == 200:
            stations = payload.get("stations") or []
            backend.attach_community_rows(stations)
            live_row = next((row for row in stations if _matches(row, charger_id, stored, backend)), None)
            if live_row is None and stored is None:
                live_row = next((row for row in payload.get("leads") or [] if _matches(row, charger_id, None, backend)), None)
            live_note = payload.get("warning")
    row = live_row or stored
    if row is None:
        hint = "" if lat is not None else " If it came from a live feed, pass its `lat` and `lon` too."
        raise ToolInputError(f"No charger with id {charger_id!r} was found.{hint}")
    view = charger_view(row, backend, detail=True)
    view["as_of"] = backend.now_iso()
    if stored is not None and live_row is not None and live_row is not stored:
        view["stored_record_id"] = stored.get("id")
    if live_note:
        view["warning"] = live_note
    lines = [_summary_line(1, view)[3:], f"Decision: {view['decision'].get('reason') or view['decision']['level']}"]
    lines.append(f"Evidence: {view['evidence'].get('label') or view['evidence']['level']}. {view['evidence'].get('note') or ''}".strip())
    if view["price"].get("note"):
        lines.append(f"Price: {view['price']['note']}")
    for source in view["sources"][:6]:
        lines.append(f"Source: {source['name']} {source.get('url') or ''}".rstrip())
    if live_note:
        lines.append(f"Warning: {live_note}")
    return {"text": "\n".join(lines), "structured": view}


_STATUS_CACHE: Dict[str, Any] = {"ts": 0.0, "value": None}
_STATUS_LOCK = threading.Lock()


def get_data_status(args: Mapping[str, Any], backend: Any) -> Dict[str, Any]:
    _reject_unknown(args, "get_data_status")
    with _STATUS_LOCK:
        cached = _STATUS_CACHE["value"]
        if cached is None or time.time() - _STATUS_CACHE["ts"] > STATUS_CACHE_SECONDS:
            health = backend.source_health_summary()
            intel = health.get("intelligence") or {}
            live_cache = health.get("live_cache") or {}
            cached = {
                "scope": "worldwide directory coverage; regional live feeds in Spain",
                "dataset": {
                    "built_at": intel.get("generated_at"),
                    "records": intel.get("total_records"),
                    "deduplicated_chargers": intel.get("deduped_leads"),
                    "map_eligible": intel.get("map_eligible_records"),
                    "by_evidence_tier": {tier: row.get("total") for tier, row in (intel.get("by_tier") or {}).items()},
                    "top_sources": dict(list((intel.get("by_source") or {}).items())[:12]),
                    "by_country": intel.get("by_country") or {},
                    "source_manifest": health.get("source_manifest") or {},
                },
                "live_sources": {
                    name: {k: info.get(k) for k in ("name", "status", "has_payload", "age_seconds", "fresh", "stale", "error", "policy") if k in info}
                    for name, info in live_cache.items()
                    if isinstance(info, dict)
                },
                "policy": (health.get("policy") or {}).get("summary"),
                "decision_levels": {
                    "go": "Live data: free or cheap, compatible and available now.",
                    "check": "Promising, but one live fact needs confirming (price window, parking, stale feed).",
                    "verify": "Good non-live evidence; confirm before driving.",
                    "lead": "Known charger; price or status unconfirmed.",
                    "avoid": "Do not route here (out of service or incompatible).",
                },
                "evidence_levels": ["live", "official", "official-lead", "directory", "community", "unverified"],
            }
            _STATUS_CACHE.update({"ts": time.time(), "value": cached})
    result = dict(cached, as_of=backend.now_iso())
    dataset = result["dataset"]
    text = (
        f"Volta Go covers {result['scope']}. Dataset built {dataset.get('built_at')}: "
        f"{dataset.get('deduplicated_chargers')} chargers ({dataset.get('map_eligible')} map-eligible). "
        "Live availability comes only from live feeds; everything else is labelled by evidence tier."
    )
    return {"text": text, "structured": result}


TOOL_HANDLERS: Dict[str, Callable[[Mapping[str, Any], Any], Dict[str, Any]]] = {
    "find_chargers": find_chargers,
    "get_charger": get_charger,
    "get_data_status": get_data_status,
}


# ------------------------------------------------------------------------ protocol

def origin_allowed(origin: Optional[str], extra_allowed: Tuple[str, ...] = ()) -> bool:
    """DNS-rebinding guard. Allows no Origin (server-side clients), any HTTPS origin
    (a rebinding page cannot complete TLS against this host's name), loopback HTTP
    origins (local inspectors), and explicitly configured origins."""
    if not origin:
        return True
    origin = origin.strip()
    if origin in extra_allowed:
        return True
    parsed = urllib.parse.urlsplit(origin)
    if parsed.scheme == "https" and parsed.hostname:
        return True
    return parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}


def _decode_header(value: str) -> Optional[str]:
    match = _BASE64_SENTINEL.match(value)
    if not match:
        return value
    try:
        return base64.b64decode(match.group(1), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None


def _error(request_id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    error: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _result(request_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _server_info() -> Dict[str, str]:
    return {"name": SERVER_NAME, "title": SERVER_TITLE, "version": SERVER_VERSION}


def _call_tool(params: Mapping[str, Any], backend: Any, rate_limit: Callable[[], Tuple[bool, float]], modern: bool) -> Dict[str, Any]:
    name = str(params.get("name"))
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise ToolInputError("`arguments` must be an object.")
    allowed, retry_after = rate_limit()
    if not allowed:
        wait = max(1, math.ceil(retry_after))
        result = {"content": [{"type": "text", "text": f"Rate limit reached. Retry in {wait} seconds."}], "isError": True}
    else:
        try:
            output = TOOL_HANDLERS[name](arguments, backend)
            result = {
                "content": [{"type": "text", "text": output["text"]}],
                "structuredContent": output["structured"],
                "isError": False,
            }
        except ToolInputError as exc:
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    if modern:
        result["resultType"] = "complete"
    return result


def handle_message(
    message: Any,
    headers: Mapping[str, str],
    *,
    backend: Any,
    rate_limit: Callable[[], Tuple[bool, float]],
) -> Tuple[int, Optional[Dict[str, Any]]]:
    """Process one decoded JSON-RPC message. Returns (HTTP status, body or None).

    ``headers`` must use lowercase names (``handle_http_post`` normalizes them).
    """
    if not isinstance(message, dict):
        return 400, _error(None, INVALID_REQUEST, "Expected a single JSON-RPC 2.0 request object.")
    if message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return 400, _error(message.get("id"), INVALID_REQUEST, "Expected a single JSON-RPC 2.0 request object.")
    method = message["method"]
    request_id = message.get("id")
    is_notification = "id" not in message
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return 400, _error(request_id, INVALID_PARAMS, "`params` must be an object.")
    raw_meta = params.get("_meta")
    meta: Dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    header_version = (headers.get("mcp-protocol-version") or "").strip() or None
    meta_version = meta.get(META_VERSION)
    modern = method != "initialize" and (
        meta_version is not None or (header_version is not None and header_version not in LEGACY_VERSIONS)
    )

    if modern:
        version = meta_version or header_version
        if version not in MODERN_VERSIONS:
            return 400, _error(request_id, UNSUPPORTED_VERSION, "Unsupported protocol version",
                               {"supported": list(SUPPORTED_VERSIONS), "requested": version})
        if header_version is None:
            return 400, _error(request_id, HEADER_MISMATCH, "Header mismatch: MCP-Protocol-Version header is required.")
        if meta_version is None or META_CAPABILITIES not in meta:
            return 400, _error(request_id, INVALID_PARAMS,
                               f"Invalid params: _meta must include {META_VERSION} and {META_CAPABILITIES}.")
        if header_version != meta_version:
            return 400, _error(request_id, HEADER_MISMATCH,
                               f"Header mismatch: MCP-Protocol-Version header value '{header_version}' does not match body value '{meta_version}'")
        header_method = headers.get("mcp-method")
        if header_method is None or header_method.strip() != method:
            return 400, _error(request_id, HEADER_MISMATCH,
                               f"Header mismatch: Mcp-Method header value '{header_method}' does not match body value '{method}'")
        if method == "tools/call":
            raw_name = headers.get("mcp-name")
            decoded = _decode_header(raw_name.strip()) if raw_name is not None else None
            if raw_name is None or decoded is None or not _HEADER_SAFE.match(raw_name.strip()) or decoded != params.get("name"):
                return 400, _error(request_id, HEADER_MISMATCH,
                                   f"Header mismatch: Mcp-Name header value '{raw_name}' does not match body value '{params.get('name')}'")
    elif header_version is not None and header_version not in LEGACY_VERSIONS:
        return 400, _error(request_id, UNSUPPORTED_VERSION, "Unsupported protocol version",
                           {"supported": list(SUPPORTED_VERSIONS), "requested": header_version})

    if is_notification:
        return 202, None

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
        return 200, _result(request_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": _server_info(),
            "instructions": INSTRUCTIONS,
        })
    if method == "server/discover":
        return 200, _result(request_id, {
            "resultType": "complete",
            "supportedVersions": list(SUPPORTED_VERSIONS),
            "capabilities": {"tools": {}},
            "_meta": {META_SERVER_INFO: _server_info()},
            "instructions": INSTRUCTIONS,
            "ttlMs": 3_600_000,
            "cacheScope": "public",
        })
    if method == "ping":
        return 200, _result(request_id, {"resultType": "complete"} if modern else {})
    if method == "tools/list":
        result: Dict[str, Any] = {"tools": deepcopy(TOOLS)}
        if modern:
            result.update({"resultType": "complete", "ttlMs": 3_600_000, "cacheScope": "public"})
        return 200, _result(request_id, result)
    if method == "tools/call":
        if params.get("name") not in _TOOL_NAMES:
            return (400 if modern else 200), _error(request_id, INVALID_PARAMS, f"Unknown tool: {params.get('name')!r}")
        try:
            return 200, _result(request_id, _call_tool(params, backend, rate_limit, modern))
        except ToolInputError as exc:
            return (400 if modern else 200), _error(request_id, INVALID_PARAMS, str(exc))
        except Exception as exc:  # internal detail stays in the server log
            backend._log_upstream_error(f"MCP tool {params.get('name')} failed", exc)
            return 500, _error(request_id, INTERNAL_ERROR, "Internal error; nothing is presented as live.")
    return (404 if modern else 200), _error(request_id, METHOD_NOT_FOUND, f"Method not found: {method}")


def handle_http_post(
    body: bytes,
    headers: Mapping[str, str],
    *,
    backend: Any,
    rate_limit: Callable[[], Tuple[bool, float]],
    allowed_origins: Tuple[str, ...] = (),
) -> Tuple[int, Optional[Dict[str, Any]]]:
    """Validate the HTTP envelope, decode one JSON-RPC message and dispatch it."""
    headers = {str(name).lower(): value for name, value in headers.items()}
    if not origin_allowed(headers.get("origin"), allowed_origins):
        return 403, _error(None, INVALID_REQUEST, "Origin not allowed.")
    try:
        message = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return 400, _error(None, PARSE_ERROR, "Parse error: body must be one JSON-RPC message.")
    if isinstance(message, list):
        return 400, _error(None, INVALID_REQUEST, "Batch requests are not supported; send one message per POST.")
    return handle_message(message, headers, backend=backend, rate_limit=rate_limit)
