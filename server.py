#!/usr/bin/env python3
"""Volta Go — local live EV charging web app server.

No third-party Python dependencies. It serves static files and exposes a small JSON
API that combines regional live feeds with source-backed chargers worldwide
(viewport/bbox-driven). Public station collection has two narrow, owner-approved POST
exceptions (scripts/source_transport.py); every other station-source non-GET is refused.
Owner-authorized vehicle OAuth is a separate, fixed-provider integration (vehicles.py).
Community/manual leads stay separate from live results.
"""
from __future__ import annotations

import gzip as gzip_lib
import heapq
import hashlib
import ipaddress
import json
import math
import mimetypes
import os
import re
import ast
import concurrent.futures
import html as html_lib
import sys
import threading
import time
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from datetime import datetime, time as dt_time, timezone
from html.parser import HTMLParser
try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python <3.9 fallback is not expected here.
    ZoneInfo = None
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import hmac

from scripts.json_recordset import load_json_recordset
from scripts.global_source_catalog import ADAPTERS as GLOBAL_ADAPTERS, ADAPTER_IDS as GLOBAL_ADAPTER_IDS
from scripts.intelligence_index import IndexedRecords, IntelligenceIndex, file_sha256
from scripts.price_evidence import price_evidence_counts
from scripts.source_policy import load_source_policy_exclusions, partition_records, recordset_sha256, value_is_excluded
from scripts.source_transport import SourcePolicyBlocked, policy_urlopen
from station_truth import (
    classify_source_resource,
    normalize_connector_label,
    normalize_public_source_url,
    primary_source_for,
    source_candidates,
)

import agent_mcp
import community as community_store
from google_maps import attach_google_maps_navigation
import vehicles as vehicle_store

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SOURCE_POLICY_EXCLUSIONS = load_source_policy_exclusions(DATA / "source_policy_exclusions.json")
VEHICLES = vehicle_store.VehicleConnections()
PRICE_EVIDENCE_SOURCE_STATUS = "active-additive-price-only-bounded"
PRICE_EVIDENCE_DECISION_TYPE = "owner-collection-policy-not-provider-licence"
PRICE_EVIDENCE_POLICY = (
    "Owner policy permits bounded anonymous public GET collection in Spain for additive price evidence on existing stations only. "
    "It is not a provider licence or rights clearance. Apple App Store Guideline 5.2.2 review remains separate. "
    "Claims cannot set primary charging or parking prices, free/cheap filters, live verification or route-ready status."
)


def source_policy_record_allowed(record: Dict[str, Any]) -> bool:
    """Whether complete primary+nested station evidence survives explicit policy."""
    return not value_is_excluded(record, SOURCE_POLICY_EXCLUSIONS)


# The React SPA (frontend/dist, built via `npm --prefix frontend run build`) is
# the ONLY UI (plans/009 retired the legacy static/ shell). /api/* is unaffected.
# Kept pure (no filesystem check, no exit) so unit tests can import this module
# freely even when dist hasn't been built in the test environment; main() is
# the sole enforcement point -- it refuses to start the server at all without
# a built dist (see the check right before serve_forever()). If dist is
# missing, the request handlers below still degrade safely to a 404 (they
# already .exists()-check before serving), never a crash.
WEB_DIST = ROOT / "frontend" / "dist"
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("application/manifest+json", ".webmanifest")


def web_root() -> Path:
    return WEB_DIST

VALENCIA_BBOX = {
    "latitude_ne": 40.20,
    "longitude_ne": 0.05,
    "latitude_sw": 38.65,
    "longitude_sw": -1.60,
}
MAPAREVE_URL = "https://www.mapareve.es/api/public/v1/locations"
MAPAREVE_PAGE_TIMEOUT_SECONDS = float(os.environ.get("VOLTA_MAPAREVE_PAGE_TIMEOUT", "6.0"))
# The bounded national/Valencia-wide OCPI crawl needs ~35s (23 pages at 1-4s each);
# an 18s budget turns every cheap+wide refresh into a "Live results may be incomplete"
# degraded response even when every page succeeds. 45s keeps the bound (a runaway
# upstream still trips it) while letting a complete crawl finish.
MAPAREVE_FETCH_BUDGET_SECONDS = float(os.environ.get("VOLTA_MAPAREVE_FETCH_BUDGET", "45.0"))
# The public API caps per_page at 20 and returns ~916 Valencia OCPI stations unfiltered.
# Filtering server-side by energy_price_max is optional. Cheap mode keeps a small
# headroom above €0.25. Full public view omits the price bound so REVE returns the
# whole OCPI set in the viewport (still paginated).
MAPAREVE_PRICE_QUERY_MAX = float(os.environ.get("VOLTA_MAPAREVE_PRICE_QUERY_MAX", "0.30"))
# 23 pages needed for the full price-unbounded Valencia OCPI set (total_count 455):
# cap 20 forced every wide refresh to end "map limit reached / incomplete" despite
# clean upstream data. 40 keeps the anti-runaway cap well above the real need.
MAPAREVE_MAX_PAGES = int(os.environ.get("VOLTA_MAPAREVE_MAX_PAGES", "40"))
MAPAREVE_PER_PAGE = 20
# Pages fetched at once after page 1 reports total_count. Bounded so one refresh never
# floods the upstream; the per-process POST budget in source_transport still applies.
MAPAREVE_PAGE_CONCURRENCY = max(1, int(os.environ.get("VOLTA_MAPAREVE_PAGE_CONCURRENCY", "4")))
CACHE_TTL_SECONDS = int(os.environ.get("VOLTA_CACHE_TTL", "60"))
CACHE: Dict[str, Any] = {"ts": 0, "payload": None, "raw_count": 0, "error": None, "assembled": None}
# CACHE["recent"]: recent successful MapaREVE fetches by viewport cache key (oldest
# evicted first). The main slot holds one key, so moving between two areas used to
# re-crawl both every time. Entries share their payload object with CACHE["payload"],
# which is never mutated in place.
MAPAREVE_RECENT_MAX_ENTRIES = 6
GAIA_CACHE: Dict[str, Dict[str, Any]] = {}
GAIA_CACHE_TTL_SECONDS = int(os.environ.get("VOLTA_GAIA_CACHE_TTL", str(CACHE_TTL_SECONDS)))
GAIA_STALE_TTL_SECONDS = int(os.environ.get("VOLTA_GAIA_STALE_TTL", "900"))
GAIA_FETCH_BUDGET_SECONDS = float(os.environ.get("VOLTA_GAIA_FETCH_BUDGET", "5.0"))
GAIA_DEFAULT_JUMP_URL = "https://partner-next.gaiacharge.com/cpo/jump/fr-grn-ees-val-alb-artesans-1/CIRCUTOR-0786-1"
GAIA_JUMP_URLS = [
    u.strip()
    for u in os.environ.get("VOLTA_GAIA_JUMP_URLS", GAIA_DEFAULT_JUMP_URL).split(",")
    if u.strip()
]
GAIA_CONNECTOR_PROBE_MAX = int(os.environ.get("VOLTA_GAIA_CONNECTOR_PROBE_MAX", "8"))
GAIA_STATION_SEEDS = [
    {
        "id": "gaia-fr-grn-ees-val-alb-artesans-1",
        "name": "Gaia Green Tech - Alboraia - Carrer dels Artesans",
        "address": "Carrer dels Artesans 2, 46120 Alboraia",
        "city": "Alboraia",
        "lat": 39.50201,
        "lon": -0.34468,
        "jump_url": GAIA_DEFAULT_JUMP_URL,
        "max_connector_index": GAIA_CONNECTOR_PROBE_MAX,
    }
]

# Feníe attribution/home URL. Feníe station rows are collected offline by
# scripts/ingest_sources.py (source_fenie) under the owner-attested reuse permission and a
# narrow read-only marker-list POST exception; there is no runtime Feníe fetch.
FENIE_ATTRIBUTION_HOME = "https://recarga.fenieenergia.es/"
FENIE_SOURCE_STATUS = "permitted-owner-attested-snapshot"
FENIE_POLICY_TEXT = (
    "Feníe's own gratuito public-map stations are ingested offline under the owner-attested written "
    "redistribution permission, using one anonymous read-only marker-list POST exception (exact host/path, "
    "no redirects, bounded budget). Cached rows are a snapshot and never live."
)


def fenie_origin_record_count(records: Iterable[Any]) -> int:
    """Count intelligence rows whose own provenance is the Feníe public map."""
    if isinstance(records, IndexedRecords):
        return int(records.index.summaries["fenie_origin_records"])
    count = 0
    for record in records or []:
        if not isinstance(record, dict):
            continue
        primary = record.get("primary_source") if isinstance(record.get("primary_source"), dict) else {}
        origin = " ".join(str(value or "") for value in (record.get("source"), record.get("source_url"), primary.get("url")))
        if "fenieenergia.es" in origin.casefold() or "feníe energía public map" in origin.casefold():
            count += 1
    return count

# ThreadingHTTPServer runs one thread per connection, and a single request can also fan out
# across an internal ThreadPoolExecutor (fetch_primary_sources_parallel), so the module-level
# caches below (this pair, plus ELECTROMAPS_CACHE, GEOCODE_CACHE, OVERVIEW_BYTES_CACHE and
# STATIC_GZ_CACHE defined further down) are mutated by concurrent threads. D-145: unlocked
# check-then-act on these dicts let interleaved read/update serve one filter-set's cached
# payload under another's key. One lock per cache, guarding ONLY the dict read-decide/copy and
# write/evict -- NEVER a network fetch (the benign "two threads both miss and both fetch" race
# stays unserialized). Plans 005 and 007 reuse this exact table/convention for their own state.
_CACHE_LOCKS: Dict[str, threading.Lock] = {
    name: threading.Lock()
    for name in ("live", "gaia", "electromaps", "geocode", "overview", "static_gz", "rate_limit", "intel", "free_registry", "known_ids")
}

# --- Plan 005: public-deploy hardening (rate limiting + upstream concurrency) ---
# The repo is deploy-ready for the public internet with no auth layer (a product
# decision -- Volta is deliberately keyless). A single anonymous client can otherwise
# fan out to upstream MapaREVE/Gaia fetches via bbox/max_price/force
# permutations, risking an upstream IP ban or self-DoS on a small VM. These two
# primitives bound that: a per-client token-bucket limiter on the two routes that do
# real work, and a global semaphore bounding concurrent upstream fetch fan-outs.
RATE_LIMIT_RPM = max(1, int(os.environ.get("VOLTA_RATE_LIMIT_RPM", "60")))
# Geocode is a human-paced action (jump to a city); no env override -- deliberately
# just a tighter default via the same limiter's per-route rpm argument.
GEOCODE_RATE_LIMIT_RPM = 10
# Set behind a reverse proxy we control (Cloudflare Tunnel, Fly's edge) ONLY -- never
# on a raw public bind, where any client could forge X-Forwarded-For to spoof identity
# or to impersonate a trusted-internal caller (see _rate_limit_client_key below).
VOLTA_TRUST_PROXY = os.environ.get("VOLTA_TRUST_PROXY", "false").strip().lower() == "true"
RATE_BUCKETS: Dict[str, Tuple[float, float]] = {}  # "{route}:{client}" -> (tokens, last_refill_ts)
RATE_BUCKETS_MAX_ENTRIES = 10_000

# --- Plan 013: community write gates. Writes are always rate-limited and daily-capped
# (community.py); optionally, VOLTA_COMMUNITY_TOKEN locks writes to clients that send
# X-Volta-Community (the owner's own app builds). VOLTA_ADMIN_TOKEN gates moderation —
# unset means moderation over HTTP is disabled entirely (fail closed). Deployments without
# durable SQLite/photo storage must set VOLTA_COMMUNITY_WRITES_ENABLED=false; every POST then
# fails before body parsing or mutation while read-only registry/community summaries remain up.
COMMUNITY_WRITES_ENABLED = os.environ.get(
    "VOLTA_COMMUNITY_WRITES_ENABLED", "true"
).strip().lower() == "true"
COMMUNITY_WRITE_RPM = max(1, int(os.environ.get("VOLTA_COMMUNITY_WRITE_RPM", "10")))
COMMUNITY_TOKEN = os.environ.get("VOLTA_COMMUNITY_TOKEN", "").strip()
ADMIN_TOKEN = os.environ.get("VOLTA_ADMIN_TOKEN", "").strip()
COMMUNITY_JSON_MAX_BYTES = 32_768


def _is_loopback_peer(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False  # unparseable -- fail closed (do not grant the exemption)


def _rate_limit_client_key(handler: "Handler") -> Optional[str]:
    """Resolve the identity used to key the rate limiter and the force=true
    cache-bypass exemption. Returns None for a caller we treat as trusted/internal
    (no rate limiting, force honored); otherwise the IP string to bucket on.

    Precedence is deliberate and easy to get backwards (read twice): a resolvable
    forwarded public identity ALWAYS wins over the raw-peer loopback check. This
    project's own recommended deploy path (DEPLOY.md's Cloudflare Tunnel from a
    home machine) runs the proxy on the SAME host, so the raw TCP peer for every
    public request is 127.0.0.1 -- an unconditional loopback exemption would silently
    make the rate limiter a no-op for every real visitor on that path. Only when
    VOLTA_TRUST_PROXY is on AND X-Forwarded-For actually carries a hop do we resolve
    a public identity from it; a bare direct loopback connection with no such header
    (local dev, QA scripts hitting the server on 127.0.0.1 directly) has no public
    identity to resolve and is genuinely internal, so it stays exempt.
    """
    raw_peer = handler.client_address[0]
    if VOLTA_TRUST_PROXY:
        forwarded = (handler.headers.get("X-Forwarded-For") or "").strip()
        if forwarded:
            first_hop = forwarded.split(",")[0].strip()
            if first_hop:
                return first_hop
    if _is_loopback_peer(raw_peer):
        return None
    return raw_peer


def _check_rate_limit(key: str, limit_rpm: int) -> Tuple[bool, float]:
    """Token-bucket check for `key` (already route-qualified). Returns
    (allowed, retry_after_seconds). Refill rate = limit_rpm per 60s; burst = limit_rpm."""
    now = time.time()
    capacity = float(limit_rpm)
    refill_per_second = capacity / 60.0
    with _CACHE_LOCKS["rate_limit"]:
        tokens, last_ts = RATE_BUCKETS.get(key, (capacity, now))
        tokens = min(capacity, tokens + max(0.0, now - last_ts) * refill_per_second)
        if tokens >= 1.0:
            RATE_BUCKETS[key] = (tokens - 1.0, now)
            allowed, retry_after = True, 0.0
        else:
            RATE_BUCKETS[key] = (tokens, now)
            allowed = False
            retry_after = (1.0 - tokens) / refill_per_second if refill_per_second > 0 else 60.0
        if len(RATE_BUCKETS) > RATE_BUCKETS_MAX_ENTRIES:
            for stale_key in list(RATE_BUCKETS.keys())[: RATE_BUCKETS_MAX_ENTRIES // 10]:
                RATE_BUCKETS.pop(stale_key, None)
    return allowed, retry_after


# Global upstream-fetch concurrency bound (see fetch_primary_sources_parallel). A short
# bounded wait, not an unbounded queue: a saturated server should fail closed fast
# rather than pile up threads/sockets against MapaREVE/Gaia indefinitely.
LIVE_FETCH_CONCURRENCY_LIMIT = max(1, int(os.environ.get("VOLTA_MAX_CONCURRENT_LIVE", "4")))
LIVE_FETCH_SEMAPHORE = threading.BoundedSemaphore(LIVE_FETCH_CONCURRENCY_LIMIT)
LIVE_FETCH_SEMAPHORE_ACQUIRE_TIMEOUT_SECONDS = 2.0

# CORS stays wide open by default (the relay-abuse concern is mitigated by the rate
# limiter above; narrowing CORS is defense-in-depth, not the fix) but is now env-gated
# so an owner who DOES want to lock it to a single origin can, without a code change.
CORS_ORIGIN = os.environ.get("VOLTA_CORS_ORIGIN", "*")
# Extra browser origins allowed to call POST /mcp besides HTTPS and loopback origins
# (comma-separated, exact match). Server-side MCP clients send no Origin at all.
MCP_ALLOWED_ORIGINS = tuple(
    origin.strip() for origin in os.environ.get("VOLTA_MCP_ALLOWED_ORIGINS", "").split(",") if origin.strip()
)
MCP_RATE_LIMIT_RPM = max(1, int(os.environ.get("VOLTA_MCP_RATE_LIMIT_RPM", "30")))
# Content-Security-Policy is OFF by default: it can break MapLibre's worker/tile
# loading and this build cannot verify that in a browser in every environment. Empty
# = no CSP header at all. The owner sets the full directive string at deploy time
# after a real browser check (see AGENTS.md §8 for a starting-point policy).
VOLTA_CSP = os.environ.get("VOLTA_CSP", "").strip()

# --- Worldwide viewport model; regional live feeds retain their own coverage ---
# Volta is now location/viewport-driven: /api/chargers fetches chargers near a bbox (the map
# viewport, the user's location, or a searched place) worldwide. Regional live adapters
# retain their own coverage; DEFAULT_BBOX is used only when no viewport is given.
# Mainland, Balearics, Canaries, Ceuta, and Melilla. The previous 35.85/-9.45
# envelope inverted Canary and Melilla viewports (sw>ne) and dropped those leads.
WORLD_BBOX = {"latitude_ne": 90.0, "longitude_ne": 180.0, "latitude_sw": -90.0, "longitude_sw": -180.0}
SPAIN_BBOX = {"latitude_ne": 44.5, "longitude_ne": 5.0, "latitude_sw": 27.0, "longitude_sw": -18.5}
DEFAULT_BBOX = VALENCIA_BBOX
VIEWPORT_MAX_SPAN_DEG = float(os.environ.get("VOLTA_VIEWPORT_MAX_SPAN", "1.2"))
VIEWPORT_DEFAULT_RADIUS_KM = float(os.environ.get("VOLTA_VIEWPORT_RADIUS_KM", "25"))
MAPAREVE_TILE_DEG = float(os.environ.get("VOLTA_MAPAREVE_TILE_DEG", "0.25"))

PRICE_MAX_DEFAULT = 0.25
LIVE_ROUTE_READY_MINIMUM = int(os.environ.get("VOLTA_LIVE_ROUTE_READY_MINIMUM", "3"))
ENGINE_VERSION = "2026-07-09.parallel-live-v2"


def _load_madrid_tz() -> Any:
    """Guarded Madrid timezone load. Two independent failure modes must both fall
    back to UTC instead of crash-looping the server at import time: (1) the zoneinfo
    module itself is unavailable (Python <3.9 -- ZoneInfo is None), and (2) zoneinfo
    IS importable but the OS has no tz database installed (e.g. python:3.12-slim with
    no tzdata package -- ZoneInfo("Europe/Madrid") raises ZoneInfoNotFoundError). A
    UTC fallback shifts overnight pricing-window boundaries by 1-2h, so this is a
    safety net, not a substitute for the Dockerfile installing tzdata."""
    if not ZoneInfo:
        return timezone.utc
    try:
        return ZoneInfo("Europe/Madrid")
    except Exception:
        return timezone.utc


MADRID_TZ = _load_madrid_tz()
LIVE_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "APP-VERSION": "1.0.0",
    "Accept-Language": "en",
    "Time-Zone": "Europe/Madrid",
    "platform": "web",
    # Browser-like UA + Referer/Origin: mapareve.es sits behind Imperva, which can
    # serve partial/blocked responses to non-browser clients. These reduce that risk.
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Referer": "https://www.mapareve.es/mapa-puntos-recarga",
    "Origin": "https://www.mapareve.es",
}
GAIA_HEADERS = {
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en",
    "User-Agent": "Mozilla/5.0 (Volta research prototype; public Gaia jump page parser)",
}

TESLA_OK_STANDARDS = {"IEC_62196_T2", "IEC_62196_T2_COMBO", "TESLA_R"}
FAST_STANDARDS = {"IEC_62196_T2_COMBO", "CHADEMO", "TESLA_R"}
TESLA_OK_LABELS = {"Type 2", "CCS2", "Tesla CCS", "Tesla"}
ACCESS_LIMIT_RE = re.compile(r"private|staff|visitor|customer|notice|required|membership|members", re.I)
GAIA_ALLOWED_HOSTS = {"partner-next.gaiacharge.com", "partner.gaiacharge.com"}
BUSY_STATUSES = {"CHARGING", "PREPARING", "RESERVED", "SUSPENDEDEV", "SUSPENDEDEVSE", "FINISHING", "OCCUPIED"}
OUT_STATUSES = {"OUTOFORDER", "OUT_OF_ORDER", "INOPERATIVE", "FAULTED", "UNAVAILABLE", "BLOCKED", "REMOVED"}
BAD_STATION_NAME_RE = re.compile(r"\b(?:n\s*['’]?\s*existe\s+pas|nexiste\s+pas|no\s+existe|does\s+not\s+exist|nonexistent|removed|inexistant|supprim[ée]e?|d[ée]mont[ée]e?|demontee|doublon|duplicate)\b", re.I)
BAD_SOURCE_STATUS_RE = re.compile(r"\b(?:operational_status\s*=\s*(?:OUT_OF_ORDER|INOPERATIVE|REMOVED|CLOSED)|availability_status\s*=\s*UNAVAILABLE|offline_status\s*=\s*NOT_WORKING)\b", re.I)
INDOOR_PARKING_NAME_RE = re.compile(r"\b(?:parking\s+interior|garaje|garage|aparcamiento|aparcament|car\s*park|underground\s+parking)\b", re.I)
RETAIL_PARKING_NAME_RE = re.compile(r"\b(?:centro\s+comercial|centre\s+comercial|\bcc\b|mall|alcampo|bonaire|mercadona|consum|lidl|carrefour|ikea|bauhaus|aldi|eroski|decathlon|ahorramas|intermarch[eé]|verdecora|leroy\s+merlin|supermercados?|puerto\s+venecia)\b", re.I)
FACILITY_PARKING_NAME_RE = re.compile(r"\b(?:colegio|school|campus|universidad|university|hospital|polideportivo|sports\s+centre|estadio|stadium|club\s+n[aá]utico|nautic|golf|gasolinera|estaci[oó]n\s+de\s+servicio|service\s+station|camping|aptos?|apartamentos?|estaci[oó]n\s+de\s+renfe|renfe|train\s+station|cofrad[ií]a|recinto\s+portuario|portuario|pescadores|punto\s+limpio|oficinas?)\b", re.I)
DEAD_SOURCE_SLUGS = {
    # Verified 2026-06-22 by scripts/qa_source_links.py: Chargemap returns HTTP 404.
    "chargemap.com/casa-ermita.html",
}

PARKING_LABELS = {
    "paid_or_metered": "Paid/conditional parking",
    "time_limited_or_validated": "Limited free parking",
    "free_parking": "Free parking",
    "retail_parking": "Retail/customer parking",
    "facility_or_customer_parking": "Facility/customer parking",
    "street_or_municipal": "Street/municipal",
    "unknown": "Parking unknown",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _log_upstream_error(context: str, exc: BaseException) -> None:
    """Server-side-only detail for an upstream/internal exception. Client-visible
    payloads carry the exception CLASS name only (see the public-hardening
    warning/error sanitization contract); this is the one place the full
    exception text -- which can carry upstream-internal detail -- is recorded."""
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {context}: {type(exc).__name__}: {exc}")


def route_ready_now(station: Dict[str, Any]) -> bool:
    if station.get("cache_stale") is True:
        return False
    decision = station.get("decision") or {}
    if decision.get("level") != "go" or station.get("lead"):
        return False
    verification = station.get("verification") or {}
    if verification.get("live_verified") is not True:
        return False
    level = str(verification.get("level") or "")
    if level and level != "live":
        return False
    summary = station.get("connector_summary") or {}
    available = safe_int(summary.get("available")) if isinstance(summary, dict) else None
    if available is None:
        available = safe_int(station.get("available"))
    if available is not None:
        return available > 0
    status = normalize_status(station.get("status"))
    return status == "AVAILABLE"


def live_coverage_profile(stations: List[Dict[str, Any]], leads: List[Dict[str, Any]], *, minimum: int = LIVE_ROUTE_READY_MINIMUM) -> Dict[str, Any]:
    """Describe whether current live coverage is enough for route-first use."""
    live_count = sum(1 for station in stations if route_ready_now(station))
    lead_count = len(leads)
    profile = {
        "mode": "standard",
        "is_degraded": False,
        "live_route_ready": live_count,
        "lead_count": lead_count,
        "minimum_live_route_ready": minimum,
        "summary": f"{live_count} live route-ready stations under the current cap.",
        "warning": None,
    }
    if live_count >= minimum:
        return profile

    profile["mode"] = "degraded"
    profile["is_degraded"] = True
    if lead_count:
        profile["summary"] = f"Low live coverage: {live_count}/{minimum} route-ready live stations; showing {lead_count} source-backed leads."
        profile["warning"] = f"Low live coverage: {live_count}/{minimum} live stations under cap. Leads need provider-source verification before driving."
    else:
        profile["summary"] = f"Low live coverage: {live_count}/{minimum} route-ready live stations and no leads loaded."
        profile["warning"] = f"Low live coverage: {live_count}/{minimum} live stations under cap. Refresh or widen filters before routing."
    return profile


def haversine_km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lon - a_lon)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(x), math.sqrt(1 - x))


def safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            value = value.strip().replace(",", ".")
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def safe_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(float(str(value).strip().replace(",", ".")))
    except Exception:
        return None


def _clamp_max_price(value: float) -> float:
    """Bound + snap a parsed max_price so query-string permutations cannot mint
    unlimited distinct cache keys against the single-slot live/overview caches
    (each request otherwise thrashes CACHE/OVERVIEW_BYTES_CACHE into a fresh
    upstream fetch). Only bounds an already-resolved float; _parse_max_price owns
    the missing/invalid fallback used by every public request seam."""
    return round(min(max(value, 0.0), 5.0), 2)


def _parse_max_price(value: Any) -> float:
    """Parse the public filter, defaulting missing, invalid, or non-finite input.

    Exact zero is a meaningful free-only cap and must reach _clamp_max_price rather
    than being treated as falsy and replaced with PRICE_MAX_DEFAULT.
    """
    parsed = safe_float(value)
    if parsed is None or not math.isfinite(parsed):
        parsed = PRICE_MAX_DEFAULT
    return _clamp_max_price(parsed)


def parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    # Normalize a basic-format tz offset ("+0000", "-0530") to extended ("+00:00", "-05:30");
    # Electromaps and some feeds emit the basic form, which datetime.fromisoformat rejected,
    # leaving freshness unknown for those rows.
    s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def age_minutes(value: Any) -> Optional[int]:
    parsed = parse_iso(value)
    if not parsed:
        return None
    if parsed.tzinfo is None:
        # parse_iso returns a timezone-NAIVE datetime for offset-less input (some
        # Electromaps/community feed timestamps omit the offset). Coerced to UTC only
        # here, not in parse_iso: parse_iso also feeds pricing_window_is_current-adjacent
        # code, which already does its own local-time handling and never subtracts an
        # aware datetime from its result, so widening parse_iso's return type is a bigger
        # blast radius than this defect needs (D-143).
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, round((datetime.now(timezone.utc) - parsed).total_seconds() / 60))


def freshness_timestamp(item: Dict[str, Any]) -> Any:
    """Timestamp used only for the displayed/scored freshness fact.

    A lead uses its oldest valid evidence timestamp across station observation and
    public-source fetch time. This is intentionally conservative for merged rows:
    fresher donor telemetry must not make older lead evidence look new. Live rows
    use genuine last_seen only. Neither source field is mutated, so live-staleness
    and Electromaps' telemetry-age policy keep their original semantics.
    """
    last_seen = item.get("last_seen")
    if not item.get("lead"):
        return last_seen

    candidates: List[Tuple[datetime, Any]] = []
    for value in (last_seen, item.get("source_fetched_at")):
        try:
            parsed = parse_iso(value)
            if parsed is None:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            else:
                parsed = parsed.astimezone(timezone.utc)
        except (OverflowError, ValueError):
            continue
        candidates.append((parsed, value))
    return min(candidates, key=lambda candidate: candidate[0])[1] if candidates else None


def text_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def compact_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def strip_tags(fragment: str) -> str:
    fragment = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", fragment or "")
    fragment = re.sub(r"(?i)</\s*(?:p|div|li|h[1-6])\s*>", "\n", fragment)
    fragment = re.sub(r"(?s)<[^>]+>", " ", fragment)
    lines = [compact_text(html_lib.unescape(line)) for line in fragment.splitlines()]
    return compact_text(" ".join(line for line in lines if line))


def visible_text_from_html(document: str) -> str:
    document = re.sub(r"(?is)<script\b[^>]*>.*?</script>", " ", document or "")
    document = re.sub(r"(?is)<style\b[^>]*>.*?</style>", " ", document)
    return strip_tags(document)


def resolve_public_url(base_url: str, maybe_path: Any) -> Optional[str]:
    if not maybe_path:
        return None
    url = urllib.parse.urljoin(base_url, str(maybe_path))
    return url if urllib.parse.urlparse(url).scheme in {"http", "https"} else None


def normalize_status(value: Any) -> str:
    status = re.sub(r"[^A-Z0-9]+", "_", str(value or "UNKNOWN").strip().upper()).strip("_")
    aliases = {
        "OUTOFORDER": "OUTOFORDER",
        "OUT_OF_ORDER": "OUTOFORDER",
        "OUT_OF_SERVICE": "OUTOFORDER",
        "OUTOFSERVICE": "OUTOFORDER",
        "WORKING": "AVAILABLE",
        "OPEN": "AVAILABLE",
        "OFFLINE": "UNAVAILABLE",
        "IN_USE": "OCCUPIED",
        "INUSE": "OCCUPIED",
        "BUSY": "OCCUPIED",
    }
    return aliases.get(status, status or "UNKNOWN")


def connector_status_group(status: Any) -> str:
    normalized = normalize_status(status)
    if normalized == "AVAILABLE":
        return "available"
    if normalized in BUSY_STATUSES:
        return "busy"
    if normalized in OUT_STATUSES:
        return "out"
    return "unknown"


def reject_station_reason(item: Dict[str, Any]) -> Optional[str]:
    """Reject stale/nonexistent charger leads before they reach the map.

    Free directory flags are not enough: if the source itself says the pool is
    unavailable/out of order or the name says it does not exist, Volta should not
    show it as a usable candidate.
    """
    searchable = " ".join(str(x or "") for x in [item.get("name"), item.get("address"), item.get("city")])
    evidence = " ".join(
        [searchable, str(item.get("insight") or ""), str(item.get("free_condition") or "")]
        + [str(s.get("evidence") or "") for s in item.get("sources") or [] if isinstance(s, dict)]
    )
    if BAD_STATION_NAME_RE.search(searchable) or BAD_STATION_NAME_RE.search(evidence):
        return "source marks station as nonexistent/removed"
    urls = [str(item.get("source_url") or "")] + [str(s.get("url") or "") for s in item.get("sources") or [] if isinstance(s, dict)]
    if any(slug in url.lower() for slug in DEAD_SOURCE_SLUGS for url in urls):
        return "source URL is verified dead"
    status = normalize_status(item.get("status"))
    if status in OUT_STATUSES:
        return f"station status is {status}"
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    operational = normalize_status(dict_pick(raw, "operational_status", "operationalStatus"))
    availability = normalize_status(dict_pick(raw, "availability_status", "availabilityStatus"))
    offline = normalize_status(dict_pick(raw, "offline_status", "offlineStatus"))
    if operational in OUT_STATUSES:
        return f"source operational_status is {operational}"
    if offline in {"NOT_WORKING", "OUTOFORDER", "UNAVAILABLE"}:
        return f"source offline_status is {offline}"
    if availability == "UNAVAILABLE" and not (safe_int(item.get("available")) or safe_int(item.get("charging"))):
        return "source availability_status is UNAVAILABLE"
    if BAD_SOURCE_STATUS_RE.search(evidence):
        return "source evidence says unavailable/out of order"
    return None


def source_label(item: Dict[str, Any]) -> str:
    reliability = item.get("reliability") or ""
    if reliability == "official-live-public-page":
        source_type = str(item.get("source_type") or "")
        if "gaia" in source_type:
            return "Gaia live page"
        return "Live provider page"
    labels = {
        "official-live": "Live OCPI",
        "official-live-public-page": "Live provider page",
        "official-cheap": "Official city",
        "official-free-policy": "Official free policy",
        "official-charger-location-price-unknown": "Official location lead",
        "directory-map-free": "Map-scraped free",
        "directory-map-cheap": "Map-scraped cheap",
        "community-free": "Community free",
        "community-cost": "Community cost",
        "community-access-limited": "Access-limited lead",
        "directory-page": "Directory page",
        "manual-check-needed": "Manual lead",
        "stale-price-lead": "Stale lead",
        "convenience-fallback-not-cheap": "Fallback",
    }
    return labels.get(reliability, str(item.get("source") or "Source"))


def access_profile(item: Dict[str, Any]) -> Dict[str, str]:
    if item.get("access"):
        return item["access"]
    evidence = " ".join(
        [str(item.get("free_condition") or ""), str(item.get("insight") or "")]
        + [str(s.get("evidence") or "") for s in item.get("sources") or [] if isinstance(s, dict)]
    )
    parking_kind = (item.get("parking") or {}).get("kind")
    if ACCESS_LIMIT_RE.search(evidence):
        return {
            "level": "restricted",
            "label": "Access caveat",
            "note": "Source mentions customer/member/private/notice-required access. Verify in the provider app or onsite before driving.",
        }
    if item.get("reliability") in {"official-live", "official-live-public-page", "official-cheap", "official-free", "official-free-policy"}:
        return {
            "level": "public",
            "label": "Public",
            "note": "Public-source evidence does not show an access restriction; onsite signs still win.",
        }
    if parking_kind in {"retail_parking", "facility_or_customer_parking"}:
        return {
            "level": "customer",
            "label": "Customer/visitor parking",
            "note": "Likely customer or visitor parking. Good for planned stops, less reliable as a pure charging trip.",
        }
    return {
        "level": "unknown",
        "label": "Access unknown",
        "note": "Access and parking rules are not fully published by this source.",
    }


def normalize_parking_profile(item: Dict[str, Any]) -> Dict[str, Any]:
    parking = dict(item.get("parking") or {})
    kind = str(parking.get("kind") or "unknown")
    if kind not in PARKING_LABELS:
        kind = "unknown"
    text = " ".join(
        str(part or "")
        for part in [
            item.get("name"),
            item.get("address"),
            item.get("insight"),
            " ".join(str((source or {}).get("evidence") or "") for source in item.get("sources") or [] if isinstance(source, dict)),
        ]
    )
    if kind == "street_or_municipal" and INDOOR_PARKING_NAME_RE.search(text):
        kind = "unknown"
        parking["paid"] = False
        parking["free_likelihood"] = "unknown"
        parking["note"] = (
            "Station name/source suggests indoor or garage parking, but no parking tariff is published. "
            "Treat parking as unknown until provider page or onsite signs confirm free access."
        )
    elif kind == "street_or_municipal" and RETAIL_PARKING_NAME_RE.search(text):
        kind = "retail_parking"
        parking["paid"] = False
        parking["free_likelihood"] = "medium"
        parking["note"] = (
            "Station name/source suggests retail or customer parking rather than street access. "
            "Verify opening hours, validation and parking signs onsite."
        )
    elif kind == "street_or_municipal" and FACILITY_PARKING_NAME_RE.search(text):
        kind = "facility_or_customer_parking"
        parking["paid"] = False
        parking["free_likelihood"] = "medium"
        parking["note"] = (
            "Station name/source suggests facility, visitor or customer parking rather than ordinary street access. "
            "Verify access rules and parking signs onsite."
        )
    parking["kind"] = kind
    parking["label"] = PARKING_LABELS[kind]
    parking.setdefault("paid", kind == "paid_or_metered")
    if kind == "paid_or_metered":
        parking.setdefault("free_likelihood", "low")
        parking.setdefault("peak_hint", "Parking cost can erase the benefit of a free or cheap charging tariff.")
    elif kind == "time_limited_or_validated":
        parking.setdefault("free_likelihood", "conditional")
        parking.setdefault("peak_hint", "Plan around the free window or purchase validation; slow AC sessions may overrun it.")
    elif kind == "free_parking":
        parking.setdefault("free_likelihood", "high")
        parking.setdefault("peak_hint", "Free parking claim is source-published, but bay occupancy is still not guaranteed.")
    elif kind in {"retail_parking", "facility_or_customer_parking", "street_or_municipal"}:
        parking.setdefault("free_likelihood", "medium")
    else:
        parking.setdefault("free_likelihood", "unknown")
    parking.setdefault("note", "Parking cost not published by this source; check onsite signs before leaving the car.")
    parking.setdefault("peak_hint", "No parking telemetry; keep a backup within 10–15 minutes.")
    return parking


def ensure_claim_contract(item: Dict[str, Any]) -> None:
    primary = item.get("primary_source") or {}
    source_name = item.get("source_label") or item.get("source") or primary.get("name") or "Source"
    source_url = primary.get("url") or item.get("source_url")
    if not item.get("charge_claim"):
        if item.get("is_free"):
            claim_type = "free"
        elif item.get("is_cheap"):
            claim_type = "cheap"
        elif item.get("price_min") is None:
            claim_type = "price_unknown"
        else:
            claim_type = "off_cap"
        item["charge_claim"] = {
            "type": claim_type,
            "source": source_name,
            "url": source_url,
            "price_min": item.get("price_min"),
            "summary": item.get("free_condition") or item.get("price_label") or "Charging tariff claim from source data.",
        }
    if not item.get("parking_claim"):
        parking = item.get("parking") or {}
        item["parking_claim"] = {
            "kind": parking.get("kind") or "unknown",
            "source": source_name,
            "url": source_url,
            "cost_hint": parking.get("cost_hint"),
            "summary": parking.get("note") or "Parking cost/access claim from source data.",
        }
    else:
        parking = item.get("parking") or {}
        claim = item["parking_claim"]
        if parking.get("kind") and claim.get("kind") != parking.get("kind"):
            claim["kind"] = parking.get("kind")
            claim["cost_hint"] = parking.get("cost_hint")
            claim["summary"] = parking.get("note") or "Parking cost/access claim from source data."
    charge_url = str((item.get("charge_claim") or {}).get("url") or "").rstrip("/")
    parking_url = str((item.get("parking_claim") or {}).get("url") or "").rstrip("/")
    parking_kind = (item.get("parking") or {}).get("kind")
    if charge_url and parking_url and charge_url != parking_url and parking_kind in {"paid_or_metered", "time_limited_or_validated"}:
        item["claim_conflict"] = {
            "level": "mixed_sources",
            "summary": "Charging price and parking condition come from different public sources; verify both before routing.",
            "charge_url": charge_url,
            "parking_url": parking_url,
        }


def verification_profile(item: Dict[str, Any], *, live: bool) -> Dict[str, Any]:
    reliability = str(item.get("reliability") or "")
    snapshot_location = ((item.get("location_claim") or {}).get("method") == "public_source_coordinate"
                         and source_health_has_valid_coordinates(item))
    # Compatibility for candidate bundles built before the directory tier was named.
    if (reliability == "community-price-unknown" and snapshot_location
            and (item.get("source_integration") or {}).get("mode") == "community_snapshot"
            and urllib.parse.urlsplit(str(item.get("source_url") or "")).hostname in {"map.openchargemap.io", "openchargemap.org", "www.openchargemap.org"}):
        reliability = item["reliability"] = "directory-community-snapshot"
    if reliability == "official-snapshot" and snapshot_location:
        profile = {"level": "official-lead", "label": "Official location snapshot",
                   "note": "Official dataset coordinates identify this charger; tariff and current availability require operator confirmation."}
    elif reliability == "directory-community-snapshot" and snapshot_location:
        profile = {"level": "directory", "label": "Community directory snapshot",
                   "note": "Community-maintained public directory coordinates identify this charger; tariff and current availability are unverified."}
    elif live and reliability == "official-live":
        profile = {
            "level": "live",
            "label": "Live verified",
            "note": "Live OCPI source has current tariff/status fields, but provider app and onsite signs still override it.",
        }
    elif live and reliability == "official-live-public-page":
        profile = {
            "level": "live",
            "label": "Live public page",
            "note": "Public operator jump page exposes current connector status/tariff fields. Provider app and onsite signs still override it.",
        }
    elif live and reliability == "official-live-aggregator":
        profile = {
            "level": "live",
            "label": "Live (aggregated)",
            "note": "Electromaps relays the operator's real-time connector status (realtime feed); only telemetry-fresh connectors are shown. Provider app and onsite signs still override it.",
        }
    elif reliability in {"official-cheap", "official-free", "official-free-policy"}:
        profile = {
            "level": "official",
            "label": "Official source",
            "note": "Official/public owner source confirms the charger or policy; occupancy is not live.",
        }
    elif reliability == "official-charger-location-price-unknown":
        profile = {
            "level": "official-lead",
            "label": "Official lead",
            "note": "Official locator confirms a charger, but tariff must be checked in the provider app.",
        }
    elif reliability.startswith("directory-map") or reliability == "directory-page":
        profile = {
            "level": "directory",
            "label": "Directory check",
            "note": "Public directory evidence is useful but not authoritative; verify before driving.",
        }
    elif reliability.startswith("community"):
        profile = {
            "level": "community",
            "label": "Community lead",
            "note": "Community data can be stale; treat this as a lead until confirmed in the provider app.",
        }
    else:
        profile = {
            "level": "unverified",
            "label": "Unverified",
            "note": "This record needs manual confirmation before relying on it.",
        }
    level = profile["level"]
    parking_kind = (normalize_parking_profile(item).get("kind") or "unknown")
    profile["live_verified"] = level == "live"
    profile["location_verified"] = level in {"live", "official", "official-lead", "directory"}
    profile["price_verified"] = level in {"live", "official"} and item.get("price_min") is not None
    profile["parking_verified"] = parking_kind != "unknown" and (level in {"live", "official"} or bool((item.get("parking") or {}).get("cost_hint")))
    profile["route_checked"] = bool(profile["price_verified"] and profile["location_verified"] and level in {"live", "official"})
    return profile


def tesla_compatibility(item: Dict[str, Any]) -> Optional[bool]:
    """True/False only when the source names a connector type; None when it doesn't.

    A source that lists a site without saying which sockets it has (Lidl's store
    list, a "connector unspecified" wall outlet) is unknown, not incompatible.
    """
    if str(item.get("country_code") or item.get("country") or "").upper() not in {"", "ES", "ESP"}:
        return None  # Vehicle model, market and adapter support are not established by socket presence.
    if item.get("tesla_ok") is True:
        return True
    labels = [str(x) for x in item.get("connector_labels") or []]
    labels += [str(c.get("label")) for c in item.get("connectors") or [] if isinstance(c, dict) and c.get("label")]
    if any(c.get("tesla_ok") for c in item.get("connectors") or [] if isinstance(c, dict)) or any(x in TESLA_OK_LABELS for x in labels):
        return True
    known = [x for x in labels if x.strip() and "unspecified" not in x.lower() and x.strip().lower() not in {"unknown", "other"}]
    return False if known else None


def decision_for(item: Dict[str, Any], *, live: bool) -> Dict[str, str]:
    # The source itself reports the charger out of service: keep it on the map so drivers
    # know it exists, but never offer it as a destination, whatever its price or live data.
    outage = item.get("source_reported_unavailable")
    if isinstance(outage, dict) and outage:
        reason = str(outage.get("summary") or "The source reports this charger unavailable.")
        return {"level": "avoid", "label": "Unavailable", "reason": reason}
    access = access_profile(item)
    parking = normalize_parking_profile(item)
    parking_kind = parking.get("kind")
    parking_risky = bool(parking.get("paid") or parking_kind in {"paid_or_metered", "time_limited_or_validated"})
    price_ok = bool(item.get("is_free") or item.get("is_cheap"))
    price_window_status = item.get("price_window_status") or {}
    has_time_pricing = bool(item.get("pricing"))
    price_ok_now = price_ok and (price_window_status.get("is_currently_within_standard", True) if has_time_pricing else True)
    next_window = price_window_status.get("next_matching_window") or {}
    window_label = next_window.get("label") or price_window_status.get("label")
    tesla_ok = item.get("tesla_ok") is True
    available = bool(item.get("available", 0) > 0 or item.get("status") == "AVAILABLE")
    stale_mins = age_minutes(item.get("last_seen"))
    stale_live = live and stale_mins is not None and stale_mins > 180

    if live and price_ok and not price_ok_now and tesla_ok:
        reason = f"Matches the price standard only in a tariff window ({window_label}); keep it on the map, but verify current app price before routing now."
        return {"level": "check", "label": "Cheap later", "reason": reason}
    if live and price_ok_now and tesla_ok and available and not stale_live and parking_risky:
        return {"level": "check", "label": "Check parking", "reason": f"Live cheap/free charger, but source indicates {parking.get('label', 'paid parking').lower()}; open source before routing."}
    if live and price_ok_now and tesla_ok and available and not stale_live:
        reason = "Live status, Tesla-compatible connector, and price is within the cap right now."
        return {"level": "go", "label": "Go now", "reason": reason}
    if live and price_ok_now and tesla_ok and available:
        return {"level": "check", "label": "Check first", "reason": "Live feed is cheap and available, but the last station timestamp is old."}
    if live and price_ok_now and tesla_ok:
        return {"level": "check", "label": "Check first", "reason": "Cheap and Tesla-compatible, but no currently available bay is confirmed."}
    if item.get("tesla_ok") is False:
        return {"level": "avoid", "label": "Not Tesla-safe", "reason": "The source lists only connectors a Tesla can't use (no CCS2, Type 2 or Tesla)."}
    if not price_ok:
        # Paid and unpriced chargers stay on the map as known places, ranked after every
        # free/cheap option. They are never green: the price standard is not met or unknown.
        price = safe_float(item.get("price_min"))
        if price is None:
            tariff = item.get("tariff_details") or {}
            hourly = safe_float(tariff.get("hour_eur"))
            if hourly is not None and hourly >= 0:
                reason = "No unconditional energy price is published. This source lists an hourly tariff; check its conditions before charging."
            elif tariff.get("text") or tariff.get("components") or tariff.get("conditions") or tariff.get("minimum_eur") is not None:
                reason = "The source publishes tariff conditions but no unconditional price per kWh; check its conditions before charging."
            else:
                reason = "No source publishes a tariff for this charger; check the price in the operator app before charging."
            return {"level": "lead", "label": "Price lead", "reason": reason}
        return {"level": "lead", "label": "Paid", "reason": f"Charging costs €{price:.2f}/kWh, above the €{PRICE_MAX_DEFAULT:.2f}/kWh free/cheap standard; confirm the price in the operator app."}
    if access.get("level") in {"restricted", "unknown"}:
        return {"level": "verify", "label": "Verify first", "reason": access["note"]}
    if parking_risky:
        return {"level": "verify", "label": "Check parking", "reason": f"Charging may be free/cheap, but the parking itself is published as paid or conditional: {parking.get('note')}"}
    if item.get("reliability") == "official-charger-location-price-unknown":
        return {"level": "lead", "label": "Price lead", "reason": "Official store locator confirms a charger, but not the tariff."}
    return {"level": "verify", "label": "Verify first", "reason": "Good cheap/free evidence, but this is not live occupancy telemetry."}


def attach_connector_summary(item: Dict[str, Any]) -> Dict[str, Any]:
    connectors = item.get("connectors") or []
    available = busy = charging = out = unknown = 0
    power_values: List[float] = []
    connector_details: List[str] = []

    for connector in connectors:
        status = normalize_status(connector.get("status"))
        group = connector_status_group(status)
        if group == "available":
            available += 1
        elif group == "busy":
            busy += 1
            if status == "CHARGING":
                charging += 1
        elif group == "out":
            out += 1
        else:
            unknown += 1

        power_kw = safe_float(connector.get("power_kw"))
        if power_kw:
            power_values.append(power_kw)
        label = connector.get("label") or connector.get("type") or connector.get("standard") or "connector"
        ref = connector.get("physical_reference") or connector.get("connector_id")
        parts = [str(ref)] if ref else []
        parts.append(str(label))
        if power_kw:
            parts.append(f"{power_kw:g} kW")
        session_power = safe_float(connector.get("session_power_kw"))
        if session_power:
            parts.append(f"now {session_power:g} kW")
        parts.append(status.lower().replace("_", " "))
        connector_details.append(" ".join(parts))

    total = len(connectors)
    if not total:
        total = safe_int(item.get("connector_count")) or safe_int(item.get("socket_count")) or safe_int(item.get("total_evse")) or 0
        available = safe_int(item.get("available")) or 0
        charging = safe_int(item.get("charging")) or 0
        busy = safe_int(item.get("busy")) or charging
        out = safe_int(item.get("out_of_service")) or 0
        unknown = safe_int(item.get("unknown")) or max(0, total - available - busy - out)
        power_kw = safe_float(item.get("max_power_kw"))
        if power_kw:
            power_values.append(power_kw)

    integration = item.get("source_integration") or {}
    snapshot_mode = integration.get("mode") in {
        "community_snapshot", "community_tags", "official_snapshot", "public_map_snapshot",
    } or (item.get("lead") is True and integration.get("live_status_integrated") is False)
    if snapshot_mode and connectors:
        counts = [safe_float(connector.get("count")) for connector in connectors]
        complete = all(value is not None and value > 0 and value.is_integer() for value in counts)
        total = sum(int(value) for value in counts) if complete else None
        # These rows describe hardware inventory, never per-port occupancy telemetry.
        available = busy = charging = out = 0
        unknown = total or 0
        item["total_evse"] = total

    summary = {
        "available": available,
        "busy": busy,
        "charging": charging,
        "out_of_service": out,
        "unknown": unknown,
        "total": total or None,
        "max_power_kw": round(max(power_values), 1) if power_values else item.get("max_power_kw"),
        "details": connector_details[:8],
    }
    item["connector_summary"] = summary
    item["connector_count"] = summary["total"]
    item["socket_count"] = summary["total"]
    item["available_connectors"] = available
    item["busy_connectors"] = busy
    item["charging_connectors"] = charging
    item["out_of_service_connectors"] = out
    item["unknown_connectors"] = unknown
    if connectors:
        item["available"] = available
        item["charging"] = charging
        item["out_of_service"] = out
        item["unknown"] = unknown
        item["total_evse"] = item.get("total_evse") or summary["total"]
    if summary["max_power_kw"] is not None:
        item["max_power_kw"] = summary["max_power_kw"]
    return item


def station_currency(item: Dict[str, Any]) -> str:
    explicit = str(item.get("price_currency") or item.get("currency") or "").upper()
    country = str(item.get("country_code") or item.get("country") or "").upper()
    return explicit or ("EUR" if country in {"", "ES", "ESP"} else "UNKNOWN")


def euro_price(item: Dict[str, Any], field: str = "price_min") -> Optional[float]:
    value = safe_float(item.get(field))
    return value if station_currency(item) == "EUR" or value == 0 else None


def enrich_station(item: Dict[str, Any], *, live: bool) -> Dict[str, Any]:
    currency = station_currency(item)
    item["price_currency"] = currency
    if currency != "EUR":
        current_price = safe_float(item.get("current_price_min"))
        if current_price is not None and current_price != 0:
            item.setdefault("current_price_native_min", current_price)
            item["current_price_min"] = None
        price = safe_float(item.get("price_min"))
        if price is not None and price != 0:
            item.setdefault("price_native_min", price)
            item.setdefault("price_native_unit", "kWh")
            item["price_min"] = None
            item["current_price_min"] = None
            item["is_free"] = False
            item["is_cheap"] = False
    attach_connector_summary(item)
    attach_pricing_summary(item)
    if item.get("source_url"):
        item["source_url"] = normalize_public_source_url(item.get("source_url"))
    for source in item.get("sources") or []:
        if isinstance(source, dict) and source.get("url"):
            source["url"] = normalize_public_source_url(source.get("url"))
    item["source_label"] = source_label(item)
    item["parking"] = normalize_parking_profile(item)
    item["access"] = access_profile(item)
    item["primary_source"] = primary_source_for(item)
    attach_google_maps_navigation(item)
    item["verification"] = verification_profile(item, live=live)
    item["tesla_ok"] = tesla_compatibility(item)
    item["decision"] = decision_for(item, live=live)
    if item["tesla_ok"] is None:
        if item.get("connector_labels") or any(c.get("label") for c in item.get("connectors") or [] if isinstance(c, dict)):
            item["decision"]["reason"] += " Confirm connector and vehicle compatibility with the operator."
        else:
            item["decision"]["reason"] += " The source doesn't say which connectors this site has."
    item["freshness_minutes"] = age_minutes(freshness_timestamp(item))
    pricing = item.get("price_window_status") or {}
    item["price_label"] = pricing.get("label") or ("Free" if item.get("is_free") else (f"€{item['price_min']:.2f}/kWh" if isinstance(item.get("price_min"), (int, float)) else "Verify price"))
    native_price = safe_float(item.get("price_native_min"))
    if currency != "EUR" and not item.get("is_free") and native_price is not None and not item.get("pricing"):
        tariff = item.get("tariff") or (item.get("charge_claim") or {}).get("raw_tariff") or {}
        unit = tariff.get("unit") or item.get("price_native_unit")
        if tariff.get("conditional") or tariff.get("additional_components"):
            item["price_label"] = str(tariff.get("raw_text") or "Conditional tariff; verify with operator")
        else:
            suffix = f"/{unit}" if unit else " (unit unconfirmed)"
            item["price_label"] = f"{currency} {native_price:.2f}{suffix}"
    ensure_claim_contract(item)
    item["recommendation_score"] = recommendation_score(item)
    return item


def recommendation_score(item: Dict[str, Any]) -> float:
    score = float(item.get("score") or 0)
    decision = (item.get("decision") or {}).get("level")
    access = (item.get("access") or {}).get("level")
    if item.get("is_free"):
        score += 16
    elif item.get("is_cheap"):
        score += 10
    if item.get("available", 0) > 0:
        score += 18
    if item.get("fast"):
        score += 8
    if item.get("freshness_minutes") is not None:
        score += max(0, 8 - min(item["freshness_minutes"], 240) / 30)
    if item.get("distance_km") is not None:
        score -= min(float(item["distance_km"]), 60) / 3
    if access in {"restricted", "unknown"}:
        score -= 20
    parking = item.get("parking") or {}
    if parking.get("paid") or parking.get("kind") == "paid_or_metered":
        score -= 16
    elif parking.get("kind") == "time_limited_or_validated":
        score -= 8
    elif parking.get("kind") == "unknown":
        score -= 6
    if item.get("lead"):
        score -= 12
    if decision == "go":
        score += 20
    elif decision in {"avoid", "lead"}:
        score -= 12
    # Cross-source corroboration: bounded boost so a station confirmed by 2-3 independent
    # public sources outranks a single-source claim, without drowning live/availability signals.
    n_fam = (item.get("corroboration") or {}).get("independent_sources") or 0
    if n_fam >= 2:
        score += min(12, 4 * (n_fam - 1))
    return round(score, 2)


def nearby_sort_key(item: Dict[str, Any]) -> Tuple[int, int, float, float, str]:
    """Order located results by honesty first, then proximity inside that tier.

    Live and lead rows are still returned in separate API arrays, but keeping source
    honesty in the key makes the invariant explicit if the helper is reused. A nearby
    check/verify row must never jump ahead of a farther route-ready live row; once two
    rows have the same safety tier, literal distance from the requested origin wins.
    Recommendation score is only a stable quality tie-break for equal distances.
    """
    source_tier = 1 if item.get("lead") else 0
    decision = str((item.get("decision") or {}).get("level") or "")
    if route_ready_now(item):
        safety_tier = 0
    else:
        # A malformed/non-route-ready `go` is treated like `check`, never like a safe
        # route-ready result. This keeps sorting fail-closed without rewriting evidence.
        safety_tier = {
            "go": 1,
            "check": 1,
            "verify": 2,
            "lead": 3,
            "avoid": 4,
        }.get(decision, 5)
    distance = safe_float(item.get("distance_km"))
    distance_key = max(0.0, distance) if distance is not None else math.inf
    score = safe_float(item.get("recommendation_score")) or 0.0
    stable_id = str(item.get("id") or item.get("name") or "")
    return source_tier, safety_tier, distance_key, -score, stable_id


def enforce_stale_cache_guard(item: Dict[str, Any]) -> Dict[str, Any]:
    """Make a row from a failed live refresh visibly non-route-ready.

    `last_seen` can be recent even when the current refresh failed, so timestamp-based
    staleness alone is insufficient for the driver's "available now" filter. Fresh
    secondary-source rows do not carry the private marker and remain eligible.
    """
    if not item.pop("_volta_stale_live_cache", False):
        return item
    item["cache_stale"] = True
    verification = dict(item.get("verification") or {})
    verification["live_verified"] = False
    item["verification"] = verification
    if (item.get("decision") or {}).get("level") == "go":
        item["decision"] = {
            "level": "check",
            "label": "Refresh first",
            "reason": "The last live refresh failed, so cached availability must be refreshed before routing.",
        }
    item["recommendation_score"] = recommendation_score(item)
    return item


def tariff_components(connector: Dict[str, Any]) -> List[Dict[str, Any]]:
    components: List[Dict[str, Any]] = []
    for t in connector.get("tariffs") or []:
        tariff = t.get("tariff") or {}
        for element in tariff.get("elements") or []:
            restrictions = element.get("restrictions") or {}
            for pc in element.get("price_components") or []:
                comp = dict(pc)
                comp["restrictions"] = restrictions
                comp["human"] = t.get("human") or []
                comp["currency"] = tariff.get("currency") or t.get("currency") or comp.get("currency") or "EUR"
                components.append(comp)
    return components


def parse_clock(value: Any) -> Optional[dt_time]:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text in {"24:00", "24:00:00"}:
        return dt_time(0, 0)
    try:
        return dt_time.fromisoformat(text[:8] if len(text) >= 8 else text)
    except Exception:
        m = re.search(r"(\d{1,2})[:h](\d{2})", text)
        if not m:
            return None
        hour = int(m.group(1)) % 24
        minute = int(m.group(2)) % 60
        return dt_time(hour, minute)


def clock_to_minutes(value: Any) -> Optional[int]:
    parsed = parse_clock(value)
    if parsed is None:
        return None
    return parsed.hour * 60 + parsed.minute


def day_allowed(days: Any, now: datetime) -> bool:
    if not days:
        return True
    if isinstance(days, str):
        day_values = [days]
    else:
        day_values = list(days) if isinstance(days, Iterable) else []
    if not day_values:
        return True
    normalized = {str(d).strip().upper()[:3] for d in day_values if str(d).strip()}
    today = now.strftime("%a").upper()[:3]
    aliases = {"TUE": "TUE", "THU": "THU", "MON": "MON", "WED": "WED", "FRI": "FRI", "SAT": "SAT", "SUN": "SUN"}
    return aliases.get(today, today) in normalized


def pricing_window_is_current(window: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    local_now = (now or datetime.now(MADRID_TZ)).astimezone(MADRID_TZ)
    if not day_allowed(window.get("days"), local_now):
        return False
    start = clock_to_minutes(window.get("valid_from") or window.get("start_time"))
    end = clock_to_minutes(window.get("valid_until") or window.get("end_time"))
    if start is None and end is None:
        return True
    minute = local_now.hour * 60 + local_now.minute
    if start is None:
        start = 0
    if end is None:
        end = 24 * 60
    if end == 0:
        end = 24 * 60
    if start == end:
        return True
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def pricing_window_label(window: Dict[str, Any]) -> str:
    price = safe_float(window.get("price"))
    currency = str(window.get("currency") or "EUR").upper()
    symbol = "€" if currency == "EUR" else f"{currency} "
    unit = str(window.get("unit") or "kWh").lstrip("/")
    if price == 0:
        price_part = "Free"
    elif price is None:
        price_part = "Verify price"
    else:
        price_part = f"{symbol}{price:.2f}/{unit}"
    start = window.get("valid_from") or window.get("start_time")
    end = window.get("valid_until") or window.get("end_time")
    if start or end:
        return f"{price_part} · {start or '00:00'}–{end or '24:00'}"
    return price_part


def normalize_pricing_window(raw: Dict[str, Any], *, default_unit: str = "kWh", default_currency: str = "EUR") -> Optional[Dict[str, Any]]:
    price = safe_float(dict_pick(raw, "price", "amount", "value"))
    if price is None:
        text = " ".join(str(dict_pick(raw, k) or "") for k in ("label", "human", "text", "tariff_text"))
        if re.search(r"\b(?:free|gratis|gratuit[ao]|sin coste)\b", text, re.I):
            price = 0.0
    if price is None:
        return None
    unit = str(dict_pick(raw, "unit", "billing_unit") or default_unit).replace("ENERGY", "kWh").replace("KWH", "kWh")
    window = {
        "price": round(price, 4),
        "currency": str(dict_pick(raw, "currency") or default_currency or "EUR").upper(),
        "unit": unit or "kWh",
        "valid_from": dict_pick(raw, "valid_from", "start_time", "startTime", "time_start"),
        "valid_until": dict_pick(raw, "valid_until", "end_time", "endTime", "time_end"),
        "days": dict_pick(raw, "days", "day_of_week", "dayOfWeek"),
        "source": dict_pick(raw, "source", "source_name") or "tariff",
        "human": dict_pick(raw, "human", "label", "text"),
    }
    source_url = dict_pick(raw, "source_url", "url")
    if source_url:
        window["source_url"] = source_url
    window["label"] = pricing_window_label(window)
    return window


def connector_pricing_windows(connector: Dict[str, Any], *, price_cap: float = PRICE_MAX_DEFAULT, default_currency: str = "EUR") -> List[Dict[str, Any]]:
    windows: List[Dict[str, Any]] = []
    for raw in connector.get("pricing") or []:
        if isinstance(raw, dict):
            normalized = normalize_pricing_window(raw, default_currency=default_currency)
            if normalized:
                windows.append(normalized)
    for comp in tariff_components(connector):
        if comp.get("type") != "ENERGY":
            continue
        restrictions = comp.get("restrictions") or {}
        raw = {
            "price": comp.get("price"),
            "currency": comp.get("currency") or "EUR",
            "unit": "kWh",
            "valid_from": restrictions.get("start_time") or restrictions.get("startTime"),
            "valid_until": restrictions.get("end_time") or restrictions.get("endTime"),
            "days": restrictions.get("day_of_week") or restrictions.get("dayOfWeek"),
            "source": "OCPI tariff",
            "human": "; ".join(str(h) for h in comp.get("human") or [] if h),
        }
        normalized = normalize_pricing_window(raw)
        if normalized:
            windows.append(normalized)
    if not windows:
        for p in connector.get("energy_prices") or []:
            normalized = normalize_pricing_window({"price": p, "source": "connector price"})
            if normalized:
                windows.append(normalized)
    if not windows and re.search(r"\bfree\b|gratis|gratuit[ao]", str(connector.get("price") or ""), re.I):
        normalized = normalize_pricing_window({"price": 0, "source": "connector label", "label": connector.get("price")})
        if normalized:
            windows.append(normalized)
    for window in windows:
        price = safe_float(window.get("price"))
        window["is_free"] = price == 0
        window["is_within_standard"] = price is not None and price <= price_cap and (price == 0 or str(window.get("currency") or "EUR").upper() == "EUR")
        window["is_currently_valid"] = pricing_window_is_current(window)
        window["label"] = pricing_window_label(window)
    # Deduplicate same price/time/unit windows across EVSEs/connectors.
    deduped: List[Dict[str, Any]] = []
    seen = set()
    for window in windows:
        key = (window.get("price"), window.get("currency"), window.get("unit"), window.get("valid_from"), window.get("valid_until"), tuple(window.get("days") or []) if isinstance(window.get("days"), list) else window.get("days"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(window)
    return deduped


def pricing_status(windows: List[Dict[str, Any]], *, price_cap: float = PRICE_MAX_DEFAULT, now: Optional[datetime] = None) -> Dict[str, Any]:
    enriched = []
    for raw in windows:
        window = dict(raw)
        price = safe_float(window.get("price"))
        window["is_free"] = price == 0
        window["is_within_standard"] = price is not None and price <= price_cap and (price == 0 or str(window.get("currency") or "EUR").upper() == "EUR")
        window["is_currently_valid"] = pricing_window_is_current(window, now=now)
        window["label"] = pricing_window_label(window)
        enriched.append(window)
    matching = [w for w in enriched if w.get("is_within_standard")]
    current = [w for w in enriched if w.get("is_currently_valid")]
    current_matching = [w for w in current if w.get("is_within_standard")]
    price_min = min((safe_float(w.get("price")) for w in matching if safe_float(w.get("price")) is not None), default=None)
    current_price_min = min((safe_float(w.get("price")) for w in current if safe_float(w.get("price")) is not None and (safe_float(w.get("price")) == 0 or str(w.get("currency") or "EUR").upper() == "EUR")), default=None)
    next_matching = current_matching[0] if current_matching else (matching[0] if matching else None)
    if current_matching:
        label = f"Now {pricing_window_label(current_matching[0])}"
    elif next_matching:
        label = f"Cheap window {pricing_window_label(next_matching)}"
    elif current:
        label = f"Now {pricing_window_label(current[0])}"
    else:
        label = "Verify price"
    return {
        "windows": enriched,
        "matching_windows": matching,
        "current_windows": current,
        "price_min": round(price_min, 4) if price_min is not None else None,
        "current_price_min": round(current_price_min, 4) if current_price_min is not None else None,
        "is_free": any(w.get("is_free") for w in matching),
        "is_cheap": bool(matching),
        "is_currently_within_standard": bool(current_matching),
        "next_matching_window": next_matching,
        "label": label,
    }


def attach_pricing_summary(item: Dict[str, Any], *, price_cap: float = PRICE_MAX_DEFAULT) -> Dict[str, Any]:
    windows: List[Dict[str, Any]] = []
    for raw in item.get("pricing") or []:
        if isinstance(raw, dict):
            normalized = normalize_pricing_window(raw, default_currency=station_currency(item))
            if normalized:
                windows.append(normalized)
    for connector in item.get("connectors") or []:
        if isinstance(connector, dict):
            windows.extend(connector_pricing_windows(connector, price_cap=price_cap, default_currency=station_currency(item)))
    # Preserve simple price_min-only sources as always-valid windows so old data still renders consistently.
    if not windows and item.get("price_min") is not None:
        charge_claim = item.get("charge_claim") if isinstance(item.get("charge_claim"), dict) else {}
        normalized = normalize_pricing_window({
            "price": item.get("price_min"),
            "currency": station_currency(item),
            "source": charge_claim.get("source") or item.get("source") or "station price",
            "source_url": charge_claim.get("url") or charge_claim.get("source_url"),
            "human": charge_claim.get("summary"),
        })
        if normalized:
            windows.append(normalized)
    status = pricing_status(windows, price_cap=price_cap)
    if status["windows"]:
        item["pricing"] = status["windows"]
        item["price_window_status"] = {k: v for k, v in status.items() if k != "windows"}
        if status["price_min"] is not None:
            item["price_min"] = status["price_min"]
        item["current_price_min"] = status["current_price_min"]
        item["is_free"] = status["is_free"]
        item["is_cheap"] = status["is_cheap"]
    return item


def extract_prices(connector: Dict[str, Any]) -> Tuple[List[float], List[float], List[str]]:
    energy: List[float] = []
    parking: List[float] = []
    human: List[str] = []
    for comp in tariff_components(connector):
        price = safe_float(comp.get("price"))
        if price is None:
            continue
        if comp.get("type") == "ENERGY":
            energy.append(price)
        elif comp.get("type") == "PARKING_TIME":
            parking.append(price)
        for h in comp.get("human") or []:
            if h and h not in human:
                human.append(str(h))
    return energy, parking, human


def connector_label(std: str) -> str:
    return normalize_connector_label(std) or str(std or "").replace("_", " ")


class GaiaJumpPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.x_data: Optional[str] = None
        self.title_parts: List[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        attr = {k: v for k, v in attrs}
        if tag.lower() == "title":
            self._in_title = True
        x_data = attr.get("x-data")
        node_id = attr.get("id")
        if x_data and (node_id == "jump-page-connector" or "connector" in x_data):
            self.x_data = x_data

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)


def html_title(document: str) -> Optional[str]:
    parser = GaiaJumpPageParser()
    try:
        parser.feed(document or "")
    except Exception:
        pass
    title = compact_text(" ".join(parser.title_parts))
    if title and not re.fullmatch(r"Gaia Charge", title, re.I):
        return html_lib.unescape(title)
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", document or "")
    if m:
        title = strip_tags(m.group(1))
        if title and not re.fullmatch(r"Gaia Charge", title, re.I):
            return title
    return None


def dict_pick(data: Any, *keys: str) -> Any:
    if not isinstance(data, dict):
        return None
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    normalized = {
        re.sub(r"[^a-z0-9]+", "", str(k).lower()): v
        for k, v in data.items()
    }
    for key in keys:
        value = normalized.get(re.sub(r"[^a-z0-9]+", "", key.lower()))
        if value not in (None, ""):
            return value
    return None


def parse_kw(value: Any) -> Optional[float]:
    numeric = safe_float(value)
    if numeric is not None:
        if numeric > 1000:
            numeric = numeric / 1000
        return round(numeric, 1)
    text = str(value or "")
    m = re.search(r"([0-9]+(?:[,.][0-9]+)?)\s*(?:k\s*w|kw)\b", text, re.I)
    if not m:
        return None
    parsed = safe_float(m.group(1))
    return round(parsed, 1) if parsed is not None else None


def balanced_fragment(text: str, start: int) -> Optional[str]:
    while start < len(text) and text[start].isspace():
        start += 1
    if start >= len(text) or text[start] not in "{[(":
        return None
    pairs = {"{": "}", "[": "]", "(": ")"}
    opener = text[start]
    closer = pairs[opener]
    stack = [closer]
    quote: Optional[str] = None
    escaped = False
    for i in range(start + 1, len(text)):
        ch = text[i]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in {"'", '"'}:
            quote = ch
            continue
        if ch in pairs:
            stack.append(pairs[ch])
            continue
        if stack and ch == stack[-1]:
            stack.pop()
            if not stack:
                return text[start:i + 1]
    return None


def object_after_key(text: str, key: str) -> Optional[str]:
    pattern = re.compile(rf"(?<![A-Za-z0-9_$]){re.escape(key)}\s*[:=]\s*", re.I)
    for match in pattern.finditer(text or ""):
        fragment = balanced_fragment(text, match.end())
        if fragment:
            return fragment
    return None


def parse_jsish_object(raw: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    text = html_lib.unescape(raw).strip()
    candidates = [text]
    normalized = re.sub(r"([{,]\s*)([A-Za-z_$][A-Za-z0-9_$]*)(\s*:)", r"\1'\2'\3", text)
    normalized = re.sub(r"\btrue\b", "True", normalized)
    normalized = re.sub(r"\bfalse\b", "False", normalized)
    normalized = re.sub(r"\bnull\b", "None", normalized)
    normalized = re.sub(r"\bundefined\b", "None", normalized)
    candidates.append(normalized)
    for candidate in candidates:
        try:
            parsed = ast.literal_eval(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def gaia_x_data(document: str) -> Optional[str]:
    parser = GaiaJumpPageParser()
    try:
        parser.feed(document or "")
    except Exception:
        pass
    if parser.x_data:
        return html_lib.unescape(parser.x_data)
    m = re.search(r"""(?is)\bx-data\s*=\s*(['"])(.*?)\1""", document or "")
    if m and "connector" in m.group(2):
        return html_lib.unescape(m.group(2))
    return None


def gaia_connector_payload(document: str) -> Dict[str, Any]:
    x_data = gaia_x_data(document)
    if not x_data:
        return {}
    connector_fragment = object_after_key(x_data, "connector")
    parsed = parse_jsish_object(connector_fragment or "")
    if parsed is not None:
        return parsed
    outer_fragment = balanced_fragment(x_data, 0) if x_data.strip().startswith("{") else None
    outer = parse_jsish_object(outer_fragment or x_data)
    nested = dict_pick(outer, "connector") if outer else None
    return nested if isinstance(nested, dict) else {}


def gaia_source_allowed(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() in GAIA_ALLOWED_HOSTS


def fetch_gaia_public_html(url: str) -> str:
    if not gaia_source_allowed(url):
        raise ValueError(f"Gaia URL host is not allow-listed: {url}")
    req = urllib.request.Request(url, headers=GAIA_HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=8) as resp:
        body = resp.read(1_500_000)
    return body.decode("utf-8", "replace")


def gaia_sibling_urls(url: str, max_index: int) -> List[str]:
    parsed = urllib.parse.urlparse(url)
    head, sep, tail = parsed.path.rpartition("/")
    m = re.search(r"-(\d+)$", tail)
    if not sep or not m:
        return [url]
    urls: List[str] = []
    for idx in range(1, max(1, max_index) + 1):
        sibling_tail = f"{tail[:m.start(1)]}{idx}"
        sibling_path = f"{head}/{sibling_tail}"
        urls.append(urllib.parse.urlunparse(parsed._replace(path=sibling_path)))
    return urls


def gaia_station_title(document: str) -> Optional[str]:
    title = html_title(document)
    if title:
        return title
    text = visible_text_from_html(document)
    candidates = [
        line.strip()
        for line in re.split(r"\s{2,}|\n", text)
        if 4 <= len(line.strip()) <= 80
    ]
    for line in candidates:
        if re.search(r"\b(?:carrer|calle|avinguda|avenida|artesans)\b", line, re.I):
            return line
    return None


def gaia_tariff_text(document: str, text: str) -> Optional[str]:
    for pattern in [
        r"You will not be charged[^.]+(?:\.)?",
        r"No se te cobrará[^.]+(?:\.)?",
        r"No se le cobrará[^.]+(?:\.)?",
    ]:
        m = re.search(pattern, text, re.I)
        if m:
            return compact_text(m.group(0))
    if re.search(r"\bFree\b", text, re.I):
        return "Gaia public start page shows Free for this connector; provider terms and onsite signs still apply."
    return None


def parse_gaia_public_page(url: str, document: str) -> Optional[Dict[str, Any]]:
    if "jump-page-connector" not in document:
        return None
    connector = gaia_connector_payload(document)
    text = visible_text_from_html(document)
    session = dict_pick(connector, "session")
    if not isinstance(session, dict):
        session = {}

    connector_id = (
        dict_pick(connector, "connectorId", "connector_id", "id", "uid")
        or urllib.parse.unquote(urllib.parse.urlparse(url).path.rstrip("/").rsplit("/", 1)[-1])
    )
    status_raw = dict_pick(connector, "status", "state", "connector_status") or "UNKNOWN"
    changed_at = dict_pick(connector, "changed_at", "changedAt", "status_updated_at", "updated_at", "last_updated")
    connector_type = dict_pick(connector, "connector_type", "connectorType", "type", "standard")
    if isinstance(connector_type, dict):
        connector_type = dict_pick(connector_type, "name", "label", "slug", "id")
    type_match = re.search(r"\b(Type\s*2|CCS2|CHAdeMO|Schuko)\b", text, re.I)
    connector_type = connector_type or (type_match.group(1) if type_match else None) or "Type 2"
    power_kw = (
        parse_kw(dict_pick(connector, "power_kw", "power", "max_power_kw", "maxPower", "localized_power"))
        or parse_kw(text)
    )
    session_power_kw = parse_kw(dict_pick(session, "localized_instantaneous_power", "instantaneous_power", "power_kw", "power"))
    price_label = dict_pick(connector, "price", "localized_price", "tariff", "tariff_label")
    if not price_label and re.search(r"\bFree\b", text, re.I):
        price_label = "Free"
    physical_reference = dict_pick(
        connector,
        "physical_reference",
        "physicalReference",
        "physical_ref",
        "visual_reference",
        "visualRef",
        "reference",
        "letter",
    )
    tariff = gaia_tariff_text(document, text)
    is_free = bool(price_label and re.search(r"\bfree\b", str(price_label), re.I)) or bool(tariff and re.search(r"\bfree\b|not be charged|no se .*cobr", tariff, re.I))

    label = connector_label(str(connector_type).strip().upper().replace(" ", "_")) if str(connector_type).upper().startswith("IEC_") else compact_text(connector_type)
    if not label:
        label = "Type 2"
    normalized_status = normalize_status(status_raw)
    source_title = gaia_station_title(document)
    source_url = resolve_public_url(url, dict_pick(connector, "jump_url", "jumpUrl", "url")) or url

    return {
        "connector_id": str(connector_id),
        "label": label,
        "type": compact_text(connector_type),
        "status": normalized_status,
        "status_label": compact_text(status_raw),
        "status_icon": dict_pick(connector, "status_icon", "statusIcon"),
        "changed_at": changed_at,
        "power_kw": power_kw,
        "price": compact_text(price_label) if price_label else None,
        "is_free": is_free,
        "physical_reference": compact_text(physical_reference) if physical_reference else None,
        "session_power_kw": session_power_kw,
        "session_energy": dict_pick(session, "localized_total_energy", "total_energy"),
        "session_duration": dict_pick(session, "localized_duration", "duration"),
        "session_start": dict_pick(session, "session_start", "sessionStart", "started_at"),
        "source_url": source_url,
        "source_title": source_title,
        "tariff_text": tariff,
        "tesla_ok": label in {"Type 2", "CCS2", "Tesla"},
        "fast": bool(power_kw and power_kw >= 43),
    }


def gaia_urls_for_seed(seed: Dict[str, Any]) -> List[str]:
    base_urls = [str(seed.get("jump_url") or "")]
    base_urls.extend(GAIA_JUMP_URLS)
    out: List[str] = []
    seen = set()
    for url in base_urls:
        if not url or not gaia_source_allowed(url):
            continue
        for candidate in gaia_sibling_urls(url, safe_int(seed.get("max_connector_index")) or GAIA_CONNECTOR_PROBE_MAX):
            if candidate in seen:
                continue
            seen.add(candidate)
            out.append(candidate)
    return out


def build_gaia_station(seed: Dict[str, Any], connectors: List[Dict[str, Any]], fetched_at: str, errors: List[str]) -> Dict[str, Any]:
    connectors = sorted(
        connectors,
        key=lambda c: (
            str(c.get("physical_reference") or ""),
            str(c.get("connector_id") or ""),
        ),
    )
    last_updates = [str(c.get("changed_at")) for c in connectors if c.get("changed_at")]
    last_seen = max(last_updates) if last_updates else fetched_at
    source_urls = []
    for c in connectors:
        source_url = c.get("source_url")
        if source_url and source_url not in source_urls:
            source_urls.append(source_url)
    tariff_text = next((c.get("tariff_text") for c in connectors if c.get("tariff_text")), None)
    max_kw = max([safe_float(c.get("power_kw")) or 0 for c in connectors] or [0])
    source_rows = [
        {
            "name": "Gaia public start-session page",
            "url": source_url,
            "evidence": "Public Gaia page exposes connector status, tariff label, socket type and session telemetry. Volta only performs GET requests against this public page.",
        }
        for source_url in source_urls[:8]
    ]
    record = {
        "id": seed["id"],
        "name": seed.get("name") or next((c.get("source_title") for c in connectors if c.get("source_title")), "Gaia Green Tech charger"),
        "address": seed.get("address"),
        "postal_code": "46120",
        "city": seed.get("city") or "Alboraia",
        "country": "ESP",
        "lat": seed.get("lat"),
        "lon": seed.get("lon"),
        "status": "UNKNOWN",
        "total_evse": len(connectors),
        "connectors": connectors,
        "connector_labels": sorted({str(c.get("label")) for c in connectors if c.get("label")}),
        "max_power_kw": round(max_kw, 1) if max_kw else None,
        "price_min": 0.0 if any(c.get("is_free") for c in connectors) else None,
        "price_max": 0.0 if any(c.get("is_free") for c in connectors) else None,
        "is_free": any(c.get("is_free") for c in connectors),
        "is_cheap": any(c.get("is_free") for c in connectors),
        "tesla_ok": any(c.get("tesla_ok") for c in connectors),
        "fast": any(c.get("fast") for c in connectors),
        "parking": {
            "kind": "street_or_municipal",
            "label": "Street/municipal",
            "paid": False,
            "note": "Gaia confirms free charging on the public start page, but parking rules and time limits are not fully published there. Check street signs and provider terms before leaving the car.",
            "free_likelihood": "medium",
            "peak_hint": "This is slow AC charging; bay availability and local parking limits matter more than route speed.",
        },
        "access": {
            "level": "public",
            "label": "Public",
            "note": "Public Gaia start page is reachable without login; do not assume the connector can be used if onsite signs or provider terms say otherwise.",
        },
        "last_seen": last_seen,
        "source_fetched_at": fetched_at,
        "source": "Gaia public start-session page",
        "source_url": source_urls[0] if source_urls else seed.get("jump_url"),
        "sources": source_rows,
        "source_count": len(source_rows),
        "source_type": "public-gaia-jump-page",
        "reliability": "official-live-public-page",
        "free_condition": tariff_text or "Gaia public start page shows Free charging; provider terms and onsite signs still apply.",
        "owner": {
            "name": "Gaia Green Tech",
            "website": "https://gaiacharge.com/",
            "phone": None,
            "logo": None,
        },
        "score": 82,
        "gaia": {
            "seed_url": seed.get("jump_url"),
            "probed_urls": source_urls,
            "errors": errors[:6],
            "policy": "GET-only parser for public Gaia start-session HTML; no login, payment setup, session start, or private API requests.",
        },
        "navigation": {
            "google": f"https://www.google.com/maps/search/?api=1&query={seed.get('lat')},{seed.get('lon')}",
            "apple": f"https://maps.apple.com/?ll={seed.get('lat')},{seed.get('lon')}&q={urllib.parse.quote(seed.get('name') or 'Gaia charger')}",
        },
    }
    attach_connector_summary(record)
    if record.get("available", 0) > 0:
        record["status"] = "AVAILABLE"
    elif record.get("charging", 0) > 0:
        record["status"] = "CHARGING"
    elif record.get("out_of_service", 0) and record.get("out_of_service") == record.get("total_evse"):
        record["status"] = "OUTOFORDER"
    else:
        record["status"] = "UNKNOWN"
    return enrich_station(record, live=True)


def fetch_gaia_live(force: bool = False) -> Dict[str, Any]:
    now = time.time()
    cache_key = ",".join(GAIA_JUMP_URLS)
    with _CACHE_LOCKS["gaia"]:
        cached = GAIA_CACHE.get("live")
        if cached and cached.get("key") == cache_key:
            age = now - cached.get("ts", 0)
            can_serve_fresh = age < GAIA_CACHE_TTL_SECONDS
            # Extended stale reuse is for previously fetched station evidence.
            # An empty/failed response should retry after the normal fresh TTL,
            # rather than suppress a recovered provider for the whole stale window.
            has_station_evidence = bool(cached["payload"].get("stations"))
            can_serve_stale = (not force and has_station_evidence and age < GAIA_STALE_TTL_SECONDS)
            if can_serve_fresh or can_serve_stale:
                stale = not can_serve_fresh
                payload = deepcopy(cached["payload"])
                payload["cache"] = {
                    "hit": True,
                    "stale": stale,
                    "age_seconds": round(age, 1),
                    "ttl_seconds": GAIA_CACHE_TTL_SECONDS,
                    "stale_ttl_seconds": GAIA_STALE_TTL_SECONDS,
                    # plans/007 REVISE (adviser Finding 1): the epoch this cache SLOT was
                    # actually last populated -- unlike age_seconds (relative to "now",
                    # so it differs on every call even for the identical underlying data),
                    # this is stable across repeated hits of the same population, so
                    # live_response's assembled-payload reuse gate can require the READ's
                    # gaia data to be the EXACT SAME population the WRITE merged, not
                    # merely "also happens to be fresh right now".
                    "populated_at": cached.get("ts"),
                }
                if stale:
                    payload["warning"] = "Gaia public-page refresh is slow; serving the last successful Gaia cache. Verify in the provider page before routing."
                return payload

        if cached and not force and cached.get("key") == cache_key and now - cached.get("ts", 0) < CACHE_TTL_SECONDS:
            payload = deepcopy(cached["payload"])
            payload["cache"] = {"hit": True, "age_seconds": round(now - cached["ts"], 1), "ttl_seconds": GAIA_CACHE_TTL_SECONDS, "populated_at": cached.get("ts")}
            return payload

    fetched_at = now_iso()
    started_at = time.time()
    stations: List[Dict[str, Any]] = []
    all_errors: List[str] = []
    for seed in GAIA_STATION_SEEDS:
        connectors: List[Dict[str, Any]] = []
        seen_connectors = set()
        consecutive_missing = 0
        seed_errors: List[str] = []
        for url in gaia_urls_for_seed(seed):
            if time.time() - started_at > GAIA_FETCH_BUDGET_SECONDS:
                seed_errors.append(f"Gaia fetch budget exceeded after {GAIA_FETCH_BUDGET_SECONDS:.1f}s; returned partial connector data.")
                break
            try:
                document = fetch_gaia_public_html(url)
                parsed = parse_gaia_public_page(url, document)
            except urllib.error.HTTPError as exc:
                seed_errors.append(f"{url}: HTTP {exc.code}")
                if exc.code == 404:
                    consecutive_missing += 1
                    if connectors and consecutive_missing >= 2:
                        break
                    continue
                continue
            except Exception as exc:
                _log_upstream_error(f"Gaia fetch failed for {url}", exc)
                seed_errors.append(f"{url}: {type(exc).__name__}")
                continue
            if not parsed:
                seed_errors.append(f"{url}: no public jump-page connector block")
                consecutive_missing += 1
                if connectors and consecutive_missing >= 2:
                    break
                continue
            consecutive_missing = 0
            key = parsed.get("connector_id") or parsed.get("source_url") or url
            if key in seen_connectors:
                continue
            seen_connectors.add(key)
            connectors.append(parsed)
        all_errors.extend(seed_errors)
        if connectors:
            stations.append(build_gaia_station(seed, connectors, fetched_at, seed_errors))

    payload = {
        "stations": stations,
        "raw_count": sum(len(st.get("connectors") or []) for st in stations),
        "fetched_at": fetched_at,
        "errors": all_errors[:12],
        "source": "gaia-public-start-pages",
        "cache": {"hit": False, "age_seconds": 0, "ttl_seconds": GAIA_CACHE_TTL_SECONDS, "populated_at": now},
    }
    with _CACHE_LOCKS["gaia"]:
        GAIA_CACHE["live"] = {"ts": now, "payload": deepcopy(payload), "key": cache_key}
    return payload


SOURCE_FAMILY_PATTERNS = [
    ("mapareve", ("mapareve",)),
    ("gaia", ("gaia",)),
    ("gic", ("recargagic", "ecove")),
    ("electromaps", ("electromaps",)),
    # openchargemap MUST precede chargemap: "map.openchargemap.io" contains "chargemap"
    # and would otherwise collapse two independent families into one (undercounting
    # corroboration for OCM+Chargemap-backed stations).
    ("openchargemap", ("openchargemap", "ocm-")),
    ("chargemap", ("chargemap",)),
    ("osm", ("openstreetmap", "overpass")),
    ("lidl", ("lidl",)),
    ("consum", ("consum",)),
    ("municipal", ("geoportal", "opendata", "ayuntamiento", "municipal", "gva")),
    ("tesla", ("tesla",)),
]


def source_family(*texts: Any) -> str:
    """Map a source name/url to an independent-source family (host-derived)."""
    blob = " ".join(str(t or "") for t in texts).lower()
    for family, needles in SOURCE_FAMILY_PATTERNS:
        if any(n in blob for n in needles):
            return family
    return ""


def station_families(item: Dict[str, Any]) -> set:
    """Distinct independent source families backing a station (for corroboration)."""
    fams = set()
    rows = [(item.get("source"), item.get("source_url"))]
    for s in item.get("sources") or []:
        if isinstance(s, dict):
            rows.append((s.get("name"), s.get("url")))
    prim = item.get("primary_source") or {}
    rows.append((prim.get("name"), prim.get("url")))
    for name, url in rows:
        fam = source_family(name, url)
        if fam:
            fams.add(fam)
    return fams


def _name_tokens(item: Dict[str, Any]) -> set:
    raw = " ".join(str(x or "") for x in [item.get("name"), item.get("address")]).lower()
    raw = re.sub(r"[^a-z0-9áéíóúñ· ]+", " ", raw)
    return {t for t in raw.split() if len(t) >= 4}


def _name_affinity(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Generic same-site check for two nearby points: shared significant name/address tokens."""
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter >= 2 or (union and inter / union >= 0.34)


def apply_corroboration(item: Dict[str, Any]) -> Dict[str, Any]:
    """Record how many independent public sources back this station (existence/price), not liveness.

    INVARIANT: corroboration never sets/raises live verification — that stays driven by a real live
    source in verification_profile. This only raises confidence that the station exists and is cheap/free.
    """
    fams = station_families(item)
    n = len(fams)
    tier = "strongly_corroborated" if n >= 3 else ("corroborated" if n >= 2 else "single")
    item["corroboration"] = {"independent_sources": n, "families": sorted(fams), "tier": tier}
    return item


def station_source_urls(item: Dict[str, Any]) -> set:
    urls = set()
    for value in [item.get("source_url"), (item.get("owner") or {}).get("website")]:
        if value:
            urls.add(str(value).split("#", 1)[0].rstrip("/").lower())
    for source in item.get("sources") or []:
        if isinstance(source, dict) and source.get("url"):
            urls.add(str(source["url"]).split("#", 1)[0].rstrip("/").lower())
    for connector in item.get("connectors") or []:
        if isinstance(connector, dict) and connector.get("source_url"):
            urls.add(str(connector["source_url"]).split("#", 1)[0].rstrip("/").lower())
    return urls


def likely_same_station(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    if a.get("id") and b.get("id") and str(a["id"]).lower() == str(b["id"]).lower():
        return True
    if station_source_urls(a) & station_source_urls(b):
        return True
    a_lat, a_lon = safe_float(a.get("lat")), safe_float(a.get("lon"))
    b_lat, b_lon = safe_float(b.get("lat")), safe_float(b.get("lon"))
    if a_lat is None or a_lon is None or b_lat is None or b_lon is None:
        return False
    if haversine_km(a_lat, a_lon, b_lat, b_lon) > 0.12:
        return False
    # Within ~120 m AND sharing significant name/address tokens => same physical site
    # (generic; the old Gaia-specific "artesans/0786" heuristic was a special case of this).
    return _name_affinity(a, b)


def source_rows_union(primary: Dict[str, Any], secondary: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen = set()
    for source in (primary.get("sources") or []) + (secondary.get("sources") or []):
        if not isinstance(source, dict):
            continue
        key = (source.get("name"), source.get("url"))
        if key in seen:
            continue
        seen.add(key)
        rows.append(source)
    return rows[:10]


def preserve_price_evidence(primary: Dict[str, Any], secondary: Dict[str, Any]) -> None:
    """Retain additive annotations when an existing merge drops/replaces a row.

    These objects never enter ``sources``: their URLs, prices and timestamps must
    not affect station identity, corroboration, primary pricing or live authority.
    Existing merge predicates and their winning row remain unchanged.
    """
    evidence: List[Dict[str, Any]] = []
    seen = set()
    for station in (primary, secondary):
        for claim in station.get("price_evidence") or []:
            if not isinstance(claim, dict):
                continue
            key = json.dumps(claim, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            if key not in seen:
                seen.add(key)
                evidence.append(deepcopy(claim))
    if evidence:
        primary["price_evidence"] = evidence


# plans/007 step 3: spatial+identity bucketing for the three O(n*m) pairwise matchers
# above/below (merge_gaia_stations, remove_duplicate_leads,
# merge_nearby_source_stations). The matchers themselves (likely_same_station, and the
# nearby-source merge's bare-proximity
# check) are UNCHANGED -- this index only narrows which pairs get probed, from a full
# linear scan down to a small candidate set. Correctness rule: the candidate set must
# never be missing a true match (extra/stale candidates are harmless -- the unchanged
# matcher rejects them); every mutation that could introduce a NEW id/url/coordinate a
# later probe needs to find (folding a source union, appending a new station, replacing
# a slot wholesale) updates the index incrementally rather than leaving it to go stale.
_MATCH_INDEX_CELL_DEG = 0.002  # ~222m N-S / ~160-180m E-W at Spain's latitudes


def _index_cell(lat: float, lon: float, cell_deg: float = _MATCH_INDEX_CELL_DEG) -> Tuple[int, int]:
    return (int(math.floor(lat / cell_deg)), int(math.floor(lon / cell_deg)))


def _index_add(index: Dict[str, Any], i: int, station: Dict[str, Any]) -> None:
    """Register (or re-register -- e.g. after slot i is replaced/updated in place)
    station `station` at position i. Safe to call repeatedly for the same i: duplicate
    bucket references are harmless (candidates are deduped via a set before use).

    IMPORTANT: ids, like urls, is APPEND-ONLY (never "lowest index wins" / overwrite-in-
    place). merge_gaia_stations can wholesale-replace slot i with content that carries a
    DIFFERENT id than whatever was there before; a single-index-per-id map would then
    either point at a slot that no longer has that id (stale, but that alone is
    harmless) OR -- the actual bug this fuzz-tested out -- fail to ever repoint an id to
    a slot that legitimately acquired it later, silently DROPPING it as a findable
    candidate for a subsequent probe. Keeping every (id -> i) assignment that ever
    existed means a later probe's true match is always still IN the candidate set;
    likely_same_station's live re-check on each candidate then correctly rejects the
    slots that no longer actually hold that id."""
    lat, lon = safe_float(station.get("lat")), safe_float(station.get("lon"))
    if lat is not None and lon is not None:
        index["grid"].setdefault(_index_cell(lat, lon, index["cell_deg"]), []).append(i)
    for u in station_source_urls(station):
        index["urls"].setdefault(u, []).append(i)
    sid = station.get("id")
    if sid:
        index["ids"].setdefault(str(sid).lower(), []).append(i)


def _station_match_index(stations: List[Dict[str, Any]], *, cell_deg: float = _MATCH_INDEX_CELL_DEG) -> Dict[str, Any]:
    """Build a spatial+identity index over `stations`.

    grid: (int(floor(lat/cell_deg)), int(floor(lon/cell_deg))) -> [i, ...]
    urls: normalized source url (station_source_urls) -> [i, ...]
    ids:  lowercased id -> [i, ...] (every index that has EVER carried this id --
          see _index_add's docstring for why this must be append-only, not a single
          "current" index)
    """
    index: Dict[str, Any] = {"grid": {}, "urls": {}, "ids": {}, "cell_deg": cell_deg}
    for i, st in enumerate(stations):
        _index_add(index, i, st)
    return index


def _match_candidates(index: Dict[str, Any], probe: Dict[str, Any]) -> List[int]:
    """Ascending-index candidate set for `probe`: id hit UNION url hits UNION the
    probe's 3x3 neighbor grid cells. The 3x3 neighborhood always covers at least one
    full cell width (~222m/~160-180m) beyond the probe's own cell, comfortably larger
    than likely_same_station's 0.12km gate and merge_nearby_source_stations' 0.08km gate --
    the grid only prunes; the exact radius check still runs in the matcher itself.
    Ascending order preserves merge_gaia_stations/merge_nearby_source_stations' first-match-
    by-index semantics."""
    candidates: set = set()
    sid = probe.get("id")
    if sid:
        candidates.update(index["ids"].get(str(sid).lower(), ()))
    for u in station_source_urls(probe):
        candidates.update(index["urls"].get(u, ()))
    lat, lon = safe_float(probe.get("lat")), safe_float(probe.get("lon"))
    if lat is not None and lon is not None:
        clat, clon = _index_cell(lat, lon, index["cell_deg"])
        for dlat in (-1, 0, 1):
            for dlon in (-1, 0, 1):
                candidates.update(index["grid"].get((clat + dlat, clon + dlon), ()))
    return sorted(candidates)


def merge_gaia_stations(stations: List[Dict[str, Any]], gaia_stations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged = list(stations)
    index = _station_match_index(merged)
    for gaia in gaia_stations:
        match_index = next(
            (i for i in _match_candidates(index, gaia) if likely_same_station(merged[i], gaia)),
            None,
        )
        if match_index is None:
            merged.append(gaia)
            _index_add(index, len(merged) - 1, gaia)
            continue
        previous = merged[match_index]
        if gaia.get("_volta_stale_live_cache") and not previous.get("_volta_stale_live_cache"):
            # Never let stale secondary evidence replace a fresh primary row at the
            # same site. Keep the fresh operational record and fold Gaia in only as
            # provenance; a stale Gaia-only site is still appended and demoted below.
            previous["sources"] = source_rows_union(previous, gaia)
            previous["source_count"] = len(previous["sources"])
            preserve_price_evidence(previous, gaia)
            _index_add(index, match_index, previous)
            continue
        gaia["sources"] = source_rows_union(gaia, previous)
        gaia["source_count"] = len(gaia["sources"])
        preserve_price_evidence(gaia, previous)
        merged[match_index] = gaia
        _index_add(index, match_index, gaia)  # gaia's own id/coords/urls now live at this slot
    return merged


def remove_duplicate_leads(leads: List[Dict[str, Any]], stations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop leads that duplicate a live station, but FIRST fold the lead's source into that
    station so the live result gains cross-source corroboration (not a silent discard)."""
    index = _station_match_index(stations)
    remaining: List[Dict[str, Any]] = []
    for lead in leads:
        match_index = next(
            (i for i in _match_candidates(index, lead) if likely_same_station(lead, stations[i])),
            None,
        )
        if match_index is None:
            remaining.append(lead)
        else:
            match = stations[match_index]
            match["sources"] = source_rows_union(match, lead)
            match["source_count"] = len(match["sources"])
            preserve_price_evidence(match, lead)
            # A later lead may share a URL with THIS lead (now folded into match) even
            # if it doesn't share one with match's original sources -- keep it findable.
            _index_add(index, match_index, match)
    return remaining


def merge_nearby_source_stations(stations: List[Dict[str, Any]], secondary_stations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Append promoted secondary rows, deduping by ~80m proximity.

    The helper is used for Electromaps promotion. URL matching alone is not reliable
    for aggregator evidence, so the exact bare-proximity contract is kept explicit.

    plans/007 step 3: uses the SAME spatial index as merge_gaia_stations/
    remove_duplicate_leads (its 3x3 neighborhood comfortably covers this function's own
    0.08km gate -- see _match_candidates), but only the grid dimension matters here: this
    matcher is bare-proximity, no id/url check at all (the id/url candidates _match_candidates
    also returns are harmless extras, rejected by the distance check below).
    """
    merged = list(stations)
    index = _station_match_index(merged)
    for f in secondary_stations:
        flat, flon = safe_float(f.get("lat")), safe_float(f.get("lon"))
        if flat is None or flon is None:
            continue
        match = None
        match_index = None
        for i in _match_candidates(index, f):
            st = merged[i]
            slat, slon = safe_float(st.get("lat")), safe_float(st.get("lon"))
            if slat is not None and slon is not None and haversine_km(flat, flon, slat, slon) <= 0.08:
                match = st
                match_index = i
                break
        if match is None:
            merged.append(f)
            _index_add(index, len(merged) - 1, f)
        else:
            # Same physical site already live (e.g. from MapaREVE): keep it, but retain
            # the promoted secondary row as corroborating evidence.
            match["sources"] = source_rows_union(match, f)
            match["source_count"] = len(match["sources"])
            preserve_price_evidence(match, f)
            _index_add(index, match_index, match)
    return merged


# ---------- Electromaps live (real-time, freshness-gated) ----------
# Electromaps aggregates many operators' real-time OCPI connector status. We treat a connector as
# GENUINE live telemetry only when Electromaps flags realtime=true on it AND the status is a known
# live state (not UNKNOWN) AND its timestamp isn't an epoch placeholder. That gate is exactly what
# lets us (a) show real-time busy/free and (b) stop surfacing "last updated long ago" / no-telemetry
# entries. status_updated_at is a last-STATE-CHANGE time (an idle-available charger can be hours/days
# old yet genuinely free), so we do NOT gate on its age — we gate on the realtime telemetry flag and
# stamp last_seen with the moment we observed it.
ELECTROMAPS_MAPI = "https://www.electromaps.com/mapi/v2"
ELECTROMAPS_DIRECT_ENABLED = False
ELECTROMAPS_HEADERS = {
    "Accept": "application/json",
    "App-platform": "web",
    "Referer": "https://map.electromaps.com/es/",
    "User-Agent": LIVE_HEADERS["User-Agent"],
}
ELECTROMAPS_CACHE: Dict[str, Any] = {}
ELECTROMAPS_DETAIL_TTL = int(os.environ.get("VOLTA_ELECTROMAPS_DETAIL_TTL", "150"))     # seconds
ELECTROMAPS_LIVE_MAX = int(os.environ.get("VOLTA_ELECTROMAPS_LIVE_MAX", "60"))           # detail fetches / request
ELECTROMAPS_FETCH_BUDGET = float(os.environ.get("VOLTA_ELECTROMAPS_FETCH_BUDGET", "6.0"))
# Electromaps' baked status timestamps run to a MEDIAN of ~1 year old (some 5+ years). A connector's
# status_updated_at is the last STATE CHANGE, so we read it as "when Electromaps last had telemetry":
# older than this many days => stale/abandoned listing, drop it. Tune with the env var.
ELECTROMAPS_MAX_AGE_DAYS = float(os.environ.get("VOLTA_ELECTROMAPS_MAX_AGE_DAYS", "30"))


def electromaps_connector_label(token: Any) -> str:
    # D-151 fix: unknown/unrecognized tokens used to default to "Type 2" here,
    # silently claiming Tesla compatibility (build_electromaps_station's
    # tesla_ok check reads this label). Fail closed instead: honest
    # passthrough for genuinely unknown tokens, "" (never Tesla-ok) for
    # empty/missing ones — never a fabricated "Type 2".
    return normalize_connector_label(token) or ""


def _electromaps_id(lead: Dict[str, Any]) -> Optional[str]:
    raw = lead.get("raw") or {}
    if raw.get("electromaps_id"):
        return str(raw["electromaps_id"])
    urls = [lead.get("source_url")] + [s.get("url") for s in (lead.get("sources") or []) if isinstance(s, dict)]
    for url in urls:
        m = re.search(r"/p/(\d+)", str(url or "")) or re.search(r"electromaps\.com/.*?/(\d{4,})\b", str(url or ""))
        if m:
            return m.group(1)
    return None


def _is_epoch_ts(ts: Any) -> bool:
    s = str(ts or "")
    return (not s) or s.startswith("1970-") or s.startswith("0001-")


def _electromaps_detail(loc_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a location detail (current real-time connector status), cached short by id."""
    if not ELECTROMAPS_DIRECT_ENABLED:
        return None
    now = time.time()
    with _CACHE_LOCKS["electromaps"]:
        cached = ELECTROMAPS_CACHE.get(loc_id)
        if cached and now - cached["ts"] < ELECTROMAPS_DETAIL_TTL:
            return cached["detail"]
    url = f"{ELECTROMAPS_MAPI}/locations/{loc_id}?skipAuthTokenValidation=true"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=ELECTROMAPS_HEADERS), timeout=ELECTROMAPS_FETCH_BUDGET) as resp:
            detail = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return cached["detail"] if cached else None
    with _CACHE_LOCKS["electromaps"]:
        ELECTROMAPS_CACHE[loc_id] = {"ts": now, "detail": detail}
        if len(ELECTROMAPS_CACHE) > 2000:
            for k in list(ELECTROMAPS_CACHE.keys())[:400]:
                ELECTROMAPS_CACHE.pop(k, None)
    return detail


def build_electromaps_station(lead: Dict[str, Any], detail: Dict[str, Any], fetched_at: str) -> Optional[Dict[str, Any]]:
    """Promote a known free/cheap Electromaps lead to a LIVE station using its current real-time
    connector status. Returns None when the station has NO genuine live telemetry right now — the
    caller then drops the stale lead instead of showing a "last updated long ago" entry.

    Price/identity come from the vetted lead; only the real-time STATUS comes from the live detail.
    """
    live_connectors: List[Dict[str, Any]] = []
    last_updates: List[str] = []
    for c in detail.get("connectors") or []:
        if c.get("realtime") is not True:
            continue  # connector has no real-time telemetry
        status = normalize_status(c.get("status"))
        if status == "UNKNOWN":
            continue  # telemetry present but state unknown -> not a usable live signal
        if _is_epoch_ts(c.get("status_updated_at")):
            continue  # placeholder/never-reported timestamp
        label = electromaps_connector_label(c.get("type"))
        power = safe_float(c.get("kw"))
        live_connectors.append({
            "connector_id": f"em-{detail.get('id')}-{len(live_connectors) + 1}",
            "label": label,
            "type": label,
            "status": status,
            "power_kw": power,
            "is_free": bool(lead.get("is_free")),
            "tesla_ok": label in {"Type 2", "CCS2", "Tesla"},
            "fast": bool(power and power >= 43),
            "status_updated_at": c.get("status_updated_at"),
            "source_url": lead.get("source_url"),
        })
        last_updates.append(str(c.get("status_updated_at")))
    if not live_connectors:
        return None  # no genuine real-time telemetry => not "really working now" => drop

    lat, lon = safe_float(lead.get("lat")), safe_float(lead.get("lon"))
    name = lead.get("name") or detail.get("name") or "Electromaps charger"
    em_url = lead.get("source_url") or f"https://map.electromaps.com/en/p/{detail.get('id')}"
    avail_now = sum(1 for c in live_connectors if c["status"] == "AVAILABLE")
    record = {
        "id": lead.get("id") or f"em-live-{detail.get('id')}",
        "name": name,
        "address": lead.get("address") or "",
        "city": lead.get("city") or "",
        "lat": lat,
        "lon": lon,
        "connectors": live_connectors,
        "connector_labels": sorted({c["label"] for c in live_connectors}),
        "price_min": lead.get("price_min"),
        "price_max": lead.get("price_max"),
        "is_free": bool(lead.get("is_free")),
        "is_cheap": bool(lead.get("is_cheap")),
        "tesla_ok": any(c["tesla_ok"] for c in live_connectors),
        "fast": any(c["fast"] for c in live_connectors),
        "parking": lead.get("parking"),
        "access": lead.get("access") or {"level": "public", "label": "Public", "note": "Electromaps public listing."},
        "last_seen": fetched_at,  # observed now via live telemetry (NOT the state-change time)
        "status_updated_at": max(last_updates) if last_updates else None,
        "source_fetched_at": fetched_at,
        "source": "Electromaps web-map API (live)",
        "source_url": em_url,
        "sources": source_rows_union(lead, {"sources": [{
            "name": "Electromaps real-time status",
            "url": em_url,
            "evidence": f"Electromaps live telemetry (realtime=true): {avail_now}/{len(live_connectors)} connectors available now.",
        }]}),
        "reliability": "official-live-aggregator",
        "free_condition": lead.get("free_condition"),
        "charge_claim": lead.get("charge_claim"),
        "parking_claim": lead.get("parking_claim"),
        "navigation": lead.get("navigation"),
        "electromaps_id": str(detail.get("id")),
    }
    preserve_price_evidence(record, lead)
    attach_connector_summary(record)
    if record.get("available", 0) > 0:
        record["status"] = "AVAILABLE"
    elif record.get("charging", 0) > 0:
        record["status"] = "CHARGING"
    elif record.get("busy_connectors", 0) > 0:
        record["status"] = "OCCUPIED"
    elif record.get("out_of_service", 0) > 0:
        record["status"] = "OUTOFORDER"
    else:
        record["status"] = "UNKNOWN"
    return enrich_station(record, live=True)


def fetch_electromaps_live(targets: List[Dict[str, Any]], *, max_price: float, fetched_at: str) -> Dict[str, Any]:
    """Refresh real-time status for the given in-view Electromaps free/cheap leads (concurrent,
    per-id cached). Returns the live stations plus the ids we checked and the ids we promoted."""
    if not ELECTROMAPS_DIRECT_ENABLED:
        return {"stations": [], "checked_ids": set(), "promoted_ids": set(), "dead_ids": set()}
    pairs: List[Tuple[str, Dict[str, Any]]] = []
    seen_ids = set()
    for lead in targets:
        if lead.get("price_min") is not None and lead.get("price_min") > max_price:
            continue
        loc_id = _electromaps_id(lead)
        if not loc_id or loc_id in seen_ids:
            continue
        seen_ids.add(loc_id)
        pairs.append((loc_id, lead))
    pairs = pairs[:ELECTROMAPS_LIVE_MAX]
    stations: List[Dict[str, Any]] = []
    checked: set = set()
    promoted: set = set()
    dead: set = set()
    if not pairs:
        return {"stations": stations, "checked_ids": checked, "promoted_ids": promoted, "dead_ids": dead}

    def work(pair: Tuple[str, Dict[str, Any]]):
        loc_id, lead = pair
        return loc_id, lead, _electromaps_detail(loc_id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(pairs))) as ex:
        for loc_id, lead, detail in ex.map(work, pairs):
            if detail is None:
                continue  # our fetch failed: leave the lead as-is, don't punish on our error
            checked.add(loc_id)
            station = build_electromaps_station(lead, detail, fetched_at)
            if station:
                stations.append(station)
                promoted.add(loc_id)
            else:
                cons = detail.get("connectors") or []
                if cons and all(normalize_status(c.get("status")) == "OUTOFORDER" for c in cons):
                    dead.add(loc_id)  # the live feed now says every connector is out of service
    return {"stations": stations, "checked_ids": checked, "promoted_ids": promoted, "dead_ids": dead}


# Spanish province by postal-code prefix (first two digits) — honest province-level labels
# nationwide when no exact municipality is published.
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

VALENCIA_MUNICIPALITIES = [
    "Ontinyent", "Serra", "Alfafar", "Guardamar de la Safor", "Cullera", "Gandia", "Paterna",
    "Torrent", "Alzira", "Aldaia", "Sagunto", "Sagunt", "Xàtiva", "L'Eliana", "Manises",
    "Massamagrell", "La Pobla de Vallbona", "Picassent", "Alboraia", "Alboraya", "Mislata",
]


def infer_city(location: Dict[str, Any]) -> str:
    address = (location.get("address") or "").strip()
    name = (location.get("name") or "").strip()
    postal = re.sub(r"\D", "", str(location.get("postal_code") or ""))
    p2 = postal[:2] if len(postal) >= 2 else ""
    text = f"{name} {address}".lower()
    # Valencia (home region): refine to a known municipality when recognizable.
    if p2 == "46" or "valència" in text or "valencia" in text:
        for city in VALENCIA_MUNICIPALITIES:
            if city.lower() in text:
                return city
        return "València"
    if p2 in PROVINCE_BY_POSTAL2:
        return PROVINCE_BY_POSTAL2[p2]
    for needle, label in (("madrid", "Madrid"), ("barcelona", "Barcelona"), ("sevilla", "Sevilla"),
                          ("zaragoza", "Zaragoza"), ("málaga", "Málaga"), ("bilbao", "Bilbao")):
        if needle in text:
            return label
    return ""


def infer_parking(location: Dict[str, Any], parking_prices: List[float]) -> Dict[str, Any]:
    name = (location.get("name") or "").lower()
    address = (location.get("address") or "").lower()
    text = f"{name} {address}"
    if parking_prices:
        mn = min(parking_prices)
        return {
            "kind": "paid_or_metered",
            "label": "Paid/conditional parking",
            "paid": True,
            "cost_hint": f"from €{mn:.2f}",
            "note": f"Charging tariff includes parking-time component from about €{mn:.2f}; check app/garage rules before leaving the car.",
            "free_likelihood": "low",
            "peak_hint": "Urban/garage stop: useful if spaces are live-available, but parking cost can dominate a cheap kWh price.",
        }
    if any(w in text for w in ["carrefour", "lidl", "mercadona", "centro comercial", "aquopolis", "bonaire", "saler", "mn4"]):
        return {
            "kind": "retail_parking",
            "label": "Retail/customer parking",
            "paid": False,
            "note": "Likely retail/customer parking; usually easiest outside Saturday, lunch and after-work peaks. Verify validation/time limits onsite.",
            "free_likelihood": "medium",
            "peak_hint": "High parking supply, but EV bays often fill during shopping peaks.",
        }
    if any(w in text for w in ["colegio", "campus", "hospital", "polideportivo", "estadio", "club náutico", "club nautico", "gasolinera", "estación de servicio", "estacion de servicio"]):
        return {
            "kind": "facility_or_customer_parking",
            "label": "Facility/customer parking",
            "paid": False,
            "note": "Likely facility, visitor or customer parking; verify access rules and opening hours onsite.",
            "free_likelihood": "medium",
            "peak_hint": "Visitor lots can be convenient, but access rules and bay occupancy vary by event and opening hours.",
        }
    if any(w in text for w in ["ayuntamiento", "carrer", "calle", "avenida", "av.", "plaza", "apk2"]):
        return {
            "kind": "street_or_municipal",
            "label": "Street/municipal",
            "paid": False,
            "note": "Street/municipal access. Parking may be free or regulated depending on zone; app/onsite sign wins.",
            "free_likelihood": "medium_low",
            "peak_hint": "City-center and beach/office-hour peaks can make the charging bay the bottleneck.",
        }
    return {
        "kind": "unknown",
        "label": "Parking unknown",
        "paid": None,
        "note": "Parking cost not published in the live feed; check signs before plugging in.",
        "free_likelihood": "unknown",
        "peak_hint": "No parking telemetry; prefer live-available stations and keep a backup within 10–15 minutes.",
    }


def transform_location(location: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    lat = safe_float((location.get("coordinates") or {}).get("latitude"))
    lon = safe_float((location.get("coordinates") or {}).get("longitude"))
    if lat is None or lon is None:
        return None

    connectors: List[Dict[str, Any]] = []
    energy_prices: List[float] = []
    parking_prices: List[float] = []
    available = charging = out = unknown = 0
    last_updates: List[str] = []
    max_kw = 0.0

    for evse in location.get("evses") or []:
        status = (evse.get("status") or "UNKNOWN").upper()
        if status == "AVAILABLE":
            available += 1
        elif status == "CHARGING":
            charging += 1
        elif status in {"OUTOFORDER", "INOPERATIVE", "BLOCKED", "REMOVED"}:
            out += 1
        else:
            unknown += 1
        if evse.get("status_updated_at"):
            last_updates.append(evse["status_updated_at"])
        if evse.get("last_updated"):
            last_updates.append(evse["last_updated"])
        for c in evse.get("connectors") or []:
            e_prices, p_prices, human = extract_prices(c)
            energy_prices.extend(e_prices)
            parking_prices.extend(p_prices)
            power_w = safe_float(c.get("max_electric_power")) or 0
            power_kw = power_w / 1000 if power_w > 1000 else power_w
            max_kw = max(max_kw, power_kw)
            std = c.get("standard") or "UNKNOWN"
            connector_record = {
                "standard": std,
                "label": connector_label(std),
                "format": c.get("format"),
                "power_kw": round(power_kw, 1) if power_kw else None,
                "energy_prices": sorted({round(x, 4) for x in e_prices}),
                "parking_prices": sorted({round(x, 4) for x in p_prices}),
                "pricing": connector_pricing_windows(c),
                "human_tariffs": human[:4],
                "status": status,
                "tesla_ok": std in TESLA_OK_STANDARDS,
                "fast": (std in FAST_STANDARDS and power_kw >= 43) or power_kw >= 43,
            }
            connectors.append(connector_record)

    energy_prices = [p for p in energy_prices if p >= 0]
    price_min = min(energy_prices) if energy_prices else None
    price_max = max(energy_prices) if energy_prices else None
    is_free = price_min == 0 if price_min is not None else False
    is_cheap = price_min is not None and price_min <= PRICE_MAX_DEFAULT
    tesla_ok = any(c.get("tesla_ok") for c in connectors)
    fast = any(c.get("fast") for c in connectors)

    gmaps = f"https://www.google.com/maps/search/?api=1&query={lat:.6f},{lon:.6f}"
    apple = f"https://maps.apple.com/?ll={lat:.6f},{lon:.6f}&q={urllib.parse.quote(location.get('name') or 'EV charger')}"
    owner = location.get("owner") or {}
    parking = infer_parking(location, parking_prices)
    # The live station provenance is MapaREVE. A provider homepage is a useful owner
    # handoff, but it is not copied station evidence and must not replace the live source.
    source_url = "https://www.mapareve.es/"
    source_evidence = "Live OCPI status/tariffs from MapaREVE public web API."
    source_rows = [{"name": "MapaREVE public live OCPI", "url": "https://www.mapareve.es/", "evidence": source_evidence}]
    last_seen = max(last_updates) if last_updates else None
    score = 0
    score += 50 if is_free else 35 if is_cheap else 0
    score += 22 if available else -8 if charging else 0
    score += 12 if tesla_ok else 0
    score += 10 if fast else 0
    score += min(max_kw, 250) / 25 if max_kw else 0

    return enrich_station({
        "id": location.get("id"),
        "name": location.get("name") or "Unnamed charger",
        "address": location.get("address"),
        "postal_code": location.get("postal_code"),
        "city": infer_city(location),
        "country": location.get("country"),
        "lat": lat,
        "lon": lon,
        "status": (location.get("status") or "UNKNOWN").upper(),
        "available": available,
        "charging": charging,
        "out_of_service": out,
        "unknown": unknown,
        "total_evse": location.get("total_evse") or (available + charging + out + unknown),
        "owner": {
            "name": owner.get("name"),
            "website": owner.get("website"),
            "phone": owner.get("phone"),
            "logo": owner.get("logo"),
        },
        "connectors": connectors,
        "connector_labels": sorted({c["label"] for c in connectors}),
        "max_power_kw": round(max_kw, 1) if max_kw else None,
        "price_min": round(price_min, 4) if price_min is not None else None,
        "price_max": round(price_max, 4) if price_max is not None else None,
        "is_free": is_free,
        "is_cheap": is_cheap,
        "tesla_ok": tesla_ok,
        "fast": fast,
        "parking": parking,
        "last_seen": last_seen,
        "source": "MapaREVE public live OCPI",
        "source_url": source_url,
        "sources": source_rows,
        "source_type": location.get("source_type"),
        "reliability": "official-live" if location.get("source_type") == "OCPI" else "registry",
        "score": round(score, 2),
        "navigation": {"google": gmaps, "apple": apple},
    }, live=True)


def longitude_intervals(bbox: Dict[str, float]) -> List[Tuple[float, float]]:
    west, east = bbox["longitude_sw"], bbox["longitude_ne"]
    return [(west, east)] if west <= east else [(west, 180.0), (-180.0, east)]


def in_bbox(lat: Optional[float], lon: Optional[float], bbox: Dict[str, float]) -> bool:
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return False
    return bbox["latitude_sw"] <= lat <= bbox["latitude_ne"] and any(
        west <= lon <= east for west, east in longitude_intervals(bbox)
    )


def bbox_contains(outer: Optional[Dict[str, float]], inner: Dict[str, float]) -> bool:
    """Containment includes dateline-crossing boxes; malformed input fails closed."""
    if not outer:
        return False
    try:
        return (
            outer["latitude_sw"] <= inner["latitude_sw"]
            and outer["latitude_ne"] >= inner["latitude_ne"]
            and all(any(ow <= iw and oe >= ie for ow, oe in longitude_intervals(outer))
                    for iw, ie in longitude_intervals(inner))
        )
    except (KeyError, TypeError):
        return False


def bbox_intersects(a: Dict[str, float], b: Dict[str, float]) -> bool:
    return (a["latitude_sw"] <= b["latitude_ne"] and a["latitude_ne"] >= b["latitude_sw"]
            and any(aw <= be and ae >= bw for aw, ae in longitude_intervals(a)
                    for bw, be in longitude_intervals(b)))


def live_geography(bbox: Dict[str, float]) -> Dict[str, bool]:
    """Coverage envelopes for the configured feeds, not worldwide live authority."""
    return {
        "mapareve": bbox_intersects(bbox, SPAIN_BBOX),
        "gaia": any(bbox_intersects(bbox, bbox_from_center(seed["lat"], seed["lon"], 30.0)) for seed in GAIA_STATION_SEEDS),
    }


def station_passes_route_filters(st: Dict[str, Any], *, max_price: float, fast_only: bool) -> bool:
    """Shared fast_only/max_price gate for merged live rows.

    Mirrors the predicate Gaia/Electromaps already apply to their own rows
    when merging (`price_min is None or price_min <= max_price`, plus fast_only).
    Applied to the MapaREVE-origin `stations` list in live_response so reused
    stale/broader-fallback rows are re-filtered fail-closed, not just bbox
    (server.py engine honesty rule: "stale broader fallback is re-filtered
    fail-closed"). A no-op for a fresh fetch, since fetch_mapareve already only
    returns free/cheap rows plus rows within the request's own max_price.
    """
    if fast_only and not st.get("fast"):
        return False
    price_min = euro_price(st)
    return price_min is None or price_min <= max_price


def normalize_longitude(lon: float) -> float:
    return (lon + 180.0) % 360.0 - 180.0


def bbox_center(bbox: Dict[str, float]) -> Tuple[float, float]:
    west, east = bbox["longitude_sw"], bbox["longitude_ne"]
    span = east - west if east >= west else east - west + 360.0
    return ((bbox["latitude_ne"] + bbox["latitude_sw"]) / 2.0,
            normalize_longitude(west + span / 2.0))


def clamp_bbox(bbox: Dict[str, float]) -> Tuple[Dict[str, float], bool]:
    """Bound a valid world viewport's live-query span without relocating its centre."""
    if not all(math.isfinite(v) for v in bbox.values()):
        raise ValueError("Viewport coordinates must be finite")
    sw_lat = max(-90.0, min(bbox["latitude_sw"], bbox["latitude_ne"]))
    ne_lat = min(90.0, max(bbox["latitude_sw"], bbox["latitude_ne"]))
    if sw_lat > ne_lat:
        raise ValueError("Latitude outside world bounds")
    west, east = bbox["longitude_sw"], bbox["longitude_ne"]
    if not (-180 <= west <= 180 and -180 <= east <= 180):
        raise ValueError("Longitude outside world bounds")
    out = {"latitude_sw": sw_lat, "latitude_ne": ne_lat, "longitude_sw": west, "longitude_ne": east}
    clat, clon = bbox_center(out)
    if ne_lat - sw_lat > VIEWPORT_MAX_SPAN_DEG:
        out["latitude_sw"] = max(-90.0, clat - VIEWPORT_MAX_SPAN_DEG / 2)
        out["latitude_ne"] = min(90.0, clat + VIEWPORT_MAX_SPAN_DEG / 2)
    lon_span = east - west if west <= east else east - west + 360.0
    max_lon_span = VIEWPORT_MAX_SPAN_DEG / max(0.35, math.cos(math.radians(clat)))
    if lon_span > max_lon_span:
        out["longitude_sw"] = normalize_longitude(clon - max_lon_span / 2)
        out["longitude_ne"] = normalize_longitude(clon + max_lon_span / 2)
    return out, out != bbox


def bbox_from_center(lat: float, lon: float, radius_km: float) -> Dict[str, float]:
    if not all(math.isfinite(v) for v in (lat, lon, radius_km)) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Invalid coordinates")
    radius_km = max(0.5, min(radius_km, 100.0))
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(0.35, math.cos(math.radians(lat))))
    return {
        "latitude_ne": min(90.0, lat + dlat),
        "longitude_ne": normalize_longitude(lon + dlon),
        "latitude_sw": max(-90.0, lat - dlat),
        "longitude_sw": normalize_longitude(lon - dlon),
    }


def resolve_viewport(params: Dict[str, List[str]]) -> Tuple[Dict[str, float], str, bool]:
    """Explicit bbox > coordinates with a bounded radius > the legacy default."""
    requested = parse_bbox_param(params)
    if requested:
        bbox, clamped = clamp_bbox(requested)
        return bbox, "explicit", clamped
    lat = safe_float((params.get("lat") or [None])[0])
    lon = safe_float((params.get("lon") or [None])[0])
    if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
        radius = safe_float((params.get("radius_km") or [None])[0])
        radius = VIEWPORT_DEFAULT_RADIUS_KM if radius is None else radius
        bbox, clamped = clamp_bbox(bbox_from_center(lat, lon, radius))
        return bbox, "geo", clamped or not 0.5 <= radius <= 100.0
    return dict(DEFAULT_BBOX), "default", False


def snapped_bbox_envelope(bbox: Dict[str, float]) -> Dict[str, float]:
    """Expand to a world-bounded cache tile; preserve dateline wrapping."""
    t = MAPAREVE_TILE_DEG
    return {
        "latitude_sw": max(-90.0, math.floor(bbox["latitude_sw"] / t) * t),
        "longitude_sw": max(-180.0, math.floor(bbox["longitude_sw"] / t) * t),
        "latitude_ne": min(90.0, math.ceil(bbox["latitude_ne"] / t) * t),
        "longitude_ne": min(180.0, math.ceil(bbox["longitude_ne"] / t) * t),
    }


def viewport_tile_key(bbox: Dict[str, float], source: str) -> str:
    """Cache-key prefix snapped to a bbox envelope.

    Empty for the default viewport so the legacy cache-key contract
    ("{max_price}:{available}:{fast}") is preserved for tests/back-compat.

    The old center-only key could reuse a narrow live fetch for a wider viewport that
    happened to share the same snapped center. Snapping the whole envelope keeps tiny pans
    cache-friendly while making the cached live coverage geometrically honest.
    """
    if source == "default":
        return ""
    envelope = snapped_bbox_envelope(bbox)
    return (
        f"{envelope['latitude_sw']:.2f},{envelope['longitude_sw']:.2f},"
        f"{envelope['latitude_ne']:.2f},{envelope['longitude_ne']:.2f}|"
    )


def engine_record_stage(
    stages: List[Dict[str, Any]],
    request_started: float,
    stage_started: float,
    name: str,
    **fields: Any,
) -> None:
    row = {
        "name": name,
        "duration_ms": round((time.perf_counter() - stage_started) * 1000, 1),
        "at_ms": round((time.perf_counter() - request_started) * 1000, 1),
    }
    row.update(fields)
    stages.append(row)


def engine_quality_meta(
    *,
    request_started: float,
    stages: List[Dict[str, Any]],
    stations: List[Dict[str, Any]],
    leads: List[Dict[str, Any]],
    bbox: Dict[str, float],
    viewport_source: str,
    viewport_clamped: bool,
    cache_key: str,
    include_leads: bool,
    max_price: float,
    available_only: bool,
    fast_only: bool,
    dropped: Dict[str, int],
) -> Dict[str, Any]:
    decisions: Dict[str, int] = {}
    live_verified = 0
    live_unverified = 0
    route_ready = 0
    for station in stations:
        level = str((station.get("decision") or {}).get("level") or "unknown")
        decisions[level] = decisions.get(level, 0) + 1
        verification = station.get("verification") or {}
        if verification.get("live_verified") is True:
            live_verified += 1
        else:
            live_unverified += 1
        if route_ready_now(station):
            route_ready += 1

    lead_tiers: Dict[str, int] = {}
    lead_map_eligible = 0
    for lead in leads:
        tier = source_health_tier(lead)
        lead_tiers[tier] = lead_tiers.get(tier, 0) + 1
        if source_health_map_eligible(lead):
            lead_map_eligible += 1

    return {
        "version": ENGINE_VERSION,
        "elapsed_ms": round((time.perf_counter() - request_started) * 1000, 1),
        "viewport": {"bbox": bbox, "source": viewport_source, "clamped": viewport_clamped, "cache_key": cache_key},
        "filters": {"include_leads": include_leads, "max_price": max_price, "available_only": available_only, "fast_only": fast_only},
        "counts": {
            "stations": len(stations),
            "leads": len(leads),
            "route_ready_live": route_ready,
            "live_verified": live_verified,
            "live_unverified": live_unverified,
            "lead_map_eligible": lead_map_eligible,
            "decisions": dict(sorted(decisions.items())),
            "lead_tiers": dict(sorted(lead_tiers.items())),
            "dropped": dropped,
        },
        "stages": stages[:16],
        "honesty": {
            "route_ready_rule": "non-stale non-lead row, decision=go, verification.live_verified=true, no contradictory verification level, and at least one available connector/status",
            "lead_rule": "leads remain source-backed suggestions; corroboration raises existence confidence but never live verification",
            "cache_rule": "live cache keys use snapped bbox envelopes plus route filters; stale broader fallback is re-filtered fail-closed",
        },
    }


def mapareve_payload(
    max_price: float,
    available_only: bool = False,
    fast_only: bool = False,
    bbox: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    bbox = bbox or VALENCIA_BBOX
    power_min = 43 if fast_only else 0
    lat_span = abs(bbox["latitude_ne"] - bbox["latitude_sw"])
    zoom = 9 if lat_span >= 1.0 else (11 if lat_span >= 0.35 else 13)
    payload = {
        **bbox,
        "zoom": zoom,
        "cpo_ids": [],
        "only_ocpi": True,
        "available": bool(available_only),
        "power_min": power_min,
        "power_max": None,
        "connector_types": ["IEC_62196_T2_COMBO", "IEC_62196_T2"],
        "energy_price_min": 0,
        "payment_methods": [],
        "facilities": [],
    }
    if max_price < 1.0:
        payload["energy_price_max"] = max(MAPAREVE_PRICE_QUERY_MAX, max_price)
    return payload


MAPAREVE_PARTIAL_DETAILS = {
    "pagination_cap": (
        "Live results may be incomplete: MapaREVE reached its page limit.",
        "Live results may be incomplete because the MapaREVE page limit was reached. "
        "Refresh or narrow the map before routing.",
    ),
    "upstream_error": (
        "Live results may be incomplete: MapaREVE stopped before every page was received.",
        "Live results may be incomplete because the MapaREVE source stopped before every page "
        "was received. Refresh or narrow the map before routing.",
    ),
    "fetch_budget": (
        "Live results may be incomplete: the MapaREVE fetch time budget elapsed.",
        "Live results may be incomplete because the MapaREVE fetch time budget elapsed before "
        "every page was received. Refresh or narrow the map before routing.",
    ),
    "invalid_continuation": (
        "Live results may be incomplete: a MapaREVE page continuation could not be read.",
        "Live results may be incomplete because a MapaREVE page continuation could not be read. "
        "Refresh or narrow the map before routing.",
    ),
}


def mapareve_partial_details(reason: Any) -> Tuple[str, str]:
    """Return bounded driver copy for every incomplete MapaREVE fetch path.

    Upstream exceptions remain server-only; the client only needs to know that the
    result set stopped early and how to recover safely.
    """
    return MAPAREVE_PARTIAL_DETAILS.get(
        str(reason or ""),
        (
            "Live results may be incomplete: MapaREVE did not return every page.",
            "Live results may be incomplete because MapaREVE did not return every page. "
            "Refresh or narrow the map before routing.",
        ),
    )


def public_get_urlopen(request: urllib.request.Request, *, timeout: float) -> Any:
    """Open a public source request through the reviewed transport policy.

    GET is allowed. The only non-GET requests permitted are the owner-approved read-only
    data-query exceptions in scripts/source_transport.py (exact HTTPS host/path/body shape,
    no redirects, bounded budget); anything else raises SourcePolicyBlocked before any
    connection is opened. SourcePolicyBlocked is re-exported from that module.
    """
    return policy_urlopen(request, timeout=timeout)


def _fetch_mapareve_page(page: int, body: bytes) -> Dict[str, Any]:
    req = urllib.request.Request(
        f"{MAPAREVE_URL}?page={page}&per_page={MAPAREVE_PER_PAGE}",
        data=body,
        headers=LIVE_HEADERS,
        method="POST",
    )
    # MapaREVE's public map query is a read-only POST data query: allowed only
    # for this exact host/path/body shape and never redirected (see transport).
    with public_get_urlopen(req, timeout=MAPAREVE_PAGE_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_mapareve_pages_parallel(pages: List[int], body: bytes, budget_left: float) -> List[Tuple[int, Optional[Dict[str, Any]], Optional[str]]]:
    """Fetch known page numbers concurrently; results stay in page order.

    Each entry is (page, data, failure) where failure is None, "upstream_error" or
    "fetch_budget". A page not yet started when the time budget runs out is skipped,
    so the overall fetch stays inside MAPAREVE_FETCH_BUDGET_SECONDS.
    """
    deadline = time.monotonic() + budget_left

    def one(page: int) -> Tuple[int, Optional[Dict[str, Any]], Optional[str]]:
        if time.monotonic() > deadline:
            return page, None, "fetch_budget"
        try:
            return page, _fetch_mapareve_page(page, body), None
        except Exception as exc:
            _log_upstream_error(f"MapaREVE page {page} fetch failed", exc)
            return page, None, "upstream_error"

    workers = max(1, min(MAPAREVE_PAGE_CONCURRENCY, len(pages)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="volta-mapareve") as executor:
        return list(executor.map(one, pages))


def fetch_mapareve(max_price: float = PRICE_MAX_DEFAULT, available_only: bool = False, fast_only: bool = False, bbox: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    bbox = bbox or VALENCIA_BBOX
    body = json.dumps(mapareve_payload(max_price, available_only, fast_only, bbox)).encode("utf-8")
    page = 1
    started_at = time.time()
    all_rows: List[Dict[str, Any]] = []
    errors: List[str] = []
    total_count = None
    fetched_pages = 0
    next_page: Optional[int] = None
    # A bad environment value must not turn the bounded fetch into an unbounded one.
    # One page remains useful, but any advertised continuation is explicitly partial.
    max_pages = max(1, MAPAREVE_MAX_PAGES)
    partial_reason: Optional[str] = None

    def read_continuation(data: Dict[str, Any], current: int) -> Tuple[bool, Optional[int]]:
        """(done, next page). Raises ValueError for a malformed continuation."""
        pagination = data.get("pagination") or {}
        # A missing/null continuation is the only trustworthy terminal marker.  Falsy
        # values such as 0 or an empty string are malformed page cursors, not proof
        # that the upstream returned every page.
        if "next" not in pagination or pagination.get("next") is None:
            return True, None
        continuation = pagination["next"]
        if isinstance(continuation, bool):
            raise ValueError("boolean page continuation")
        parsed_next_page = int(continuation)
        if parsed_next_page <= current:
            raise ValueError("non-progressing page continuation")
        return False, parsed_next_page

    while True:
        if time.time() - started_at > MAPAREVE_FETCH_BUDGET_SECONDS:
            errors.append(f"MapaREVE fetch budget exceeded after {MAPAREVE_FETCH_BUDGET_SECONDS:.1f}s")
            partial_reason = "fetch_budget"
            break
        try:
            data = _fetch_mapareve_page(page, body)
        except Exception as exc:
            _log_upstream_error(f"MapaREVE page {page} fetch failed", exc)
            errors.append(f"MapaREVE page {page}: {type(exc).__name__}")
            # A previous page may legitimately contain no rows, but it still proves a
            # continuation was in progress. Preserve those results and mark them partial
            # rather than escalating the whole response into an all-or-nothing failure.
            if fetched_pages:
                partial_reason = "upstream_error"
                break
            raise
        all_rows.extend(data.get("data") or [])
        fetched_pages += 1
        total_count = (data.get("pagination") or {}).get("total_count", total_count)
        try:
            done, parsed_next_page = read_continuation(data, page)
        except (TypeError, ValueError):
            errors.append("MapaREVE returned an invalid page continuation")
            partial_reason = "invalid_continuation"
            break
        if done:
            break
        next_page = parsed_next_page
        if fetched_pages >= max_pages:
            partial_reason = "pagination_cap"
            break
        page = next_page

        # Pages used to be fetched one after another (~1-4s each, ~23 pages for a wide
        # Valencia view), so a live refresh took ~35s. Once total_count tells us how many
        # pages remain, fetch them concurrently: the same pages, page cap and time budget,
        # only fewer seconds. Results are consumed in page order, so a failed or skipped
        # page ends the run as partial exactly as the sequential loop did.
        if not (isinstance(total_count, int) and not isinstance(total_count, bool) and total_count > 0):
            continue
        last_page = min(math.ceil(total_count / MAPAREVE_PER_PAGE), page + (max_pages - fetched_pages) - 1)
        if last_page <= page:
            continue
        if time.time() - started_at > MAPAREVE_FETCH_BUDGET_SECONDS:
            continue  # the loop head records the budget stop
        budget_left = MAPAREVE_FETCH_BUDGET_SECONDS - (time.time() - started_at)
        stop = False
        for batch_page, batch_data, failure in _fetch_mapareve_pages_parallel(list(range(page, last_page + 1)), body, budget_left):
            if failure == "fetch_budget":
                errors.append(f"MapaREVE fetch budget exceeded after {MAPAREVE_FETCH_BUDGET_SECONDS:.1f}s")
            elif failure:
                errors.append(f"MapaREVE page {batch_page}: upstream error")
            if failure:
                partial_reason = failure
                next_page = batch_page
                stop = True
                break
            assert batch_data is not None
            all_rows.extend(batch_data.get("data") or [])
            fetched_pages += 1
            total_count = (batch_data.get("pagination") or {}).get("total_count", total_count)
            try:
                done, parsed_next_page = read_continuation(batch_data, batch_page)
            except (TypeError, ValueError):
                errors.append("MapaREVE returned an invalid page continuation")
                partial_reason = "invalid_continuation"
                next_page = None
                stop = True
                break
            if done:
                next_page = None
                stop = True
                break
            next_page = parsed_next_page
            if batch_page == last_page:
                break
            if parsed_next_page != batch_page + 1:
                # The upstream skipped numbering; pages fetched past here may not be
                # the continuation it advertised. Resume sequentially from its cursor.
                break
        if stop:
            break
        if next_page is None:
            break
        if fetched_pages >= max_pages:
            partial_reason = "pagination_cap"
            break
        page = next_page
    transformed = [x for x in (transform_location(r) for r in all_rows) if x]
    # Bound to the requested viewport (the upstream bbox can return edge hits).
    transformed = [x for x in transformed if in_bbox(x.get("lat"), x.get("lon"), bbox)]
    # Free/cheap rows always survive (unchanged; is_cheap is the app's fixed 0.25
    # concept, not the request). Pricier rows survive up to the REQUEST's own
    # max_price instead of being dropped unconditionally — max_price was
    # previously ignored here entirely, so widening the price filter (e.g. to
    # 5.0) never surfaced a priced MapaREVE row. Rows with no parsed price stay
    # dropped from MapaREVE: unknown price is not presentable, which preserves
    # the existing fail-closed behavior for this source.
    transformed = [
        x for x in transformed
        if x.get("is_free") or x.get("is_cheap") or (x.get("price_min") is not None and x["price_min"] <= max_price)
    ]
    transformed.sort(key=lambda x: (-x["score"], x["price_min"] if x["price_min"] is not None else 999, -x.get("available", 0)))
    payload = {
        "stations": transformed,
        "raw_count": len(all_rows),
        "total_count": total_count if total_count is not None else len(all_rows),
        "fetched_at": now_iso(),
        "source": "mapareve-public-web-api",
        "pagination": {
            "fetched_pages": fetched_pages,
            "max_pages": max_pages,
            **({"next_page": next_page} if next_page is not None else {}),
        },
    }
    if partial_reason:
        partial_summary, partial_warning = mapareve_partial_details(partial_reason)
        payload["partial"] = True
        payload["partial_reason"] = partial_reason
        payload["partial_summary"] = partial_summary
        payload["warning"] = partial_warning
    if errors:
        payload["errors"] = errors[:5]
        if not partial_reason:
            payload["warning"] = "MapaREVE refresh returned partial data; verify live source before routing."
    return payload


def fallback_source_for_lead(lead: Dict[str, Any], default_source: str) -> Dict[str, str]:
    name = f"{lead.get('name') or ''} {lead.get('source') or ''}".lower()
    if "lidl" in name:
        return {
            "name": "Lidl official free list",
            "url": "https://empresa.lidl.es/sostenibilidad/bueno-para-el-planeta/proteger-el-clima/construccion-y-logistica-sostenible/puntos-de-recarga",
            "evidence": lead.get("source_notes") or "Manual lead tied to Lidl's official free-charging policy/list; verify exact store in source/app.",
        }
    if "mercadona" in name:
        return {
            "name": "Mercadona store locator",
            "url": "https://info.mercadona.es/es/supermercados",
            "evidence": lead.get("source_notes") or "Manual lead; verify exact store charger and price in Mercadona/provider source.",
        }
    if "tesla" in name:
        return {
            "name": "Tesla Find Us",
            "url": "https://www.tesla.com/findus",
            "evidence": lead.get("source_notes") or "Manual convenience fallback; verify current Supercharger price/availability in Tesla app.",
        }
    if "municipal" in name or "valència" in name or "valencia" in name:
        return {
            "name": "València OpenData",
            "url": "https://geoportal.valencia.es/",
            "evidence": lead.get("source_notes") or "Manual/stale municipal lead; verify current source and operator app before routing.",
        }
    nav = (lead.get("navigation") or {}).get("google") or f"https://www.google.com/maps/search/?api=1&query={lead.get('lat')},{lead.get('lon')}"
    return {
        "name": default_source,
        "url": nav,
        "evidence": lead.get("source_notes") or "Manual lead fallback; verify in provider app and onsite signs.",
    }


def _normalize_lead(lead: Dict[str, Any], default_source: str) -> Dict[str, Any]:
    lat = lead.get("lat")
    lon = lead.get("lon")
    lead.setdefault("source", default_source)
    lead.setdefault("reliability", "manual-check-needed")
    lead.setdefault("status", "VERIFY")
    lead.setdefault("available", 0)
    lead.setdefault("total_evse", None)
    lead.setdefault("source_count", len(lead.get("sources") or [lead.get("source")]))
    lead.setdefault("confidence", 50)
    lead["lead"] = True
    reliability = str(lead.get("reliability") or "")
    if default_source == "manual research lead" and reliability.startswith("official"):
        raw = dict(lead.get("raw") or {})
        raw["manual_reliability_downgraded_from"] = reliability
        lead["raw"] = raw
        lead["reliability"] = "manual-check-needed"
        note = str(lead.get("source_notes") or "").strip()
        guard_note = "Manual seed cannot claim official verification; verify in a public parser or ingestion source before map placement."
        lead["source_notes"] = f"{guard_note} {note}".strip()
    if not lead.get("sources"):
        fallback = fallback_source_for_lead(lead, default_source)
        lead["sources"] = [fallback]
        lead.setdefault("source_url", fallback["url"])
        lead["source_count"] = 1
    if lead.get("max_power_kw") is not None:
        lead["fast"] = bool(safe_float(lead.get("max_power_kw")) and safe_float(lead.get("max_power_kw")) >= 43)
    lead.setdefault("navigation", {
        "google": f"https://www.google.com/maps/search/?api=1&query={lat},{lon}",
        "apple": f"https://maps.apple.com/?ll={lat},{lon}&q={urllib.parse.quote(lead.get('name','EV charger'))}",
    })
    return enrich_station(lead, live=False)


def load_seed_leads() -> List[Dict[str, Any]]:
    """Normalized manual seed leads (no bbox filter). Backed by the same
    (db mtime, seeds mtime)-keyed memo as load_intelligence/load_all_leads
    (plans/007 step 2) -- always hands out a deepcopy, never the cached list
    itself, since callers mutate returned rows."""
    records = load_intelligence_meta().get("records")
    if isinstance(records, IndexedRecords) or len(records or []) > _INTEL_SCOPE_THRESHOLD:
        return [_normalize_lead(row, "manual research lead") for row in _raw_seed_records() if not reject_station_reason(row)]
    _intel, _intel_coords, seeds, _seeds_coords = _intel_enriched_pool()
    return deepcopy(seeds)


# Parsed intel DB cached by file mtime — at national scale we must NOT re-read/re-parse the
# (large) JSON on every request. Normalize lazily and only for in-viewport records.
_INTEL_RAW: Dict[str, Any] = {"mtime": None, "data": None}
_INTEL_RAW_LOCK = threading.Lock()


def intelligence_path() -> Path:
    """Resolve one immutable runtime bundle, or retain the repository default.

    Runtime data stays outside Git so a code update cannot rewind charger data.
    Resolving the symlink once also binds each cache lookup/read to one bundle.
    """
    return Path(os.environ.get("VOLTA_INTELLIGENCE_DB") or DATA / "free_intelligence.json").expanduser().resolve()


def _interning_object_hook() -> Any:
    """Share object keys and repeated values across independently decoded records.

    The streaming decoder clears its key memo after each row. Interning keys here
    avoids allocating the same schema strings hundreds of thousands of times.
    """
    seen: Dict[str, str] = {}

    def hook(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
        return {sys.intern(key): seen.setdefault(value, value) if isinstance(value, str) else value for key, value in pairs}

    return hook


def load_intelligence_meta() -> Dict[str, Any]:
    """Raw intel dict (records NOT normalized) — for summary counts. mtime-cached."""
    p = intelligence_path()
    if not p.exists():
        return {"generated_at": None, "total_records": 0, "records": [], "errors": ["Run scripts/ingest_sources.py to build intelligence DB"]}
    mtime = (str(p), p.stat().st_mtime_ns)
    indexed_path = p.suffix.lower() in {".sqlite", ".sqlite3", ".db"}
    if indexed_path:
        dependencies = (DATA / "source_policy_exclusions.json", DATA / "seed_leads.json")
        mtime += tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size) if path.exists() else None for path in dependencies)
    with _INTEL_RAW_LOCK:
        if _INTEL_RAW.get("mtime") != mtime:
            if indexed_path:
                index = IntelligenceIndex(p, policy_sha256=file_sha256(DATA / "source_policy_exclusions.json"))
                payload = index.metadata()
                payload["records"] = index.records
                _validate_index_summaries(payload)
                _INTEL_RAW.update(data=payload, mtime=mtime)
                return payload
            excluded_count = 0
            def clean_record(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                nonlocal excluded_count
                kept, removed = partition_records([row], SOURCE_POLICY_EXCLUSIONS)
                excluded_count += len(removed)
                return kept[0] if kept else None
            payload = load_json_recordset(p, object_pairs_hook=_interning_object_hook(), record_transform=clean_record)
            allowed = payload.get("records") or []
            payload["total_records"] = len(allowed)
            payload["price_evidence_counts"] = price_evidence_counts(allowed)
            payload["recordset_sha256"] = runtime_recordset_sha256(allowed)
            payload["recordset_records"] = len(allowed)
            payload["source_policy_excluded_records"] = excluded_count
            _INTEL_RAW["data"] = payload
            _INTEL_RAW["mtime"] = mtime
        return _INTEL_RAW["data"] or {"records": []}


def runtime_recordset_sha256(records: List[Dict[str, Any]]) -> str:
    """Same canonical hash as source_policy without a second full JSON string/bytes."""
    digest = hashlib.sha256()
    digest.update(b"[")
    for position, row in enumerate(sorted(records, key=lambda item: str(item.get("id") or ""))):
        if position:
            digest.update(b",")
        digest.update(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    digest.update(b"]")
    return digest.hexdigest()


Coords = List[Tuple[Optional[float], Optional[float]]]


def _coords_for(records: List[Dict[str, Any]]) -> Coords:
    return [(safe_float(r.get("lat")), safe_float(r.get("lon"))) for r in records]


def _filter_by_coords(records: List[Dict[str, Any]], coords: Coords, bbox: Optional[Dict[str, float]]) -> List[Dict[str, Any]]:
    if bbox is None:
        return records
    return [r for r, (lat, lon) in zip(records, coords) if in_bbox(lat, lon, bbox)]


# plans/007 step 2: a SECOND memo layer on top of _INTEL_RAW. _INTEL_RAW only caches the
# raw parsed JSON -- every call to load_intelligence/load_all_leads still re-deep-copied
# and re-ran the FULL enrich_station pipeline (verification/decision/pricing/parking/...)
# on every in-view record, every single request (~190ms warm on the ~955-record national
# DB; see qa/run-log.md's plans/007 iteration for measured before/after). _INTEL_ENRICHED
# instead enriches EVERY record once (no bbox filter) per (db mtime, seeds mtime) key --
# both, because load_all_leads folds seed leads into the same view -- plus a prebuilt
# (lat, lon) coord array parallel to each list so a bbox scan avoids repeated dict
# .get()/safe_float() overhead. On a hit, callers get a bbox-filtered SLICE, deepcopy'd --
# NEVER the cached objects themselves (callers mutate returned rows: corroboration,
# distance fields, source unions -- a shared cache must keep handing out copies). Guarded
# by _CACHE_LOCKS["intel"]; the dict is never mutated in place after publish (only
# wholesale-replaced on a rebuild), so callers holding an old list reference are never
# caught mid-update -- same convention as CACHE["payload"] elsewhere in this file.
_INTEL_SCOPED: Dict[Any, Any] = {}
_INTEL_SCOPE_THRESHOLD = 5000
_INTEL_ENRICHED: Dict[str, Any] = {"key": None, "intel": None, "intel_coords": None, "seeds": None, "seeds_coords": None}


def _intel_enriched_pool() -> Tuple[List[Dict[str, Any]], Coords, List[Dict[str, Any]], Coords]:
    """Fully enriched, UNFILTERED intel + seed-lead universes, memoized by (db mtime,
    seeds mtime). Returns (intel_records, intel_coords, seed_records, seed_coords) -- a
    consistent snapshot taken under the lock. Never mutate the returned lists/dicts in
    place; slice + deepcopy (see load_intelligence/load_all_leads/load_seed_leads)."""
    db_path = intelligence_path()
    seeds_path = DATA / "seed_leads.json"
    key = (
        (str(db_path), db_path.stat().st_mtime_ns) if db_path.exists() else None,
        seeds_path.stat().st_mtime if seeds_path.exists() else None,
    )
    with _CACHE_LOCKS["intel"]:
        if _INTEL_ENRICHED.get("key") == key and _INTEL_ENRICHED.get("intel") is not None:
            return (
                _INTEL_ENRICHED["intel"], _INTEL_ENRICHED["intel_coords"],
                _INTEL_ENRICHED["seeds"], _INTEL_ENRICHED["seeds_coords"],
            )

    # Enrichment is CPU-only and idempotent for a given key -- build outside the lock
    # (the same fetch-outside-lock pattern the network caches use: only the dict
    # read-decide and the publish/write happen under lock, never the expensive part).
    raw = load_intelligence_meta()
    intel_records = [
        normalized for r in raw.get("records", [])
        for normalized in [_normalize_lead(deepcopy(r), "free/cheap intelligence DB")]
        if not reject_station_reason(normalized)
    ]
    seed_records: List[Dict[str, Any]] = []
    if seeds_path.exists():
        with seeds_path.open("r", encoding="utf-8") as f:
            raw_seeds = json.load(f)
        seed_records = [
            normalized for lead in raw_seeds
            for normalized in [_normalize_lead(lead, "manual research lead")]
            if source_policy_record_allowed(lead)
            if not reject_station_reason(normalized)
        ]
    intel_coords = _coords_for(intel_records)
    seed_coords = _coords_for(seed_records)
    with _CACHE_LOCKS["intel"]:
        _INTEL_ENRICHED["key"] = key
        _INTEL_ENRICHED["intel"] = intel_records
        _INTEL_ENRICHED["intel_coords"] = intel_coords
        _INTEL_ENRICHED["seeds"] = seed_records
        _INTEL_ENRICHED["seeds_coords"] = seed_coords
    return intel_records, intel_coords, seed_records, seed_coords


def _raw_seed_records() -> List[Dict[str, Any]]:
    path = DATA / "seed_leads.json"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [row for row in json.load(handle) if source_policy_record_allowed(row)]


def _scoped_intelligence_pool(bbox: Optional[Dict[str, float]]) -> Tuple[List[Dict[str, Any]], Coords, List[Dict[str, Any]], Coords]:
    """Keep small legacy bundles shared; enrich only the selected viewport at world scale."""
    raw = load_intelligence_meta()
    records = raw.get("records")
    indexed = isinstance(records, IndexedRecords)
    if not indexed and (bbox is None or len(records or []) <= _INTEL_SCOPE_THRESHOLD):
        return _intel_enriched_pool()
    if indexed and bbox is None:
        raise ValueError("Indexed intelligence requires a viewport; use paginated /api/intel for catalogue inspection")
    seed_path = DATA / "seed_leads.json"
    key = (id(raw), tuple(sorted(bbox.items())), seed_path.stat().st_mtime_ns if seed_path.exists() else None)
    with _CACHE_LOCKS["intel"]:
        cached = _INTEL_SCOPED.get(key)
    if cached is not None:
        return cached
    tables = []
    scoped_records = records.iter_bbox(bbox) if indexed else records or []
    for rows, source in ((scoped_records, "free/cheap intelligence DB"), (_raw_seed_records(), "manual research lead")):
        selected = []
        for row in rows:
            if in_bbox(safe_float(row.get("lat")), safe_float(row.get("lon")), bbox):
                normalized = _normalize_lead(deepcopy(row), source)
                if not reject_station_reason(normalized):
                    selected.append(normalized)
        tables.append(selected)
    value = (tables[0], _coords_for(tables[0]), tables[1], _coords_for(tables[1]))
    with _CACHE_LOCKS["intel"]:
        _INTEL_SCOPED[key] = value
        while len(_INTEL_SCOPED) > 8:
            _INTEL_SCOPED.pop(next(iter(_INTEL_SCOPED)))
    return value


def intelligence_response(params: Dict[str, List[str]]) -> Dict[str, Any]:
    """Bound public inspection requests; the canonical artifact fingerprint remains intact."""
    raw = load_intelligence_meta()
    bbox = parse_bbox_param(params)
    limit = max(1, min(safe_int((params.get("limit") or [1000])[0]) or 1000, 2500))
    offset = max(0, min(safe_int((params.get("offset") or [0])[0]) or 0, len(raw.get("records") or [])))
    if isinstance(raw.get("records"), IndexedRecords):
        selected, matched = raw["records"].index.query(bbox=bbox, offset=offset, limit=limit)
    else:
        matches = (row for row in raw.get("records") or [] if bbox is None or in_bbox(safe_float(row.get("lat")), safe_float(row.get("lon")), bbox))
        selected = []
        matched = 0
        for row in matches:
            if offset <= matched < offset + limit:
                selected.append(row)
            matched += 1
    records = [_normalize_lead(deepcopy(row), "free/cheap intelligence DB") for row in selected]
    result = {key: value for key, value in raw.items() if key != "records"}
    result["records"] = _refresh_time_derived_fields(attach_community_rows(records))
    result["pagination"] = {"offset": offset, "limit": limit, "returned": len(records), "matched": matched,
                            "next_offset": offset + len(records) if offset + len(records) < matched else None}
    return result


def _refresh_time_derived_fields(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Recompute the two enrich_station outputs that go stale between _INTEL_ENRICHED
    pool rebuilds -- pure, O(n) arithmetic on an already-deepcopied in-view subset,
    NEVER a re-enrichment (that would defeat the memo plans/007 step 2 exists for).

    plans/007 REVISE (adviser Finding 4, Medium; qa/defects.csv D-156): _intel_enriched_pool() stamps
    freshness_minutes=age_minutes(freshness_timestamp(record)) (and the
    recommendation_score derived from it) exactly once per (db mtime, seeds mtime)
    key -- up to a full DB-refresh
    interval (about a week) later, every card served from that pool was silently
    showing an evidence-age/score frozen at pool-build time. The SPA's ageText
    (frontend/src/lib/format.ts) prefers freshness_minutes over recomputing from
    the row timestamp, so this reached rendered lead-age text on both /api/chargers
    leads and /api/intel. recommendation_score is recomputed AFTER freshness_minutes since
    it reads that field (see recommendation_score's freshness term); mutating the
    caller's own already-deepcopied rows in place, never the memo pool itself.
    """
    for r in records:
        r["freshness_minutes"] = age_minutes(freshness_timestamp(r))
        r["recommendation_score"] = recommendation_score(r)
    return records


def load_intelligence(bbox: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """Intel DB with normalized records. When bbox is given, only in-view records are
    returned (national-scale perf) -- normalization/enrichment itself is memoized by
    _intel_enriched_pool (step 2), so a bbox scan is a filter + deepcopy of the already-
    enriched cached list, never a re-normalize. Callers always get their own copy.
    freshness_minutes/recommendation_score are refreshed on every call (Finding 4)."""
    raw = load_intelligence_meta()
    data = dict(raw)
    intel_records, intel_coords, _seeds, _seeds_coords = _scoped_intelligence_pool(bbox)
    data["records"] = _refresh_time_derived_fields(attach_community_rows(deepcopy(_filter_by_coords(intel_records, intel_coords, bbox))))
    return data


def load_all_leads(bbox: Optional[Dict[str, float]] = None) -> List[Dict[str, Any]]:
    intel_records, intel_coords, seed_records, seed_coords = _scoped_intelligence_pool(bbox)
    intel = deepcopy(_filter_by_coords(intel_records, intel_coords, bbox))
    manual = deepcopy(_filter_by_coords(seed_records, seed_coords, bbox))
    seen: Dict[Any, Dict[str, Any]] = {}
    out: List[Dict[str, Any]] = []
    for lead in intel + manual:
        lat = safe_float(lead.get("lat"))
        lon = safe_float(lead.get("lon"))
        if lat is not None and lon is not None:
            key = ("coord", round(lat, 4), round(lon, 4))
        else:
            key = ("name", str(lead.get("name", "")).lower()[:48])
        if key in seen:
            preserve_price_evidence(seen[key], lead)
            continue
        seen[key] = lead
        out.append(lead)
    # Finding 4: refresh the two time-derived fields on the final (post-dedup)
    # in-view subset -- see _refresh_time_derived_fields's docstring.
    return _refresh_time_derived_fields(attach_community_rows(out))


# --- Plan 013: free-first registry + community evidence ---------------------------------
# Community evidence (check-ins / photos / suggested points, community.py) is its own
# tier: apply_community_evidence attaches a per-station block and may DOWNGRADE a "go"
# decision after repeated recent failures — it never upgrades one and never touches
# verification.live_verified. Attachment happens on per-request copies only, so the
# memoized enriched pool stays community-free.

_KNOWN_IDS_CACHE: Dict[str, Any] = {"ts": 0.0, "ids": set()}


def attach_community_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    try:
        stations = community_store.summaries()["stations"]
    except Exception as exc:  # community layer must never break the charger path
        _log_upstream_error("Community summaries unavailable", exc)
        return rows
    if stations:
        for row in rows:
            summary = stations.get(str(row.get("id") or ""))
            if summary:
                community_store.apply_community_evidence(row, summary)
    return rows


def known_station_ids() -> set:
    """Ids a check-in/photo may reference: intel + seeds + currently cached live rows
    + accepted community suggestions. 60 s memo; new points go through /station."""
    now = time.time()
    with _CACHE_LOCKS["known_ids"]:
        if now - _KNOWN_IDS_CACHE["ts"] < 60 and _KNOWN_IDS_CACHE["ids"]:
            return _KNOWN_IDS_CACHE["ids"]
    ids: set = set()
    try:
        records = load_intelligence_meta().get("records") or []
        if isinstance(records, IndexedRecords):
            ids.update(records.index.ids())
            records = []
        for row in records + _raw_seed_records():
            if row.get("id"):
                ids.add(str(row["id"]))
    except Exception as exc:
        _log_upstream_error("known_station_ids intel pool unavailable", exc)
    with _CACHE_LOCKS["live"]:
        cached_payload = CACHE.get("payload") or {}
        live_rows = list(cached_payload.get("stations") or [])
    for row in live_rows:
        if isinstance(row, dict) and row.get("id"):
            ids.add(str(row["id"]))
    try:
        for suggestion in community_store.summaries()["accepted_suggestions"]:
            ids.add(f"community_suggestion_{suggestion['id']}")
    except Exception:
        pass
    with _CACHE_LOCKS["known_ids"]:
        _KNOWN_IDS_CACHE["ts"] = now
        _KNOWN_IDS_CACHE["ids"] = ids
    return ids


def free_row(item: Dict[str, Any]) -> bool:
    """Genuinely-free evidence only: an explicit free flag or an explicit €0 price.
    A missing price is unknown, never free (honesty rule)."""
    if item.get("is_free") is True:
        return True
    price = safe_float(item.get("price_min"))
    return price is not None and price == 0.0


FREE_LEVEL_LABELS = {
    "live": "Live now",
    "community": "Community verified",
    "corroborated": "Corroborated",
    "reported": "Reported",
}
_FREE_LEVEL_ORDER = {"live": 0, "community": 1, "corroborated": 2, "reported": 3}
FREE_REGISTRY_BYTES_CACHE: Dict[Any, Any] = {}
FREE_REGISTRY_BYTES_CACHE_MAX_ENTRIES = 8


def free_registry_payload(bbox: Optional[Dict[str, float]], full: bool) -> Dict[str, Any]:
    meta = load_intelligence_meta()
    indexed = isinstance(meta.get("records"), IndexedRecords)
    if indexed or len(meta.get("records") or []) > _INTEL_SCOPE_THRESHOLD:
        rows, candidate_count = overview_candidates(bbox or WORLD_BBOX, 0.0, False)
        data = {"records": rows}
    else:
        data = load_intelligence(bbox)
        candidate_count = sum(1 for row in data.get("records") or [] if free_row(row))
    stations: List[Dict[str, Any]] = []
    by_level = {level: 0 for level in _FREE_LEVEL_ORDER}
    by_source: Dict[str, int] = {}
    with_photos = 0
    for row in data.get("records") or []:
        if not free_row(row):
            continue
        apply_corroboration(row)
        row["recommendation_score"] = recommendation_score(row)
        summary = row.get("community")
        level = community_store.free_verification_level(row, summary)
        row["free_verification"] = {
            "level": level,
            "label": FREE_LEVEL_LABELS[level],
            "note": "Ladder ranks free-claim confidence only; live occupancy still comes solely from live sources.",
        }
        by_level[level] += 1
        label = source_label(row) or "unknown"
        by_source[label] = by_source.get(label, 0) + 1
        if summary and summary.get("photos"):
            with_photos += 1
        stations.append(row if full else compact_station_for_ui(row))
    stations.sort(key=lambda s: (_FREE_LEVEL_ORDER.get((s.get("free_verification") or {}).get("level"), 9), -(s.get("recommendation_score") or 0)))
    community_data = community_store.summaries()
    return {
        "generated_at": now_iso(),
        "db_generated_at": meta.get("generated_at"),
        "result_limit": 5000 if indexed or len(meta.get("records") or []) > _INTEL_SCOPE_THRESHOLD else None,
        "truncated": candidate_count > len(stations),
        "scope": "worldwide",
        "bbox": bbox,
        "stats": {
            "free_total": len(stations),
            "by_level": by_level,
            "by_source": dict(sorted(by_source.items(), key=lambda kv: -kv[1])[:12]),
            "with_photos": with_photos,
            "community": community_data["totals"],
        },
        "stations": stations,
        "community_suggestions": {
            "pending_count": community_data["totals"].get("suggestions_pending", 0),
            "accepted": [
                dict(suggestion, kind="community_suggestion", status="candidate")
                for suggestion in community_data["accepted_suggestions"]
            ],
        },
        "honesty": {
            "note": (
                "Free-first registry. Community check-ins and cross-source corroboration raise "
                "confidence in the FREE claim only; they are never live occupancy. Suggested "
                "points are unverified candidates until corroborated by a public source."
            ),
        },
    }


def free_registry_response_bytes(params: Dict[str, List[str]]) -> Tuple[int, bytes, Optional[bytes]]:
    db = intelligence_path()
    bbox_param = (params.get("bbox") or [""])[0]
    full = (params.get("full") or ["false"])[0].lower() == "true"
    key = (
        (str(db), db.stat().st_mtime_ns) if db.exists() else None,
        community_store.generation(),  # any community write invalidates instantly
        bbox_param,
        full,
        int(time.time() // 60),  # freshness_minutes inside rows keep aging
    )
    with _CACHE_LOCKS["free_registry"]:
        cached = FREE_REGISTRY_BYTES_CACHE.get(key)
        if cached:
            return cached["status"], cached["json"], cached["gz"]
    payload = free_registry_payload(parse_bbox_param(params), full)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    gz = gzip_lib.compress(body, 6)
    with _CACHE_LOCKS["free_registry"]:
        FREE_REGISTRY_BYTES_CACHE[key] = {"status": 200, "json": body, "gz": gz}
        while len(FREE_REGISTRY_BYTES_CACHE) > FREE_REGISTRY_BYTES_CACHE_MAX_ENTRIES:
            FREE_REGISTRY_BYTES_CACHE.pop(next(iter(FREE_REGISTRY_BYTES_CACHE)), None)
    return 200, body, gz


def apply_free_only_filter(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Post-filter an /api/chargers payload to genuinely-free rows (both tiers)."""
    stations = payload.get("stations") or []
    leads = payload.get("leads") or []
    kept_stations = [row for row in stations if free_row(row)]
    kept_leads = [row for row in leads if free_row(row)]
    payload["stations"] = kept_stations
    payload["leads"] = kept_leads
    meta = payload.setdefault("meta", {})
    meta["free_only"] = {
        "applied": True,
        "dropped_stations": len(stations) - len(kept_stations),
        "dropped_leads": len(leads) - len(kept_leads),
        "note": "Only rows with an explicit free claim are shown; unknown prices are excluded, not assumed free.",
    }
    return payload


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
GEOCODE_CACHE: Dict[str, Any] = {}
GEOCODE_TTL_SECONDS = int(os.environ.get("VOLTA_GEOCODE_TTL", "86400"))
# Nominatim's usage policy (https://operations.osmfoundation.org/policies/nominatim/)
# requires a genuine way to contact the operator. VOLTA_CONTACT is empty by default --
# never invent one; the owner fills it in (email or contact URL) at deploy time. No
# code change is needed later: the User-Agent just starts including it once set.
VOLTA_CONTACT = os.environ.get("VOLTA_CONTACT", "").strip()
GEOCODE_UA = (
    f"Volta EV-charging app (+{VOLTA_CONTACT})"
    if VOLTA_CONTACT
    else "Volta EV-charging app (public geocode; contact via app owner)"
)


def geocode_search(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Public Nominatim proxy (worldwide), cached, so users can jump to any city/place.

    Returns [{label, name, lat, lon, type, bbox}]. bbox uses Volta's bbox dict shape.
    """
    q = (query or "").strip()
    if len(q) < 2:
        return []
    key = q.lower()[:80]
    now = time.time()
    with _CACHE_LOCKS["geocode"]:
        cached = GEOCODE_CACHE.get(key)
        if cached and now - cached["ts"] < GEOCODE_TTL_SECONDS:
            return cached["results"]
    qs = urllib.parse.urlencode({
        "q": q, "format": "jsonv2",
        "limit": max(1, min(limit, 8)), "addressdetails": 0,
    })
    req = urllib.request.Request(
        f"{NOMINATIM_URL}?{qs}",
        headers={"User-Agent": GEOCODE_UA, "Accept": "application/json", "Accept-Language": "es,en"},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return cached["results"] if cached else []
    results: List[Dict[str, Any]] = []
    for r in raw:
        lat, lon = safe_float(r.get("lat")), safe_float(r.get("lon"))
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        bb = r.get("boundingbox") or []
        bbox = None
        if len(bb) == 4:
            s, n, w, e = (safe_float(bb[0]), safe_float(bb[1]), safe_float(bb[2]), safe_float(bb[3]))
            if None not in (s, n, w, e):
                bbox = {"latitude_sw": s, "latitude_ne": n, "longitude_sw": w, "longitude_ne": e}
        label = r.get("display_name") or q
        results.append({
            "label": label,
            "name": r.get("name") or label.split(",")[0],
            "lat": lat, "lon": lon, "type": r.get("type"), "bbox": bbox,
        })
    with _CACHE_LOCKS["geocode"]:
        GEOCODE_CACHE[key] = {"ts": now, "results": results}
        if len(GEOCODE_CACHE) > 500:
            for k in list(GEOCODE_CACHE.keys())[:100]:
                GEOCODE_CACHE.pop(k, None)
    return results


def parse_bbox_param(params: Dict[str, List[str]]) -> Optional[Dict[str, float]]:
    """Parse bbox=south,west,north,east into Volta's bbox dict (None if absent/invalid)."""
    raw = (params.get("bbox") or [""])[0]
    if not raw:
        return None
    parts = [safe_float(x) for x in raw.split(",")]
    if len(parts) == 4 and None not in parts:
        s, w, n, e = parts  # type: ignore[misc]
        if -90 <= s <= n <= 90 and -180 <= w <= 180 and -180 <= e <= 180:
            return {"latitude_sw": s, "longitude_sw": w, "latitude_ne": n, "longitude_ne": e}
    return None


COMPACT_OVERVIEW_DROP_FIELDS = {
    "raw",
    "source_integration",
    "access_claim",
    "location_claim",
    "hardware_claim",
    "source_fetched_at",
}
COMPACT_CONNECTOR_FIELDS = {"connector_id", "label", "type", "status", "power_kw", "count"}
COMPACT_PRICING_FIELDS = {"valid_from", "valid_until", "days"}


def compact_station_for_ui(station: Dict[str, Any]) -> Dict[str, Any]:
    """Return the card/map contract without ingestion-only bulk.

    The SPA still receives source links, charge/parking claims, verification, media,
    navigation and connector facts. Large raw/source-integration claim mirrors are
    useful for audits but not rendered by the first-use overview, so compact=true can
    avoid parsing several megabytes before the driver sees the first card.
    """
    row = {k: v for k, v in station.items() if k not in COMPACT_OVERVIEW_DROP_FIELDS}
    raw = station.get("raw") or {}
    # `raw` is ingestion/audit bulk and is deliberately not part of the compact client
    # contract. Preserve a usable source-reported operator only at its public field. If
    # the top-level and raw values disagree, neither is safe to present as a CPO brand.
    def compact_operator(value: Any) -> Optional[str]:
        if not isinstance(value, str):
            return None
        cleaned = " ".join(value.split())
        return cleaned or None

    top_operator = compact_operator(station.get("operator"))
    raw_operator = compact_operator(raw.get("operator")) if isinstance(raw, dict) else None
    if top_operator and raw_operator and top_operator.casefold() != raw_operator.casefold():
        row.pop("operator", None)
    elif top_operator:
        row["operator"] = top_operator
    elif raw_operator:
        row["operator"] = raw_operator
    else:
        row.pop("operator", None)

    if "connectors" in row:
        row["connectors"] = [
            {k: v for k, v in connector.items() if k in COMPACT_CONNECTOR_FIELDS and v is not None}
            for connector in (row.get("connectors") or [])
            if isinstance(connector, dict)
        ]

    time_bound_pricing = []
    for window in row.get("pricing") or []:
        if not isinstance(window, dict) or not any(window.get(k) for k in COMPACT_PRICING_FIELDS):
            continue
        time_bound_pricing.append({
            k: v for k, v in window.items()
            if k in COMPACT_PRICING_FIELDS and v not in (None, "", [])
        })
    if time_bound_pricing:
        row["pricing"] = time_bound_pricing
        status = row.get("price_window_status") or {}
        row["price_window_status"] = {
            k: status[k]
            for k in ("label", "is_currently_within_standard")
            if k in status
        }
    else:
        row.pop("pricing", None)
        row.pop("price_window_status", None)

    return row


# Opt-in first-paint contract: every closed-card, map, filter and safety field stays
# available. Long provenance, photos and ingestion mirrors are fetched only when a
# driver opens Details. Keep short attribution references even in offline summaries.
SUMMARY_OVERVIEW_FIELDS = frozenset("""
    id name operator owner city address lat lon
    price_min price_max current_price_min price_currency price_native_unit price_label is_free is_cheap free_condition price_claim_type
    tesla_ok fast max_power_kw connector_labels connectors connector_summary connector_count available_connectors
    status available total_evse source source_url source_label primary_source source_count reliability confidence
    claim_conflict parking access verification decision pricing price_window_status tariff_details price_evidence
    freshness_minutes last_seen recommendation_score distance_km drive_eta_min_est navigation lead corroboration cache_stale
""".split())
SUMMARY_SOURCE_FIELDS = frozenset({"name", "url", "license", "license_url", "attribution"})
# Community generations are process-local. A restart must not accidentally accept
# an old token merely because its write counter has reached the same value again.
_DETAIL_SNAPSHOT_PROCESS = os.urandom(16).hex()


def catalogue_detail_snapshot() -> str:
    """Opaque binding to one immutable catalogue, seeds, policy and community state.

    No large JSON/index hashing or catalogue enumeration on the request path. The
    resolved path and inode/stat identity bind the published immutable bundle;
    seed and policy replacements are also detected. Private paths are only hashed.
    """
    def identity(path: Path) -> Any:
        path = path.resolve()
        try:
            stat = path.stat()
        except FileNotFoundError:
            return [str(path), None]
        return [str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]

    binding = [
        "directory-detail-v1", _DETAIL_SNAPSHOT_PROCESS,
        identity(intelligence_path()), identity(DATA / "seed_leads.json"),
        identity(DATA / "source_policy_exclusions.json"),
        intelligence_health_fingerprint(), community_store.generation(),
    ]
    return hashlib.sha256(json.dumps(binding, separators=(",", ":")).encode()).hexdigest()


def summary_station_for_ui(station: Dict[str, Any], snapshot: str) -> Dict[str, Any]:
    row = {key: value for key, value in station.items() if key in SUMMARY_OVERVIEW_FIELDS}
    if isinstance(station.get("sources"), list):
        row["sources"] = [
            {key: value for key, value in source.items() if key in SUMMARY_SOURCE_FIELDS}
            for source in station["sources"] if isinstance(source, dict)
        ]
    row["detail_snapshot"] = snapshot
    return row


def station_detail_response(params: Dict[str, List[str]]) -> Tuple[int, Dict[str, Any]]:
    """Stored directory disclosure only; exact canonical IDs, never live/upstream I/O."""
    identifiers, snapshots = params.get("id") or [], params.get("snapshot") or []
    if (len(identifiers) != 1 or len(snapshots) != 1
            # Existing imported canonical IDs can exceed the community-write
            # limit (128). A bounded read lookup must still address those rows.
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:\-]{0,255}", identifiers[0])
            or not re.fullmatch(r"[0-9a-f]{64}", snapshots[0])):
        return 400, {"error": "invalid_station_request"}
    identifier, requested_snapshot = identifiers[0], snapshots[0]
    snapshot = catalogue_detail_snapshot()
    if not hmac.compare_digest(requested_snapshot, snapshot):
        return 409, {"error": "snapshot_changed"}

    records = load_intelligence_meta().get("records") or []
    matches: List[Tuple[Dict[str, Any], str]] = []
    if isinstance(records, IndexedRecords):
        # The general index lookup resolves merged aliases. Details intentionally
        # require a unique canonical row, so an alias or duplicate ID cannot open
        # another station's evidence. This uses station_identifiers, not a scan.
        with records.index.connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM stations WHERE identifier=? ORDER BY seq LIMIT 2", (identifier,),
            ).fetchall()
        matches.extend((records.index.decode(row[0]), "free/cheap intelligence DB") for row in rows)
    else:
        for row in records:
            if str(row.get("id") or "") == identifier:
                matches.append((row, "free/cheap intelligence DB"))
                if len(matches) > 1:
                    break
    matches.extend((row, "manual research lead") for row in _raw_seed_records()
                   if str(row.get("id") or "") == identifier)
    if len(matches) != 1:
        return 404, {"error": "not_found"}
    raw, default_source = matches[0]
    if not source_policy_record_allowed(raw):
        return 404, {"error": "not_found"}
    station = _normalize_lead(deepcopy(raw), default_source)
    if reject_station_reason(station):
        return 404, {"error": "not_found"}
    _refresh_time_derived_fields(attach_community_rows([station]))
    apply_corroboration(station)
    station["recommendation_score"] = recommendation_score(station)
    # Publication/community changes while the row was decoded must never pair a
    # newer disclosure with the caller's older card (even with the same ID).
    if not hmac.compare_digest(snapshot, catalogue_detail_snapshot()):
        return 409, {"error": "snapshot_changed"}
    return 200, {"station": station, "snapshot": snapshot}


def overview_candidates(bbox: Dict[str, float], price_cap: float, fast_only: bool, origin: Optional[Tuple[float, float]] = None) -> Tuple[List[Dict[str, Any]], int]:
    raw = load_intelligence_meta()
    records = raw.get("records")
    indexed = isinstance(records, IndexedRecords)
    records = records if indexed else records or []
    if not indexed and len(records) <= _INTEL_SCOPE_THRESHOLD:
        rows = load_all_leads(bbox)
        return rows, len(rows)
    count = 0
    if indexed:
        records, count = records.index.query(bbox=bbox, price_cap=price_cap, fast_only=fast_only,
                                             limit=5000, overview=True, origin=origin)
    manual_rows = _raw_seed_records()
    manual_ids = {id(row) for row in manual_rows}
    def candidates() -> Iterable[Dict[str, Any]]:
        nonlocal count
        for rows in (records, manual_rows):
            for row in rows:
                if not in_bbox(safe_float(row.get("lat")), safe_float(row.get("lon")), bbox):
                    continue
                price = 0.0 if row.get("is_free") is True else euro_price(row)
                if price_cap < 5.0 and (price is None or price > price_cap):
                    continue
                if fast_only and not (row.get("fast") or (safe_float(row.get("max_power_kw")) or 0) >= 43):
                    continue
                if not indexed or rows is manual_rows:
                    count += 1
                yield row
    selected = heapq.nsmallest(5000, candidates(), key=lambda row: (
        not bool(row.get("is_free")), not bool(row.get("is_cheap")),
        haversine_km(origin[0], origin[1], row["lat"], row["lon"]) if origin else 0,
        -(safe_float(row.get("recommendation_score")) or safe_float(row.get("score")) or 0), str(row.get("id") or "")))
    out = []
    for row in selected:
        item = _normalize_lead(deepcopy(row), "manual research lead" if id(row) in manual_ids else "free/cheap intelligence DB")
        if not reject_station_reason(item):
            out.append(item)
    return _refresh_time_derived_fields(attach_community_rows(out)), count


def overview_response(params: Dict[str, List[str]], max_price: float, fast_only: bool) -> Tuple[int, Dict[str, Any]]:
    """Bounded directory overview of the requested world extent; never a live fetch.

    Large bundles select a bounded candidate set before expensive enrichment.
    The historic Spain overview remains the default when no extent is supplied.
    """
    bbox = parse_bbox_param(params) or {
        "latitude_sw": SPAIN_BBOX["latitude_sw"], "longitude_sw": SPAIN_BBOX["longitude_sw"],
        "latitude_ne": SPAIN_BBOX["latitude_ne"], "longitude_ne": SPAIN_BBOX["longitude_ne"],
    }
    compact = (params.get("compact") or ["false"])[0].lower() == "true"
    summary = compact and (params.get("summary") or ["false"])[0].lower() == "true"
    snapshot = catalogue_detail_snapshot() if summary else None
    free_only = (params.get("free_only") or ["false"])[0].lower() == "true"
    price_cap = 0.0 if free_only else max_price
    out: List[Dict[str, Any]] = []
    user_lat = safe_float((params.get("lat") or [None])[0])
    user_lon = safe_float((params.get("lon") or [None])[0])
    origin = (user_lat, user_lon) if user_lat is not None and user_lon is not None and -90 <= user_lat <= 90 and -180 <= user_lon <= 180 else None
    candidates, candidate_count = overview_candidates(bbox, price_cap, fast_only, origin)
    for lead in candidates:
        price = euro_price(lead)
        if price is None and lead.get("is_free") is True:
            price = 0.0
        # 5.0 is the clients' broad-discovery sentinel. Explicit price limits
        # exclude unknown tariffs before ranking and the response-size cutoff.
        if price is None:
            if price_cap < 5.0:
                continue
        elif not math.isfinite(price) or price < 0 or (price_cap < 5.0 and price > price_cap):
            continue
        if fast_only and not lead.get("fast"):
            continue
        if origin and source_health_has_valid_coordinates(lead):
            lead["distance_km"] = round(haversine_km(origin[0], origin[1], lead["lat"], lead["lon"]), 2)
        apply_corroboration(lead)
        # Corroboration must reach ranking here, not just the corroboration block:
        # recommendation_score was computed at ingestion/normalization time, before
        # this per-request corroboration boost is attached, so re-score explicitly
        # before compacting/sorting (compact_station_for_ui copies fields as-is).
        lead["recommendation_score"] = recommendation_score(lead)
        out.append(compact_station_for_ui(lead) if compact else lead)
    out.sort(key=nearby_sort_key if origin else lambda s: -(s.get("recommendation_score") or 0))
    results_truncated = len(out) > 2500 or candidate_count > len(candidates)
    out = out[:2500]
    coverage = live_coverage_profile([], out)
    coverage["mode"] = "overview"
    coverage["warning"] = None  # intentionally leads-only; not a live-coverage problem
    payload = {
        "stations": [],
        "leads": out,
        "price_evidence_counts": price_evidence_counts(out),
        "meta": {
            "scope": "worldwide",
            "mode": "overview",
            "viewport": {"bbox": bbox, "source": "overview", "clamped": False},
            "coverage": coverage,
            "generated_at": now_iso(),
            "compact": compact,
            "result_limit": 2500,
            "candidate_records": candidate_count,
            "truncated": results_truncated,
        },
        "cache": {"hit": False, "overview": True},
        "warning": None,
    }
    if summary:
        if not hmac.compare_digest(snapshot, catalogue_detail_snapshot()):
            return 409, {"error": "snapshot_changed"}
        payload["leads"] = [summary_station_for_ui(row, snapshot) for row in out]
        payload["meta"]["summary"] = True
    return 200, payload


# The national overview is the app's default view and is pure function of (intel DB, seeds,
# filters) — no live fetch. Serializing the full enriched national dataset is material work,
# so cache the SERIALIZED body (plus its gzip) keyed by the data files' mtimes.
#
# plans/007 step 5: OVERVIEW_BYTES_CACHE used to be a SINGLE "entry" slot, so any filter
# toggle (max_price/fast_only/compact) evicted whatever was cached for every OTHER
# combination -- a user flipping a filter and flipping it back always recomputed. Now
# keyed by the FULL key tuple directly, a small FIFO-capped dict: up to
# OVERVIEW_BYTES_CACHE_MAX_ENTRIES distinct (mtime, mtime, max_price, fast_only, compact)
# combinations stay cached simultaneously. FIFO (oldest INSERTION evicted first), not true
# LRU (no touch-on-read reordering) -- deliberately simple; a handful of filter
# combinations churn in practice, so exact recency tracking isn't worth the complexity.
OVERVIEW_BYTES_CACHE: Dict[Any, Any] = {}
OVERVIEW_BYTES_CACHE_MAX_ENTRIES = 8
# plans/007 step 5 (optional icing): the SPA's instant first-paint layer always sends
# overview=true WITH a bbox (chargersUrl in frontend/src/lib/api.ts), so that path
# used to bypass this cache entirely (Handler._route_get only used it for the no-bbox
# default view) and pay full JSON-serialize+gzip on every viewport-scoped poll. A
# SEPARATE, larger-capped memo (not the no-bbox cache above) so a user panning around
# never evicts the important default-view slots.
OVERVIEW_BBOX_BYTES_CACHE: Dict[Any, Any] = {}
OVERVIEW_BBOX_BYTES_CACHE_MAX_ENTRIES = 16
STATIC_GZ_CACHE: Dict[Any, bytes] = {}


def overview_response_bytes(params: Dict[str, List[str]], max_price: float, fast_only: bool) -> Tuple[int, bytes, Optional[bytes]]:
    db = intelligence_path()
    seeds = DATA / "seed_leads.json"
    summary = ((params.get("compact") or ["false"])[0].lower() == "true"
               and (params.get("summary") or ["false"])[0].lower() == "true")
    base_key = (
        (str(db), db.stat().st_mtime_ns) if db.exists() else None,
        seeds.stat().st_mtime if seeds.exists() else 0,
        max_price,
        fast_only,
        (params.get("free_only") or ["false"])[0].lower() == "true",
        (params.get("compact") or ["false"])[0].lower() == "true",
        catalogue_detail_snapshot() if summary else None,
        community_store.generation(),
        safe_float((params.get("lat") or [None])[0]),
        safe_float((params.get("lon") or [None])[0]),
        # Serialized rows carry minute-derived evidence age. Keep fast repeated
        # requests in the same slot, but never freeze that age until the next DB
        # rebuild merely because the byte cache otherwise has no TTL.
        int(time.time() // 60),
    )
    bbox_param = (params.get("bbox") or [""])[0]
    cache, cap, key = (
        (OVERVIEW_BBOX_BYTES_CACHE, OVERVIEW_BBOX_BYTES_CACHE_MAX_ENTRIES, base_key + (bbox_param,))
        if bbox_param
        else (OVERVIEW_BYTES_CACHE, OVERVIEW_BYTES_CACHE_MAX_ENTRIES, base_key)
    )
    with _CACHE_LOCKS["overview"]:
        cached = cache.get(key)
        if cached:
            return cached["status"], cached["json"], cached["gz"]
    # Serialization/compression (CPU-only, idempotent for a given key) stays outside the lock,
    # matching the fetch-outside-lock pattern used for the network-backed caches.
    status, payload = overview_response(params, max_price, fast_only)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    gz = gzip_lib.compress(body, 6)
    if status != 200:
        return status, body, gz
    with _CACHE_LOCKS["overview"]:
        cache[key] = {"status": status, "json": body, "gz": gz}
        while len(cache) > cap:
            oldest_key = next(iter(cache))
            cache.pop(oldest_key, None)
    return status, body, gz


def _timed_source_fetch(name: str, fetcher: Any, *args: Any, **kwargs: Any) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        payload = fetcher(*args, **kwargs)
        return {
            "name": name,
            "payload": payload,
            "error": None,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    except Exception as exc:
        return {
            "name": name,
            "payload": None,
            "error": exc,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }


def fetch_primary_sources_parallel(
    *,
    fetch_mapareve_now: bool,
    max_price: float,
    available_only: bool,
    fast_only: bool,
    bbox: Dict[str, float],
    force: bool,
) -> Dict[str, Dict[str, Any]]:
    """Fetch independent public live sources concurrently within their own budgets.

    MapaREVE and Gaia do not depend on one another. Running them serially made
    route-ready latency roughly the sum of two public networks; this keeps each
    source's existing cache/failure policy but makes request latency approach the
    slowest source instead. Errors are returned as data so live_response can preserve
    its existing stale-cache and fail-closed behavior.
    """
    coverage = live_geography(bbox)
    skipped = {
        name: {"name": name, "payload": {"stations": [], "raw_count": 0,
               "supported": False, "cache": {"hit": False}, "errors": []},
               "error": None, "duration_ms": 0.0}
        for name in ("mapareve", "gaia") if not coverage[name]
    }
    jobs: Dict[str, Tuple[Any, Tuple[Any, ...], Dict[str, Any]]] = {}
    if coverage["gaia"]:
        jobs["gaia"] = (fetch_gaia_live, (), {"force": force})
    if fetch_mapareve_now and coverage["mapareve"]:
        jobs["mapareve"] = (fetch_mapareve, (), {
            "max_price": max_price, "available_only": available_only,
            "fast_only": fast_only, "bbox": bbox,
        })
    if not jobs:
        return skipped

    # Bound how many requests can fan out to upstream sources AT ONCE across the whole
    # process (each request here already spawns len(jobs) threads via the executor
    # below). A short bounded acquire, not an unbounded queue: on timeout every job in
    # THIS request is reported using the exact {name, payload:None, error, duration_ms}
    # shape _timed_source_fetch already returns for a genuine fetch exception, so
    # live_response's existing stale-cache/fail-closed handling needs zero new
    # branches -- this is deliberately not a new error shape.
    acquired = LIVE_FETCH_SEMAPHORE.acquire(timeout=LIVE_FETCH_SEMAPHORE_ACQUIRE_TIMEOUT_SECONDS)
    if not acquired:
        timeout_exc = TimeoutError(
            f"Upstream fetch concurrency limit reached (VOLTA_MAX_CONCURRENT_LIVE={LIVE_FETCH_CONCURRENCY_LIMIT})"
        )
        _log_upstream_error("Upstream fetch semaphore acquire timed out", timeout_exc)
        return dict(skipped, **{name: {"name": name, "payload": None, "error": timeout_exc, "duration_ms": 0.0} for name in jobs})
    try:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(jobs),
            thread_name_prefix="volta-live",
        ) as executor:
            futures = {
                name: executor.submit(_timed_source_fetch, name, fetcher, *args, **kwargs)
                for name, (fetcher, args, kwargs) in jobs.items()
            }
            return dict(skipped, **{name: future.result() for name, future in futures.items()})
    finally:
        LIVE_FETCH_SEMAPHORE.release()


def _source_payload_or_empty(result: Dict[str, Any], label: str) -> Dict[str, Any]:
    payload = result.get("payload")
    if isinstance(payload, dict):
        return payload
    error = result.get("error")
    return {
        "stations": [],
        "raw_count": 0,
        "cache": {"hit": False},
        "errors": [type(error).__name__ if error else "UnknownError"],
        "warning": f"{label} live refresh unavailable; continuing with other public sources.",
    }


def directory_response(params: Dict[str, List[str]], bbox: Dict[str, float], viewport_source: str,
                       clamped: bool, max_price: float, fast_only: bool,
                       include_leads: bool, available_only: bool) -> Tuple[int, Dict[str, Any]]:
    scoped = dict(params, bbox=[",".join(str(bbox[k]) for k in ("latitude_sw", "longitude_sw", "latitude_ne", "longitude_ne"))])
    _, payload = overview_response(scoped, max_price, fast_only)
    leads = payload["leads"] if include_leads and not available_only else []
    lat, lon = safe_float((params.get("lat") or [None])[0]), safe_float((params.get("lon") or [None])[0])
    if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
        for lead in leads:
            if source_health_has_valid_coordinates(lead):
                lead["distance_km"] = round(haversine_km(lat, lon, lead["lat"], lead["lon"]), 2)
        leads.sort(key=nearby_sort_key)
    note = "Live availability is not connected in this area. Source-backed directory records require confirmation with the operator."
    if payload["meta"].get("truncated"):
        note += " Results are limited; narrow the area for a more complete search."
    payload["leads"] = leads
    payload["meta"].update({"mode": "directory", "viewport": {"bbox": bbox, "source": viewport_source, "clamped": clamped},
                            "price_cap_eur_kwh": max_price, "live_sources": live_geography(bbox)})
    payload["meta"]["coverage"].update({"mode": "directory", "supported": False, "is_degraded": False, "summary": note, "warning": None})
    payload["cache"] = {"hit": False, "live_supported": False, "live_failed": False, "fresh_live_authority": False, "stale": False}
    payload["warning"] = note
    return 200, payload


def live_response(params: Dict[str, List[str]]) -> Tuple[int, Dict[str, Any]]:
    request_started = time.perf_counter()
    engine_stages: List[Dict[str, Any]] = []
    dropped: Dict[str, int] = {
        "out_of_view_live": 0,
        "rejected_live": 0,
        "duplicate_leads": 0,
        "electromaps_stale": 0,
        "electromaps_dead": 0,
        "available_filter": 0,
        "route_filtered": 0,
        "source_policy_excluded": 0,
    }
    include_leads = (params.get("include_leads") or ["false"])[0].lower() == "true"
    max_price = _parse_max_price((params.get("max_price") or [None])[0])
    available_only = (params.get("available") or ["false"])[0].lower() == "true"
    fast_only = (params.get("fast") or ["false"])[0].lower() == "true"
    force = (params.get("force") or ["false"])[0].lower() == "true"

    if (params.get("overview") or ["false"])[0].lower() == "true":
        return overview_response(params, max_price, fast_only)

    bbox, vp_source, vp_clamped = resolve_viewport(params)
    geography = live_geography(bbox)
    if not any(geography.values()):
        return directory_response(params, bbox, vp_source, vp_clamped, max_price, fast_only, include_leads, available_only)
    tile = viewport_tile_key(bbox, vp_source)
    cache_bbox = dict(bbox) if vp_source == "default" else snapped_bbox_envelope(bbox)
    cache_key = f"{tile}{max_price}:{available_only}:{fast_only}"
    engine_record_stage(engine_stages, request_started, request_started, "viewport", source=vp_source, clamped=vp_clamped, cache_key=cache_key)
    now = time.time()
    # D-145: CACHE is a single module-level dict mutated by every request thread. The
    # cache_fresh read-decide and the deepcopy read below happen under one lock acquisition
    # (a consistent snapshot of ts/payload/key together) so a concurrent write from another
    # filter set's request can never be observed half-applied; the fetch itself (below, outside
    # any lock) is never serialized.
    # plans/007 step 4: tracks whether THIS request performed a genuine, successful
    # MapaREVE live fetch (as opposed to a cache_fresh hit or a stale/fallback serve).
    # Gates the assembled-payload cache WRITE below -- never written from a stale/
    # fallback branch.
    mapareve_fresh_fetch = False
    mapareve_failure_warning: Optional[str] = None
    mapareve_failure_kind: Optional[str] = None
    with _CACHE_LOCKS["live"]:
        recent = (CACHE.get("recent") or {}).get(cache_key)
        if (
            not force
            and CACHE.get("key") != cache_key
            and recent
            and now - recent["ts"] < CACHE_TTL_SECONDS
        ):
            # Panning back to an area fetched within the TTL: make that fetch current
            # again instead of re-crawling MapaREVE. Its original timestamp is kept, so
            # freshness and stale handling are exactly as if it had stayed in CACHE.
            CACHE.update({"ts": recent["ts"], "payload": recent["payload"], "raw_count": recent["raw_count"], "key": cache_key})
        cache_fresh = bool(
            not force
            and CACHE.get("payload")
            and CACHE.get("key") == cache_key
            and now - CACHE.get("ts", 0) < CACHE_TTL_SECONDS
        )
        if cache_fresh:
            stage_started = time.perf_counter()
            payload = deepcopy(CACHE["payload"])
            payload["cache"] = {"hit": True, "age_seconds": round(now - CACHE["ts"], 1), "ttl_seconds": CACHE_TTL_SECONDS}
    if cache_fresh:
        engine_record_stage(engine_stages, request_started, stage_started, "mapareve", mode="cache_hit", stations=len(payload.get("stations") or []))
        primary_results = fetch_primary_sources_parallel(
            fetch_mapareve_now=False,
            max_price=max_price,
            available_only=available_only,
            fast_only=fast_only,
            bbox=cache_bbox,
            force=force,
        )
    else:
        primary_results = fetch_primary_sources_parallel(
            fetch_mapareve_now=True,
            max_price=max_price,
            available_only=available_only,
            fast_only=fast_only,
            bbox=cache_bbox,
            force=force,
        )
        mapareve_result = primary_results["mapareve"]
        mapareve_error = mapareve_result.get("error")
        if mapareve_error is None:
            # The upstream fetch covers the same snapped envelope encoded in cache_key.
            # We re-filter to the exact requested bbox below, after cache reuse.
            mapareve_fresh_fetch = True
            payload = deepcopy(mapareve_result["payload"])
            allowed_rows, excluded_rows = partition_records(payload.get("stations") or [], SOURCE_POLICY_EXCLUSIONS)
            payload["stations"] = allowed_rows
            dropped["source_policy_excluded"] += len(excluded_rows)
            with _CACHE_LOCKS["live"]:
                CACHE.update({"ts": now, "payload": deepcopy(payload), "raw_count": payload.get("raw_count"), "error": None, "key": cache_key})
                recent_fetches = CACHE.setdefault("recent", {})
                recent_fetches.pop(cache_key, None)
                recent_fetches[cache_key] = {"ts": now, "payload": CACHE["payload"], "raw_count": payload.get("raw_count")}
                while len(recent_fetches) > MAPAREVE_RECENT_MAX_ENTRIES:
                    recent_fetches.pop(next(iter(recent_fetches)), None)
            payload["cache"] = {"hit": False, "age_seconds": 0, "ttl_seconds": CACHE_TTL_SECONDS}
            engine_record_stage(
                engine_stages,
                request_started,
                request_started,
                "mapareve",
                duration_ms=mapareve_result["duration_ms"],
                mode="live_fetch",
                parallel=True,
                stations=len(payload.get("stations") or []),
                raw_count=payload.get("raw_count", 0),
            )
        else:
            # If the live source hiccups, serve stale cache if possible.
            # Client-visible text (this `err`, which reaches payload["warning"] below
            # and CACHE["error"] -- itself exposed via /api/health) carries the
            # exception CLASS name or a fixed policy explanation, never the message
            # (upstream-internal detail such as a raw URL, host, or library-internal string). Full detail is
            # logged server-side only, once, here.
            _log_upstream_error("MapaREVE live refresh failed", mapareve_error)
            err = (
                "collection disabled by source-access policy"
                if isinstance(mapareve_error, SourcePolicyBlocked)
                else type(mapareve_error).__name__
            )
            with _CACHE_LOCKS["live"]:
                CACHE["error"] = err
                if CACHE.get("payload") and CACHE.get("key") == cache_key:
                    payload = deepcopy(CACHE["payload"])
                    payload["warning"] = f"Live refresh failed; serving stale cache. {err}"
                    payload["cache"] = {"hit": True, "stale": True, "age_seconds": round(now - CACHE.get("ts", 0), 1), "ttl_seconds": CACHE_TTL_SECONDS}
                elif CACHE.get("payload"):
                    payload = deepcopy(CACHE["payload"])
                    payload["warning"] = (
                        "Live refresh failed; serving stale cache from a broader filter and reapplying current route filters. "
                        f"{err}"
                    )
                    payload["cache"] = {
                        "hit": True,
                        "stale": True,
                        "filter_fallback": True,
                        "source_key": CACHE.get("key"),
                        "requested_key": cache_key,
                        "age_seconds": round(now - CACHE.get("ts", 0), 1),
                        "ttl_seconds": CACHE_TTL_SECONDS,
                    }
                else:
                    # No cached live data at all (fresh process, or a host whose IP the live
                    # upstream blocks — e.g. datacenter IPs vs Imperva). A bare 502 reads as
                    # "server down" to every client; the honest AND useful answer is a 200 with
                    # ZERO live stations (fail-closed) plus the source-backed leads and an
                    # explicit warning. Nothing is presented as live.
                    payload = {
                        "stations": [],
                        "raw_count": 0,
                        "fetched_at": now_iso(),
                        "warning": f"Live source unavailable and no cached live data yet ({err}). Showing source-backed leads only — nothing is presented as live.",
                        "cache": {"hit": False, "age_seconds": 0, "ttl_seconds": CACHE_TTL_SECONDS, "live_failed": True},
                    }
                mapareve_failure_warning = str(payload.get("warning") or "")
                mapareve_failure_kind = err
            engine_record_stage(
                engine_stages,
                request_started,
                request_started,
                "mapareve",
                duration_ms=mapareve_result["duration_ms"],
                mode="fallback",
                parallel=True,
                error_type=type(mapareve_error).__name__,
                stations=len(payload.get("stations") or []),
                stale=bool((payload.get("cache") or {}).get("stale")),
            )

    stations, excluded_rows = partition_records(payload.get("stations") or [], SOURCE_POLICY_EXCLUSIONS)
    dropped["source_policy_excluded"] += len(excluded_rows)
    if (payload.get("cache") or {}).get("stale"):
        # Mark only rows originating in the stale MapaREVE payload. Fresh Gaia
        # rows merged below remain eligible for route-ready status.
        for station in stations:
            station["_volta_stale_live_cache"] = True

    gaia_result = primary_results["gaia"]
    gaia_payload = _source_payload_or_empty(gaia_result, "Gaia")
    gaia_cache_info = dict(gaia_payload.get("cache") or {})
    if gaia_payload.get("supported") is False:
        gaia_cache_info["populated_at"] = "outside-configured-region"
    if gaia_payload.get("warning"):
        existing_warning = str(payload.get("warning") or "").strip()
        payload["warning"] = f"{existing_warning} {gaia_payload['warning']}".strip() if existing_warning else gaia_payload["warning"]
    user_lat = safe_float((params.get("lat") or [None])[0])
    user_lon = safe_float((params.get("lon") or [None])[0])

    # plans/007 step 4: assembled-payload reuse. A MapaREVE raw-cache hit (cache_fresh)
    # still re-ran every merge/dedup/enrich stage on every request -- nearly as expensive
    # as a miss (see qa/run-log.md's plans/007 iteration for measured before/after). If
    # Gaia ALSO served a genuinely fresh (non-stale) hit THIS request, the fully
    # merged+enriched (pre-distance/eta, pre-sort) stations/leads from the write that
    # populated the raw cache are still exactly correct to reuse here -- only the
    # per-request distance/eta/sort actually depend on anything request-specific
    # (lat/lon). NEVER reused from a stale/fallback branch: those never populate
    # CACHE["assembled"] in the first place (see the write, gated on
    # mapareve_fresh_fetch, at the end of the "not reused" branch below). If
    # electromaps did any live work on the write that built the assembled entry, that
    # entry is never reused either -- electromaps has no per-call cache.hit flag at the
    # granularity needed to safely re-verify freshness for a DIFFERENT request, so we
    # fall through to the full tail instead of guessing (see electromaps_did_work).
    #
    # plans/007 REVISE (adviser review, two HIGH findings against the original step-4
    # gate -- see qa/defects.csv D-153/D-154 and qa/run-log.md's REVISE iteration):
    #
    # Finding 1: the original write was gated ONLY on mapareve_fresh_fetch -- it never
    # checked whether Gaia was ITSELF serving a stale fallback at write time,
    # and the read gate only checked "is Gaia fresh NOW", never "is it the SAME
    # data that was merged at write time". A gaia row that was AVAILABLE while gaia was
    # stale could therefore be cached, then served again on a later read as if fresh
    # (with no stale warning) even after the real gaia feed had moved on. Fixed with two
    # changes below: (a) the write refuses to store an assembled entry at all when
    # gaia_cache_info was stale THIS request (see the write site), and
    # (b) reuse now additionally requires the READ's Gaia cache.populated_at (a
    # stable per-population epoch, unlike age_seconds which drifts with "now" even for
    # identical data -- see fetch_gaia_live) to EQUAL what was stored at
    # write time -- data identity, not merely "also happens to be fresh right now".
    #
    # Finding 2: assembled rows are filtered to the WRITE request's EXACT bbox (see the
    # write site) -- NOT the wider snapped cache_bbox envelope a stale comment used to
    # claim here. Reusing across two different exact bboxes that share a snapped tile
    # (e.g. a small pan) could therefore silently drop a station that IS in the raw
    # upstream cache and IS inside the read's own bbox, simply because it fell outside
    # the narrower bbox the FIRST request happened to ask for. Fixed by storing the
    # write bbox in the assembled entry and only reusing when the read bbox is fully
    # bbox_contains()-ed by it; otherwise falling through to the full tail (which
    # correctly re-derives from the full snapped-envelope raw caches).
    sub_sources_fresh_hit = (
        cache_fresh
        and (gaia_payload.get("supported") is False or bool(gaia_cache_info.get("hit")))
        and not gaia_cache_info.get("stale")
    )
    em_info: Dict[str, Any] = {"stations": [], "checked_ids": set(), "promoted_ids": set(), "dead_ids": set(), "stale_dropped": 0}
    stations_assembled: Optional[List[Dict[str, Any]]] = None
    leads_assembled: Optional[List[Dict[str, Any]]] = None
    if sub_sources_fresh_hit:
        with _CACHE_LOCKS["live"]:
            assembled = CACHE.get("assembled")
            if (
                assembled
                and assembled.get("key") == cache_key
                and assembled.get("include_leads") == include_leads
                and not assembled.get("electromaps_did_work")
                and now - assembled.get("ts", 0) < CACHE_TTL_SECONDS
                # Finding 1: data identity, not merely "fresh now" (see the comment above).
                and assembled.get("gaia_populated_at") is not None
                and assembled.get("gaia_populated_at") == gaia_cache_info.get("populated_at")
                # Finding 2: the read bbox must be fully covered by the write bbox.
                and bbox_contains(assembled.get("bbox"), bbox)
            ):
                stations_assembled = deepcopy(assembled["stations"])
                leads_assembled = deepcopy(assembled["leads"])
                em_info = deepcopy(assembled["em_info"])

    if stations_assembled is not None:
        # Assembled rows were bbox-filtered to the WRITE request's EXACT bbox (see the
        # write site below), and the reuse gate above only reaches here when the READ
        # bbox is fully contained in that write bbox (plans/007 REVISE, Finding 2) -- so
        # this in_bbox pass narrows an already-safe superset down to the exact requested
        # area; it can never need to recover a station the assembled snapshot dropped.
        # Repartition the snapshot on every read. An older assembled entry may predate
        # a policy change (or may have been populated before a nested provenance field
        # was understood), so trusting only the write-time filter would let retired
        # evidence survive until the cache expires. Independent observations of an
        # operator remain valid because partition_records examines provenance, not
        # free-form station/operator names.
        assembled_station_candidates, excluded_rows = partition_records(
            stations_assembled,
            SOURCE_POLICY_EXCLUSIONS,
        )
        dropped["source_policy_excluded"] += len(excluded_rows)
        assembled_lead_candidates, excluded_rows = partition_records(
            leads_assembled,
            SOURCE_POLICY_EXCLUSIONS,
        )
        dropped["source_policy_excluded"] += len(excluded_rows)
        stations = [st for st in assembled_station_candidates if in_bbox(st.get("lat"), st.get("lon"), bbox)]
        leads = [l for l in assembled_lead_candidates if in_bbox(l.get("lat"), l.get("lon"), bbox)]
        gaia_candidates, excluded_rows = partition_records(gaia_payload.get("stations") or [], SOURCE_POLICY_EXCLUSIONS)
        dropped["source_policy_excluded"] += len(excluded_rows)
        if gaia_cache_info.get("stale"):
            # A stale Gaia cache is useful evidence but never fresh authority. Mark it
            # before merging so enrichment applies the same route-ready guard used for
            # stale MapaREVE rows.
            for station in gaia_candidates:
                station["_volta_stale_live_cache"] = True
        gaia_stations = [
            st for st in gaia_candidates
            if in_bbox(st.get("lat"), st.get("lon"), bbox)
            and (not available_only or bool(st.get("available", 0) > 0 or st.get("status") == "AVAILABLE"))
            and (not fast_only or st.get("fast"))
            and (st.get("price_min") is None or st.get("price_min") <= max_price)
        ]
        engine_record_stage(engine_stages, request_started, request_started, "assembled", mode="cache_hit", stations=len(stations), leads=len(leads))
    else:
        # ---- Phase A: full merge/dedup/enrich build. Unchanged shape/order from before
        # plans/007 step 4, MINUS the distance/eta injection (moved to Phase B below, so
        # it runs identically whether stations/leads came from this fresh build or an
        # assembled-cache reuse above).
        # Re-bound to the request viewport (a stale/broader cache may hold a wider area).
        before_viewport_filter = len(stations)
        stations = [st for st in stations if in_bbox(st.get("lat"), st.get("lon"), bbox)]
        dropped["out_of_view_live"] += max(0, before_viewport_filter - len(stations))
        # Reapply fast_only/max_price too, unconditionally (a no-op for a fresh fetch;
        # closes the stale/broader-fallback reuse gap where reused MapaREVE rows were
        # never re-checked against the current request's route filters).
        before_route_filter = len(stations)
        stations = [st for st in stations if station_passes_route_filters(st, max_price=max_price, fast_only=fast_only)]
        dropped["route_filtered"] += max(0, before_route_filter - len(stations))
        gaia_candidates, excluded_rows = partition_records(gaia_payload.get("stations") or [], SOURCE_POLICY_EXCLUSIONS)
        dropped["source_policy_excluded"] += len(excluded_rows)
        if gaia_cache_info.get("stale"):
            for station in gaia_candidates:
                station["_volta_stale_live_cache"] = True
        gaia_stations = [
            st for st in gaia_candidates
            if in_bbox(st.get("lat"), st.get("lon"), bbox)
            and (not available_only or bool(st.get("available", 0) > 0 or st.get("status") == "AVAILABLE"))
            and (not fast_only or st.get("fast"))
            and (st.get("price_min") is None or st.get("price_min") <= max_price)
        ]
        stations = merge_gaia_stations(stations, gaia_stations)
        engine_record_stage(
            engine_stages,
            request_started,
            request_started,
            "gaia",
            duration_ms=gaia_result["duration_ms"],
            parallel=True,
            error_type=type(gaia_result["error"]).__name__ if gaia_result.get("error") else None,
            stations=len(gaia_payload.get("stations") or []),
            included=len(gaia_stations),
            cache_hit=bool(gaia_cache_info.get("hit")),
        )
        before_reject = len(stations)
        stations = [st for st in stations if not reject_station_reason(st)]
        dropped["rejected_live"] += max(0, before_reject - len(stations))

        # Load leads early: a lead duplicating a live station folds its source into that station
        # (corroboration) before scoring; the rest remain as in-view leads.
        leads = []
        if include_leads:
            stage_started = time.perf_counter()
            in_view_leads = load_all_leads(bbox)
            before_dedupe = len(in_view_leads)
            leads = remove_duplicate_leads(in_view_leads, stations)
            dropped["duplicate_leads"] += max(0, before_dedupe - len(leads))
            engine_record_stage(engine_stages, request_started, stage_started, "leads", loaded=before_dedupe, remaining=len(leads))

        # Historical Electromaps live refresh stays hard-disabled. The separate
        # ingestion adapter may attach only dated additive price evidence; owner
        # collection policy does not authorize live promotion or provider reuse.
        electromaps_did_work = False
        if include_leads and ELECTROMAPS_DIRECT_ENABLED:
            fresh_em: List[Dict[str, Any]] = []
            kept: List[Dict[str, Any]] = []
            stale_dropped = 0
            for l in leads:
                if "electromaps" not in str(l.get("source") or "").lower():
                    kept.append(l)
                    continue
                mins = age_minutes(l.get("last_seen"))
                if mins is not None and mins > ELECTROMAPS_MAX_AGE_DAYS * 1440:
                    stale_dropped += 1  # no Electromaps telemetry within the window -> drop as stale
                    continue
                fresh_em.append(l)
                kept.append(l)
            leads = kept
            em_info["stale_dropped"] = stale_dropped
            dropped["electromaps_stale"] += stale_dropped

            electromaps_did_work = bool(fresh_em) and ELECTROMAPS_LIVE_MAX > 0
            if electromaps_did_work:
                stage_started = time.perf_counter()
                if user_lat is not None and user_lon is not None:
                    fresh_em.sort(key=lambda l: haversine_km(user_lat, user_lon, safe_float(l.get("lat")) or 0.0, safe_float(l.get("lon")) or 0.0))
                try:
                    live = fetch_electromaps_live(fresh_em, max_price=max_price, fetched_at=now_iso())
                except Exception as exc:  # a live-aggregator hiccup must never break the response
                    _log_upstream_error("Electromaps live refresh failed", exc)
                    live = {"stations": [], "checked_ids": set(), "promoted_ids": set(), "dead_ids": set(), "error": type(exc).__name__}
                em_info.update(live)
                em_candidates, excluded_rows = partition_records(em_info.get("stations") or [], SOURCE_POLICY_EXCLUSIONS)
                dropped["source_policy_excluded"] += len(excluded_rows)
                em_stations = [
                    st for st in em_candidates
                    if in_bbox(st.get("lat"), st.get("lon"), bbox)
                    and (not available_only or bool(st.get("available", 0) > 0 or st.get("status") == "AVAILABLE"))
                    and (not fast_only or st.get("fast"))
                    and (st.get("price_min") is None or st.get("price_min") <= max_price)
                ]
                stations = merge_nearby_source_stations(stations, em_stations)
                remove_ids = (em_info.get("promoted_ids") or set()) | (em_info.get("dead_ids") or set())

                def _drop_em(l: Dict[str, Any]) -> bool:
                    if "electromaps" not in str(l.get("source") or "").lower():
                        return False
                    lid = _electromaps_id(l)
                    return bool(lid and lid in remove_ids)  # now live (merged) or now out of service

                leads = [l for l in leads if not _drop_em(l)]
                dropped["electromaps_dead"] += len(em_info.get("dead_ids") or [])
                engine_record_stage(engine_stages, request_started, stage_started, "electromaps", checked=len(em_info.get("checked_ids") or []), promoted=len(em_info.get("promoted_ids") or []), dead=len(em_info.get("dead_ids") or []), error=em_info.get("error"))

        stations, excluded_rows = partition_records(stations, SOURCE_POLICY_EXCLUSIONS)
        dropped["source_policy_excluded"] += len(excluded_rows)
        leads, excluded_rows = partition_records(leads, SOURCE_POLICY_EXCLUSIONS)
        dropped["source_policy_excluded"] += len(excluded_rows)

        # ---- Enrichment (expensive; this is exactly what a reuse above avoids
        # repeating). Distance/eta are deliberately NOT set here -- that is Phase B
        # below, shared with the reuse path, since it depends on THIS request's lat/lon.
        stage_started = time.perf_counter()
        for st in stations:
            apply_corroboration(st)
            enrich_station(st, live=True)
            enforce_stale_cache_guard(st)
        engine_record_stage(engine_stages, request_started, stage_started, "enrich_live", stations=len(stations))

        if include_leads:
            stage_started = time.perf_counter()
            for lead in leads:
                apply_corroboration(lead)
                # Corroboration must reach ranking, and load_all_leads' memoized rows
                # were normalized/enriched before this per-request corroboration
                # boost is attached -- re-score explicitly. Phase B re-scores again
                # after distance is known (recommendation_score is a pure function of
                # the row's current fields, so re-running it is exact, not approximate).
                lead["recommendation_score"] = recommendation_score(lead)
            engine_record_stage(engine_stages, request_started, stage_started, "enrich_leads", leads=len(leads))

        # plans/007 step 4: cache the assembled (merged + enriched, pre-distance/eta,
        # pre-sort) rows -- ONLY on the fully-fresh MapaREVE path (a genuine live fetch
        # succeeded this request; never a cache_fresh hit -- nothing new to store -- and
        # never a stale/fallback serve). See the reuse read above for the matching
        # conditions this write must satisfy to ever be reused.
        #
        # plans/007 REVISE (adviser Finding 1; qa/defects.csv D-153): additionally refuse the write outright
        # when Gaia was ITSELF serving a stale fallback this request -- writing
        # such an entry would let a later "gaia looks fresh now" read reuse write-time-
        # stale station content (e.g. a since-vanished AVAILABLE row) with no stale
        # warning at all, since the per-request warning text is always recomputed fresh
        # from THIS call's own Gaia payload but the reused STATION rows never were.
        gaia_fresh_at_write = not gaia_cache_info.get("stale")
        if mapareve_fresh_fetch and gaia_fresh_at_write:
            with _CACHE_LOCKS["live"]:
                CACHE["assembled"] = {
                    "key": cache_key,
                    "include_leads": include_leads,
                    "ts": now,
                    "stations": deepcopy(stations),
                    "leads": deepcopy(leads),
                    "em_info": deepcopy(em_info),
                    "electromaps_did_work": electromaps_did_work,
                    # Finding 2: the exact request bbox `stations`/`leads` above were
                    # filtered to (see the viewport re-filter earlier in this branch) --
                    # a later read may reuse these rows only when ITS bbox is fully
                    # covered by this one (bbox_contains, checked at the reuse read).
                    "bbox": dict(bbox),
                    # Finding 1: write-time data identity (see the reuse read's comment).
                    "gaia_populated_at": gaia_cache_info.get("populated_at"),
                }
        elif mapareve_fresh_fetch:
            # A brand-new MapaREVE fetch just replaced CACHE["payload"]/CACHE["ts"]
            # (mapareve_fresh_fetch implies that unconditional write already ran, above),
            # but Gaia was stale THIS request, so no new assembled snapshot was
            # written. Any OLD assembled entry under this same cache_key is now for a
            # DIFFERENT (already-replaced) MapaREVE fetch than the current raw cache, so
            # drop it rather than leave it sitting there reusable -- otherwise a later
            # request whose Gaia populated_at happens to still match the OLD entry
            # (their own cache TTLs can outlive MapaREVE's) could reuse merged content
            # whose MapaREVE portion no longer matches CACHE["payload"] at all.
            with _CACHE_LOCKS["live"]:
                CACHE["assembled"] = None

    # ---- Phase B: per-request finishing. Distance/eta and the recommendation_score
    # recompute that depends on it (the only enrich_station output that varies with
    # lat/lon) run here, followed by the available_only filter and sort -- identically
    # whether stations/leads came from the fresh build above or an assembled-cache reuse.
    stage_started = time.perf_counter()
    if user_lat is not None and user_lon is not None:
        for st in stations:
            st["distance_km"] = round(haversine_km(user_lat, user_lon, st["lat"], st["lon"]), 2)
            st["drive_eta_min_est"] = max(2, round(st["distance_km"] / 32 * 60))
            st["recommendation_score"] = recommendation_score(st)
    if available_only:
        before_available_filter = len(stations)
        stations = [st for st in stations if route_ready_now(st)]
        dropped["available_filter"] += max(0, before_available_filter - len(stations))
    if user_lat is not None and user_lon is not None:
        stations.sort(key=nearby_sort_key)
    else:
        stations.sort(key=lambda s: -s.get("recommendation_score", 0))
    engine_record_stage(engine_stages, request_started, stage_started, "finish_stations", stations=len(stations), route_ready=sum(1 for st in stations if route_ready_now(st)))

    intel_meta = load_intelligence_meta()
    if include_leads:
        stage_started = time.perf_counter()
        if user_lat is not None and user_lon is not None:
            for lead in leads:
                lead["distance_km"] = round(haversine_km(user_lat, user_lon, lead["lat"], lead["lon"]), 2)
                lead["drive_eta_min_est"] = max(2, round(lead["distance_km"] / 32 * 60))
                lead["recommendation_score"] = recommendation_score(lead)
            leads.sort(key=nearby_sort_key)
        else:
            leads.sort(key=lambda s: -s.get("recommendation_score", 0))
        payload["leads"] = leads
        engine_record_stage(engine_stages, request_started, stage_started, "finish_leads", leads=len(leads))
    else:
        payload["leads"] = []

    # Response-level cache flags describe the aggregate after every permitted live
    # source has been merged. MapaREVE can fail while a fresh Gaia row remains valid;
    # in that case the response is partially live, not globally failed. Conversely,
    # stale rows retain cache_stale and cannot supply fresh authority.
    fresh_live_authority = any(
        not station.get("lead")
        and not station.get("cache_stale")
        and bool((station.get("verification") or {}).get("live_verified"))
        for station in stations
    )
    has_stale_live_evidence = any(station.get("cache_stale") for station in stations)
    response_cache = payload.setdefault("cache", {})
    response_cache["fresh_live_authority"] = fresh_live_authority
    response_cache["mixed_live"] = bool(fresh_live_authority and has_stale_live_evidence)
    # `stale` describes evidence that survived into this response, not merely that
    # MapaREVE consulted a fallback before viewport/filter/identity reconciliation.
    # A fully-overlapped or out-of-view stale row cannot demote a fresh Gaia result.
    response_cache["stale"] = bool(has_stale_live_evidence)
    response_cache["live_failed"] = bool(
        not fresh_live_authority and mapareve_failure_kind and not has_stale_live_evidence
    )
    if mapareve_failure_warning and mapareve_failure_kind:
        if fresh_live_authority and has_stale_live_evidence:
            replacement = (
                f"MapaREVE refresh failed ({mapareve_failure_kind}); its cached rows are marked stale, "
                "while fresh permitted secondary-source status remains live."
            )
        elif fresh_live_authority:
            replacement = (
                f"MapaREVE live source unavailable ({mapareve_failure_kind}); "
                "fresh permitted secondary-source status remains live."
            )
        elif not has_stale_live_evidence:
            replacement = (
                f"Live source unavailable (MapaREVE {mapareve_failure_kind}); "
                "no cached live rows remain after current viewport and route filters; "
                "nothing is presented as live."
            )
        else:
            replacement = mapareve_failure_warning
        current_warning = str(payload.get("warning") or "")
        if current_warning.startswith(mapareve_failure_warning):
            payload["warning"] = f"{replacement}{current_warning[len(mapareve_failure_warning):]}"

    coverage = live_coverage_profile(stations, payload["leads"])
    if payload.get("partial") is True:
        partial_reason = str(payload.get("partial_reason") or "")
        partial_summary, partial_warning = mapareve_partial_details(partial_reason)
        coverage.update({
            "mode": "degraded",
            "is_degraded": True,
            "partial": True,
            "partial_reason": partial_reason or "unknown",
            "summary": partial_summary,
            "warning": partial_warning,
        })
    if coverage.get("warning"):
        existing_warning = str(payload.get("warning") or "").strip()
        if coverage["warning"] not in existing_warning:
            payload["warning"] = f"{existing_warning} {coverage['warning']}".strip() if existing_warning else coverage["warning"]
    if dropped["source_policy_excluded"]:
        policy_warning = "Some source evidence was excluded by the active source policy."
        existing_warning = str(payload.get("warning") or "").strip()
        payload["warning"] = f"{existing_warning} {policy_warning}".strip() if existing_warning else policy_warning

    payload["stations"] = stations
    payload["price_evidence_counts"] = price_evidence_counts(stations + payload["leads"])
    payload["gaia"] = {
        "live_stations": len(gaia_payload.get("stations", [])),
        "live_connectors": gaia_payload.get("raw_count", 0),
        "included_stations": len(gaia_stations),
        "cache": gaia_payload.get("cache"),
        "errors": gaia_payload.get("errors", []),
        "policy": "GET-only public Gaia start-session page parser; no login, private API, payment setup, start-session or confirmation requests.",
    }
    payload["fenie"] = {
        "status": FENIE_SOURCE_STATUS,
        "station_evidence_records": fenie_origin_record_count(intel_meta.get("records") or []),
        "source_url": FENIE_ATTRIBUTION_HOME,
        "policy": FENIE_POLICY_TEXT,
        "revocation_gate": "Withdrawing the owner-attested permission restores the Feníe exclusion record; cached Feníe rows are then partitioned out.",
    }
    payload["electromaps"] = {
        "status": PRICE_EVIDENCE_SOURCE_STATUS,
        "price_evidence_records": payload["price_evidence_counts"]["electromaps_community"],
        "checked_leads": 0,
        "promoted_live": 0,
        "out_of_service_dropped": 0,
        "runtime_live_enabled": False,
        "source_url": "https://www.electromaps.com/",
        "policy": PRICE_EVIDENCE_POLICY + " Community prices retain the source update date; a fresh fetch is not a new observation. Historical live refresh remains disabled.",
    }
    payload["intel"] = {
        "generated_at": intel_meta.get("generated_at"),
        "total_records": intel_meta.get("total_records", 0),
        **intelligence_counts(intel_meta),
        "errors": intel_meta.get("errors", []),
        "license_notes": intel_meta.get("license_notes", []),
    }
    payload["meta"] = {
        "scope": "worldwide",
        "viewport": {"bbox": bbox, "source": vp_source, "clamped": vp_clamped},
        "price_cap_eur_kwh": max_price,
        "live_source": "MapaREVE public OCPI API (viewport bbox) plus permitted Gaia public pages",
        "refresh_policy": f"Server cache {CACHE_TTL_SECONDS}s; client refreshes every 60s.",
        "important_caveat": "Provider app pricing and onsite parking signs override additive price evidence. Public directory price annotations are separate from primary prices, parking and live authority.",
        "coverage": dict(coverage, supported=True),
        "live_sources": geography,
        "named_provider_strategy": {
            "Google Maps": "deep links/navigation only in this build; Places API requires key/billing and its EV fields do not expose €/kWh tariffs",
            "Repsol": "bounded Spain-only additive published network tariff; station may differ; exact registry operator match only; no primary price or live authority",
            "Electromaps": "bounded Spain-only anonymous public GET price annotations with mandatory source update date; no primary price, new pins or live promotion; owner policy is not a provider licence",
            "Chargemap": "bounded Spain-only anonymous public GET Chargemap Pass prices; payment-route billing and fees remain separate from direct operator prices; owner policy is not a provider licence",
            "Gaia Charge": "implemented for the seeded Alboraia free charger through public start-session HTML pages, with conservative sibling connector probing and short cache TTL; MapaREVE/public directories remain secondary coverage",
            "Feníe Energía Recarga": "offline ingestion of Feníe's own gratuito public-map stations under the owner-attested written redistribution permission, via one anonymous read-only marker-list POST exception; cached rows are never live",
            "LetMeCharge/EuroCharger": "public websites/app-store metadata verified; no unauthenticated station-price map API found on the web surfaces. Use only official API/export or underlying CPO public feeds.",
            "Iberdrola/Acciona/GIC/Miio": "primary facts come from MapaREVE/OCPI or other admitted source evidence; additive directory prices cannot override official/live prices",
        },
    }
    payload["meta"]["engine"] = engine_quality_meta(
        request_started=request_started,
        stages=engine_stages,
        stations=stations,
        leads=payload["leads"],
        bbox=bbox,
        viewport_source=vp_source,
        viewport_clamped=vp_clamped,
        cache_key=cache_key,
        include_leads=include_leads,
        max_price=max_price,
        available_only=available_only,
        fast_only=fast_only,
        dropped=dropped,
    )
    return 200, payload


def source_health_tier(item: Dict[str, Any]) -> str:
    """Coarse source tier used by backend health diagnostics and map-gating audits."""
    if not item.get("lead"):
        return "live"
    reliability = str(item.get("reliability") or "").lower()
    level = str((item.get("verification") or {}).get("level") or "").lower()
    if reliability.startswith("official") or level in {"official", "official-lead"}:
        return "official"
    if reliability.startswith("directory") or "directory" in reliability or level == "directory":
        return "directory"
    if reliability.startswith("community") or level == "community":
        return "community"
    return "unverified"


def source_health_has_valid_coordinates(item: Dict[str, Any]) -> bool:
    lat = safe_float(item.get("lat"))
    lon = safe_float(item.get("lon"))
    return lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180


def source_health_outside_spain(item: Dict[str, Any]) -> bool:
    lat = safe_float(item.get("lat"))
    lon = safe_float(item.get("lon"))
    return lat is not None and lon is not None and not in_bbox(lat, lon, SPAIN_BBOX)


def source_health_has_exact_directory_location(item: Dict[str, Any]) -> bool:
    if source_health_tier(item) != "directory":
        return False
    verification = item.get("verification") or verification_profile(item, live=False)
    if verification.get("location_verified") is not True:
        return False
    primary = item.get("primary_source") or {}
    kind = str(primary.get("kind") or "").lower()
    quality = str(primary.get("quality") or primary.get("exactness") or "").lower()
    if kind in {"station_page", "start_page", "provider_station_page", "map_object", "live_map", "official_data"}:
        return True
    if quality in {"exact", "map", "official"}:
        return True
    url = str(primary.get("url") or item.get("source_url") or "").lower()
    return (
        "map.electromaps.com" in url
        or re.search(r"electromaps\.com/(?:en|es|fr|pt)/p/", url) is not None
        or "chargemap.com/" in url
    )


def source_health_has_exact_osm_location(item: Dict[str, Any]) -> bool:
    if not source_health_has_valid_coordinates(item):
        return False
    url = str((item.get("primary_source") or {}).get("url") or item.get("source_url") or "").lower()
    return re.search(r"openstreetmap\.org/(?:node|way|relation)/\d+", url) is not None


def source_health_map_eligible(item: Dict[str, Any]) -> bool:
    """Mirror the frontend/iOS map rule: live pins, official locations, exact directory/OSM pins."""
    if not source_health_has_valid_coordinates(item):
        return False
    if not item.get("lead"):
        return (item.get("verification") or {}).get("location_verified") is not False

    verification = item.get("verification") or verification_profile(item, live=False)
    if verification.get("route_checked") is True:
        return True
    if source_health_has_exact_directory_location(item):
        return True
    if source_health_has_exact_osm_location(item):
        return True
    return source_health_tier(item) == "official" and verification.get("location_verified") is True


def source_health_cache_status(name: str, cache: Dict[str, Any], ttl_seconds: int) -> Dict[str, Any]:
    ts = safe_float(cache.get("ts"))
    age_seconds = round(time.time() - ts, 1) if ts else None
    payload = cache.get("payload") if isinstance(cache.get("payload"), dict) else {}
    return {
        "name": name,
        "has_payload": bool(payload),
        "age_seconds": age_seconds,
        "ttl_seconds": ttl_seconds,
        "fresh": bool(age_seconds is not None and age_seconds <= ttl_seconds),
        "stale": bool(age_seconds is not None and age_seconds > ttl_seconds),
        "error": cache.get("error"),
        "stations": len(payload.get("stations") or []) if isinstance(payload, dict) else 0,
        "leads": len(payload.get("leads") or []) if isinstance(payload, dict) else 0,
    }


_SOURCE_INVENTORY: Dict[str, Any] = {}


def intelligence_health_fingerprint() -> str:
    """Bind offline evidence aggregates to the code that classifies their rows."""
    digest = hashlib.sha256()
    for name in ("server.py", "station_truth.py", "scripts/global_source_catalog.py"):
        digest.update((ROOT / name).read_bytes())
    return digest.hexdigest()


def _validate_index_summaries(intel: Dict[str, Any]) -> None:
    records = intel.get("records")
    if not isinstance(records, IndexedRecords):
        return
    seeds = DATA / "seed_leads.json"
    manifest = intel.get("index_manifest") or {}
    seed_hash = file_sha256(seeds) if seeds.exists() else None
    if manifest.get("seeds_sha256") != seed_hash or manifest.get("health_code_sha256") != intelligence_health_fingerprint():
        raise ValueError("Intelligence index evidence summary is stale; rebuild with --health-summary")
    for key in ("source_health_inventory", "intel_counts", "global_source_counts", "fenie_origin_records"):
        if key not in records.index.summaries:
            raise ValueError("Intelligence index requires --health-summary for runtime summaries")


def intelligence_counts(intel: Dict[str, Any]) -> Dict[str, int]:
    records = intel.get("records")
    if isinstance(records, IndexedRecords):
        return deepcopy(records.index.summaries["intel_counts"])
    return {
        "free_records": sum(1 for row in records or [] if row.get("is_free")),
        "cheap_records": sum(1 for row in records or [] if row.get("is_cheap")),
        "official_records": sum(1 for row in records or [] if str(row.get("reliability", "")).startswith("official")),
        "access_limited_records": sum(1 for row in records or [] if access_profile(row).get("level") == "restricted"),
    }


def _health_leads(raw: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """Stream aggregate evidence without retaining a second enriched world database."""
    seen = set()
    for rows, source in ((raw.get("records") or [], "free/cheap intelligence DB"), (_raw_seed_records(), "manual research lead")):
        for row in rows:
            lat, lon = safe_float(row.get("lat")), safe_float(row.get("lon"))
            key = ("coord", round(lat, 4), round(lon, 4)) if lat is not None and lon is not None else ("name", str(row.get("name", "")).lower()[:48])
            if key in seen or reject_station_reason(row):
                continue
            seen.add(key)
            if source == "manual research lead" or len(raw.get("records") or []) <= _INTEL_SCOPE_THRESHOLD:
                yield _normalize_lead(deepcopy(row), source)
            else:
                # Health needs evidence/location classification, not pricing, community,
                # navigation, connector detail or recommendation enrichment for every site.
                item = dict(row, lead=True)
                item["primary_source"] = primary_source_for(item)
                item["verification"] = verification_profile(item, live=False)
                yield item


def source_health_inventory(intel: Dict[str, Any], *, allow_index_scan: bool = False) -> Dict[str, Any]:
    seeds = DATA / "seed_leads.json"
    records = intel.get("records")
    if isinstance(records, IndexedRecords) and not allow_index_scan:
        _validate_index_summaries(intel)
        summary = records.index.summaries.get("source_health_inventory")
        if not isinstance(summary, dict):
            raise ValueError("Intelligence index requires --health-summary for source health")
        return deepcopy(summary)
    key = (id(intel), seeds.stat().st_mtime_ns if seeds.exists() else None)
    if _SOURCE_INVENTORY.get("key") == key:
        return deepcopy(_SOURCE_INVENTORY["value"])
    leads = _health_leads(intel)
    by_tier: Dict[str, Dict[str, int]] = {}
    by_reliability: Dict[str, int] = {}
    by_source: Dict[str, Dict[str, int]] = {}
    hidden_from_map = {
        "official_price_unknown": 0,
        "directory": 0,
        "community": 0,
        "unverified": 0,
        "invalid_coordinates": 0,
    }
    coordinate_issues = {"missing_or_invalid": 0}
    source_link_issues = {"missing_primary_url": 0, "generic_primary_source": 0}
    map_eligible_records = 0

    deduped_leads = 0
    by_country: Dict[str, int] = {}
    for item in leads:
        deduped_leads += 1
        country = str(item.get("country_code") or item.get("country") or "unknown").upper()
        country = {"ESP": "ES"}.get(country, country)
        by_country[country] = by_country.get(country, 0) + 1
        tier = source_health_tier(item)
        reliability = str(item.get("reliability") or "unknown")
        source_name = str(item.get("source_label") or item.get("source") or "Unknown")
        verification = item.get("verification") or verification_profile(item, live=False)
        primary = item.get("primary_source") or {}
        eligible = source_health_map_eligible(item)

        tier_row = by_tier.setdefault(tier, {"total": 0, "map_eligible": 0, "hidden_from_map": 0})
        tier_row["total"] += 1
        if eligible:
            tier_row["map_eligible"] += 1
            map_eligible_records += 1
        else:
            tier_row["hidden_from_map"] += 1
            if not source_health_has_valid_coordinates(item):
                hidden_from_map["invalid_coordinates"] += 1
            elif tier == "official" and verification.get("price_verified") is not True:
                hidden_from_map["official_price_unknown"] += 1
            elif tier in {"directory", "community", "unverified"}:
                hidden_from_map[tier] += 1
            else:
                hidden_from_map["unverified"] += 1

        by_reliability[reliability] = by_reliability.get(reliability, 0) + 1
        source_row = by_source.setdefault(source_name, {"total": 0, "map_eligible": 0})
        source_row["total"] += 1
        if eligible:
            source_row["map_eligible"] += 1

        if not source_health_has_valid_coordinates(item):
            coordinate_issues["missing_or_invalid"] += 1
        if not primary.get("url"):
            source_link_issues["missing_primary_url"] += 1
        if primary.get("quality") == "generic":
            source_link_issues["generic_primary_source"] += 1

    result = {
        "deduped_leads": deduped_leads,
        "map_eligible_records": map_eligible_records,
        "hidden_from_map": hidden_from_map,
        "by_tier": by_tier,
        "by_reliability": dict(sorted(by_reliability.items())),
        "by_source": dict(sorted(by_source.items(), key=lambda kv: (-kv[1]["total"], kv[0]))),
        "by_country": dict(sorted(by_country.items())),
        "coordinate_issues": coordinate_issues,
        "source_link_issues": source_link_issues,
    }
    _SOURCE_INVENTORY.update({"key": key, "value": result})
    return deepcopy(result)


def source_health_summary() -> Dict[str, Any]:
    """Machine-readable source audit: what is verified enough for the map vs only a lead."""
    intel = load_intelligence_meta()
    raw_records = intel.get("records") or []
    errors = list(intel.get("errors") or [])
    inventory = source_health_inventory(intel, allow_index_scan=False)
    by_tier = inventory["by_tier"]

    mapareve_cache = source_health_cache_status("MapaREVE live response", CACHE, CACHE_TTL_SECONDS)
    gaia_cache = source_health_cache_status("Gaia public start pages", GAIA_CACHE.get("live") or {}, GAIA_CACHE_TTL_SECONDS)
    fenie_policy = {
        "name": "Feníe Energía Recarga",
        "status": FENIE_SOURCE_STATUS,
        "has_payload": False,
        "stations": fenie_origin_record_count(raw_records),
        "source_url": FENIE_ATTRIBUTION_HOME,
        "policy": FENIE_POLICY_TEXT,
    }
    official_total = by_tier.get("official", {}).get("total", 0)
    official_map = by_tier.get("official", {}).get("map_eligible", 0)
    return {
        "generated_at": now_iso(),
        "scope": "worldwide",
        "live_geography": {"mapareve": "Spain coverage envelope", "gaia": "Configured Alboraia station only"},
        "source_manifest": (intel.get("global_coverage") or {}).get("source_manifests") or intel.get("source_manifest") or {},
        "global_coverage": intel.get("global_coverage") or {},
        "policy": {
            "allowed": ["GET-only public pages (default)", "official/open APIs", "public datasets", "user-verifiable source links", "two owner-approved read-only data-query POSTs (MapaREVE bbox query, Feníe marker list)"],
            "blocked": ["login bypass", "private tokens", "MITM", "payment/session-start actions", "app-private scraping", "any other POST or non-GET request", "sources denied by reuse-rights policy"],
            "summary": "Volta may collect and verify public/user-verifiable evidence only; app-only/private provider data requires official API/export permission.",
        },
        "intelligence": {
            "generated_at": intel.get("generated_at"),
            "total_records": len(raw_records),
            "price_evidence_counts": intel.get("price_evidence_counts") or price_evidence_counts(raw_records),
            **inventory,
            "official_map_coverage": {
                "map_eligible": official_map,
                "total": official_total,
                "ratio": round(official_map / official_total, 4) if official_total else 0,
            },
            "errors": errors,
            "source_policy_excluded_records": intel.get("source_policy_excluded_records", 0),
        },
        "live_cache": {
            "mapareve": mapareve_cache,
            "gaia": gaia_cache,
            "fenie": fenie_policy,
            "electromaps": {
                "name": "Electromaps real-time detail cache",
                "status": PRICE_EVIDENCE_SOURCE_STATUS,
                "cached_details": 0,
                "has_payload": False,
                "runtime_live_enabled": False,
                "source_url": "https://www.electromaps.com/",
                "policy": "Historical runtime live fetching stays disabled. " + PRICE_EVIDENCE_POLICY,
            },
        },
        "engine": {
            "version": ENGINE_VERSION,
            "request_metadata": "Live /api/chargers responses expose additive meta.engine timings, viewport cache key, filters, source/drop counts, and route-ready honesty rules.",
            "route_ready_rule": "A backend route-ready row must be non-stale, non-lead, decision=go, have verification.live_verified=true with no contradictory verification level, and have an available connector/status.",
            "cache_key_rule": "Viewport live-cache keys use the snapped bbox envelope plus price/availability/fast filters; default viewport keeps the legacy filter-only key.",
            "diagnostic_fields": ["meta.engine.elapsed_ms", "meta.engine.stages", "meta.engine.counts", "meta.engine.honesty"],
        },
        "collection_targets": {
            "safe_to_expand": [
                "more official/open datasets with exact coordinates and tariff evidence",
                "provider exports or documented public APIs",
                "public OCPI/roaming feeds that expose status/tariffs without auth bypass",
            ],
            "needs_official_api_or_export": ["LetMeCharge", "EuroCharger", "Miio app-private records"],
            "map_rule": "Live rows, official leads with verified price+location, and exact public directory station pages are eligible for pins. Directory pins remain non-live/verify-first; community rows stay list-only until corroborated.",
        },
    }


_GLOBAL_SOURCE_COUNTS: Dict[str, Any] = {}


def global_source_counts(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(records, IndexedRecords):
        return deepcopy(records.index.summaries["global_source_counts"])
    if _GLOBAL_SOURCE_COUNTS.get("records") is records:
        return _GLOBAL_SOURCE_COUNTS["counts"]
    counts: Dict[str, Any] = {"gaia_total": 0, "gaia_free": 0, "adapters": {}}
    gaia_pattern = re.compile(r"\bgaia\b", re.I)
    for row in records:
        identifier = str(row.get("id") or "")
        for adapter in GLOBAL_ADAPTER_IDS:
            if identifier.startswith(f"global_{adapter}_"):
                counts["adapters"][adapter] = counts["adapters"].get(adapter, 0) + 1
                break
        text = " ".join(str(row.get(key) or "") for key in ("source", "name", "operator"))
        if gaia_pattern.search(text):
            counts["gaia_total"] += 1
            counts["gaia_free"] += bool(row.get("is_free"))
    _GLOBAL_SOURCE_COUNTS.update({"records": records, "counts": counts})
    return counts


def global_adapter_catalog() -> List[Dict[str, Any]]:
    meta = load_intelligence_meta()
    manifests = (meta.get("global_coverage") or {}).get("source_manifests") or []
    last_runs = {row.get("source"): row for row in manifests if isinstance(row, dict)}
    counts = global_source_counts(meta.get("records") or [])["adapters"]
    return [{"id": identifier, "name": name, "scope": scope, "implemented": True,
             "method": method, "credential_requirement": credential,
             "live_status_integrated": False,
             "records_added_by_global_adapter": counts.get(identifier, 0),
             "last_collection": {key: last_runs.get(identifier, {}).get(key) for key in ("scope", "accepted", "rejected", "duplicates", "errors")},
             "status": last_runs.get(identifier, {}).get("status", "not_run")}
            for identifier, name, scope, method, credential in GLOBAL_ADAPTERS]


def requested_source_strategy() -> Dict[str, Any]:
    """Current source-discovery matrix for the apps the owner explicitly named.

    Keep this as a product contract: it tells the UI and the user which apps are
    actually ingested, which are covered through a primary upstream, and which
    require an official API/export before we should treat them as parseable.
    """
    intel = load_intelligence_meta()
    records = intel.get("records", [])
    source_counts = global_source_counts(records)
    evidence_counts = intel.get("price_evidence_counts") or price_evidence_counts(records)
    live_owner_counts: Dict[str, int] = {}
    cached_payload = CACHE.get("payload") or {}
    for station in cached_payload.get("stations", []) if isinstance(cached_payload, dict) else []:
        owner = ((station.get("owner") or {}).get("name") or station.get("source") or "Unknown").strip()
        if owner:
            live_owner_counts[owner] = live_owner_counts.get(owner, 0) + 1
    gaia_cached = (GAIA_CACHE.get("live") or {}).get("payload") or {}
    gaia_live_stations = gaia_cached.get("stations", []) if isinstance(gaia_cached, dict) else []
    gaia_live_connectors = sum(len(st.get("connectors") or []) for st in gaia_live_stations if isinstance(st, dict))
    def live_owner_probe(*needles: str) -> Dict[str, Any]:
        matches = {
            owner: count for owner, count in live_owner_counts.items()
            if any(re.search(needle, owner, re.I) for needle in needles)
        }
        return {
            "live_owner_matches": matches,
            "live_owner_total": sum(matches.values()) if matches else 0,
            "cache_note": "Run /api/chargers?max_price=0.25 first; value is from current MapaREVE cache.",
        }
    return {
        "generated_at": now_iso(),
        "policy": "No login bypass, captcha bypass, private-token extraction, or MITM scraping. Existing source exclusions remain in force except the narrow owner collection decisions described here. " + PRICE_EVIDENCE_POLICY,
        "price_evidence_counts": evidence_counts,
        "price_evidence_scope": "Actual retained annotations, not national price coverage or new directory stations.",
        "requested_apps": [
            {
                "name": "Google Maps",
                "status": "navigation-linkout",
                "method": "Volta links known Google Maps place identifiers and reviewed share links, with labeled name/address/coordinate searches for unmatched stations. Google Places content is not mixed into the non-Google map or used as tariff evidence.",
                "records_now": {"navigation_links": "generated per station/card"},
                "source_url": "https://www.google.com/maps",
                "risk": "Google Maps opens externally for navigation, reviews and place details; ratings/reviews are not fetched into Volta. Provider/source pages and onsite signs remain tariff/parking authority.",
            },
            {
                "name": "Repsol",
                "status": PRICE_EVIDENCE_SOURCE_STATUS,
                "decision_type": PRICE_EVIDENCE_DECISION_TYPE,
                "method": "Every build checks the anonymous official public tariff page by GET. Attach the full qualitative network table only to existing Spanish registry rows whose actual operator is REPSOL SOLUCIONES ENERGÉTICAS SA; station tariffs may differ. No invented kW bands or station-specific numeric price.",
                "records_now": {"additive_price_evidence": evidence_counts["repsol_network_tariff"], "direct_station_records": 0, "live_stations": 0},
                "source_url": "https://www.repsol.es/particulares/vehiculos/movilidad-electrica/recarga-fuera-de-casa/",
                "risk": PRICE_EVIDENCE_POLICY + " An unavailable or changed table withholds this annotation only.",
            },
            {
                "name": "Electromaps",
                "status": PRICE_EVIDENCE_SOURCE_STATUS,
                "decision_type": PRICE_EVIDENCE_DECISION_TYPE,
                "method": "Bounded Spain-only anonymous public map-list/detail GETs attach dated community price text to uniquely matched existing stations. Source updated_at is mandatory and remains distinct from fetch time. Snapshots retain only required matching and price fields, without user profiles, reviews, photos or media. Historical live refresh remains disabled.",
                "records_now": {"additive_price_evidence": evidence_counts["electromaps_community"], "direct_station_records": 0, "live_stations": 0},
                "source_url": "https://www.electromaps.com/",
                "risk": PRICE_EVIDENCE_POLICY + " Old source dates stay old after retrieval; ambiguity withholds the claim.",
            },
            {
                "name": "Gaia Charge",
                "status": "implemented-direct-public-page",
                "method": "Seeded Gaia free charger support parses public start-session HTML pages that expose connector status/tariff/session fields. Sibling connectors are probed conservatively by trailing connector index with short TTL caching. Volta only performs public GET requests; it does not start sessions, submit payment details, use login state, or call private APIs.",
                "records_now": {
                    "direct_live_stations": len(gaia_live_stations),
                    "direct_live_connectors": gaia_live_connectors,
                    "directory_free_records": source_counts["gaia_free"],
                    "directory_total_records": source_counts["gaia_total"],
                    "live_source": "Gaia public start-session page plus MapaREVE/public directories where applicable",
                },
                "source_url": GAIA_DEFAULT_JUMP_URL,
                "risk": "Public page shape can change. Cache gently, treat onsite signs/provider terms as authoritative, and keep this limited to public GET parsing unless Gaia offers an official API/export.",
            },
            {
                "name": "Chargemap",
                "status": PRICE_EVIDENCE_SOURCE_STATUS,
                "decision_type": PRICE_EVIDENCE_DECISION_TYPE,
                "method": "Bounded Spain-only anonymous public map-list and price-HTML GETs attach Price with Chargemap Pass evidence to uniquely matched existing stations. Preserve started-unit billing, idle triggers, grace periods and numeric fees. List is_free never supplies free evidence.",
                "records_now": {"additive_price_evidence": evidence_counts["chargemap_pass"], "direct_station_records": 0, "live_stations": 0},
                "source_url": "https://chargemap.com/",
                "risk": PRICE_EVIDENCE_POLICY + " This is a payment-route price, not a direct operator quote; directory statuses never grant live authority.",
            },
            {
                "name": "Consum store locator",
                "status": "link-only-rights-blocked",
                "method": "Conventional homepage link only. Direct and nested store-location or charger evidence is excluded because the public locator has no open reuse grant.",
                "records_now": {"direct_station_records": 0, "nested_station_evidence_records": 0},
                "source_url": "https://www.consum.es/",
                "risk": "Written permission covering retrieval, caching, transformation, redistribution, attribution, and deep links is required before station evidence can return.",
            },
            {
                "name": "Feníe Energía Recarga",
                "status": FENIE_SOURCE_STATUS,
                "method": FENIE_POLICY_TEXT,
                "records_now": {"direct_station_records": fenie_origin_record_count(records), "nested_station_evidence_records": 0},
                "source_url": FENIE_ATTRIBUTION_HOME,
                "risk": "Rights rest on the owner-attested off-repo written permission; withdrawing it restores the exclusion. Rows are a cached snapshot, never live; the public endpoint can be challenged or change shape, in which case ingestion fails closed or uses a labeled stale cache.",
            },
            {
                "name": "Movilidad Iberdrola",
                "status": "covered-via-primary",
                "method": "Iberdrola-operated public chargers are covered when they appear in MapaREVE/OCPI live data or public directory evidence; no private app/session scraping is used.",
                "records_now": live_owner_probe("iberdrola"),
                "source_url": "https://www.mapareve.es/",
                "risk": "Direct app status/tariff data needs official API/export if not present in public OCPI/directory sources.",
            },
            {
                "name": "Acciona Recarga",
                "status": "covered-via-primary",
                "method": "Acciona-operated public chargers are covered when present in MapaREVE/OCPI live data or public directory evidence.",
                "records_now": live_owner_probe("acciona"),
                "source_url": "https://www.mapareve.es/",
                "risk": "Direct app/session data needs official API/export; public upstream and onsite/provider terms win.",
            },
            {
                "name": "GIC",
                "status": "covered-via-primary",
                "method": "GIC/network records are covered when present in MapaREVE/OCPI live data or public directory evidence.",
                "records_now": live_owner_probe(r"\bGIC\b", "gestion inteligente", "gestión inteligente"),
                "source_url": "https://www.mapareve.es/",
                "risk": "Ambiguous operator naming can hide records; direct app/export would improve attribution.",
            },
            {
                "name": "Miio",
                "status": "covered-via-primary",
                "method": "Miio is treated as an aggregator/app route: stations are covered through MapaREVE/OCPI or underlying public CPO/directory evidence when present, not by private Miio app scraping.",
                "records_now": live_owner_probe(r"\bmiio\b"),
                "source_url": "https://www.miio.pt/",
                "risk": "Direct Miio app database requires official API/export or permission; underlying public CPO evidence remains the safe source.",
            },
            {
                "name": "LetMeCharge",
                "status": "official-api-needed",
                "method": "Public website/Next.js bundles and /map route do not expose station coordinates/prices; App Store/Google Play metadata identify an aggregator app and support contacts.",
                "records_now": {"direct_public_records": 0},
                "source_url": "https://www.letmecharge.net/",
                "risk": "Parsing the mobile app backend would require private app/API inspection. Use official B2B/API/export or aggregate underlying public CPO sources instead.",
            },
            {
                "name": "EuroCharger",
                "status": "official-api-needed",
                "method": "Public website is Wix marketing and old Covergy URL redirects there; no unauthenticated station-price map endpoint was found in public web assets.",
                "records_now": {"direct_public_records": 0},
                "source_url": "https://www.eurocharger.es/",
                "risk": "Use official export/API/contact or underlying CPO public feeds; do not scrape private mobile sessions.",
            },
        ],
    }


def source_catalog_payload() -> Dict[str, Any]:
    """Public, policy-aligned inventory for /api/sources."""
    return {
        "primary_live": [
            "MapaREVE public web API: live OCPI status/tariffs, Spain viewport/bbox-driven, <= €0.25/kWh filter",
            "Gaia public start-session pages: seeded free-charger connector status/tariff/session fields via GET-only HTML parsing",
        ],
        "moat_intelligence": [
            "València municipal OpenData ArcGIS low-cost chargers",
            "Barcelona Endolla CC BY 4.0 locations with conditional official tariff evidence",
            "Madrid CC BY 4.0 and Zaragoza municipal charger facts used only as corroboration until cheap/free evidence matches",
            "Lidl Spain official free-charging store list",
            "OpenStreetMap fee/access/parking tags, including narrowly matched Red MOVEM official tariff evidence",
            "OpenChargeMap public GitHub snapshot free/cost strings",
            "ACCIONA Moraleja Green first-party free-policy lead",
        ],
        "navigation_enrichment": ["Google Maps/Apple Maps deep links"],
        "covered_via_primary": [
            "Independent MapaREVE/OSM/OpenChargeMap records may identify operators, but retired provider-origin evidence is not retained",
            "Iberdrola/Acciona/GIC/Miio -> MapaREVE/OCPI or independent OSM/OpenChargeMap evidence when present",
        ],
        "official_api_needed": ["LetMeCharge direct app database", "EuroCharger direct app database"],
        "policy": "Existing source exclusions and authentication boundaries remain. " + PRICE_EVIDENCE_POLICY,
        "scope": "worldwide",
        "global_adapters": global_adapter_catalog(),
        "source_manifest": (load_intelligence_meta().get("global_coverage") or {}).get("source_manifests") or load_intelligence_meta().get("source_manifest") or {},
        "source_health": source_health_summary(),
        "requested_source_strategy": requested_source_strategy(),
    }


# Minimize sensitive searches, station positions and vehicle OAuth codes/state in
# request logs. Keep paths and the client IP (logged separately by address_string),
# and redact before formatting details can escape through a request-line log.
_LOG_QUERY_STRIP_RE = re.compile(r"(/api/(?:geocode|chargers|station|vehicles(?:/[^?\s]*)?))\?\S*")


def _sanitize_log_line(message: str) -> str:
    return _LOG_QUERY_STRIP_RE.sub(r"\1", message)


class Handler(BaseHTTPRequestHandler):
    server_version = "Volta/2.0"
    # Plan 005 hardening of the plan-004 do_GET/do_HEAD 500-recovery guard (the guard
    # that fixed D-144, the originally-unguarded-handler defect -- NOT D-146, which is
    # the unrelated tzdata boot-crash defect). Fresh Handler instance per request
    # (socketserver model), so this class-level default is never shared mutable state --
    # it just gives do_GET/do_HEAD's 500-recovery a way to know whether
    # send_response()/end_headers() already ran on THIS request before they try to write
    # a second, connection-corrupting response.
    _headers_sent = False

    def log_message(self, fmt: str, *args: Any) -> None:
        # ISO-8601 UTC, with sensitive API query strings removed. Keep the client
        # IP for the existing rate limiter's operational diagnostics.
        message = _sanitize_log_line(fmt % args)
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {self.address_string()} {message}")

    def _client_accepts_gzip(self) -> bool:
        return "gzip" in (self.headers.get("Accept-Encoding") or "").lower()

    def _rate_limit_key(self) -> Optional[str]:
        return _rate_limit_client_key(self)

    def _enforce_rate_limit(self, route: str, limit_rpm: int, extra_headers: Optional[Dict[str, str]] = None) -> bool:
        """Returns True if the request may proceed. Sends 429 + Retry-After and
        returns False otherwise. Trusted/internal callers (see
        _rate_limit_client_key) are exempt entirely -- local dev/QA is never throttled."""
        client_key = self._rate_limit_key()
        if client_key is None:
            return True
        allowed, retry_after = _check_rate_limit(f"{route}:{client_key}", limit_rpm)
        if allowed:
            return True
        retry_after_seconds = max(1, math.ceil(retry_after))
        self.send_json(
            429,
            {
                "error": "rate_limited",
                "warning": "Too many requests; please slow down and try again shortly.",
                "retry_after_seconds": retry_after_seconds,
            },
            extra_headers={**(extra_headers or {}), "Retry-After": str(retry_after_seconds)},
        )
        return False

    def send_json(self, status: int, payload: Dict[str, Any], extra_headers: Optional[Dict[str, str]] = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_json_bytes(status, body, extra_headers=extra_headers)

    def send_json_bytes(
        self,
        status: int,
        body: bytes,
        gz: Optional[bytes] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        # Payloads here reach megabytes (the national overview is ~5 MB raw, ~260 KB gzipped);
        # over a tunnel/mobile that 20x matters far more than the CPU spent compressing.
        use_gzip = self._client_accepts_gzip() and len(body) > 1400
        if use_gzip and gz is None:
            gz = gzip_lib.compress(body, 6)
        out = gz if (use_gzip and gz is not None) else body
        self.send_response(status)
        # Public-hardening response headers (unconditional -- always safe for a JSON API).
        headers = {
            "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store",
            "Access-Control-Allow-Origin": CORS_ORIGIN, "Vary": "Accept-Encoding",
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
        }
        if use_gzip and gz is not None:
            headers["Content-Encoding"] = "gzip"
        if extra_headers:
            headers.update(extra_headers)
        for header_name, header_value in headers.items():
            self.send_header(header_name, header_value)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self._headers_sent = True
        try:
            self.wfile.write(out)
        except BrokenPipeError:
            return

    def do_HEAD(self) -> None:
        try:
            self._route_head()
        except BrokenPipeError:
            return
        except Exception:
            self.log_message("%s", f"UNHANDLED HEAD {traceback.format_exc(limit=8)}")
            if self._headers_sent:
                return  # a response already started on this connection; don't send a second one
            try:
                self.send_error(500)
            except Exception:
                return

    def _route_head(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/":
            path = "/index.html"
        if path == agent_mcp.MCP_PATH:
            self._send_mcp_method_not_allowed(head=True)
            return
        if path.startswith("/api/"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self._headers_sent = True
            return
        safe = Path(os.path.normpath(path.lstrip("/")))
        if ".." in safe.parts:
            self.send_error(403)
            return
        file_path = web_root() / safe
        if file_path.is_dir():
            file_path = file_path / "index.html"
        if not file_path.exists() or not file_path.is_file():
            if not safe.suffix:
                file_path = web_root() / "index.html"
            if not file_path.exists() or not file_path.is_file():
                self.send_error(404)
                return
        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(file_path.stat().st_size))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if VOLTA_CSP and file_path.name == "index.html":
            self.send_header("Content-Security-Policy", VOLTA_CSP)
        self.end_headers()
        self._headers_sent = True

    def do_GET(self) -> None:
        try:
            self._route_get()
        except BrokenPipeError:
            return
        except Exception:
            # Server-side only: the client body below carries NO exception detail (fail-closed
            # voice, consistent with existing warnings -- nothing is presented as live on an
            # internal error either). D-144: do_GET previously had no top-level guard, so an
            # uncaught exception (e.g. malformed feed data reaching live_response) aborted the
            # TCP connection with no HTTP response at all.
            self.log_message("%s", f"UNHANDLED GET {traceback.format_exc(limit=8)}")
            if self._headers_sent:
                return  # a response already started on this connection; don't send a second one
            try:
                self.send_json(500, {
                    "error": "internal_error",
                    "warning": "Internal error; nothing is presented as live.",
                })
            except Exception:
                return

    def _route_get(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)
        if path == "/api/vehicles" or path.startswith("/api/vehicles/"):
            return self._route_vehicle_get(path, params)
        if path == agent_mcp.MCP_PATH:
            # MCP is POST-only: no standalone SSE stream, so GET is 405 (never the SPA).
            return self._send_mcp_method_not_allowed()
        if path == "/api/chargers":
            if not self._enforce_rate_limit("chargers", RATE_LIMIT_RPM):
                return
            # force=true bypasses the live cache outright; honoring it from any public
            # caller is exactly the single-slot-cache-defeating abuse this plan closes
            # (see _rate_limit_client_key). Force is honored ONLY for a genuinely-internal
            # caller -- a loopback peer with no forwarded identity, i.e. _rate_limit_key()
            # returns None. Every public caller -- a direct raw-peer IP OR a resolved
            # trusted-proxy X-Forwarded-For identity, both non-None keys -- gets force
            # silently neutered rather than rejected, so a stray force=true from an
            # untrusted caller just behaves like a normal cached request.
            if self._rate_limit_key() is not None and (params.get("force") or ["false"])[0].lower() == "true":
                params = dict(params)
                params["force"] = ["false"]
            # Hot path: any overview=true request (the default national view with no
            # bbox, OR the SPA's instant bbox-scoped first-paint layer -- plans/007
            # step 5) is served from the bytes cache; overview_response_bytes routes
            # bbox vs no-bbox requests to separate capped memos internally.
            if (params.get("overview") or ["false"])[0].lower() == "true":
                max_price = _parse_max_price((params.get("max_price") or [None])[0])
                fast_only = (params.get("fast") or ["false"])[0].lower() == "true"
                status, body, gz = overview_response_bytes(params, max_price, fast_only)
                return self.send_json_bytes(status, body, gz)
            status, payload = live_response(params)
            # Plan 013: live rows gain community evidence at the response choke point
            # (leads got theirs inside load_all_leads); fail-closed only.
            attach_community_rows(payload.get("stations") or [])
            if status == 200 and (params.get("free_only") or ["false"])[0].lower() == "true":
                payload = apply_free_only_filter(payload)
            return self.send_json(status, payload)
        if path == "/api/station":
            if not self._enforce_rate_limit("chargers", RATE_LIMIT_RPM):
                return
            status, payload = station_detail_response(params)
            return self.send_json(status, payload)
        if path == "/api/free-registry":
            if not self._enforce_rate_limit("chargers", RATE_LIMIT_RPM):
                return
            status, body, gz = free_registry_response_bytes(params)
            return self.send_json_bytes(status, body, gz)
        if path == "/api/community/summary":
            ids = [sid for sid in (params.get("ids") or [""])[0].split(",") if sid][:200]
            stations = community_store.summaries()["stations"]
            return self.send_json(200, {
                "stations": {sid: stations[sid] for sid in ids if sid in stations},
                "totals": community_store.stats(),
            })
        if path.startswith("/api/community/photo/"):
            resolved = community_store.photo_file(path.rsplit("/", 1)[-1])
            if not resolved:
                return self.send_json(404, {"error": "not_found"})
            photo_path, mime = resolved
            body = photo_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self._headers_sent = True
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass
            return
        if path == "/api/health":
            # plans/007 REVISE (adviser Finding 3, HIGH; qa/defects.csv D-155): CACHE["assembled"] (step 4) can
            # hold station/lead ARRAYS and an em_info dict containing real Python set()
            # values (checked_ids/promoted_ids/dead_ids) -- both are non-JSON-serializable
            # (set) or simply too large/irrelevant for a health probe, so dumping the raw
            # entry straight into json.dumps 500'd (TypeError) the moment one assembled
            # entry existed, i.e. right after the server's first successful live fetch.
            # "assembled" is reduced to a small scalar summary (ts/key/counts) here; every
            # OTHER cache dict is unaffected (still stripped of "payload" only, as before)
            # -- never dump station/lead arrays into health regardless of cache shape.
            cache_snapshot = {k: v for k, v in CACHE.items() if k not in ("payload", "assembled", "recent")}
            assembled = CACHE.get("assembled")
            if assembled:
                cache_snapshot["assembled"] = {
                    "ts": assembled.get("ts"),
                    "key": assembled.get("key"),
                    "include_leads": assembled.get("include_leads"),
                    "electromaps_did_work": assembled.get("electromaps_did_work"),
                    "stations_count": len(assembled.get("stations") or []),
                    "leads_count": len(assembled.get("leads") or []),
                }
            return self.send_json(200, {"ok": True, "time": now_iso(), "cache": cache_snapshot})
        if path == "/api/intel":
            return self.send_json(200, intelligence_response(params))
        if path == "/api/sources":
            return self.send_json(200, source_catalog_payload())
        if path == "/api/source-health":
            return self.send_json(200, source_health_summary())
        if path == "/api/source-strategy":
            return self.send_json(200, requested_source_strategy())
        if path == "/api/geocode":
            # city-jump is human-paced, so geocode gets its own tighter bucket rather
            # than sharing /api/chargers' budget.
            if not self._enforce_rate_limit("geocode", GEOCODE_RATE_LIMIT_RPM):
                return
            q = (params.get("q") or [""])[0]
            return self.send_json(200, {"query": q, "results": geocode_search(q)})
        return self.serve_static(path)

    def do_OPTIONS(self) -> None:
        if urllib.parse.urlparse(self.path).path.startswith("/api/vehicles"):
            try:
                VEHICLES.require_origin(self.headers.get("Origin"))
            except vehicle_store.VehicleError as exc:
                return self.send_json(exc.status, {"error": exc.code, "warning": exc.warning}, extra_headers=self._vehicle_headers())
            self.send_response(204)
            for name, value in self._vehicle_headers().items():
                self.send_header(name, value)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()
            self._headers_sent = True
            return
        # CORS preflight for the community POST endpoints (same-origin SPA never needs
        # it; native/external clients might). Mirrors send_json's CORS origin.
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", CORS_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Accept, X-Volta-Community, X-Volta-Admin, MCP-Protocol-Version, Mcp-Method, Mcp-Name",
        )
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()
        self._headers_sent = True

    def do_DELETE(self) -> None:
        # Legacy Streamable HTTP clients DELETE their session on exit; this server
        # mints no sessions, so the specification's answer is 405.
        if urllib.parse.urlparse(self.path).path == agent_mcp.MCP_PATH:
            return self._send_mcp_method_not_allowed()
        self.send_json(404, {"error": "not_found"})

    def _send_mcp_method_not_allowed(self, head: bool = False) -> None:
        body = json.dumps(
            {"error": "method_not_allowed", "warning": "The MCP endpoint accepts JSON-RPC over POST only."},
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(405)
        self.send_header("Allow", "POST, OPTIONS")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._headers_sent = True
        if not head:
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                return

    def _vehicle_headers(self) -> Dict[str, str]:
        # Private telemetry never inherits the public station API's wildcard CORS.
        return {
            "Access-Control-Allow-Origin": VEHICLES.config.app_origin if VEHICLES.config.configured else "null",
            "Cache-Control": "private, no-store", "Vary": "Cookie, Origin, Accept-Encoding",
            "Cross-Origin-Resource-Policy": "same-origin",
        }

    def _vehicle_redirect(self, location: str, cookie: Optional[str] = None) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()
        self._headers_sent = True

    def _route_vehicle_get(self, path: str, params: Dict[str, List[str]]) -> None:
        try:
            if not self._enforce_rate_limit("vehicle-read", RATE_LIMIT_RPM, self._vehicle_headers()):
                return
            if path in {"/api/vehicles", "/api/vehicles/status"}:
                return self.send_json(200, VEHICLES.status(self.headers.get("Cookie")), extra_headers=self._vehicle_headers())
            if path == "/api/vehicles/native/start":
                if not self._enforce_rate_limit("vehicle-native", 10, self._vehicle_headers()):
                    return
                location, cookie = VEHICLES.native_start(params)
                return self._vehicle_redirect(location, cookie)
            if path == "/api/vehicles/callback":
                location, cookie = VEHICLES.callback(self.headers.get("Cookie"), params)
                return self._vehicle_redirect(location, cookie)
            status = 405 if path in {"/api/vehicles/connect", "/api/vehicles/refresh", "/api/vehicles/disconnect", "/api/vehicles/native/exchange"} else 404
            return self.send_json(status, {"error": "method_not_allowed" if status == 405 else "not_found"},
                                  extra_headers={**self._vehicle_headers(), **({"Allow": "POST"} if status == 405 else {})})
        except vehicle_store.VehicleError as exc:
            return self.send_json(exc.status, {"error": exc.code, "warning": exc.warning}, extra_headers=self._vehicle_headers())
        except Exception as exc:
            # OAuth exceptions must never put token/code-bearing locals or URLs in a traceback.
            self.log_message("Vehicle connection failed (%s)", type(exc).__name__)
            return self.send_json(500, {"error": "vehicle_connection_error", "warning": "Vehicle connection could not be updated."},
                                  extra_headers=self._vehicle_headers())

    def _route_vehicle_post(self, path: str) -> None:
        try:
            if path not in {"/api/vehicles/connect", "/api/vehicles/refresh", "/api/vehicles/disconnect", "/api/vehicles/native/exchange"}:
                return self.send_json(404, {"error": "not_found"}, extra_headers=self._vehicle_headers())
            VEHICLES.require_origin(self.headers.get("Origin"))
            if not self._enforce_rate_limit("vehicle-write", 10, self._vehicle_headers()):
                return
            if self.headers.get_content_type() != "application/json" or self.headers.get("Transfer-Encoding"):
                raise vehicle_store.VehicleError(415, "vehicle_json_required", "Send a JSON object to manage a vehicle connection.")
            try:
                body = json.loads(self._read_body(4096).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise vehicle_store.VehicleError(400, "bad_json", "Body must be a valid JSON object.") from None
            allowed_keys = ({"vehicle_id"} if path == "/api/vehicles/refresh" else
                            {"code", "verifier"} if path == "/api/vehicles/native/exchange" else set())
            if not isinstance(body, dict) or set(body) - allowed_keys:
                raise vehicle_store.VehicleError(400, "bad_vehicle_request", "The vehicle request contains unsupported fields.")
            cookie, headers = self.headers.get("Cookie"), self._vehicle_headers()
            if path == "/api/vehicles/connect":
                payload, new_cookie = VEHICLES.connect(cookie, self.headers.get("Origin"))
                if new_cookie:
                    headers["Set-Cookie"] = new_cookie
            elif path == "/api/vehicles/disconnect":
                payload, headers["Set-Cookie"] = VEHICLES.disconnect(cookie, self.headers.get("Origin"))
            elif path == "/api/vehicles/native/exchange":
                payload, headers["Set-Cookie"] = VEHICLES.native_exchange(self.headers.get("Origin"), body.get("code"), body.get("verifier"))
            else:
                payload = VEHICLES.refresh(cookie, self.headers.get("Origin"), body.get("vehicle_id"))
            return self.send_json(200, payload, extra_headers=headers)
        except vehicle_store.VehicleError as exc:
            return self.send_json(exc.status, {"error": exc.code, "warning": exc.warning}, extra_headers=self._vehicle_headers())
        except community_store.CommunityError as exc:
            return self.send_json(exc.status, {"error": exc.code, "warning": exc.message}, extra_headers=self._vehicle_headers())
        except Exception as exc:
            self.log_message("Vehicle connection failed (%s)", type(exc).__name__)
            return self.send_json(500, {"error": "vehicle_connection_error", "warning": "Vehicle connection could not be updated."},
                                  extra_headers=self._vehicle_headers())

    def _route_mcp_post(self) -> None:
        try:
            body = self._read_body(agent_mcp.MAX_BODY_BYTES)
        except community_store.CommunityError as exc:
            return self.send_json(exc.status, {"jsonrpc": "2.0", "id": None, "error": {"code": agent_mcp.INVALID_REQUEST, "message": exc.message}})
        client_key = self._rate_limit_key()

        def rate_limit() -> Tuple[bool, float]:
            # Only tool calls reach live sources; discovery and listing stay unthrottled.
            if client_key is None:
                return True, 0.0
            return _check_rate_limit(f"mcp:{client_key}", MCP_RATE_LIMIT_RPM)

        status, payload = agent_mcp.handle_http_post(
            body,
            {name: value for name, value in self.headers.items()},
            backend=sys.modules[__name__],
            rate_limit=rate_limit,
            allowed_origins=MCP_ALLOWED_ORIGINS,
        )
        if payload is None:
            self.send_response(status)
            self.send_header("Access-Control-Allow-Origin", CORS_ORIGIN)
            self.send_header("Content-Length", "0")
            self.end_headers()
            self._headers_sent = True
            return
        return self.send_json(status, payload)

    def do_POST(self) -> None:
        try:
            self._route_post()
        except BrokenPipeError:
            return
        except community_store.CommunityError as exc:
            if self._headers_sent:
                return
            try:
                self.send_json(exc.status, {"error": exc.code, "warning": exc.message})
            except Exception:
                return
        except Exception:
            self.log_message("%s", f"UNHANDLED POST {traceback.format_exc(limit=8)}")
            if self._headers_sent:
                return
            try:
                self.send_json(500, {"error": "internal_error", "warning": "Internal error; nothing was recorded."})
            except Exception:
                return

    def _read_body(self, cap: int) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            raise community_store.CommunityError(400, "bad_length", "Invalid Content-Length.")
        if length <= 0:
            raise community_store.CommunityError(411, "length_required", "Content-Length is required.")
        if length > cap:
            raise community_store.CommunityError(413, "too_large", f"Body exceeds {cap} bytes.")
        return self.rfile.read(length)

    def _parse_json_body(self) -> Dict[str, Any]:
        raw = self._read_body(COMMUNITY_JSON_MAX_BYTES)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise community_store.CommunityError(400, "bad_json", "Body must be valid JSON.")
        if not isinstance(body, dict):
            raise community_store.CommunityError(400, "bad_json", "Body must be a JSON object.")
        return body

    def _route_post(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)
        if path == "/api/vehicles" or path.startswith("/api/vehicles/"):
            return self._route_vehicle_post(path)
        if path == agent_mcp.MCP_PATH:
            return self._route_mcp_post()
        if not path.startswith("/api/community/"):
            return self.send_json(404, {"error": "not_found"})
        if not COMMUNITY_WRITES_ENABLED:
            raise community_store.CommunityError(
                503,
                "community_writes_disabled",
                "Community writes are disabled on this deployment because durable storage is not configured.",
            )
        if path == "/api/community/moderate":
            # Owner moderation only. Unset token == HTTP moderation disabled (fail closed);
            # the owner can always moderate via sqlite3 on the host.
            if not ADMIN_TOKEN:
                raise community_store.CommunityError(403, "moderation_disabled", "Set VOLTA_ADMIN_TOKEN to enable HTTP moderation.")
            if not hmac.compare_digest(self.headers.get("X-Volta-Admin", ""), ADMIN_TOKEN):
                raise community_store.CommunityError(403, "bad_admin_token", "Invalid admin token.")
            body = self._parse_json_body()
            result = community_store.moderate(str(body.get("kind") or ""), body.get("id"), str(body.get("action") or ""))
            return self.send_json(200, {"ok": True, "result": result})
        if not self._enforce_rate_limit("community-write", COMMUNITY_WRITE_RPM):
            return
        if COMMUNITY_TOKEN and not hmac.compare_digest(self.headers.get("X-Volta-Community", ""), COMMUNITY_TOKEN):
            raise community_store.CommunityError(403, "community_token_required", "Community writes require the X-Volta-Community token on this deployment.")
        client_ip = self._rate_limit_key() or "local-owner"
        if path == "/api/community/checkin":
            body = self._parse_json_body()
            result = community_store.record_checkin(
                body.get("station_id"), body.get("verdict"), body.get("note") or "",
                client_ip=client_ip,
                known_station=lambda sid: sid in known_station_ids(),
            )
            return self.send_json(201, {"ok": True, "checkin": result})
        if path == "/api/community/photo":
            station_id = (params.get("station_id") or [""])[0]
            data = self._read_body(community_store.PHOTO_MAX_BYTES + 1024)
            result = community_store.save_photo(
                station_id, data, client_ip=client_ip,
                known_station=lambda sid: sid in known_station_ids(),
            )
            return self.send_json(201, {"ok": True, "photo": result})
        if path == "/api/community/station":
            body = self._parse_json_body()
            result = community_store.record_suggestion(
                body.get("name"), body.get("lat"), body.get("lon"),
                note=body.get("note") or "", connectors=body.get("connectors") or "",
                client_ip=client_ip,
            )
            return self.send_json(201, {"ok": True, "suggestion": result})
        return self.send_json(404, {"error": "not_found"})

    def serve_static(self, path: str) -> None:
        root = web_root()
        if path == "/":
            path = "/index.html"
        safe = Path(os.path.normpath(path.lstrip("/")))
        if ".." in safe.parts:
            self.send_error(403)
            return
        file_path = root / safe
        if file_path.is_dir():
            file_path = file_path / "index.html"
        if not file_path.exists() or not file_path.is_file():
            # SPA client-side routes have no file suffix: fall back to index.html.
            if not safe.suffix:
                file_path = root / "index.html"
            if not file_path.exists() or not file_path.is_file():
                self.send_error(404)
                return
        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        body = file_path.read_bytes()
        # gzip text assets (the hashed maplibre chunk alone is ~1 MB -> ~270 KB); compressed
        # bytes are memoized per (path, mtime) so hot assets are compressed exactly once.
        gz: Optional[bytes] = None
        compressible = mime.startswith("text/") or mime in {
            "application/javascript", "text/javascript", "application/json",
            "image/svg+xml", "application/manifest+json",
        }
        if compressible and len(body) > 1400 and self._client_accepts_gzip():
            gz_key = (str(file_path), file_path.stat().st_mtime)
            with _CACHE_LOCKS["static_gz"]:
                cached = STATIC_GZ_CACHE.get(gz_key)
            if cached is None:
                # gzip compression itself stays outside the lock (CPU-only, idempotent).
                cached = gzip_lib.compress(body, 6)
                with _CACHE_LOCKS["static_gz"]:
                    if len(STATIC_GZ_CACHE) > 48:
                        STATIC_GZ_CACHE.clear()
                    STATIC_GZ_CACHE[gz_key] = cached
            gz = cached
        out = gz if gz is not None else body
        self.send_response(200)
        self.send_header("Content-Type", mime)
        if safe.parts and safe.parts[0] == "assets":
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        else:
            self.send_header("Cache-Control", "no-cache")
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        # CSP is env-gated OFF by default (VOLTA_CSP empty) -- see the constant's
        # definition for why. Only the HTML document gets it, never hashed/immutable
        # assets, matching the header's actual scope (a page-level policy).
        if VOLTA_CSP and file_path.name == "index.html":
            self.send_header("Content-Security-Policy", VOLTA_CSP)
        if gz is not None:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self._headers_sent = True
        try:
            self.wfile.write(out)
        except BrokenPipeError:
            return


class VoltaHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with a raised TCP accept backlog. The stdlib default
    (request_queue_size=5, from socketserver.TCPServer) drops connections under a
    20/30-way concurrent burst (observed during plans/004 QA); this must be a class
    attribute, not an instance one, because TCPServer.__init__ calls server_bind()
    -> socket.listen(self.request_queue_size) synchronously during construction,
    before any instance attribute could be set."""

    request_queue_size = int(os.environ.get("VOLTA_REQUEST_QUEUE_SIZE", "128"))


# The SPA's first request (preloaded by frontend/index.html). Its serialized body is keyed
# per minute (row ages), so without a warm-up the first visitor each minute, and the first
# after a restart, paid the full enrich+serialize+gzip before seeing any charger.
DEFAULT_OVERVIEW_QUERY = "include_leads=true&max_price=5.0&overview=true&bbox=35.8%2C-9.6%2C43.9%2C3.4&compact=true&summary=true"


def warm_default_overview_forever() -> None:
    params = urllib.parse.parse_qs(DEFAULT_OVERVIEW_QUERY)
    max_price = _parse_max_price((params.get("max_price") or [None])[0])
    while True:
        try:
            overview_response_bytes(params, max_price, False)
        except Exception as exc:  # warm-up is an optimization; requests still compute on a miss
            _log_upstream_error("Default overview warm-up failed", exc)
        # Rebuild just after each minute boundary, when the cache key rolls over.
        time.sleep(60.0 - (time.time() % 60.0) + 0.05)


def main() -> None:
    if not (WEB_DIST / "index.html").exists():
        print(
            "FATAL: frontend/dist missing — run: npm --prefix frontend run build",
            file=sys.stderr,
        )
        raise SystemExit(1)
    port = int(os.environ.get("PORT", "8765"))
    host = os.environ.get("HOST", "127.0.0.1")
    server = VoltaHTTPServer((host, port), Handler)
    threading.Thread(target=warm_default_overview_forever, name="volta-overview-warm", daemon=True).start()
    print(f"Volta Go running on http://{host}:{port}")
    print("Live data: permitted Gaia public pages plus the MapaREVE read-only bbox query (narrow reviewed POST exception)")
    server.serve_forever()


if __name__ == "__main__":
    main()
