"""Community evidence store for Volta (plan 013, slice B).

Stdlib-only (sqlite3 + hashlib + secrets). Holds user check-ins ("did charging
actually work?"), charger photos, and suggested new stations. Community data is
its own evidence tier: it may downgrade or annotate a decision, never upgrade
one — `apply_community_evidence` enforces that, and nothing here ever touches
`verification.live_verified`.

Storage: SQLite at data/community.db (WAL) + photo files under
data/community/photos/. Both are gitignored — user content never enters git;
Syncthing carries them between owner machines.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parent
_DB_PATH = Path(os.environ.get("VOLTA_COMMUNITY_DB", str(ROOT / "data" / "community.db")))
_PHOTOS_DIR = Path(os.environ.get("VOLTA_COMMUNITY_PHOTOS", str(ROOT / "data" / "community" / "photos")))

VERDICTS = ("charged", "failed", "occupied", "offline", "blocked")
_SUCCESS = {"charged"}
_FAILURE = {"failed", "offline", "blocked"}  # "occupied" is neutral: the charger works, a bay didn't.

NOTE_MAX = 280
NAME_MAX = 120
STATION_ID_MAX = 128
# Spain envelope incl. Canarias/Balears — suggestions outside it are rejected.
SPAIN_BBOX = (27.0, -18.5, 44.5, 5.0)

PHOTO_MAX_BYTES = int(os.environ.get("VOLTA_PHOTO_MAX_BYTES", str(4 * 1024 * 1024)))
CHECKIN_DAILY_CAP = int(os.environ.get("VOLTA_CHECKIN_DAILY_CAP", "20"))
PHOTO_DAILY_CAP = int(os.environ.get("VOLTA_PHOTO_DAILY_CAP", "10"))
SUGGESTION_DAILY_CAP = int(os.environ.get("VOLTA_SUGGESTION_DAILY_CAP", "5"))
# A successful check-in counts as "community verified" for this long.
FRESH_DAYS = int(os.environ.get("VOLTA_COMMUNITY_FRESH_DAYS", "60"))

# Fail-closed downgrade policy: this many failure-class check-ins within the
# window, with no success after them, drop a "go" decision to "check".
DOWNGRADE_MIN_FAILURES = 2
DOWNGRADE_WINDOW_DAYS = 14

_SCHEMA_VERSION = 1
_SUMMARY_TTL_SECONDS = 30.0

_lock = threading.Lock()
_summary_cache: Dict[str, Any] = {"ts": 0.0, "data": None, "gen": 0}


class CommunityError(Exception):
    """Validation/limit error carrying an HTTP status + machine code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def configure(db_path: Optional[Path] = None, photos_dir: Optional[Path] = None) -> None:
    """Repoint storage (tests). Resets the summary cache."""
    global _DB_PATH, _PHOTOS_DIR
    if db_path is not None:
        _DB_PATH = Path(db_path)
    if photos_dir is not None:
        _PHOTOS_DIR = Path(photos_dir)
    with _lock:
        _summary_cache["ts"] = 0.0
        _summary_cache["data"] = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= _SCHEMA_VERSION:
        return
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS checkins (
            id INTEGER PRIMARY KEY,
            station_id TEXT NOT NULL,
            verdict TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            client TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
        );
        CREATE INDEX IF NOT EXISTS idx_checkins_station ON checkins(station_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_checkins_client ON checkins(client, created_at);
        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY,
            station_id TEXT NOT NULL,
            ext TEXT NOT NULL,
            size INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            client TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
        );
        CREATE INDEX IF NOT EXISTS idx_photos_station ON photos(station_id, created_at);
        CREATE TABLE IF NOT EXISTS suggestions (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            connectors TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            client TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        );
        """
    )
    conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
    conn.commit()


def _salt(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM meta WHERE key='client_salt'").fetchone()
    if row:
        return row["value"]
    salt = secrets.token_hex(16)
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('client_salt', ?)", (salt,))
    conn.commit()
    row = conn.execute("SELECT value FROM meta WHERE key='client_salt'").fetchone()
    return row["value"]


def client_token(conn: sqlite3.Connection, client_ip: str) -> str:
    """Stable pseudonymous per-client key. The raw IP is never stored."""
    digest = hashlib.sha256((_salt(conn) + (client_ip or "unknown")).encode("utf-8")).hexdigest()
    return digest[:16]


def _clean_text(value: Any, max_len: int) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def _require_station_id(station_id: Any) -> str:
    sid = _clean_text(station_id, STATION_ID_MAX)
    if not sid:
        raise CommunityError(400, "station_id_required", "station_id is required.")
    return sid


def _enforce_daily_cap(conn: sqlite3.Connection, table: str, client: str, cap: int) -> None:
    """Keep the cap check and the caller's insert in one write transaction.

    The caller commits after inserting, or closes the connection on failure.
    Reserving the writer now prevents simultaneous requests passing the same cap.
    """
    conn.execute("BEGIN IMMEDIATE")
    day_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM {table} WHERE client=? AND created_at>=?",
        (client, day_start),
    ).fetchone()
    if row["n"] >= cap:
        raise CommunityError(429, "daily_cap", "Daily community limit reached for this client; try again tomorrow.")


def _invalidate_summaries() -> None:
    with _lock:
        _summary_cache["ts"] = 0.0
        _summary_cache["data"] = None
        _summary_cache["gen"] += 1


def generation() -> int:
    """Monotonic write counter — lets response caches invalidate on community writes."""
    with _lock:
        return _summary_cache["gen"]


def record_checkin(
    station_id: Any,
    verdict: Any,
    note: Any = "",
    *,
    client_ip: str = "",
    known_station: Optional[Callable[[str], bool]] = None,
) -> Dict[str, Any]:
    sid = _require_station_id(station_id)
    verdict_clean = _clean_text(verdict, 32).lower()
    if verdict_clean not in VERDICTS:
        raise CommunityError(400, "bad_verdict", f"verdict must be one of {', '.join(VERDICTS)}.")
    if known_station is not None and not known_station(sid):
        raise CommunityError(404, "unknown_station", "Unknown station id; use a station id from the API, or suggest a new point.")
    conn = _connect()
    try:
        client = client_token(conn, client_ip)
        _enforce_daily_cap(conn, "checkins", client, CHECKIN_DAILY_CAP)
        created = now_iso()
        cursor = conn.execute(
            "INSERT INTO checkins(station_id, verdict, note, created_at, client) VALUES(?,?,?,?,?)",
            (sid, verdict_clean, _clean_text(note, NOTE_MAX), created, client),
        )
        conn.commit()
        _invalidate_summaries()
        return {"id": cursor.lastrowid, "station_id": sid, "verdict": verdict_clean, "created_at": created}
    finally:
        conn.close()


_PHOTO_MAGIC: Tuple[Tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
)


def _photo_ext(data: bytes) -> Optional[str]:
    for magic, ext in _PHOTO_MAGIC:
        if data.startswith(magic):
            return ext
    if len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def save_photo(
    station_id: Any,
    data: bytes,
    *,
    client_ip: str = "",
    known_station: Optional[Callable[[str], bool]] = None,
) -> Dict[str, Any]:
    sid = _require_station_id(station_id)
    if known_station is not None and not known_station(sid):
        raise CommunityError(404, "unknown_station", "Unknown station id; use a station id from the API.")
    if not data:
        raise CommunityError(400, "empty_photo", "Empty photo body.")
    if len(data) > PHOTO_MAX_BYTES:
        raise CommunityError(413, "photo_too_large", f"Photo exceeds {PHOTO_MAX_BYTES} bytes.")
    ext = _photo_ext(data)
    if ext is None:
        raise CommunityError(415, "unsupported_photo", "Only JPEG, PNG, or WebP photos are accepted.")
    conn = _connect()
    try:
        client = client_token(conn, client_ip)
        _enforce_daily_cap(conn, "photos", client, PHOTO_DAILY_CAP)
        created = now_iso()
        cursor = conn.execute(
            "INSERT INTO photos(station_id, ext, size, created_at, client) VALUES(?,?,?,?,?)",
            (sid, ext, len(data), created, client),
        )
        photo_id = cursor.lastrowid
        _PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
        (_PHOTOS_DIR / f"{photo_id}.{ext}").write_bytes(data)
        conn.commit()
        _invalidate_summaries()
        return {"id": photo_id, "station_id": sid, "url": photo_url(photo_id, ext), "created_at": created}
    finally:
        conn.close()


def photo_url(photo_id: int, ext: str) -> str:
    return f"/api/community/photo/{photo_id}.{ext}"


def photo_file(name: str) -> Optional[Tuple[Path, str]]:
    """Resolve `{id}.{ext}` to (path, mime) for ACTIVE photos only."""
    match = re.fullmatch(r"(\d{1,12})\.(jpg|png|webp)", name or "")
    if not match:
        return None
    photo_id, ext = int(match.group(1)), match.group(2)
    conn = _connect()
    try:
        row = conn.execute("SELECT ext, status FROM photos WHERE id=?", (photo_id,)).fetchone()
    finally:
        conn.close()
    if not row or row["status"] != "active" or row["ext"] != ext:
        return None
    path = _PHOTOS_DIR / f"{photo_id}.{ext}"
    if not path.is_file():
        return None
    mime = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[ext]
    return path, mime


def record_suggestion(
    name: Any,
    lat: Any,
    lon: Any,
    note: Any = "",
    connectors: Any = "",
    *,
    client_ip: str = "",
) -> Dict[str, Any]:
    clean_name = _clean_text(name, NAME_MAX)
    if len(clean_name) < 3:
        raise CommunityError(400, "bad_name", "Give the point a name of at least 3 characters.")
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError):
        raise CommunityError(400, "bad_coordinates", "lat and lon must be numbers.")
    south, west, north, east = SPAIN_BBOX
    if not (south <= lat_f <= north and west <= lon_f <= east):
        raise CommunityError(400, "outside_spain", "Volta currently covers Spain only; coordinates fall outside it.")
    conn = _connect()
    try:
        client = client_token(conn, client_ip)
        _enforce_daily_cap(conn, "suggestions", client, SUGGESTION_DAILY_CAP)
        created = now_iso()
        cursor = conn.execute(
            "INSERT INTO suggestions(name, lat, lon, note, connectors, created_at, client) VALUES(?,?,?,?,?,?,?)",
            (clean_name, lat_f, lon_f, _clean_text(note, NOTE_MAX), _clean_text(connectors, NOTE_MAX), created, client),
        )
        conn.commit()
        _invalidate_summaries()
        return {"id": cursor.lastrowid, "name": clean_name, "status": "pending", "created_at": created}
    finally:
        conn.close()


def moderate(kind: str, item_id: Any, action: str) -> Dict[str, Any]:
    """Owner moderation (server gates this behind VOLTA_ADMIN_TOKEN)."""
    try:
        item_int = int(item_id)
    except (TypeError, ValueError):
        raise CommunityError(400, "bad_id", "id must be an integer.")
    plans = {
        ("suggestion", "accept"): ("suggestions", "accepted"),
        ("suggestion", "reject"): ("suggestions", "rejected"),
        ("photo", "remove"): ("photos", "removed"),
        ("checkin", "remove"): ("checkins", "removed"),
    }
    plan = plans.get((kind, action))
    if plan is None:
        raise CommunityError(400, "bad_action", "Unsupported kind/action pair.")
    table, new_status = plan
    conn = _connect()
    try:
        cursor = conn.execute(f"UPDATE {table} SET status=? WHERE id=?", (new_status, item_int))
        conn.commit()
        if cursor.rowcount == 0:
            raise CommunityError(404, "not_found", f"No such {kind}.")
        _invalidate_summaries()
        return {"kind": kind, "id": item_int, "status": new_status}
    finally:
        conn.close()


def _summaries_uncached() -> Dict[str, Any]:
    conn = _connect()
    try:
        stations: Dict[str, Dict[str, Any]] = {}

        def entry(sid: str) -> Dict[str, Any]:
            if sid not in stations:
                counts: Dict[str, Any] = {v: 0 for v in VERDICTS}
                counts.update({"total": 0, "last_success_at": None, "last_failure_at": None})
                stations[sid] = {
                    "checkins": counts,
                    "photos": [],
                    "recent_success": False,
                    "recent_failure": False,
                }
            return stations[sid]

        for row in conn.execute(
            "SELECT station_id, verdict, COUNT(*) AS n, MAX(created_at) AS latest"
            " FROM checkins WHERE status='active' GROUP BY station_id, verdict"
        ):
            block = entry(row["station_id"])["checkins"]
            block[row["verdict"]] = row["n"]
            block["total"] += row["n"]
            if row["verdict"] in _SUCCESS and (block["last_success_at"] or "") < row["latest"]:
                block["last_success_at"] = row["latest"]
            if row["verdict"] in _FAILURE and (block["last_failure_at"] or "") < row["latest"]:
                block["last_failure_at"] = row["latest"]

        for row in conn.execute(
            "SELECT id, station_id, ext, created_at FROM photos WHERE status='active' ORDER BY created_at DESC"
        ):
            photos = entry(row["station_id"])["photos"]
            if len(photos) < 6:
                photos.append({"id": row["id"], "url": photo_url(row["id"], row["ext"]), "created_at": row["created_at"]})

        now = datetime.now(timezone.utc)
        fresh_cut = now - timedelta(days=FRESH_DAYS)
        fail_cut = now - timedelta(days=DOWNGRADE_WINDOW_DAYS)
        for sid, summary in stations.items():
            block = summary["checkins"]
            last_success = _parse_iso(block["last_success_at"])
            last_failure = _parse_iso(block["last_failure_at"])
            summary["recent_success"] = bool(last_success and last_success >= fresh_cut)
            recent_failures = conn.execute(
                "SELECT COUNT(*) AS n FROM checkins WHERE status='active' AND station_id=?"
                " AND verdict IN ('failed','offline','blocked') AND created_at>=?"
                " AND (? IS NULL OR created_at>?)",
                (sid, fail_cut.isoformat(timespec="seconds"), block["last_success_at"], block["last_success_at"]),
            ).fetchone()["n"]
            failure_after_success = bool(last_failure and (last_success is None or last_failure > last_success))
            summary["recent_failure"] = bool(recent_failures and failure_after_success and last_failure and last_failure >= fail_cut)
            summary["recent_failure_count"] = recent_failures if summary["recent_failure"] else 0

        pending = conn.execute("SELECT COUNT(*) AS n FROM suggestions WHERE status='pending'").fetchone()["n"]
        accepted_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT id, name, lat, lon, note, connectors, created_at FROM suggestions WHERE status='accepted' ORDER BY id"
            )
        ]
        totals = {
            "checkins": conn.execute("SELECT COUNT(*) AS n FROM checkins WHERE status='active'").fetchone()["n"],
            "photos": conn.execute("SELECT COUNT(*) AS n FROM photos WHERE status='active'").fetchone()["n"],
            "suggestions_pending": pending,
            "suggestions_accepted": len(accepted_rows),
        }
        return {"stations": stations, "totals": totals, "accepted_suggestions": accepted_rows}
    finally:
        conn.close()


def summaries() -> Dict[str, Any]:
    """All community state, cached ~30 s (invalidated on every write)."""
    now = time.monotonic()
    with _lock:
        if _summary_cache["data"] is not None and now - _summary_cache["ts"] < _SUMMARY_TTL_SECONDS:
            return _summary_cache["data"]
        read_generation = _summary_cache["gen"]
    data = _summaries_uncached()
    with _lock:
        # A write may have invalidated the cache while SQLite was being read.
        # Do not make that earlier snapshot reusable by subsequent requests.
        if _summary_cache["gen"] == read_generation:
            _summary_cache["ts"] = time.monotonic()
            _summary_cache["data"] = data
    return data


def station_summary(station_id: str) -> Optional[Dict[str, Any]]:
    return summaries()["stations"].get(station_id)


def apply_community_evidence(item: Dict[str, Any], summary: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Attach the community block; fail-closed decision adjustment ONLY.

    Honesty invariants enforced here:
    - never touches verification.live_verified (community is not live authority);
    - never upgrades a decision (a success annotates, a failure pattern
      downgrades "go" to "check").
    """
    if not summary:
        return item
    item["community"] = summary
    decision = item.get("decision") or {}
    if (
        summary.get("recent_failure")
        and summary.get("recent_failure_count", 0) >= DOWNGRADE_MIN_FAILURES
        and decision.get("level") == "go"
    ):
        item["decision"] = {
            "level": "check",
            "label": "Check first",
            "reason": (
                f"Drivers reported {summary['recent_failure_count']} failed charge attempts in the last "
                f"{DOWNGRADE_WINDOW_DAYS} days with no success since; verify in the provider app before routing."
            ),
        }
        item["community_caution"] = True
    elif summary.get("recent_failure"):
        item["community_caution"] = True
    return item


def free_verification_level(item: Dict[str, Any], summary: Optional[Dict[str, Any]]) -> str:
    """Presentation ladder for FREE rows: live > community > corroborated > reported."""
    verification = item.get("verification") or {}
    if verification.get("live_verified"):
        return "live"
    if summary and summary.get("recent_success"):
        return "community"
    corroboration = item.get("corroboration") or {}
    if (corroboration.get("independent_sources") or 0) >= 2:
        return "corroborated"
    return "reported"


def stats() -> Dict[str, Any]:
    return summaries()["totals"]
