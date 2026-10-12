#!/usr/bin/env python3
"""Reviewed source transport policy (Python standard library only).

Volta's collection boundary is public, read-only retrieval. GET is the default. The
owner explicitly approved exactly four read-only data-query POST families that the
upstream public web apps themselves use:

* MapaREVE public OCPI map query (``POST /api/public/v1/locations?page=N&per_page=M``
  with a JSON bbox/filter body), and
* Feníe Energía public marker list (``POST /punto-recarga/todos`` with an empty body,
  after an anonymous GET of the public map page for its session/CSRF pair), and
* Repsol public charging-point finder list (``POST /bin/repsol/searchmiddleware/
  station-search.json?action=search&idioma=es&tipo=3`` with an empty body; owner
  decision 2026-10-12), and
* Place to Plug public charging-zone query (``POST https://api.placetoplug.com/v2/graphql``
  with one fixed read-only GraphQL ``query`` document, bbox + page variables and no
  credentials; owner decision 2026-10-12).

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
REPSOL_HOST = "www.repsol.es"
REPSOL_SEARCH_PATH = "/bin/repsol/searchmiddleware/station-search.json"
REPSOL_SEARCH_QUERY = {"action": ["search"], "idioma": ["es"], "tipo": ["3"]}
PLACETOPLUG_HOST = "api.placetoplug.com"
PLACETOPLUG_PATH = "/v2/graphql"
PLACETOPLUG_URL = f"https://{PLACETOPLUG_HOST}{PLACETOPLUG_PATH}"
PLACETOPLUG_OPERATION = "probe"
# The only GraphQL document the exception accepts, byte for byte: a read-only list query.
PLACETOPLUG_QUERY = (
    "query probe($query: FindChargingZone!) { findChargingZones(query: $query) "
    "{ totalCount hasNextPage chargingZones { id name coordinates address { street city country } "
    "powerRange { min max } status } } }"
)
# The API silently falls back to 10 rows per page above 50, so 50 is the useful bound.
PLACETOPLUG_MAX_LIMIT = 50
PLACETOPLUG_MAX_PAGE = 2000
PLACETOPLUG_MAX_BODY_BYTES = 2048
# Headers a Place to Plug POST may carry: nothing that identifies an account or session.
_PLACETOPLUG_ALLOWED_HEADERS = {"content-type", "accept", "user-agent", "platform", "accept-language"}

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
    "repsol-charging-points-list": (600.0, 3),
    # A national walk is ~580 pages of 50 zones; the process-wide window is a hard ceiling.
    "placetoplug-zones-query": (600.0, 1000),
}
_BUDGET_LOCK = threading.Lock()
_BUDGET_EVENTS: Dict[str, Deque[float]] = {}


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


def _is_finite_int(value: Any, *, minimum: int, maximum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum


def _validate_placetoplug_request(request: urllib.request.Request, body: bytes) -> None:
    headers = {name.lower(): str(value) for name, value in request.headers.items()}
    headers.update({name.lower(): str(value) for name, value in request.unredirected_hdrs.items()})
    if set(headers) - _PLACETOPLUG_ALLOWED_HEADERS:
        raise _blocked("Place to Plug query carries headers outside the reviewed anonymous set")
    if not headers.get("content-type", "").lower().startswith("application/json"):
        raise _blocked("Place to Plug query must be application/json")
    if not body or len(body) > PLACETOPLUG_MAX_BODY_BYTES:
        raise _blocked("Place to Plug query body must be a non-empty bounded JSON object")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _blocked("Place to Plug query body is not valid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"operationName", "variables", "query"}:
        raise _blocked("Place to Plug query body must be exactly operationName, variables and query")
    if payload["operationName"] != PLACETOPLUG_OPERATION or payload["query"] != PLACETOPLUG_QUERY:
        raise _blocked("Place to Plug accepts only the reviewed read-only zone query document")
    variables = payload["variables"]
    if not isinstance(variables, dict) or set(variables) != {"query"} or not isinstance(variables["query"], dict):
        raise _blocked("Place to Plug variables must be exactly {query: {...}}")
    query = variables["query"]
    if set(query) != {"filters", "params", "limit", "page"}:
        raise _blocked("Place to Plug query variables have unreviewed fields")
    if not _is_finite_int(query["limit"], minimum=1, maximum=PLACETOPLUG_MAX_LIMIT):
        raise _blocked("Place to Plug query limit is outside the reviewed bound")
    if not _is_finite_int(query["page"], minimum=1, maximum=PLACETOPLUG_MAX_PAGE):
        raise _blocked("Place to Plug query page is outside the reviewed bound")
    filters, params = query["filters"], query["params"]
    if not isinstance(filters, dict) or set(filters) != {"bounds"} or not isinstance(filters["bounds"], dict):
        raise _blocked("Place to Plug filters must be exactly a bounds box")
    bounds = filters["bounds"]
    if set(bounds) != {"minLat", "maxLat", "minLon", "maxLon"} or not all(_is_finite_number(v) for v in bounds.values()):
        raise _blocked("Place to Plug bounds must be four finite numbers")
    if not (-90 <= bounds["minLat"] <= bounds["maxLat"] <= 90 and -180 <= bounds["minLon"] <= bounds["maxLon"] <= 180):
        raise _blocked("Place to Plug bounds are not a valid box")
    if not isinstance(params, dict) or set(params) != {"location"} or not isinstance(params["location"], dict):
        raise _blocked("Place to Plug params must be exactly a location")
    location = params["location"]
    if set(location) != {"latitude", "longitude"} or not all(_is_finite_number(v) for v in location.values()):
        raise _blocked("Place to Plug location must be two finite numbers")


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
    if host == REPSOL_HOST and parsed.path == REPSOL_SEARCH_PATH:
        if query != REPSOL_SEARCH_QUERY or body:
            raise _blocked("Repsol charging-point list takes exactly action=search&idioma=es&tipo=3 and no body")
        return "repsol-charging-points-list"
    if host == PLACETOPLUG_HOST and parsed.path == PLACETOPLUG_PATH:
        if parsed.query:
            raise _blocked("Place to Plug query takes no query string")
        _validate_placetoplug_request(request, body)
        return "placetoplug-zones-query"
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
