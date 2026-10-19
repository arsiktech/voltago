"""Owner-authorized, read-only Smartcar telemetry. Python standard library only.

This is separate from the public charging-station source transport. Only fixed
Smartcar HTTPS endpoints are reachable; the only upstream POST is OAuth's token
exchange. Credentials and vehicle data live in bounded process memory, never files.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import math
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from typing import Any, Callable, Mapping


COOKIE_NAME = "volta_vehicle_session"
SESSION_SECONDS = 12 * 60 * 60
STATE_SECONDS = 10 * 60
HANDOFF_SECONDS = 60
NATIVE_CALLBACK = "voltagovehicle://connection/callback"
REFRESH_SECONDS = 60
FRESH_SECONDS = 5 * 60
MAX_SESSIONS = 512
MAX_VEHICLES = 8
MAX_RESPONSE_BYTES = 256 * 1024
REQUEST_SECONDS = 5.0
SYNC_SECONDS = 20.0
SCOPES = ("read_vehicle_info", "read_battery", "read_charge")
_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_SESSION_RE = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_VERIFIER_RE = re.compile(r"[A-Za-z0-9._~-]{43,128}\Z")
_GET_PATH_RE = re.compile(
    r"/v2\.0/vehicles(?:\?limit=8|/[A-Za-z0-9_-]{1,128}(?:/(?:battery(?:/capacity)?|charge))?)\Z"
)
_FIELDS = (
    "batteryPercent", "rangeKm", "chargingState", "chargePowerKw",
    "batteryCapacityKwh", "usableEnergyKwh", "maxAcKw", "maxDcKw", "connectors",
)
_DYNAMIC_FIELDS = ("batteryPercent", "rangeKm", "chargingState", "chargePowerKw")


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _timestamp(value: Any, now: float) -> float | None:
    """sc-data-age is an observation timestamp, not the HTTP retrieval time."""
    if not isinstance(value, str) or len(value) > 80:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        timestamp = parsed.timestamp()
        return timestamp if 0 < timestamp <= now + 30 else None
    except (ValueError, OverflowError, OSError):
        return None


def _number(value: Any, minimum: float, maximum: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and minimum <= number <= maximum else None
    except OverflowError:
        return None


def _text(value: Any, limit: int = 100) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())[:limit]
    return text or None


def _origin(value: str) -> str | None:
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query
            or parsed.fragment or parsed.path not in ("", "/") or "\\" in value
            or any(ord(char) <= 32 for char in value)):
        return None
    host = parsed.hostname.lower()
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and host in {"localhost", "127.0.0.1", "::1"}
    ):
        return None
    if port is not None and not 1 <= port <= 65535:
        return None
    # Keep the exact browser origin, including a non-default local development port.
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc.lower(), "", "", ""))


@dataclass(frozen=True)
class Config:
    app_origin: str = ""
    client_id: str = ""
    client_secret: str = field(default="", repr=False)
    mode: str = "live"

    @classmethod
    def from_environment(cls) -> "Config":
        return cls(
            app_origin=os.environ.get("VOLTA_VEHICLE_APP_ORIGIN", "").strip().rstrip("/"),
            client_id=os.environ.get("VOLTA_SMARTCAR_CLIENT_ID", "").strip(),
            client_secret=os.environ.get("VOLTA_SMARTCAR_CLIENT_SECRET", "").strip(),
            mode=os.environ.get("VOLTA_SMARTCAR_MODE", "live").strip().lower(),
        )

    @property
    def configured(self) -> bool:
        return bool(
            self.client_id and self.client_secret and len(self.client_id) <= 512
            and len(self.client_secret) <= 4096
            and not any(char in self.client_id + self.client_secret for char in "\r\n")
            and _origin(self.app_origin) == self.app_origin
            and self.mode in {"live", "test", "simulated"}
        )

    @property
    def source_mode(self) -> str | None:
        return ("live" if self.mode == "live" else "simulation") if self.configured else None

    @property
    def redirect_uri(self) -> str:
        return self.app_origin + "/api/vehicles/callback"


class VehicleError(Exception):
    def __init__(self, status: int, code: str, warning: str):
        super().__init__(code)
        self.status, self.code, self.warning = status, code, warning


class ProviderError(Exception):
    """Only safe, app-defined codes; never include an upstream body or credential."""
    def __init__(self, status: int, code: str = "provider_unavailable"):
        super().__init__(code)
        self.status, self.code = status, code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SmartcarClient:
    def __init__(self, config: Config):
        self.config = config
        # Retain urllib's environment ProxyHandler and default verified TLS trust.
        # Only redirect handling changes; the allowlist below still runs first.
        self._opener = urllib.request.build_opener(_NoRedirect())

    def token(self, grant: str, value: str, deadline: float) -> dict[str, Any]:
        if grant == "authorization_code":
            form = {"grant_type": grant, "code": value, "redirect_uri": self.config.redirect_uri}
        elif grant == "refresh_token":
            form = {"grant_type": grant, "refresh_token": value}
        else:
            raise ProviderError(400, "invalid_grant")
        payload, _ = self._request("POST", "/oauth/token", deadline, form=form)
        return payload

    def get(self, path: str, access_token: str, deadline: float) -> tuple[dict[str, Any], dict[str, str]]:
        return self._request("GET", path, deadline, access_token=access_token)

    def _request(
        self, method: str, path: str, deadline: float, *,
        access_token: str | None = None, form: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        if method == "GET" and _GET_PATH_RE.fullmatch(path) and access_token and form is None:
            host = "api.smartcar.com"
            body = None
            authorization = "Bearer " + access_token
        elif method == "POST" and path == "/oauth/token" and form and access_token is None:
            host = "auth.smartcar.com"
            if form.get("grant_type") == "authorization_code":
                valid = set(form) == {"grant_type", "code", "redirect_uri"} and form.get("redirect_uri") == self.config.redirect_uri
            else:
                valid = form.get("grant_type") == "refresh_token" and set(form) == {"grant_type", "refresh_token"}
            if not valid:
                raise ProviderError(400, "invalid_grant")
            body = urllib.parse.urlencode(form).encode("ascii")
            credentials = (self.config.client_id + ":" + self.config.client_secret).encode("utf-8")
            authorization = "Basic " + base64.b64encode(credentials).decode("ascii")
        else:
            raise ProviderError(400, "vehicle_request_blocked")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderError(504, "provider_timeout")
        response = None
        try:
            headers = {
                "Accept": "application/json", "Accept-Encoding": "identity",
                "Authorization": authorization, "User-Agent": "Volta-Go/vehicle-connection",
            }
            if method == "GET":
                headers["sc-unit-system"] = "metric"
            else:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            request = urllib.request.Request("https://" + host + path, data=body, headers=headers, method=method)
            response = self._opener.open(request, timeout=min(REQUEST_SECONDS, remaining))
            if response.status < 200 or response.status >= 300:
                code = "provider_rate_limited" if response.status == 429 else "provider_unavailable"
                raise ProviderError(response.status, code)
            response_headers = {name.lower(): value for name, value in response.headers.items()}
            if "application/json" not in response_headers.get("content-type", "").lower():
                raise ProviderError(502, "invalid_provider_data")
            length = response_headers.get("content-length")
            if length is not None and (not length.isdigit() or int(length) > MAX_RESPONSE_BYTES):
                raise ProviderError(502, "invalid_provider_data")
            chunks, size = [], 0
            network_socket = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
            while True:
                if getattr(response, "isclosed", lambda: False)() is True:
                    break
                self._timeout(network_socket, deadline)
                chunk = response.read1(min(32768, MAX_RESPONSE_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise ProviderError(502, "invalid_provider_data")
            payload = json.loads(b"".join(chunks).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ProviderError(502, "invalid_provider_data")
            return payload, response_headers
        except ProviderError:
            raise
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            raise ProviderError(status, "provider_rate_limited" if status == 429 else "provider_unavailable") from None
        except (TimeoutError, OSError, http.client.HTTPException, ValueError, UnicodeError):
            raise ProviderError(502) from None
        finally:
            if response is not None:
                response.close()

    @staticmethod
    def _timeout(network_socket: Any, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderError(504, "provider_timeout")
        if network_socket is not None:
            network_socket.settimeout(min(REQUEST_SECONDS, remaining))


@dataclass(repr=False)
class _Token:
    access: str
    refresh: str
    expires_at: float


@dataclass(repr=False)
class _Session:
    key: str
    expires_at: float
    token: _Token | None = None
    state_digest: str | None = None
    state_expires_at: float = 0
    native_challenge: str | None = None
    handoff_digest: str | None = None
    handoff_expires_at: float = 0
    vehicles: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_attempt_at: float | None = None
    error: str | None = None
    limited: bool = False
    lock: Any = field(default_factory=threading.Lock)
    operation: Any = field(default_factory=threading.Lock)


def _new_vehicle(vehicle_id: str, mode: str) -> dict[str, Any]:
    return {
        "id": vehicle_id, "make": None, "model": None, "year": None,
        **{name: None for name in _FIELDS},
        "usableEnergyEstimated": False,
        "source": {"provider": "Smartcar", "mode": mode},
        "observedAt": None, "fetchedAt": None, "freshUntil": None,
        "lastAttemptAt": None, "isStale": True, "error": None,
        "fieldObservedAt": {name: None for name in _FIELDS},
        "fieldFetchedAt": {name: None for name in _FIELDS},
        "fieldFreshUntil": {name: None for name in _FIELDS},
    }


def _freshness(vehicle: dict[str, Any], now: float, *, failed: bool = False) -> None:
    observations = vehicle["fieldObservedAt"]
    for name in _FIELDS:
        observed = _timestamp(observations.get(name), now)
        vehicle["fieldFreshUntil"][name] = _iso(observed + FRESH_SECONDS) if observed is not None else None
    # kWh is an estimate of usable remaining energy. Never substitute nominal trim
    # specifications, a default pack size, or an observation from another car/time.
    battery_at = _timestamp(observations.get("batteryPercent"), now)
    capacity_at = _timestamp(observations.get("batteryCapacityKwh"), now)
    energy = None
    if (vehicle["batteryPercent"] is not None and vehicle["batteryCapacityKwh"] is not None
            and battery_at is not None and capacity_at is not None
            and abs(battery_at - capacity_at) <= FRESH_SECONDS
            and now <= min(battery_at, capacity_at) + FRESH_SECONDS):
        energy = round(vehicle["batteryCapacityKwh"] * vehicle["batteryPercent"] / 100, 2)
    vehicle["usableEnergyKwh"] = energy
    vehicle["usableEnergyEstimated"] = energy is not None
    observations["usableEnergyKwh"] = _iso(min(battery_at, capacity_at)) if energy is not None else None
    vehicle["fieldFreshUntil"]["usableEnergyKwh"] = _iso(min(battery_at, capacity_at) + FRESH_SECONDS) if energy is not None else None
    vehicle["fieldFetchedAt"]["usableEnergyKwh"] = vehicle["fetchedAt"] if energy is not None else None
    field_times = [_timestamp(observations[name], now) for name in _DYNAMIC_FIELDS if vehicle[name] is not None]
    oldest = min(field_times) if field_times and None not in field_times else None
    vehicle["observedAt"] = _iso(oldest) if oldest is not None else None
    vehicle["freshUntil"] = _iso(oldest + FRESH_SECONDS) if oldest is not None else None
    vehicle["isStale"] = failed or oldest is None or now > oldest + FRESH_SECONDS


class VehicleConnections:
    def __init__(
        self, config: Config | None = None, *,
        client: SmartcarClient | None = None, clock: Callable[[], float] = time.time,
    ):
        self.config = config or Config.from_environment()
        self.client = client or SmartcarClient(self.config)
        self.clock = clock
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()
        self._upstream_slots = threading.BoundedSemaphore(4)

    def require_origin(self, origin: str | None) -> None:
        if not self.config.configured:
            raise VehicleError(503, "vehicle_connection_unavailable", "Vehicle connection has not been enabled on this app yet.")
        # Do not trust Host, Referer or forwarded headers, or allow wildcard CORS.
        if origin != self.config.app_origin:
            raise VehicleError(403, "vehicle_origin_denied", "Open this app directly to manage your vehicle connection.")

    def _session(self, cookie_header: str | None) -> _Session | None:
        value = ""
        if cookie_header and len(cookie_header) <= 4096:
            try:
                cookies = SimpleCookie()
                cookies.load(cookie_header)
                value = cookies[COOKIE_NAME].value if COOKIE_NAME in cookies else ""
            except Exception:
                return None
        if not _SESSION_RE.fullmatch(value):
            return None
        key = hashlib.sha256(value.encode("ascii")).hexdigest()
        with self._lock:
            self._purge()
            return self._sessions.get(key)

    def _purge(self) -> None:
        now = self.clock()
        for key in [key for key, session in self._sessions.items() if session.expires_at <= now]:
            del self._sessions[key]

    def _active(self, session: _Session) -> bool:
        with self._lock:
            return self._sessions.get(session.key) is session and session.expires_at > self.clock()

    def _cookie(self, value: str, *, clear: bool = False) -> str:
        cookie = SimpleCookie()
        cookie[COOKIE_NAME] = value
        morsel = cookie[COOKIE_NAME]
        morsel["path"] = "/"
        morsel["httponly"] = True
        morsel["samesite"] = "Lax"
        morsel["max-age"] = 0 if clear else SESSION_SECONDS
        if self.config.app_origin.startswith("https://"):
            morsel["secure"] = True
        return morsel.OutputString()

    def _new_session(self) -> tuple[_Session, str]:
        value = secrets.token_urlsafe(32)
        key = hashlib.sha256(value.encode("ascii")).hexdigest()
        session = _Session(key=key, expires_at=self.clock() + STATE_SECONDS)
        with self._lock:
            self._purge()
            if len(self._sessions) >= MAX_SESSIONS:
                raise VehicleError(503, "vehicle_connection_busy", "Vehicle connection is busy. Try again shortly.")
            self._sessions[key] = session
        return session, self._cookie(value)

    def _rotate(self, session: _Session) -> str | None:
        value = secrets.token_urlsafe(32)
        new_key = hashlib.sha256(value.encode("ascii")).hexdigest()
        with self._lock:
            if self._sessions.pop(session.key, None) is not session:
                return None
            session.key = new_key
            session.expires_at = self.clock() + SESSION_SECONDS
            self._sessions[new_key] = session
        return self._cookie(value)

    def status(self, cookie_header: str | None = None) -> dict[str, Any]:
        return self._status(self._session(cookie_header) if self.config.configured else None)

    def _status(self, session: _Session | None) -> dict[str, Any]:
        connected, error, limited, last_attempt, rows = False, None, False, None, []
        if session is not None and self._active(session):
            with session.lock:
                connected = session.token is not None
                error, limited, last_attempt = session.error, session.limited, session.last_attempt_at
                rows = deepcopy(list(session.vehicles.values()))
        for vehicle in rows:
            _freshness(vehicle, self.clock(), failed=bool(vehicle.get("error")) or not connected)
        configured = self.config.configured
        connection_status = (
            "unavailable" if not configured else "reconnect" if error == "reconnect_required"
            else "connected" if connected else "disconnected"
        )
        reason = None
        if not configured:
            reason = "Vehicle connection has not been enabled on this app yet."
        elif error == "reconnect_required":
            reason = "Your vehicle authorization expired. Connect again to get new readings."
        elif error:
            reason = "The vehicle provider could not be reached. Previous readings may be out of date."
        elif connected and not rows:
            reason = "No vehicles were authorized. Connect again to choose your electric vehicle."
        return {
            "provider": "smartcar", "configured": configured, "mode": self.config.source_mode,
            "connected": connected, "connectionStatus": connection_status, "reason": reason,
            "refreshIntervalSeconds": REFRESH_SECONDS, "freshnessSeconds": FRESH_SECONDS,
            "lastAttemptAt": _iso(last_attempt) if last_attempt is not None else None,
            "refreshAllowedAt": _iso(last_attempt + REFRESH_SECONDS) if last_attempt is not None else None,
            "vehicleLimitReached": limited, "vehicles": rows,
        }

    def connect(self, cookie_header: str | None, origin: str | None) -> tuple[dict[str, Any], str | None]:
        self.require_origin(origin)
        session, cookie = self._session(cookie_header), None
        if session is None:
            session, cookie = self._new_session()
        if not session.operation.acquire(blocking=False):
            raise VehicleError(409, "vehicle_connection_busy", "Your vehicle connection is updating. Try again shortly.")
        try:
            state = secrets.token_urlsafe(32)
            with session.lock:
                session.state_digest = hashlib.sha256(state.encode("ascii")).hexdigest()
                session.state_expires_at = self.clock() + STATE_SECONDS
            query = urllib.parse.urlencode({
                "response_type": "code", "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri, "scope": " ".join(SCOPES),
                "state": state, "mode": self.config.mode, "approval_prompt": "force",
            })
            return {"authorization_url": "https://connect.smartcar.com/oauth/authorize?" + query}, cookie
        finally:
            session.operation.release()

    def callback(self, cookie_header: str | None, params: Mapping[str, list[str]]) -> tuple[str, str | None]:
        """Always leave OAuth's code-bearing URL for a fixed, same-app landing page."""
        landing = self.config.app_origin + "/?vehicle_connection=" if self.config.configured else "/?vehicle_connection="
        session = self._session(cookie_header) if self.config.configured else None
        if session is not None and session.native_challenge is not None:
            landing = NATIVE_CALLBACK + "?error="
        state_values = params.get("state") or []
        if (session is None or len(state_values) != 1 or len(state_values[0]) > 256
                or not session.operation.acquire(blocking=False)):
            return landing + "error", None
        cookie = None
        try:
            with session.lock:
                valid_state = (
                    session.state_digest is not None and self.clock() < session.state_expires_at
                    and hmac.compare_digest(hashlib.sha256(state_values[0].encode("utf-8")).hexdigest(), session.state_digest)
                )
                if not valid_state:
                    return landing + "error", None
                session.state_digest = None
                session.state_expires_at = 0
            code_values = params.get("code") or []
            if params.get("error") or len(code_values) != 1 or not 1 <= len(code_values[0]) <= 4096:
                return landing + "error", None
            if not self._upstream_slots.acquire(blocking=False):
                return landing + "error", None
            try:
                deadline = time.monotonic() + SYNC_SECONDS
                token = self._parse_token(self.client.token("authorization_code", code_values[0], deadline))
                if not self._active(session):
                    return landing + "error", None
                with session.lock:
                    session.token = token
                    session.error = None
                    session.vehicles = {}
                    session.last_attempt_at = None
                cookie = self._rotate(session)
                if cookie is None:
                    return landing + "error", None
                self._sync(session, deadline, discover=True)
                if session.native_challenge is not None:
                    # The browser cannot transfer its session cookie through a URL.
                    # A short-lived ticket is useful only with the native verifier.
                    handoff = secrets.token_urlsafe(32)
                    with session.lock:
                        session.handoff_digest = hashlib.sha256(handoff.encode("ascii")).hexdigest()
                        session.handoff_expires_at = self.clock() + HANDOFF_SECONDS
                        session.expires_at = session.handoff_expires_at
                    return NATIVE_CALLBACK + "?code=" + handoff, self._cookie("", clear=True)
                return landing + "connected", cookie
            except ProviderError:
                with session.lock:
                    session.error = "provider_unavailable"
                return landing + "error", cookie
            finally:
                self._upstream_slots.release()
        finally:
            session.operation.release()

    def native_start(self, params: Mapping[str, list[str]]) -> tuple[str, str]:
        """Start in an ephemeral system browser; no arbitrary app return URL."""
        challenge = (params.get("challenge") or [None])[0]
        if (set(params) != {"challenge"} or len(params["challenge"]) != 1
                or not isinstance(challenge, str) or not _SESSION_RE.fullmatch(challenge)):
            raise VehicleError(400, "native_challenge_required", "A native S256 connection challenge is required.")
        response, cookie = self.connect(None, self.config.app_origin)
        session = self._session(cookie)
        if session is None:
            raise VehicleError(503, "vehicle_connection_busy", "Vehicle connection is busy. Try again shortly.")
        with session.lock:
            session.native_challenge = challenge
        return response["authorization_url"], cookie

    def native_exchange(self, origin: str | None, code: Any, verifier: Any) -> tuple[dict[str, Any], str]:
        self.require_origin(origin)
        invalid = VehicleError(401, "native_connection_invalid", "This vehicle connection expired. Start the connection again.")
        if (not isinstance(code, str) or not _SESSION_RE.fullmatch(code)
                or not isinstance(verifier, str) or not _VERIFIER_RE.fullmatch(verifier)):
            raise invalid
        digest = hashlib.sha256(code.encode("ascii")).hexdigest()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
        with self._lock:
            self._purge()
            session = next((session for session in self._sessions.values()
                            if session.handoff_digest is not None and hmac.compare_digest(session.handoff_digest, digest)), None)
        if session is None:
            raise invalid
        if not session.operation.acquire(blocking=False):
            raise VehicleError(409, "vehicle_connection_busy", "Your vehicle connection is already updating.")
        try:
            with session.lock:
                if (session.handoff_digest is None or not hmac.compare_digest(session.handoff_digest, digest)
                        or self.clock() >= session.handoff_expires_at or session.native_challenge is None
                        or not hmac.compare_digest(challenge, session.native_challenge)):
                    raise invalid
                session.handoff_digest = None
                session.handoff_expires_at = 0
                session.native_challenge = None
            cookie = self._rotate(session)
            if cookie is None:
                raise invalid
            return self._status(session), cookie
        finally:
            session.operation.release()

    def _parse_token(self, payload: dict[str, Any], previous: _Token | None = None) -> _Token:
        access = payload.get("access_token")
        refresh = payload.get("refresh_token") or (previous.refresh if previous else None)
        expires = _number(payload.get("expires_in"), 1, 24 * 60 * 60)
        if (not isinstance(access, str) or not isinstance(refresh, str)
                or not 1 <= len(access) <= 8192 or not 1 <= len(refresh) <= 8192
                or any(char in access + refresh for char in "\r\n") or expires is None
                or str(payload.get("token_type", "Bearer")).lower() != "bearer"):
            raise ProviderError(502, "invalid_provider_data")
        return _Token(access, refresh, self.clock() + expires)

    def _sync(self, session: _Session, deadline: float, *, discover: bool = False, vehicle_id: str | None = None) -> None:
        with session.lock:
            token = session.token
            session.last_attempt_at = self.clock()
            rows = deepcopy(session.vehicles)
        if token is None:
            return
        try:
            if token.expires_at <= self.clock() + 30:
                token = self._parse_token(self.client.token("refresh_token", token.refresh, deadline), token)
                with session.lock:
                    session.token = token
            if discover or not rows:
                payload, _ = self.client.get(f"/v2.0/vehicles?limit={MAX_VEHICLES}", token.access, deadline)
                vehicle_ids = payload.get("vehicles")
                if not isinstance(vehicle_ids, list) or any(not isinstance(value, str) or not _ID_RE.fullmatch(value) for value in vehicle_ids):
                    raise ProviderError(502, "invalid_provider_data")
                vehicle_ids = list(dict.fromkeys(vehicle_ids))
                rows = {value: rows.get(value) or _new_vehicle(value, self.config.source_mode) for value in vehicle_ids[:MAX_VEHICLES]}
                paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
                with session.lock:
                    session.limited = len(vehicle_ids) >= MAX_VEHICLES or (_number(paging.get("count"), 0, 100000) or 0) > MAX_VEHICLES
            any_failure = False
            for current_id, row in rows.items():
                if vehicle_id is not None and current_id != vehicle_id:
                    continue
                row["lastAttemptAt"] = _iso(self.clock())
                failed = False
                endpoints = ("", "battery", "charge", "battery/capacity")
                if row["make"] is not None:
                    endpoints = endpoints[1:]
                for endpoint in endpoints:
                    path = f"/v2.0/vehicles/{current_id}" + ("/" + endpoint if endpoint else "")
                    try:
                        payload, headers = self.client.get(path, token.access, deadline)
                        self._apply_reading(row, endpoint, payload, headers)
                    except ProviderError as exc:
                        if exc.status == 401:
                            raise
                        if exc.status in {403, 404, 501}:
                            # Brand/model permissions and capabilities differ. A
                            # missing capability is unknown, never a made-up zero.
                            self._clear_reading(row, endpoint)
                            continue
                        failed = True
                row["error"] = "telemetry_unavailable" if failed else None
                _freshness(row, self.clock(), failed=failed)
                any_failure = any_failure or failed
            if self._active(session):
                with session.lock:
                    session.vehicles = rows
                    session.error = "provider_unavailable" if any_failure else None
        except ProviderError as exc:
            with session.lock:
                if exc.status in {400, 401}:
                    session.token = None
                    session.error = "reconnect_required"
                else:
                    session.error = "provider_unavailable"
                for row in session.vehicles.values():
                    row["error"] = session.error

    def _apply_reading(self, row: dict[str, Any], endpoint: str, payload: dict[str, Any], headers: dict[str, str]) -> None:
        now = self.clock()
        observed = _timestamp(headers.get("sc-data-age"), now)
        observation = _iso(observed) if observed is not None else None
        fetched = _iso(now)
        fields: dict[str, Any] = {}
        if not endpoint:
            if payload.get("id", row["id"]) != row["id"]:
                raise ProviderError(502, "invalid_provider_data")
            row.update(make=_text(payload.get("make")), model=_text(payload.get("model")))
            year = payload.get("year")
            row["year"] = int(year) if isinstance(year, (str, int)) and len(str(year)) == 4 and str(year).isdigit() and 1900 <= int(year) <= 2200 else None
        elif endpoint == "battery":
            fraction = _number(payload.get("percentRemaining"), 0, 1)
            # The request explicitly asks for metric. A contradictory unit header
            # invalidates distance; never present miles under a kilometre label.
            metric = headers.get("sc-unit-system", "metric").lower() == "metric"
            fields = {"batteryPercent": round(fraction * 100, 2) if fraction is not None else None,
                      "rangeKm": _number(payload.get("range"), 0, 5000) if metric else None}
        elif endpoint == "charge":
            state = {"CHARGING": "charging", "FULLY_CHARGED": "fully_charged", "NOT_CHARGING": "not_charging"}.get(_text(payload.get("state"), 32))
            if payload.get("isPluggedIn") is False:
                state = "unplugged"
            elif payload.get("isPluggedIn") is True and state is None:
                state = "plugged_in"
            fields = {"chargingState": state}
        elif endpoint == "battery/capacity":
            # Smartcar's actual capacity endpoint reports usable pack capacity in
            # kWh; nominal_capacity (trim alternatives) is deliberately never read.
            fields = {"batteryCapacityKwh": _number(payload.get("capacity"), 0.1, 1000)}
        for name, value in fields.items():
            row[name] = value
            row["fieldObservedAt"][name] = observation if value is not None else None
            row["fieldFetchedAt"][name] = fetched if value is not None else None
        row["fetchedAt"] = fetched

    @staticmethod
    def _clear_reading(row: dict[str, Any], endpoint: str) -> None:
        for name in {"battery": ("batteryPercent", "rangeKm"), "charge": ("chargingState",),
                     "battery/capacity": ("batteryCapacityKwh",)}.get(endpoint, ()):
            row[name] = None
            row["fieldObservedAt"][name] = None
            row["fieldFetchedAt"][name] = None

    def refresh(self, cookie_header: str | None, origin: str | None, vehicle_id: Any = None) -> dict[str, Any]:
        self.require_origin(origin)
        session = self._session(cookie_header)
        if session is None:
            raise VehicleError(401, "vehicle_not_connected", "Connect your electric vehicle to see its readings.")
        with session.lock:
            if session.token is None:
                raise VehicleError(401, "vehicle_not_connected", "Connect your electric vehicle again to get new readings.")
            if vehicle_id is not None and (not isinstance(vehicle_id, str) or vehicle_id not in session.vehicles):
                raise VehicleError(404, "vehicle_not_found", "That vehicle is not connected to this browser.")
        if not session.operation.acquire(blocking=False):
            raise VehicleError(409, "vehicle_connection_busy", "Your vehicle connection is already updating.")
        try:
            with session.lock:
                cached = session.last_attempt_at is not None and self.clock() < session.last_attempt_at + REFRESH_SECONDS
            if cached:
                return self._status(session)
            if not self._upstream_slots.acquire(blocking=False):
                raise VehicleError(503, "vehicle_connection_busy", "Vehicle connection is busy. Try again shortly.")
            try:
                self._sync(session, time.monotonic() + SYNC_SECONDS, vehicle_id=vehicle_id)
            finally:
                self._upstream_slots.release()
            return self._status(session)
        finally:
            session.operation.release()

    def disconnect(self, cookie_header: str | None, origin: str | None) -> tuple[dict[str, Any], str]:
        self.require_origin(origin)
        session = self._session(cookie_header)
        if session is not None:
            with self._lock:
                self._sessions.pop(session.key, None)
            with session.lock:
                session.token = None
                session.state_digest = None
                session.handoff_digest = None
                session.native_challenge = None
                session.vehicles.clear()
        return {**self._status(None), "providerRevoked": False}, self._cookie("", clear=True)
