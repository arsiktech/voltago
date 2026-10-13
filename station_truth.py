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
    if ("CCS" in normalized or "COMBO" in normalized) and "UNSPECIFIED" in normalized:
        return "CCS (unspecified)"
    # The two CCS standards are physically different. Match explicit Type 1
    # before the legacy European shorthand so a US plug never becomes CCS2.
    if ("CCS" in normalized or "COMBO" in normalized) and re.search(
        r"(?:CCS[ _-]*1|(?:TYPE[ _-]*|T)1(?:\b|_)|J1772)", normalized
    ):
        return "CCS1"
    if "CCS" in normalized or "COMBO" in normalized:
        return "CCS2"
    if "CHADEMO" in normalized:
        return "CHAdeMO"
    if "NACS" in normalized or "J3400" in normalized or (
        "TESLA" in normalized and "NORTH AMERICA" in normalized
    ):
        return "NACS"
    if re.search(r"GB[ /_-]*T", normalized):
        if "DC" in normalized or "20234.3" in normalized:
            return "GB/T DC"
        if "AC" in normalized or "20234.2" in normalized:
            return "GB/T AC"
        return "GB/T"
    if "TESLA" in normalized:
        return "Tesla"
    if any(alias in normalized for alias in ("T2", "MENNEKES", "TYPE 2", "TIPO 2")):
        return "Type 2"
    if any(alias in normalized for alias in ("SCHUKO", "SHUKO", "DOMESTIC", "SCEE")):
        return "Schuko"
    if "T1" in normalized or "TYPE 1" in normalized or "J1772" in normalized:
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


_DIRECTORY_RECORDS = {
    "etecnic": ("Etecnic EVcharge public charger map", "etecnic_charger_id", "https://etecnic.net/api/v1/chargers/index.json", "https://etecnic.es/mapa-de-recarga/"),
    "atlante": ("Atlante public charger map", "atlante_location_id", "https://map.atlante.energy/geodata.json", "https://map.atlante.energy/"),
    "zunder": ("Zunder public charger map", "zunder_location_id", "https://www.zunder.com/mapa-de-ubicaciones/", "https://www.zunder.com/mapa-de-ubicaciones/"),
    "mot_reus": ("MOT (Aparcar) — Ajuntament de Reus municipal GIS", "reus_feature_id", "https://geoportal.reus.cat/apps/giscube-admin/layerserver/geojsonlayers/carregadors-municipals", None),
    "mot_tarragona": ("MOT (Aparcar) — Ajuntament de Tarragona open data", "tarragona_nom", "https://opendatafiles.tarragona.cat/03303.csv", "https://opendatafiles.tarragona.cat/03303.csv"),
    "interparking": ("P-APP (Interparking) public car park finder", "interparking_external_id", "https://www.interparking.es/es/encuentra-un-aparcamiento/", None),
}


def _directory_record_source(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Coordinate proof from a parsed public feed, never live or tariff proof.

    Generic source links and child evidence cannot grant map eligibility. Require
    the matching primary source, acquisition URL, stable upstream ID and position.
    """
    raw = item.get("raw")
    if not isinstance(raw, dict):
        return None
    coords = []
    for field, limit in (("lat", 90), ("lon", 180)):
        value = item.get(field)
        if type(value) not in (int, float) or not math.isfinite(value) or not -limit <= value <= limit:
            return None
        coords.append(value)
    if coords == [0, 0]:
        return None
    source, url = item.get("source"), item.get("source_url")
    fetch = item.get("source_fetch") or {}
    if not isinstance(fetch, dict) or not fetch.get("fetched_at"):
        return None
    proof = _DIRECTORY_RECORDS.get(item.get("source_adapter"))
    parking = False
    if proof:
        name, id_field, feed_url, exact_url = proof
        sid = raw.get(id_field)
        if source != name or fetch.get("url") != feed_url or isinstance(sid, bool) or not isinstance(sid, (str, int)) or not str(sid).strip():
            return None
        if exact_url is not None and url != exact_url:
            return None
        if item["source_adapter"] == "mot_reus" and (not isinstance(url, str) or not re.fullmatch(r"https://(?:www\.)?reus\.cat/[^?#]*", url)):
            return None
        if item["source_adapter"] == "interparking":
            if (not isinstance(url, str) or not re.fullmatch(r"https://www\.interparking\.es/es/(?:encuentra-un-aparcamiento/|aparcamientos/[a-z0-9/_-]+/)", url)
                    or not str(sid).startswith("es-") or raw.get("ev_facility_flag") is not True):
                return None
            parking = True
    elif source == "Telpark public parking pages":
        if (not isinstance(url, str) or re.fullmatch(r"https://www\.telpark\.com/es/ciudades/[a-z0-9]+(?:-[a-z0-9]+)*/[a-z0-9]+(?:-[a-z0-9]+)*/", url) is None
                or re.fullmatch(r"[1-9][0-9]*", str(raw.get("telpark_parking_id") or "")) is None
                or raw.get("telpark_detail_url") != url or raw.get("ev_charging") is not True
                or raw.get("country") != "ES" or raw.get("ev_flag_source") != "detail_jsonld_amenityFeature"
                or raw.get("coordinate_source") != "telpark_detail_jsonld_geo"):
            return None
        artifacts = raw.get("source_fetch_artifacts") or []
        if not any(isinstance(a, dict) and a.get("url") == url and a.get("fetched_at") for a in artifacts):
            return None
        parking = True
    elif source == "Elektrum Drive public charging points":
        expected = "https://elektrum.ee/en/elektrum-drive/charging-solutions/public-charging-points"
        if (url != expected or fetch.get("url") != expected or raw.get("source") != "elektrum_drive"
                or not raw.get("source_id") or raw.get("country") not in {"EE", "LV", "LT"}
                or item.get("country_code") != raw.get("country")
                or raw.get("lat") != item.get("lat") or raw.get("lon") != item.get("lon")):
            return None
    else:
        return None
    return {"name": source, "url": url, "evidence": item.get("insight") or "Public operator coordinate",
            "kind": "official_data", "quality": "official", "score": 78,
            "label": "Parking with EV charging" if parking else "Operator map",
            "action": "Open operator page",
            "warning": ("Parking location; exact charging bays need checking. " if parking else "Operator location; exact station/start page may be unavailable. ")
                       + "Charging price and current availability are unverified."}


def primary_source_for(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Choose the strongest exact source, preferring richer evidence on score ties."""
    ranked = [classify_source_resource(source) for source in source_candidates(item)]
    directory = _directory_record_source(item)
    if directory is not None:
        ranked.append(directory)
    if not ranked:
        return None
    ranked.sort(
        key=lambda source: (source.get("score") or 0, len(source.get("evidence") or "")),
        reverse=True,
    )
    primary = dict(ranked[0])
    primary.pop("score", None)
    return primary
