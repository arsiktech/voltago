"""Immutable, disk-backed catalogue queries using only Python's standard library.

Raw station JSON is stored losslessly in individually compressed blobs. Metadata,
coordinates and filter fields are separate so a viewport never loads the world.
The JSON catalogue remains the source artifact; this is a derived runtime index.
"""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Any
import urllib.parse
import zlib

SCHEMA_VERSION = 1
APPLICATION_ID = 0x564F4C54


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def finite_number(value: Any) -> float | None:
    try:
        result = float(value.strip().replace(",", ".") if isinstance(value, str) else value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def indexed_fields(row: dict) -> tuple:
    """Match the raw-row prefilters used by server.overview_candidates."""
    lat, lon = finite_number(row.get("lat")), finite_number(row.get("lon"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        lat = lon = None
    currency = str(row.get("price_currency") or row.get("currency") or "").upper()
    country = str(row.get("country_code") or row.get("country") or "").upper()
    currency = currency or ("EUR" if country in {"", "ES", "ESP"} else "UNKNOWN")
    price = finite_number(row.get("price_min"))
    if row.get("is_free") is True:
        price = 0.0
    elif currency != "EUR" and price != 0:
        price = None
    return (lat, lon, price, int(bool(row.get("is_free"))), int(bool(row.get("is_cheap"))),
            int(bool(row.get("fast") or (finite_number(row.get("max_power_kw")) or 0) >= 43)),
            finite_number(row.get("recommendation_score")) or finite_number(row.get("score")) or 0)


def _distance(lat: float, lon: float, origin_lat: float, origin_lon: float) -> float:
    if lat is None or lon is None:
        return float("inf")
    delta_lat, delta_lon = math.radians(lat - origin_lat), math.radians(lon - origin_lon)
    angle = (math.sin(delta_lat / 2) ** 2 + math.cos(math.radians(lat))
             * math.cos(math.radians(origin_lat)) * math.sin(delta_lon / 2) ** 2)
    return 6371 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, angle))))


class IndexedRecords(Sequence):
    """A read-only sequence; returned dictionaries always belong to the caller."""

    def __init__(self, index: "IntelligenceIndex"):
        self.index = index

    def __len__(self) -> int:
        return self.index.count

    def __iter__(self) -> Iterator[dict]:
        return self.index.iter_bbox()

    def __getitem__(self, position: int | slice) -> dict | list:
        if isinstance(position, slice):
            start, stop, step = position.indices(len(self))
            if step == 1:
                return self.index.query(offset=start, limit=max(0, stop - start))[0]
            return [self[item] for item in range(start, stop, step)]
        if position < 0:
            position += len(self)
        if position < 0 or position >= len(self):
            raise IndexError(position)
        with self.index.connection() as connection:
            row = connection.execute("SELECT payload FROM stations WHERE seq=?", (position,)).fetchone()
        if row is None:
            raise IndexError(position)
        return self.index.decode(row[0])

    def iter_bbox(self, bbox: dict | None = None) -> Iterator[dict]:
        return self.index.iter_bbox(bbox)


class IntelligenceIndex:
    def __init__(self, path: str | Path, *, policy_sha256: str | None = None):
        # The caller must switch immutable files by atomically replacing a symlink,
        # never by updating the published SQLite file in place.
        self.path = Path(path).expanduser().resolve()
        with self.connection() as connection:
            if connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                raise ValueError("Not a Volta intelligence index")
            if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                raise ValueError("Unsupported intelligence index version")
            stored = connection.execute("SELECT value FROM metadata WHERE key='catalogue'").fetchone()
            if stored is None:
                raise ValueError("Intelligence index metadata is missing")
            self._metadata = json.loads(stored[0])
            manifest = self._metadata.get("index_manifest") or {}
            self.count = connection.execute("SELECT count(*) FROM stations").fetchone()[0]
            if self.count != self._metadata.get("total_records") or self.count != manifest.get("records"):
                raise ValueError("Intelligence index record count does not match its manifest")
            if policy_sha256 is not None and manifest.get("policy_sha256") != policy_sha256:
                raise ValueError("Intelligence index source policy differs from the runtime policy")
            stored = connection.execute("SELECT value FROM metadata WHERE key='summaries'").fetchone()
            self.summaries = json.loads(stored[0]) if stored else {}
        self.records = IndexedRecords(self)

    @contextmanager
    def connection(self):
        uri = f"file:{urllib.parse.quote(str(self.path), safe='/')}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA cache_size=-2048")
        connection.execute("PRAGMA mmap_size=0")
        connection.create_function("station_distance", 4, _distance, deterministic=True)
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def decode(payload: bytes) -> dict:
        return json.loads(zlib.decompress(payload))

    def metadata(self) -> dict:
        return deepcopy(self._metadata)

    @staticmethod
    def _where(bbox: dict | None, price_cap: float | None, fast_only: bool) -> tuple[str, list]:
        clauses, values = [], []
        if bbox is not None:
            south, north = finite_number(bbox.get("latitude_sw")), finite_number(bbox.get("latitude_ne"))
            west, east = finite_number(bbox.get("longitude_sw")), finite_number(bbox.get("longitude_ne"))
            if (None in (south, north, west, east) or not -90 <= south <= north <= 90
                    or not -180 <= west <= 180 or not -180 <= east <= 180):
                raise ValueError("Invalid intelligence query bounding box")
            clauses.append("lat BETWEEN ? AND ?")
            values.extend((south, north))
            clauses.append("lon BETWEEN ? AND ?" if west <= east else "(lon >= ? OR lon <= ?)")
            values.extend((west, east))
        if price_cap is not None:
            parsed_price = finite_number(price_cap)
            if parsed_price is None or parsed_price < 0:
                raise ValueError("Invalid intelligence query price cap")
            if parsed_price < 5.0:
                clauses.append("price IS NOT NULL AND price <= ?")
                values.append(parsed_price)
        if fast_only:
            clauses.append("fast=1")
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), values

    def iter_bbox(self, bbox: dict | None = None) -> Iterator[dict]:
        where, values = self._where(bbox, None, False)
        with self.connection() as connection:
            for row in connection.execute("SELECT payload FROM stations" + where + " ORDER BY seq", values):
                yield self.decode(row[0])

    def query(self, bbox: dict | None = None, *, price_cap: float | None = None,
              fast_only: bool = False, offset: int = 0, limit: int | None = None,
              overview: bool = False, origin: tuple[float, float] | None = None) -> tuple[list, int]:
        """Select before decompression; count is the full pre-enrichment match count."""
        if offset < 0 or (limit is not None and limit < 0):
            raise ValueError("Invalid intelligence query pagination")
        where, values = self._where(bbox, price_cap, fast_only)
        order = " ORDER BY seq"
        order_values = []
        if overview:
            order = " ORDER BY is_free DESC,is_cheap DESC,"
            if origin is not None:
                latitude, longitude = map(finite_number, origin)
                if latitude is None or longitude is None or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                    raise ValueError("Invalid intelligence query origin")
                order += "station_distance(lat,lon,?,?),"
                order_values.extend((latitude, longitude))
            order += "score DESC,identifier,seq"
        with self.connection() as connection:
            count = connection.execute("SELECT count(*) FROM stations" + where, values).fetchone()[0]
            rows = connection.execute("SELECT payload FROM stations" + where + order + " LIMIT ? OFFSET ?",
                                      values + order_values + [limit if limit is not None else -1, offset])
            return [self.decode(row[0]) for row in rows], count

    def get_by_id(self, identifier: str) -> dict | None:
        with self.connection() as connection:
            row = connection.execute("SELECT payload FROM stations JOIN identifiers USING(seq) WHERE alias=?",
                                     (str(identifier),)).fetchone()
        return self.decode(row[0]) if row else None

    def ids(self) -> set[str]:
        with self.connection() as connection:
            return {row[0] for row in connection.execute("SELECT identifier FROM stations WHERE identifier != ''")}
