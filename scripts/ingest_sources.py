#!/usr/bin/env python3
"""Build Volta's cheap/free charging intelligence database.

This script intentionally uses only public/no-private-auth sources. It stores
source evidence and confidence instead of pretending that every directory is live.
"""
from __future__ import annotations

import gzip
import hashlib
import html
import http.cookiejar
import argparse
import csv
import io
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

try:
    from .source_policy import (
        load_source_policy_exclusions,
        normalize_policy_text,
        partition_records,
        recordset_sha256,
        value_is_excluded,
    )
    from .source_transport import (
        PLACETOPLUG_MAX_LIMIT,
        PLACETOPLUG_OPERATION,
        PLACETOPLUG_QUERY,
        PLACETOPLUG_URL,
        SourcePolicyBlocked,
        policy_urlopen,
    )
    from .station_identity import identity_keys, primary_identity_keys
    from .spain_geography import point_in_spain
except ImportError:  # Direct `python3 scripts/ingest_sources.py` execution.
    from source_policy import (
        load_source_policy_exclusions,
        normalize_policy_text,
        partition_records,
        recordset_sha256,
        value_is_excluded,
    )
    from source_transport import (
        PLACETOPLUG_MAX_LIMIT,
        PLACETOPLUG_OPERATION,
        PLACETOPLUG_QUERY,
        PLACETOPLUG_URL,
        SourcePolicyBlocked,
        policy_urlopen,
    )
    from station_identity import identity_keys, primary_identity_keys
    from spain_geography import point_in_spain

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from station_truth import (  # noqa: E402
    classify_source_resource,
    normalize_connector_label as connector_label_from_token,
    normalize_public_source_url,
    primary_source_for,
    source_candidates,
)
DATA = ROOT / "data"
CACHE = DATA / "cache"
OUT = DATA / "free_intelligence.json"
SOURCE_POLICY_EXCLUSIONS = load_source_policy_exclusions(DATA / "source_policy_exclusions.json")
CACHE.mkdir(parents=True, exist_ok=True)

UA = "Mozilla/5.0 (Volta public-source EV charger research; contact via app owner)"
VALENCIA_BBOX = (38.65, -1.60, 40.20, 0.05)  # south, west, north, east
VALENCIA_CITY_BBOX = (39.30, -0.55, 39.60, -0.20)
SPAIN_BBOX = (27.0, -18.5, 44.5, 5.0)  # south, west, north, east — mainland, Balearics, Canaries, Ceuta, Melilla
# Tight mainland/Balearic tiles keep the existing Overpass cache. Island tiles are extra.
MAINLAND_TILE_BBOX = (35.85, -9.45, 43.95, 4.45)
CANARY_TILE_BBOX = (27.60, -18.20, 29.50, -13.30)
CEUTA_TILE_BBOX = (35.85, -5.40, 35.92, -5.28)
MELILLA_TILE_BBOX = (35.26, -2.98, 35.33, -2.90)
# Overpass is a shared, donation-funded service with no API key and an honour-system rate limit.
# The first national run fired 16 tiles back-to-back and got 429/504 on 11 of them, so the pass
# now pauses between tiles and backs off before retrying. Slower is the point.
OVERPASS_TILE_PAUSE_SECONDS = 3.0
OVERPASS_BACKOFF_SECONDS = 15.0
# A 504 means the box is too expensive for Overpass to compute, so it gets split into quarters
# and retried. Two levels turn one national tile into at most 16 much cheaper boxes.
OVERPASS_MAX_SUBDIVISION = 2
BARCELONA_ENDOLLA_LOCATIONS_URL = "https://opendata-ajuntament.barcelona.cat/data/dataset/8cdafa08-d378-4bf1-aad4-fafffe815940/resource/9febc26f-d6a7-45f2-8f73-f529ba4da930/download"
BARCELONA_ENDOLLA_STATUS_URL = "https://opendata-ajuntament.barcelona.cat/data/dataset/a2bd4c83-d024-4d78-8436-040ef996cf7f/resource/ada4c823-9566-477d-9362-7b15e7d38189/download"
BARCELONA_ENDOLLA_TARIFF_URL = "https://endolla.barcelona/ca/modalitats-de-pagament"
BARCELONA_ENDOLLA_FAQ_URL = "https://endolla.barcelona/es/faqs"
ENDOLLA_STATUS_MAX_AGE_SECONDS = 20 * 60
ENDOLLA_TARIFF_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
COMMUNITY_PRICE_EVIDENCE_MAX_AGE_DAYS = 730
OPENCHARGEMAP_QUARANTINED_IDS = {"169683"}
MADRID_CHARGERS_CSV_URL = "https://geoportal.madrid.es/fsdescargas/IDEAM_WBGEOPORTAL/MOVILIDAD/VEHICULOS_ELECTRICOS/CSV/PUNTOS_PUBLICOS_RECARGA_VEHICULOS_ELECTRICOS.csv"
MADRID_CHARGERS_CATALOG_URL = "https://datos.madrid.es/dataset/208979-0-puntos-recarga-historico"
# Canarias te recarga: a public Canary Islands charger map (Maps Marker Pro on WordPress).
# The map export renders every marker server-side, including the tariff badge; the AJAX
# marker route returns the same stations with per-marker icons but no popup text. Volta
# reads the export only (single bounded request, cached) and never claims live availability.
CANARIAS_TE_RECARGA_MAP_ID = "4"
CANARIAS_TE_RECARGA_GEOJSON_URL = "https://canariasterecarga.com/maps/export/geojson/4/"
CANARIAS_TE_RECARGA_MAP_PAGE_URL = "https://canariasterecarga.com/es/mapa/"
CANARIAS_TE_RECARGA_CACHE_NAME = "canarias_tecarga_map4.geojson.json"
CANARIAS_TE_RECARGA_MIN_FEATURES = 180
# The site's own marker scheme (uploads/maps-marker-pro/icons/). The free icon is only
# trusted when the popup prints no tariff text at all: an explicit badge always wins,
# because markers with a stale icon exist (verified 2026-09-16 against station pages).
# Paid tariff classes are admitted with their class price and ranked after free/cheap.
CANARIAS_TE_RECARGA_ICON_PRICES = {
    "icono_free.png": 0.0,
    "icono_mini.png": 0.28,
    "icono_super.png": 0.38,
    "icono_mega.png": 0.45,
}
CANARIAS_TE_RECARGA_CONNECTOR_ICONS = {
    "ev-plug-type2.svg": "Type 2",
    "ev-plug-ccs2.svg": "CCS2",
    "ev-plug-ccs2-1.svg": "CCS2",
    "ev-plug-chademo.svg": "CHAdeMO",
    "ev-plug-type1.svg": "Type 1",
}
# MapaREVE public OCPI web API (MITECO + Red Eléctrica). No API key: the browser-facing
# route accepts a read-only bbox data query (POST, an owner-approved narrow exception
# enforced by scripts/source_transport.py) with browser-like headers. per_page is capped at 20 server-side,
# so the Tenerife slice is paginated (~12 pages) with a pause between live fetches.
MAPAREVE_PUBLIC_LOCATIONS_URL = "https://www.mapareve.es/api/public/v1/locations"
MAPAREVE_PUBLIC_MAP_PAGE_URL = "https://www.mapareve.es/mapa-puntos-recarga"
MAPAREVE_PUBLIC_TENERIFE_BBOX = {
    "latitude_ne": 28.7,
    "longitude_ne": -15.9,
    "latitude_sw": 28.0,
    "longitude_sw": -17.0,
}
MAPAREVE_PUBLIC_MAX_PAGES = 14
MAPAREVE_PUBLIC_MIN_ROWS = 150
MAPAREVE_PUBLIC_PAGE_PAUSE_SECONDS = 1.0
MAPAREVE_PUBLIC_TTL_HOURS = 12
ZARAGOZA_CHARGERS_URL = "https://www.zaragoza.es/sede/portal/movilidad/puntos-recarga"
ZARAGOZA_CATEGORY_URL = "https://www.zaragoza.es/sede/servicio/equipamiento/category/2200.json?srsname=wgs84"
ZARAGOZA_REUSE_URL = "https://www.zaragoza.es/sede/portal/aviso-legal#condiciones"
RED_MOVEM_TARIFF_URL = "https://www.dip-badajoz.es/toolsphp/descarga.php?fr=6&id=bop_2024-07-08000000_firmado.pdf"
RED_MOVEM_CURRENT_URL = "https://sostenibilidad.dip-badajoz.es/entrada/noticias/la-diputacion-de-badajoz-inicia-los-trabajos-de-ampliacion-y-repotenciacion-de-la-red-provincial-de-recarga-de-vehiculos-electricos"
RED_MOVEM_TARIFF_CHECKED_AT = "2026-07-15T00:00:00Z"
RED_MOVEM_REVIEW_MAX_AGE_DAYS = 90
MORALEJA_FREE_URL = "https://www.acciona.com/es/gsc"
MORALEJA_VENUE_URL = "https://moralejagreen.com/servicios/"
MORALEJA_EVIDENCE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
ACCESS_LIMIT_RE = re.compile(r"private|staff|visitor|customer|notice|required|membership|members", re.I)
BAD_STATION_NAME_RE = re.compile(r"\b(?:n\s*['’]?\s*existe\s+pas|nexiste\s+pas|no\s+existe|does\s+not\s+exist|nonexistent|removed|inexistant|supprim[ée]e?|d[ée]mont[ée]e?|demontee|doublon|duplicate)\b", re.I)
DEAD_CHARGEMAP_SLUGS = {
    # Verified 2026-06-22 by scripts/qa_source_links.py: page returns HTTP 404.
    "casa-ermita",
}
LIDL_COORD_HINTS = {
    "46183": (39.5583911, -0.5068073, "nominatim_street"),
    "46940": (39.4922645, -0.4674381, "nominatim_street"),
    "46136": (39.5634047, -0.3334377, "nominatim_street"),
    "46200": (39.4259560, -0.4049760, "nominatim_lidl_poi"),
    "46015": (39.4816455, -0.4014497, "nominatim_lidl_poi"),
    "46910": (39.4172579, -0.3804452, "nominatim_lidl_poi"),
    "46500": (39.6836874, -0.2702085, "nominatim_street"),
}
# Official page sections Volta ingests, with the postcode prefix each one must carry. The
# page also lists other provinces; those stores stay out until their coordinates are
# verified store by store (a guessed pin is worse than no pin).
LIDL_SECTIONS = {
    "Valencia": "46",
    "Fuerteventura": "35",
    "Gran Canaria": "35",
    "Lanzarote": "35",
    "Tenerife": "38",
    "La Palma": "38",
}
# The page prefixes Canary rows with the province ("Las Palmas", "Santa Cruz de Tenerife"),
# which is not the town; those rows fall back to the section heading instead.
LIDL_PROVINCE_HEADS = {"valencia", "valència", "las palmas", "santa cruz de tenerife"}
# Store label (as printed by the official page) -> pinned coordinates, coordinate method and
# the evidence behind it. Pinned so a weekly rebuild cannot drift onto a different point or
# depend on a live geocoder for ids the delta gate expects to still exist. Every entry was
# reverse-geocoded to its own store before being written here; the audit trail is
# qa/rebrand-2026-09-16/lidl-canary-verification.json.
LIDL_STORE_COORDS = {
    "Puerto de la Cruz - Ctra Icod-Sta Cruz s/n (CP 38400)": (28.39831, -16.54171, "osm_lidl_store_poi",
        "OpenStreetMap way/91505373 'Carretera Gral. Icod-Santa Cruz, Puerto de la Cruz 38400'"),
    "Icod de Los Vinos - Ctra. General del Norte, 26 (CP 38434)": (28.38029, -16.68775, "osm_lidl_store_poi",
        "OpenStreetMap node/1293317790 'Carretera General del Norte 28-30, Icod de los Vinos 38434'"),
    "Adeje - Calle Charfa, 28, Urb. Barranco del Inglés, ( CP 38670 )": (28.12859, -16.7429, "osm_lidl_store_poi",
        "OpenStreetMap way/170785684 'Lidl, 38670 Adeje'; 300 m from Calle Charfa"),
    "San Isidro - Avenida de la Democracia, s/n, s/n,(CP 3861)": (28.07329, -16.54958, "osm_lidl_store_poi",
        "OpenStreetMap way/462719671 'Avenida de la Democracia 16, San Isidro 38611'"),
    "San Miguel de Abona - Calle Siete Islas Canarias, 132 (CP 38639)": (28.05152, -16.62175, "osm_lidl_store_poi",
        "OpenStreetMap way/310879522 'Avenida Siete Islas Canarias 132, 38639'"),
    "San Cristóbal de La Laguna - Calle Ntra. Sra. de la Ternura (CP 38108)": (28.45715, -16.30338, "osm_lidl_store_poi",
        "OpenStreetMap way/199082542; 100 m from Calle Ntra. Sra. de la Ternura, La Laguna"),
    "Santa Cruz de Tenerife - Calle Álvaro Rodríguez López 2 (CP 38005)": (28.45818, -16.25843, "osm_lidl_store_poi",
        "OpenStreetMap node/6339297985 'Lidl, Calle Álvaro Rodríguez López, Salud-La Salle'"),
    "Santa Cruz de Tenerife - Polígono Industrial el Carretón (CP 38550)": (28.3307, -16.37667, "osm_lidl_store_poi",
        "OpenStreetMap node/4354669954; only Lidl POI in the 38550 Arafo/Güímar estate"),
    "Santa Cruz de Tenerife - Carretera General La Cuesta- Taco KM 1,1, 38420, La Laguna": (28.46093, -16.2917, "osm_lidl_store_poi",
        "OpenStreetMap way/651534191 'Lidl, Carretera General La Cuesta-Taco'"),
    "Santa Úrsula - Avenida de las Palmeras, 3 (CP 38390)": (28.42734, -16.48916, "osm_lidl_store_poi",
        "OpenStreetMap way/403187878 'Avenida las Palmeras 3, Santa Úrsula 38390'"),
    "Santa Cruz de Tenerife - Carretera General del Sur s/n, 38109": (28.42159, -16.31954, "osm_lidl_store_poi",
        "OpenStreetMap way/1487729786 'Lidl, Carretera General del Sur, El Chorrillo, La Gallega'"),
    "C/ Tenerife, s/n 38612 El Médano": (28.04591, -16.53995, "nominatim_street",
        "Nominatim street match 'Calle Tenerife, El Médano, Granadilla de Abona 38612'"),
    "Los Llanos - Calle Ramón Pol, 7 (CP 38760)": (28.66105, -17.91582, "osm_lidl_store_poi",
        "OpenStreetMap way/250850900 'Calle Ramón Poll, Los Llanos de Aridane 38760'"),
    "Las Palmas - Calle El Guirre, s/n (CP 35640)": (28.71184, -13.87063, "osm_lidl_store_poi",
        "OpenStreetMap way/1029030184 'Calle Guirre 11, Corralejo 35660'"),
    "Las Palmas de Gran Canaria - Autovía GC - 3 , Salida Tamaraceite, (CP 35010)": (28.09925, -15.44314, "osm_lidl_store_poi",
        "OpenStreetMap way/755014390 'Carretera del Norte 163, 35013' (Tamaraceite)"),
    "Arrecife - Pol. Ind. Playa Honda (CP 35509)": (28.95922, -13.5915, "osm_lidl_store_poi",
        "OpenStreetMap way/670322315 'Lidl, San Bartolomé' (Playa Honda estate)"),
    "C/ Industrial,s/n, 35580, Yaiza, Lanzarote": (28.88056, -13.83346, "osm_lidl_store_poi",
        "OpenStreetMap way/1506202906 'Lidl, Yaiza'"),
}
# The page repeats a few stores as a second (sometimes coordinate-bearing) row. One store
# must stay one pin: these labels are recognised and skipped after the first mention.
LIDL_DUPLICATE_LABELS = {
    "ctra. general del norte, 26 - 38434, icod de los vinos (tenerife)",
    "avda. ramon poll s/n, 38760, los llanos de aridane",
}


TESLA_OK_LABELS = {"Type 2", "CCS2", "Tesla CCS", "Tesla"}


def tesla_compat(labels: Any) -> Optional[bool]:
    """None when the source names no specific connector: unknown is not incompatible."""
    labels = [str(x) for x in labels or []]
    if any(x in TESLA_OK_LABELS for x in labels):
        return True
    if any(x.strip() and "unspecified" not in x.lower() and x.strip().lower() not in {"unknown", "unknown connector", "connector", "other", "n/a", "none"} for x in labels):
        return False
    return None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def epoch_iso(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class FetchArtifact:
    """Bytes plus the provenance of the network/cache evidence that supplied them."""

    body: bytes
    url: str
    cache_name: str
    fetched_at: str
    age_seconds: float
    hit: bool
    fallback: bool
    stale: bool
    error: Optional[str] = None

    def decode(self, encoding: str = "utf-8", errors: str = "strict") -> str:
        return self.body.decode(encoding, errors)

    def as_evidence(self) -> Dict[str, Any]:
        evidence: Dict[str, Any] = {
            "url": self.url,
            "cache_name": self.cache_name,
            "fetched_at": self.fetched_at,
            "age_seconds": round(self.age_seconds, 3),
            "cache": {
                "hit": self.hit,
                "fallback": self.fallback,
                "stale": self.stale,
            },
        }
        if self.error:
            evidence["error"] = self.error
        return evidence


FETCH_EVIDENCE: List[Dict[str, Any]] = []


def remember_fetch_artifact(artifact: FetchArtifact) -> FetchArtifact:
    # Retain only metadata for the build report; keeping every response body here
    # would duplicate the full source corpus in memory until the build completes.
    FETCH_EVIDENCE.append(artifact.as_evidence())
    return artifact


def fetch_evidence_payload() -> Dict[str, Any]:
    artifacts = list(FETCH_EVIDENCE)
    return {
        "summary": {
            "total": len(artifacts),
            "network": sum(not artifact["cache"]["hit"] for artifact in artifacts),
            "cache_hits": sum(artifact["cache"]["hit"] for artifact in artifacts),
            "fallbacks": sum(artifact["cache"]["fallback"] for artifact in artifacts),
            "stale": sum(artifact["cache"]["stale"] for artifact in artifacts),
            "stale_fallbacks": sum(
                artifact["cache"]["fallback"] and artifact["cache"]["stale"]
                for artifact in artifacts
            ),
        },
        "artifacts": artifacts,
    }


def cache_file_artifact(
    path: Path,
    *,
    url: str,
    cache_name: str,
    ttl_hours: float,
    fallback: bool = False,
    error: Optional[str] = None,
    now: Optional[float] = None,
) -> FetchArtifact:
    checked_at = time.time() if now is None else now
    cached_at = path.stat().st_mtime
    age_seconds = max(0.0, checked_at - cached_at)
    return remember_fetch_artifact(FetchArtifact(
        body=path.read_bytes(),
        url=url,
        cache_name=cache_name,
        fetched_at=epoch_iso(cached_at),
        age_seconds=age_seconds,
        hit=True,
        fallback=fallback,
        stale=age_seconds >= ttl_hours * 3600,
        error=error,
    ))


def raw_timestamp_value(value: Any) -> Optional[str]:
    if value in (None, "", "null", "None"):
        return None
    if isinstance(value, dict):
        value = value.get("$date") or value.get("date") or value.get("value")
    if value in (None, "", "null", "None"):
        return None
    return str(value)


def raw_last_seen(raw: Dict[str, Any]) -> Optional[str]:
    for key in ("LastVerified", "DateLastVerified", "DateLastStatusUpdate", "DateLastConfirmed", "last_seen", "lastSeen", "last_used_time", "lastUsedTime", "updated_at", "updatedAt", "creation_date", "osm_last_modified"):
        value = raw_timestamp_value(raw.get(key))
        if value:
            return value
    return None


def fetch(url: str, *, data: Optional[bytes] = None, headers: Optional[Dict[str, str]] = None, timeout: int = 35) -> bytes:
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    # Method is explicit: no body means GET. A body is a POST that only
    # scripts/source_transport.py's reviewed read-only query exceptions (MapaREVE bbox
    # query, Feníe marker list, Repsol list, Place to Plug zone query) may send; any other POST raises SourcePolicyBlocked
    # before a socket opens, and exception POSTs never follow redirects.
    req = urllib.request.Request(url, data=data, headers=h, method="POST" if data is not None else "GET")
    with policy_urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_json(url: str, *, headers: Optional[Dict[str, str]] = None, timeout: int = 35) -> Any:
    return json.loads(fetch(url, headers=headers or {"Accept": "application/json"}, timeout=timeout).decode("utf-8", "replace"))


def cached_fetch(url: str, cache_name: str, ttl_hours: float = 24, *, fetcher=None, cache_only: bool = False, **kw: Any) -> FetchArtifact:
    path = CACHE / cache_name
    if cache_only:
        if not path.exists():
            raise FileNotFoundError(cache_name)
        return cache_file_artifact(path, url=url, cache_name=cache_name, ttl_hours=ttl_hours)
    checked_at = time.time()
    if path.exists() and checked_at - path.stat().st_mtime < ttl_hours * 3600:
        return cache_file_artifact(
            path,
            url=url,
            cache_name=cache_name,
            ttl_hours=ttl_hours,
            now=checked_at,
        )
    try:
        body = (fetcher or fetch)(url, **kw)
    except Exception as exc:
        if path.exists():
            return cache_file_artifact(
                path,
                url=url,
                cache_name=cache_name,
                ttl_hours=ttl_hours,
                fallback=True,
                error=type(exc).__name__,
            )
        raise
    fetched_at = time.time()
    path.write_bytes(body)
    os.utime(path, (fetched_at, fetched_at))
    return remember_fetch_artifact(FetchArtifact(
        body=body,
        url=url,
        cache_name=cache_name,
        fetched_at=epoch_iso(fetched_at),
        age_seconds=0.0,
        hit=False,
        fallback=False,
        stale=False,
    ))


def cached_json(url: str, cache_name: str, ttl_hours: float = 24, *, with_artifact: bool = False, **kw: Any) -> Any:
    artifact = cached_fetch(url, cache_name, ttl_hours=ttl_hours, **kw)
    payload = json.loads(artifact.decode("utf-8", "replace"))
    return (payload, artifact) if with_artifact else payload


def unpack_json_artifact(value: Any) -> Tuple[Any, Optional[FetchArtifact]]:
    """Accept the provenance return shape while keeping parser-test fakes lightweight."""
    if (
        isinstance(value, tuple)
        and len(value) == 2
        and isinstance(value[1], FetchArtifact)
    ):
        return value[0], value[1]
    return value, None


def fetch_artifact_or_none(value: Any) -> Optional[FetchArtifact]:
    return value if isinstance(value, FetchArtifact) else None


OVERPASS_INTERPRETER_URL = "https://overpass-api.de/api/interpreter"


def cached_overpass(query: str, cache_name: str, ttl_hours: float = 12) -> FetchArtifact:
    # Overpass serves the same read-only query via GET (?data=); no POST exception is
    # granted for it.
    return cached_fetch(
        f"{OVERPASS_INTERPRETER_URL}?{urllib.parse.urlencode({'data': query})}",
        cache_name,
        ttl_hours=ttl_hours,
        headers={"User-Agent": "Volta public-source EV charger research"},
        timeout=80,
    )


def in_bbox(lat: Optional[float], lon: Optional[float], bbox: Tuple[float, float, float, float] = VALENCIA_BBOX) -> bool:
    if lat is None or lon is None:
        return False
    s, w, n, e = bbox
    return s <= lat <= n and w <= lon <= e


def likely_valencia_province(lat: Optional[float], lon: Optional[float], tags: Optional[Dict[str, Any]] = None) -> bool:
    if not in_bbox(lat, lon):
        return False
    tags = tags or {}
    postcode = str(tags.get("addr:postcode") or tags.get("postal_code") or "")
    if postcode:
        return postcode.startswith("46")
    text = " ".join(str(tags.get(k) or "") for k in ("addr:province", "addr:county", "addr:city", "is_in", "name"))
    if re.search(r"castell[oó]|alicante|alacant", text, re.I):
        return False
    # The broad Valencia bbox clips neighbouring provinces. These guards keep
    # Sagunt/Utiel/Oliva but remove common Castellón and Alicante edge hits.
    return lat is not None and 38.85 <= lat <= 39.74


# Spanish province by postal-code prefix — honest province-level city labels nationwide.
PROVINCE_BY_POSTAL2 = {
    "01": "Álava", "02": "Albacete", "03": "Alicante", "04": "Almería", "05": "Ávila",
    "06": "Badajoz", "07": "Illes Balears", "08": "Barcelona", "09": "Burgos", "10": "Cáceres",
    "11": "Cádiz", "12": "Castelló", "13": "Ciudad Real", "14": "Córdoba", "15": "A Coruña",
    "16": "Cuenca", "17": "Girona", "18": "Granada", "19": "Guadalajara", "20": "Gipuzkoa",
    "21": "Huelva", "22": "Huesca", "23": "Jaén", "24": "León", "25": "Lleida", "26": "La Rioja",
    "27": "Lugo", "28": "Madrid", "29": "Málaga", "30": "Murcia", "31": "Navarra", "32": "Ourense",
    "33": "Asturias", "34": "Palencia", "35": "Las Palmas", "36": "Pontevedra", "37": "Salamanca",
    "38": "S. C. de Tenerife", "39": "Cantabria", "40": "Segovia", "41": "Sevilla", "42": "Soria",
    "43": "Tarragona", "44": "Teruel", "45": "Toledo", "46": "València", "47": "Valladolid",
    "48": "Bizkaia", "49": "Zamora", "50": "Zaragoza", "51": "Ceuta", "52": "Melilla",
}


def derive_city(raw: Optional[Dict[str, Any]], lat: Optional[float] = None, lon: Optional[float] = None) -> str:
    """Honest city/province label nationwide: province from postcode prefix; else Valencia
    only if inside its box; else unknown (never mislabel a non-Valencia point as Valencia)."""
    raw = raw or {}
    postcode = str(raw.get("addr:postcode") or raw.get("postal_code") or "").strip()
    if len(postcode) >= 2 and postcode[:2].isdigit():
        prov = PROVINCE_BY_POSTAL2.get(postcode[:2])
        if prov:
            return prov
    if in_bbox(lat, lon, VALENCIA_BBOX):
        return "València"
    return ""


# Spanish/Catalan place-name particles that stay lowercase inside a title-cased name.
_PLACE_PARTICLES = {"de", "del", "la", "las", "los", "el", "els", "les",
                    "i", "y", "dels", "des", "da", "do", "na", "en"}


def titlecase_place(name: str) -> str:
    """Title-case an ALL-CAPS place name without mangling particles or accents:
    'BARCELONA' -> 'Barcelona'; 'QUART DE POBLET' -> 'Quart de Poblet'. Words that are
    already mixed-case ('València', "l'Hospitalet") are returned untouched, so this is
    only ever called on an all-caps value -- it must not "fix" a correct name."""
    out: List[str] = []
    for i, word in enumerate(name.split()):
        low = word.lower()
        if i > 0 and low in _PLACE_PARTICLES:
            out.append(low)
        elif word.isupper():
            out.append(low[:1].upper() + low[1:])
        else:
            out.append(word)
    return " ".join(out)


def city_from_address(address: str) -> str:
    """Spanish addresses print the town straight after the 5-digit postcode:
    'C/ de Morella, 86, 46910 Sedaví, Valencia' -> 'Sedaví'. Returns '' when the address
    ends at the postcode or has no postcode at all."""
    if not address:
        return ""
    match = re.search(r"\b\d{5}\b[,\s]+([^,\d][^,]*)", address)
    if not match:
        return ""
    candidate = match.group(1).strip(" ,. ")
    return candidate if 1 < len(candidate) <= 60 else ""


def is_placeholder_city(city: str, address: str = "") -> bool:
    """True when `city` carries no real place information and must be re-derived: empty,
    the historical 'Valencia province' default, or a bare postcode leaked into the city
    field (Electromaps' web-map API returns the postcode where a town name belongs).

    OpenChargeMap can also truncate the street into the city field (for example
    `Via Laet` beside `Via Laetana 69`). Treat only a multi-word value that ends midway
    through the matching address token as a placeholder; one-word real cities such as
    Ronda must remain untouched.
    """
    value = (city or "").strip()
    if (not value) or value == "Valencia province" or re.fullmatch(r"\d{4,5}", value):
        return True
    normalized_city = " ".join(value.casefold().split())
    normalized_address = " ".join((address or "").casefold().split())
    return (
        len(normalized_city.split()) >= 2
        and normalized_address.startswith(normalized_city)
        and len(normalized_address) > len(normalized_city)
        and normalized_address[len(normalized_city)].isalpha()
    )


def is_unverified_free_supercharger(name: str, price_min: Optional[float], reliability: str) -> bool:
    """Drop stale directory/community `Free` claims for Tesla Superchargers.

    Supercharger tariffs are account/time dependent and the public directories currently
    contain legacy zero-price rows. An official source may still establish a genuinely free
    municipal/program station; this guard only rejects lower-confidence evidence families.
    """
    if price_min != 0 or not re.search(r"\b(?:supercharger|supercargador)\b", name or "", re.IGNORECASE):
        return False
    return reliability not in {"official-free", "official-free-policy", "official-cheap"}


def fnum(x: Any) -> Optional[float]:
    try:
        if x is None or x == "":
            return None
        return float(str(x).replace(",", "."))
    except Exception:
        return None


def count_int(x: Any, default: int = 1) -> int:
    try:
        if x is None or x == "" or x is True or str(x).strip().lower() in {"yes", "true"}:
            return default
        value = int(float(str(x).replace(",", ".")))
        return value if value > 0 else default
    except Exception:
        return default


def price_from_text(text: str) -> Optional[float]:
    low = text.lower().replace(" ", "")
    # "Free" must be a standalone price token, not a multi-word English phrase like
    # "Free publicly accessible" (which means access is open, not that charging is free).
    if re.fullmatch(r"(?:gratis|free|gratuita|gratuito|fee=no)", low):
        return 0.0
    if re.fullmatch(r"(?:0|0[,.]0+)(?:€|eur)?", low):
        return 0.0
    m = re.search(r"(?:€|eur)?\s*([0-9]+[,.][0-9]{1,3})\s*(?:€|eur)?\s*/?\s*(?:kw|kwh|kwp)", text, re.I)
    return fnum(m.group(1)) if m else None


def money_from_text(text: Any) -> Optional[float]:
    low = str(text or "").strip().lower()
    if not low:
        return None
    if re.fullmatch(r"(?:0|0[,.]0+)(?:\s*(?:€|eur))?", low):
        return 0.0
    cent = re.search(r"([0-9]+[,.]?[0-9]*)\s*(?:c[eé]ntimos?|cent)\b", low, re.I)
    if cent:
        value = fnum(cent.group(1))
        return value / 100 if value is not None else None
    amounts = re.findall(r"(?:€|eur)\s*([0-9]+[,.]?[0-9]*)|([0-9]+[,.]?[0-9]*)\s*(?:€|eur)", low, re.I)
    nums = [fnum(a or b) for a, b in amounts]
    nums = [n for n in nums if n is not None]
    if nums:
        return max(nums)
    if re.fullmatch(r"[0-9]+[,.]?[0-9]*", low):
        return fnum(low)
    return None


def parking_kind_from_price(text: Any, default_kind: str) -> str:
    low = str(text or "").strip().lower()
    if not low:
        return default_kind
    if re.search(r"\b(?:por ver|unknown|desconocid|sin datos|n/?a)\b", low, re.I):
        return default_kind
    has_free = bool(re.search(r"\b(?:gratis|gratuito|gratuita|free|sin coste)\b|^0\s*€?$", low, re.I))
    amount = money_from_text(low)
    has_paid_phrase = bool(re.search(r"de pago|tarifa|pagar|paga|despu[eé]s|luego|a partir|resto|por hora|hora|€/min|€/h|/min|/h|hotel", low, re.I))
    if has_free and (not amount or amount == 0) and not has_paid_phrase:
        return default_kind if default_kind in {"retail_parking", "facility_or_customer_parking"} else "free_parking"
    if has_free and (has_paid_phrase or (amount is not None and amount > 0)):
        return "time_limited_or_validated"
    if has_paid_phrase or (amount is not None and amount > 0):
        return "paid_or_metered"
    return default_kind


def parking_kind_from_context(name: str, address: str, usage_cost: str, usage_type: str, default_kind: str = "unknown") -> Tuple[str, str]:
    text = " ".join([name or "", address or "", usage_cost or "", usage_type or ""])
    low = text.lower()
    place_text = " ".join([name or "", address or ""]).lower()
    if re.search(r"parking charges apply|paid parking|parking de pago|aparcamiento de pago|aparcament de pagament|but paid parking", low, re.I):
        return "paid_or_metered", "Public source text says charging is free but parking is paid/charged."
    if re.search(r"\b(?:2\s*h|2\s*horas|primeras?\s+\d+\s*h|gratis\s+con\s+compras|validated|validaci[oó]n)\b", low, re.I):
        return "time_limited_or_validated", "Public source text suggests parking is free only with a time limit or validation."
    if re.search(r"\b(?:airport|aeropuerto|aeroport|parking|aparcamiento|aparcament|garaje|garage|subterr[aá]neo|underground)\b", low, re.I):
        return "paid_or_metered", "Name/address indicates a parking facility; assume parking may be charged until onsite/source signs prove otherwise."
    if re.search(r"\b(?:ikea|lidl|aldi|bauhaus|mercadona|consum|carrefour|eroski|decathlon|ahorramas|intermarch[eé]|verdecora|leroy merlin|mcdonald|hotel|restaurant|supermercados?|centro comercial|commercial centre|mar shopping|mall|shop|porsche|volkswagen|nissan|concesionario|dealership|oceanogr[aà]fic|oceanografic|pol[ií]gono|poligono|puerto venecia)\b", low, re.I):
        return "retail_parking", "Name/source context indicates retail, hotel, restaurant, dealership, or customer parking; verify signs onsite."
    if re.search(r"\b(?:colegio|school|campus|universidad|university|hospital|polideportivo|sports centre|estadio|stadium|club\s+n[aá]utico|nautic|golf|gasolinera|estaci[oó]n\s+de\s+servicio|service station|fonda|camping|aptos?|apartamentos?|estaci[oó]n\s+de\s+renfe|renfe|train station|cofrad[ií]a|recinto\s+portuario|portuario|pescadores|punto\s+limpio|oficinas?)\b", low, re.I):
        return "facility_or_customer_parking", "Name/source context indicates facility, visitor, or customer parking; verify access and signs onsite."
    if re.search(r"(?:\bc\s*[/.]\s*|\bcl\s+|\bcarrer\b|\bcalle\b|\bav\s*[.]?\s+|\bavd[.]?\s+|\bavda[.]?\b|\bavenida\b|\bavinguda\b|\bctra[.]?\b|\bcarretera\b|\bcam[ií]no\b|\bcam[ií]\b|\bpaseo\b|\bpasseig\b|\bpassatge\b|\blugar\b|\bdiseminado\b|\bv[ií]a\b|\bstreet\b|\broad\b|\bronda\b|\bplaza\b|\bpla[çc]a\b|\bpza[.]?\b|\bpl[.]?\s+|\bpraza\b|\bglorieta\b|\btravessia\b|\bkalea\b|\bbidea\b|\bn-\d+\b|\bcm-\d+\b|\bayuntamiento\b|\bajuntament\b|\bmunicipal\b|\baj[.]?(?:\s|(?=[a-zà-ÿ])))", low, re.I):
        return "street_or_municipal", "Name/address indicates a street or municipal location; check regulated parking signs onsite."
    if re.search(r"\b(?:public|p[uú]blic[ao]|p[uú]blico)\b", usage_type or "", re.I) and str(address or "").strip():
        return "street_or_municipal", "Public usage plus a published address indicates street/municipal context; check regulated parking signs onsite."
    if re.search(r"\b[^\W\d_][\wÀ-ÿ'’.-]*(?:\s+[^\W\d_][\wÀ-ÿ'’.-]*){0,5},?\s+(?:n[ºo]\s*)?\d{1,4}\b", place_text, re.I):
        return "street_or_municipal", "Name/address includes a street-style address number; check regulated parking signs onsite."
    return default_kind, ""


def parking_profile(kind: str, note: str, raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    raw = raw or {}
    kind = parking_kind_from_price(raw.get("parking_price"), kind)
    spaces = count_int(raw.get("parking_spaces") or raw.get("aparcamientos"), default=0) or None
    ownership_code = str(raw.get("propiedad") or raw.get("ownership_code") or "").strip()
    ownership = "public" if ownership_code == "1" else "private" if ownership_code == "2" else None
    ownership_label = "Public point" if ownership == "public" else "Private point" if ownership == "private" else None
    labels = {
        "paid_or_metered": "Paid/conditional parking",
        "time_limited_or_validated": "Limited free parking",
        "free_parking": "Free parking",
        "retail_parking": "Retail/customer parking",
        "facility_or_customer_parking": "Facility/customer parking",
        "street_or_municipal": "Street/municipal",
        "unknown": "Parking unknown",
    }
    if kind not in labels:
        kind = "unknown"
    raw_price = str(raw.get("parking_price") or "").strip()
    if kind == "unknown" and ownership == "public" and spaces:
        kind = "street_or_municipal"
        note = (
            f"{note} Source publishes public ownership and {spaces} EV parking space"
            f"{'s' if spaces != 1 else ''}; classify as street/municipal context, not free parking."
        ).strip()
    if raw_price:
        note = f"Parking source says: {raw_price}. {note}"
    if spaces:
        note = f"{note} Source publishes {spaces} EV parking space{'s' if spaces != 1 else ''}.".strip()
    if ownership_label:
        note = f"{note} Source marks the charging point as {ownership}.".strip()
    free_likelihood = {
        "paid_or_metered": "low",
        "time_limited_or_validated": "conditional",
        "free_parking": "high",
        "retail_parking": "medium",
        "facility_or_customer_parking": "medium",
        "street_or_municipal": "medium",
        "unknown": "unknown",
    }.get(kind, "unknown")
    profile = {
        "kind": kind,
        "label": labels[kind],
        "paid": kind == "paid_or_metered",
        "cost_hint": raw_price[:120] if raw_price and kind in {"paid_or_metered", "time_limited_or_validated"} else None,
        "note": note,
        "free_likelihood": free_likelihood,
        "peak_hint": "This is an intelligence lead, not live parking telemetry; keep a live backup nearby.",
    }
    if spaces:
        profile["spaces"] = spaces
    if ownership:
        profile["ownership"] = ownership
        profile["ownership_label"] = ownership_label
    return profile


def price_from_charge_fields(charge_price: Any, *fallback_texts: Any) -> Optional[float]:
    primary = str(charge_price or "").strip()
    if primary:
        return price_from_text(primary)
    # Only treat fallback text as free if it contains an explicit free-charging phrase.
    # "Free publicly accessible" does NOT qualify — that describes access, not price.
    fallback = " ".join(str(x or "") for x in fallback_texts)
    if re.search(r"\b(?:carga\s+gratuita|recarga\s+gratuita|free\s+charging|charge\s+for\s+free|sin\s+coste)\b", fallback, re.I):
        return 0.0
    return price_from_text(fallback)


def slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower())
    return s.strip("-")[:70] or "charger"


def access_from_evidence(evidence: str, reliability: str, parking_kind: str, raw: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    raw = raw or {}
    if ACCESS_LIMIT_RE.search(evidence or ""):
        return {
            "level": "restricted",
            "label": "Access caveat",
            "note": "Source mentions customer/member/private/notice-required access. Verify before driving.",
        }
    ownership_code = str(raw.get("propiedad") or raw.get("ownership_code") or "").strip()
    if ownership_code == "2":
        return {
            "level": "restricted",
            "label": "Private point",
            "note": "Public source marks the charging point as private; verify access before driving.",
        }
    if ownership_code == "1":
        return {
            "level": "public",
            "label": "Public",
            "note": "Public source marks the charging point as public; onsite signs still win.",
        }
    osm_access = str(raw.get("access") or raw.get("access:charging_station") or "").strip().lower()
    if osm_access in {"private", "no"}:
        return {
            "level": "restricted",
            "label": "Access caveat",
            "note": f"OpenStreetMap access={osm_access}; verify access before driving.",
        }
    if osm_access in {"customers", "customer", "destination"}:
        return {
            "level": "customer",
            "label": "Customer parking",
            "note": f"OpenStreetMap access={osm_access}; likely customer/visitor parking.",
        }
    if osm_access in {"yes", "public", "permissive", "designated"}:
        return {
            "level": "public",
            "label": "Public",
            "note": f"OpenStreetMap access={osm_access}; community tags still need onsite verification.",
        }
    if reliability in {"official-cheap", "official-free", "official-free-policy"}:
        return {"level": "public", "label": "Public", "note": "Official public source; onsite signs still win."}
    if parking_kind in {"retail_parking", "facility_or_customer_parking"}:
        return {"level": "customer", "label": "Customer parking", "note": "Likely customer/visitor parking."}
    if parking_kind in {"street_or_municipal", "free_parking"}:
        return {
            "level": "public",
            "label": "Public",
            "note": "Parking context indicates public street/municipal/free-parking access; onsite signs still win.",
        }
    return {"level": "unknown", "label": "Access unknown", "note": "Access is not fully proven by this source."}


def source_integration_profile(source: str, reliability: str, status: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    source_l = source.lower()
    has_osm_connector_fields = bool(
        "openstreetmap" in source_l
        and (
            any(str(key).startswith("socket:") for key in raw)
            or re.search(r"\b(?:ccs|chademo|type\s*2|mennekes|schuko|combo)\b", " ".join(str(raw.get(k) or "") for k in ("description", "note")), re.I)
        )
    )
    has_connector_status = bool(raw.get("connector_status_fields") or raw.get("connectors") or raw.get("availability_status") or has_osm_connector_fields)
    has_osm_check_date = bool(
        "openstreetmap" in source_l
        and (
            raw.get("check_date")
            or raw.get("check_date:charging_station")
            or raw.get("lastcheck")
            or raw.get("lastcheck:status")
            or raw.get("survey:date")
        )
    )
    has_osm_schedule = bool("openstreetmap" in source_l and (raw.get("opening_hours") or raw.get("service_times")))
    has_osm_source_timestamp = bool("openstreetmap" in source_l and raw.get("osm_last_modified"))
    has_ocm_connector_status = bool("openchargemap" in source_l and raw.get("connector_status_fields"))
    status_text = str(status or "").strip().upper()
    has_station_status = bool(
        raw.get("availability_status")
        or raw.get("operational_status")
        or raw.get("online_status")
        or raw.get("offline_status")
        or has_ocm_connector_status
        or has_osm_check_date
        or has_osm_schedule
        or has_osm_source_timestamp
        or status_text not in {"", "VERIFY", "UNKNOWN"}
    )
    if "electromaps" in source_l:
        mode = "directory_connector_status"
        note = "Public Electromaps web-map API includes connector status fields; Volta treats it as directory evidence, not authoritative live availability."
    elif "openchargemap" in source_l:
        mode = "community_snapshot"
        note = "OpenChargeMap is a public community snapshot; status and media may be stale."
    elif "chargemap" in source_l:
        mode = "directory_pool_status"
        note = "Public Chargemap map API exposes pool availability/status flags; Volta treats it as directory evidence unless independently live."
    elif "openstreetmap" in source_l:
        mode = "community_tags"
        note = "OpenStreetMap tags describe location/connector details/access/schedule/source freshness, not live charger availability."
    elif "canarias te recarga" in source_l:
        mode = "public_map_snapshot"
        note = "Canarias te recarga is a public Canary Islands charger map; Volta reads its published marker tariff classes and never claims live bay availability from it."
    elif "mapareve" in source_l:
        mode = "official_ocpi_snapshot"
        note = "MapaREVE publishes public OCPI status/tariffs; this cached row is a snapshot and claims no live status. Live status/prices come from the runtime MapaREVE fetch."
    elif "lidl" in source_l or "consum" in source_l:
        mode = "official_locator"
        note = "Official store/locator source confirms charger presence/policy, but not live bay availability."
    else:
        mode = "public_source"
        note = "Public source evidence; no live availability integration is claimed."
    return {
        "mode": mode,
        "live_status_integrated": False,
        "station_status_fields": has_station_status,
        "connector_status_fields": has_connector_status,
        "remote_start_public_url": None,
        "status_basis": status,
        "note": note,
        "source_reliability": reliability,
    }


def location_claim_for(item: Dict[str, Any]) -> Dict[str, Any]:
    raw = item.get("raw") or {}
    source = str(item.get("source") or "Source")
    source_l = source.lower()
    coordinate_source = str(raw.get("coordinate_source") or "").strip().lower()
    precision = "source_coordinate"
    method = "public_source_coordinate"
    summary = "Public source publishes coordinates for this charger; onsite position and bay signage still win."

    if coordinate_source:
        method = coordinate_source
        if coordinate_source == "nominatim_lidl_poi":
            precision = "geocoded_poi"
            summary = "Coordinates come from a public geocoded Lidl POI because the official Lidl page lists the store address but not charger-bay coordinates."
        elif coordinate_source == "osm_lidl_store_poi":
            precision = "geocoded_poi"
            summary = "Coordinates come from the public OpenStreetMap Lidl store object matched to the address the official Lidl page lists; the store pin, not the charger bay."
        elif coordinate_source == "official_page_link_coordinates":
            precision = "source_coordinate"
            summary = "The official Lidl page publishes this store's own map link; store level, so verify the bay onsite."
        elif coordinate_source == "nominatim_street":
            precision = "geocoded_street"
            summary = "Coordinates come from public street-level geocoding because the official source lacks exact charger-bay coordinates; verify onsite."
        elif "geocode" in coordinate_source:
            precision = "geocoded"
            summary = "Coordinates come from public geocoding because the source did not publish exact charger-bay coordinates; verify onsite."
    elif "electromaps" in source_l or "chargemap" in source_l:
        precision = "directory_station_coordinate"
        method = "public_directory_map_coordinate"
        summary = "Public directory map publishes this station coordinate; verify exact bay position onsite."
    elif "openchargemap" in source_l:
        precision = "community_snapshot_coordinate"
        method = "openchargemap_public_snapshot"
        summary = "OpenChargeMap public snapshot publishes this coordinate; community data can be stale."
    elif "openstreetmap" in source_l:
        precision = "osm_object_coordinate"
        method = "openstreetmap_object_coordinate"
        summary = "OpenStreetMap object coordinate; community edits can be stale, so check the bay onsite."
    elif "consum" in source_l:
        precision = "official_store_coordinate"
        method = "official_store_locator_coordinate"
        summary = "Consum official store locator publishes this store coordinate; charger-bay position may be within the store car park."
    elif "lidl" in source_l:
        precision = "official_locator_coordinate"
        method = "official_locator_or_geocoded_store_coordinate"
        summary = "Lidl official source confirms the store/charger policy; coordinates are store-level unless the public source is more exact."
    elif "valència" in source_l or "opendata" in source_l:
        precision = "official_data_coordinate"
        method = "official_open_data_coordinate"
        summary = "Official open-data layer publishes this charger coordinate."

    return {
        "source": source,
        "url": item.get("source_url"),
        "lat": item.get("lat"),
        "lon": item.get("lon"),
        "precision": precision,
        "method": method,
        "summary": summary,
    }


def hardware_claim_for(item: Dict[str, Any]) -> Dict[str, Any]:
    connectors = [c for c in item.get("connectors") or [] if isinstance(c, dict)]
    labels = sorted(connector_labels_from_rows(connectors) if connectors else list(item.get("connector_labels") or []))
    port_count = item.get("total_evse") or item.get("connector_count")
    if port_count is None and connectors:
        port_count = sum(count_int(c.get("count"), default=1) for c in connectors)
    connector_powers = [fnum(c.get("power_kw")) for c in connectors]
    connector_powers = [p for p in connector_powers if p is not None]
    max_power = item.get("max_power_kw")
    if max_power is None and connector_powers:
        max_power = max(connector_powers)
    integration = item.get("source_integration") or {}
    source_url = item.get("source_url")
    source_name = item.get("source")
    for connector in connectors:
        if connector.get("source_url"):
            source_url = connector.get("source_url")
            break
    for source in item.get("sources") or []:
        if (
            isinstance(source, dict)
            and str(source.get("url") or "").rstrip("/") == str(source_url or "").rstrip("/")
        ):
            source_name = source.get("name") or source_name
            break
    has_published_hardware = bool(labels or port_count is not None or max_power is not None)
    return {
        "source": source_name,
        "url": source_url,
        "port_count": port_count,
        "connector_types": labels,
        "max_power_kw": max_power,
        "connector_power_kw": sorted({p for p in connector_powers}),
        "station_status_evidence": bool(integration.get("station_status_fields")),
        "connector_status_evidence": bool(integration.get("connector_status_fields")),
        "live_status_integrated": bool(integration.get("live_status_integrated")),
        "summary": (
            "Connector inventory, port count and power come from public source fields; provider app and onsite labels still win."
            if has_published_hardware
            else "Connector type, port count and power are not published by the current public sources; verify compatibility onsite or in the provider app."
        ),
    }


def access_claim_for(item: Dict[str, Any]) -> Dict[str, Any]:
    access = item.get("access") or {}
    parking = item.get("parking") or {}
    source = item.get("source")
    url = item.get("source_url")
    ownership = parking.get("ownership")
    private = access.get("level") == "restricted" or ownership == "private"
    paid = bool(parking.get("paid") or parking.get("kind") in {"paid_or_metered", "time_limited_or_validated"})
    return {
        "source": source,
        "url": url,
        "level": access.get("level"),
        "label": access.get("label"),
        "parking_kind": parking.get("kind"),
        "parking_paid": paid,
        "ownership": ownership,
        "private_or_restricted": private,
        "summary": access.get("note") or parking.get("note") or "Access and parking claim from public source evidence; onsite signs still win.",
    }


def merge_source_integration(items: Iterable[Dict[str, Any]], base: Dict[str, Any]) -> Dict[str, Any]:
    profile = dict(base.get("source_integration") or {})
    modes = []
    station_status_fields = bool(profile.get("station_status_fields"))
    connector_status_fields = bool(profile.get("connector_status_fields"))
    for item in items:
        integration = item.get("source_integration") or {}
        mode = integration.get("mode")
        if mode and mode not in modes:
            modes.append(mode)
        station_status_fields = station_status_fields or bool(integration.get("station_status_fields"))
        connector_status_fields = connector_status_fields or bool(integration.get("connector_status_fields"))
    if station_status_fields:
        profile["station_status_fields"] = True
    if connector_status_fields:
        profile["connector_status_fields"] = True
    if modes:
        profile["source_modes"] = modes
    if len(modes) > 1 and connector_status_fields:
        profile["note"] = (
            f"{profile.get('note') or 'Public source evidence.'} "
            "Merged public sources include connector-level status fields; Volta treats them as source evidence, not authoritative live availability."
        ).strip()
    return profile


def photo_refs(source: str, source_url: str, photos: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    cleaned: List[Dict[str, Any]] = []
    for photo in photos or []:
        if not isinstance(photo, dict):
            continue
        url = str(photo.get("url") or photo.get("href") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        cleaned.append({
            "url": url,
            "thumbnail_url": photo.get("thumbnail_url") or photo.get("thumb_url") or url,
            "source": photo.get("source") or source,
            "source_url": photo.get("source_url") or source_url,
            "caption": str(photo.get("caption") or "").strip()[:240],
            "license": photo.get("license") or "source-published",
        })
    return {
        "photos": cleaned[:4],
        "photo_status": "public_photo_available" if cleaned else "no_public_photo",
        "note": "Photo comes from a public source page/API and may be stale; onsite view wins." if cleaned else "No public photo found in the currently ingested source payload.",
    }


PANORAMAX_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def panoramax_ids_from_tags(tags: Dict[str, Any]) -> List[str]:
    ids: List[str] = []
    for key, value in sorted(tags.items()):
        if key != "panoramax" and not key.startswith("panoramax:"):
            continue
        for part in re.split(r"[\s,;]+", str(value or "")):
            pic_id = part.strip()
            if PANORAMAX_ID_RE.match(pic_id) and pic_id not in ids:
                ids.append(pic_id)
    return ids


def panoramax_photos_from_tags(tags: Dict[str, Any], source_url: str) -> List[Dict[str, Any]]:
    photos: List[Dict[str, Any]] = []
    for pic_id in panoramax_ids_from_tags(tags)[:4]:
        try:
            item = cached_json(
                f"https://api.panoramax.xyz/api/pictures/{urllib.parse.quote(pic_id)}",
                f"panoramax_picture_{pic_id}.json",
                ttl_hours=24 * 30,
                headers={"Accept": "application/geo+json, application/json"},
                timeout=20,
            )
        except Exception:
            continue
        assets = item.get("assets") or {}
        sd = (assets.get("sd") or {}).get("href")
        hd = (assets.get("hd") or {}).get("href")
        thumb = (assets.get("thumb") or {}).get("href")
        photo_url = sd or hd or thumb
        if not isinstance(photo_url, str) or not photo_url.startswith(("http://", "https://")):
            continue
        if not isinstance(thumb, str) or not thumb.startswith(("http://", "https://")):
            thumb = photo_url
        item_url = ""
        for link in item.get("links") or []:
            if isinstance(link, dict) and link.get("rel") == "self" and str(link.get("href") or "").startswith(("http://", "https://")):
                item_url = str(link.get("href"))
                break
        props = item.get("properties") or {}
        captured = str(props.get("datetime") or props.get("created") or "").strip()
        caption = f"Panoramax street-level image for OSM-tagged charger"
        if captured:
            caption = f"{caption} ({captured[:10]})"
        photos.append({
            "url": photo_url,
            "thumbnail_url": thumb,
            "source": "Panoramax public image",
            "source_url": item_url or source_url,
            "caption": caption,
            "license": props.get("license") or "Panoramax source-published media",
        })
    return photos


def connector_ref(label: str, *, count: int = 1, power_kw: Optional[float] = None, status: str = "UNKNOWN", source_url: Optional[str] = None, connector_id: Any = None, raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "connector_id": str(connector_id) if connector_id not in (None, "") else None,
        "label": label,
        "type": label,
        "status": status or "UNKNOWN",
        "power_kw": power_kw,
        "count": count_int(count),
        "source_url": source_url,
        "raw": raw or {},
    }


def osm_description_connector_rows(text: str, source_url: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    connector_token_re = re.compile(r"\b(?:CCS(?:\s+Combo)?|CHAdeMO|TYPE\s*2|Type\s*2|type\s*2)\b", re.I)
    specs = [
        ("CCS2", r"\bCCS(?:\s+Combo)?\b"),
        ("CHAdeMO", r"\bCHAdeMO\b"),
        ("Type 2", r"\b(?:TYPE|Type|type)\s*2\b"),
    ]
    for label, pattern in specs:
        candidate = None
        for m in re.finditer(pattern, text or "", re.I | re.S):
            trail = (text or "")[m.end():m.end() + 90]
            pm = re.search(r"([0-9]+[,.]?[0-9]*)\s*kW\b", trail, re.I)
            if not pm:
                if candidate is None:
                    candidate = (m, None, trail)
                continue
            if connector_token_re.search(trail[:pm.start()]):
                continue
            candidate = (m, pm, trail)
            break
        if not candidate:
            continue
        m, pm, trail = candidate
        power = None
        if pm:
            power = fnum(pm.group(1))
        rows.append(connector_ref(
            label,
            count=1,
            power_kw=power,
            status="UNKNOWN",
            source_url=source_url,
            connector_id=f"osm-description-{label.lower().replace(' ', '-')}",
            raw={"basis": "OSM description connector text", "matched": ((m.group(0) or "") + trail).strip()[:160]},
        ))
    return rows


OSM_CAR_SOCKET_KEYS = (
    "socket:type2_combo",
    "socket:type2_cable",
    "socket:type2",
    "socket:chademo",
    "socket:tesla_supercharger",
)

UNKNOWN_CONNECTOR_LABELS = {"", "unknown", "unknown connector", "connector", "other", "n/a", "none"}


def osm_tag_yes(value: Any) -> bool:
    return str(value or "").strip().lower() in {"yes", "designated", "1", "true"}


def osm_socket_tag_present(tags: Dict[str, Any], key: str) -> bool:
    raw = str(tags.get(key) or "").strip().lower()
    if raw in {"", "no", "0", "false"}:
        return False
    return any(k == key or k.startswith(key + ":") for k in tags)


def osm_is_micromobility_only(tags: Dict[str, Any]) -> bool:
    has_micromobility_marker = osm_tag_yes(tags.get("bicycle")) or osm_tag_yes(tags.get("small_electric_vehicle"))
    if not has_micromobility_marker:
        return False
    if osm_tag_yes(tags.get("motorcar")) or osm_tag_yes(tags.get("car")):
        return False
    if any(osm_socket_tag_present(tags, key) for key in OSM_CAR_SOCKET_KEYS):
        return False
    car_text = " ".join(str(tags.get(k) or "") for k in ("name", "description", "note", "charge"))
    return not re.search(r"\b(?:ccs|combo|chademo|type\s*2|mennekes|tesla|modo\s*3)\b", car_text, re.I)


def osm_has_car_charger_evidence(tags: Dict[str, Any], connector_rows: List[Dict[str, Any]]) -> bool:
    if connector_rows:
        return True
    if any(osm_socket_tag_present(tags, key) for key in OSM_CAR_SOCKET_KEYS):
        return True
    if osm_tag_yes(tags.get("motorcar")) or osm_tag_yes(tags.get("car")):
        return True
    car_text = " ".join(str(tags.get(k) or "") for k in ("name", "description", "note", "charge"))
    return bool(re.search(r"\b(?:ccs|combo|chademo|type\s*2|mennekes|tesla|modo\s*3)\b", car_text, re.I))


def openchargemap_connector_label(title: Any) -> Optional[str]:
    raw = str(title or "").strip()
    low = raw.lower()
    if low in UNKNOWN_CONNECTOR_LABELS:
        return None
    if "ccs" in low or "combo" in low:
        return "CCS2"
    if "type 2" in low or "mennekes" in low:
        return "Type 2"
    if "tesla" in low:
        return "Tesla"
    if "chademo" in low:
        return "CHAdeMO"
    return raw


def openchargemap_has_minimum_route_evidence(poi: Dict[str, Any]) -> bool:
    addr = poi.get("AddressInfo") or {}
    if poi.get("Connections"):
        return True
    if any(str(addr.get(key) or "").strip() for key in ("AddressLine1", "Town", "Postcode")):
        return True
    if poi.get("DateLastVerified"):
        return True
    return False


def openchargemap_is_weak_count_only_lead(poi: Dict[str, Any], parking_kind: str) -> bool:
    usage = ((poi.get("UsageType") or {}).get("Title") or "").strip().lower()
    return (
        not poi.get("Connections")
        and not poi.get("DateLastVerified")
        and usage in {"", "(unknown)", "unknown"}
        and parking_kind == "unknown"
    )


def openchargemap_has_connector_type_evidence(poi: Dict[str, Any]) -> bool:
    for conn in poi.get("Connections") or []:
        title = (conn.get("ConnectionType") or {}).get("Title")
        if openchargemap_connector_label(title):
            return True
    return False


def openchargemap_has_power_evidence(poi: Dict[str, Any]) -> bool:
    return any(fnum(conn.get("PowerKW")) is not None for conn in poi.get("Connections") or [])


def chargemap_connector_power(connector: Dict[str, Any]) -> Optional[float]:
    for key in ("power", "kw", "maxPower", "power_max"):
        power = fnum(connector.get(key))
        if power is not None:
            return power
    return None


def chargemap_has_power_evidence(pool: Dict[str, Any]) -> bool:
    return any(chargemap_connector_power(connector) is not None for connector in pool.get("charging_connectors") or [])


def openchargemap_country_iso(poi: Dict[str, Any]) -> str:
    addr = poi.get("AddressInfo") or {}
    country = addr.get("Country") or {}
    return str(country.get("ISOCode") or "").strip().upper()


def openchargemap_parking_kind(
    name: str,
    address: str,
    town: str,
    usage_cost: str,
    usage_type: str,
) -> Tuple[str, str]:
    kind, note = parking_kind_from_context(name, address, usage_cost, usage_type)
    if (
        kind == "unknown"
        and re.fullmatch(r"\s*public\s*", usage_type or "", re.I)
        and str(town or "").strip()
    ):
        return (
            "street_or_municipal",
            "OpenChargeMap marks usage public and publishes a town/coordinate but no private/facility context; classify as street/municipal context, not free parking; verify regulated signs onsite.",
        )
    return kind, note


def apply_single_type_station_power(connectors: List[Dict[str, Any]], max_kw: Optional[float], basis: str) -> List[Dict[str, Any]]:
    if not max_kw or not connectors:
        return connectors
    labels = {str(c.get("label") or c.get("type") or "") for c in connectors}
    if len(labels) != 1 or any(c.get("power_kw") is not None for c in connectors):
        return connectors
    filled: List[Dict[str, Any]] = []
    for connector in connectors:
        c = dict(connector)
        raw = dict(c.get("raw") or {})
        raw["power_basis"] = basis
        c["raw"] = raw
        c["power_kw"] = max_kw
        filled.append(c)
    return filled


def price_claim_type_for(price_min: Optional[float]) -> str:
    if price_min is None:
        return "price_unknown_lead"
    if price_min == 0:
        return "confirmed_free"
    return "confirmed_cheap" if price_min <= 0.25 else "confirmed_paid"


def add_record(records: List[Dict[str, Any]], *, source: str, source_url: str, name: str, lat: Optional[float], lon: Optional[float], address: str = "", city: str = "Valencia province", price_min: Optional[float] = None, free_condition: str = "", connector_labels: Optional[List[str]] = None, max_power_kw: Optional[float] = None, status: str = "VERIFY", reliability: str = "medium", evidence: str = "", parking_kind: str = "unknown", parking_note: str = "Parking details not published by this source; verify onsite signs.", raw: Optional[Dict[str, Any]] = None, google_url: Optional[str] = None, connectors: Optional[List[Dict[str, Any]]] = None, photos: Optional[List[Dict[str, Any]]] = None, port_count: Optional[int] = None, source_artifact: Optional[FetchArtifact] = None) -> None:
    raw = raw or {}
    # National gate (all Spain). Per-source functions that should stay regional still pre-filter
    # with likely_valencia_province before calling here, so widening this does not un-scope them.
    if not in_bbox(lat, lon, SPAIN_BBOX):
        return
    # Honest city label nationwide. A caller may leave the Valencia default, pass nothing, or
    # (Electromaps' web-map API) hand us a bare postcode where a town belongs -- none of those
    # are real place labels, so resolve them all here: prefer the actual town printed in the
    # address, then province-by-postcode. derive_city() keeps the honesty guard that a point
    # outside the Valencia box is never labelled Valencia.
    city = (city or "").strip()
    if is_placeholder_city(city, address):
        city = city_from_address(address) or derive_city(raw, lat, lon) or ""
    if city.isupper():
        city = titlecase_place(city)
    is_free = price_min == 0
    is_cheap = price_min is not None and price_min <= 0.25
    if is_unverified_free_supercharger(name, price_min, reliability):
        return
    # Every located charger is admitted: free first and cheap next is a ranking and
    # labelling rule, not an inclusion rule. A paid charger keeps its published price
    # (price_claim_type=confirmed_paid); a charger whose source publishes no price stays
    # price_unknown_lead and is never treated as free or cheap.
    connectors = connectors or []
    labels = connector_labels if connector_labels is not None else sorted({str(c.get("label") or c.get("type")) for c in connectors if c.get("label") or c.get("type")}) or ["Type 2"]
    port_total = sum(count_int(c.get("count")) for c in connectors) if connectors else count_int(port_count, default=0) or None
    parking = parking_profile(parking_kind, parking_note, raw)
    access = access_from_evidence(" ".join([evidence or "", free_condition or ""]), reliability, parking["kind"], raw)
    rec = {
        "id": f"intel_{slug(source)}_{slug(name)}_{round(lat or 0, 5)}_{round(lon or 0, 5)}",
        "name": name.strip() or "Unnamed charger",
        "city": city,
        "address": re.sub(r"\s+", " ", address or "").strip(),
        "lat": lat,
        "lon": lon,
        "price_min": price_min,
        "is_free": bool(is_free),
        "is_cheap": bool(is_cheap),
        "price_claim_type": price_claim_type_for(price_min),
        "free_condition": free_condition,
        "tesla_ok": tesla_compat(labels),
        "fast": bool(max_power_kw and max_power_kw >= 43),
        "max_power_kw": max_power_kw,
        "connector_labels": labels,
        "connectors": connectors,
        "status": status,
        "available": 0,
        "total_evse": port_total,
        "connector_count": port_total if connectors else None,
        "source": source,
        "source_url": source_url,
        "sources": [{"name": source, "url": source_url, "evidence": evidence[:500]}],
        "source_integration": source_integration_profile(source, reliability, status, raw),
        "reliability": reliability,
        "charge_claim": {
            "type": "free" if is_free else "cheap" if is_cheap else "paid" if price_min is not None else "price_unknown",
            "source": source,
            "url": source_url,
            "price_min": price_min,
            "summary": free_condition or evidence[:240] or "Charging tariff claim from public source.",
        },
        "parking_claim": {
            "kind": parking["kind"],
            "source": source,
            "url": source_url,
            "cost_hint": parking.get("cost_hint"),
            "summary": parking.get("note") or "Parking cost/access claim from public source.",
        },
        "source_fetched_at": source_artifact.fetched_at if source_artifact else now_iso(),
        "last_seen": raw_last_seen(raw) if isinstance(raw, dict) else None,
        "access": access,
        "parking": parking,
        "media": photo_refs(source, source_url, photos),
        "insight": evidence or free_condition or "Charger evidence from a public source.",
        "navigation": {
            "google": google_url or f"https://www.google.com/maps/search/?api=1&query={lat:.6f},{lon:.6f}",
            "apple": f"https://maps.apple.com/?ll={lat:.6f},{lon:.6f}&q={urllib.parse.quote(name)}",
        },
        "raw": raw,
    }
    apply_directory_lifecycle(rec)
    rec["primary_source"] = primary_source_for(rec)
    rec["location_claim"] = location_claim_for(rec)
    rec["hardware_claim"] = hardware_claim_for(rec)
    rec["access_claim"] = access_claim_for(rec)
    records.append(rec)




def directory_history_only(row: Dict[str, Any]) -> bool:
    """Removed directory identities are history, not facts about an active pin."""
    flag = row.get("source_reported_unavailable") or {}
    return flag.get("source") in {"Electromaps public web map", "Chargemap public web map"} and (
        flag.get("field") in {"name", "detail_name", "slug"}
        or str(flag.get("value") or "").upper() in {"REMOVED", "CLOSED", "PLANNED", "UNDER_CONSTRUCTION"}
    )


def apply_directory_lifecycle(row: Dict[str, Any]) -> None:
    """Retain inactive directory identities with their original removal evidence."""
    source = row.get("source")
    if source not in {
        "Electromaps public web map", "Chargemap public web map",
        PTP_SOURCE, PLUGSHARE_SOURCE, MOT_TARRAGONA_SOURCE,
    }:
        return
    raw = row.get("raw") or {}
    field, value = None, None
    for name_field, name in (("name", row.get("name")), ("detail_name", raw.get("detail_name"))):
        if BAD_STATION_NAME_RE.search(normalized_identity_text(name)):
            field, value = name_field, name
            break
    if field is None and source == "Chargemap public web map" and str(raw.get("slug") or "").lower() in DEAD_CHARGEMAP_SLUGS:
        field, value = "slug", raw["slug"]
    if field is None and source == "Chargemap public web map" and str(raw.get("operational_status") or "").upper() in {
        "OUT_OF_ORDER", "REMOVED", "CLOSED", "PLANNED", "UNDER_CONSTRUCTION",
    }:
        field, value = "operational_status", raw["operational_status"]
    elif field is None and source == "Electromaps public web map" and str(raw.get("offline_status") or "").upper() in {
        "NOT_WORKING", "ALL_SOCKETS_NOT_WORK", "REMOVED",
    }:
        field, value = "offline_status", raw["offline_status"]
    elif source == PTP_SOURCE and raw.get("ptp_status_raw") in PTP_OUT_OF_SERVICE_STATUSES:
        field, value = "status", raw["ptp_status_raw"]
    elif source == PLUGSHARE_SOURCE and raw.get("under_repair") is True:
        field, value = "under_repair", True
    elif source == MOT_TARRAGONA_SOURCE and str(raw.get("status_raw") or "").strip().lower() == "out of service":
        field, value = "STATUS", raw["status_raw"]
    if field is not None:
        row["source_reported_unavailable"] = {
            "source": source, "field": field, "value": value,
            "summary": "Directory reports a removed, duplicate or unavailable listing. Verify the operator before travelling.",
        }
        row["is_free"] = False
        row["is_cheap"] = False


def normalize_valencia_municipal_address(value: Any) -> str:
    """Repair the city feed's ``C\\ street`` typo without altering source evidence.

    The ArcGIS layer mixes the standard ``C/ street`` form with ``C\\ street`` on
    several rows.  The latter is a source typo, not a path or an escape sequence, and
    should never reach the driver-facing name/address.  Raw source rows remain attached
    to the record unchanged for provenance.
    """
    return re.sub(r"(?im)\bc\\(?=\s)", "C/", str(value or "").strip())


def source_valencia_municipal(records: List[Dict[str, Any]]) -> None:
    url = "https://geoportal.valencia.es/server/rest/services/OPENDATA/UrbanismoEInfraestructuras/MapServer/288/query?where=1%3D1&outFields=*&f=json&returnGeometry=true&outSR=4326"
    source_fetch = cached_fetch(url, "valencia_municipal_arcgis.json", ttl_hours=6)
    j = json.loads(source_fetch.decode())
    grouped: Dict[str, Dict[str, Any]] = {}
    for feat in j.get("features", []):
        a = feat.get("attributes") or {}
        g = feat.get("geometry") or {}
        addr = normalize_valencia_municipal_address(
            a.get("emplazamie") or "València municipal charger"
        )
        key = re.sub(r"\s+", " ", addr).strip().lower()
        price = price_from_text(a.get("precio__iv") or "")
        power = price_from_text(a.get("potenc_ia") or "")
        if power is None:
            m = re.search(r"([0-9]+[,.]?[0-9]*)\s*kw", a.get("potenc_ia") or "", re.I)
            power = fnum(m.group(1)) if m else None
        item = grouped.setdefault(key, {"addr": addr, "lat": [], "lon": [], "prices": [], "powers": [], "obs": set(), "count": 0, "raw": []})
        item["lat"].append(fnum(g.get("y"))); item["lon"].append(fnum(g.get("x")))
        if price is not None: item["prices"].append(price)
        if power is not None: item["powers"].append(power)
        if a.get("observacio"): item["obs"].add(a["observacio"])
        item["count"] += 1; item["raw"].append(a)
    for item in grouped.values():
        lat = sum(x for x in item["lat"] if x is not None) / max(1, len([x for x in item["lat"] if x is not None]))
        lon = sum(x for x in item["lon"] if x is not None) / max(1, len([x for x in item["lon"] if x is not None]))
        price = min(item["prices"]) if item["prices"] else None
        obs = "; ".join(sorted(item["obs"]))
        status = "VERIFY" if "No disponible" in obs else "OPEN"
        add_record(records, source="València OpenData", source_url=url, name=f"Municipal charger — {item['addr'].splitlines()[0]}", address=item["addr"], city="València", lat=lat, lon=lon, price_min=price, free_condition="Official city low-cost charger" if price is not None else "Official city charger", connector_labels=["Type 2"], max_power_kw=max(item["powers"]) if item["powers"] else None, status=status, reliability="official-cheap", evidence=f"València OpenData layer: {item['count']} socket(s), price {price} €/kWh-equivalent, notes: {obs}", parking_kind="street_or_municipal", parking_note="Municipal street/public charger; check regulated parking signs and charger app before leaving the car.", raw={"items": item["raw"][:3]}, source_artifact=fetch_artifact_or_none(source_fetch))


def nominatim_geocode(query: str) -> Tuple[Optional[float], Optional[float]]:
    cache_key = "geocode_" + slug(query) + ".json"
    p = CACHE / cache_key
    if p.exists():
        try:
            data = json.loads(p.read_text())
            return data.get("lat"), data.get("lon")
        except Exception:
            pass
    if os.environ.get("VOLTA_SKIP_GEOCODE") == "1":
        return None, None
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({"q": query + ", Spain", "format": "json", "limit": 1, "countrycodes": "es"})
    try:
        time.sleep(1.05)  # Nominatim courtesy limit
        arr = json.loads(fetch(url, headers={"Accept": "application/json"}, timeout=25).decode())
        if arr:
            lat, lon = float(arr[0]["lat"]), float(arr[0]["lon"])
            p.write_text(json.dumps({"lat": lat, "lon": lon, "query": query}), encoding="utf-8")
            return lat, lon
    except Exception:
        return None, None
    return None, None


def _lidl_label_key(label: str) -> str:
    """Accent-insensitive, whitespace-collapsed identity of a label as the page prints it."""
    return re.sub(r"\s+", " ", _nap_strip_accents(label).lower()).strip()


def _lidl_store_key(label: str) -> str:
    """Label without the subsidy/charger-count footnote some rows append in parentheses."""
    return re.sub(r"\s*-\s*\([^)]*(?:cargador|subvenciones|moves)[^)]*\)\s*$", "", label, flags=re.I).strip()


def _lidl_store_city(label: str, section: str) -> str:
    """Driver-facing locality: the town the page prints, else the island section.

    Canary rows lead with the *province* ("Las Palmas - Calle El Guirre…" is Fuerteventura),
    so a province head is never passed through as a town.
    """
    head = re.split(r"\s+-\s+", label, maxsplit=1)[0]
    head = re.sub(r"\(?\s*CP\s*[0-9]{4,5}\s*\)?", "", head, flags=re.I).strip(" ,-")
    if head and _lidl_label_key(head) not in LIDL_PROVINCE_HEADS and not re.search(r"[/,0-9]", head):
        return head
    return section


def source_lidl(records: List[Dict[str, Any]]) -> None:
    url = "https://empresa.lidl.es/sostenibilidad/bueno-para-el-planeta/proteger-el-clima/construccion-y-logistica-sostenible/puntos-de-recarga"
    source_fetch = cached_fetch(url, "lidl_puntos_recarga.html", ttl_hours=24)
    text = source_fetch.decode("utf-8", "replace")
    for heading, section in re.findall(r"<p><strong>([^<]+)</strong></p><ul>(.*?)</ul>", text, re.I | re.S):
        province = heading.strip()
        cp_prefix = LIDL_SECTIONS.get(province)
        if not cp_prefix:
            continue
        links = re.findall(r'<a[^>]+href="([^"]*(?:google\.com/maps|maps\.app\.goo\.gl)[^"]*)"[^>]*>(.*?)</a>', section, re.I | re.S)
        for href, label_html in links:
            label = re.sub(r"<[^>]+>", " ", html.unescape(label_html))
            label = re.sub(r"\s+", " ", label).strip()
            href = html.unescape(href)
            parsed = urllib.parse.urlparse(href)
            qs = urllib.parse.parse_qs(parsed.query)
            q = (qs.get("query") or qs.get("q") or [label])[0]
            evidence_text = html.unescape(label + " " + q)
            store_label = _lidl_store_key(label)
            if _lidl_label_key(store_label) in LIDL_DUPLICATE_LABELS:
                continue
            postcode = re.search(r"\(?\s*CP\s*([0-9]{4,5})\s*\)?", label, re.I) or re.search(r"\b([0-9]{4,5})\b", label)
            if not (postcode and postcode.group(1).startswith(cp_prefix)):
                continue
            pinned = LIDL_STORE_COORDS.get(store_label)
            coordinate_evidence = ""
            if pinned:
                lat, lon, coordinate_source, coordinate_evidence = pinned
            else:
                link_point = re.match(r"^\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)\s*$", q)
                if link_point:
                    lat, lon = float(link_point.group(1)), float(link_point.group(2))
                    coordinate_source = "official_page_link_coordinates"
                    coordinate_evidence = "The official Lidl page links this store with its own coordinates."
                else:
                    geocode_query = re.sub(r"\s*-\s*\([^)]*(?:cargador|subvenciones|moves)[^)]*\)", "", label, flags=re.I)
                    lat, lon = nominatim_geocode(geocode_query or q)
                    coordinate_source = "nominatim_geocode"
                    if lat is None or lon is None:
                        hint = LIDL_COORD_HINTS.get(postcode.group(1))
                        if hint:
                            lat, lon, coordinate_source = hint
            count_match = re.search(r"\b([1-9]\d*)\s+cargador(?:es)?\b", evidence_text, re.I)
            listed_charger_count = count_int(count_match.group(1), default=0) if count_match else None
            evidence = (
                "Official Lidl Spain page states 30 minutes free charging and lists this store. "
                "The page says the Lidl network may support CHAdeMO, CCS and AC Type 2 depending on store availability, "
                "but this row does not publish store-specific connector types."
            )
            raw = {
                "official_query": q,
                "coordinate_source": coordinate_source,
                "listed_charger_count": listed_charger_count,
                "connector_scope": "network-level compatibility only; per-store connector types not listed",
            }
            if coordinate_evidence:
                raw["coordinate_evidence"] = coordinate_evidence
            add_record(records, source="Lidl official free list", source_url=url, name="Lidl — " + label.split("(CP")[0].strip(" -"), address=label, city=_lidl_store_city(label, province), lat=lat, lon=lon, price_min=0.0, free_condition="30 minutes free charging while shopping; Lidl says no bank card/RFID needed.", connector_labels=[], max_power_kw=None, status="VERIFY", reliability="official-free-policy", evidence=evidence, parking_kind="retail_parking", parking_note="Retail parking; free charging is time-limited and EV bays can fill at shopping peaks.", raw=raw, google_url=href, connectors=[], port_count=listed_charger_count, source_artifact=fetch_artifact_or_none(source_fetch))


def source_consum(records: List[Dict[str, Any]]) -> None:
    base = "https://www.consum.es/supermercados/valencia/"
    article_re = re.compile(r'<article\b(?=[^>]*\bdata-lat="([^"]+)")(?=[^>]*\bdata-lon="([^"]+)")[^>]*>.*?</article>', re.I | re.S)
    for page in range(1, 19):
        url = base if page == 1 else f"{base}{page}/"
        try:
            source_fetch = cached_fetch(url, f"consum_valencia_{page}.html", ttl_hours=24)
            text = source_fetch.decode("utf-8", "replace")
        except Exception:
            continue
        for m in article_re.finditer(text):
            chunk = m.group(0)
            if "Recarga coche" not in chunk:
                continue
            lat, lon = fnum(m.group(1)), fnum(m.group(2))
            if not in_bbox(lat, lon):
                continue
            detail_url = None
            for href in re.findall(r'href="([^"]*/supermercados/[^"]+/)"', chunk, re.I):
                href = html.unescape(href)
                if re.search(r"/supermercados/valencia/\d*/?$", href):
                    continue
                detail_url = urllib.parse.urljoin("https://www.consum.es", href)
                break
            title = re.search(r'<div[^>]+class="[^"]*supermarket-label[^"]*"[^>]*>.*?<a[^>]+href="[^"]+"[^>]*>(.*?)</a>', chunk, re.I | re.S)
            if not title:
                title = re.search(r'<h[23][^>]*>(.*?)</h[23]>', chunk, re.I | re.S)
            name = re.sub(r"<[^>]+>", " ", html.unescape(title.group(1))) if title else "Consum supermarket charger"
            name = re.sub(r"\s+", " ", name).strip()
            addr_match = re.search(r'<div[^>]+class="[^"]*supermarket-data[^"]*"[^>]*>\s*<p>\s*(.*?)\s*</p>', chunk, re.I | re.S)
            if not addr_match:
                addr_match = re.search(r'<div[^>]+class="[^"]*(?:address|store-card__address)[^"]*"[^>]*>(.*?)</div>', chunk, re.I | re.S)
            address = re.sub(r"<[^>]+>", " ", html.unescape(addr_match.group(1))) if addr_match else ""
            source_url = detail_url or url
            add_record(records, source="Consum store locator", source_url=source_url, name="Consum — " + name, address=address, city="Valencia province", lat=lat, lon=lon, price_min=None, free_condition="Official Consum store listing has EV charging icon; price must be cross-checked with MapaREVE/Eranovum.", connector_labels=["Type 2"], max_power_kw=None, status="VERIFY", reliability="official-charger-location-price-unknown", evidence=f"Consum official store {'detail page' if detail_url else 'listing page'} includes 'Recarga coche' at this store with coordinates. Listing page: {url}", parking_kind="retail_parking", parking_note="Supermarket parking; not necessarily free charging — use as a parking/charger lead and verify price in app.", raw={"listing_url": url, "store_url": detail_url}, connectors=[connector_ref("Type 2", source_url=source_url)], source_artifact=fetch_artifact_or_none(source_fetch))


# --- Feníe Energía public map — FREE stations (owner-attested written permission 2026-07-18) --
# Feníe publishes its own public charging map (recarga.fenieenergia.es), a network of FREE
# stations each carrying an explicit `gratuito`/`tarifa` flag. Access is anonymous and
# read-only: GET the public page for a session cookie + CSRF token, then POST the markers
# list exactly as the public map's own JS does (no login, account, private token, mutation).
#
# RIGHTS: Feníe's public terms otherwise reserve reuse rights; this adapter exists ONLY
# because the owner attests EXPRESS WRITTEN PERMISSION (2026-07-18) covering PUBLIC
# REDISTRIBUTION via Volta. The record lives in data/source_policy_exclusions.json under
# `granted_reuse` ("fenie-owner-attested-permission"); it is the audit trail and the
# revocation gate. On 2026-10-06 the owner also approved this one read-only marker-list
# POST as a narrow exception to the former GET-only boundary; the exact host/path/body
# shape is enforced by scripts/source_transport.py (no redirects, bounded budget), so no
# other POST is enabled by it. If the permission lapses, restore the exclusion record.
# Only the operator's own gratuito==1 stations are ingested; the snapshot `estado` is
# provenance, never live authority (live comes from MapaREVE/REVE). Out-of-service
# markers (estado 4) stay on the map with `source_reported_unavailable`, so the server
# never offers them as Go now and the station keeps a stable ID while it is down
# (owner decision 2026-10-11; dropping them broke every refresh's zero-missing-ID gate).
FENIE_MAP_URL = "https://recarga.fenieenergia.es/"
FENIE_MARKERS_URL = "https://recarga.fenieenergia.es/punto-recarga/todos"
FENIE_STATION_URL = "https://recarga.fenieenergia.es/punto-recarga/"
FENIE_MAX_RESPONSE_BYTES = 20 * 1024 * 1024
# estado legend (from Feníe's own marker legend): 1 Libre, 2 Reservado, 3 Ocupado,
# 4 No disponible (out of service — kept, marked unavailable), 5 No gestionado (no telemetry).
FENIE_OUT_OF_SERVICE_ESTADO = 4
# Feníe's public map includes a handful of internal test/QA units ("Fenie Prueba",
# "Pruebas Postventa", an OCPP test rig...). Data honesty: never show a test unit as a
# real free charger a driver can go to. Drop by name.
FENIE_TEST_STATION_RE = re.compile(r"\b(pruebas?|test|demo|ocpp\b.*\bwolt|postventa)\b", re.I)


def _fenie_fetch_markers_bytes() -> bytes:
    """Anonymous public flow: GET the map page for a CSRF token + session cookie, then POST
    the markers list as the page's own JS does. Exactly two requests, no login/private token;
    the POST is validated and redirect-free via source_transport.policy_urlopen."""
    jar = http.cookiejar.CookieJar()
    page_req = urllib.request.Request(FENIE_MAP_URL, headers={"User-Agent": UA, "Accept": "text/html"}, method="GET")
    with policy_urlopen(page_req, timeout=35, cookiejar=jar) as response:
        final_host = (urllib.parse.urlsplit(response.geturl()).hostname or "").lower()
        if final_host != "recarga.fenieenergia.es":
            raise SourcePolicyBlocked("Feníe map page redirected off its public origin")
        page_html = response.read(FENIE_MAX_RESPONSE_BYTES).decode("utf-8", "replace")
    token_match = re.search(r'csrf-token"\s+content="([^"]+)"', page_html)
    if not token_match:
        raise RuntimeError("Feníe CSRF token not found on the public map page")
    markers_req = urllib.request.Request(
        FENIE_MARKERS_URL,
        data=b"",
        headers={
            "User-Agent": UA,
            "X-CSRF-TOKEN": token_match.group(1),
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": FENIE_MAP_URL,
        },
        method="POST",
    )
    with policy_urlopen(markers_req, timeout=35, cookiejar=jar) as response:
        body = response.read(FENIE_MAX_RESPONSE_BYTES + 1)
    if len(body) > FENIE_MAX_RESPONSE_BYTES:
        raise RuntimeError("Feníe marker response exceeded the reviewed size bound")
    return body


def parse_fenie_markers(payload: Any, *, minimum_markers: int = 100) -> List[Dict[str, Any]]:
    markers = payload if isinstance(payload, list) else (payload or {}).get("markers") or []
    if len(markers) < minimum_markers:
        raise RuntimeError(f"Feníe public marker population shrank to {len(markers)} (<{minimum_markers})")
    return [m for m in markers if isinstance(m, dict)]


def _fenie_city(address: str) -> str:
    """Feníe addresses read 'street, number, town, province'. Take the town (penultimate
    comma field), skipping a purely-numeric segment; fall back to the general parser."""
    parts = [p.strip() for p in str(address or "").split(",") if p.strip()]
    if len(parts) >= 3:
        candidate = parts[-2]
        if candidate and not candidate.isdigit():
            return titlecase_place(candidate)
    if len(parts) == 2 and not parts[-1].isdigit():
        return titlecase_place(parts[-1])
    return city_from_address(address) or "Spain"


def fenie_records_from_markers(markers: List[Dict[str, Any]], *, source_artifact: Optional[FetchArtifact] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for marker in markers:
        # FREE-first: only the operator's own gratuito flag (tarifa 0 corroborates it).
        if int(marker.get("gratuito") or 0) != 1:
            continue
        out_of_service = int(marker.get("estado") or 0) == FENIE_OUT_OF_SERVICE_ESTADO
        if FENIE_TEST_STATION_RE.search(str(marker.get("nombre_punto") or "")):
            continue  # internal Feníe test/QA unit, not a real public free charger
        lat, lon = fnum(marker.get("lat")), fnum(marker.get("lon"))
        if lat is None or lon is None:
            continue
        code = str(marker.get("code") or marker.get("id") or "").strip()
        station_url = f"{FENIE_STATION_URL}{code}" if code else FENIE_MAP_URL
        connectors: List[Dict[str, Any]] = []
        connector_labels: List[str] = []
        power_values: List[float] = []
        for connector in marker.get("conectores") or []:
            if not isinstance(connector, dict):
                continue
            label = connector_label_from_token(connector.get("type"))
            if not label:
                continue
            power_kw = fnum(connector.get("power"))
            if power_kw:
                power_values.append(power_kw)
            connector_labels.append(label)
            connectors.append(connector_ref(label, count=count_int(connector.get("count"), default=1), power_kw=power_kw, source_url=station_url))
        address = str(marker.get("direccion") or "").strip()
        name = str(marker.get("nombre_punto") or "Punto de recarga").strip()
        add_record(
            out,
            source="Feníe Energía public map (Gratuitos)",
            source_url=station_url,
            name=f"Feníe — {name}",
            address=address,
            city=_fenie_city(address),
            lat=lat,
            lon=lon,
            price_min=0.0,
            free_condition="Feníe Energía's own public map marks this station as gratuito (tarifa 0). Confirm in the Recarga Feníe app before routing; live status is not from this weekly snapshot.",
            connector_labels=connector_labels,
            max_power_kw=max(power_values) if power_values else fnum(marker.get("potencia")),
            status="VERIFY",
            reliability="official-free",
            evidence="Feníe Energía publishes this station on its own public map (no login) with an explicit gratuito=1 / tarifa=0 free declaration. Reused under owner-attested written permission from Feníe (2026-07-18) covering public redistribution.",
            parking_kind="unknown",
            parking_note="Feníe publishes a parking-space count but no parking tariff; verify onsite signs.",
            raw={
                "fenie_code": code,
                "fenie_estado": marker.get("estado"),
                "fenie_tarifa": marker.get("tarifa"),
                "fenie_gratuito": marker.get("gratuito"),
                "parking_spaces": marker.get("aparcamientos"),
                "coordinate_source": "fenie_public_map",
            },
            connectors=connectors,
            port_count=len(connectors) or None,
            source_artifact=source_artifact,
        )
        if out:
            out[-1]["sources"][0]["attribution"] = "Feníe Energía — Red de recarga (public map)"
            out[-1]["sources"][0]["transformation"] = "Volta ingests only Feníe's own gratuito=1 stations from its public map; out-of-service markers stay listed as unavailable and no live status is inferred from this weekly snapshot."
            if out_of_service:
                out[-1]["source_reported_unavailable"] = {
                    "source": "Feníe Energía public map (Gratuitos)",
                    "field": "estado",
                    "value": FENIE_OUT_OF_SERVICE_ESTADO,
                    "summary": "Feníe reports this charger unavailable (out of service on its public map). Don't route here until it is back in service.",
                }
            if source_artifact is not None:
                out[-1]["sources"][0]["source_fetched_at"] = source_artifact.fetched_at
    return out


def _grid_tiles(
    bbox: Tuple[float, float, float, float], rows: int, cols: int
) -> List[Tuple[float, float, float, float]]:
    south, west, north, east = bbox
    dlat = (north - south) / rows
    dlon = (east - west) / cols
    return [
        (south + r * dlat, west + c * dlon, south + (r + 1) * dlat, west + (c + 1) * dlon)
        for r in range(rows)
        for c in range(cols)
    ]


def source_fenie(records: List[Dict[str, Any]]) -> None:
    cache_path = CACHE / "fenie_markers.json"
    age = time.time() - cache_path.stat().st_mtime if cache_path.exists() else None
    if age is not None and 0 <= age < 24 * 3600:
        artifact = cache_file_artifact(cache_path, url=FENIE_MARKERS_URL,
            cache_name="fenie_markers.json", ttl_hours=24)
        markers = parse_fenie_markers(json.loads(artifact.decode("utf-8", "replace")))
        records.extend(fenie_records_from_markers(markers, source_artifact=artifact))
        return
    try:
        body = _fenie_fetch_markers_bytes()
        # Validate before the cache is overwritten, so a challenge page cannot poison it.
        parse_fenie_markers(json.loads(body.decode("utf-8", "replace")))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(body)
        fetched_at = time.time()
        os.utime(cache_path, (fetched_at, fetched_at))
        artifact = remember_fetch_artifact(FetchArtifact(
            body=body, url=FENIE_MARKERS_URL, cache_name="fenie_markers.json",
            fetched_at=epoch_iso(fetched_at), age_seconds=0.0, hit=False, fallback=False, stale=False,
        ))
    except Exception as exc:  # noqa: BLE001 - fall back to a labeled stale cache, else fail loudly
        if not cache_path.exists():
            raise
        fallback_age = time.time() - cache_path.stat().st_mtime
        if not 0 <= fallback_age <= 7 * 24 * 3600:
            raise RuntimeError("Feníe refresh failed and cached evidence exceeds the seven-day limit") from exc
        artifact = cache_file_artifact(
            cache_path, url=FENIE_MARKERS_URL, cache_name="fenie_markers.json",
            ttl_hours=24, fallback=True, error=type(exc).__name__,
        )
    markers = parse_fenie_markers(json.loads(artifact.decode("utf-8", "replace")))
    records.extend(fenie_records_from_markers(markers, source_artifact=artifact))


def spain_tiles(rows: int = 4, cols: int = 4) -> List[Tuple[float, float, float, float]]:
    """Mainland/Balearic grid plus dedicated Canary, Ceuta and Melilla tiles.

    The first `rows*cols` tiles stay the historical mainland envelope so Overpass
    cache files `osm_es_area_chargers_00`… remain valid. Island tiles are appended.
    One big rectangle over the Atlantic would make every tile too expensive for Overpass.
    """
    tiles = _grid_tiles(MAINLAND_TILE_BBOX, rows, cols)
    tiles.extend(_grid_tiles(CANARY_TILE_BBOX, 2, 3))
    tiles.append(CEUTA_TILE_BBOX)
    tiles.append(MELILLA_TILE_BBOX)
    return tiles


def osm_fetch_box(south: float, west: float, north: float, east: float, label: str, depth: int = 0) -> Tuple[List[Tuple[Dict[str, Any], Any]], List[str]]:
    """Fetch charging stations inside Spain within one box. Returns (elements_with_artifact, failures).

    A 504 from Overpass means "this box is too expensive for me to compute", not "no data" --
    western Spain times out at 1/16-of-the-country size. Splitting the box into quarters and
    retrying asks the same question in cheaper pieces, so one heavy region degrades into a few
    more requests instead of silently dropping Salamanca/Zamora/Valladolid from the database.
    """
    box = f"{south:.4f},{west:.4f},{north:.4f},{east:.4f}"
    # SPAIN_BBOX is a crude rectangle: its corners reach well into France, Portugal and Andorra,
    # so a bbox-only national query imports foreign chargers (a Carrefour at 43.58,3.93 is
    # Montpellier; "Marina de Albufeira" is the Algarve). VALENCIA_BBOX never exposed this
    # because it sits entirely inland. Intersecting every box with Spain's real administrative
    # area lets the country boundary -- not a rectangle -- decide what belongs.
    query = f"""
[out:json][timeout:180];
area[\"ISO3166-1\"=\"ES\"][admin_level=2]->.spain;
(
  node[\"amenity\"=\"charging_station\"](area.spain)({box});
  way[\"amenity\"=\"charging_station\"](area.spain)({box});
  relation[\"amenity\"=\"charging_station\"](area.spain)({box});
);
out center meta;
"""
    last_error = "no attempt made"
    for attempt in range(3):
        try:
            # Cache key is the filename, not the query: renamed when the query gained the
            # ES-area filter so the bbox-only (France/Portugal-contaminated) tiles cannot be
            # served from cache.
            body = cached_overpass(query, f"osm_es_area_chargers_{label}.json", ttl_hours=12)
            time.sleep(OVERPASS_TILE_PAUSE_SECONDS)
            return [(el, body) for el in json.loads(body.decode()).get("elements", [])], []
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < 2:
                time.sleep(OVERPASS_BACKOFF_SECONDS * (attempt + 1))
    if depth < OVERPASS_MAX_SUBDIVISION:
        mid_lat, mid_lon = (south + north) / 2, (west + east) / 2
        quarters = [
            (south, west, mid_lat, mid_lon), (south, mid_lon, mid_lat, east),
            (mid_lat, west, north, mid_lon), (mid_lat, mid_lon, north, east),
        ]
        found: List[Tuple[Dict[str, Any], Any]] = []
        failed: List[str] = []
        for i, (s, w, n, e) in enumerate(quarters):
            sub_found, sub_failed = osm_fetch_box(s, w, n, e, f"{label}_{i}", depth + 1)
            found.extend(sub_found)
            failed.extend(sub_failed)
        return found, failed
    return [], [f"tile {label} ({box}): {last_error}"]


def red_movem_identity(tags: Dict[str, Any]) -> bool:
    """Require a named network or its exact official-network host, never a place-name guess."""
    text = " ".join(
        str(tags.get(key) or "")
        for key in ("name", "network", "operator", "operator:website", "website")
    )
    normalized = normalized_identity_text(text)
    return bool(
        re.search(r"\bred\s+movem\b", normalized)
        or re.search(r"\bmovembadajoz\.es\b", normalized)
    )


def osm_station_name(tags: Dict[str, Any]) -> str:
    explicit = tags.get("name") or tags.get("operator") or tags.get("brand")
    if explicit:
        return str(explicit)
    return "Red MOVEM charger" if red_movem_identity(tags) else "OSM free charging station"


def red_movem_price(tags: Dict[str, Any], max_power_kw: Optional[float]) -> Optional[float]:
    """Return the official 2024 BOP tariff only for unambiguous <=22 kW Red MOVEM rows."""
    if not red_movem_identity(tags) or max_power_kw is None or max_power_kw > 22.0:
        return None
    return 0.25


def assert_reference_review_current(
    checked_at: str,
    max_age_days: int,
    *,
    now: Optional[datetime] = None,
    label: str,
) -> None:
    """Fail closed when a manually reviewed first-party reference is too old."""
    reviewed = _source_datetime(checked_at)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        current = current.replace(tzinfo=timezone.utc)
    if reviewed is None:
        raise RuntimeError(f"{label} has no valid timezone-aware review timestamp")
    age_seconds = (current.astimezone(timezone.utc) - reviewed.astimezone(timezone.utc)).total_seconds()
    if age_seconds < 0 or age_seconds > max_age_days * 24 * 60 * 60:
        raise RuntimeError(f"{label} review is outside its {max_age_days}-day validity window")


def source_osm(records: List[Dict[str, Any]]) -> None:
    # National (was VALENCIA_BBOX until 2026-07-15): the app went national in June but this
    # source never did, so Spain outside Valencia had no OSM evidence at all.
    tiles = spain_tiles()
    # Each element is carried WITH the artifact of the tile fetch that produced it. Keeping only
    # the loop's last `body` would attribute every record's provenance to the last tile fetched.
    elements: List[Tuple[Dict[str, Any], Any]] = []
    seen_elements: Set[Tuple[Any, Any]] = set()
    failures: List[str] = []
    for idx, (south, west, north, east) in enumerate(tiles):
        found, failed = osm_fetch_box(south, west, north, east, f"{idx:02d}")
        failures.extend(failed)
        for el, body in found:
            # A way/relation whose geometry straddles a tile edge is returned by both tiles.
            key = (el.get("type"), el.get("id"))
            if key in seen_elements:
                continue
            seen_elements.add(key)
            elements.append((el, body))
    # Fatal only when nothing came back AND something actually failed. "Every tile answered and
    # Spain held no matching element" is a legitimate (if surprising) empty result -- an offline
    # or mocked fetch produces exactly that, and treating it as fatal turns a stubbed test into
    # a false alarm. Partial failures fall through to the loud-but-not-destructive raise below,
    # which keeps the records the answering tiles did produce.
    if failures and not elements:
        raise RuntimeError(f"Overpass returned no usable elements; first failure: {failures[0]}")
    for el, body in elements:
        tags = el.get("tags") or {}
        fee = (tags.get("fee") or "").lower()
        charge_text = " ".join(str(tags.get(k) or "") for k in ("charge", "fee:conditional", "payment:app", "description", "note"))
        price = price_from_text(charge_text)
        is_free = fee in {"no", "0", "free"} or price == 0
        is_cheap = price is not None and price <= 0.25
        is_red_movem = red_movem_identity(tags)
        # OSM contributes every car charger it maps. Free and cheap tags rank first; a
        # paid charge tag keeps its price and a charger without one is a price-unknown lead.
        if osm_is_micromobility_only(tags):
            continue
        lat = fnum(el.get("lat") or (el.get("center") or {}).get("lat"))
        lon = fnum(el.get("lon") or (el.get("center") or {}).get("lon"))
        name = osm_station_name(tags)
        osm_type = el.get("type")
        osm_id = el.get("id")
        source_url = f"https://www.openstreetmap.org/{osm_type}/{osm_id}" if osm_type and osm_id else "https://www.openstreetmap.org/"
        labels = []
        connector_rows: List[Dict[str, Any]] = []
        socket_defs = [
            ("socket:type2_combo", "CCS2"),
            ("socket:type2_cable", "Type 2"),
            ("socket:type2", "Type 2"),
            ("socket:chademo", "CHAdeMO"),
            ("socket:schuko", "Schuko"),
            ("socket:tesla_supercharger", "Tesla"),
        ]
        for key, label in socket_defs:
            matching = [k for k in tags if k == key or k.startswith(key + ":")]
            if not matching:
                continue
            if label not in labels:
                labels.append(label)
            raw_count = tags.get(key)
            count = count_int(raw_count, default=1)
            output = tags.get(key + ":output")
            power = None
            if output:
                m = re.search(r"([0-9]+[,.]?[0-9]*)\s*kw", str(output), re.I)
                power = fnum(m.group(1)) if m else fnum(output)
            connector_rows.append(connector_ref(label, count=count, power_kw=power, status="UNKNOWN", source_url=source_url, connector_id=key, raw={"osm_key": key, "count": raw_count, "output": output}))
        if not connector_rows:
            connector_rows = osm_description_connector_rows(charge_text, source_url)
            labels = sorted({str(c.get("label") or c.get("type")) for c in connector_rows if c.get("label") or c.get("type")})
        if not osm_has_car_charger_evidence(tags, connector_rows):
            continue
        max_kw = None
        max_kw_basis = ""
        for key, value in tags.items():
            if key.startswith("socket:") and key.endswith(":output"):
                m = re.search(r"([0-9]+[,.]?[0-9]*)\s*kw", str(value), re.I)
                if m:
                    max_kw = max(max_kw or 0, fnum(m.group(1)) or 0)
                    max_kw_basis = "OSM socket output tag"
        if max_kw is None:
            m = re.search(r"([0-9]+[,.]?[0-9]*)\s*kw", charge_text, re.I)
            if m:
                max_kw = fnum(m.group(1))
                max_kw_basis = "OSM charge/description text"
        if max_kw is None:
            volts = fnum(tags.get("voltage"))
            amps = fnum(tags.get("amperage"))
            if volts and amps:
                max_kw = round(volts * amps / 1000, 1)
                max_kw_basis = "OSM voltage/amperage"
        movem_price = red_movem_price(tags, max_kw)
        if is_red_movem:
            if movem_price is None:
                continue
            assert_reference_review_current(
                RED_MOVEM_TARIFF_CHECKED_AT,
                RED_MOVEM_REVIEW_MAX_AGE_DAYS,
                label="Red MOVEM tariff",
            )
            price = movem_price
            is_free = False
            is_cheap = True
        addr_parts = [tags.get("addr:street"), tags.get("addr:housenumber"), tags.get("addr:city")]
        address = " ".join(x for x in addr_parts if x) or tags.get("addr:full") or ""
        access = tags.get("access") or ""
        evidence = f"OSM fee={tags.get('fee')}; charge={tags.get('charge')}; access={access}; operator={tags.get('operator')}; capacity={tags.get('capacity')}; sockets={','.join(labels)}"
        if movem_price is not None:
            evidence += "; official Red MOVEM BOP tariff is €0.25/kWh only at connectors <=22 kW"
            free_condition = "Official Red MOVEM tariff: €0.25/kWh only for this unambiguously matched connector inventory at or below 22 kW; recheck after network upgrades."
        else:
            free_condition = (
                "OpenStreetMap tag fee=no" if is_free
                else "OpenStreetMap charge tag is at or below €0.25/kWh" if is_cheap
                else f"OpenStreetMap charge tag lists €{price:.2f}/kWh" if price is not None
                else "OpenStreetMap confirms charger/socket, price missing"
            )
        parking_fee = str(tags.get("parking:fee") or tags.get("fee:parking") or "").lower()
        retail_name = re.search(r"lidl|aldi|bauhaus|mercadona|consum|carrefour", " ".join([name, str(tags.get('operator'))]), re.I)
        if parking_fee in {"yes", "paid", "metered"}:
            parking_kind = "paid_or_metered"
        elif parking_fee in {"no", "0", "free"}:
            parking_kind = "free_parking"
        elif retail_name:
            parking_kind = "retail_parking"
        else:
            parking_kind = "street_or_municipal"
        if connector_rows and max_kw_basis:
            connector_rows = apply_single_type_station_power(connector_rows, max_kw, max_kw_basis)
        if not connector_rows and tags.get("capacity") and labels:
            raw = {"capacity": tags.get("capacity"), "basis": "OSM capacity"}
            if max_kw_basis:
                raw["power_basis"] = max_kw_basis
            connector_rows.append(connector_ref(labels[0], count=count_int(tags.get("capacity")), power_kw=max_kw, status="UNKNOWN", source_url=source_url, connector_id="capacity", raw=raw))
        photos = panoramax_photos_from_tags(tags, source_url)
        before_add = len(records)
        add_record(records, source="OpenStreetMap public tags", source_url=source_url, name=name, address=address, city=tags.get("addr:city") or "", lat=lat, lon=lon, price_min=0.0 if is_free else price, free_condition=free_condition, connector_labels=labels, max_power_kw=max_kw, status="VERIFY", reliability="community-free" if is_free else "community-cost" if price is not None else "community-price-unknown", evidence=evidence, parking_kind=parking_kind, parking_note="OSM location/hardware evidence and the separately attributed official tariff still require an onsite/provider check before routing." if movem_price is not None else "OSM community evidence; verify price, access and parking signs before routing.", raw={**tags, "osm_type": osm_type, "osm_id": osm_id, "osm_last_modified": el.get("timestamp"), "red_movem_tariff_match": movem_price is not None}, connectors=connector_rows, photos=photos, port_count=count_int(tags.get("capacity"), default=0), source_artifact=fetch_artifact_or_none(body))
        if movem_price is not None and len(records) > before_add:
            row = records[-1]
            row["sources"].append({
                "name": "Red MOVEM official public-price regulation",
                "url": RED_MOVEM_TARIFF_URL,
                "evidence": "BOP 129/2024 sets €0.25/kWh at connectors at or below 22 kW; higher-power connectors do not qualify.",
                "checked_at": RED_MOVEM_TARIFF_CHECKED_AT,
                "source_fetched_at": RED_MOVEM_TARIFF_CHECKED_AT,
            })
            row["sources"].append({
                "name": "Red MOVEM current-network confirmation",
                "url": RED_MOVEM_CURRENT_URL,
                "evidence": "Diputación de Badajoz confirms the network remains current while upgrades can change connector power.",
                "checked_at": RED_MOVEM_TARIFF_CHECKED_AT,
                "source_fetched_at": RED_MOVEM_TARIFF_CHECKED_AT,
            })
            row["charge_claim"] = {
                "type": "cheap",
                "source": "Red MOVEM official public-price regulation",
                "url": RED_MOVEM_TARIFF_URL,
                "price_min": 0.25,
                "summary": free_condition,
                "checked_at": RED_MOVEM_TARIFF_CHECKED_AT,
            }
            row["source_fetched_at"] = oldest_contributing_source_timestamp([row]) or row.get("source_fetched_at")
            row["primary_source"] = primary_source_for(row)
    if failures:
        # Records from the tiles that DID answer are already in `records`; build_payload catches
        # this and files it in the payload's `errors` list. That is deliberate: a partially
        # fetched Spain must read as an error, not as a quietly thinner country.
        raise RuntimeError(
            f"Overpass answered {len(tiles) - len(failures)}/{len(tiles)} tiles; "
            f"failed: {'; '.join(failures[:3])}"
        )


def openchargemap_price_condition(price: Optional[float]) -> str:
    if price is None:
        return "OpenChargeMap lists this charger without a usable €/kWh price."
    if price == 0:
        return "OpenChargeMap usage cost indicates free/cheap."
    if price <= 0.25:
        return "OpenChargeMap usage cost indicates cheap charging."
    return f"OpenChargeMap usage cost lists €{price:.2f}/kWh."


def source_openchargemap(records: List[Dict[str, Any]]) -> None:
    url = "https://raw.githubusercontent.com/openchargemap/ocm-data/master/poi.json.gz"
    gz_path = CACHE / "ocm_poi.json.gz"
    source_artifact = cached_fetch(url, "ocm_poi.json.gz", ttl_hours=7 * 24, timeout=180)
    count = 0
    with gzip.open(gz_path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                poi = json.loads(line)
            except Exception:
                continue
            poi_id = poi.get("ID")
            if str(poi_id) in OPENCHARGEMAP_QUARANTINED_IDS:
                continue
            addr = poi.get("AddressInfo") or {}
            if openchargemap_country_iso(poi) not in {"", "ES"}:
                continue
            lat, lon = fnum(addr.get("Latitude")), fnum(addr.get("Longitude"))
            if not in_bbox(lat, lon, SPAIN_BBOX):
                continue
            usage_cost = poi.get("UsageCost") or ""
            price = price_from_text(usage_cost)
            if not openchargemap_has_minimum_route_evidence(poi):
                continue
            if not openchargemap_has_connector_type_evidence(poi):
                continue
            if not openchargemap_has_power_evidence(poi):
                continue
            source_page = f"https://map.openchargemap.io/?mode=details&id={poi_id}" if poi_id else url
            conns = poi.get("Connections") or []
            labels = []
            max_kw = None
            connector_rows: List[Dict[str, Any]] = []
            for c in conns:
                title = (c.get("ConnectionType") or {}).get("Title")
                label = openchargemap_connector_label(title)
                if label:
                    labels.append(label)
                p = fnum(c.get("PowerKW")); max_kw = max(max_kw or 0, p or 0) or max_kw
                status_type = c.get("StatusType") or {}
                current_type = c.get("CurrentType") or {}
                level = c.get("Level") or {}
                status_title = (status_type.get("Title") or ((poi.get("StatusType") or {}).get("Title")) or "UNKNOWN").upper()
                connector_rows.append(connector_ref(label or "Unknown", count=count_int(c.get("Quantity")), power_kw=p, status=status_title, source_url=source_page, connector_id=c.get("ID"), raw={
                    "ConnectionTypeID": c.get("ConnectionTypeID"),
                    "ConnectionTypeTitle": title,
                    "StatusTypeID": c.get("StatusTypeID"),
                    "StatusTypeTitle": status_type.get("Title"),
                    "StatusTypeIsOperational": status_type.get("IsOperational"),
                    "LevelID": c.get("LevelID"),
                    "LevelTitle": level.get("Title"),
                    "CurrentTypeID": c.get("CurrentTypeID"),
                    "CurrentTypeTitle": current_type.get("Title"),
                }))
            labels = sorted(set(labels))
            name = addr.get("Title") or "OpenChargeMap station"
            status = ((poi.get("StatusType") or {}).get("Title") or "VERIFY").upper()
            usage = ((poi.get('UsageType') or {}).get('Title') or '')
            evidence = f"OCM UsageCost: {usage_cost}; Status: {status}; Usage: {usage}"
            reliability = (
                "community-access-limited" if ACCESS_LIMIT_RE.search(evidence)
                else "community-cost" if price is not None
                else "community-price-unknown"
            )
            parking_kind, parking_context_note = openchargemap_parking_kind(
                name,
                addr.get("AddressLine1") or "",
                addr.get("Town") or "",
                usage_cost,
                usage,
            )
            if parking_kind == "unknown" and re.search(r"ikea|hotel|parking|centro", name, re.I):
                parking_kind = "retail_parking"
                parking_context_note = "Name/source context indicates retail/customer or parking-facility access; verify signs onsite."
            if openchargemap_is_weak_count_only_lead(poi, parking_kind):
                continue
            photos = []
            for media in poi.get("MediaItems") or []:
                if media.get("IsVideo") or media.get("IsEnabled") is False:
                    continue
                photos.append({
                    "url": media.get("ItemURL"),
                    "thumbnail_url": media.get("ItemThumbnailURL"),
                    "source": "OpenChargeMap media",
                    "source_url": source_page,
                    "caption": media.get("Comment") or "",
                    "license": "OpenChargeMap user-submitted media",
                })
            parking_note = parking_context_note or "Community directory cost evidence; verify in the operator app/onsite because OCM can be stale."
            before = len(records)
            add_record(records, source="OpenChargeMap public snapshot", source_url=source_page, name=name, address=addr.get("AddressLine1") or "", city=addr.get("Town") or "", lat=lat, lon=lon, price_min=price, free_condition=openchargemap_price_condition(price), connector_labels=labels, max_power_kw=max_kw, status=status, reliability=reliability, evidence=evidence, parking_kind=parking_kind, parking_note=parking_note, raw={"UUID": poi.get("UUID"), "ID": poi_id, "LastVerified": poi.get("DateLastVerified"), "DateLastStatusUpdate": poi.get("DateLastStatusUpdate"), "DateLastConfirmed": poi.get("DateLastConfirmed"), "SubmissionStatus": (poi.get("SubmissionStatus") or {}).get("Title"), "addr:postcode": addr.get("Postcode"), "UsageCost": usage_cost, "UsageType": usage, "NumberOfPoints": poi.get("NumberOfPoints"), "StatusTypeID": poi.get("StatusTypeID"), "StatusTypeTitle": (poi.get("StatusType") or {}).get("Title"), "connector_status_fields": bool(conns)}, connectors=connector_rows, photos=photos, port_count=count_int(poi.get("NumberOfPoints"), default=0), source_artifact=source_artifact)
            if len(records) == before:
                continue
            source_last_updated = raw_last_seen(records[-1].get("raw") or {})
            if source_last_updated:
                records[-1]["sources"][0]["source_last_updated"] = source_last_updated
            count += 1


def _payload_rows(payload: Any, key: str) -> List[Dict[str, Any]]:
    value = payload.get(key) if isinstance(payload, dict) else None
    if isinstance(value, dict):
        return [row for row in value.values() if isinstance(row, dict)]
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def _source_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except (ValueError, OverflowError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _endolla_connector_label(value: Any) -> str:
    token = str(value or "").strip().upper()
    if token in {"MENNEKES", "TYPE_2", "IEC_62196_T2"}:
        return "Type 2"
    if token in {"CCS_TYPE_2", "CCS2", "COMBO_2"}:
        return "CCS2"
    if token == "CHADEMO":
        return "CHAdeMO"
    if token == "WALL_OUTLET":
        return "Wall outlet (connector unspecified)"
    return str(value or "Unknown connector").strip() or "Unknown connector"


def _endolla_motorcycle_only(port: Dict[str, Any]) -> bool:
    """Return true only for an explicitly motorcycle-only Endolla port."""
    evidence = " ".join(
        str(port.get(key) or "")
        for key in ("notes", "note", "label", "restriction", "vehicle_type")
    ).upper()
    return "MOTORCYCLE_ONLY" in evidence


def _endolla_status_index(payload: Dict[str, Any]) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    result: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for location in _payload_rows(payload, "locations"):
        for station in location.get("stations") or []:
            if not isinstance(station, dict):
                continue
            for port in station.get("ports") or []:
                if isinstance(port, dict):
                    result[(str(location.get("id")), str(station.get("id")), str(port.get("id")))] = port
    return result


def _endolla_page_text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def validate_endolla_tariff_contract(tariff_html: str, faq_html: str) -> None:
    """Pin every first-party fact required by the narrow €0.22 Endolla claim."""
    tariff = normalized_identity_text(_endolla_page_text(tariff_html))
    faq = normalized_identity_text(_endolla_page_text(faq_html))
    tariff_checks = {
        "EcoVehicles plan": r"\becovehicles\b",
        "monthly subscription": r"\bsubscripcio mensual\b",
        "B:SM car park": r"\baparcament bsm\b",
        "normal charging": r"\btipus de recarrega\s*:\s*normal\b",
        "night tariff": r"\b0[.,]22\s*€\s*/\s*kwh\s+nocturn\b",
    }
    missing = [label for label, pattern in tariff_checks.items() if not re.search(pattern, tariff)]
    night_start = re.search(r"\b00(?::00|\s*h)\b", faq)
    night_end = re.search(r"\b0?7:59\b", faq)
    if missing or not night_start or not night_end:
        detail = ", ".join(missing + (["00:00 night start"] if not night_start else []) + (["07:59 night end"] if not night_end else []))
        raise RuntimeError(f"Endolla official tariff contract changed or is incomplete: {detail}")


def parse_barcelona_endolla(
    location_payload: Dict[str, Any],
    status_payload: Dict[str, Any],
    tariff_html: str,
    faq_html: str,
    *,
    observed_at: Optional[datetime] = None,
    minimum_locations: int = 100,
    location_artifact: Optional[FetchArtifact] = None,
    status_artifact: Optional[FetchArtifact] = None,
    tariff_artifact: Optional[FetchArtifact] = None,
    faq_artifact: Optional[FetchArtifact] = None,
) -> List[Dict[str, Any]]:
    """Build only the exact conditional cheap Endolla case from CC BY station data."""
    validate_endolla_tariff_contract(tariff_html, faq_html)
    locations = _payload_rows(location_payload, "locations")
    if len(locations) < minimum_locations:
        raise RuntimeError(f"Barcelona Endolla location population shrank to {len(locations)} (<{minimum_locations})")
    status_index = _endolla_status_index(status_payload)
    now = observed_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        now = now.replace(tzinfo=timezone.utc)
    out: List[Dict[str, Any]] = []
    for location in locations:
        host = str(((location.get("host") or {}).get("name") or ""))
        if location.get("onstreet_location") is not False or not re.search(r"b:?sm|barcelona de serveis municipals", host, re.I):
            continue
        coordinates = location.get("coordinates") or {}
        lat, lon = fnum(coordinates.get("latitude")), fnum(coordinates.get("longitude"))
        connectors: List[Dict[str, Any]] = []
        station_names: List[str] = []
        timestamps: List[str] = []
        for station in location.get("stations") or []:
            if not isinstance(station, dict):
                continue
            station_name = str(station.get("notes") or station.get("label") or "").strip()
            if station_name and station_name not in station_names:
                station_names.append(station_name)
            for detail_port in station.get("ports") or []:
                if not isinstance(detail_port, dict):
                    continue
                if _endolla_motorcycle_only(detail_port):
                    continue
                power = fnum(detail_port.get("power_kw"))
                if power is None or power > 22.0:
                    continue
                key = (str(location.get("id")), str(station.get("id")), str(detail_port.get("id")))
                status_port = status_index.get(key) or {}
                updated = status_port.get("last_updated") or detail_port.get("last_updated")
                parsed_updated = _source_datetime(updated)
                age = (now.astimezone(timezone.utc) - parsed_updated.astimezone(timezone.utc)).total_seconds() if parsed_updated else None
                raw_statuses = status_port.get("port_status") or detail_port.get("port_status") or []
                raw_status = str(((raw_statuses[0] if raw_statuses else {}) or {}).get("status") or "UNKNOWN").upper()
                observed_status_fresh = age is not None and 0 <= age <= ENDOLLA_STATUS_MAX_AGE_SECONDS
                if updated:
                    timestamps.append(str(updated))
                connectors.append(connector_ref(
                    _endolla_connector_label(detail_port.get("connector_type")),
                    count=1,
                    power_kw=power,
                    # This weekly artifact is never live telemetry at runtime. Preserve the
                    # observed state only as provenance and always route as verify-first.
                    status="UNKNOWN",
                    source_url=BARCELONA_ENDOLLA_LOCATIONS_URL,
                    connector_id=f"{station.get('id')}:{detail_port.get('id')}",
                    raw={
                        "location_id": location.get("id"),
                        "station_id": station.get("id"),
                        "port_id": detail_port.get("id"),
                        "connector_type": detail_port.get("connector_type"),
                        "payment_required": any(bool(auth.get("payment_required")) for auth in detail_port.get("authentications") or [] if isinstance(auth, dict)),
                        "observed_status": raw_status,
                        "observed_status_fresh": observed_status_fresh,
                        "status_last_updated": updated,
                        "status_age_seconds": round(age, 1) if age is not None else None,
                    },
                ))
        if not connectors:
            continue
        address = location.get("address") or {}
        name = station_names[0] if station_names else f"Endolla B:SM — {address.get('address_string') or location.get('id')}"
        condition = (
            "€0.22/kWh applies only to normal charging in a B:SM car park during 00:00–07:59 "
            "with an eligible EcoVehicles or professional membership subscription; parking fees are separate."
        )
        latest_timestamp = max(timestamps, key=lambda value: _source_datetime(value) or datetime.min.replace(tzinfo=timezone.utc)) if timestamps else None
        before = len(out)
        add_record(
            out,
            source="Barcelona Endolla open data",
            source_url=BARCELONA_ENDOLLA_LOCATIONS_URL,
            name=name,
            address=str(address.get("address_string") or ""),
            city=str(address.get("locality") or "Barcelona"),
            lat=lat,
            lon=lon,
            price_min=0.22,
            free_condition=condition,
            connector_labels=connector_labels_from_rows(connectors),
            max_power_kw=max((fnum(c.get("power_kw")) or 0 for c in connectors), default=0) or None,
            status="VERIFY",
            reliability="official-cheap",
            evidence="CC BY 4.0 Endolla location and port-status datasets plus the official conditional B:SM night subscription tariff.",
            parking_kind="paid_or_metered",
            parking_note="A separate B:SM car-park tariff applies; the €0.22 charging tariff does not include parking. Verify the current parking price and access before routing.",
            raw={
                "endolla_location_id": location.get("id"),
                "onstreet_location": location.get("onstreet_location"),
                "host": host,
                "last_seen": latest_timestamp,
                "connector_status_fields": True,
                "official_hardware_inventory": True,
                "tariff_scope": "BSM normal charging; 00:00-07:59; eligible subscription",
            },
            connectors=connectors,
            port_count=len(connectors),
            source_artifact=location_artifact,
        )
        if len(out) == before:
            continue
        row = out[-1]
        row["sources"] = [
            {
                "name": "Barcelona Endolla location/details open data",
                "url": BARCELONA_ENDOLLA_LOCATIONS_URL,
                "evidence": "Official location, address, access, connector and power facts.",
                "license": "CC BY 4.0",
                "license_url": "https://creativecommons.org/licenses/by/4.0/",
                "attribution": "Ajuntament de Barcelona / Endolla",
                "transformation": "Normalized and combined by Volta; weekly port status is retained as non-live provenance only.",
                "source_fetched_at": location_artifact.fetched_at if location_artifact else row.get("source_fetched_at"),
            },
            {
                "name": "Barcelona Endolla port-status open data",
                "url": BARCELONA_ENDOLLA_STATUS_URL,
                "evidence": "Official per-port state and timestamp; stale states are normalized to UNKNOWN.",
                "license": "CC BY 4.0",
                "license_url": "https://creativecommons.org/licenses/by/4.0/",
                "attribution": "Ajuntament de Barcelona / Endolla",
                "transformation": "Normalized by Volta; observed status is never presented as current live telemetry.",
                "source_fetched_at": status_artifact.fetched_at if status_artifact else row.get("source_fetched_at"),
            },
            {
                "name": "Endolla official conditional tariff",
                "url": BARCELONA_ENDOLLA_TARIFF_URL,
                "evidence": condition,
                "source_fetched_at": tariff_artifact.fetched_at if tariff_artifact else row.get("source_fetched_at"),
            },
            {
                "name": "Endolla official tariff-window and parking FAQ",
                "url": BARCELONA_ENDOLLA_FAQ_URL,
                "evidence": "Official FAQ defines the night window as 00:00–07:59 and keeps charging/access conditions separate from parking.",
                "source_fetched_at": faq_artifact.fetched_at if faq_artifact else row.get("source_fetched_at"),
            },
        ]
        row["pricing"] = [{
            "price": 0.22,
            "currency": "EUR",
            "unit": "kWh",
            "valid_from": "00:00",
            "valid_until": "08:00",
            "source": "Endolla official conditional tariff",
            "human": condition,
        }]
        row["charge_claim"] = {
            "type": "cheap",
            "source": "Endolla official conditional tariff",
            "url": BARCELONA_ENDOLLA_TARIFF_URL,
            "price_min": 0.22,
            "summary": condition,
            "checked_at": tariff_artifact.fetched_at if tariff_artifact else row.get("source_fetched_at"),
        }
        row["parking_claim"] = {
            "kind": row["parking"]["kind"],
            "source": "Endolla/B:SM parking terms",
            "url": BARCELONA_ENDOLLA_FAQ_URL,
            "cost_hint": "Separate B:SM car-park tariff applies; verify the current amount.",
            "summary": row["parking"]["note"],
        }
        row["primary_source"] = primary_source_for(row)
        row["location_claim"] = location_claim_for(row)
        row["hardware_claim"] = hardware_claim_for(row)
        row["access_claim"] = access_claim_for(row)
        row["source_fetched_at"] = oldest_contributing_source_timestamp([row]) or row.get("source_fetched_at")
    return out


def source_barcelona_endolla(records: List[Dict[str, Any]]) -> None:
    locations, location_artifact = cached_json(
        BARCELONA_ENDOLLA_LOCATIONS_URL,
        "barcelona_endolla_locations.json",
        ttl_hours=1,
        with_artifact=True,
        timeout=35,
    )
    statuses, status_artifact = cached_json(
        BARCELONA_ENDOLLA_STATUS_URL,
        "barcelona_endolla_status.json",
        ttl_hours=0.25,
        with_artifact=True,
        timeout=35,
    )
    tariff_artifact = cached_fetch(
        BARCELONA_ENDOLLA_TARIFF_URL,
        "barcelona_endolla_tariff.html",
        ttl_hours=24,
        timeout=35,
    )
    faq_artifact = cached_fetch(
        BARCELONA_ENDOLLA_FAQ_URL,
        "barcelona_endolla_faq.html",
        ttl_hours=24,
        timeout=35,
    )
    for label, artifact in (("tariff", tariff_artifact), ("FAQ", faq_artifact)):
        if artifact.age_seconds > ENDOLLA_TARIFF_MAX_AGE_SECONDS:
            raise RuntimeError(f"Endolla {label} evidence is older than seven days")
    records.extend(parse_barcelona_endolla(
        locations,
        statuses,
        tariff_artifact.decode("utf-8", "replace"),
        faq_artifact.decode("utf-8", "replace"),
        location_artifact=location_artifact,
        status_artifact=status_artifact,
        tariff_artifact=tariff_artifact,
        faq_artifact=faq_artifact,
    ))


def utm30n_to_wgs84(easting: float, northing: float) -> Tuple[float, float]:
    """Convert ETRS89/UTM zone 30N to WGS84 (sub-metre datum difference in Spain)."""
    a = 6378137.0
    ecc_sq = 0.00669438002290
    k0 = 0.9996
    x = float(easting) - 500000.0
    y = float(northing)
    ecc_prime_sq = ecc_sq / (1.0 - ecc_sq)
    m = y / k0
    mu = m / (a * (1.0 - ecc_sq / 4.0 - 3.0 * ecc_sq ** 2 / 64.0 - 5.0 * ecc_sq ** 3 / 256.0))
    e1 = (1.0 - math.sqrt(1.0 - ecc_sq)) / (1.0 + math.sqrt(1.0 - ecc_sq))
    phi1 = (
        mu
        + (3.0 * e1 / 2.0 - 27.0 * e1 ** 3 / 32.0) * math.sin(2.0 * mu)
        + (21.0 * e1 ** 2 / 16.0 - 55.0 * e1 ** 4 / 32.0) * math.sin(4.0 * mu)
        + (151.0 * e1 ** 3 / 96.0) * math.sin(6.0 * mu)
        + (1097.0 * e1 ** 4 / 512.0) * math.sin(8.0 * mu)
    )
    n1 = a / math.sqrt(1.0 - ecc_sq * math.sin(phi1) ** 2)
    t1 = math.tan(phi1) ** 2
    c1 = ecc_prime_sq * math.cos(phi1) ** 2
    r1 = a * (1.0 - ecc_sq) / (1.0 - ecc_sq * math.sin(phi1) ** 2) ** 1.5
    d = x / (n1 * k0)
    lat = phi1 - (n1 * math.tan(phi1) / r1) * (
        d ** 2 / 2.0
        - (5.0 + 3.0 * t1 + 10.0 * c1 - 4.0 * c1 ** 2 - 9.0 * ecc_prime_sq) * d ** 4 / 24.0
        + (61.0 + 90.0 * t1 + 298.0 * c1 + 45.0 * t1 ** 2 - 252.0 * ecc_prime_sq - 3.0 * c1 ** 2) * d ** 6 / 720.0
    )
    lon = (
        d
        - (1.0 + 2.0 * t1 + c1) * d ** 3 / 6.0
        + (5.0 - 2.0 * c1 + 28.0 * t1 - 3.0 * c1 ** 2 + 8.0 * ecc_prime_sq + 24.0 * t1 ** 2) * d ** 5 / 120.0
    ) / math.cos(phi1)
    return math.degrees(lat), -3.0 + math.degrees(lon)


def _official_hardware(text: str, source_url: str) -> Tuple[List[str], List[Dict[str, Any]], Optional[float]]:
    labels: List[str] = []
    for pattern, label in ((r"\bCCS(?:2)?\b", "CCS2"), (r"\bCHAdeMO\b", "CHAdeMO"), (r"\b(?:Mennekes|Type\s*2)\b", "Type 2")):
        if re.search(pattern, text or "", re.I) and label not in labels:
            labels.append(label)
    powers = [fnum(value) for value in re.findall(r"([0-9]+(?:[.,][0-9]+)?)\s*kW\b", text or "", re.I)]
    powers = [value for value in powers if value is not None]
    max_power = max(powers) if powers else None
    connectors = [
        connector_ref(label, count=1, power_kw=max_power if len(labels) == 1 else None, status="UNKNOWN", source_url=source_url, connector_id=f"official-{slug(label)}")
        for label in labels
    ]
    return labels, connectors, max_power


def parse_madrid_official_csv(
    csv_text: str,
    *,
    minimum_records: int = 100,
    source_artifact: Optional[FetchArtifact] = None,
) -> List[Dict[str, Any]]:
    raw_rows = [dict(row) for row in csv.DictReader(io.StringIO(csv_text), delimiter=";")]
    if len(raw_rows) < minimum_records:
        raise RuntimeError(f"Madrid official charger population shrank to {len(raw_rows)} (<{minimum_records})")
    out: List[Dict[str, Any]] = []
    for source_row in raw_rows:
        easting, northing = fnum(source_row.get("POINT_X")), fnum(source_row.get("POINT_Y"))
        if easting is None or northing is None:
            continue
        lat, lon = utm30n_to_wgs84(easting, northing)
        hardware = str(source_row.get("CARACTERÍ") or source_row.get("CARACTERÍSTICAS_EQUIPO") or "")
        labels, connectors, max_power = _official_hardware(hardware, MADRID_CHARGERS_CSV_URL)
        evidence = "Madrid CC BY 4.0 monthly dataset publishes location, operator and hardware only; it contains no tariff or live availability."
        add_record(
            out,
            source="Madrid public charger open data",
            source_url=MADRID_CHARGERS_CSV_URL,
            name=f"Madrid public charger — {source_row.get('UBICACIÓN') or source_row.get('BARRIO') or 'official record'}",
            address=str(source_row.get("UBICACIÓN") or ""),
            city="Madrid",
            lat=lat,
            lon=lon,
            price_min=None,
            free_condition="The national registry publishes no tariff; check the price in the operator app.",
            connector_labels=labels,
            max_power_kw=max_power,
            status="VERIFY",
            reliability="official-charger-location-price-unknown",
            evidence=evidence,
            parking_kind="street_or_municipal" if re.search(r"v[ií]a p[uú]blica", str(source_row.get("EMPLAZAMIE") or ""), re.I) else "unknown",
            parking_note="Madrid open data identifies the setting but publishes no parking tariff; verify signs onsite.",
            raw={
                **source_row,
                "operator": source_row.get("OPERADOR"),
                "coordinate_source": "official_etrs89_utm30n",
                "corroboration_only": True,
                "official_hardware_inventory": True,
            },
            connectors=connectors,
            port_count=count_int(source_row.get("Nº_EQUIPO"), default=0),
            source_artifact=source_artifact,
        )
        if out:
            out[-1]["sources"][0]["license"] = "CC BY 4.0"
            out[-1]["sources"][0]["license_url"] = "https://creativecommons.org/licenses/by/4.0/"
            out[-1]["sources"][0]["attribution"] = "Ayuntamiento de Madrid"
            out[-1]["sources"][0]["transformation"] = "Coordinates converted by Volta from ETRS89 / UTM zone 30N to WGS84; unmatched price-unknown rows are not admitted."
            out[-1]["sources"][0]["catalog_url"] = MADRID_CHARGERS_CATALOG_URL
            if source_artifact is not None:
                out[-1]["sources"][0]["source_fetched_at"] = source_artifact.fetched_at
    return out


def source_madrid_official(records: List[Dict[str, Any]]) -> None:
    artifact = cached_fetch(MADRID_CHARGERS_CSV_URL, "madrid_public_chargers.csv", ttl_hours=24 * 7, timeout=45)
    records.extend(parse_madrid_official_csv(artifact.decode("utf-8-sig", "replace"), source_artifact=artifact))


def _html_text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def _html_attr(fragment: str, property_name: str) -> str:
    match = re.search(rf"property=[\"']{re.escape(property_name)}[\"'][^>]*\bcontent=[\"']([^\"']*)", fragment, re.I | re.S)
    return html.unescape(match.group(1)).strip() if match else ""


def parse_zaragoza_official_page(
    page_html: str,
    *,
    metadata_payload: Optional[Dict[str, Any]] = None,
    minimum_records: int = 37,
    source_artifact: Optional[FetchArtifact] = None,
) -> List[Dict[str, Any]]:
    metadata_by_title = {
        normalized_identity_text(item.get("title")): item
        for item in ((metadata_payload or {}).get("equipamiento") or [])
        if isinstance(item, dict) and item.get("title")
    }
    parsed: List[Dict[str, Any]] = []
    for block in re.split(r"(?=<div\s+class=[\"']geo[\"']\s+id=[\"']geo[\"'])", page_html or "", flags=re.I):
        title = _html_attr(block, "title")
        lat, lon = fnum(_html_attr(block, "latitude")), fnum(_html_attr(block, "longitude"))
        if not title or lat is None or lon is None:
            continue
        street = _html_attr(block, "calle")
        record_id = _html_attr(block, "id")
        operator_match = re.search(r"data-term=[\"']gestora[\"'].*?<br\s*/?>\s*([^<]+)", block, re.I | re.S)
        operator = html.unescape(operator_match.group(1)).strip() if operator_match else ""
        tech_match = re.search(r"data-term=[\"']tecnologia[\"'][^>]*>(.*?)</li>", block, re.I | re.S)
        technology = _html_text(tech_match.group(1) if tech_match else "")
        labels, connectors, max_power = _official_hardware(technology, ZARAGOZA_CHARGERS_URL)
        port_match = re.search(r"Puntos\s+de\s+carga:\s*<strong>(\d+)</strong>", block, re.I | re.S)
        metadata = metadata_by_title.get(normalized_identity_text(title)) or {}
        last_updated = metadata.get("lastUpdated")
        evidence = "Zaragoza's official reusable municipal page publishes location, operator and hardware; missing payment text is never a free-price claim."
        add_record(
            parsed,
            source="Zaragoza municipal open data",
            source_url=ZARAGOZA_CHARGERS_URL,
            name=title,
            address=street,
            city="Zaragoza",
            lat=lat,
            lon=lon,
            price_min=None,
            free_condition="The national registry publishes no tariff; check the price in the operator app.",
            connector_labels=labels,
            max_power_kw=max_power,
            status="VERIFY",
            reliability="official-charger-location-price-unknown",
            evidence=evidence,
            parking_kind="street_or_municipal",
            parking_note="The municipal source publishes no parking tariff; verify regulated signs onsite.",
            raw={
                "zaragoza_id": record_id,
                "operator": operator,
                "technology": technology,
                "payment_text_present": bool(re.search(r"\bPago\b", _html_text(block), re.I)),
                "last_seen": last_updated,
                "corroboration_only": True,
                "official_hardware_inventory": True,
            },
            connectors=connectors,
            port_count=count_int(port_match.group(1) if port_match else None, default=0),
            source_artifact=source_artifact,
        )
        if parsed:
            parsed[-1]["sources"][0]["license"] = "Zaragoza municipal open-data reuse conditions"
            parsed[-1]["sources"][0]["license_url"] = ZARAGOZA_REUSE_URL
            parsed[-1]["sources"][0]["attribution"] = "Ayuntamiento de Zaragoza"
            parsed[-1]["sources"][0]["transformation"] = "Normalized and combined by Volta as corroboration-only evidence; no missing tariff is inferred to be free."
            if last_updated:
                parsed[-1]["sources"][0]["source_last_updated"] = last_updated
    if len(parsed) < minimum_records:
        raise RuntimeError(f"Zaragoza official charger population shrank to {len(parsed)} (<{minimum_records})")
    return parsed


def source_zaragoza_official(records: List[Dict[str, Any]]) -> None:
    artifact = cached_fetch(ZARAGOZA_CHARGERS_URL, "zaragoza_public_chargers.html", ttl_hours=24, timeout=35)
    try:
        metadata = cached_json(ZARAGOZA_CATEGORY_URL, "zaragoza_public_chargers_category.json", ttl_hours=24, timeout=35)
    except Exception:
        metadata = {}
    records.extend(parse_zaragoza_official_page(artifact.decode("utf-8", "replace"), metadata_payload=metadata, source_artifact=artifact))


def parse_moraleja_green(
    records: List[Dict[str, Any]],
    acciona_html: str,
    venue_html: str,
    *,
    acciona_artifact: Optional[FetchArtifact] = None,
    venue_artifact: Optional[FetchArtifact] = None,
) -> None:
    if acciona_artifact is None or venue_artifact is None:
        raise RuntimeError("Moraleja Green requires timestamped evidence from both first-party pages")
    for label, artifact in (
        ("ACCIONA Green Smart Charge", acciona_artifact),
        ("Moraleja Green services", venue_artifact),
    ):
        if artifact.age_seconds < 0 or artifact.age_seconds > MORALEJA_EVIDENCE_MAX_AGE_SECONDS:
            raise RuntimeError(f"{label} evidence is too old for an official free claim")
    acciona_text = normalized_identity_text(_html_text(acciona_html))
    venue_text = normalized_identity_text(_html_text(venue_html))
    if not (
        "acciona" in acciona_text
        and "green smart charge" in acciona_text
        and re.search(r"(?:proyecto\s+piloto|innovacion)", acciona_text)
        and "acciona" in venue_text
        and re.search(r"nueva\s+tecnologia", venue_text)
        and re.search(r"recarga\s+inteligente", venue_text)
    ):
        raise RuntimeError("Moraleja Green first-party identity contract no longer matches ACCIONA Green Smart Charge")
    if not (
        re.search(r"recarga\s+es\s+gratuita", acciona_text)
        and "rfid" in acciona_text
        and re.search(r"punto\s+de\s+informacion", acciona_text)
    ):
        raise RuntimeError("ACCIONA Green Smart Charge page no longer proves free RFID charging and information-desk recovery")
    if not (
        re.search(r"tres\s+cargadores", venue_text)
        and re.search(r"parking\s+norte", venue_text)
        and re.search(r"planta\s+-?\s*2", venue_text)
    ):
        raise RuntimeError("Moraleja Green services page no longer proves three chargers in Parking Norte level -2")
    if not (
        re.search(r"2\s+horas\s+gratuitas", venue_text)
        and re.search(r"parking\s+gratis\s+el\s+fin\s+de\s+semana", venue_text)
        and re.search(r"viernes\s+a\s+las\s+16", venue_text)
    ):
        raise RuntimeError("Moraleja Green services page no longer proves the current limited free-parking windows")
    condition = (
        "ACCIONA states Green Smart Charge is free but requires its RFID process and information-desk assistance; "
        "Moraleja Green lists three chargers in Parking Norte, level -2. Confirm access at the information desk before relying on it."
    )
    add_record(
        records,
        source="ACCIONA Green Smart Charge official policy",
        source_url=MORALEJA_FREE_URL,
        name="ACCIONA Green Smart Charge — Moraleja Green",
        address="Avenida de Europa 13, Parking Norte, planta -2",
        city="Alcobendas",
        lat=40.5194044,
        lon=-3.6564012,
        price_min=0.0,
        free_condition=condition,
        connector_labels=[],
        max_power_kw=None,
        status="VERIFY",
        reliability="official-free-policy",
        evidence="First-party ACCIONA free-charge policy plus the current Moraleja Green venue service listing; connector compatibility and occupancy are not published.",
        parking_kind="time_limited_or_validated",
        parking_note="Moraleja Green currently states the first two hours are free on weekdays and parking is free for the weekend from Friday 16:00; charging does not prove unlimited free parking. Verify current venue terms.",
        raw={
            "official_static_lead": True,
            "coordinate_source": "openstreetmap_mall_centroid",
            "venue_chargers": 3,
            "preserve_location_claim": True,
        },
        connectors=[],
        port_count=None,
        source_artifact=acciona_artifact,
    )
    row = records[-1]
    row["is_free"] = False
    row["price_claim_type"] = "access_limited_free"
    row["access"] = {
        "level": "restricted",
        "label": "Conditional RFID access",
        "note": "Free charging requires the ACCIONA RFID process and information desk assistance; confirm access before routing.",
    }
    row["sources"] = [
        {"name": "ACCIONA Green Smart Charge instructions", "url": MORALEJA_FREE_URL, "evidence": "ACCIONA states charging is free and uses the RFID attached to the charger.", "source_fetched_at": acciona_artifact.fetched_at},
        {"name": "Moraleja Green current services page", "url": MORALEJA_VENUE_URL, "evidence": "The venue lists three ACCIONA chargers in Parking Norte, level -2.", "source_fetched_at": venue_artifact.fetched_at},
        {"name": "OpenStreetMap mall geometry", "url": "https://www.openstreetmap.org/way/23590738", "evidence": "ODbL venue centroid only; exact charger bay remains the venue's level -2 description.", "license": "ODbL"},
    ]
    row["charge_claim"] = {"type": "access_limited_free", "source": "ACCIONA Green Smart Charge instructions", "url": MORALEJA_FREE_URL, "price_min": 0.0, "summary": condition}
    row["parking_claim"] = {"kind": row["parking"]["kind"], "source": "Moraleja Green current services page", "url": MORALEJA_VENUE_URL, "cost_hint": None, "summary": row["parking"]["note"]}
    row["primary_source"] = primary_source_for(row)
    row["location_claim"] = {
        "source": "Moraleja Green current services page + OSM venue geometry",
        "url": MORALEJA_VENUE_URL,
        "lat": row["lat"],
        "lon": row["lon"],
        "precision": "venue_centroid",
        "method": "official_venue_address_plus_osm_geometry",
        "summary": "The venue confirms Parking Norte level -2; coordinates are the reusable shopping-centre centroid, not an exact bay pin.",
    }
    row["hardware_claim"] = hardware_claim_for(row)
    row["access_claim"] = access_claim_for(row)
    row["source_fetched_at"] = oldest_contributing_source_timestamp([row]) or "2026-07-15T00:00:00Z"


def source_moraleja_green(records: List[Dict[str, Any]]) -> None:
    acciona_artifact = cached_fetch(MORALEJA_FREE_URL, "acciona_green_smart_charge.html", ttl_hours=24, timeout=35)
    venue_artifact = cached_fetch(MORALEJA_VENUE_URL, "moraleja_green_services.html", ttl_hours=24, timeout=35)
    parse_moraleja_green(
        records,
        acciona_artifact.decode("utf-8", "replace"),
        venue_artifact.decode("utf-8", "replace"),
        acciona_artifact=acciona_artifact,
        venue_artifact=venue_artifact,
    )


def source_electromaps_probe(records: List[Dict[str, Any]]) -> None:
    # Gentle, narrow scrape: use known Valencia city page, parse a small number of station pages
    # that advertise free/gratis/0.25 in their HTML. This is secondary evidence only.
    city_url = "https://www.electromaps.com/es/puntos-carga/espana/valencia"
    try:
        city_fetch = cached_fetch(city_url, "electromaps_valencia_city.html", ttl_hours=24)
        text = city_fetch.decode("utf-8", "replace")
    except Exception:
        return
    links = []
    for href in re.findall(r'href="([^"]+/es/puntos-carga/espana/valencia/[^"]+)"', text):
        href = urllib.parse.urljoin(city_url, html.unescape(href))
        if href not in links:
            links.append(href)
    # Prioritize obvious supermarket/free/mercadona/lidl/carrefour paths.
    links.sort(key=lambda u: 0 if re.search(r"mercadona|lidl|carrefour|ikea|gratis|free", u, re.I) else 1)
    for i, url in enumerate(links[:60]):
        try:
            page_fetch = cached_fetch(url, "electromaps_" + slug(url) + ".html", ttl_hours=72)
            page = page_fetch.decode("utf-8", "replace")
        except Exception:
            continue
        txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(page)))
        if not re.search(r"gratis|free|0[,\.]?25\s*€|0[,\.]?2\s*€", txt, re.I):
            continue
        # Try JSON-LD coordinates first, fallback to embedded lat/lon patterns.
        lat = lon = None
        for pat in [r'"latitude"\s*:\s*"?(-?\d+\.\d+)"?.{0,80}"longitude"\s*:\s*"?(-?\d+\.\d+)', r'lat(?:itude)?["\']?\s*[:=]\s*["\']?(-?\d+\.\d+).*?lon(?:gitude)?["\']?\s*[:=]\s*["\']?(-?\d+\.\d+)']:
            m = re.search(pat, page, re.I | re.S)
            if m:
                lat, lon = fnum(m.group(1)), fnum(m.group(2)); break
        if not in_bbox(lat, lon):
            continue
        name = re.search(r'<h1[^>]*>(.*?)</h1>', page, re.I | re.S)
        name_text = re.sub(r"<[^>]+>", " ", html.unescape(name.group(1))).strip() if name else "Electromaps cheap/free lead"
        price = price_from_text(txt)
        evidence_match = re.search(r"(.{0,90}(?:gratis|free|0[,\.]?25\s*€|0[,\.]?2\s*€).{0,140})", txt, re.I)
        evidence = evidence_match.group(1) if evidence_match else "Electromaps page contains cheap/free text."
        add_record(records, source="Electromaps public page", source_url=url, name=name_text, address="", city="Valencia province", lat=lat, lon=lon, price_min=price, free_condition="Electromaps page mentions free/cheap charging; verify in app." if price is None else "Electromaps page has cheap tariff evidence.", connector_labels=["Type 2"], max_power_kw=None, status="VERIFY", reliability="directory-page", evidence=evidence, parking_kind="unknown", parking_note="Electromaps directory evidence; parking/live bay status still needs app/onsite confirmation.", source_artifact=fetch_artifact_or_none(page_fetch))


def electromaps_lidl_official_match(records: List[Dict[str, Any]], detail: Dict[str, Any], lat: Optional[float], lon: Optional[float]) -> Optional[Dict[str, Any]]:
    """Return the official Lidl row this Electromaps detail can safely enrich.

    Some exact Lidl Electromaps details publish connector rows/photos while the
    charge price is "Por ver" or the directory-level status is not working. They
    are not safe standalone route recommendations, but they are useful
    corroborating evidence when they strongly match a Lidl official free-list row.
    """
    if lat is None or lon is None:
        return None
    name = str(detail.get("name") or "")
    if not re.search(r"\blidl\b", name, re.I):
        return None
    address = detail.get("address") or {}
    candidate = {
        "name": name,
        "address": " ".join(str(address.get(k) or "") for k in ("address", "street", "street_number", "postal_code", "city")),
        "city": address.get("city") or "",
        "lat": lat,
        "lon": lon,
        "source": "Electromaps web-map API",
        "raw": {"brand": "Lidl"},
    }
    for official in records:
        if official.get("source") != "Lidl official free list":
            continue
        distance = _dist_km(official, candidate)
        if distance <= 0.09 and strong_station_identity_match(official, candidate, distance_km=distance):
            return official
        if loose_lidl_locator_match(records, official, candidate, distance):
            return official
    return None


def electromaps_existing_photo_match(records: List[Dict[str, Any]], detail: Dict[str, Any], lat: Optional[float], lon: Optional[float]) -> Optional[Dict[str, Any]]:
    """Return an existing free row this Electromaps detail can safely photo-enrich."""
    if lat is None or lon is None:
        return None
    image = detail.get("image") if isinstance(detail.get("image"), dict) else {}
    if not (image.get("l") or image.get("m") or image.get("s")):
        return None
    address = detail.get("address") or {}
    candidate = {
        "name": detail.get("name") or "",
        "address": " ".join(str(address.get(k) or "") for k in ("address", "street", "street_number", "postal_code", "city")),
        "city": address.get("city") or "",
        "lat": lat,
        "lon": lon,
        "source": "Electromaps web-map API",
        "raw": {"brand": str(detail.get("contact") or detail.get("name") or "").split()[0]},
    }
    for existing in records:
        distance = _dist_km(existing, candidate)
        if distance <= 0.08 and record_source_family(existing) == "electromaps" and strong_station_identity_match(existing, candidate, distance_km=distance):
            return None
    for existing in records:
        if record_source_family(existing) == "electromaps":
            continue
        if not existing.get("is_free") or existing.get("access", {}).get("limited"):
            continue
        if ((existing.get("media") or {}).get("photo_status") == "public_photo_available"):
            continue
        distance = _dist_km(existing, candidate)
        if distance <= 0.08 and strong_station_identity_match(existing, candidate, distance_km=distance):
            return existing
    return None


def source_electromaps_mapi(records: List[Dict[str, Any]]) -> None:
    headers = {
        "Accept": "application/json",
        "App-platform": "web",
        "Referer": "https://map.electromaps.com/es/",
        "User-Agent": UA,
    }
    types = "ON_STREET,PARKING,AIRPORT,CAMPING,HOTEL,RESTAURANT,SHOP,WORKSHOP,FUEL_STATION,CAR_DEALER,MALL,TAXI"
    params = urllib.parse.urlencode({
        "latNE": VALENCIA_BBOX[2],
        "lngNE": VALENCIA_BBOX[3],
        "latSW": VALENCIA_BBOX[0],
        "lngSW": VALENCIA_BBOX[1],
        "realtime": "false",
        "connectors": "",
        "types": types,
        "power": "3",
        "app": "false",
        "rfid": "false",
        "favorites": "false",
        "skipAuthTokenValidation": "true",
    })
    base = "https://www.electromaps.com/mapi/v2"
    locations_result = cached_json(
        f"{base}/locations?{params}",
        "electromaps_mapi_locations.json",
        ttl_hours=6,
        headers=headers,
        with_artifact=True,
    )
    locations, _locations_artifact = unpack_json_artifact(locations_result)
    limit = int(os.environ.get("VOLTA_ELECTROMAPS_DETAIL_LIMIT", "1300"))
    seen = 0
    for loc in locations:
        lat, lon = fnum(loc.get("latitude")), fnum(loc.get("longitude"))
        if not likely_valencia_province(lat, lon, {}):
            continue
        loc_id = loc.get("id")
        if not loc_id:
            continue
        if seen >= limit:
            break
        seen += 1
        detail_url = f"{base}/locations/{loc_id}?skipAuthTokenValidation=true"
        try:
            detail_result = cached_json(
                detail_url,
                f"electromaps_location_{loc_id}.json",
                ttl_hours=48,
                headers=headers,
                with_artifact=True,
            )
            d, detail_artifact = unpack_json_artifact(detail_result)
        except Exception:
            continue
        addr = d.get("address") or {}
        if addr.get("area_lvl2") and "valencia" not in str(addr.get("area_lvl2")).lower():
            continue
        if addr.get("postal_code") and not str(addr.get("postal_code")).startswith("46"):
            continue
        lat, lon = fnum(d.get("latitude")), fnum(d.get("longitude"))
        if not likely_valencia_province(lat, lon, {**addr, "name": d.get("name")}):
            continue
        connectors = d.get("connectors") or []
        lidl_official_match = electromaps_lidl_official_match(records, d, lat, lon)
        photo_only_match = electromaps_existing_photo_match(records, d, lat, lon)
        if connectors and not any(str(c.get("status") or "").upper() in {"AVAILABLE", "WORKING", "UNKNOWN", "CHARGING"} for c in connectors) and not photo_only_match:
            continue
        if str(d.get("offline_status") or "").upper() == "NOT_WORKING" and not any(str(c.get("status") or "").upper() in {"AVAILABLE", "WORKING"} for c in connectors) and not lidl_official_match and not photo_only_match:
            continue

        charge_price_raw = d.get("charge_price")
        connector_costs = {str(c.get("cost") or "").upper() for c in connectors}
        price = price_from_charge_fields(charge_price_raw, d.get("instructions"), d.get("directions"))
        if not charge_price_raw and price == 0 and "PAYMENT" in connector_costs:
            continue
        out_without_working = str(d.get("offline_status") or "").upper() == "NOT_WORKING" and not any(str(c.get("status") or "").upper() in {"AVAILABLE", "WORKING"} for c in connectors)
        photo_only = bool(photo_only_match and not lidl_official_match and (price is None or price > 0.25 or out_without_working))
        if price is None and not lidl_official_match and not photo_only:
            continue
        if price is not None and price > 0.25 and not photo_only:
            continue
        # D-151: connector_label_from_token now returns None for a missing/empty
        # token instead of fabricating "Type 2" — coalesce to an honest
        # "Unknown" label here rather than letting None reach sorted()/JSON,
        # or (worse) silently re-introducing the old Tesla-ok-by-default bug.
        labels = sorted({connector_label_from_token(c.get("type")) or "Unknown" for c in connectors}) or ["Unknown"]
        max_kw = None
        last_updates = []
        working = available = charging = 0
        connector_rows: List[Dict[str, Any]] = []
        for c in connectors:
            kw = fnum(c.get("kw"))
            if kw:
                max_kw = max(max_kw or 0, kw)
            status = str(c.get("status") or "").upper()
            if status in {"AVAILABLE", "WORKING"}:
                working += 1
            if status == "AVAILABLE":
                available += 1
            if status == "CHARGING":
                charging += 1
            if c.get("status_updated_at") and not str(c.get("status_updated_at")).startswith("1970-"):
                last_updates.append(c.get("status_updated_at"))
            connector_rows.append(connector_ref(
                connector_label_from_token(c.get("type")) or "Unknown",
                count=1,
                power_kw=kw,
                status=status or "UNKNOWN",
                source_url=d.get("url") or f"https://map.electromaps.com/en/p/{loc_id}",
                connector_id=c.get("id"),
                raw={
                    "visualRef": c.get("visualRef"),
                    "realtime": c.get("realtime"),
                    "format": c.get("format"),
                    "power_type": c.get("power_type"),
                    "cost": c.get("cost"),
                    "authenticationRemote": c.get("authenticationRemote"),
                    "authenticationRfid": c.get("authenticationRfid"),
                    "status_updated_at": c.get("status_updated_at"),
                },
            ))
        loc_type = str(d.get("type") or "").upper()
        parking_kind = "retail_parking" if loc_type in {"SHOP", "MALL", "RESTAURANT", "HOTEL"} else "street_or_municipal" if loc_type == "ON_STREET" else "unknown"
        if loc_type in {"PARKING", "AIRPORT"}:
            parking_kind = "paid_or_metered"
        if lidl_official_match:
            parking_kind = "retail_parking"
        context_kind, context_note = parking_kind_from_context(d.get("name") or loc.get("name") or "", " ".join(str(x or "") for x in [addr.get("address"), addr.get("city")]), str(d.get("parking_price") or ""), "")
        context_rank = {
            "paid_or_metered": 100,
            "time_limited_or_validated": 88,
            "free_parking": 72,
            "retail_parking": 55,
            "facility_or_customer_parking": 54,
            "street_or_municipal": 52,
            "unknown": 0,
        }
        priced_parking_kind = parking_kind_from_price(d.get("parking_price"), parking_kind)
        if priced_parking_kind == "free_parking" and context_kind in {"retail_parking", "facility_or_customer_parking"}:
            parking_kind = context_kind
        elif context_kind != "unknown" and context_rank.get(context_kind, 0) > context_rank.get(parking_kind, 0):
            parking_kind = context_kind
        if lidl_official_match:
            parking_kind = "retail_parking"
        status = "AVAILABLE" if available else "OPEN" if working else "CHARGING" if charging else "VERIFY"
        evidence = "; ".join(
            x for x in [
                f"Electromaps web-map API charge_price={d.get('charge_price')!r}",
                f"parking_price={d.get('parking_price')!r}" if d.get("parking_price") is not None else "",
                f"offline_status={d.get('offline_status')}",
                f"online_status={d.get('online_status')}",
                f"rating={d.get('rating')} comments={d.get('count_comments')}",
                f"instructions={d.get('instructions')}" if d.get("instructions") else "",
            ] if x
        )
        free_condition = (
            "Electromaps web-map detail says the charging price is free."
            if price == 0
            else "Electromaps exact Lidl detail supplies connector/photo evidence; the free-charge claim comes from Lidl official list."
            if lidl_official_match
            else "Electromaps web-map detail reports a tariff at or below €0.25/kWh."
        )
        raw = {
            "electromaps_id": loc_id,
            "updated_at": d.get("updated_at"),
            "last_seen": max(last_updates) if last_updates else d.get("updated_at"),
            "charge_price": d.get("charge_price"),
            "parking_price": d.get("parking_price"),
            "online_status": d.get("online_status"),
            "offline_status": d.get("offline_status"),
            "realtime": d.get("realtime"),
            "connector_count": len(connectors),
            "connector_status_fields": bool(connectors),
            "count_images": d.get("count_images"),
        }
        photos = []
        image = d.get("image") if isinstance(d.get("image"), dict) else {}
        image_url = image.get("l") or image.get("m") or image.get("s")
        if image_url:
            photos.append({
                "url": image_url,
                "thumbnail_url": image.get("s") or image_url,
                "source": "Electromaps public image",
                "source_url": d.get("url") or f"https://map.electromaps.com/en/p/{loc_id}",
                "caption": d.get("name") or "",
                "license": "Electromaps source-published media",
            })
        if photo_only:
            add_record(
                records,
                source="Electromaps public photo",
                source_url=d.get("url") or f"https://map.electromaps.com/en/p/{loc_id}",
                name=d.get("name") or loc.get("name") or "Electromaps charger photo",
                address=addr.get("address") or "",
                city=addr.get("city") or "Valencia province",
                lat=lat,
                lon=lon,
                price_min=None,
                free_condition="Exact same-site Electromaps public photo; charging price claim remains with the matched free source.",
                connector_labels=[],
                max_power_kw=None,
                status="VERIFY",
                reliability="photo-only",
                evidence="Exact same-site Electromaps public photo used only as visual evidence.",
                parking_kind="unknown",
                parking_note="Photo-only directory evidence; parking claim remains with the matched source.",
                raw={"electromaps_id": loc_id, "photo_only": True, "matched_free_id": photo_only_match.get("id") if photo_only_match else None},
                photos=photos,
                source_artifact=detail_artifact,
            )
            continue
        add_record(
            records,
            source="Electromaps web-map API",
            source_url=d.get("url") or f"https://map.electromaps.com/en/p/{loc_id}",
            name=d.get("name") or loc.get("name") or "Electromaps charger",
            address=addr.get("address") or "",
            city=addr.get("city") or "Valencia province",
            lat=lat,
            lon=lon,
            price_min=price,
            free_condition=free_condition,
            connector_labels=labels,
            max_power_kw=max_kw,
            status=status,
            reliability="directory-map-free" if price == 0 else "directory-page" if lidl_official_match else "directory-map-cheap",
            evidence=evidence,
            parking_kind=parking_kind,
            parking_note=(
                "Exact Electromaps Lidl card corroborates connector/photo details; parking remains retail/customer context and onsite signs win."
                if lidl_official_match
                else context_note or "Electromaps directory data; parking_price is shown separately when published, and onsite signs still win."
            ),
            raw=raw,
            connectors=connector_rows,
            photos=photos,
            source_artifact=detail_artifact,
        )


def source_electromaps_photo_second_pass(records: List[Dict[str, Any]]) -> None:
    """Attach cached Electromaps public photos to sources loaded after Electromaps."""
    added = 0
    for path in sorted(CACHE.glob("electromaps_location_*.json")):
        cached_loc_id = re.sub(r"\D+", "", path.stem)
        source_artifact = cache_file_artifact(
            path,
            url=f"https://www.electromaps.com/mapi/v2/locations/{cached_loc_id}",
            cache_name=path.name,
            ttl_hours=48,
        )
        try:
            d = json.loads(source_artifact.decode("utf-8", "replace"))
        except Exception:
            continue
        lat, lon = fnum(d.get("latitude")), fnum(d.get("longitude"))
        photo_match = electromaps_existing_photo_match(records, d, lat, lon)
        if not photo_match:
            continue
        loc_id = d.get("id") or cached_loc_id
        image = d.get("image") if isinstance(d.get("image"), dict) else {}
        image_url = image.get("l") or image.get("m") or image.get("s")
        if not image_url:
            continue
        addr = d.get("address") or {}
        add_record(
            records,
            source="Electromaps public photo",
            source_url=d.get("url") or f"https://map.electromaps.com/en/p/{loc_id}",
            name=d.get("name") or "Electromaps charger photo",
            address=addr.get("address") or "",
            city=addr.get("city") or "Valencia province",
            lat=lat,
            lon=lon,
            price_min=None,
            free_condition="Exact same-site Electromaps public photo; charging price claim remains with the matched free source.",
            connector_labels=[],
            max_power_kw=None,
            status="VERIFY",
            reliability="photo-only",
            evidence="Exact same-site Electromaps public photo used only as visual evidence.",
            parking_kind="unknown",
            parking_note="Photo-only directory evidence; parking claim remains with the matched source.",
            raw={"electromaps_id": loc_id, "photo_only": True, "matched_free_id": photo_match.get("id"), "photo_second_pass": True},
            photos=[{
                "url": image_url,
                "thumbnail_url": image.get("s") or image_url,
                "source": "Electromaps public image",
                "source_url": d.get("url") or f"https://map.electromaps.com/en/p/{loc_id}",
                "caption": d.get("name") or "",
                "license": "Electromaps source-published media",
            }],
            source_artifact=source_artifact,
        )
        added += 1
def source_chargemap_mappy(records: List[Dict[str, Any]]) -> None:
    headers = {
        "Accept": "application/json",
        "Referer": "https://chargemap.com/en-gb/map",
        "User-Agent": UA,
    }
    visited = set()
    pools: Dict[int, Dict[str, Any]] = {}
    pool_artifacts: Dict[int, FetchArtifact] = {}
    max_boxes = int(os.environ.get("VOLTA_CHARGEMAP_BOX_LIMIT", "260"))
    box_count = 0

    def box_key(n: float, w: float, s: float, e: float) -> str:
        return f"{n:.5f}_{w:.5f}_{s:.5f}_{e:.5f}".replace("-", "m").replace(".", "p")

    def fetch_box(n: float, w: float, s: float, e: float) -> Tuple[List[Dict[str, Any]], Optional[FetchArtifact]]:
        query = urllib.parse.urlencode({"NW": f"{n};{w}", "SE": f"{s};{e}"})
        url = f"https://map.chargemap.com/mappy/charging_pools.json?{query}"
        result = cached_json(
            url,
            f"chargemap_mappy_{box_key(n, w, s, e)}.json",
            ttl_hours=24,
            headers=headers,
            timeout=40,
            with_artifact=True,
        )
        data, source_artifact = unpack_json_artifact(result)
        items = ((data.get("response") or {}).get("content") or {}).get("items") or []
        return items, source_artifact

    def walk(n: float, w: float, s: float, e: float, depth: int = 0) -> None:
        nonlocal box_count
        key = (round(n, 5), round(w, 5), round(s, 5), round(e, 5))
        if key in visited or box_count >= max_boxes:
            return
        visited.add(key)
        box_count += 1
        try:
            items, source_artifact = fetch_box(n, w, s, e)
        except Exception:
            return
        has_cluster = False
        for item in items:
            pool = item.get("pool")
            if pool and pool.get("id"):
                pool_id = int(pool["id"])
                pools[pool_id] = pool
                if source_artifact is not None:
                    pool_artifacts[pool_id] = source_artifact
            elif item.get("type") == "cluster":
                has_cluster = True
        if has_cluster and depth < 7 and abs(n - s) > 0.01 and abs(e - w) > 0.01:
            mid_lat = (n + s) / 2
            mid_lng = (w + e) / 2
            walk(n, w, mid_lat, mid_lng, depth + 1)
            walk(n, mid_lng, mid_lat, e, depth + 1)
            walk(mid_lat, w, s, mid_lng, depth + 1)
            walk(mid_lat, mid_lng, s, e, depth + 1)

    walk(39.74, VALENCIA_BBOX[1], 38.85, VALENCIA_BBOX[3])

    for pool in pools.values():
        coords = pool.get("gps_coordinates") or pool.get("coordinates") or {}
        lat, lon = fnum(coords.get("lat")), fnum(coords.get("lon"))
        if not likely_valencia_province(lat, lon, {"postal_code": pool.get("postal_code"), "name": pool.get("name"), "addr:city": pool.get("city")}):
            continue
        price = 0.0 if pool.get("is_free") else None
        if price is None:
            continue
        connectors = pool.get("charging_connectors") or []
        if not chargemap_has_power_evidence(pool):
            continue
        labels = []
        max_kw = None
        connector_rows: List[Dict[str, Any]] = []
        for c in connectors:
            ctype = c.get("connector_type") or c.get("connectorType") or {}
            # D-151: connector_label_from_token returns None (not a fabricated
            # "Type 2") for a token it can't place — coalesce to "Unknown" so
            # this never claims Tesla compatibility, and never puts a bare
            # None into labels/connector_ref (sorted()/JSON would choke on it).
            label = connector_label_from_token(ctype.get("name") or ctype.get("label") or ctype.get("slug") or ctype.get("id") or c.get("type")) or "Unknown"
            labels.append(label)
            power = chargemap_connector_power(c)
            if power:
                max_kw = max(max_kw or 0, power)
            available_count = count_int(c.get("available_count"), default=0)
            total_count = count_int(c.get("count"), default=1)
            status_for_group = "AVAILABLE" if available_count > 0 else str(pool.get("availability_status") or "UNKNOWN").upper()
            connector_rows.append(connector_ref(label, count=total_count, power_kw=power, status=status_for_group, source_url=f"https://chargemap.com/{pool.get('slug') or pool.get('id')}.html", connector_id=ctype.get("id") or c.get("type"), raw={"available_count": c.get("available_count"), "evse_ids": c.get("evse_ids")}))
        if not labels:
            speed_id = ((pool.get("speed") or {}).get("id"))
            labels = ["CCS2", "Type 2"] if speed_id and int(speed_id) >= 3 else ["Type 2"]
        if not connector_rows:
            evse_count = len(pool.get("evses") or []) or None
            connector_rows.append(connector_ref(labels[0], count=evse_count or 1, power_kw=max_kw, status=str(pool.get("availability_status") or "UNKNOWN").upper(), source_url=f"https://chargemap.com/{pool.get('slug') or pool.get('id')}.html", connector_id="pool-summary", raw={"basis": "Chargemap pool summary", "evses": evse_count, "speed": pool.get("speed")}))
        network = pool.get("network") or {}
        operational_status = str(pool.get("operational_status") or "").upper()
        availability_status = str(pool.get("availability_status") or "").upper()
        pool_name = str(pool.get("name") or "")
        if BAD_STATION_NAME_RE.search(pool_name):
            continue
        slug_value = str(pool.get("slug") or pool.get("id") or "")
        if slug_value.lower() in DEAD_CHARGEMAP_SLUGS:
            continue
        if operational_status and operational_status not in {"OPERATIONAL", "UNKNOWN"}:
            continue
        if availability_status == "UNAVAILABLE":
            continue
        status = "AVAILABLE" if availability_status == "AVAILABLE" else "VERIFY"
        evidence = f"Chargemap public map pool is_free={pool.get('is_free')}; network={network.get('name')}; operational_status={pool.get('operational_status')}; availability_status={pool.get('availability_status')}; rating={pool.get('rating')}; last_used={pool.get('last_used_time')}"
        address_text = " ".join(str(x) for x in [pool.get("street_name"), pool.get("number"), pool.get("postal_code"), pool.get("location_type_slug")] if x)
        parking_kind, parking_context_note = parking_kind_from_context(pool.get("name") or "", address_text, "", "")
        if parking_kind == "unknown":
            parking_kind = "retail_parking" if str(pool.get("location_type_slug") or "").lower() in {"shop", "mall", "restaurant", "hotel"} else "street_or_municipal" if str(pool.get("location_type_slug") or "").lower() in {"street", "on-street"} else "unknown"
            parking_context_note = "Chargemap location type informs parking context; verify access/parking signs onsite before relying on the bay." if parking_kind != "unknown" else ""
        photos = []
        for key, label in (("cover_url", "Chargemap cover image"), ("avatar_url", "Chargemap avatar image")):
            if pool.get(key):
                photos.append({
                    "url": pool.get(key),
                    "thumbnail_url": pool.get(key),
                    "source": label,
                    "source_url": f"https://chargemap.com/{slug_value}.html",
                    "caption": pool.get("name") or "",
                    "license": "Chargemap source-published media",
                })
        add_record(
            records,
            source="Chargemap public map API",
            source_url=f"https://chargemap.com/{slug_value}.html",
            name=pool.get("name") or "Chargemap free charger",
            address=" ".join(str(x) for x in [pool.get("street_name"), pool.get("number"), pool.get("postal_code")] if x),
            city=pool.get("city") or "Valencia province",
            lat=lat,
            lon=lon,
            price_min=price,
            free_condition="Chargemap public map marks this charging pool as free.",
            connector_labels=sorted(set(labels)) or ["Type 2"],
            max_power_kw=max_kw,
            status=status,
            reliability="directory-map-free",
            evidence=evidence,
            parking_kind=parking_kind,
            parking_note=parking_context_note or "Chargemap public map evidence; verify access/parking signs onsite before relying on the bay.",
            raw={"chargemap_id": pool.get("id"), "slug": pool.get("slug"), "operational_status": pool.get("operational_status"), "availability_status": pool.get("availability_status"), "real_time_available": pool.get("real_time_available"), "can_remote_start_charge": pool.get("can_remote_start_charge"), "can_start_plug_and_charge": pool.get("can_start_plug_and_charge"), "can_start_auto_charge": pool.get("can_start_auto_charge"), "last_seen": pool.get("last_used_time"), "creation_date": ((pool.get("statistic") or {}).get("creation_date"))},
            connectors=connector_rows,
            photos=photos,
            source_artifact=pool_artifacts.get(int(pool["id"])) if pool.get("id") else None,
        )


RELIABILITY_RANK = {"official-cheap": 100, "official-free": 100, "official-free-policy": 95, "official-charger-location-price-unknown": 70, "directory-map-free": 68, "community-free": 65, "directory-map-cheap": 63, "community-cost": 60, "directory-page": 55, "community-access-limited": 45, "photo-only": 5}
PARKING_KIND_RANK = {"paid_or_metered": 100, "time_limited_or_validated": 88, "free_parking": 72, "retail_parking": 55, "facility_or_customer_parking": 54, "street_or_municipal": 52, "unknown": 0}


def oldest_contributing_source_timestamp(items: Iterable[Dict[str, Any]]) -> Optional[str]:
    """Return the oldest explicit valid fetch timestamp represented by merged evidence.

    A merged row can adopt claims, hardware, parking, media, or corroboration from every
    contributing item. Its record-level freshness must therefore be no newer than the
    oldest such evidence. Existing per-source timestamps participate when present and
    remain on their source entries; missing/invalid timestamps are ignored, never made up.
    """
    candidates: List[Tuple[datetime, str]] = []
    for item in items:
        values = [item.get("source_fetched_at")]
        for source in item.get("sources") or []:
            if isinstance(source, dict):
                values.extend((source.get("source_fetched_at"), source.get("fetched_at")))
        for value in values:
            if not isinstance(value, str) or not value.strip():
                continue
            text = value.strip()
            try:
                parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    continue
                normalized = parsed.astimezone(timezone.utc)
            except (ValueError, OverflowError):
                continue
            candidates.append((normalized, text))
    return min(candidates, key=lambda candidate: candidate[0])[1] if candidates else None


def merge_connector_rows(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen = set()
    for item in items:
        for connector in item.get("connectors") or []:
            if not isinstance(connector, dict):
                continue
            key = (
                connector.get("connector_id") or "",
                connector.get("label") or connector.get("type") or "",
                connector.get("power_kw"),
                connector.get("source_url") or item.get("source_url") or "",
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(connector)
    return merged


def connector_inventory_items(items: Iterable[Dict[str, Any]], base: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Choose the connector inventory to show after cross-source merge.

    Cross-source corroboration should not inflate port counts: if the primary source
    already publishes exact connector rows, use that source family's inventory. If
    the primary is an official locator with no exact connector inventory (for example
    Lidl/Consum), adopt the most reliable corroborating exact connector inventory
    instead of summing multiple source families as extra physical ports.
    """
    item_list = list(items)
    candidates = [item for item in item_list if item.get("connectors")]
    if not candidates:
        return item_list
    candidates.sort(
        key=lambda item: (
            1 if (item.get("raw") or {}).get("official_hardware_inventory") else 0,
            1 if any(fnum(connector.get("power_kw")) is not None for connector in item.get("connectors") or []) else 0,
            RELIABILITY_RANK.get(item.get("reliability"), 50),
            1 if record_source_family(item) == record_source_family(base) else 0,
            sum(count_int(connector.get("count")) for connector in item.get("connectors") or []),
        ),
        reverse=True,
    )
    chosen_family = record_source_family(candidates[0])
    return [item for item in candidates if record_source_family(item) == chosen_family]


def preserved_location_claim(items: Iterable[Dict[str, Any]], base: Dict[str, Any]) -> Dict[str, Any]:
    for item in items:
        if (
            (item.get("raw") or {}).get("preserve_location_claim")
            and isinstance(item.get("location_claim"), dict)
        ):
            return dict(item["location_claim"])
    return location_claim_for(base)


def price_evidence_is_current_for_official_lead(
    base: Dict[str, Any],
    candidate: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> bool:
    """Do not turn a current official price-unknown row into a cheap lead from stale OCM data."""
    if base.get("reliability") != "official-charger-location-price-unknown":
        return True
    if record_source_family(candidate) != "openchargemap":
        return True
    timestamp = raw_last_seen(candidate.get("raw") or {})
    if timestamp is None:
        for source in candidate.get("sources") or []:
            if isinstance(source, dict):
                timestamp = raw_timestamp_value(source.get("source_last_updated"))
                if timestamp:
                    break
    observed = _source_datetime(timestamp)
    if observed is None:
        return False
    checked = now or datetime.now(timezone.utc)
    if checked.tzinfo is None or checked.utcoffset() is None:
        checked = checked.replace(tzinfo=timezone.utc)
    age_seconds = (checked.astimezone(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
    return 0 <= age_seconds <= COMMUNITY_PRICE_EVIDENCE_MAX_AGE_DAYS * 24 * 60 * 60


def explicit_spanish_postcode(item: Dict[str, Any]) -> str:
    raw = item.get("raw") or {}
    text = " ".join(
        str(value or "")
        for value in (
            item.get("name"),
            item.get("address"),
            item.get("city"),
            raw.get("official_query"),
            raw.get("addr:postcode"),
        )
    )
    match = re.search(r"\b([0-5]\d{4})\b", text)
    return match.group(1) if match else ""


def reject_suspicious_openchargemap_coordinate_clusters(
    records: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Quarantine OCM rows whose exact coordinate claims incompatible Spanish places."""
    record_list = list(records)
    rejected = {
        id(item)
        for item in record_list
        if record_source_family(item) == "openchargemap"
        and str((item.get("raw") or {}).get("ID")) in OPENCHARGEMAP_QUARANTINED_IDS
    }
    groups: Dict[Tuple[int, int], List[Dict[str, Any]]] = defaultdict(list)
    for item in record_list:
        if record_source_family(item) != "openchargemap":
            continue
        # Match dedupe()'s exact-coordinate bucket. OCM rows can differ by a few
        # metres because of source rounding and still collapse into one exported pin.
        groups[(round(float(item["lat"]) * 10_000), round(float(item["lon"]) * 10_000))].append(item)
    for items in groups.values():
        if len(items) < 2:
            continue
        item_postcodes = [explicit_spanish_postcode(item) for item in items]
        postcodes = set(item_postcodes)
        postcodes.discard("")
        if len(postcodes) > 1:
            rejected.update(id(item) for item in items)
            continue
        if len(postcodes) == 1 and all(item_postcodes):
            # One postcode on every row is stronger place evidence than OCM's free-text
            # Town field, which often holds a street number or road marker ("Km 124").
            continue
        localities = {
            normalized_identity_text(item.get("city"))
            for item in items
            if str(item.get("city") or "").strip() and not re.search(r"\d", str(item.get("city")))
        }
        if len(localities) > 1:
            rejected.update(id(item) for item in items)
    return [item for item in record_list if id(item) not in rejected]


def connector_labels_from_rows(connectors: Iterable[Dict[str, Any]]) -> List[str]:
    labels: List[str] = []
    for connector in connectors:
        if not isinstance(connector, dict):
            continue
        label = connector.get("label") or connector.get("type")
        label_text = str(label or "").strip()
        if label_text and label_text.lower() not in UNKNOWN_CONNECTOR_LABELS and label_text not in labels:
            labels.append(label_text)
    return labels


def merge_media(items: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    photos: List[Dict[str, Any]] = []
    seen = set()
    for item in items:
        for photo in ((item.get("media") or {}).get("photos") or []):
            if not isinstance(photo, dict):
                continue
            url = photo.get("url")
            if not url or url in seen:
                continue
            seen.add(url)
            photos.append(photo)
    return {
        "photos": photos[:4],
        "photo_status": "public_photo_available" if photos else "no_public_photo",
        "note": "Photo comes from a public source page/API and may be stale; onsite view wins." if photos else "No public photo found in the currently ingested source payload.",
    }


def record_merged_ids(base: Dict[str, Any], items: Iterable[Dict[str, Any]]) -> None:
    """Keep the ids of rows folded into this pin, so a refresh can show where they went."""
    items = list(items)
    base["merged_source_families"] = sorted(set().union(record_source_families(base), *(record_source_families(item) for item in items)))
    # Split ancestry is identity evidence, not a claim that all split stations are
    # aliases of this pin. Keep it even when another source wins a later merge.
    collision_ids = set(base.get("collision_base_ids") or [])
    for item in items:
        collision_ids.update(item.get("collision_base_ids") or [])
        if item.get("collision_base_id"):
            collision_ids.add(item["collision_base_id"])
    if collision_ids:
        base["collision_base_ids"] = sorted(collision_ids)
    merged_ids = set(base.get("merged_ids") or [])
    for item in items:
        merged_ids.update(item.get("merged_ids") or [])
        if item.get("id") and item["id"] != base.get("id"):
            merged_ids.add(item["id"])
    merged_ids.discard(base.get("id"))
    if merged_ids:
        base["merged_ids"] = sorted(merged_ids)
    else:
        base.pop("merged_ids", None)
    # Keep the absorbed rows' stable source identifiers (NAP site id, Feníe code, OSM/OCM id...)
    # on the pin. Without them a row folded into a pin from another source family shares no
    # identifier with the pin, and the refresh's zero-missing-ID gate cannot prove the merge.
    own = identity_keys({k: v for k, v in base.items() if k != "merged_source_identifiers"})
    carried = {tuple(k) for k in base.get("merged_source_identifiers") or []}
    for item in items:
        if item is base or item.get("id") == base.get("id"):
            continue
        carried.update(tuple(k) for k in item.get("merged_source_identifiers") or [])
        carried.update(identity_keys(item))
    carried -= own
    if carried:
        base["merged_source_identifiers"] = sorted(list(k) for k in carried)
    else:
        base.pop("merged_source_identifiers", None)
    # A source-reported outage on any merged row stays on the pin (fail closed).
    if not base.get("source_reported_unavailable"):
        for item in items:
            if item.get("source_reported_unavailable"):
                base["source_reported_unavailable"] = item["source_reported_unavailable"]
                break


def dedupe(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    records = reject_suspicious_openchargemap_coordinate_clusters(records)
    # Name + rounded location IDs can collide for distinct upstream stations. Only
    # stable source identifiers may disambiguate them; never claim the ambiguous ID
    # as a merged alias on several pins. Historical IDs are reconciled after build.
    id_keys: Dict[str, set] = defaultdict(set)
    for row in records:
        id_keys[row["id"]].add(tuple(sorted(primary_identity_keys(row))))
    unique_records = []
    for row in records:
        keys = tuple(sorted(primary_identity_keys(row)))
        if len(id_keys[row["id"]]) > 1 and keys:
            digest = hashlib.sha256(json.dumps(keys, separators=(",", ":")).encode()).hexdigest()[:16]
            row = dict(row, id=f"{row['id']}_station_{digest}", collision_base_id=row["id"])
        unique_records.append(row)
    records = unique_records
    groups: Dict[Tuple[int, int], List[Dict[str, Any]]] = defaultdict(list)
    for r in records:
        groups[(round(r["lat"] * 10000), round(r["lon"] * 10000))].append(r)
    merged = []
    rank = RELIABILITY_RANK
    parking_rank = PARKING_KIND_RANK
    # A rounded coordinate is a search bucket, not a station identity. Partition each
    # bucket before merging, retaining every absorbed family for the next build stage.
    station_groups: List[List[Dict[str, Any]]] = []
    for items in groups.values():
        clusters: List[List[Dict[str, Any]]] = []
        for item in items:
            incoming = record_source_families(item)
            for cluster in clusters:
                # History may retain identity, but cannot donate facts to an active pin.
                if any(directory_history_only(row) != directory_history_only(item) for row in cluster):
                    continue
                existing = set().union(*(record_source_families(row) for row in cluster))
                if not (incoming & existing) or (
                    incoming == existing and len(incoming) == 1
                    and all(same_stable_station(item, row) for row in cluster)
                ):
                    cluster.append(item)
                    break
            else:
                clusters.append([item])
        station_groups.extend(clusters)
    for items in station_groups:
        items.sort(key=lambda r: (rank.get(r.get("reliability"), 50), 1 if r.get("is_free") else 0, -len(r.get("sources", []))), reverse=True)
        base = dict(items[0])
        sources = []
        for it in items:
            sources.extend(it.get("sources", []))
        merge_charge_price(items, base, prefer_lower=True)
        best_parking = max(items, key=lambda it: parking_rank.get(((it.get("parking") or {}).get("kind") or "unknown"), 0))
        base_kind = ((base.get("parking") or {}).get("kind") or "unknown")
        best_kind = ((best_parking.get("parking") or {}).get("kind") or "unknown")
        if parking_rank.get(best_kind, 0) > parking_rank.get(base_kind, 0):
            base["parking"] = best_parking.get("parking")
            base["parking_claim"] = best_parking.get("parking_claim")
        base["sources"] = sources[:8]
        base["source_count"] = len(sources)
        record_merged_ids(base, items)
        inventory_items = connector_inventory_items(items, base)
        merged_connectors = merge_connector_rows(inventory_items)
        if merged_connectors:
            base["connectors"] = merged_connectors
            labels = connector_labels_from_rows(merged_connectors)
            base["connector_labels"] = labels
            base["connector_count"] = sum(count_int(c.get("count")) for c in merged_connectors)
            base["total_evse"] = base["connector_count"]
            base["tesla_ok"] = tesla_compat(labels)
            connector_powers = [fnum(c.get("power_kw")) for c in merged_connectors]
            connector_powers = [p for p in connector_powers if p is not None]
            inventory_powers = [fnum(item.get("max_power_kw")) for item in inventory_items]
            inventory_powers = [power for power in inventory_powers if power is not None]
            base["max_power_kw"] = max(inventory_powers or connector_powers) if (inventory_powers or connector_powers) else None
            base["fast"] = bool(base.get("max_power_kw") and base["max_power_kw"] >= 43)
        base["source_integration"] = merge_source_integration(items, base)
        base["media"] = merge_media(items)
        has_restricted_free = (
            any(((item.get("access") or {}).get("level") == "restricted") for item in items)
            or any(ACCESS_LIMIT_RE.search(s.get("evidence") or "") for s in sources)
        )
        base["is_free"] = base.get("price_min") == 0 and not has_restricted_free and not base.get("source_reported_unavailable")
        base["is_cheap"] = base.get("price_min") is not None and base["price_min"] <= 0.25 and not base.get("source_reported_unavailable")
        base["price_claim_type"] = price_claim_type_for(base.get("price_min"))
        if has_restricted_free and base.get("price_min") == 0:
            base["price_claim_type"] = "access_limited_free"
            if (base.get("access") or {}).get("level") != "restricted":
                base["access"] = {"level": "restricted", "label": "Access caveat", "note": "A source reports free charging but also access restrictions."}
        charge_url = str(((base.get("charge_claim") or {}).get("url") or "")).rstrip("/")
        parking_url = str(((base.get("parking_claim") or {}).get("url") or "")).rstrip("/")
        parking_kind = ((base.get("parking") or {}).get("kind") or "unknown")
        charge_conflict = charge_price_conflict(items, base)
        if charge_conflict:
            base["claim_conflict"] = charge_conflict
        elif charge_url and parking_url and charge_url != parking_url and parking_kind in {"paid_or_metered", "time_limited_or_validated"}:
            base["claim_conflict"] = {
                "level": "mixed_sources",
                "summary": "Charging price and parking condition come from different public sources; verify both before routing.",
                "charge_url": charge_url,
                "parking_url": parking_url,
            }
        else:
            base.pop("claim_conflict", None)
        base["primary_source"] = primary_source_for(base)
        base["location_claim"] = preserved_location_claim(items, base)
        base["hardware_claim"] = hardware_claim_for(base)
        base["access_claim"] = access_claim_for(base)
        base["confidence"] = min(99, rank.get(base.get("reliability"), 50) + min(15, len(sources) * 3) - (15 if has_restricted_free else 0))
        merged_fetched_at = oldest_contributing_source_timestamp(items)
        if merged_fetched_at is not None:
            base["source_fetched_at"] = merged_fetched_at
        merged.append(base)
    merged.sort(key=lambda r: (0 if r.get("is_free") else 1, -(r.get("confidence") or 0), r.get("price_min") if r.get("price_min") is not None else 999))
    return merged


def record_source_family(r: Dict[str, Any]) -> str:
    """Independent-source family for a record (mirrors server.py source_family semantics)."""
    blob = " ".join(str(x or "") for x in [r.get("source"), r.get("source_url")]).lower()
    for family, needles in [
        ("openchargemap", ("openchargemap",)),
        ("electromaps", ("electromaps",)),
        ("osm", ("openstreetmap", "overpass")),
        ("consum", ("consum",)),
        ("chargemap", ("chargemap",)),
        ("opendata", ("opendata", "valencia.es", "geoportal")),
        ("lidl", ("lidl",)),
    ]:
        if any(n in blob for n in needles):
            return family
    return blob[:16]


def record_source_families(r: Dict[str, Any]) -> set:
    """Families of all station rows in a pin, independent of display-truncated sources."""
    families = {record_source_family(r), *(r.get("merged_source_families") or [])}
    for source in r.get("sources") or []:
        if isinstance(source, dict) and (source.get("name") or source.get("url")):
            families.add(record_source_family({"source": source.get("name"), "source_url": source.get("url")}))
    return families


def same_stable_station(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Duplicate copies need identical stable keys, not just a shared name or coordinate."""
    a_keys, b_keys = identity_keys(a), identity_keys(b)
    if a_keys or b_keys:
        return bool(a_keys) and a_keys == b_keys
    return bool(a.get("id")) and a.get("id") == b.get("id")


def _dist_km(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    dlat = (a["lat"] - b["lat"]) * 111.32
    dlon = (a["lon"] - b["lon"]) * 111.32 * math.cos(math.radians(a["lat"]))
    return (dlat * dlat + dlon * dlon) ** 0.5


KNOWN_SITE_BRANDS = ("lidl", "mercadona", "carrefour", "bauhaus", "consum", "ikea", "aldi", "decathlon")
GENERIC_PLACE_TOKENS = {
    "avenida", "avinguda", "avda", "calle", "carrer", "carretera", "cierva", "ciudad",
    "charger", "charging", "comunidad", "destination", "espana", "espagne", "francesc", "free",
    "juan", "local", "maestro", "parque", "plaza", "provincia", "rodrigo", "ronda", "spain",
    "street", "supercharger", "tesla", "valencia", "valenciana", "wallbox",
}


def station_identity_brands(r: Dict[str, Any]) -> set:
    raw = r.get("raw") or {}
    text = " ".join(str(x or "") for x in [r.get("name"), raw.get("brand"), raw.get("operator"), r.get("address")]).lower()
    return {brand for brand in KNOWN_SITE_BRANDS if re.search(rf"\b{re.escape(brand)}\b", text, re.I)}


def normalized_identity_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def station_identity_place_tokens(r: Dict[str, Any]) -> set:
    raw = r.get("raw") or {}
    text = normalized_identity_text(" ".join(str(x or "") for x in [
        r.get("name"),
        r.get("address"),
        r.get("city"),
        raw.get("official_query"),
        raw.get("operator"),
    ]))
    tokens = set()
    for token in re.findall(r"[a-z0-9]{3,}", text):
        if token in KNOWN_SITE_BRANDS or token in GENERIC_PLACE_TOKENS or token.isdigit():
            continue
        tokens.add(token)
    return tokens


def record_postcode(r: Dict[str, Any]) -> str:
    return explicit_spanish_postcode(r)


def station_direction_tokens(r: Dict[str, Any]) -> set:
    raw = r.get("raw") or {}
    text = normalized_identity_text(" ".join(str(x or "") for x in [r.get("name"), r.get("address"), raw.get("official_query")]))
    tokens = set(re.findall(r"[a-z0-9]{3,}", text))
    directions = set()
    if tokens & {"north", "northbound", "norte"}:
        directions.add("north")
    if tokens & {"south", "southbound", "sur"}:
        directions.add("south")
    if tokens & {"east", "eastbound", "este"}:
        directions.add("east")
    if tokens & {"west", "westbound", "oeste"}:
        directions.add("west")
    return directions


def strong_station_identity_match(a: Dict[str, Any], b: Dict[str, Any], *, distance_km: Optional[float] = None) -> bool:
    a_brands = station_identity_brands(a)
    b_brands = station_identity_brands(b)
    brands = a_brands & b_brands
    if not brands:
        if a_brands or b_brands:
            return False
        if distance_km is None or distance_km > 0.08:
            return False
        if station_direction_tokens(a) and station_direction_tokens(b) and station_direction_tokens(a) != station_direction_tokens(b):
            return False
        return len(station_identity_place_tokens(a) & station_identity_place_tokens(b)) >= 2
    if distance_km is not None and distance_km <= 0.07:
        return True
    return bool(station_identity_place_tokens(a) & station_identity_place_tokens(b))


CORROBORATION_SAME_POINT_KM = 0.015


def brand_conflict(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    a_brands, b_brands = station_identity_brands(a), station_identity_brands(b)
    return bool(a_brands and b_brands and not (a_brands & b_brands))


def corroboration_only_identity_match(a: Dict[str, Any], b: Dict[str, Any], distance_km: float) -> bool:
    """Require address identity before an official price-unknown row can corroborate a lead."""
    if distance_km > 0.08:
        return False
    official = a if (a.get("raw") or {}).get("corroboration_only") else b
    candidate = b if official is a else a
    official_text = normalized_identity_text(" ".join(str(value or "") for value in [official.get("address"), official.get("name")]))
    candidate_text = normalized_identity_text(" ".join(str(value or "") for value in [candidate.get("address"), candidate.get("name")]))
    if not official_text.strip() or not candidate_text.strip():
        return False
    official_tokens = set(re.findall(r"[a-z0-9]{2,}", official_text))
    candidate_tokens = set(re.findall(r"[a-z0-9]{2,}", candidate_text))
    # The town name and short particles ("puerto de la cruz") say nothing about which
    # charger in that town a row describes.
    town_tokens = set(re.findall(r"[a-z0-9]{2,}", normalized_identity_text(f"{official.get('city') or ''} {candidate.get('city') or ''}")))
    shared = (official_tokens & candidate_tokens) - town_tokens
    shared_numbers = {token for token in shared if token.isdigit()}
    shared_words = {
        token for token in shared
        if not token.isdigit() and len(token) >= 3
        and token not in GENERIC_PLACE_TOKENS and token not in {"puntos", "recarga", "public", "charger"}
    }
    return bool((shared_numbers and shared_words) or len(shared_words) >= 2)


def loose_lidl_locator_match(records: List[Dict[str, Any]], official: Dict[str, Any], candidate: Dict[str, Any], distance_km: float) -> bool:
    """Allow Electromaps to enrich a Lidl locator row when the official coordinate is street-level."""
    raw = official.get("raw") or {}
    if raw.get("coordinate_source") != "nominatim_street":
        return False
    if distance_km > 0.25:
        return False
    if not (station_identity_brands(official) & station_identity_brands(candidate) & {"lidl"}):
        return False
    if not record_postcode(official) or record_postcode(official) != record_postcode(candidate):
        return False
    nearby_same_postcode = [
        r for r in records
        if r.get("source") == "Lidl official free list"
        and record_postcode(r) == record_postcode(official)
        and _dist_km(r, candidate) <= 0.35
    ]
    return len(nearby_same_postcode) == 1


def charge_price_candidates(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Retain structured tariffs, not prices guessed from source-evidence prose.

    A merged row's identity/raw data can belong to a different source than its price.
    Keep each tariff's own provenance and display fields so subsequent merges can
    reapply the official-lead age guard and cannot resurrect a discarded cheap claim.
    """
    candidates: List[Dict[str, Any]] = []
    seen = set()
    for item in items:
        preserved = item.get("charge_price_claims") or []
        legacy = []
        for claim in (item.get("claim_conflict") or {}).get("charge_prices") or []:
            if isinstance(claim, dict):
                legacy.append({
                    "price_min": claim.get("price_min"),
                    "charge_claim": dict(claim),
                    "free_condition": claim.get("summary") or "",
                    "sources": item.get("sources") or [],
                })
        # Prefer complete preserved fields over the current projection or a legacy
        # conflict's compact claim. Deduplicate by the same (URL, price) identity.
        for candidate in [*preserved, item, *legacy]:
            price = fnum(candidate.get("price_min"))
            if price is None:
                continue
            claim = candidate.get("charge_claim") or {}
            source = claim.get("source") or candidate.get("source")
            url = str(claim.get("url") or candidate.get("source_url") or "")
            key = (url.rstrip("/"), price)
            if key in seen:
                continue
            seen.add(key)
            tariff = {
                "price_min": price,
                "source": source,
                "source_url": url,
                "charge_claim": dict(claim) if claim else None,
                "free_condition": candidate.get("free_condition") or "",
            }
            if candidate.get("pricing"):
                tariff["pricing"] = list(candidate["pricing"])
            timestamp = None
            if record_source_family(candidate) == record_source_family(tariff):
                timestamp = raw_last_seen(candidate.get("raw") or {})
            if timestamp is None:
                for evidence in candidate.get("sources") or []:
                    if str(evidence.get("url") or "").rstrip("/") == url.rstrip("/"):
                        timestamp = raw_timestamp_value(evidence.get("source_last_updated"))
                        if timestamp:
                            break
            if timestamp:
                tariff["raw"] = {"last_seen": timestamp}
            candidates.append(tariff)
    return candidates


def merge_charge_price(items: List[Dict[str, Any]], base: Dict[str, Any], *, prefer_lower: bool = False) -> None:
    """Select a tariff atomically in both merge stages and retain its alternatives."""
    candidates = charge_price_candidates(items)
    if not candidates:
        return
    base["charge_price_claims"] = candidates
    eligible = [it for it in candidates if price_evidence_is_current_for_official_lead(base, it)]
    if not eligible:
        return
    if charge_price_conflict(eligible, base):
        chosen = max(eligible, key=lambda it: it["price_min"])
    elif prefer_lower:
        chosen = min(eligible, key=lambda it: it["price_min"])
    else:
        # The candidates follow reliability-sorted input order; preserve the primary
        # tariff when there is no disagreement, or adopt the first eligible tariff.
        primary_url = str((base.get("charge_claim") or {}).get("url") or base.get("source_url") or "").rstrip("/")
        chosen = next((it for it in eligible if it["price_min"] == base.get("price_min")
                       and it["source_url"].rstrip("/") == primary_url), eligible[0])
    base["price_min"] = chosen["price_min"]
    base["charge_claim"] = chosen["charge_claim"]
    base["free_condition"] = chosen["free_condition"]
    if chosen.get("pricing"):
        base["pricing"] = list(chosen["pricing"])
    else:
        base.pop("pricing", None)


def charge_price_conflict(items: Iterable[Dict[str, Any]], base: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    claims: List[Dict[str, Any]] = []
    seen = set()
    for item in charge_price_candidates(items):
        claim = item.get("charge_claim") or {}
        price = fnum(claim.get("price_min"))
        if price is None:
            price = fnum(item.get("price_min"))
        url = str(claim.get("url") or item.get("source_url") or "").rstrip("/")
        if price is None or not url:
            continue
        key = (url, price)
        if key in seen:
            continue
        seen.add(key)
        claims.append({
            "price": price,
            "url": url,
            "source": claim.get("source") or item.get("source"),
            "summary": claim.get("summary") or item.get("free_condition") or "",
        })
    if len(claims) < 2:
        return None
    low = min(claims, key=lambda c: c["price"])
    high = max(claims, key=lambda c: c["price"])
    if abs(high["price"] - low["price"]) <= 0.02:
        return None
    base_url = str(((base.get("charge_claim") or {}).get("url") or base.get("source_url") or "")).rstrip("/")
    alternate = next((c for c in claims if c["url"] != base_url), high if high["url"] != base_url else low)
    return {
        "level": "mixed_sources",
        "summary": "Public charging-tariff sources disagree for this same site; verify the provider app/source before routing.",
        "charge_url": base_url or low["url"],
        "alternate_charge_url": alternate["url"],
        "charge_prices": sorted(
            [{"source": c["source"], "url": c["url"], "price_min": c["price"], "summary": c["summary"]} for c in claims],
            key=lambda c: (c["price_min"], c["url"]),
        ),
    }


def merge_cross_source_duplicates(records: List[Dict[str, Any]], *, radius_km: float = 0.04) -> Tuple[List[Dict[str, Any]], int]:
    """Merge the SAME physical charger listed by DIFFERENT source families within ~40 m.

    Accuracy rules (data honesty first):
    - only cross-family pairs merge — the same source listing two nearby points usually
      means two genuinely distinct chargers at one site, so same-family stays untouched;
    - records whose prices disagree by more than €0.02/kWh merge only on a same-site
      identity match within the radius; the merged row shows the HIGHEST published price
      and a claim_conflict listing every price, so a free claim never hides a paid one;
    - the higher-reliability record wins identity/claims; the other's sources are unioned,
      raising the station's corroboration count instead of drawing two pins.
    Returns (merged_records, merge_count).
    """
    grid: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    strong_identity_radius_km = 0.32
    cell = 2000.0  # 1/2000 deg ≈ 55 m of latitude.
    neighbor_span = max(1, math.ceil((strong_identity_radius_km / 111.32) * cell))
    for idx, r in enumerate(records):
        grid[(int(r["lat"] * cell), int(r["lon"] * cell))].append(idx)

    parent = list(range(len(records)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # Same-family rows never merge, including through a third row: one registry listing two
    # nearby sites (for example two operators in one car park) keeps two pins.
    families = [record_source_families(r) for r in records]

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx == ry or families[rx] & families[ry]:
            return
        if directory_history_only(records[rx]) != directory_history_only(records[ry]):
            return
        parent[ry] = rx
        families[rx] |= families[ry]

    edges: List[Tuple[bool, float, int, int]] = []
    for (gx, gy), idxs in grid.items():
        neighbors: List[int] = []
        for dx in range(-neighbor_span, neighbor_span + 1):
            for dy in range(-neighbor_span, neighbor_span + 1):
                neighbors.extend(grid.get((gx + dx, gy + dy), []))
        for i in idxs:
            a = records[i]
            for j in neighbors:
                if j <= i:
                    continue
                b = records[j]
                if record_source_family(a) == record_source_family(b):
                    continue
                distance = _dist_km(a, b)
                corroboration_pair = bool(
                    (a.get("raw") or {}).get("corroboration_only")
                    or (b.get("raw") or {}).get("corroboration_only")
                )
                if corroboration_pair and brand_conflict(a, b):
                    continue
                if corroboration_pair:
                    # Registry sites carry the town name, so the generic place-token match
                    # would pair any two chargers in one town. Require a shared retail
                    # brand or a shared address instead.
                    identity_match = (
                        bool(station_identity_brands(a) & station_identity_brands(b))
                        and strong_station_identity_match(a, b, distance_km=distance)
                    ) or corroboration_only_identity_match(a, b, distance)
                else:
                    identity_match = strong_station_identity_match(a, b, distance_km=distance)
                if corroboration_pair and not identity_match:
                    # A price-unknown registry site is its own station unless it sits on
                    # the other row's coordinates without a conflicting operator brand.
                    # Anything looser could hand a free claim to a different operator's
                    # paid site; anything stricter draws two pins for one charger.
                    if distance > CORROBORATION_SAME_POINT_KM:
                        continue
                    identity_match = True
                if not identity_match:
                    identity_match = loose_lidl_locator_match(records, a, b, distance) or loose_lidl_locator_match(records, b, a, distance)
                pa, pb = a.get("price_min"), b.get("price_min")
                price_conflict = pa is not None and pb is not None and abs(pa - pb) > 0.02
                if price_conflict and not (distance <= radius_km and identity_match):
                    continue  # conflicting price claims need a same-site identity match
                if distance <= radius_km or (
                    distance <= strong_identity_radius_km
                    and identity_match
                ):
                    edges.append((price_conflict, distance, i, j))

    # Agreeing prices and the nearest pairs claim a station first, so a chained listing
    # joins the row it most resembles rather than whichever row was scanned first.
    for _conflict, _distance, i, j in sorted(edges):
        union(i, j)

    clusters: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for idx, r in enumerate(records):
        clusters[find(idx)].append(r)

    merged: List[Dict[str, Any]] = []
    merge_count = 0
    for items in clusters.values():
        if len(items) == 1:
            merged.append(items[0])
            continue
        merge_count += len(items) - 1
        items.sort(key=lambda r: (RELIABILITY_RANK.get(r.get("reliability"), 50), 1 if r.get("is_free") else 0, -len(r.get("sources", []))), reverse=True)
        base = dict(items[0])
        seen_srcs = set()
        srcs: List[Dict[str, Any]] = []
        for it in items:
            for s in it.get("sources", []):
                key = (s.get("name"), s.get("url"))
                if key not in seen_srcs:
                    seen_srcs.add(key)
                    srcs.append(s)
            kw = it.get("max_power_kw")
            if kw is not None and (base.get("max_power_kw") is None or kw > base["max_power_kw"]):
                base["max_power_kw"] = kw
            # More specific parking knowledge wins (same rule as dedupe()).
            it_kind = ((it.get("parking") or {}).get("kind") or "unknown")
            base_kind = ((base.get("parking") or {}).get("kind") or "unknown")
            if PARKING_KIND_RANK.get(it_kind, 0) > PARKING_KIND_RANK.get(base_kind, 0):
                base["parking"] = it.get("parking")
                base["parking_claim"] = it.get("parking_claim") or base.get("parking_claim")
        merge_charge_price(items, base)
        base["sources"] = srcs[:10]
        base["source_count"] = len(base["sources"])
        record_merged_ids(base, items)
        inventory_items = connector_inventory_items(items, base)
        merged_connectors = merge_connector_rows(inventory_items)
        if merged_connectors:
            base["connectors"] = merged_connectors
            labels = connector_labels_from_rows(merged_connectors)
            base["connector_labels"] = labels
            base["connector_count"] = sum(count_int(c.get("count")) for c in merged_connectors)
            base["total_evse"] = base["connector_count"]
            connector_powers = [fnum(c.get("power_kw")) for c in merged_connectors]
            connector_powers = [p for p in connector_powers if p is not None]
            inventory_powers = [fnum(item.get("max_power_kw")) for item in inventory_items]
            inventory_powers = [power for power in inventory_powers if power is not None]
            base["max_power_kw"] = max(inventory_powers or connector_powers) if (inventory_powers or connector_powers) else None
        base["source_integration"] = merge_source_integration(items, base)
        base["media"] = merge_media(items)
        has_restricted_free = any(
            ((item.get("access") or {}).get("level") == "restricted")
            or any(ACCESS_LIMIT_RE.search(str(source.get("evidence") or "")) for source in item.get("sources", []) if isinstance(source, dict))
            for item in items
        )
        base["is_free"] = base.get("price_min") == 0 and not has_restricted_free and not base.get("source_reported_unavailable")
        base["is_cheap"] = base.get("price_min") is not None and base["price_min"] <= 0.25 and not base.get("source_reported_unavailable")
        base["price_claim_type"] = price_claim_type_for(base.get("price_min"))
        if has_restricted_free and base.get("price_min") == 0:
            base["price_claim_type"] = "access_limited_free"
            if (base.get("access") or {}).get("level") != "restricted":
                base["access"] = {"level": "restricted", "label": "Access caveat", "note": "A source reports free charging but also access restrictions."}
        base["fast"] = bool(base.get("max_power_kw") and base["max_power_kw"] >= 43)
        labels = list(base.get("connector_labels") or [])
        base["tesla_ok"] = tesla_compat(labels)
        # Cross-source parking vs charge provenance may now differ — keep the conflict marker honest.
        charge_url = str(((base.get("charge_claim") or {}).get("url") or "")).rstrip("/")
        parking_url = str(((base.get("parking_claim") or {}).get("url") or "")).rstrip("/")
        parking_kind = ((base.get("parking") or {}).get("kind") or "unknown")
        charge_conflict = charge_price_conflict(items, base)
        if charge_conflict:
            base["claim_conflict"] = charge_conflict
        elif charge_url and parking_url and charge_url != parking_url and parking_kind in {"paid_or_metered", "time_limited_or_validated"}:
            base["claim_conflict"] = {
                "level": "mixed_sources",
                "summary": "Charging price and parking condition come from different public sources; verify both before routing.",
                "charge_url": charge_url,
                "parking_url": parking_url,
            }
        else:
            base.pop("claim_conflict", None)
        base["primary_source"] = primary_source_for(base)
        base["location_claim"] = preserved_location_claim(items, base)
        base["hardware_claim"] = hardware_claim_for(base)
        base["access_claim"] = access_claim_for(base)
        base["confidence"] = min(99, RELIABILITY_RANK.get(base.get("reliability"), 50) + min(15, len(srcs) * 3))
        merged_fetched_at = oldest_contributing_source_timestamp(items)
        if merged_fetched_at is not None:
            base["source_fetched_at"] = merged_fetched_at
        merged.append(base)

    merged.sort(key=lambda r: (0 if r.get("is_free") else 1, -(r.get("confidence") or 0), r.get("price_min") if r.get("price_min") is not None else 999))
    return merged, merge_count


def apply_source_policy_exclusions_to_payload(
    payload: Dict[str, Any],
    *,
    preserve_raw_records: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return a policy-clean payload plus an out-of-band removal report.

    The report deliberately stays outside the returned payload so removed IDs cannot be
    mistaken for retained station data or reintroduced by a later merge. Independent
    records may still mention an excluded operator in names/context; only that operator's
    own prohibited provenance/evidence is excluded.
    """
    def metadata_is_excluded(value: Any) -> bool:
        """Match build-level provenance without treating station/operator prose as evidence."""
        normalized = normalize_policy_text(value)
        for exclusion in SOURCE_POLICY_EXCLUSIONS.get("exclusions") or []:
            terms = [normalize_policy_text(term) for term in exclusion.get("source_family_terms") or []]
            hosts = [normalize_policy_text(host) for host in exclusion.get("origin_hosts") or []]
            family = normalize_policy_text(exclusion.get("family") or "")
            family_token = family.split()[0] if family else ""
            phrase_terms = terms + hosts + ([family] if " " in family else [])
            if any(term and term in normalized for term in phrase_terms):
                return True
            if family_token and re.search(
                rf"(?<![a-z0-9]){re.escape(family_token)}(?![a-z0-9])",
                normalized,
            ):
                return True
        return False

    cleaned = dict(payload)
    allowed, excluded = partition_records(payload.get("records") or [], SOURCE_POLICY_EXCLUSIONS)
    cleaned["records"] = allowed
    cleaned["total_records"] = len(allowed)
    cleaned["recordset_sha256"] = recordset_sha256(allowed)
    cleaned["recordset_records"] = len(allowed)
    # A fresh build knows the real pre-dedupe population and keeps it even when the
    # final policy partition removes merged rows. A later deterministic clean cannot
    # reconstruct that source population safely, so it resets a dirty artifact to the
    # retained count unless the caller explicitly identifies a fresh build.
    cleaned["raw_records"] = (
        int(payload.get("raw_records") or len(allowed))
        if preserve_raw_records or not excluded
        else len(allowed)
    )
    cleaned["source_policy_cleaned_at"] = now_iso()
    cleaned["source_policy_excluded_records"] = len(excluded)

    original_fetch = payload.get("fetch_evidence") or {}
    original_artifacts = original_fetch.get("artifacts") if isinstance(original_fetch, dict) else []
    retained_artifacts = [
        artifact for artifact in (original_artifacts or [])
        if isinstance(artifact, dict) and not metadata_is_excluded(artifact)
    ]
    cleaned["fetch_evidence"] = {
        "summary": {
            "total": len(retained_artifacts),
            "network": sum(not bool((artifact.get("cache") or {}).get("hit")) for artifact in retained_artifacts),
            "cache_hits": sum(bool((artifact.get("cache") or {}).get("hit")) for artifact in retained_artifacts),
            "fallbacks": sum(bool((artifact.get("cache") or {}).get("fallback")) for artifact in retained_artifacts),
            "stale": sum(bool((artifact.get("cache") or {}).get("stale")) for artifact in retained_artifacts),
            "stale_fallbacks": sum(
                bool((artifact.get("cache") or {}).get("fallback"))
                and bool((artifact.get("cache") or {}).get("stale"))
                for artifact in retained_artifacts
            ),
        },
        "artifacts": retained_artifacts,
    }
    for field in ("source_adapter_counts", "source_adapter_status"):
        if isinstance(payload.get(field), dict):
            cleaned[field] = {name: value for name, value in payload[field].items()
                              if not metadata_is_excluded(name)}
    cleaned["errors"] = [
        error for error in (payload.get("errors") or [])
        if not metadata_is_excluded(error)
    ]
    cleaned["license_notes"] = [
        note for note in (payload.get("license_notes") or [])
        if not metadata_is_excluded(note)
    ]
    cleaned["license_notes"].append(
        "Rights-blocked commercial map/locator station ingestion is retired; direct and merged source evidence is excluded by structured source policy."
    )
    if any(value_is_excluded(row, SOURCE_POLICY_EXCLUSIONS) for row in cleaned["records"]):
        raise RuntimeError("source-policy terms remain outside filtered records")
    for metadata in (cleaned["fetch_evidence"], cleaned["errors"]):
        if metadata_is_excluded(metadata):
            raise RuntimeError("source-policy terms remain in build-level provenance")
    report = {
        "excluded_records": len(excluded),
        "excluded_ids": sorted(str(row.get("id") or "") for row in excluded),
        "excluded_fetch_artifacts": len(original_artifacts or []) - len(retained_artifacts),
        "excluded_errors": len(payload.get("errors") or []) - len(cleaned["errors"]),
    }
    return cleaned, report


# --- Plan 013: DGT National Access Point (nap.dgt.es) corroboration adapter -------------
# Spain's official charging-infrastructure registry (AFIR art. 20 / Orden TED/445/2023):
# DATEX II v3 EnergyInfrastructureTablePublication, CC BY, no registration, refreshed
# daily by DGT/MITECO. Verified 2026-07-17: 12,331 sites / 151 operators, but NO tariff
# and NO live status fields — so every row is corroboration-only (location, operator,
# connectors, power). Rows that do not match existing cheap/free evidence are dropped by
# the same gate Madrid/Zaragoza use; a missing price is never treated as free.
DGT_NAP_FEED_URL = "https://infocar.dgt.es/datex2/v3/miterd/EnergyInfrastructureTablePublication/electrolineras.xml"
DGT_NAP_CATALOG_URL = "https://nap.dgt.es/dataset/puntos-de-recarga-electrica-para-vehiculos"

# Operator identity → the app a driver would actually open. Attribution/handoff ONLY —
# never a data path (apps without a public surface stay linked, not scraped).
NAP_OPERATOR_APPS: Tuple[Tuple[str, str, str], ...] = (
    ("EMAYA", "MELIB", "https://www.institutbalearenergia.com/es/red-melib/"),
    ("MELIB", "MELIB", "https://www.institutbalearenergia.com/es/red-melib/"),
    ("IBERDROLA", "Recarga Pública Iberdrola | bp pulse", "https://iberdrola-bppulse.es/"),
    ("CHARGING TOGETHER", "Iberdrola | bp pulse", "https://iberdrola-bppulse.es/"),
    ("FENIE", "Recarga Feníe Energía", "https://recarga.fenieenergia.es/"),
    ("ACCIONA", "ACCIONA recarga VE", "https://recarga.acciona.com/"),
    ("GESTION INTELIGENTE", "GiC (eCoVe)", "https://recargagic.com/"),
    ("GIC", "GiC (eCoVe)", "https://recargagic.com/"),
    ("ENDESA", "Endesa X Way", "https://www.endesaxway.com/"),
    ("WENEA", "Wenea", "https://wenea.com/"),
    ("ZUNDER", "Zunder", "https://www.zunder.com/"),
    ("REPSOL", "Repsol (Waylet)", "https://www.repsol.es/particulares/movilidad/"),
    ("TESLA", "Tesla", "https://www.tesla.com/es_es/findus"),
    ("CANARIAS TE RECARGA", "Canarias te recarga", "https://canariasterecarga.com/es/puntos-de-recarga/"),
    ("TERECARGA", "Canarias te recarga", "https://canariasterecarga.com/es/puntos-de-recarga/"),
    ("LIDL", "Lidl", "https://empresa.lidl.es/sostenibilidad/bueno-para-el-planeta/proteger-el-clima/construccion-y-logistica-sostenible/puntos-de-recarga"),
    ("ETECNIC", "EVcharge (Etecnic)", "https://etecnic.es/"),
)


def _nap_strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def nap_operator_app(operator_name: str, operator_id: str) -> Optional[Dict[str, str]]:
    haystack = _nap_strip_accents(f"{operator_name} {operator_id}").upper()
    for needle, app, url in NAP_OPERATOR_APPS:
        if needle in haystack:
            return {"app": app, "url": url}
    return None


def _dtx_local(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _dtx_value_text(element: ET.Element) -> str:
    """First non-empty <com:value> text under a DATEX multilingual value block."""
    for node in element.iter():
        if _dtx_local(node.tag) == "value" and (node.text or "").strip():
            return str(node.text).strip()
    return ""


def _dtx_connector_rows(refill_point: ET.Element) -> List[Tuple[str, Optional[float]]]:
    rows: List[Tuple[str, Optional[float]]] = []
    for connector in refill_point:
        if _dtx_local(connector.tag) != "connector":
            continue
        ctype, power_kw = "", None
        for field in connector:
            local = _dtx_local(field.tag)
            if local == "connectorType":
                ctype = (field.text or "").strip()
            elif local == "maxPowerAtSocket":
                watts = fnum(field.text)
                if watts:
                    power_kw = round(watts / 1000.0, 1)
        label = connector_label_from_token(ctype)
        if label:
            rows.append((label, power_kw))
    return rows


def _dtx_parse_site(elem: ET.Element) -> Dict[str, Any]:
    site: Dict[str, Any] = {
        "site_id": elem.get("id") or "", "name": "", "last_updated": "",
        "lat": None, "lon": None, "address": "", "city": "", "postcode": "",
        "operator_id": "", "operator_name": "", "type_of_site": "",
        "auth_methods": [], "evse_ids": [], "connector_rows": [], "port_count": 0,
    }
    for child in elem:
        local = _dtx_local(child.tag)
        if local == "name" and not site["name"]:
            site["name"] = _dtx_value_text(child)
        elif local == "lastUpdated":
            site["last_updated"] = (child.text or "").strip()
        elif local == "locationReference":
            for node in child.iter():
                node_local = _dtx_local(node.tag)
                text = (node.text or "").strip()
                if node_local == "latitude":
                    site["lat"] = fnum(text)
                elif node_local == "longitude":
                    site["lon"] = fnum(text)
                elif node_local == "postcode":
                    site["postcode"] = text
                elif node_local == "value":
                    if text.startswith("Dirección:"):
                        site["address"] = text.split(":", 1)[1].strip()
                    elif text.startswith("Municipio:"):
                        site["city"] = text.split(":", 1)[1].strip()
        elif local == "operator":
            site["operator_id"] = child.get("id") or ""
            site["operator_name"] = _dtx_value_text(child)
        elif local == "typeOfSite":
            site["type_of_site"] = (child.text or "").strip()
        elif local == "energyInfrastructureStation":
            for station_child in child:
                station_local = _dtx_local(station_child.tag)
                if station_local == "authenticationAndIdentificationMethods":
                    method = (station_child.text or "").strip()
                    if method and method not in site["auth_methods"]:
                        site["auth_methods"].append(method)
                elif station_local == "refillPoint":
                    site["port_count"] += 1
                    for rp_child in station_child:
                        if _dtx_local(rp_child.tag) == "name":
                            evse = _dtx_value_text(rp_child)
                            if evse and len(site["evse_ids"]) < 12:
                                site["evse_ids"].append(evse)
                            break
                    site["connector_rows"].extend(_dtx_connector_rows(station_child))
    return site


def parse_dgt_nap_feed(source: Any, *, minimum_sites: int = 5000) -> List[Dict[str, Any]]:
    """Stream-parse the ~85 MB national DATEX II feed with bounded memory."""
    context = ET.iterparse(source, events=("start", "end"))
    _, root = next(context)
    sites: List[Dict[str, Any]] = []
    for event, elem in context:
        if event == "end" and _dtx_local(elem.tag) == "energyInfrastructureSite":
            site = _dtx_parse_site(elem)
            if site["lat"] is not None and site["lon"] is not None:
                sites.append(site)
            elem.clear()
            root.clear()  # keep the already-parsed tree from accumulating
    if len(sites) < minimum_sites:
        raise RuntimeError(f"DGT NAP site population shrank to {len(sites)} (<{minimum_sites})")
    return sites


def nap_records_from_sites(sites: List[Dict[str, Any]], *, source_artifact: Optional[FetchArtifact] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for site in sites:
        connector_counts: Dict[Tuple[str, Optional[float]], int] = {}
        for label, power_kw in site["connector_rows"]:
            key = (label, power_kw)
            connector_counts[key] = connector_counts.get(key, 0) + 1
        connectors = [
            connector_ref(label, count=count, power_kw=power_kw, status="UNKNOWN", source_url=DGT_NAP_CATALOG_URL)
            for (label, power_kw), count in sorted(connector_counts.items(), key=lambda kv: str(kv[0]))
        ]
        power_values = [power_kw for (_label, power_kw) in site["connector_rows"] if power_kw]
        operator_app = nap_operator_app(site["operator_name"], site["operator_id"])
        display_name = (site["name"] or "official site").replace("_", " ").strip()
        operator_prefix = site["operator_name"] or "Official registry"
        add_record(
            out,
            source="DGT National Access Point charging registry",
            source_url=DGT_NAP_CATALOG_URL,
            name=f"{operator_prefix} — {display_name}",
            address=site["address"],
            city=site["city"] or "Spain",
            lat=site["lat"],
            lon=site["lon"],
            price_min=None,
            free_condition="The national registry publishes no tariff; check the price in the operator app.",
            connector_labels=[label for (label, _power) in site["connector_rows"]],
            max_power_kw=max(power_values) if power_values else None,
            status="VERIFY",
            reliability="official-charger-location-price-unknown",
            evidence=(
                "DGT/MITECO national access point (DATEX II, CC BY, daily) publishes location, operator, "
                "connector and power facts only; it contains no tariff and no live availability."
            ),
            parking_kind="street_or_municipal" if site["type_of_site"] == "onstreet" else "unknown",
            parking_note="The national registry identifies the setting but publishes no parking tariff; verify signs onsite.",
            raw={
                "nap_site_id": site["site_id"],
                "operator": site["operator_name"],
                "operator_emi3_id": site["operator_id"],
                "operator_app": operator_app,
                "evse_ids": site["evse_ids"],
                "auth_methods": site["auth_methods"],
                "type_of_site": site["type_of_site"],
                "nap_last_updated": site["last_updated"],
                "postcode": site["postcode"],
                "coordinate_source": "dgt_nap_datex2",
                "corroboration_only": True,
                "official_hardware_inventory": True,
            },
            connectors=connectors,
            port_count=site["port_count"],
            source_artifact=source_artifact,
        )
        if out:
            out[-1]["sources"][0]["license"] = "CC BY"
            out[-1]["sources"][0]["license_url"] = "https://creativecommons.org/licenses/by/4.0/"
            out[-1]["sources"][0]["attribution"] = "DGT / MITECO — Punto de Acceso Nacional"
            out[-1]["sources"][0]["catalog_url"] = DGT_NAP_CATALOG_URL
            out[-1]["sources"][0]["transformation"] = (
                "Normalized by Volta as corroboration-only evidence (operator, connectors, power); "
                "no missing tariff is inferred to be free."
            )
            if source_artifact is not None:
                out[-1]["sources"][0]["source_fetched_at"] = source_artifact.fetched_at
    return out


def source_dgt_nap(records: List[Dict[str, Any]]) -> None:
    artifact = cached_fetch(DGT_NAP_FEED_URL, "dgt_nap_electrolineras.xml", ttl_hours=24, timeout=240)
    sites = parse_dgt_nap_feed(io.BytesIO(artifact.body))
    records.extend(nap_records_from_sites(sites, source_artifact=artifact))


# --- Canarias te recarga (public Canary Islands charger map) -----------------------------
# One public GET (the map export) renders every marker with its tariff badge, connector
# pictograms and station page link. The import stays deliberately strict:
#   * an explicit price badge always wins over the marker icon (stale icons are real);
#   * only a plain "Gratis" badge or an icon-only free marker counts as free;
#   * time-conditional ("Gratis - 1º Hora") and hourly ("€/Hora") badges are logged but
#     never promoted to a free or €/kWh claim, because Volta has no duration-priced model;
#   * the popup's "Disponible N plazas" text is stored verbatim as a snapshot and is never
#     presented as live availability.
CANARIAS_TE_RECARGA_SOURCE = "Canarias te recarga public map"
CANARIAS_TE_RECARGA_KWH_RE = re.compile(r"(\d+[.,]\d{1,3})\s*€\s*/\s*kWh", re.I)
CANARIAS_TE_RECARGA_HOUR_RE = re.compile(r"(\d+[.,]\d{1,3}|\d+)\s*€\s*/\s*[Hh]ora", re.I)
CANARIAS_TE_RECARGA_GRATIS_RE = re.compile(r"\bGratis\b", re.I)
CANARIAS_TE_RECARGA_CONDITIONAL_GRATIS_RE = re.compile(r"\bGratis\s*[-–]", re.I)
CANARIAS_TE_RECARGA_BAY_SNAPSHOT_RE = re.compile(r"Disponible\s+\d+\s+plazas?", re.I)
CANARIAS_TE_RECARGA_DETAIL_HREF_RE = re.compile(r'href="(https://canariasterecarga\.com/(?:es|en)/[^"]+)"', re.I)


def canarias_tecarga_popup_text(popup_html: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", popup_html or ""))
    return re.sub(r"\s+", " ", text).strip()


def canarias_tecarga_price(popup_html: str) -> Tuple[Optional[float], str, str]:
    """Parse the marker's own tariff badge. Returns (price_min, basis, badge_text).

    A k€/kWh badge yields the kWh price; an hourly badge yields no €/kWh price; a plain
    "Gratis" badge yields 0.0; any other badge text yields no claim at all.
    """
    text = canarias_tecarga_popup_text(popup_html)
    match = CANARIAS_TE_RECARGA_KWH_RE.search(text)
    if match:
        return fnum(match.group(1)), "popup_kwh_price", match.group(0)
    hour_match = CANARIAS_TE_RECARGA_HOUR_RE.search(text)
    if hour_match:
        return None, "popup_hourly_price", hour_match.group(0)
    gratis_match = CANARIAS_TE_RECARGA_GRATIS_RE.search(text)
    if gratis_match:
        if CANARIAS_TE_RECARGA_CONDITIONAL_GRATIS_RE.search(text):
            return None, "popup_conditional_gratis", gratis_match.group(0)
        return 0.0, "popup_badge_gratis", gratis_match.group(0)
    return None, "", ""


def canarias_tecarga_connectors(popup_html: str, detail_url: str) -> Tuple[List[Dict[str, Any]], Optional[int]]:
    """Connector rows from the popup's own plug pictograms (ev-plug-*.svg) and their xN alt text."""
    rows: List[Dict[str, Any]] = []
    for match in re.finditer(r"<img[^>]*>", popup_html or "", re.I):
        tag = match.group(0)
        src = re.search(r'src="([^"]+)"', tag, re.I)
        if not src:
            continue
        label = CANARIAS_TE_RECARGA_CONNECTOR_ICONS.get(src.group(1).rsplit("/", 1)[-1])
        if not label:
            continue
        count_match = re.search(r'alt="x(\d+)"', tag, re.I)
        rows.append(connector_ref(
            label,
            count=int(count_match.group(1)) if count_match else 1,
            source_url=detail_url,
            raw={"basis": "canarias_te_recarga popup pictogram"},
        ))
    if not rows:
        return [], None
    return rows, sum(count_int(row.get("count")) for row in rows)


def canarias_tecarga_detail_url(popup_html: str) -> str:
    match = CANARIAS_TE_RECARGA_DETAIL_HREF_RE.search(popup_html or "")
    return match.group(1) if match else ""


def parse_canarias_tecarga_geojson(
    payload: Any,
    *,
    minimum_features: int = CANARIAS_TE_RECARGA_MIN_FEATURES,
    source_artifact: Optional[FetchArtifact] = None,
) -> List[Dict[str, Any]]:
    features = (payload or {}).get("features")
    if not isinstance(features, list):
        raise RuntimeError("Canarias te recarga export did not contain a feature list")
    parsed: List[Dict[str, Any]] = []
    skipped: Dict[str, int] = defaultdict(int)
    unpriced: Dict[str, int] = defaultdict(int)
    seen_points = 0
    for feature in features:
        if not isinstance(feature, dict) or (feature.get("geometry") or {}).get("type") != "Point":
            skipped["not_a_point"] += 1
            continue
        coordinates = (feature.get("geometry") or {}).get("coordinates") or []
        props = feature.get("properties") or {}
        lat, lon = (fnum(coordinates[1]) if len(coordinates) >= 2 else None), (fnum(coordinates[0]) if len(coordinates) >= 2 else None)
        name = str(props.get("name") or "").strip()
        if lat is None or lon is None or not name:
            skipped["missing_name_or_coordinates"] += 1
            continue
        seen_points += 1
        popup_html = str(props.get("popup") or "")
        detail_url = canarias_tecarga_detail_url(popup_html)
        price, basis, badge = canarias_tecarga_price(popup_html)
        if not basis:
            icon = str(props.get("icon") or "").rsplit("/", 1)[-1]
            icon_price = CANARIAS_TE_RECARGA_ICON_PRICES.get(icon)
            if icon_price is None:
                basis = "no_tariff_evidence"
            else:
                price, basis, badge = icon_price, f"marker_icon_{icon.replace('icono_', '').replace('.png', '')}", ""
        if price is None:
            # Hourly, time-conditional and unbadged markers stay on the map as
            # price-unknown chargers; their badge text is kept as evidence only.
            unpriced[basis] += 1
        connectors, port_count = canarias_tecarga_connectors(popup_html, detail_url or CANARIAS_TE_RECARGA_MAP_PAGE_URL)
        bay_match = CANARIAS_TE_RECARGA_BAY_SNAPSHOT_RE.search(canarias_tecarga_popup_text(popup_html))
        address = str(props.get("address") or "").strip()
        if basis == "popup_badge_gratis":
            tariff_claim = "tariff badge 'Gratis' (zero-cost charging published by the map)"
        elif basis == "popup_kwh_price":
            tariff_claim = f"tariff badge prints {price} €/kWh"
        elif price is None:
            tariff_claim = f"tariff badge '{badge}' has no €/kWh price" if badge else "no tariff badge or tariff icon"
        else:
            tariff_claim = f"marker icon marks the {price} €/kWh tariff class ({props.get('icon')})"
        evidence_bits = [
            f"Canarias te recarga public map station '{name}'",
            f"address '{address}'" if address else "",
            tariff_claim,
        ]
        evidence = "; ".join(bit for bit in evidence_bits if bit) + "."
        if bay_match:
            evidence += f" Source popup also printed '{bay_match.group(0)}' when fetched; that text is a page-render snapshot, not live telemetry."
        evidence += " Confirm the current tariff and bay status onsite."
        add_record(
            parsed,
            source=CANARIAS_TE_RECARGA_SOURCE,
            source_url=detail_url or CANARIAS_TE_RECARGA_MAP_PAGE_URL,
            name=name,
            address=address,
            city="",
            lat=lat,
            lon=lon,
            price_min=price,
            free_condition=(
                "Canarias te recarga lists this charger as Gratis."
                if basis == "popup_badge_gratis"
                else f"Canarias te recarga publishes {price} €/kWh for this charger."
                if basis == "popup_kwh_price"
                else f"Canarias te recarga shows '{badge}', which is not a €/kWh price; check the tariff in the operator app."
                if price is None and badge
                else "Canarias te recarga publishes no tariff for this charger; check it in the operator app."
                if price is None
                else "Canarias te recarga marks this charger with its free tariff icon."
                if price == 0
                else f"Canarias te recarga marks this charger with its {price} €/kWh tariff class."
            ),
            connector_labels=None if connectors else [],
            max_power_kw=None,
            status="VERIFY",
            # Directory-grade evidence, not official: the map's badge is a published tariff
            # class on a public community/association map, so it stays below the
            # community-free tier in RELIABILITY_RANK. That ordering also keeps existing
            # station identities stable: a merge must never re-key a station that already
            # exists in the DB (the delta gate requires the rebuild to preserve every
            # baseline id, and the app stores favorites/check-ins by station id).
            reliability="directory-page",
            evidence=evidence,
            parking_kind="unknown",
            parking_note="The map publishes no parking detail for this charger; check the signs onsite.",
            raw={
                "source_map_id": CANARIAS_TE_RECARGA_MAP_ID,
                "source_record_id": str(props.get("id") or ""),
                "marker_icon": str(props.get("icon") or ""),
                "marker_badge": badge,
                "tariff_basis": basis,
                "popup_text": canarias_tecarga_popup_text(popup_html)[:400],
                "popup_bay_snapshot": bay_match.group(0) if bay_match else "",
                "map_published_at": epoch_iso(float(props["published"])) if fnum(props.get("published")) else "",
                "map_modified_at": epoch_iso(float(props["modified"])) if fnum(props.get("modified")) else "",
                "detail_url": detail_url,
            },
            connectors=connectors,
            port_count=port_count,
            source_artifact=source_artifact,
        )
    if seen_points < minimum_features:
        raise RuntimeError(
            f"Canarias te recarga export shrank to {seen_points} usable station points (<{minimum_features}); skipped={dict(skipped)}"
        )
    print(f"canarias_tecarga: {len(parsed)} rows from {seen_points} station points (price unknown: {dict(unpriced)}; skipped: {dict(skipped)})")
    return parsed


def source_canarias_tecarga(records: List[Dict[str, Any]]) -> None:
    artifact = cached_fetch(
        CANARIAS_TE_RECARGA_GEOJSON_URL,
        CANARIAS_TE_RECARGA_CACHE_NAME,
        ttl_hours=24,
        timeout=45,
        headers={"Accept": "application/geo+json, application/json;q=0.9, */*;q=0.8"},
    )
    records.extend(parse_canarias_tecarga_geojson(json.loads(artifact.decode("utf-8", "replace")), source_artifact=artifact))


# --- MapaREVE public OCPI web API (Tenerife slice) ---------------------------------------
# Official Spanish charger map (MITECO + Red Eléctrica). Volta already serves this source
# live at request time; this adapter keeps a bounded cached snapshot of the Tenerife slice in
# the intelligence DB so the free/cheap tier has coverage when the live fetch is unavailable.
# Only rows whose published ENERGY components are genuinely free or ≤ €0.25/kWh are emitted;
# pricier rows stay live-only (an intelligence row must never invent a cheaper tariff).
MAPAREVE_PUBLIC_SOURCE = "MapaREVE public OCPI snapshot"


def mapareve_energy_prices(connector: Dict[str, Any]) -> List[float]:
    prices: List[float] = []
    if not isinstance(connector, dict):
        return prices
    for tariff in connector.get("tariffs") or []:
        if not isinstance(tariff, dict):
            continue
        elements = (tariff.get("tariff") or {}).get("elements")
        if isinstance(elements, dict):
            elements = [elements]
        for element in elements or []:
            if not isinstance(element, dict):
                continue
            for component in element.get("price_components") or []:
                if not isinstance(component, dict) or str(component.get("type") or "").upper() != "ENERGY":
                    continue
                value = fnum(component.get("price"))
                if value is not None:
                    prices.append(value)
    return prices


def mapareve_display_name(row: Dict[str, Any]) -> str:
    """Prefer the source's own station name; several OCPI rows carry only a numeric id."""
    name = str(row.get("name") or "").strip()
    if name and not re.fullmatch(r"[0-9*\-]{6,}", name):
        return name
    address = str(row.get("address") or "").strip()
    head = re.split(r"\s[-–]\s|,", address)[0].strip() if address else ""
    return head or "MapaREVE OCPI charger"


CANARY_POSTAL_PREFIXES = {"35", "38"}


def mapareve_city_postal_code(row: Dict[str, Any], lat: float, lon: float) -> str:
    """Only publish the source postcode when it cannot contradict the coordinates.

    Upstream rows exist with a wrong postal code (a Tenerife station carrying 14001),
    and add_record derives the city label from it, which would print a mainland city on
    an island card. A Canary postcode (35/38) must stay in the Canary box and any other
    prefix must stay outside it; otherwise the code is dropped and the city stays unknown.
    """
    code = str(row.get("postal_code") or "").strip()
    if len(code) < 2 or not code[:2].isdigit():
        return ""
    in_canaries = 27.5 <= lat <= 29.5 and -18.5 <= lon <= -13.0
    if (code[:2] in CANARY_POSTAL_PREFIXES) != in_canaries:
        return ""
    return code


def parse_mapareve_public_pages(
    pages: List[Dict[str, Any]],
    *,
    source_artifact: Optional[FetchArtifact] = None,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for page in pages:
        rows.extend(page.get("data") or [])
    parsed: List[Dict[str, Any]] = []
    skipped: Dict[str, int] = defaultdict(int)
    for row in rows:
        if not isinstance(row, dict):
            continue
        coordinates = row.get("coordinates") or {}
        lat, lon = fnum(coordinates.get("latitude")), fnum(coordinates.get("longitude"))
        if lat is None or lon is None:
            skipped["missing_coordinates"] += 1
            continue
        evses = [evse for evse in (row.get("evses") or []) if isinstance(evse, dict)]
        prices: List[float] = []
        connectors: List[Dict[str, Any]] = []
        power_values: List[float] = []
        status_counts: Dict[str, int] = defaultdict(int)
        timestamps: List[str] = []
        for evse in evses:
            status = str(evse.get("status") or "UNKNOWN").upper()
            status_counts[status] += 1
            if evse.get("status_updated_at"):
                timestamps.append(str(evse["status_updated_at"]))
            for connector in evse.get("connectors") or []:
                if not isinstance(connector, dict):
                    continue
                energy_prices = mapareve_energy_prices(connector)
                prices.extend(energy_prices)
                power_watts = fnum(connector.get("max_electric_power")) or 0.0
                power_kw = round(power_watts / 1000, 1) if power_watts > 1000 else (power_watts or None)
                if power_kw:
                    power_values.append(power_kw)
                standard = str(connector.get("standard") or "").strip()
                connectors.append(connector_ref(
                    connector_label_from_token(standard) or standard or "Unknown connector",
                    count=1,
                    power_kw=power_kw,
                    status=status,
                    source_url=MAPAREVE_PUBLIC_MAP_PAGE_URL,
                    connector_id=connector.get("id"),
                    raw={
                        "evse_id": evse.get("evse_id"),
                        "standard": standard,
                        "format": connector.get("format"),
                        "energy_prices": sorted({round(value, 4) for value in energy_prices}),
                    },
                ))
        price_min = min(prices) if prices else None
        free = bool(prices) and all(value == 0 for value in prices)
        owner = row.get("owner") or {}
        name = mapareve_display_name(row)
        address = str(row.get("address") or "").strip()
        last_updated = max(timestamps) if timestamps else ""
        status_summary = ", ".join(f"{count} {status.lower()}" for status, count in sorted(status_counts.items()))
        evidence = (
            f"MapaREVE public OCPI snapshot of the official Spanish charger map: operator {owner.get('name') or 'unknown'}; "
            f"location status {row.get('status') or 'UNKNOWN'}; {len(evses)} EVSEs ({status_summary}); "
            + (f"cheapest published ENERGY component {price_min} €/kWh. " if price_min is not None else "no ENERGY price component published. ")
            + "This row is a cached intelligence snapshot; live status and prices always come from the live MapaREVE fetch."
        )
        add_record(
            parsed,
            source=MAPAREVE_PUBLIC_SOURCE,
            source_url=MAPAREVE_PUBLIC_MAP_PAGE_URL,
            name=name,
            address=address,
            city="",
            lat=lat,
            lon=lon,
            price_min=price_min,
            free_condition=(
                "MapaREVE publishes a zero-cost ENERGY component for every connector." if free
                else "MapaREVE publishes no ENERGY price for this charger; check it in the operator app." if price_min is None
                else "MapaREVE publishes this charger at or below €0.25/kWh." if price_min <= 0.25
                else f"MapaREVE publishes €{price_min:.2f}/kWh for this charger."
            ),
            connector_labels=None,
            max_power_kw=max(power_values) if power_values else None,
            status="VERIFY",
            # Directory-grade cached snapshot, deliberately below community-free in
            # RELIABILITY_RANK: a merge must never re-key a station that already exists in
            # the DB (the delta gate requires the rebuild to preserve every baseline id).
            # The runtime live MapaREVE fetch is the live layer; this row is only a lead.
            reliability="directory-page",
            evidence=evidence,
            parking_kind="unknown",
            parking_note="MapaREVE publishes no parking detail for this charger; check the signs onsite.",
            raw={
                "location_id": row.get("id"),
                "name_raw": str(row.get("name") or ""),
                "postal_code": mapareve_city_postal_code(row, lat, lon),
                "owner": owner.get("name"),
                "location_status": row.get("status"),
                "total_evse": row.get("total_evse"),
                "evse_status_counts": dict(sorted(status_counts.items())),
                "energy_prices": sorted({round(value, 4) for value in prices}),
                "connector_count": len(connectors),
                "source": "mapareve-public-web-api",
                "updated_at": last_updated or None,
            },
            connectors=connectors,
            port_count=len(connectors) or None,
            source_artifact=source_artifact,
        )
    if not parsed and rows:
        print(f"mapareve_public: 0 rows from {len(rows)} scanned rows (skipped: {dict(skipped)})")
    return parsed


def source_mapareve_public(records: List[Dict[str, Any]]) -> None:
    """Fetch the paginated Tenerife slice of the public MapaREVE OCPI web API.

    The endpoint caps per_page at 20, so the slice is paged (bounded), each page cached
    separately, with a polite pause between live fetches. Header/bbox choices mirror the
    already-shipped live fetch in server.py (browser-like headers; the API is public and
    keyless). Fails closed: a missing first page raises instead of publishing a partial
    snapshot as if it were complete.
    """
    pages: List[Dict[str, Any]] = []
    body = json.dumps({**MAPAREVE_PUBLIC_TENERIFE_BBOX, "zoom": 9, "only_ocpi": True, "available": False}).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "APP-VERSION": "1.0.0",
        "platform": "web",
        "Time-Zone": "Atlantic/Canary",
        "Referer": MAPAREVE_PUBLIC_MAP_PAGE_URL,
        "Origin": "https://www.mapareve.es",
    }
    page = 1
    artifacts: List[FetchArtifact] = []
    for _ in range(MAPAREVE_PUBLIC_MAX_PAGES):
        cache_name = f"mapareve_public_tenerife_p{page}.json"
        cache_path = CACHE / cache_name
        cache_fresh = cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < MAPAREVE_PUBLIC_TTL_HOURS * 3600
        artifact = None
        last_error: Optional[Exception] = None
        for attempt in (1, 2):
            try:
                artifact = cached_fetch(
                    f"{MAPAREVE_PUBLIC_LOCATIONS_URL}?page={page}&per_page=20",
                    cache_name,
                    ttl_hours=MAPAREVE_PUBLIC_TTL_HOURS,
                    timeout=35,
                    data=body,
                    headers=headers,
                )
                break
            except Exception as exc:  # noqa: BLE001 - one bounded retry, then fail closed below
                last_error = exc
                if attempt == 1:
                    time.sleep(3)
        if artifact is None:
            raise RuntimeError(f"MapaREVE public page {page} fetch failed: {type(last_error).__name__}") from last_error
        artifacts.append(artifact)
        payload = json.loads(artifact.decode("utf-8", "replace"))
        pages.append(payload)
        if not cache_fresh:
            time.sleep(MAPAREVE_PUBLIC_PAGE_PAUSE_SECONDS)
        pagination = payload.get("pagination") or {}
        next_page = pagination.get("next")
        if next_page is None or len(payload.get("data") or []) < 20:
            break
        try:
            next_page = int(next_page)
        except (TypeError, ValueError):
            raise RuntimeError("MapaREVE public API returned an invalid page continuation")
        if next_page <= page:
            raise RuntimeError("MapaREVE public API page continuation did not progress")
        page = next_page
    if not pages:
        raise RuntimeError("MapaREVE public API returned no pages")
    rows = sum(len(payload.get("data") or []) for payload in pages)
    if rows < MAPAREVE_PUBLIC_MIN_ROWS:
        raise RuntimeError(
            f"MapaREVE public Tenerife slice shrank to {rows} rows (<{MAPAREVE_PUBLIC_MIN_ROWS}); upstream shape change?"
        )
    parsed = parse_mapareve_public_pages(pages, source_artifact=artifacts[0] if artifacts else None)
    print(f"mapareve_public: scanned {rows} Tenerife rows over {len(pages)} pages, kept {len(parsed)} rows")
    records.extend(parsed)


# --- Operator open feeds (owner decision 2026-10-12) -------------------------------------
# Charge point operators' own public, no-login map feeds. Each adapter reads the same
# anonymous request the operator's public web map makes. None of them publishes a price,
# so every row is a price-unknown lead; status snapshots stay in `raw` and never become
# live authority (MapaREVE remains the live layer). Rows sit at directory-page reliability
# so a cross-source merge never re-keys a station already in the DB.

# Region bounds are labels only; country filtering uses the attributed IGN boundary.
SPAIN_ISLAND_AND_CITY_BOXES: Tuple[Tuple[float, float, float, float], ...] = (
    (38.55, 1.1, 40.15, 4.4),      # Balearics (south, west, north, east)
    (27.5, -18.3, 29.5, -13.3),    # Canaries
    (35.85, -5.40, 35.93, -5.27),  # Ceuta
    (35.25, -2.99, 35.33, -2.91),  # Melilla
)


def likely_in_spain(lat: Optional[float], lon: Optional[float]) -> bool:
    return point_in_spain(lat, lon)


def ocpi_power_kw(connector: Dict[str, Any]) -> Optional[float]:
    watts = fnum(connector.get("max_electric_power"))
    if not watts:
        return None
    return round(watts / 1000, 1) if watts > 1000 else watts


OPERATOR_FEED_PARKING_NOTE = "The operator's public map publishes no parking detail; check the signs onsite."

# Per-adapter run status for this build (completeness, caps, detail coverage). Cleared by
# build_payload and published as `source_adapter_status`, so a partial walk is visible
# instead of looking like a complete one.
ADAPTER_STATUS: Dict[str, Dict[str, Any]] = {}

SPAIN_COUNTRY_CODES = {"ES", "ESP", "IC", "EA", "ESPAÑA", "ESPANA", "SPAIN"}
KNOWN_CONNECTOR_LABELS = {"Type 2", "CCS2", "CHAdeMO", "Tesla", "Schuko", "Type 1"}


def point_in_declared_spain(lat: Optional[float], lon: Optional[float], declared_country: Any = None) -> bool:
    """An explicit Spanish country declaration is authoritative inside the broad Spain extent.

    The coastline outline is coarse; a source that itself says ES/IC/EA must not lose
    border-area stations to it. Another declared country is never Spain. With no
    declaration, geometry alone decides.
    """
    country = str(declared_country or "").strip().upper()
    if country in SPAIN_COUNTRY_CODES:
        return in_bbox(lat, lon, SPAIN_BBOX)
    if country:
        return False
    return likely_in_spain(lat, lon)


def known_connector_label(token: Any) -> Optional[str]:
    """A recognised connector family, or None. Arbitrary tokens are never title-cased into a type."""
    label = connector_label_from_token(token)
    return label if label in KNOWN_CONNECTOR_LABELS else None


def published_connector_labels(connectors: List[Dict[str, Any]]) -> List[str]:
    """Labels for add_record that exclude "Unknown connector"; [] (not None) so no Type 2 default applies."""
    return sorted({c["label"] for c in connectors if c.get("label") in KNOWN_CONNECTOR_LABELS})


# Etecnic platform: the public map behind the EVcharge app, the Balearic Government's
# MELIB network and other councils/companies Etecnic manages. Power units in this feed are
# mixed (some rows carry kW, others look like amps), so power is kept raw and never
# published. The feed's connector_type_id values have no public legend (the operator map
# page never names them), so connectors stay "Unknown connector" with the raw id kept.
ETECNIC_SOURCE = "Etecnic EVcharge public charger map"
ETECNIC_FEED_URL = "https://etecnic.net/api/v1/chargers/index.json"
ETECNIC_MAP_PAGE_URL = "https://etecnic.es/mapa-de-recarga/"
ETECNIC_MIN_ROWS = 3000


def etecnic_network(name: str) -> str:
    """Network membership only when the row's own name says so; region never implies it."""
    if re.search(r"\bMELIB\b", name or "", re.I):
        return "MELIB (Govern de les Illes Balears), Etecnic platform"
    return "Etecnic platform (operator not established)"


def parse_etecnic_chargers(rows: List[Any], *, source_artifact: Optional[FetchArtifact] = None) -> List[Dict[str, Any]]:
    parsed: List[Dict[str, Any]] = []
    skipped: Dict[str, int] = defaultdict(int)
    for row in rows:
        if not isinstance(row, dict):
            skipped["not_object"] += 1
            continue
        if row.get("show_map") is False:
            skipped["hidden_from_operator_map"] += 1
            continue
        lat, lon = fnum(row.get("lat")), fnum(row.get("lon"))
        if not likely_in_spain(lat, lon):
            skipped["outside_spain"] += 1
            continue
        sockets = [s for s in (row.get("charger_sockets") or []) if isinstance(s, dict)]
        type_counts: Dict[Any, int] = defaultdict(int)
        for socket in sockets:
            type_counts[socket.get("connector_type_id")] += 1
        connectors = [
            connector_ref(
                "Unknown connector",
                count=count,
                source_url=ETECNIC_MAP_PAGE_URL,
                connector_id=type_id,
                raw={"etecnic_connector_type_id": type_id},
            )
            for type_id, count in sorted(type_counts.items(), key=lambda kv: str(kv[0]))
        ]
        name = str(row.get("name") or "").strip() or "Etecnic charger"
        network = etecnic_network(name)
        add_record(
            parsed,
            source=ETECNIC_SOURCE,
            source_url=ETECNIC_MAP_PAGE_URL,
            name=name,
            address=str(row.get("address") or ""),
            city="",
            lat=lat,
            lon=lon,
            price_min=None,
            free_condition="The Etecnic public map publishes no tariff; check the price in the operator app.",
            connector_labels=[],  # connector ids have no public legend; never default to Type 2
            max_power_kw=None,
            status="VERIFY",
            reliability="directory-page",
            evidence=(
                f"Etecnic public charger map ({network}): {len(sockets)} sockets, connector types not published. "
                "Socket status is a cached snapshot from the operator map, never live availability."
            ),
            parking_kind="unknown",
            parking_note=OPERATOR_FEED_PARKING_NOTE,
            raw={
                "etecnic_charger_id": row.get("id"),
                "network": network,
                "status_code": row.get("status"),
                "socket_status_codes": [s.get("status") for s in sockets],
                "power_raw": row.get("power"),
                "phases": row.get("phases"),
                "mobile_app_manage": row.get("mobile_app_manage"),
                "source": "etecnic-public-map",
            },
            connectors=connectors,
            port_count=len(sockets) or None,
            source_artifact=source_artifact,
        )
    print(f"etecnic: {len(parsed)} rows from {len(rows)} chargers (skipped: {dict(skipped)})")
    return parsed


def source_etecnic(records: List[Dict[str, Any]]) -> None:
    artifact = cached_fetch(ETECNIC_FEED_URL, "etecnic_chargers.json", ttl_hours=6, timeout=60,
                            headers={"Accept": "application/json", "Referer": ETECNIC_MAP_PAGE_URL})
    rows = json.loads(artifact.decode("utf-8", "replace"))
    if not isinstance(rows, list) or len(rows) < ETECNIC_MIN_ROWS:
        raise RuntimeError(f"Etecnic feed shrank to {len(rows) if isinstance(rows, list) else 'non-list'} rows (<{ETECNIC_MIN_ROWS})")
    records.extend(parse_etecnic_chargers(rows, source_artifact=artifact))


# Atlante: the public map's static OCPI-shaped location export. Sites still waiting for a
# grid connection or under construction are not chargers a driver can use yet.
ATLANTE_SOURCE = "Atlante public charger map"
ATLANTE_FEED_URL = "https://map.atlante.energy/geodata.json"
ATLANTE_MAP_PAGE_URL = "https://map.atlante.energy/"
ATLANTE_NOT_OPERATIONAL = {"WAITING FOR CONNECTION", "UNDER CONSTRUCTION"}
ATLANTE_MIN_SPAIN_ROWS = 40


def parse_atlante_locations(rows: List[Any], *, source_artifact: Optional[FetchArtifact] = None) -> List[Dict[str, Any]]:
    parsed: List[Dict[str, Any]] = []
    skipped: Dict[str, int] = defaultdict(int)
    for row in rows:
        if not isinstance(row, dict) or str(row.get("country") or "").upper() not in {"ES", "ESP"}:
            skipped["not_spain"] += 1
            continue
        if str(row.get("site_status") or "").upper() in ATLANTE_NOT_OPERATIONAL:
            skipped["not_operational"] += 1
            continue
        evses = [evse for evse in (row.get("evses") or []) if isinstance(evse, dict)]
        if not evses:
            skipped["no_evses"] += 1
            continue
        coords = row.get("coordinates") or (evses[0].get("coordinates") or {})
        lat, lon = fnum(coords.get("latitude")), fnum(coords.get("longitude"))
        if not point_in_declared_spain(lat, lon, row.get("country")):
            skipped["outside_spain"] += 1
            continue
        connectors: List[Dict[str, Any]] = []
        powers: List[float] = []
        status_counts: Dict[str, int] = defaultdict(int)
        for evse in evses:
            status = str(evse.get("status") or "UNKNOWN").upper()
            status_counts[status] += 1
            for connector in evse.get("connectors") or []:
                if not isinstance(connector, dict):
                    continue
                power = ocpi_power_kw(connector)
                if power:
                    powers.append(power)
                standard = str(connector.get("standard") or "")
                connectors.append(connector_ref(
                    known_connector_label(standard) or "Unknown connector",
                    power_kw=power,
                    status="UNKNOWN",
                    source_url=ATLANTE_MAP_PAGE_URL,
                    connector_id=connector.get("id"),
                    raw={"evse_id": evse.get("evse_id"), "standard": standard, "format": connector.get("format")},
                ))
        address = ", ".join(p for p in (str(row.get("street") or "").strip(), str(row.get("postal_code") or "").strip(), str(row.get("city") or "").strip()) if p)
        status_summary = ", ".join(f"{n} {s.lower()}" for s, n in sorted(status_counts.items()))
        add_record(
            parsed,
            source=ATLANTE_SOURCE,
            source_url=ATLANTE_MAP_PAGE_URL,
            name=re.sub(r"\s+", " ", str(row.get("name") or "Atlante charger")).strip(),
            address=address,
            city=str(row.get("city") or ""),
            lat=lat,
            lon=lon,
            price_min=None,
            free_condition="Atlante's public map publishes no tariff; check the price in the operator app.",
            connector_labels=published_connector_labels(connectors),  # [] when none recognised: no Type 2 default
            max_power_kw=max(powers) if powers else None,
            status="VERIFY",
            reliability="directory-page",
            evidence=(
                f"Atlante public charger map: {len(evses)} EVSEs ({status_summary}). "
                "EVSE status is a cached snapshot from the operator map, never live availability."
            ),
            parking_kind="unknown",
            parking_note=OPERATOR_FEED_PARKING_NOTE,
            raw={
                "atlante_location_id": row.get("id"),
                "party_id": row.get("party_id"),
                "postal_code": row.get("postal_code"),
                "evse_ids": [evse.get("evse_id") for evse in evses if evse.get("evse_id")][:12],
                "evse_status_counts": dict(sorted(status_counts.items())),
                "updated_at": row.get("last_updated"),
                "source": "atlante-public-geodata",
            },
            connectors=connectors,
            port_count=len(connectors) or None,
            source_artifact=source_artifact,
        )
    print(f"atlante: {len(parsed)} Spanish rows from {len(rows)} locations (skipped: {dict(skipped)})")
    return parsed


def source_atlante(records: List[Dict[str, Any]]) -> None:
    artifact = cached_fetch(ATLANTE_FEED_URL, "atlante_geodata.json", ttl_hours=12, timeout=90,
                            headers={"Accept": "application/json", "Referer": ATLANTE_MAP_PAGE_URL})
    rows = json.loads(artifact.decode("utf-8", "replace"))
    parsed = parse_atlante_locations(rows if isinstance(rows, list) else [], source_artifact=artifact)
    if len(parsed) < ATLANTE_MIN_SPAIN_ROWS:
        raise RuntimeError(f"Atlante feed yielded {len(parsed)} Spanish rows (<{ATLANTE_MIN_SPAIN_ROWS}); upstream shape change?")
    records.extend(parsed)


# Zunder: the public map page embeds every marker (position, title, location id) inline.
# The per-station location.php JSON carries live occupancy but is not fetched here: one page
# GET per build keeps the load to a single request.
ZUNDER_SOURCE = "Zunder public charger map"
ZUNDER_MAP_PAGE_URL = "https://www.zunder.com/mapa-de-ubicaciones/"
ZUNDER_LOCATION_URL = "https://www.zunder.com/wp-content/plugins/easycharger/location.php?id={id}"
ZUNDER_MIN_ROWS = 300
ZUNDER_MARKER_RE = re.compile(
    r"new\s+google\.maps\.Marker\(\{\s*position:\s*\{\s*lat:\s*(-?\d+(?:\.\d+)?),\s*lng:\s*(-?\d+(?:\.\d+)?)\s*\}"
    r".*?title:\s*'((?:[^'\\]|\\.)*)'",
    re.S,
)
ZUNDER_LOCATION_ID_RE = re.compile(r"easycharger/location\.php\?id=(\d+)")


def parse_zunder_map_page(page: str, *, source_artifact: Optional[FetchArtifact] = None) -> List[Dict[str, Any]]:
    parsed: List[Dict[str, Any]] = []
    skipped: Dict[str, int] = defaultdict(int)
    blocks = page.split("new google.maps.Marker(")[1:]
    for block in blocks:
        match = ZUNDER_MARKER_RE.match("new google.maps.Marker(" + block)
        id_match = ZUNDER_LOCATION_ID_RE.search(block)
        if not match or not id_match:
            skipped["unparsed_marker"] += 1
            continue
        lat, lon = fnum(match.group(1)), fnum(match.group(2))
        if not likely_in_spain(lat, lon):
            skipped["outside_spain"] += 1
            continue
        title = html.unescape(match.group(3).replace("\\'", "'")).strip()
        location_id = id_match.group(1)
        add_record(
            parsed,
            source=ZUNDER_SOURCE,
            source_url=ZUNDER_MAP_PAGE_URL,
            name=title or "Zunder charger",
            address="",
            city="",
            lat=lat,
            lon=lon,
            price_min=None,
            free_condition="Zunder's map publishes no per-station tariff; its published network tariffs apply. Check the app.",
            connector_labels=[],  # no connector evidence; never fall back to the Type 2 default
            max_power_kw=None,
            status="VERIFY",
            reliability="directory-page",
            evidence="Zunder public charger map marker. Availability is not part of this snapshot.",
            parking_kind="unknown",
            parking_note=OPERATOR_FEED_PARKING_NOTE,
            raw={
                "zunder_location_id": location_id,
                "location_json_url": ZUNDER_LOCATION_URL.format(id=location_id),
                "source": "zunder-public-map",
            },
            source_artifact=source_artifact,
        )
    print(f"zunder: {len(parsed)} Spanish rows from {len(blocks)} markers (skipped: {dict(skipped)})")
    return parsed


def source_zunder(records: List[Dict[str, Any]]) -> None:
    artifact = cached_fetch(ZUNDER_MAP_PAGE_URL, "zunder_map_page.html", ttl_hours=24, timeout=60,
                            headers={"Accept": "text/html"})
    parsed = parse_zunder_map_page(artifact.decode("utf-8", "replace"), source_artifact=artifact)
    if len(parsed) < ZUNDER_MIN_ROWS:
        raise RuntimeError(f"Zunder map yielded {len(parsed)} Spanish rows (<{ZUNDER_MIN_ROWS}); upstream shape change?")
    records.extend(parsed)


# Repsol (Waylet): the public station finder's charging-point list. Its own web client asks
# for it with an empty-body POST. Acquisition is dormant: the current policy grants
# only MapaREVE and Feníe query exceptions; this parser grants no extra exception.
REPSOL_SOURCE = "Repsol public charging point finder"
REPSOL_FINDER_URL = "https://www.repsol.es/buscador-eess-y-puntos-de-recarga/"
REPSOL_SEARCH_URL = "https://www.repsol.es/bin/repsol/searchmiddleware/station-search.json?action=search&idioma=es&tipo=3"
REPSOL_MIN_ROWS = 2000


def parse_repsol_charging_points(items: List[Any], *, source_artifact: Optional[FetchArtifact] = None) -> List[Dict[str, Any]]:
    parsed: List[Dict[str, Any]] = []
    skipped: Dict[str, int] = defaultdict(int)
    for item in items:
        if not isinstance(item, dict):
            skipped["not_object"] += 1
            continue
        if str(item.get("pais") or "").strip().lower() not in {"españa", "espana", "spain"}:
            skipped["not_spain"] += 1
            continue
        lat, lon = fnum(item.get("y")), fnum(item.get("x"))
        if not point_in_declared_spain(lat, lon, item.get("pais")):
            skipped["outside_spain"] += 1
            continue
        speeds = [str(s) for s in (item.get("velocidad") or []) if s]
        address = ", ".join(p for p in (str(item.get("direccion") or "").strip(), str(item.get("cp") or "").strip(), str(item.get("localidad") or "").strip()) if p)
        add_record(
            parsed,
            source=REPSOL_SOURCE,
            source_url=REPSOL_FINDER_URL,
            name=str(item.get("nombre") or "Repsol charging point").strip(),
            address=address,
            city=str(item.get("localidad") or ""),
            lat=lat,
            lon=lon,
            price_min=None,
            free_condition="Repsol's finder publishes no tariff here; check the price in Waylet.",
            connector_labels=[],  # no connector evidence; never fall back to the Type 2 default
            # The finder's speed class (Normal/Rápida/Ultrarrápida) is not a kW figure, so no
            # power is published and the fast flag stays off; the class stays in evidence/raw.
            max_power_kw=None,
            status="VERIFY",
            reliability="directory-page",
            evidence=f"Repsol public charging point finder: speed class {', '.join(speeds) or 'not stated'}.",
            parking_kind="unknown",
            parking_note=OPERATOR_FEED_PARKING_NOTE,
            raw={
                "repsol_point_id": item.get("id"),
                "postal_code": item.get("cp"),
                "speed_classes": speeds,
                "platform": item.get("platform"),
                "source": "repsol-public-finder",
            },
            source_artifact=source_artifact,
        )
    print(f"repsol: {len(parsed)} Spanish rows from {len(items)} charging points (skipped: {dict(skipped)})")
    return parsed


def source_repsol(records: List[Dict[str, Any]]) -> None:
    require_reviewed_query("source_repsol")
    artifact = cached_fetch(REPSOL_SEARCH_URL, "repsol_charging_points.json", ttl_hours=24, timeout=45, data=b"",
                            headers={"Accept": "application/json", "Referer": REPSOL_FINDER_URL})
    payload = json.loads(artifact.decode("utf-8", "replace"))
    items = ((payload.get("recarga") or {}).get("items") or []) if isinstance(payload, dict) else []
    parsed = parse_repsol_charging_points(items, source_artifact=artifact)
    if len(parsed) < REPSOL_MIN_ROWS:
        raise RuntimeError(f"Repsol finder yielded {len(parsed)} Spanish rows (<{REPSOL_MIN_ROWS}); upstream shape change?")
    records.extend(parsed)


# --- Place to Plug, MOT (Reus/Tarragona) and P-APP ---
# PlugShare is disabled. Active collectors use reviewed public retrieval surfaces.
# Price fields stay unknown unless a source says otherwise; community "free" flags
# remain community claims.

# Place to Plug: the public web app's GraphQL list uses a POST query that is not an
# approved transport exception. Acquisition is dormant; pagination is retained for
# offline fixtures. `coordinates` is
# [lon, lat] (confirmed with Aranjuez [-3.619, 40.023] and L'Hospitalet [2.105, 41.352]).
# Verified anonymously on 2026-10-12. The API silently serves only 10 rows per page above
# limit 50, so pages are 50 zones.
PTP_SOURCE = "Place to Plug public charging zones"
PTP_MAP_PAGE_URL = "https://placetoplug.com/"
PTP_PAGE_LIMIT = PLACETOPLUG_MAX_LIMIT
PTP_TTL_HOURS = 24 * 7
PTP_MIN_ROWS = int(os.environ.get("VOLTA_PTP_MIN_ROWS", "5000"))
PTP_MAX_PAGES_PER_BOX = 1500
PTP_PAGE_PAUSE_SECONDS = 0.3
PTP_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
PTP_SPAIN_COUNTRIES = {"spain", "españa", "espana", "es", "esp"}
# Only the two statuses seen with real data have a mapped meaning; anything else is unknown.
PTP_STATUS_AVAILABILITY = {"AVAILABLE": "available", "UNKNOWN": "unknown"}
PTP_OUT_OF_SERVICE_STATUSES = {"OUTOFORDER"}


def ptp_request_body(box: Tuple[float, float, float, float], page: int) -> bytes:
    south, west, north, east = box
    return json.dumps({
        "operationName": PLACETOPLUG_OPERATION,
        "variables": {"query": {
            "filters": {"bounds": {"minLat": south, "maxLat": north, "minLon": west, "maxLon": east}},
            "params": {"location": {"latitude": round((south + north) / 2, 6), "longitude": round((west + east) / 2, 6)}},
            "limit": PTP_PAGE_LIMIT,
            "page": page,
        }},
        "query": PLACETOPLUG_QUERY,
    }, separators=(",", ":")).encode("utf-8")


def parse_place_to_plug_zones(zones: List[Any], *, source_artifact: Optional[FetchArtifact] = None) -> List[Dict[str, Any]]:
    parsed: List[Dict[str, Any]] = []
    skipped: Dict[str, int] = defaultdict(int)
    for zone in zones:
        if not isinstance(zone, dict) or zone.get("id") in (None, ""):
            skipped["not_a_zone"] += 1
            continue
        coordinates = zone.get("coordinates")
        if not isinstance(coordinates, (list, tuple)) or len(coordinates) != 2:
            skipped["no_coordinates"] += 1
            continue
        lon, lat = fnum(coordinates[0]), fnum(coordinates[1])  # [lon, lat], never [lat, lon]
        address = zone.get("address") if isinstance(zone.get("address"), dict) else {}
        country = str(address.get("country") or "").strip()
        if country and country.lower() not in PTP_SPAIN_COUNTRIES:
            skipped["not_spain"] += 1
            continue
        if not point_in_declared_spain(lat, lon, "ES" if country else None):
            skipped["outside_spain"] += 1
            continue
        power = zone.get("powerRange") if isinstance(zone.get("powerRange"), dict) else {}
        power_min, power_max = fnum(power.get("min")), fnum(power.get("max"))
        status_raw = str(zone.get("status") or "").strip().upper()
        availability = PTP_STATUS_AVAILABILITY.get(status_raw, "unknown")
        street, city = str(address.get("street") or "").strip(), str(address.get("city") or "").strip()
        add_record(
            parsed,
            source=PTP_SOURCE,
            source_url=PTP_MAP_PAGE_URL,
            name=re.sub(r"\s+", " ", str(zone.get("name") or "Place to Plug charger")).strip(),
            address=", ".join(p for p in (street, city) if p),
            city=city,
            lat=lat,
            lon=lon,
            price_min=None,
            free_condition="Place to Plug's public zone list publishes no tariff; check the price in the operator app.",
            connector_labels=[],  # the zone list names no connector types; never default to Type 2
            max_power_kw=power_max if power_max else None,
            status="VERIFY",
            reliability="directory-page",
            evidence=(
                "Place to Plug public charging-zone list"
                + (f": {power_min:g}-{power_max:g} kW range" if power_min and power_max else "")
                + f"; status {status_raw or 'not stated'} is a cached snapshot, never live availability."
            ),
            parking_kind="unknown",
            parking_note=OPERATOR_FEED_PARKING_NOTE,
            raw={
                "ptp_zone_id": str(zone["id"]),
                "power_min_kw": power_min,
                "power_max_kw": power_max,
                "ptp_status_raw": status_raw or None,
                "availability": availability,
                "country": country or None,
                "source": "placetoplug-public-graphql",
            },
            source_artifact=source_artifact,
        )
    print(f"place_to_plug: {len(parsed)} Spanish rows from {len(zones)} zones (skipped: {dict(skipped)})")
    return parsed


def source_place_to_plug(records: List[Dict[str, Any]]) -> None:
    require_reviewed_query("source_place_to_plug")
    budget_start = int(os.environ.get("VOLTA_PTP_PAGE_BUDGET", "700"))
    budget = [budget_start]
    # Data and provenance are inseparable: duplicate zones replace both together.
    zones: Dict[str, Tuple[Dict[str, Any], FetchArtifact]] = {}
    pages = failures = boxes_complete = 0
    for index, box in enumerate(DIRECTORY_SPAIN_LIST_BOXES):
        for page in range(1, PTP_MAX_PAGES_PER_BOX + 1):
            cache_name = f"ptp_zones/box{index}_p{page}.json"
            (CACHE / cache_name).parent.mkdir(parents=True, exist_ok=True)
            spent_before = budget[0]
            try:
                result = budgeted_cached_json(
                    transport_placetoplug_url(), cache_name, ttl_hours=PTP_TTL_HOURS, budget=budget,
                    data=ptp_request_body(box, page), headers=dict(PTP_HEADERS), timeout=45,
                )
            except Exception as exc:
                failures += 1
                print(f"place_to_plug: box {index} page {page} failed: {type(exc).__name__}: {exc}")
                break
            if result is None:
                break  # The box remains incomplete when budget and cache are exhausted.
            if budget[0] < spent_before:
                time.sleep(PTP_PAGE_PAUSE_SECONDS)
            payload, page_artifact = result
            data = payload.get("data") if isinstance(payload, dict) else None
            body = data.get("findChargingZones") if isinstance(data, dict) else None
            if not isinstance(body, dict) or payload.get("errors"):
                failures += 1
                break
            page_zones, has_next = body.get("chargingZones"), body.get("hasNextPage")
            if (not isinstance(page_zones, list) or type(has_next) is not bool
                    or not isinstance(page_artifact, FetchArtifact)
                    or any(not isinstance(zone, dict) or zone.get("id") in (None, "") for zone in page_zones)
                    or (has_next and not page_zones)):
                failures += 1
                break
            pages += 1
            for zone in page_zones:
                zones[str(zone["id"])] = (zone, page_artifact)
            if not has_next:
                boxes_complete += 1
                break
    parsed = []
    by_artifact: Dict[str, Tuple[List[Dict[str, Any]], FetchArtifact]] = {}
    for zone, artifact in zones.values():
        batch, _ = by_artifact.setdefault(artifact.cache_name, ([], artifact))
        batch.append(zone)
    for batch, artifact in by_artifact.values():
        parsed.extend(parse_place_to_plug_zones(batch, source_artifact=artifact))
    complete = boxes_complete == len(DIRECTORY_SPAIN_LIST_BOXES) and failures == 0
    ADAPTER_STATUS["source_place_to_plug"] = {
        "zones": len(zones), "rows": len(parsed), "pages": pages,
        "network_fetches": budget_start - budget[0], "boxes_complete": boxes_complete,
        "failed_pages": failures, "complete": complete,
    }
    if not complete or len(parsed) < PTP_MIN_ROWS:
        raise RuntimeError(f"Place to Plug walk yielded {len(parsed)} Spanish rows; "
                           f"complete={complete}, minimum={PTP_MIN_ROWS}; refusing a partial walk")
    # Publish only after all boxes and pages have passed, even above the row floor.
    records.extend(parsed)


def transport_placetoplug_url() -> str:
    return PLACETOPLUG_URL


# MOT (Aparcar): municipal car parks that run on the MOT parking platform. Only the Reus
# "Carregadors municipals" layer (13 points) belongs here; the combined and privats layers
# are third-party or aggregate and are excluded. No licence is stated for the Reus GIS and
# endolls (plug count) is all it says about hardware, so power is never invented.
MOT_REUS_SOURCE = "MOT (Aparcar) — Ajuntament de Reus municipal GIS"
MOT_REUS_URL = "https://geoportal.reus.cat/apps/giscube-admin/layerserver/geojsonlayers/carregadors-municipals"
MOT_REUS_FALLBACK_PAGE = "https://www.reus.cat/serveis/vehicle-electric"
MOT_REUS_LAYER = "Carregadors municipals"
MOT_REUS_MIN_ROWS = 8
MOT_TARRAGONA_SOURCE = "MOT (Aparcar) — Ajuntament de Tarragona open data"
MOT_TARRAGONA_URL = "https://opendatafiles.tarragona.cat/03303.csv"
MOT_TARRAGONA_MIN_ROWS = 3
MOT_CROSSREF_RADIUS_KM = 0.04
MOT_REUS_ENDOLLS_RE = re.compile(r"(\d+)\s*endolls?", re.I)


def parse_mot_reus_geojson(payload: Any, *, source_artifact: Optional[FetchArtifact] = None) -> List[Dict[str, Any]]:
    parsed: List[Dict[str, Any]] = []
    skipped: Dict[str, int] = defaultdict(int)
    features = payload.get("features") if isinstance(payload, dict) else None
    for feature in features or []:
        props = feature.get("properties") if isinstance(feature, dict) and isinstance(feature.get("properties"), dict) else None
        geometry = feature.get("geometry") if isinstance(feature, dict) and isinstance(feature.get("geometry"), dict) else {}
        if props is None:
            skipped["not_a_feature"] += 1
            continue
        if str(props.get("layer") or "").strip() != MOT_REUS_LAYER:
            skipped["other_layer"] += 1  # carregadors privats and the combined layer are not MOT
            continue
        coords = geometry.get("coordinates")
        if geometry.get("type") != "Point" or not isinstance(coords, (list, tuple)) or len(coords) < 2:
            skipped["no_point"] += 1
            continue
        lon, lat = fnum(coords[0]), fnum(coords[1])  # GeoJSON order
        feature_id = props.get("id") if props.get("id") not in (None, "") else feature.get("id")
        if feature_id in (None, "") or not point_in_declared_spain(lat, lon, "ES"):
            skipped["no_id_or_outside_spain"] += 1
            continue
        match = MOT_REUS_ENDOLLS_RE.search(str(props.get("info") or ""))
        plugs = int(match.group(1)) if match else None
        link = str(props.get("nid") or "").strip()
        source_url = link if link.startswith("https://") else MOT_REUS_FALLBACK_PAGE
        connectors = [connector_ref("Unknown connector", count=plugs, source_url=source_url,
                                    raw={"reus_info": props.get("info")})] if plugs else []
        add_record(
            parsed,
            source=MOT_REUS_SOURCE,
            source_url=source_url,
            name=re.sub(r"\s+", " ", str(props.get("name") or "Reus municipal charger")).strip(),
            address="",
            city="Reus",
            lat=lat,
            lon=lon,
            price_min=None,
            free_condition="The Reus municipal GIS publishes no tariff; check the price onsite or with the car park.",
            connector_labels=[],  # only a plug count is published; never default to Type 2
            max_power_kw=None,
            status="VERIFY",
            reliability="official-charger-location-price-unknown",
            evidence=(
                f"Reus municipal public GIS layer '{MOT_REUS_LAYER}' (MOT-activated municipal car parks)"
                + (f": {plugs} endolls (plugs)" if plugs else "")
                + ". Power and tariff are not published. Municipal public GIS, no licence stated."
            ),
            parking_kind="unknown",
            parking_note="Municipal car park in Reus; the layer publishes no parking fee. Check the signs onsite.",
            raw={
                "reus_feature_id": str(feature_id),
                "plugs": plugs,
                "info_raw": props.get("info"),
                "code": props.get("code"),
                "category": props.get("category"),
                "subcategory": props.get("subcategory"),
                "layer": props.get("layer"),
                "source": "reus-municipal-gis",
            },
            connectors=connectors,
            port_count=plugs,
            source_artifact=source_artifact,
        )
    print(f"mot_reus: {len(parsed)} rows from {len(features or [])} features (skipped: {dict(skipped)})")
    return parsed


def parse_mot_tarragona_csv(body: Any, *, source_artifact: Optional[FetchArtifact] = None) -> List[Dict[str, Any]]:
    text = body.decode("utf-8-sig", "replace") if isinstance(body, (bytes, bytearray)) else str(body)
    parsed: List[Dict[str, Any]] = []
    skipped: Dict[str, int] = defaultdict(int)
    for row in csv.DictReader(io.StringIO(text)):
        nom = re.sub(r"\s+", " ", str(row.get("NOM") or "")).strip()
        lat, lon = fnum(row.get("LATITUD")), fnum(row.get("LONGITUD"))
        if not nom or lat is None or lon is None or not point_in_declared_spain(lat, lon, "ES"):
            skipped["no_name_or_outside_spain"] += 1
            continue
        status_raw = str(row.get("STATUS") or "").strip()
        street, postal = str(row.get("ADRECA") or "").strip(), str(row.get("CP") or "").strip()
        add_record(
            parsed,
            source=MOT_TARRAGONA_SOURCE,
            source_url=MOT_TARRAGONA_URL,
            name=nom,
            address=", ".join(p for p in (street, postal, "Tarragona") if p),
            city="Tarragona",
            lat=lat,
            lon=lon,
            price_min=None,
            free_condition="The Ajuntament de Tarragona open dataset publishes no tariff; check the price in the operator app.",
            connector_labels=[],  # AMPERATGE is amperes, not a connector type or kW
            max_power_kw=None,
            status="VERIFY",
            reliability="official-charger-location-price-unknown",
            evidence=(
                f"Ajuntament de Tarragona open data (MOT/Aparcar): status {status_raw or 'not stated'} (snapshot, never live)"
                f"; {row.get('AMPERATGE') or '?'} A. Power in kW and tariff are not published."
            ),
            parking_kind="unknown",
            parking_note=OPERATOR_FEED_PARKING_NOTE,
            raw={
                "tarragona_nom": nom,
                "status_raw": status_raw or None,
                "amperage_raw": str(row.get("AMPERATGE") or "").strip() or None,
                "postal_code": postal or None,
                "codi_ens": str(row.get("CODI_ENS") or "").strip() or None,
                "nom_ens": str(row.get("NOM_ENS") or "").strip() or None,
                "source": "tarragona-open-data-csv",
            },
            source_artifact=source_artifact,
        )
    print(f"mot_tarragona: {len(parsed)} rows (skipped: {dict(skipped)})")
    return parsed


def apply_mot_tarragona(records: List[Dict[str, Any]], mot_rows: List[Dict[str, Any]]) -> Tuple[int, int]:
    """Attach MOT as a cross-reference to the Etecnic row it duplicates; add only unmatched rows.

    The six Tarragona CSV rows are the same chargers Etecnic already publishes, at the same
    coordinates. Creating a second record would draw duplicate pins, so a MOT row within
    ``MOT_CROSSREF_RADIUS_KM`` of an Etecnic row (one-to-one, nearest first) only adds its
    source, identity key and out-of-service flag to that row; the Etecnic id is never re-keyed.
    Returns (rows_added, rows_cross_referenced).
    """
    etecnic = [r for r in records if r.get("source") == ETECNIC_SOURCE]
    pairs = sorted(
        (_dist_km(m, e), mi, ei)
        for mi, m in enumerate(mot_rows) for ei, e in enumerate(etecnic)
        if _dist_km(m, e) <= MOT_CROSSREF_RADIUS_KM
    )
    used_mot: Set[int] = set()
    used_etecnic: Set[int] = set()
    crossref = 0
    for _distance, mi, ei in pairs:
        if mi in used_mot or ei in used_etecnic:
            continue
        used_mot.add(mi)
        used_etecnic.add(ei)
        crossref += 1
        mot, row = mot_rows[mi], etecnic[ei]
        entry = {"name": MOT_TARRAGONA_SOURCE, "url": MOT_TARRAGONA_URL, "evidence": mot["sources"][0]["evidence"]}
        if all((s.get("name"), s.get("url")) != (entry["name"], entry["url"]) for s in row.get("sources", [])):
            row.setdefault("sources", []).append(entry)
            row["source_count"] = len(row["sources"])
        row["raw"]["mot_crossref"] = {
            "nom": mot["raw"]["tarragona_nom"],
            "status": mot["raw"]["status_raw"],
            "amperage_raw": mot["raw"]["amperage_raw"],
            "codi_ens": mot["raw"]["codi_ens"],
            "source_url": MOT_TARRAGONA_URL,
            "fetched_at": mot.get("source_fetched_at"),
        }
        key = [MOT_TARRAGONA_SOURCE, "tarragona_nom", mot["raw"]["tarragona_nom"]]
        carried = [list(k) for k in row.get("merged_source_identifiers") or []]
        if key not in carried:
            row["merged_source_identifiers"] = sorted(carried + [key])
        if mot.get("source_reported_unavailable") and not row.get("source_reported_unavailable"):
            row["source_reported_unavailable"] = mot["source_reported_unavailable"]
            row["is_free"] = False
            row["is_cheap"] = False
    added = 0
    for mi, mot in enumerate(mot_rows):
        if mi not in used_mot:
            records.append(mot)
            added += 1
    return added, crossref


def source_mot_aparcar(records: List[Dict[str, Any]]) -> None:
    errors: List[str] = []
    reus_rows: List[Dict[str, Any]] = []
    tgn_rows: List[Dict[str, Any]] = []
    try:
        payload, artifact = unpack_json_artifact(cached_json(
            MOT_REUS_URL, "mot_reus.json", ttl_hours=24, with_artifact=True, timeout=45,
            headers={"Accept": "application/json"},
        ))
        reus_rows = parse_mot_reus_geojson(payload, source_artifact=artifact)
        if len(reus_rows) < MOT_REUS_MIN_ROWS:
            errors.append(f"Reus layer yielded {len(reus_rows)} rows (<{MOT_REUS_MIN_ROWS})")
            reus_rows = []
    except Exception as exc:
        errors.append(f"Reus: {type(exc).__name__}: {exc}")
    try:
        artifact = cached_fetch(MOT_TARRAGONA_URL, "tgn_csv.csv", ttl_hours=24, timeout=45, headers={"Accept": "text/csv,*/*"})
        tgn_rows = parse_mot_tarragona_csv(artifact.body, source_artifact=artifact)
        if len(tgn_rows) < MOT_TARRAGONA_MIN_ROWS:
            errors.append(f"Tarragona CSV yielded {len(tgn_rows)} rows (<{MOT_TARRAGONA_MIN_ROWS})")
            tgn_rows = []
    except Exception as exc:
        errors.append(f"Tarragona: {type(exc).__name__}: {exc}")
    records.extend(reus_rows)
    added, crossref = apply_mot_tarragona(records, tgn_rows)
    ADAPTER_STATUS["source_mot_aparcar"] = {
        "reus_rows": len(reus_rows), "tarragona_rows": len(tgn_rows),
        "tarragona_added": added, "tarragona_crossref_only": crossref,
        "errors": errors, "complete": not errors,
    }
    print(f"mot_aparcar: reus={len(reus_rows)} tarragona={len(tgn_rows)} (added {added}, cross-referenced {crossref})")
    if errors:
        raise RuntimeError("; ".join(errors))


# PlugShare ingestion is disabled. The historical parser remains for offline evidence
# inspection only; no provider web-client credential or cached contribution is permitted.
PLUGSHARE_SOURCE = "PlugShare community web map"
PLUGSHARE_API = "https://api.plugshare.com/v3"
PLUGSHARE_STATION_URL = "https://www.plugshare.com/location/{id}"
PLUGSHARE_GRID_STEP_DEGREES = 0.38
PLUGSHARE_RADIUS_METERS = 30000
PLUGSHARE_COUNT = 1000
PLUGSHARE_NEARBY_TTL_HOURS = 24 * 7
PLUGSHARE_DETAIL_TTL_HOURS = 24 * 30
PLUGSHARE_PAUSE_SECONDS = float(os.environ.get("VOLTA_PLUGSHARE_PAUSE_SECONDS", "8"))
PLUGSHARE_MIN_ROWS = int(os.environ.get("VOLTA_PLUGSHARE_MIN_ROWS", "5000"))
PLUGSHARE_MAX_CONSECUTIVE_FAILURES = 3
PLUGSHARE_CONNECTOR_LABELS = {
    "type 2": "Type 2", "ccs2": "CCS2", "chademo": "CHAdeMO", "wall (euro)": "Schuko", "j-1772": "Type 1",
}


def plugshare_headers() -> Dict[str, str]:
    raise SourcePolicyBlocked("PlugShare collection is disabled; no reviewed public API grant")


def plugshare_nearby_url(lat: float, lon: float) -> str:
    params = urllib.parse.urlencode({
        "access": 1, "count": PLUGSHARE_COUNT, "latitude": lat, "longitude": lon, "radius": PLUGSHARE_RADIUS_METERS,
    })
    return f"{PLUGSHARE_API}/locations/nearby?{params}"


def plugshare_cell_cache_name(lat: float, lon: float) -> str:
    return f"plugshare_nearby/{lat:.4f}_{lon:.4f}.json"


def plugshare_grid_cells() -> List[Tuple[float, float]]:
    """Cell centres over the Spanish list boxes whose neighbourhood touches Spain.

    A 30 km circle covers a square cell of this step (half-diagonal under 28 km at Spanish
    latitudes), so the grid leaves no gaps; cells over open sea or other countries are dropped.
    """
    step, probe = PLUGSHARE_GRID_STEP_DEGREES, PLUGSHARE_GRID_STEP_DEGREES / 2 - 0.01
    cells: Set[Tuple[float, float]] = set()
    for south, west, north, east in DIRECTORY_SPAIN_LIST_BOXES:
        lat = south
        while lat <= north + 1e-9:
            lon = west
            while lon <= east + 1e-9:
                if any(point_in_spain(lat + dy, lon + dx) for dy in (-probe, 0, probe) for dx in (-probe, 0, probe)):
                    cells.add((round(lat, 4), round(lon, 4)))
                lon += step
            lat += step
    return sorted(cells)


def plugshare_connector_label(name: Any) -> Optional[str]:
    return PLUGSHARE_CONNECTOR_LABELS.get(str(name or "").strip().lower())


def parse_plugshare_location(item: Dict[str, Any], detail: Optional[Dict[str, Any]], *,
                             source_artifact: Optional[FetchArtifact] = None,
                             detail_artifact: Optional[FetchArtifact] = None,
                             out: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """`source_artifact` is the nearby-list response; `detail_artifact` the cached detail, if used."""
    out = out if out is not None else []
    location_id = item.get("id")
    detail = detail if isinstance(detail, dict) else {}
    if detail and str(detail.get("id")) != str(location_id):
        detail = {}  # a detail for another location never informs this row
    lat, lon = fnum(item.get("latitude")), fnum(item.get("longitude"))
    if location_id in (None, "") or not likely_in_spain(lat, lon):
        return out
    if item.get("coming_soon") or detail.get("coming_soon") or detail.get("enabled") is False:
        return out
    used_detail_artifact = detail_artifact if detail and detail_artifact is not None else None
    station_url = PLUGSHARE_STATION_URL.format(id=location_id)
    stations = (detail.get("stations") if detail else None) or item.get("stations") or []
    connectors: List[Dict[str, Any]] = []
    powers: List[float] = []
    for station in stations:
        for outlet in (station.get("outlets") or []) if isinstance(station, dict) else []:
            if not isinstance(outlet, dict):
                continue
            power = fnum(outlet.get("kilowatts")) or None
            if power:
                powers.append(power)
            if detail:
                connectors.append(connector_ref(
                    plugshare_connector_label(outlet.get("connector_name")) or "Unknown connector",
                    power_kw=power, status="UNKNOWN", source_url=station_url, connector_id=outlet.get("id"),
                    raw={"connector_name": outlet.get("connector_name"), "is_dc": outlet.get("is_dc")},
                ))
    type_names = [str(t) for t in (detail.get("connector_types") or item.get("connector_types") or [])]
    labels = sorted({plugshare_connector_label(t) for t in type_names if plugshare_connector_label(t)}
                    | set(published_connector_labels(connectors)))
    cost_flag = detail.get("cost") if detail else None
    community_free = cost_flag is False
    price = 0.0 if community_free else None  # a community claim, recorded as such below
    under_repair = bool(detail.get("under_repair") if detail else item.get("under_repair")) or bool(item.get("under_repair"))
    add_record(
        out,
        source=PLUGSHARE_SOURCE,
        source_url=station_url,
        name=str(item.get("name") or detail.get("name") or "PlugShare charger").strip(),
        address=str(detail.get("address") or item.get("address") or ""),
        city="",
        lat=lat,
        lon=lon,
        price_min=price,
        free_condition=(
            "PlugShare community listing flags this location as not costing money (cost=false). "
            "Community claim, not an official tariff; check onsite."
            if community_free else
            "PlugShare publishes no verified tariff for this listing; check the operator app."
        ),
        connector_labels=labels,
        max_power_kw=max(powers) if powers else None,
        status="VERIFY",
        reliability="community-free" if community_free else "directory-page",
        evidence=(
            f"PlugShare community web map listing {location_id}"
            + (f"; community score {item.get('score')}" if item.get("score") is not None else "")
            + ". Community data (power, connectors, cost are user/CPO-entered claims); availability is never treated as live."
        ),
        parking_kind="unknown",
        parking_note=DIRECTORY_PARKING_NOTE,
        raw={
            "plugshare_location_id": str(location_id),
            "connector_types": type_names,
            "station_count": item.get("station_count"),
            "score": item.get("score"),
            "is_fast_charger": item.get("is_fast_charger"),
            "under_repair": under_repair,
            "access": item.get("access"),
            "hours": detail.get("hours") if detail else None,
            "open247": detail.get("open247") if detail else None,
            "cost_flag": cost_flag,
            "cost_description": (detail.get("cost_description") or None) if detail else None,
            "community_free_claim": community_free,
            "detail_fetched": bool(detail),
            "list_fetched_at": source_artifact.fetched_at if source_artifact else None,
            "detail_fetched_at": used_detail_artifact.fetched_at if used_detail_artifact else None,
            "detail_stale": used_detail_artifact.stale if used_detail_artifact else None,
            "updated_at": detail.get("updated_at") if detail else None,
            "source": "plugshare-public-web-api",
        },
        connectors=connectors,
        port_count=len(connectors) or None,
        source_artifact=used_detail_artifact or source_artifact,
    )
    return out


def plugshare_location_id(value: Any) -> str:
    """Validate an external identifier before it can become a path or URL component."""
    if type(value) not in (int, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", str(value)):
        raise ValueError("invalid PlugShare location ID")
    return str(value)


def plugshare_cached_detail(location_id: Any, budget: List[int]) -> Optional[Tuple[Dict[str, Any], FetchArtifact]]:
    plugshare_location_id(location_id)
    raise SourcePolicyBlocked("PlugShare collection and cached evidence are disabled")


def plugshare_detail_priority(key: str) -> Tuple[int, float]:
    """Missing details first (newest id first), then the oldest cached; fresh ones cost nothing."""
    key = plugshare_location_id(key)
    path = CACHE / f"plugshare_locations/{key}.json"
    if not path.exists():
        return (0, -float(int(key) if key.isdigit() else 0))
    return (1, path.stat().st_mtime)


def source_plugshare(records: List[Dict[str, Any]]) -> None:
    """Block both network and historical cache ingestion before any work begins."""
    ADAPTER_STATUS["source_plugshare"] = {"complete": False, "enabled": False, "rows": 0,
                                          "reason": "source-policy-blocked"}
    raise SourcePolicyBlocked("PlugShare collection and cached evidence are disabled")


# P-APP (Interparking): the public car park finder embeds its Next.js (RSC flight) data in the
# page itself. One robots-allowed GET of this exact path (never /api/ or a query variant).
# P-APP & WEB SERVICIOS DIGITALES S.L.U. (CIF B67447151) belongs to the Interparking group;
# the DGT operator id is ES*IPK and its EVSE ids embed the car park id (ES*IPK*E<id> <->
# es-<id>). Only Spanish (es-*) car parks carrying the 'Electric charging station' facility
# flag are rows; the flag is all the page says about charging. Its `price` is a PARKING
# price and never a charging price. The page's coordinates are the source's own claim.
# DGT proximity agreement is mean <=100 m but has outliers (Jerez Hospital 4.2 km, Lleida
# 682 m, Cadiz 234 m, BCN Parc Sanitari 184 m, BCN WTC name-only 302 m), so merging stays
# on the existing cross-source rules only.
INTERPARKING_SOURCE = "P-APP (Interparking) public car park finder"
INTERPARKING_PAGE_URL = "https://www.interparking.es/es/encuentra-un-aparcamiento/"
INTERPARKING_SITE = "https://www.interparking.es"
INTERPARKING_EV_FACILITY = "Electric charging station"
INTERPARKING_MIN_ROWS = int(os.environ.get("VOLTA_INTERPARKING_MIN_ROWS", "30"))
INTERPARKING_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')


def interparking_car_parks_from_page(page: str) -> List[Dict[str, Any]]:
    chunks: List[str] = []
    for literal in INTERPARKING_PUSH_RE.findall(page):
        try:
            chunks.append(json.loads(literal))
        except ValueError:
            continue
    flight = "".join(chunks)
    marker = flight.find('"carParks":')
    if marker < 0:
        raise RuntimeError("Interparking finder page carries no embedded carParks data; upstream shape change?")
    try:
        car_parks, _end = json.JSONDecoder().raw_decode(flight[marker + len('"carParks":'):])
    except ValueError as exc:
        raise RuntimeError(f"Interparking embedded carParks JSON does not parse: {exc}") from exc
    if not isinstance(car_parks, list) or not car_parks:
        raise RuntimeError("Interparking embedded carParks list is empty")
    return [c for c in car_parks if isinstance(c, dict)]


def interparking_has_ev_flag(car_park: Dict[str, Any]) -> bool:
    return any(isinstance(f, dict) and f.get("id") == INTERPARKING_EV_FACILITY for f in car_park.get("facilities") or [])


def interparking_is_spanish(car_park: Dict[str, Any]) -> bool:
    return str(car_park.get("externalId") or "").startswith("es-")


def parse_interparking_car_parks(car_parks: List[Dict[str, Any]], *, source_artifact: Optional[FetchArtifact] = None) -> List[Dict[str, Any]]:
    parsed: List[Dict[str, Any]] = []
    skipped: Dict[str, int] = defaultdict(int)
    for park in car_parks:
        if not interparking_is_spanish(park):
            skipped["not_spain"] += 1
            continue
        if not interparking_has_ev_flag(park):
            skipped["no_ev_flag"] += 1  # no charger claim: not an actionable record
            continue
        location = park.get("location") if isinstance(park.get("location"), dict) else {}
        lat, lon = fnum(location.get("lat")), fnum(location.get("lng"))
        if not point_in_declared_spain(lat, lon, "ES"):
            skipped["outside_spain"] += 1
            continue
        price = re.sub(r"\s+", " ", str(park.get("price") or "").replace("\xa0", " ")).strip()
        price = "" if price.startswith("$") else price  # "$undefined" is the RSC null marker
        path = str(park.get("url") or "")
        source_url = INTERPARKING_SITE + path if re.fullmatch(r"/es/aparcamientos/[a-z0-9/_-]+/", path) else INTERPARKING_PAGE_URL
        city = park.get("city") if isinstance(park.get("city"), dict) else {}
        add_record(
            parsed,
            source=INTERPARKING_SOURCE,
            source_url=source_url,
            name=re.sub(r"\s+", " ", str(park.get("name") or "Interparking car park")).strip(),
            address=str(park.get("address") or ""),
            city=str(city.get("title") or ""),
            lat=lat,
            lon=lon,
            price_min=None,
            free_condition=(
                f"The Interparking finder shows a parking price ({price}), not a charging tariff; charging price unknown. Check onsite or in P-APP."
                if price else
                "Charging price unknown: the Interparking finder lists only a parking price, when it lists any price. Check onsite or in P-APP."
            ),
            connector_labels=[],  # the finder flags charging only; no plug type or power is published
            max_power_kw=None,
            status="VERIFY",
            reliability="directory-page",
            evidence="Interparking public car park finder flags 'Puntos de carga eléctrica' at this car park; no plug count, connector type, power or tariff is published.",
            parking_kind="paid_or_metered",
            parking_note="Interparking car park; the finder's price is the parking price, not a charging tariff.",
            raw={
                "interparking_external_id": str(park.get("externalId")),
                "parking_price": price or None,
                "brand": park.get("brand"),
                "city_slug": city.get("slug"),
                "ev_facility_flag": True,
                "source": "interparking-public-finder",
            },
            source_artifact=source_artifact,
        )
    print(f"interparking_papp: {len(parsed)} EV-flagged Spanish rows from {len(car_parks)} car parks (skipped: {dict(skipped)})")
    return parsed


def parse_interparking_page(page: str, *, source_artifact: Optional[FetchArtifact] = None) -> List[Dict[str, Any]]:
    return parse_interparking_car_parks(interparking_car_parks_from_page(page), source_artifact=source_artifact)


def source_interparking_papp(records: List[Dict[str, Any]]) -> None:
    artifact = cached_fetch(INTERPARKING_PAGE_URL, "interparking_finder.html", ttl_hours=24, timeout=60,
                            headers={"Accept": "text/html"})
    car_parks = interparking_car_parks_from_page(artifact.decode("utf-8", "replace"))
    parsed = parse_interparking_car_parks(car_parks, source_artifact=artifact)
    spanish = [c for c in car_parks if interparking_is_spanish(c)]
    ADAPTER_STATUS["source_interparking_papp"] = {
        "car_parks_total": len(car_parks),
        "spanish_car_parks": len(spanish),
        "ev_flagged_rows": len(parsed),
        "unflagged_spanish_skipped": sum(1 for c in spanish if not interparking_has_ev_flag(c)),
    }
    if len(parsed) < INTERPARKING_MIN_ROWS:
        raise RuntimeError(f"Interparking finder yielded {len(parsed)} EV-flagged Spanish rows (<{INTERPARKING_MIN_ROWS}); upstream shape change?")
    records.extend(parsed)


# --- Directory web maps, national (owner decision 2026-10-12) ----------------------------
# Electromaps and Chargemap web maps answer anonymous GETs. The owner decided on
# 2026-10-12 to collect their public station data despite their terms. Both are community
# directories: their price text and free flags are directory claims, never official
# tariffs, and their availability fields stay in `raw` (never live authority).
DIRECTORY_SPAIN_LIST_BOXES: Tuple[Tuple[float, float, float, float], ...] = (
    (35.2, -9.6, 43.9, 4.4),     # mainland, Balearics, Ceuta, Melilla (south, west, north, east)
    (27.5, -18.3, 29.5, -13.3),  # Canaries
)
DIRECTORY_PARKING_NOTE = "Parking detail comes from a community directory; check the signs onsite."


def budgeted_cached_json(url: str, cache_name: str, *, ttl_hours: float, budget: List[int], **kw: Any) -> Optional[Tuple[Any, FetchArtifact]]:
    """Cached JSON where only network fetches spend the per-run budget.

    A fresh cache is used as is. A stale cache is refreshed while budget remains and is
    otherwise reused, so a row never disappears just because this run ran out of budget
    (the delta gate requires every baseline id to survive). Missing caches are fetched
    only while budget remains; otherwise None.
    """
    path = CACHE / cache_name
    if path.exists():
        fresh = time.time() - path.stat().st_mtime < ttl_hours * 3600
        if fresh or budget[0] <= 0:
            artifact = cache_file_artifact(path, url=url, cache_name=cache_name, ttl_hours=ttl_hours)
            return json.loads(artifact.decode("utf-8", "replace")), artifact
    elif budget[0] <= 0:
        return None
    budget[0] -= 1
    payload, artifact = unpack_json_artifact(cached_json(url, cache_name, ttl_hours=ttl_hours, with_artifact=True, **kw))
    return payload, artifact

ELECTROMAPS_NATIONAL_SOURCE = "Electromaps public web map"
ELECTROMAPS_API = "https://www.electromaps.com/mapi/v2"
ELECTROMAPS_HEADERS = {"Accept": "application/json", "App-platform": "web", "Referer": "https://map.electromaps.com/es/"}
ELECTROMAPS_LIST_TYPES = "ON_STREET,PARKING,AIRPORT,CAMPING,HOTEL,RESTAURANT,SHOP,WORKSHOP,FUEL_STATION,CAR_DEALER,MALL,TAXI"
ELECTROMAPS_MIN_ROWS = 20000
ELECTROMAPS_DETAIL_TTL_HOURS = 24 * 30
# A tariff is trusted only when the whole text is one plain energy price. Mixed text
# ("0,00 €/kWh + 0,05 €/min", "0.20€/kWh + 2€ por sesión", ranges, day/night) is unknown.
ELECTROMAPS_KWH_RE = re.compile(r"^\s*(\d+(?:[.,]\d{1,3})?)\s*€\s*/\s*kwh\s*\.?\s*$", re.I)
ELECTROMAPS_FREE_RE = re.compile(r"^\s*(gratis|gratuito|free)\s*\.?\s*$", re.I)


def electromaps_station_url(location_id: Any) -> str:
    return f"https://map.electromaps.com/es/p/{location_id}"


def electromaps_list_url(box: Tuple[float, float, float, float]) -> str:
    south, west, north, east = box
    params = urllib.parse.urlencode({
        "latNE": north, "lngNE": east, "latSW": south, "lngSW": west,
        "realtime": "false", "connectors": "", "types": ELECTROMAPS_LIST_TYPES, "power": "3",
        "app": "false", "rfid": "false", "favorites": "false", "skipAuthTokenValidation": "true",
    })
    return f"{ELECTROMAPS_API}/locations?{params}"


def electromaps_price(text: str) -> Optional[float]:
    """A plain "Gratis" is 0.0; a text that is, in full, one €/kWh figure is that price; else unknown."""
    if not text:
        return None
    if ELECTROMAPS_FREE_RE.match(text):
        return 0.0
    match = ELECTROMAPS_KWH_RE.match(text)
    return fnum(match.group(1).replace(",", ".")) if match else None


def valid_electromaps_detail(detail: Any) -> bool:
    return (
        isinstance(detail, dict)
        and (detail.get("address") is None or isinstance(detail["address"], dict))
        and (detail.get("connectors") is None or isinstance(detail["connectors"], list))
        and not detail.get("error")
    )


def parse_electromaps_location(item: Dict[str, Any], detail: Optional[Dict[str, Any]], *,
                               source_artifact: Optional[FetchArtifact] = None,
                               detail_artifact: Optional[FetchArtifact] = None,
                               out: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """`source_artifact` is the list response; `detail_artifact` the cached detail, if one was used.

    Price, connectors and status come from the detail, so when a detail is used the row's
    freshness is the detail's age, not the list's; both times are kept in `raw`.
    """
    out = out if out is not None else []
    location_id = item.get("id")
    lat, lon = fnum(item.get("latitude")), fnum(item.get("longitude"))
    detail = detail if valid_electromaps_detail(detail) else {}
    address = detail.get("address") or {}
    # A detail's own country code is authoritative; with none, geometry decides.
    if location_id in (None, "") or not point_in_declared_spain(lat, lon, address.get("country_code") if detail else None):
        return out
    if detail and str(address.get("country_code") or "ES").upper() not in {"ES", "IC", "EA"}:
        return out
    used_detail_artifact = detail_artifact if detail and detail_artifact is not None else None
    connectors: List[Dict[str, Any]] = []
    powers: List[float] = []
    for connector in detail.get("connectors") or []:
        if not isinstance(connector, dict):
            continue
        power = fnum(connector.get("kw")) or None
        if power:
            powers.append(power)
        connectors.append(connector_ref(
            connector_label_from_token(str(connector.get("type") or "")) or "Unknown connector",
            power_kw=power,
            status="UNKNOWN",
            source_url=electromaps_station_url(location_id),
            connector_id=connector.get("id"),
            raw={"type": connector.get("type"), "directory_status": connector.get("status"), "power_type": connector.get("power_type")},
        ))
    charge_text = str(detail.get("charge_price") or "").strip()
    price = electromaps_price(charge_text)
    if price == 0:
        reliability = "directory-map-free"
    elif price is not None and price <= 0.25:
        reliability = "directory-map-cheap"
    else:
        reliability = "directory-page"
    parking_text = str(detail.get("parking_price") or "").strip()
    before = len(out)
    add_record(
        out,
        source=ELECTROMAPS_NATIONAL_SOURCE,
        source_url=electromaps_station_url(location_id),
        # The list name, not the detail name: a detail arriving later must not change the row id.
        name=str(item.get("name") or "Electromaps charger").strip(),
        address=str(address.get("address") or ""),
        city=str(address.get("city") or ""),
        lat=lat,
        lon=lon,
        price_min=price,
        free_condition=(
            f"Electromaps community listing says \"{charge_text}\". Directory claim, not an official tariff; check onsite."
            if charge_text else "Electromaps publishes no price for this listing; check the operator app."
        ),
        connector_labels=None if connectors else [],
        max_power_kw=max(powers) if powers else None,
        status="VERIFY",
        reliability=reliability,
        evidence=(
            f"Electromaps public web map listing {location_id}"
            + (f"; community rating {detail.get('rating')} from {detail.get('count_ratings')} ratings" if detail.get("count_ratings") else "")
            + ". Directory data; availability is never treated as live."
        ),
        parking_kind="free_parking" if ELECTROMAPS_FREE_RE.match(parking_text or "-") else "unknown",
        parking_note=(f"Electromaps community listing: parking \"{parking_text}\". " if parking_text else "") + DIRECTORY_PARKING_NOTE,
        raw={
            "electromaps_location_id": str(location_id),
            "marker": item.get("marker"),
            "detail_fetched": bool(detail),
            "list_fetched_at": source_artifact.fetched_at if source_artifact else None,
            "detail_fetched_at": used_detail_artifact.fetched_at if used_detail_artifact else None,
            "detail_stale": used_detail_artifact.stale if used_detail_artifact else None,
            "charge_price_text": charge_text or None,
            "parking_price_text": parking_text or None,
            "offline_status": detail.get("offline_status"),
            "detail_name": detail.get("name"),
            "only_tesla": detail.get("only_tesla"),
            "postal_code": address.get("postal_code"),
            "updated_at": detail.get("updated_at"),
            "source": "electromaps-public-web-map",
            "list_fetch": source_artifact.as_evidence() if source_artifact else None,
            "detail_fetch": detail_artifact.as_evidence() if detail_artifact else None,
        },
        connectors=connectors,
        port_count=len(connectors) or None,
        source_artifact=used_detail_artifact or source_artifact,
    )
    if len(out) == before:
        return out
    row = out[-1]
    if source_artifact:
        row["sources"][0]["source_fetched_at"] = source_artifact.fetched_at
        row["location_claim"]["source_fetched_at"] = source_artifact.fetched_at
    if detail and detail_artifact:
        row["sources"].append({
            "name": ELECTROMAPS_NATIONAL_SOURCE + " detail",
            "url": electromaps_station_url(location_id),
            "source_fetched_at": detail_artifact.fetched_at,
            "evidence": "Cached community tariff, hardware and parking detail; never live availability.",
        })
        for claim in ("charge_claim", "hardware_claim", "parking_claim"):
            row[claim]["source_fetched_at"] = detail_artifact.fetched_at
    # A newly acquired list must not make an older cached detail appear fresh.
    row["source_fetched_at"] = oldest_contributing_source_timestamp([row]) or row.get("source_fetched_at")
    return out


def electromaps_cached_detail(location_id: Any, budget: List[int]) -> Tuple[Optional[Dict[str, Any]], Optional[FetchArtifact]]:
    url = f"{ELECTROMAPS_API}/locations/{location_id}?skipAuthTokenValidation=true"
    cache_name = f"electromaps_location_{location_id}.json"
    evidence_start = len(FETCH_EVIDENCE)
    artifact = None
    try:
        result = budgeted_cached_json(
            url, cache_name, ttl_hours=ELECTROMAPS_DETAIL_TTL_HOURS,
            budget=budget, headers=ELECTROMAPS_HEADERS, timeout=30,
        )
        if result is None:
            return None, None
        detail, artifact = result
        if not valid_electromaps_detail(detail):
            raise ValueError("Malformed Electromaps detail")
        return detail, artifact
    except Exception as exc:
        # Keep failed acquisition/parsing visible without inventing a fetch date.
        evidence = next((entry for entry in reversed(FETCH_EVIDENCE[evidence_start:])
                         if entry.get("cache_name") == cache_name), None)
        if evidence is None:
            FETCH_EVIDENCE.append({
                "url": url, "cache_name": cache_name, "error": type(exc).__name__,
                "cache": {"hit": False, "fallback": False, "stale": False},
            })
        else:
            evidence["parse_error"] = type(exc).__name__
        return None, artifact


def electromaps_detail_priority(key: str) -> Tuple[int, float]:
    """Missing details first (newest listing id first), then the oldest cached; fresh ones cost nothing."""
    path = CACHE / f"electromaps_location_{key}.json"
    if not path.exists():
        return (0, -float(int(key) if key.isdigit() else 0))
    return (1, path.stat().st_mtime)


def source_electromaps_national(records: List[Dict[str, Any]]) -> None:
    require_allowed_source("Electromaps")
    items: Dict[str, Tuple[Dict[str, Any], Optional[FetchArtifact]]] = {}
    for index, box in enumerate(DIRECTORY_SPAIN_LIST_BOXES):
        payload, box_artifact = unpack_json_artifact(cached_json(
            electromaps_list_url(box), f"electromaps_list_es_{index}.json", ttl_hours=12,
            headers=ELECTROMAPS_HEADERS, timeout=90, with_artifact=True,
        ))
        for item in payload if isinstance(payload, list) else []:
            if isinstance(item, dict) and item.get("id") is not None:
                items[str(item["id"])] = (item, box_artifact)
    if len(items) < ELECTROMAPS_MIN_ROWS:
        raise RuntimeError(f"Electromaps list shrank to {len(items)} locations (<{ELECTROMAPS_MIN_ROWS})")
    # Details enrich a rolling subset: cached details are reused for 30 days, and only a
    # bounded number of missing ones are fetched per run, so full coverage builds up over
    # successive refreshes instead of hammering the upstream in one build.
    budget_start = int(os.environ.get("VOLTA_ELECTROMAPS_DETAIL_BUDGET", "400"))
    budget = [budget_start]
    candidates = [
        key for key in items
        if in_bbox(fnum(items[key][0].get("latitude")), fnum(items[key][0].get("longitude")), SPAIN_BBOX)
    ]
    # Spend the budget where it matters: listings with no detail (newest first), then the
    # oldest cached details. Ascending id order starved every new listing.
    details: Dict[str, Tuple[Optional[Dict[str, Any]], Optional[FetchArtifact]]] = {}
    for key in sorted(candidates, key=electromaps_detail_priority):
        result = electromaps_cached_detail(key, budget)
        if result:
            details[key] = result
    parsed: List[Dict[str, Any]] = []
    for key in sorted(candidates, key=lambda k: int(k) if k.isdigit() else 0):
        item, list_artifact = items[key]
        detail, detail_artifact = details.get(key, (None, None))
        parse_electromaps_location(item, detail, source_artifact=list_artifact,
                                  detail_artifact=detail_artifact, out=parsed)
    with_detail = sum(1 for row in parsed if row["raw"].get("detail_fetched"))
    stale_detail = sum(1 for row in parsed if row["raw"].get("detail_stale"))
    ADAPTER_STATUS["source_electromaps_national"] = {
        "listings": len(items),
        "rows": len(parsed),
        "rows_with_detail": with_detail,
        "rows_with_stale_detail": stale_detail,
        "rows_without_detail": len(parsed) - with_detail,
        "detail_network_fetches": budget_start - budget[0],
        "complete": with_detail == len(parsed),
    }
    print(f"electromaps_national: {len(parsed)} Spanish rows from {len(items)} listings ({with_detail} with details, {stale_detail} stale)")
    records.extend(parsed)


CHARGEMAP_NATIONAL_SOURCE = "Chargemap public web map"
CHARGEMAP_POOLS_URL = "https://map.chargemap.com/mappy/charging_pools.json"
CHARGEMAP_HEADERS = {"Accept": "application/json", "Referer": "https://chargemap.com/en-gb/map"}
CHARGEMAP_BOX_TTL_HOURS = 24 * 7
CHARGEMAP_MAX_DEPTH = 10
# Observed partial walks yield ~4,650 Spanish pools; below the floor, or well below the
# previous successful run, the walk is treated as broken rather than published as complete.
CHARGEMAP_MIN_ROWS = int(os.environ.get("VOLTA_CHARGEMAP_MIN_ROWS", "4000"))
CHARGEMAP_MIN_PREVIOUS_FRACTION = 0.9
CHARGEMAP_LAST_RUN_CACHE = "chargemap_national_last_run.json"


def parse_chargemap_pool(pool: Dict[str, Any], *, source_artifact: Optional[FetchArtifact] = None,
                         out: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    out = out if out is not None else []
    if str(pool.get("country_code") or "").upper() not in {"ES", "IC", "EA"}:
        return out
    coords = pool.get("gps_coordinates") or {}
    lat, lon = fnum(coords.get("lat")), fnum(coords.get("lon"))
    # The pool declares ES/IC/EA above, which outranks the coarse coastline outline.
    if not point_in_declared_spain(lat, lon, pool.get("country_code")) or pool.get("id") in (None, ""):
        return out
    slug_value = str(pool.get("slug") or pool.get("id"))
    url = f"https://chargemap.com/{slug_value}.html"
    connectors: List[Dict[str, Any]] = []
    powers: List[float] = []
    for connector in pool.get("charging_connectors") or []:
        if not isinstance(connector, dict):
            continue
        power = fnum(connector.get("power_max")) or None
        if power:
            powers.append(power)
        connectors.append(connector_ref(
            connector_label_from_token(str(connector.get("type") or "")) or "Unknown connector",
            count=count_int(connector.get("count")),
            power_kw=power,
            status="UNKNOWN",
            source_url=url,
            connector_id=(connector.get("connector_type") or {}).get("id"),
            raw={"type": connector.get("type"), "available_count": connector.get("available_count"), "evse_ids": connector.get("evse_ids")},
        ))
    is_free = pool.get("is_free") is True
    address = " ".join(p for p in (str(pool.get("street_name") or "").strip(), str(pool.get("number") or "").strip()) if p)
    address = ", ".join(p for p in (address, str(pool.get("postal_code") or "").strip(), str(pool.get("city") or "").strip()) if p)
    before = len(out)
    add_record(
        out,
        source=CHARGEMAP_NATIONAL_SOURCE,
        source_url=url,
        name=str(pool.get("name") or "Chargemap charger").strip(),
        address=address,
        city=str(pool.get("city") or ""),
        lat=lat,
        lon=lon,
        price_min=0.0 if is_free else None,
        free_condition=(
            "Chargemap's community listing marks this station free. Directory claim, not an official tariff; check onsite."
            if is_free else "Chargemap publishes no price in its map listing; check the operator app."
        ),
        connector_labels=None if connectors else [],
        max_power_kw=max(powers) if powers else None,
        status="VERIFY",
        reliability="directory-map-free" if is_free else "directory-page",
        evidence=(
            f"Chargemap public web map pool {pool.get('id')} ({pool.get('location_type_slug') or 'location type unknown'}); "
            f"operational status {pool.get('operational_status') or 'unknown'}. Directory data; availability is never treated as live."
        ),
        parking_kind="unknown",
        parking_note=DIRECTORY_PARKING_NOTE,
        raw={
            "chargemap_pool_id": str(pool.get("id")),
            "slug": slug_value,
            "postal_code": pool.get("postal_code"),
            "operational_status": pool.get("operational_status"),
            "availability_status": pool.get("availability_status"),
            "is_tesla": pool.get("is_tesla"),
            "evse_emi3_ids": (pool.get("evse_emi3_ids") or [])[:12],
            "last_used_time": pool.get("last_used_time"),
            "source": "chargemap-public-web-map",
        },
        connectors=connectors,
        port_count=sum(count_int(c.get("count")) for c in connectors) or None,
        source_artifact=source_artifact,
    )
    # Operational outage describes the station; occupancy UNAVAILABLE does not.
    # Keep an out-of-order listing and its identity, but never offer it as usable.
    if len(out) > before and str(pool.get("operational_status") or "").upper() == "OUT_OF_ORDER":
        out[-1]["source_reported_unavailable"] = {
            "source": CHARGEMAP_NATIONAL_SOURCE,
            "field": "operational_status",
            "value": pool["operational_status"],
            "summary": "Chargemap reports this station out of order; verify restoration with the operator.",
        }
    return out


def chargemap_previous_rows() -> int:
    try:
        value = json.loads((CACHE / CHARGEMAP_LAST_RUN_CACHE).read_text(encoding="utf-8")).get("rows")
        return value if isinstance(value, int) and value > 0 else 0
    except (OSError, ValueError, AttributeError):
        return 0


def source_chargemap_national(records: List[Dict[str, Any]]) -> None:
    """Quadtree walk of the public pool endpoint over Spain.

    Boxes that return clusters are split until they return pools. Each box response is
    cached for 7 days and only uncached boxes count against the per-run network budget, so
    a national pass completes over a few refreshes without a burst of thousands of calls.
    """
    require_allowed_source("Chargemap")
    budget = [int(os.environ.get("VOLTA_CHARGEMAP_NETWORK_BUDGET", "1200"))]
    pools: Dict[str, Tuple[Dict[str, Any], Optional[FetchArtifact]]] = {}
    incomplete = [0]
    max_depth_incomplete = [0]
    boxes_fetched = [0]
    stale_boxes = [0]

    def fetch_box(north: float, west: float, south: float, east: float) -> Optional[Tuple[List[Dict[str, Any]], Optional[FetchArtifact]]]:
        key = f"{north:.4f}_{west:.4f}_{south:.4f}_{east:.4f}".replace("-", "m").replace(".", "p")
        query = urllib.parse.urlencode({"NW": f"{north};{west}", "SE": f"{south};{east}"})
        result = budgeted_cached_json(
            f"{CHARGEMAP_POOLS_URL}?{query}", f"chargemap_es_{key}.json", ttl_hours=CHARGEMAP_BOX_TTL_HOURS,
            budget=budget, headers=CHARGEMAP_HEADERS, timeout=40,
        )
        if result is None:
            return None
        data, artifact = result
        return ((data.get("response") or {}).get("content") or {}).get("items") or [], artifact

    def walk(north: float, west: float, south: float, east: float, depth: int) -> None:
        try:
            result = fetch_box(north, west, south, east)
        except Exception:
            incomplete[0] += 1
            return
        if result is None:
            incomplete[0] += 1
            return
        items, artifact = result
        boxes_fetched[0] += 1
        if artifact is not None and artifact.stale:
            stale_boxes[0] += 1
        has_cluster = False
        for item in items:
            pool = item.get("pool") if isinstance(item, dict) else None
            if isinstance(pool, dict) and pool.get("id") is not None:
                known = pools.get(str(pool["id"]))
                # A pool in overlapping boxes keeps its freshest response.
                if known is None or (artifact and known[1] and artifact.fetched_at > known[1].fetched_at) or known[1] is None:
                    pools[str(pool["id"])] = (pool, artifact)
            elif isinstance(item, dict) and item.get("type") == "cluster":
                has_cluster = True
        if has_cluster and depth >= CHARGEMAP_MAX_DEPTH:
            # Clusters we can no longer split hide pools: that box is incomplete, not done.
            incomplete[0] += 1
            max_depth_incomplete[0] += 1
        if has_cluster and depth < CHARGEMAP_MAX_DEPTH:
            mid_lat, mid_lon = (north + south) / 2, (west + east) / 2
            for box in ((north, west, mid_lat, mid_lon), (north, mid_lon, mid_lat, east),
                        (mid_lat, west, south, mid_lon), (mid_lat, mid_lon, south, east)):
                walk(*box, depth + 1)

    for south, west, north, east in DIRECTORY_SPAIN_LIST_BOXES:
        walk(north, west, south, east, 0)
    parsed: List[Dict[str, Any]] = []
    for pool, artifact in pools.values():
        parse_chargemap_pool(pool, source_artifact=artifact, out=parsed)
    previous = chargemap_previous_rows()
    floor = max(CHARGEMAP_MIN_ROWS, int(previous * CHARGEMAP_MIN_PREVIOUS_FRACTION))
    ADAPTER_STATUS["source_chargemap_national"] = {
        "pools": len(pools),
        "rows": len(parsed),
        "boxes_fetched": boxes_fetched[0],
        "stale_boxes": stale_boxes[0],
        "incomplete_boxes": incomplete[0],
        "max_depth_incomplete_boxes": max_depth_incomplete[0],
        "previous_rows": previous or None,
        "row_floor": floor,
        "complete": incomplete[0] == 0,
    }
    print(f"chargemap_national: {len(parsed)} Spanish rows from {len(pools)} pools; "
          f"{incomplete[0]} boxes left for later runs ({max_depth_incomplete[0]} at max depth), {stale_boxes[0]} stale boxes")
    if len(parsed) < floor:
        raise RuntimeError(
            f"Chargemap walk yielded {len(parsed)} Spanish rows (<{floor}; "
            f"{incomplete[0]} incomplete boxes); refusing a partial walk"
        )
    if incomplete[0] == 0 or len(parsed) >= previous:  # a thinner partial walk never lowers the baseline
        (CACHE / CHARGEMAP_LAST_RUN_CACHE).write_text(json.dumps({"rows": len(parsed), "at": now_iso()}), encoding="utf-8")
    records.extend(parsed)


TELPARK_STATUS: Dict[str, Any] = {}


def source_telpark(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Spain parking pages with their own EV flag; no live or tariff authority."""
    try:
        from .ingest_telpark import collect_telpark
    except ImportError:
        from ingest_telpark import collect_telpark
    parsed, status = collect_telpark(cached_fetch, add_record)
    TELPARK_STATUS.clear()
    TELPARK_STATUS.update(status)
    # The source-health integration may provide this shared status dictionary.
    adapter_status = globals().get("ADAPTER_STATUS")
    if isinstance(adapter_status, dict):
        adapter_status["source_telpark"] = dict(status)
    if not status["ready"]:
        raise RuntimeError("Telpark intake failed closed: " + json.dumps(status, sort_keys=True))
    records.extend(parsed)
    return status


def require_reviewed_query(adapter: str) -> None:
    ADAPTER_STATUS[adapter] = {"enabled": False, "complete": False, "rows": 0,
                               "reason": "transport-review-required"}
    raise SourcePolicyBlocked(f"{adapter}: acquisition and cached ingestion require transport review")


def require_allowed_source(family: str) -> None:
    if value_is_excluded({"source": family}, SOURCE_POLICY_EXCLUSIONS):
        raise SourcePolicyBlocked(f"{family} collection and cached evidence are excluded")


ACTIVE_SOURCE_ADAPTERS = (
    source_valencia_municipal,
    source_lidl,
    source_osm,
    source_openchargemap,
    source_barcelona_endolla,
    source_madrid_official,
    source_zaragoza_official,
    source_dgt_nap,
    source_fenie,
    source_moraleja_green,
    source_canarias_tecarga,
    source_mapareve_public,
    source_etecnic,
    source_atlante,
    source_zunder,
    source_mot_aparcar,
    source_interparking_papp,
    source_telpark,
)


def build_payload() -> Dict[str, Any]:
    # Provenance is per build. Never let a prior in-process build/test make the next
    # output claim fetches that did not happen in that build.
    FETCH_EVIDENCE.clear()
    ADAPTER_STATUS.clear()
    records: List[Dict[str, Any]] = []
    errors = []
    source_adapter_counts: Dict[str, int] = {}
    for fn in ACTIVE_SOURCE_ADAPTERS:
        before = len(records)
        try:
            fn(records)
            added = len(records) - before
            source_adapter_counts[fn.__name__] = added
            print(f"{fn.__name__}: +{added}")
        except Exception as e:
            source_adapter_counts[fn.__name__] = len(records) - before
            errors.append({"source": fn.__name__, "error": f"{type(e).__name__}: {e}"})
            print(f"{fn.__name__}: ERROR {type(e).__name__}: {e}")
    # Remove prohibited source contributions before price/hardware/identity union.
    # A post-merge primary-label filter cannot reconstruct untainted allowed facts.
    records, excluded_inputs = partition_records(records, SOURCE_POLICY_EXCLUSIONS)
    merged = dedupe(records)
    merged, cross_merged = merge_cross_source_duplicates(merged)
    print(f"cross-source duplicate merge: -{cross_merged} rows (corroborating sources unioned)")
    unpriced = sum(1 for row in merged if row.get("price_min") is None)
    print(f"price-unknown chargers kept as leads: {unpriced}")
    payload = {
        "generated_at": now_iso(),
        "total_records": len(merged),
        "raw_records": len(records),
        "source_policy_excluded_input_records": len(excluded_inputs),
        "cross_source_merged": cross_merged,
        "source_adapter_counts": source_adapter_counts,
        "source_adapter_status": {name: dict(status) for name, status in sorted(ADAPTER_STATUS.items())},
        "errors": errors,
        "fetch_evidence": fetch_evidence_payload(),
        "license_notes": [
            "OSM/Overpass data is ODbL; keep attribution and share-alike obligations in mind.",
            "Barcelona Endolla and Madrid official datasets are reused under CC BY 4.0; Zaragoza municipal facts follow its published reuse conditions. Preserve source/update attribution.",
            "Red MOVEM's €0.25/kWh regulation is attached only to unambiguous OSM locations at or below 22 kW; Moraleja Green is a single static Verify-first official-policy lead.",
            "Feníe snapshots retain owner-attested written redistribution permission and the narrow read-only marker-list POST exception; neither establishes independent legal certification.",
            "Provider apps/onsite signs override every cached intelligence record.",
        ],
        "records": merged,
    }
    cleaned, policy_report = apply_source_policy_exclusions_to_payload(payload, preserve_raw_records=True)
    print(f"source-policy exclusions: -{policy_report['excluded_records']} records")
    return cleaned


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Volta's public-source cheap/free charging intelligence database.")
    parser.add_argument("--out", type=Path, default=OUT, help="Output JSON path. Defaults to data/free_intelligence.json.")
    parser.add_argument(
        "--policy-clean-from",
        type=Path,
        help="Deterministically clean an existing DB without network fetches (for explicit source-policy retirement).",
    )
    args = parser.parse_args()

    if args.policy_clean_from:
        source_path = args.policy_clean_from.expanduser()
        source_payload = json.loads(source_path.read_text(encoding="utf-8"))
        payload, report = apply_source_policy_exclusions_to_payload(source_payload)
        print(f"source-policy deterministic clean: -{report['excluded_records']} records from {source_path}")
    else:
        payload = build_payload()
    out_path = args.out.expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Compact JSON: the all-charger national DB is ~100 MB indented, at GitHub's file limit.
    out_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {out_path}: {len(payload['records'])} merged records from {payload['raw_records']} raw records; errors={len(payload['errors'])}")


if __name__ == "__main__":
    main()
