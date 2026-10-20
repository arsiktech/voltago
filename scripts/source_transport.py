#!/usr/bin/env python3
"""Reviewed source transport policy (Python standard library only).

Volta's collection boundary is public, read-only retrieval. GET is the default. The
owner explicitly approved exactly two read-only data-query POST families that the
upstream public web apps themselves use:

* MapaREVE public OCPI map query (``POST /api/public/v1/locations?page=N&per_page=M``
  with a JSON bbox/filter body), and
* Feníe Energía public marker list (``POST /punto-recarga/todos`` with an empty body,
  after an anonymous GET of the public map page for its session/CSRF pair).

Everything else that is not GET is refused before a socket is opened. An exception only
matches an exact HTTPS host + exact path + allowed query keys + a bounded, read-only body
shape. A POST exception request never follows redirects (urllib would otherwise silently
turn a redirected POST into a GET on another path or origin), carries no credentials, and
is charged against a small process-wide rolling budget. This module grants no login,
payment, session-start, account or mutation capability.
"""
from __future__ import annotations

import http.cookiejar
import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from typing import Any, Deque, Dict, Optional, Tuple


class SourcePolicyBlocked(RuntimeError):
    """The request is outside the reviewed public read-only collection policy."""


MAPAREVE_HOST = "www.mapareve.es"
MAPAREVE_LOCATIONS_PATH = "/api/public/v1/locations"
FENIE_HOST = "recarga.fenieenergia.es"
FENIE_MARKERS_PATH = "/punto-recarga/todos"

# Hard upper bounds, independent of any caller's own (smaller) configuration.
MAX_POST_TIMEOUT_SECONDS = 45.0
MAPAREVE_MAX_PAGE = 60
MAPAREVE_MAX_PER_PAGE = 20
MAPAREVE_MAX_BODY_BYTES = 2048

_MAPAREVE_BODY_KEYS = {
    "latitude_ne", "longitude_ne", "latitude_sw", "longitude_sw",
    "zoom", "cpo_ids", "only_ocpi", "available",
    "power_min", "power_max", "connector_types",
    "energy_price_min", "energy_price_max", "payment_methods", "facilities",
}
_MAPAREVE_NUMBER_KEYS = {
    "latitude_ne", "longitude_ne", "latitude_sw", "longitude_sw",
    "zoom", "power_min", "power_max", "energy_price_min", "energy_price_max",
}
_MAPAREVE_BOOL_KEYS = {"only_ocpi", "available"}
_MAPAREVE_STRING_LIST_KEYS = {"cpo_ids", "connector_types", "payment_methods", "facilities"}
_MAPAREVE_ALLOWED_CONNECTOR_TYPES = {"IEC_62196_T2_COMBO", "IEC_62196_T2"}
# Credentials and ambient auth never ride a POST exception.
_FORBIDDEN_POST_HEADERS = {"authorization", "proxy-authorization", "x-api-key", "cookie"}

# (window seconds, max POSTs per window) per exception family, process-wide.
POST_BUDGETS: Dict[str, Tuple[float, int]] = {
    "mapareve-locations-query": (600.0, 1000),
    "fenie-markers-list": (600.0, 3),
}
_BUDGET_LOCK = threading.Lock()
_BUDGET_EVENTS: Dict[str, Deque[float]] = {}

# These runtime feeds have narrower GET grants than the general public-page gate.
PUBLIC_LIVE_URLS = {
    "endolla-static": "https://opendata-ajuntament.barcelona.cat/data/dataset/8cdafa08-d378-4bf1-aad4-fafffe815940/resource/9febc26f-d6a7-45f2-8f73-f529ba4da930/download",
    "endolla-status": "https://opendata-ajuntament.barcelona.cat/data/dataset/a2bd4c83-d024-4d78-8436-040ef996cf7f/resource/ada4c823-9566-477d-9362-7b15e7d38189/download",
    "spirii-static": "https://api.spirii.com/v2/afir/energy-infrastructure-tables?countryIsos=ES",
    "spirii-status": "https://api.spirii.com/v2/afir/energy-infrastructure-statuses?countryIsos=ES",
}
PUBLIC_LIVE_BUDGET = (600.0, 24)


def public_live_urlopen(request: urllib.request.Request, *, timeout: float) -> Any:
    """Exact anonymous GETs only, bounded per endpoint, without redirects."""
    if (request.get_method() != "GET" or request.full_url not in PUBLIC_LIVE_URLS.values()
            or request.data is not None or not 0 < timeout <= 6):
        raise SourcePolicyBlocked("Unreviewed public live feed request")
    headers = dict(request.header_items()) | request.unredirected_hdrs
    if any(name.lower() in _FORBIDDEN_POST_HEADERS for name in headers):
        raise SourcePolicyBlocked("Public live feeds never carry credentials")
    current = time.monotonic()
    with _BUDGET_LOCK:
        events = _BUDGET_EVENTS.setdefault(request.full_url, deque())
        while events and current - events[0] > PUBLIC_LIVE_BUDGET[0]:
            events.popleft()
        if len(events) >= PUBLIC_LIVE_BUDGET[1]:
            raise SourcePolicyBlocked("Public live feed GET budget exhausted")
        events.append(current)
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)


def _blocked(message: str) -> SourcePolicyBlocked:
    return SourcePolicyBlocked(message)


def _single_int(query: Dict[str, list], key: str, *, minimum: int, maximum: int) -> None:
    values = query.get(key)
    if values is None:
        return
    if len(values) != 1 or not values[0].isascii() or not values[0].isdigit():
        raise _blocked(f"POST exception query '{key}' must be one positive integer")
    number = int(values[0])
    if not minimum <= number <= maximum:
        raise _blocked(f"POST exception query '{key}' is outside the reviewed bound")


def _body_bytes(request: urllib.request.Request) -> bytes:
    data = request.data
    if data is None:
        return b""
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    raise _blocked("POST exception body must be bytes")


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_mapareve_body(body: bytes) -> None:
    if not body or len(body) > MAPAREVE_MAX_BODY_BYTES:
        raise _blocked("MapaREVE query body must be a non-empty bounded JSON object")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _blocked("MapaREVE query body is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise _blocked("MapaREVE query body must be a JSON object")
    unexpected = set(payload) - _MAPAREVE_BODY_KEYS
    if unexpected:
        raise _blocked("MapaREVE query body has non-read-only/unreviewed fields")
    for key in ("latitude_ne", "longitude_ne", "latitude_sw", "longitude_sw", "zoom"):
        if key not in payload:
            raise _blocked("MapaREVE query body must carry a bbox and zoom")
    for key, value in payload.items():
        if key in _MAPAREVE_NUMBER_KEYS:
            # power_max is legitimately null in the public client's own query.
            if value is None and key in {"power_max"}:
                continue
            if not _is_finite_number(value):
                raise _blocked(f"MapaREVE query field '{key}' must be a finite number")
        elif key in _MAPAREVE_BOOL_KEYS:
            if not isinstance(value, bool):
                raise _blocked(f"MapaREVE query field '{key}' must be boolean")
        elif key in _MAPAREVE_STRING_LIST_KEYS:
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value) or len(value) > 8:
                raise _blocked(f"MapaREVE query field '{key}' must be a short list of strings")
            if key == "connector_types" and not set(value) <= _MAPAREVE_ALLOWED_CONNECTOR_TYPES:
                raise _blocked("MapaREVE query connector type is not reviewed")
            if key in {"cpo_ids", "payment_methods", "facilities"} and value:
                raise _blocked(f"MapaREVE query field '{key}' must stay empty")


def classify_post_exception(request: urllib.request.Request) -> str:
    """Return the matching reviewed POST-exception id or raise SourcePolicyBlocked."""
    method = request.get_method()
    if method != "POST":
        raise _blocked("Source collection permits public GET requests and reviewed read-only query POSTs only")
    try:
        parsed = urllib.parse.urlsplit(request.full_url)
        port = parsed.port
    except ValueError as exc:
        raise _blocked("POST exception URL is malformed") from exc
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password or port not in (None, 443) or parsed.fragment:
        raise _blocked("POST exceptions require plain HTTPS without credentials, custom ports or fragments")
    for name in request.headers:
        if name.lower() in _FORBIDDEN_POST_HEADERS:
            raise _blocked("POST exceptions never carry credentials")
    for name, _value in request.unredirected_hdrs.items():
        if name.lower() in _FORBIDDEN_POST_HEADERS:
            raise _blocked("POST exceptions never carry credentials")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, strict_parsing=bool(parsed.query))
    body = _body_bytes(request)

    if host == MAPAREVE_HOST and parsed.path == MAPAREVE_LOCATIONS_PATH:
        if set(query) - {"page", "per_page"}:
            raise _blocked("MapaREVE query string has unreviewed parameters")
        _single_int(query, "page", minimum=1, maximum=MAPAREVE_MAX_PAGE)
        _single_int(query, "per_page", minimum=1, maximum=MAPAREVE_MAX_PER_PAGE)
        _validate_mapareve_body(body)
        return "mapareve-locations-query"
    if host == FENIE_HOST and parsed.path == FENIE_MARKERS_PATH:
        if parsed.query or body:
            raise _blocked("Feníe marker list takes no query string or body")
        return "fenie-markers-list"
    raise _blocked("POST is not an approved read-only source query for this origin/path")


def _charge_budget(exception_id: str, *, now: Optional[float] = None) -> None:
    window, limit = POST_BUDGETS[exception_id]
    current = time.monotonic() if now is None else now
    with _BUDGET_LOCK:
        events = _BUDGET_EVENTS.setdefault(exception_id, deque())
        while events and current - events[0] > window:
            events.popleft()
        if len(events) >= limit:
            raise _blocked(f"{exception_id} POST budget exhausted for this process window")
        events.append(current)


def reset_budgets() -> None:
    with _BUDGET_LOCK:
        _BUDGET_EVENTS.clear()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect for a POST exception (it would silently become a GET)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401, ANN001
        return None


def _build_post_opener(cookiejar: Optional[http.cookiejar.CookieJar]) -> urllib.request.OpenerDirector:
    handlers: list = [_NoRedirect()]
    if cookiejar is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(cookiejar))
    return urllib.request.build_opener(*handlers)


def policy_urlopen(
    request: urllib.request.Request,
    *,
    timeout: float,
    cookiejar: Optional[http.cookiejar.CookieJar] = None,
) -> Any:
    """Open ``request`` only if it is a GET or a reviewed read-only query POST."""
    if request.get_method() == "GET":
        if cookiejar is None:
            return urllib.request.urlopen(request, timeout=timeout)
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookiejar))
        return opener.open(request, timeout=timeout)
    exception_id = classify_post_exception(request)
    if not (0 < float(timeout) <= MAX_POST_TIMEOUT_SECONDS):
        raise _blocked("POST exception timeout must be positive and bounded")
    _charge_budget(exception_id)
    return _build_post_opener(cookiejar).open(request, timeout=timeout)


def public_price_urlopen(request: urllib.request.Request, *, timeout: float) -> Any:
    """Anonymous, non-redirecting GET for the three additive price sources only."""
    try:
        parsed = urllib.parse.urlsplit(request.full_url)
        port = parsed.port
    except ValueError as exc:
        raise _blocked("Price evidence URL is malformed") from exc
    headers = {name.lower() for name in (*request.headers, *request.unredirected_hdrs)}
    if (request.get_method() != "GET" or request.data is not None
            or parsed.scheme != "https" or parsed.username or parsed.password
            or port not in (None, 443) or parsed.fragment
            or headers & _FORBIDDEN_POST_HEADERS or not 0 < timeout <= 12):
        raise _blocked("Price evidence requires anonymous bounded HTTPS GET")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    allowed = False
    if parsed.hostname == "www.repsol.es":
        allowed = parsed.path == "/particulares/vehiculos/movilidad-electrica/recarga-fuera-de-casa/" and not query
    elif parsed.hostname == "www.electromaps.com":
        allowed = bool(re.fullmatch(r"/mapi/v2/locations/\d{1,12}", parsed.path)) and not query
        if parsed.path == "/mapi/v2/locations":
            fixed = {"realtime": "false", "connectors": "", "types": "ON_STREET,PARKING,AIRPORT,CAMPING,HOTEL,RESTAURANT,SHOP,WORKSHOP,FUEL_STATION,CAR_DEALER,MALL,TAXI", "power": "3", "app": "false", "rfid": "false", "favorites": "false", "skipAuthTokenValidation": "true"}
            allowed = all(query.get(k) == [v] for k, v in fixed.items()) and set(query) == set(fixed) | {"latNE", "lngNE", "latSW", "lngSW"}
            if allowed:
                allowed = _ordered_price_bbox(query, ("latSW", "lngSW", "latNE", "lngNE"))
    elif parsed.hostname == "map.chargemap.com":
        if parsed.path == "/mappy/charging_pools.json" and set(query) == {"NW", "SE"}:
            try:
                n, w = map(float, query["NW"][0].split(";"))
                s, e = map(float, query["SE"][0].split(";"))
                allowed = len(query["NW"]) == len(query["SE"]) == 1 and _price_bbox(s, w, n, e)
            except (ValueError, IndexError):
                pass
        elif re.fullmatch(r"/pool-prices/v3/prices/\d{1,12}", parsed.path):
            allowed = query == {"locale": ["es-es"], "noBootstrap": ["true"], "noFooter": ["true"]}
    if not allowed:
        raise _blocked("Unreviewed price-evidence GET endpoint or query")
    # A fresh opener cannot carry cookies or follow a redirect into authentication,
    # payment or another source. No POST exceptions are invoked here.
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)


def _price_bbox(s: float, w: float, n: float, e: float) -> bool:
    return all(math.isfinite(x) for x in (s, w, n, e)) and 27 <= s < n <= 44.5 and -18.5 <= w < e <= 5


def _ordered_price_bbox(query: Dict[str, list], keys: tuple) -> bool:
    try:
        return all(len(query[k]) == 1 for k in keys) and _price_bbox(*(float(query[k][0]) for k in keys))
    except (ValueError, IndexError):
        return False
