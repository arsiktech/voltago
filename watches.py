"""Station availability watches (docs/spec-availability-alerts.md, VG-19/VG-20).

Stdlib-only (sqlite3 + secrets). A watch names one charger and, optionally, the
connector types a car can use. While it is active a single watcher loop re-reads
the charger's served row; when a compatible connector moves from a fresh,
known non-available status to a fresh AVAILABLE status, the watch sends one
alert and ends.

Honesty rules, enforced here:
* Only rows whose status comes from an eligible live feed with a valid, fresh
  snapshot can start a watch. Cached, stale, directory and community rows cannot.
* UNKNOWN, errors and a feed recovering from stale never count as "became free":
  a stale observation clears the baseline, and the next fresh one only re-baselines.
* One alert per watch; watches expire; a long outage ends the watch silently.
* No location history, account or vehicle data is stored. The device token is
  dropped as soon as a watch ends; ended rows are purged after a day.

Delivery goes through an injected sender. Until APNs credentials exist the server
uses `UnconfiguredSender`, which delivers nothing and says so.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent
_DB_PATH = Path(os.environ.get("VOLTA_WATCH_DB", str(ROOT / "data" / "watches.db")))

ELIGIBLE_FEEDS = ("endolla", "spirii")
DEFAULT_MINUTES = 120
MAX_MINUTES = 360
OUTAGE_SECONDS = 15 * 60
PURGE_AFTER_SECONDS = 24 * 3600
MAX_ACTIVE_PER_DEVICE = 3
MAX_ACTIVE_TOTAL = int(os.environ.get("VOLTA_WATCH_MAX_ACTIVE", "500"))
STATION_ID_MAX = 200
_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{64,200}$")
_WATCH_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

AVAILABLE = "AVAILABLE"
_NOT_A_STATE = {"", "UNKNOWN", None}

_lock = threading.Lock()


class WatchError(Exception):
    """Validation/limit error carrying an HTTP status + machine code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def configure(db_path: Path) -> None:
    """Repoint storage (tests)."""
    global _DB_PATH
    _DB_PATH = Path(db_path)


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS watches (
            id TEXT PRIMARY KEY,
            station_id TEXT NOT NULL,
            station_name TEXT,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            connector_types TEXT NOT NULL,
            device_token TEXT,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            baseline TEXT,
            last_fresh_at REAL,
            ended_at REAL,
            end_reason TEXT,
            alert TEXT,
            delivery TEXT
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS watches_active ON watches(active, device_token)")
    return conn


def _iso(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ------------------------------------------------------------------ evidence rules


def live_snapshot_is_fresh(row: Optional[Mapping[str, Any]]) -> bool:
    """True only for a row served from an eligible feed's valid, fresh snapshot."""
    if not isinstance(row, Mapping) or row.get("lead") or row.get("cache_stale"):
        return False
    live = row.get("live_status")
    return bool(isinstance(live, Mapping) and live.get("valid") is True and live.get("feed") in ELIGIBLE_FEEDS)


def ineligible_reason(row: Optional[Mapping[str, Any]]) -> Optional[str]:
    if not isinstance(row, Mapping):
        return "This charger is not in the current results."
    live = row.get("live_status")
    if not isinstance(live, Mapping) or live.get("feed") not in ELIGIBLE_FEEDS:
        return "This charger has no live status that can be watched."
    if not live_snapshot_is_fresh(row):
        return "Live status for this charger is currently unavailable or stale."
    return None


def _compatible(connector: Mapping[str, Any], types: Sequence[str], key_for: Callable[[Any], Optional[str]]) -> bool:
    if not types:
        return True
    # Never guess: a connector whose type is unknown does not match a requested type.
    return key_for(connector.get("label") or connector.get("type")) in types


def connector_states(row: Mapping[str, Any], types: Sequence[str],
                     key_for: Callable[[Any], Optional[str]]) -> Dict[str, Dict[str, Any]]:
    """Compatible connectors keyed by their stable source connector ID."""
    states: Dict[str, Dict[str, Any]] = {}
    for connector in row.get("connectors") or []:
        if not isinstance(connector, Mapping) or not connector.get("connector_id"):
            continue
        if not _compatible(connector, types, key_for):
            continue
        states[str(connector["connector_id"])] = {
            "status": str(connector.get("status") or "UNKNOWN").upper(),
            "label": connector.get("label") or connector.get("type"),
            "changed_at": connector.get("last_changed_at"),
        }
    return states


def evaluate(watch: Mapping[str, Any], row: Optional[Mapping[str, Any]], now: float,
             key_for: Callable[[Any], Optional[str]]) -> Dict[str, Any]:
    """Pure transition step. Returns {"baseline", "last_fresh_at", "end_reason", "alert"}."""
    baseline = watch.get("baseline")
    last_fresh = watch.get("last_fresh_at")
    result: Dict[str, Any] = {"baseline": baseline, "last_fresh_at": last_fresh, "end_reason": None, "alert": None}
    if now >= float(watch["expires_at"]):
        result["end_reason"] = "expired"
        return result
    if not live_snapshot_is_fresh(row):
        # Forget what we saw: a status read after an outage only re-baselines.
        result["baseline"] = None
        if now - float(last_fresh or watch["created_at"]) >= OUTAGE_SECONDS:
            result["end_reason"] = "outage"
        return result
    assert row is not None
    current = connector_states(row, watch["connector_types"], key_for)
    result["last_fresh_at"] = now
    result["baseline"] = {key: value["status"] for key, value in current.items()}
    if baseline is None:
        return result
    for key, value in sorted(current.items()):
        before = baseline.get(key)
        if value["status"] == AVAILABLE and before not in _NOT_A_STATE and before != AVAILABLE:
            result["alert"] = {
                "connector_id": key,
                "label": value["label"],
                "from_status": before,
                "observed_at": value["changed_at"] or _iso(now),
            }
            result["end_reason"] = "alerted"
            return result
    return result


# ------------------------------------------------------------------ payloads + senders


def _clock(value: Any) -> Optional[str]:
    """HH:MM in the source's own offset (Endolla publishes local time)."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    suffix = " UTC" if parsed.utcoffset() == timezone.utc.utcoffset(None) else ""
    return parsed.strftime("%H:%M") + suffix


def alert_payload(watch: Mapping[str, Any], alert: Mapping[str, Any]) -> Dict[str, Any]:
    label = alert.get("label") or "compatible"
    when = _clock(alert.get("observed_at"))
    body = f"A {label} connector showed free" + (f" at {when}" if when else "") + ". Not reserved; it may be taken."
    return {
        "aps": {"alert": {"title": watch.get("station_name") or "Watched charger", "body": body}, "sound": "default"},
        "volta": {"station_id": watch["station_id"], "watch_id": watch["id"], "observed_at": alert.get("observed_at")},
    }


class UnconfiguredSender:
    """Default until APNs credentials exist: delivers nothing and reports it."""

    configured = False

    def send(self, device_token: str, payload: Mapping[str, Any], *, expires_at: float, collapse_id: str) -> str:
        return "not_configured"


class RecordingSender:
    """Test double. `results` maps a device token to the outcome to report."""

    configured = True

    def __init__(self, results: Optional[Dict[str, str]] = None) -> None:
        self.sent: List[Dict[str, Any]] = []
        self.results = results or {}

    def send(self, device_token: str, payload: Mapping[str, Any], *, expires_at: float, collapse_id: str) -> str:
        self.sent.append({"token": device_token, "payload": dict(payload), "expires_at": expires_at, "collapse_id": collapse_id})
        return self.results.get(device_token, "sent")


# ------------------------------------------------------------------ store


def _row_to_watch(record: sqlite3.Row) -> Dict[str, Any]:
    watch = dict(record)
    watch["connector_types"] = json.loads(watch["connector_types"] or "[]")
    watch["baseline"] = json.loads(watch["baseline"]) if watch.get("baseline") else None
    watch["alert"] = json.loads(watch["alert"]) if watch.get("alert") else None
    return watch


def public_view(watch: Mapping[str, Any]) -> Dict[str, Any]:
    """What the app sees. Never includes the device token."""
    return {
        "id": watch["id"],
        "station_id": watch["station_id"],
        "station_name": watch.get("station_name"),
        "connector_types": list(watch.get("connector_types") or []),
        "active": bool(watch.get("active")),
        "created_at": _iso(watch.get("created_at")),
        "expires_at": _iso(watch.get("expires_at")),
        "last_fresh_at": _iso(watch.get("last_fresh_at")),
        "ended_at": _iso(watch.get("ended_at")),
        "end_reason": watch.get("end_reason"),
        "alert": watch.get("alert"),
        "delivery": watch.get("delivery"),
        "note": "A watch never reserves a connector. Availability can change before you arrive.",
    }


def _clean_types(value: Any, allowed: Iterable[str]) -> List[str]:
    if value in (None, [], ""):
        return []
    if not isinstance(value, list) or len(value) > 10:
        raise WatchError(400, "bad_connector_types", "connector_types must be a list of connector type names.")
    allowed_set = set(allowed)
    cleaned = sorted({str(item) for item in value})
    unknown = [item for item in cleaned if item not in allowed_set]
    if unknown:
        raise WatchError(400, "bad_connector_types", f"Unknown connector types: {', '.join(unknown)}.")
    return cleaned


def create_watch(body: Mapping[str, Any], *, lookup: Callable[[str, float, float], Optional[Dict[str, Any]]],
                 key_for: Callable[[Any], Optional[str]], allowed_types: Iterable[str],
                 now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    station_id = body.get("station_id")
    if not isinstance(station_id, str) or not station_id.strip() or len(station_id) > STATION_ID_MAX:
        raise WatchError(400, "bad_station_id", "station_id is required.")
    token = body.get("device_token")
    if not isinstance(token, str) or not _TOKEN_RE.match(token):
        raise WatchError(400, "bad_device_token", "device_token must be the hexadecimal APNs token.")
    try:
        lat, lon = float(body.get("lat")), float(body.get("lon"))
    except (TypeError, ValueError):
        raise WatchError(400, "bad_coordinates", "lat and lon of the charger are required.")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise WatchError(400, "bad_coordinates", "lat and lon are out of range.")
    minutes = body.get("duration_minutes", DEFAULT_MINUTES)
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 10 <= minutes <= MAX_MINUTES:
        raise WatchError(400, "bad_duration", f"duration_minutes must be an integer from 10 to {MAX_MINUTES}.")
    types = _clean_types(body.get("connector_types"), allowed_types)

    row = lookup(station_id, lat, lon)
    reason = ineligible_reason(row)
    if reason:
        raise WatchError(409, "not_watchable", reason)
    assert row is not None
    states = connector_states(row, types, key_for)
    if not states:
        raise WatchError(409, "no_compatible_connector", "No connector with live status matches the requested types.")

    with _lock:
        conn = _connect()
        try:
            purge(conn, now)
            active_device = conn.execute(
                "SELECT COUNT(*) FROM watches WHERE active = 1 AND device_token = ?", (token,)).fetchone()[0]
            if active_device >= MAX_ACTIVE_PER_DEVICE:
                raise WatchError(429, "too_many_watches", f"At most {MAX_ACTIVE_PER_DEVICE} active watches per device.")
            active_total = conn.execute("SELECT COUNT(*) FROM watches WHERE active = 1").fetchone()[0]
            if active_total >= MAX_ACTIVE_TOTAL:
                raise WatchError(503, "watch_capacity", "Watch capacity is full; try again later.")
            watch_id = secrets.token_urlsafe(18)
            baseline = {key: value["status"] for key, value in states.items()}
            conn.execute(
                """INSERT INTO watches (id, station_id, station_name, lat, lon, connector_types, device_token,
                   created_at, expires_at, active, baseline, last_fresh_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (watch_id, station_id, str(row.get("name") or "")[:120] or None, lat, lon, json.dumps(types), token,
                 now, now + minutes * 60, json.dumps(baseline), now),
            )
            conn.commit()
            record = conn.execute("SELECT * FROM watches WHERE id = ?", (watch_id,)).fetchone()
        finally:
            conn.close()
    view = public_view(_row_to_watch(record))
    view["free_now"] = sorted(key for key, value in states.items() if value["status"] == AVAILABLE)
    return view


def get_watch(watch_id: str) -> Dict[str, Any]:
    if not isinstance(watch_id, str) or not _WATCH_ID_RE.match(watch_id):
        raise WatchError(404, "not_found", "No such watch.")
    with _lock:
        conn = _connect()
        try:
            record = conn.execute("SELECT * FROM watches WHERE id = ?", (watch_id,)).fetchone()
        finally:
            conn.close()
    if record is None:
        raise WatchError(404, "not_found", "No such watch.")
    return public_view(_row_to_watch(record))


def _end(conn: sqlite3.Connection, watch_id: str, reason: str, now: float, *,
         alert: Optional[Mapping[str, Any]] = None, delivery: Optional[str] = None) -> None:
    conn.execute(
        """UPDATE watches SET active = 0, device_token = NULL, ended_at = ?, end_reason = ?,
           alert = COALESCE(?, alert), delivery = COALESCE(?, delivery) WHERE id = ? AND active = 1""",
        (now, reason, json.dumps(alert) if alert else None, delivery, watch_id),
    )


def cancel_watch(watch_id: str, now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    get_watch(watch_id)
    with _lock:
        conn = _connect()
        try:
            _end(conn, watch_id, "cancelled", now)
            conn.commit()
        finally:
            conn.close()
    return get_watch(watch_id)


def purge(conn: sqlite3.Connection, now: float) -> None:
    conn.execute("DELETE FROM watches WHERE active = 0 AND ended_at < ?", (now - PURGE_AFTER_SECONDS,))


def active_watches() -> List[Dict[str, Any]]:
    with _lock:
        conn = _connect()
        try:
            return [_row_to_watch(r) for r in conn.execute("SELECT * FROM watches WHERE active = 1 ORDER BY created_at")]
        finally:
            conn.close()


def has_active_watches() -> bool:
    if not _DB_PATH.exists():
        return False
    with _lock:
        conn = _connect()
        try:
            return conn.execute("SELECT 1 FROM watches WHERE active = 1 LIMIT 1").fetchone() is not None
        finally:
            conn.close()


# ------------------------------------------------------------------ watcher


def run_once(*, lookup: Callable[[str, float, float], Optional[Dict[str, Any]]], sender: Any,
             key_for: Callable[[Any], Optional[str]], now: Optional[float] = None) -> Dict[str, int]:
    """One pass over every active watch. Returns counts for logging and tests."""
    now = time.time() if now is None else now
    counts = {"checked": 0, "alerted": 0, "ended": 0}
    rows: Dict[Tuple[str, float, float], Optional[Dict[str, Any]]] = {}
    for watch in active_watches():
        counts["checked"] += 1
        key = (watch["station_id"], watch["lat"], watch["lon"])
        if key not in rows:
            try:
                rows[key] = lookup(*key)
            except Exception:
                rows[key] = None  # a failed read is an outage observation, never a transition
        step = evaluate(watch, rows[key], now, key_for)
        delivery = None
        reason = step["end_reason"]
        if step["alert"]:
            outcome = sender.send(watch["device_token"], alert_payload(watch, step["alert"]),
                                  expires_at=float(watch["expires_at"]), collapse_id=watch["id"])
            delivery = outcome
            if outcome == "invalid_token":
                reason = "invalid_token"
            counts["alerted"] += 1
        with _lock:
            conn = _connect()
            try:
                if reason:
                    _end(conn, watch["id"], reason, now, alert=step["alert"], delivery=delivery)
                    counts["ended"] += 1
                else:
                    conn.execute(
                        "UPDATE watches SET baseline = ?, last_fresh_at = ? WHERE id = ? AND active = 1",
                        (json.dumps(step["baseline"]) if step["baseline"] is not None else None,
                         step["last_fresh_at"], watch["id"]),
                    )
                conn.commit()
            finally:
                conn.close()
    return counts


class Watcher:
    """Background loop. Idle (no upstream reads) whenever no watch is active."""

    def __init__(self, *, lookup: Callable[[str, float, float], Optional[Dict[str, Any]]], sender: Any,
                 key_for: Callable[[Any], Optional[str]], interval: float = 30.0,
                 log: Callable[[str], None] = print) -> None:
        self.lookup, self.sender, self.key_for = lookup, sender, key_for
        self.interval, self.log = interval, log
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="volta-watcher", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                if has_active_watches():
                    counts = run_once(lookup=self.lookup, sender=self.sender, key_for=self.key_for)
                    if counts["alerted"] or counts["ended"]:
                        self.log(f"watcher: {counts}")
            except Exception as exc:  # keep the loop alive; the next pass retries
                self.log(f"watcher error: {type(exc).__name__}")
