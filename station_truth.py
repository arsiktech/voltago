#!/usr/bin/env python3
"""Canonical stdlib station-truth primitives shared by runtime and ingestion.

This module owns normalization/classification that must not drift between the live server
and offline snapshot builder. It has no filesystem, network, cache, or product-state
dependencies; callers keep orchestration and localized presentation on their surface.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional


def normalize_connector_label(token: Any) -> Optional[str]:
    """Normalize a connector token without inventing Tesla compatibility."""
    if token is None:
        return None
    raw = str(token).strip()
    if not raw:
        return None
    normalized = raw.upper()
    if "CCS" in normalized or "COMBO" in normalized:
        return "CCS2"
    if "CHADEMO" in normalized:
        return "CHAdeMO"
    if "TESLA" in normalized:
        return "Tesla"
    if any(alias in normalized for alias in ("T2", "MENNEKES", "TYPE 2", "TIPO 2")):
        return "Type 2"
    if any(alias in normalized for alias in ("SCHUKO", "SHUKO", "DOMESTIC", "SCEE")):
        return "Schuko"
    if "T1" in normalized:
        return "Type 1"
    return raw.replace("_", " ").title()


def normalize_public_source_url(url: Any) -> str:
    """Normalize a source handoff without turning arbitrary schemes into web links."""
    value = str(url or "").strip()
    match = re.search(
        r"https?://(?:www\.)?openchargemap\.org/site/poi/details/(\d+)",
        value,
        re.I,
    )
    if match:
        return f"https://map.openchargemap.io/?mode=details&id={match.group(1)}"
    if (
        value
        and not re.match(r"(?i)^[a-z][a-z0-9+.-]*:", value)
        and re.match(r"(?i)^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(/|$)", value)
    ):
        return f"https://{value}"
    return value


def source_candidates(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect and de-duplicate public source candidates from one station record."""
    candidates: List[Dict[str, Any]] = []

    def push(name: Any, url: Any, evidence: Any = "") -> None:
        if not url:
            return
        value = normalize_public_source_url(url)
        if not value.startswith(("https://", "http://")):
            return
        candidates.append(
            {
                "name": str(name or "Source"),
                "url": value,
                "evidence": str(evidence or ""),
            }
        )

    push(item.get("source_label") or item.get("source") or "Source", item.get("source_url"))
    for source in item.get("sources") or []:
        if isinstance(source, dict):
            push(
                source.get("name") or item.get("source") or "Source",
                source.get("url"),
                source.get("evidence"),
            )
    owner = item.get("owner") or {}
    push(f"{owner.get('name') or 'Provider'} website", owner.get("website"))
    if "mapareve" in str(item.get("source") or "").lower():
        push("MapaREVE", "https://www.mapareve.es/", "Live/public source map.")

    seen = set()
    output: List[Dict[str, Any]] = []
    for candidate in candidates:
        key = candidate["url"].split("#", 1)[0].rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(candidate)
    return output


def classify_source_resource(source: Dict[str, Any]) -> Dict[str, Any]:
    """Classify how exact/actionable a source handoff is, with a stable score."""
    url = str(source.get("url") or "")
    name = str(source.get("name") or "Source")
    low = f"{name} {url}".lower()
    kind = "generic"
    label = "Source"
    quality = "generic"
    action = f"Open {name if name and name != 'Source' else 'source'}"
    score = 35

    if re.search(r"gaiacharge\.com/cpo/jump", low):
        kind, label, quality, action, score = "start_page", "Start page", "exact", "Check/start Gaia", 120
    elif re.search(r"electromaps\.com/(?:en|es|fr|pt)/p/|map\.electromaps\.com", low):
        kind, label, quality, action, score = "station_page", "Station page", "exact", "Check Electromaps", 112
    elif re.search(r"chargemap\.com/.+\.html", low):
        kind, label, quality, action, score = "station_page", "Station page", "exact", "Check Chargemap", 108
    elif "openchargemap.org/site/poi/details" in low or "map.openchargemap.io" in low:
        kind, label, quality, action, score = "station_page", "Station page", "exact", "Check OCM", 104
    elif re.search(r"consum\.es/supermercados/(?!valencia/)", low):
        kind, label, quality, action, score = "store_page", "Store page", "exact", "Check Consum store", 102
    elif re.search(r"canariasterecarga\.com/(?:es|en)/mapa", low):
        kind, label, quality, action, score = "live_map", "Map", "map", "Open Canarias te recarga map", 80
    elif re.search(r"canariasterecarga\.com/(?:es|en)/", low):
        kind, label, quality, action, score = "station_page", "Station page", "exact", "Open Canarias te recarga", 104
    elif re.search(r"openstreetmap\.org/(node|way|relation)/", low):
        kind, label, quality, action, score = "map_object", "Map object", "map", "Open OSM object", 92
    elif "mapareve.es" in low:
        kind, label, quality, action, score = "live_map", "Live map", "map", "Open live source", 86
    elif re.search(r"geoportal\.valencia|arcgis|mapserver", low):
        kind, label, quality, action, score = "official_data", "City data", "official", "Open city source", 78
    elif "tesla.com/findus" in low:
        kind, label, quality, action, score = "provider_locator", "Tesla locator", "locator", "Check Tesla", 70
    elif re.search(r"lidl|mercadona|consum|official", low):
        kind, label, quality, action, score = "official_list", "Official list", "list", "Open official source", 62
    elif re.search(r"google\.com/maps|maps\.app\.goo\.gl|maps\.apple\.com", low):
        kind, label, quality, action, score = "map_search", "Map search", "generic", "Open map search", 30
    elif re.search(r"iberdrola|acciona|eurocharger|letmecharge|miio|tesla|endesa|repsol|erano", low):
        kind, label, quality, action, score = "provider_homepage", "Provider page", "generic", f"Open {name}", 45

    warning = None
    if quality in {"list", "locator", "generic"}:
        warning = "This source may be a locator/list/homepage rather than an exact station/start page."
    return {
        **source,
        "kind": kind,
        "label": label,
        "quality": quality,
        "action": action,
        "score": score,
        "warning": warning,
    }


# These parser-owned identities prove an operator feed record, not an exact page.
# Keep this allowlist separate from URL classification: a map link alone proves nothing.
_OPERATOR_MAP_RECORD_SOURCES = {
    "Etecnic EVcharge public charger map": (
        "etecnic_charger_id", int, "https://etecnic.es/mapa-de-recarga/",
    ),
    "Atlante public charger map": (
        "atlante_location_id", str, "https://map.atlante.energy/",
    ),
    "Zunder public charger map": (
        "zunder_location_id", str, "https://www.zunder.com/mapa-de-ubicaciones/",
    ),
    "Repsol public charging point finder": (
        "repsol_point_id", str, "https://www.repsol.es/buscador-eess-y-puntos-de-recarga/",
    ),
}


def telpark_parking_detail_url(url: Any) -> bool:
    """Observed Spanish parking-detail shape, not a host-wide URL allowlist."""
    return isinstance(url, str) and re.fullmatch(
        r"https://www\.telpark\.com/es/ciudades/[a-z0-9]+(?:-[a-z0-9]+)*/[a-z0-9]+(?:-[a-z0-9]+)*/", url,
    ) is not None


def _telpark_record_source(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    name = "Telpark public parking pages"
    if item.get("source") != name:
        return None
    raw = item.get("raw")
    url = item.get("source_url")
    if not isinstance(raw, dict) or not telpark_parking_detail_url(url):
        return None
    stable_id = raw.get("telpark_parking_id")
    if (type(stable_id) is not str or re.fullmatch(r"[1-9][0-9]*", stable_id) is None
            or raw.get("telpark_detail_url") != url or raw.get("ev_charging") is not True
            or raw.get("ev_flag_source") != "detail_jsonld_amenityFeature"
            or raw.get("coordinate_source") != "telpark_detail_jsonld_geo" or raw.get("country") != "ES"):
        return None
    coordinates = []
    for field, limit in (("lat", 90), ("lon", 180)):
        value = item.get(field)
        if isinstance(value, bool):
            return None
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(value) or not -limit <= value <= limit:
            return None
        coordinates.append(value)
    if coordinates == [0, 0]:
        return None
    return {
        "name": name, "url": url,
        "evidence": "Telpark parking page advertises EV charging; exact bays and plugs are unknown.",
        "kind": "official_data", "label": "Parking with EV charging", "quality": "official",
        "action": "Open Telpark parking page", "score": 78,
        "warning": "Parking location only, not an exact charging bay or station start page. Plugs, power, tariff and availability need checking.",
    }


def _operator_record_source(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Admit only a located operator record with its own matching raw identity.

    Child evidence cannot supply the missing primary provenance. The existing
    official_data token describes coordinate evidence, not live or price authority.
    """
    if item.get("source") == "Telpark public parking pages":
        return _telpark_record_source(item)
    name = item.get("source")
    proof = _OPERATOR_MAP_RECORD_SOURCES.get(name) if isinstance(name, str) else None
    if proof is None:
        return None
    id_field, id_type, url = proof
    raw = item.get("raw")
    if not isinstance(raw, dict) or item.get("source_url") != url:
        return None
    stable_id = raw.get(id_field)
    if type(stable_id) is not id_type:
        return None
    if isinstance(stable_id, str) and not stable_id.strip():
        return None
    if isinstance(stable_id, int) and stable_id <= 0:
        return None
    for field, limit in (("lat", 90), ("lon", 180)):
        value = item.get(field)
        if isinstance(value, bool):
            return None
        try:
            coordinate = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(coordinate) or not -limit <= coordinate <= limit:
            return None

    evidence = next(
        (str(source.get("evidence") or "") for source in item.get("sources") or []
         if isinstance(source, dict) and source.get("name") == name and source.get("url") == url),
        "",
    )
    return {
        "name": name,
        "url": url,
        "evidence": evidence,
        "kind": "official_data",
        "label": "Operator map",
        "quality": "official",
        "action": "Open operator map",
        "score": 78,  # Existing official_data score; station/start pages still win.
        "warning": (
            "This link opens the operator map, not an exact station/start page. "
            "Availability and tariff still need checking."
        ),
    }


def primary_source_for(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Choose the strongest source, preferring exact pages and richer tied evidence."""
    ranked = [classify_source_resource(source) for source in source_candidates(item)]
    operator_record = _operator_record_source(item)
    if operator_record is not None:
        ranked.append(operator_record)
    if not ranked:
        return None
    ranked.sort(
        key=lambda source: (source.get("score") or 0, len(source.get("evidence") or "")),
        reverse=True,
    )
    primary = dict(ranked[0])
    primary.pop("score", None)
    return primary
