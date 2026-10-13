#!/usr/bin/env python3
"""OpenChargeMap `ocm-export` snapshot reader (Python standard library only).

Pure parsing and gating helpers for Volta's OpenChargeMap intake. Nothing here
opens a socket: the caller downloads a commit-pinned GitHub archive with a plain
GET through ``source_transport.policy_urlopen`` and hands the file to
:func:`read_export_archive`.

Safety rules (all fail closed with :class:`OcmExportError`):

* bounded gzip reads feed ``tarfile`` in ``r|`` mode and are never extracted;
* only regular files and directories under ``ocm-export-<sha>/`` are accepted --
  symlinks, hardlinks, devices, absolute paths, ``..`` components and a wrong
  top-level directory reject the whole snapshot;
* only ``data/referencedata.json`` and ``data/ES/OCM-<id>.json`` are ever read;
* member count, per-file size, total size, wall-clock time and the share of
  malformed rows are bounded;
* each licensed row needs an explicit CC BY 4.0 or CC0 grant on its DataProvider
  in ``referencedata.json``. Missing, null, ambiguous (``General public data``),
  ShareAlike or NonCommercial grants exclude the row.

Dates: ``DateLastVerified`` / ``DateLastStatusUpdate`` are contributor
timestamps. The archive commit date is a bulk-export date and the download time
is a lookup time; none of them is a station observation.
"""
from __future__ import annotations

import json
import gzip
import re
import tarfile
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

OCM_EXPORT_REPO = "openchargemap/ocm-export"
OCM_EXPORT_COMMIT_API_URL = f"https://api.github.com/repos/{OCM_EXPORT_REPO}/commits/main"
OCM_EXPORT_COUNTRY_DIR = "ES"
OCM_EXPORT_COUNTRY_ISO = "ES"

# Legacy `ocm-data` feed (deprecated). Pinned to the last upstream commit so the
# historical snapshot is immutable and its date is a fact, not a fetch time.
OCM_LEGACY_REPO = "openchargemap/ocm-data"
OCM_LEGACY_SHA = "0a033bd404bf28f72dd1c252d9ca3e6dc11721a4"
OCM_LEGACY_DATE = "2021-08-13T12:15:47Z"
OCM_LEGACY_URL = f"https://raw.githubusercontent.com/{OCM_LEGACY_REPO}/{OCM_LEGACY_SHA}/poi.json.gz"

# Hard bounds, independent of the caller's own configuration.
MAX_ARCHIVE_DOWNLOAD_BYTES = 160 * 1024 * 1024  # current archive is ~45 MB
MAX_LEGACY_DOWNLOAD_BYTES = 80 * 1024 * 1024  # legacy gz is ~30 MB
MAX_ARCHIVE_MEMBERS = 600_000  # current archive has ~267k
MAX_ARCHIVE_TOTAL_BYTES = 1024 * 1024 * 1024  # all decompressed bytes, including tar headers/padding
MAX_POI_FILE_BYTES = 1024 * 1024  # largest ES file today is ~16 KB
MAX_REFERENCE_BYTES = 4 * 1024 * 1024  # referencedata.json is ~0.6 MB
MAX_READ_SECONDS = 240.0
MIN_COUNTRY_FILES = 10_000  # ES has ~19.9k; far fewer means a partial snapshot
MAX_MALFORMED_FRACTION = 0.005
CONTRIBUTOR_LICENSE_CHANGE_URL = "https://community.openchargemap.org/t/announcing-our-new-simpler-data-license-for-ocm-data-cc-by-4-0-international/565"
LICENSE_URLS = {"CC-BY-4.0": "https://creativecommons.org/licenses/by/4.0/",
                "CC0": "https://creativecommons.org/publicdomain/zero/1.0/"}

_SHA_RE = re.compile(r"[0-9a-f]{40}")
_POI_NAME_RE = re.compile(r"data/ES/OCM-(\d{1,10})\.json")

# OCM StatusTypeIDs (referencedata StatusTypes).
STATUS_NOT_OPERATIONAL = 100
STATUS_PLANNED = 150
STATUS_REMOVED_DECOMMISSIONED = 200
STATUS_REMOVED_DUPLICATE = 210
STATUS_TEMPORARILY_UNAVAILABLE = 30
# Never usable now: a new row with one of these is omitted; a row Volta already
# knows keeps its identity but is marked source-reported unavailable.
NON_LIVE_STATUS_IDS = frozenset({
    STATUS_NOT_OPERATIONAL, STATUS_PLANNED, STATUS_REMOVED_DECOMMISSIONED, STATUS_REMOVED_DUPLICATE,
})
# Listed, but reported unavailable by the source.
UNAVAILABLE_STATUS_IDS = NON_LIVE_STATUS_IDS | {STATUS_TEMPORARILY_UNAVAILABLE}


class OcmExportError(RuntimeError):
    """The OCM snapshot is unsafe, partial, unlicensed or otherwise unusable."""


def parse_commit_manifest(body: bytes) -> Tuple[str, str]:
    """Return ``(sha, commit_date)`` from GitHub's commit JSON, or fail closed."""
    try:
        payload = json.loads(body.decode("utf-8"))
        sha = str(payload["sha"])
        date = str(payload["commit"]["committer"]["date"])
    except (UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        raise OcmExportError("OCM commit manifest is malformed") from exc
    if not _SHA_RE.fullmatch(sha):
        raise OcmExportError("OCM commit manifest SHA is not a 40-hex commit id")
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", date):
        raise OcmExportError("OCM commit manifest date is not a UTC timestamp")
    return sha, date


def archive_url(sha: str) -> str:
    if not _SHA_RE.fullmatch(sha):
        raise OcmExportError("OCM archive must be pinned to a 40-hex commit SHA")
    return f"https://codeload.github.com/{OCM_EXPORT_REPO}/tar.gz/{sha}"


def license_class(text: Any, *, open_licensed: Any = True) -> Optional[str]:
    """Classify a DataProvider license: ``CC-BY-4.0``, ``CC0`` or ``None`` (not allowed)."""
    if open_licensed is not True or not isinstance(text, str) or not text.strip():
        return None
    low = re.sub(r"\s+", " ", text.lower())
    if re.search(r"share-?\s?alike|non-?\s?commercial|\bby-?(?:nc|sa)\b|\bno-?derivs?\b|\bnd\b", low):
        return None
    if re.search(r"\bcc[ -]?0\b|creative commons zero|\bcc0\b", low):
        return "CC0"
    if re.search(r"creative commons attribution 4\.0|\bcc[ -]?by[ -]?4\.0\b", low):
        return "CC-BY-4.0"
    return None


@dataclass
class ReferenceData:
    countries: Dict[int, Dict[str, Any]]
    connection_types: Dict[int, Dict[str, Any]]
    status_types: Dict[int, Dict[str, Any]]
    usage_types: Dict[int, Dict[str, Any]]
    operators: Dict[int, Dict[str, Any]]
    charger_types: Dict[int, Dict[str, Any]]
    current_types: Dict[int, Dict[str, Any]]
    submission_statuses: Dict[int, Dict[str, Any]]
    data_providers: Dict[int, Dict[str, Any]]
    es_country_id: int

    def provider_license(self, provider_id: Any) -> Tuple[Optional[str], str, str]:
        """``(class | None, provider title, license text)``; unknown providers fail closed."""
        provider = _lookup(self.data_providers, provider_id)
        if not provider:
            return None, "", ""
        text = provider.get("License")
        return (
            license_class(text, open_licensed=provider.get("IsOpenDataLicensed")),
            str(provider.get("Title") or ""),
            str(text or ""),
        )


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        return int(value.strip())
    return None


def _lookup(table: Dict[int, Dict[str, Any]], value: Any) -> Optional[Dict[str, Any]]:
    key = _int_or_none(value)
    return table.get(key) if key is not None else None


def _by_id(rows: Any, name: str) -> Dict[int, Dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise OcmExportError(f"referencedata.{name} is missing or empty")
    out: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        key = _int_or_none(row.get("ID")) if isinstance(row, dict) else None
        if key is None or key in out:
            raise OcmExportError(f"referencedata.{name} has a malformed or duplicate ID")
        if name != "Countries" and not isinstance(row.get("Title"), str):
            raise OcmExportError(f"referencedata.{name} has a malformed title")
        for field_name in ("IsLive", "IsOperational", "IsOpenDataLicensed", "IsPrivateIndividual"):
            if row.get(field_name) is not None and not isinstance(row[field_name], bool):
                raise OcmExportError(f"referencedata.{name} has a malformed {field_name}")
        if name == "SubmissionStatusTypes" and not isinstance(row.get("IsLive"), bool):
            raise OcmExportError("submission reference lacks a publication decision")
        if name == "DataProviders" and row.get("License") is not None and not isinstance(row["License"], str):
            raise OcmExportError("provider reference has a malformed license")
        out[key] = row
    if not out:
        raise OcmExportError(f"referencedata.{name} has no identifiable rows")
    return out


def build_reference(payload: Any) -> ReferenceData:
    if not isinstance(payload, dict):
        raise OcmExportError("referencedata.json is not an object")
    countries = _by_id(payload.get("Countries"), "Countries")
    es_ids = [cid for cid, row in countries.items() if str(row.get("ISOCode") or "").upper() == OCM_EXPORT_COUNTRY_ISO]
    if len(es_ids) != 1:
        raise OcmExportError("referencedata.Countries must contain exactly one ES row")
    return ReferenceData(
        countries=countries,
        connection_types=_by_id(payload.get("ConnectionTypes"), "ConnectionTypes"),
        status_types=_by_id(payload.get("StatusTypes"), "StatusTypes"),
        usage_types=_by_id(payload.get("UsageTypes"), "UsageTypes"),
        operators=_by_id(payload.get("Operators"), "Operators"),
        charger_types=_by_id(payload.get("ChargerTypes"), "ChargerTypes"),
        current_types=_by_id(payload.get("CurrentTypes"), "CurrentTypes"),
        submission_statuses=_by_id(payload.get("SubmissionStatusTypes"), "SubmissionStatusTypes"),
        data_providers=_by_id(payload.get("DataProviders"), "DataProviders"),
        es_country_id=es_ids[0],
    )


@dataclass
class ExportSnapshot:
    commit_sha: str
    reference: ReferenceData
    pois: Dict[int, Dict[str, Any]]  # POI id (== filename == JSON ID) -> compact record
    stats: Dict[str, int] = field(default_factory=dict)
    quarantined_ids: Set[int] = field(default_factory=set)


def _check_member_name(name: str) -> None:
    parts = name.split("/")
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or "\x00" in name
        or any(part in {"..", "."} for part in parts if part)
        or re.match(r"^[A-Za-z]:", name)
    ):
        raise OcmExportError("OCM archive contains an unsafe member path")


class _BoundedDecompressedReader:
    """Limit gzip output before tarfile can allocate extended-header payloads."""

    def __init__(self, handle, limit: int, deadline: float):
        self.handle, self.limit, self.deadline = handle, limit, deadline
        self.total = 0

    def read(self, size: int = -1) -> bytes:
        if time.monotonic() > self.deadline:
            raise OcmExportError("OCM archive read exceeded its time budget")
        size = min(size if size >= 0 else 65536, 65536, self.limit - self.total + 1)
        chunk = self.handle.read(size)
        self.total += len(chunk)
        if self.total > self.limit:
            raise OcmExportError("OCM archive expands beyond its byte bound")
        if time.monotonic() > self.deadline:
            raise OcmExportError("OCM archive read exceeded its time budget")
        return chunk


def read_export_archive(
    path: Path,
    commit_sha: str,
    *,
    max_seconds: float = MAX_READ_SECONDS,
    min_country_files: Optional[int] = None,
) -> ExportSnapshot:
    """Stream the commit-pinned archive; return Spain's compact rows plus reference data."""
    if not _SHA_RE.fullmatch(commit_sha):
        raise OcmExportError("OCM archive must be pinned to a 40-hex commit SHA")
    started = time.monotonic()
    try:
        with gzip.open(path, "rb") as compressed:
            bounded = _BoundedDecompressedReader(compressed, MAX_ARCHIVE_TOTAL_BYTES, started + max_seconds)
            return _read_tar(bounded, commit_sha, min_country_files, started, max_seconds)
    except OcmExportError:
        raise
    except (tarfile.TarError, EOFError, OSError, ValueError, zlib.error) as exc:
        raise OcmExportError(f"OCM archive is unreadable or truncated: {type(exc).__name__}") from exc


def _read_tar(bounded, commit_sha, min_country_files, started, max_seconds):
    top = f"ocm-export-{commit_sha}"
    members = declared_total = malformed = 0
    reference = None
    pois = {}
    try:
        with tarfile.open(fileobj=bounded, mode="r|") as archive:
            for member in archive:
                members += 1
                if members > MAX_ARCHIVE_MEMBERS:
                    raise OcmExportError("OCM archive has too many members")
                if time.monotonic() - started > max_seconds:
                    raise OcmExportError("OCM archive read exceeded its time budget")
                name = member.name
                _check_member_name(name)
                if name != top and not name.startswith(top + "/"):
                    raise OcmExportError("OCM archive member is outside the pinned commit directory")
                if not (member.isfile() or member.isdir()):
                    raise OcmExportError("OCM archive contains a non-regular member (link/device)")
                if member.size < 0:
                    raise OcmExportError("OCM archive member has an invalid size")
                declared_total += member.size
                if declared_total > MAX_ARCHIVE_TOTAL_BYTES:
                    raise OcmExportError("OCM archive declares too many uncompressed bytes")
                if not member.isfile():
                    continue
                relative = name[len(top) + 1:]
                if relative == "data/referencedata.json":
                    if reference is not None:
                        raise OcmExportError("OCM archive repeats referencedata.json")
                    if member.size > MAX_REFERENCE_BYTES:
                        raise OcmExportError("OCM referencedata.json is too large")
                    handle = archive.extractfile(member)
                    if handle is None:
                        raise OcmExportError("OCM referencedata.json is unreadable")
                    try:
                        reference = build_reference(json.loads(handle.read().decode("utf-8")))
                    except (UnicodeDecodeError, ValueError) as exc:
                        raise OcmExportError("OCM referencedata.json is not valid JSON") from exc
                    continue
                if relative.startswith(f"data/{OCM_EXPORT_COUNTRY_DIR}/"):
                    match = _POI_NAME_RE.fullmatch(relative)
                    if not match:
                        raise OcmExportError("OCM archive has an unexpected file in the country directory")
                    poi_id = int(match.group(1))
                    if poi_id in pois or member.size > MAX_POI_FILE_BYTES:
                        raise OcmExportError("OCM archive has a duplicate or oversized POI file")
                    handle = archive.extractfile(member)
                    if handle is None:
                        raise OcmExportError("OCM POI file is unreadable")
                    try:
                        row = json.loads(handle.read().decode("utf-8"))
                    except (UnicodeDecodeError, ValueError):
                        malformed += 1
                        pois[poi_id] = {}
                        continue
                    if not isinstance(row, dict) or _int_or_none(row.get("ID")) != poi_id:
                        # Filename id and JSON id must agree; a mismatch is not trustworthy.
                        malformed += 1
                        pois[poi_id] = {}
                        continue
                    validate_poi_shape(row)
                    pois[poi_id] = row
        # Validate the gzip trailer and count bytes beyond tar's end marker too.
        while bounded.read(65536):
            pass
    except OcmExportError:
        raise
    except (tarfile.TarError, EOFError, OSError, ValueError, zlib.error) as exc:
        raise OcmExportError(f"OCM archive is unreadable or truncated: {type(exc).__name__}") from exc
    if reference is None:
        raise OcmExportError("OCM archive has no data/referencedata.json")
    floor = MIN_COUNTRY_FILES if min_country_files is None else min_country_files
    if len(pois) < floor:
        raise OcmExportError(f"OCM archive has only {len(pois)} Spain files; refusing a partial snapshot")
    if malformed > max(0, int(len(pois) * MAX_MALFORMED_FRACTION)):
        raise OcmExportError(f"OCM archive has {malformed} malformed Spain files; refusing the snapshot")
    return ExportSnapshot(
        commit_sha=commit_sha,
        reference=reference,
        pois={k: v for k, v in pois.items() if v},
        stats={"members": members, "country_files": len(pois), "malformed_files": malformed},
        quarantined_ids={key for key, row in pois.items() if not row},
    )


def expand_poi(poi: Dict[str, Any], ref: ReferenceData) -> Dict[str, Any]:
    """Resolve compact lookup IDs into the nested shape the Volta record builder reads.

    ``Comments``/``MediaItems``/``UserComments`` are deliberately not carried: only
    facts (location, hardware, status, usage) are reused, never contributor media or
    free-text comments.
    """
    validate_poi_shape(poi)
    addr = dict(poi.get("AddressInfo") or {})
    country = _lookup(ref.countries, addr.get("CountryID")) or {}
    addr["Country"] = {"ID": addr.get("CountryID"), "ISOCode": str(country.get("ISOCode") or "")}
    out: Dict[str, Any] = {
        "ID": poi.get("ID"),
        "UUID": poi.get("UUID"),
        "DataProviderID": poi.get("DataProviderID"),
        "DataProvider": _lookup(ref.data_providers, poi.get("DataProviderID")),
        "OperatorID": poi.get("OperatorID"),
        "UsageCost": poi.get("UsageCost"),
        "NumberOfPoints": poi.get("NumberOfPoints"),
        "StatusTypeID": poi.get("StatusTypeID"),
        "DateLastVerified": poi.get("DateLastVerified"),
        "DateLastStatusUpdate": poi.get("DateLastStatusUpdate"),
        "DateLastConfirmed": poi.get("DateLastConfirmed"),
        "DateCreated": poi.get("DateCreated"),
        "AddressInfo": addr,
    }
    status = _lookup(ref.status_types, poi.get("StatusTypeID"))
    out["StatusType"] = {"Title": status.get("Title"), "IsOperational": status.get("IsOperational")} if status else None
    usage = _lookup(ref.usage_types, poi.get("UsageTypeID"))
    out["UsageType"] = {"Title": usage.get("Title")} if usage else None
    submission = _lookup(ref.submission_statuses, poi.get("SubmissionStatusTypeID"))
    out["SubmissionStatus"] = (
        {"Title": submission.get("Title"), "IsLive": submission.get("IsLive")} if submission else None
    )
    if submission is None or (poi.get("StatusTypeID") is not None and status is None):
        out["unresolved_reference"] = True
    if poi.get("UsageTypeID") is not None and usage is None:
        out["unresolved_reference"] = True
    operator = _lookup(ref.operators, poi.get("OperatorID"))
    out["OperatorInfo"] = (
        {"Title": operator.get("Title")} if operator and not operator.get("IsPrivateIndividual") else None
    )
    connections = []
    for conn in poi.get("Connections") or []:
        if not isinstance(conn, dict):
            continue
        row = dict(conn)
        ctype = _lookup(ref.connection_types, conn.get("ConnectionTypeID"))
        row["ConnectionType"] = {"Title": ctype.get("Title")} if ctype else None
        cstatus = _lookup(ref.status_types, conn.get("StatusTypeID"))
        row["StatusType"] = {"Title": cstatus.get("Title"), "IsOperational": cstatus.get("IsOperational")} if cstatus else None
        level = _lookup(ref.charger_types, conn.get("LevelID"))
        row["Level"] = {"Title": level.get("Title")} if level else None
        current = _lookup(ref.current_types, conn.get("CurrentTypeID"))
        row["CurrentType"] = {"Title": current.get("Title")} if current else None
        connections.append(row)
    out["Connections"] = connections
    return out


def validate_poi_shape(poi: Dict[str, Any]) -> None:
    for name in ("AddressInfo", "StatusType", "UsageType", "SubmissionStatus", "DataProvider", "OperatorInfo"):
        if poi.get(name) is not None and not isinstance(poi[name], dict):
            raise OcmExportError(f"OCM POI has malformed {name}")
    addr = poi.get("AddressInfo") or {}
    if addr.get("Country") is not None and not isinstance(addr["Country"], dict):
        raise OcmExportError("OCM POI has malformed Country")
    for obj, fields in ((poi, ("UsageCost",)), (addr, ("Title", "AddressLine1", "Town", "Postcode"))):
        if any(obj.get(key) is not None and not isinstance(obj[key], str) for key in fields):
            raise OcmExportError("OCM POI has malformed text fields")
    connections = poi.get("Connections")
    if connections is None:
        connections = []
    if not isinstance(connections, list) or any(not isinstance(c, dict) for c in connections):
        raise OcmExportError("OCM POI has malformed Connections")
    for conn in connections:
        for name in ("ConnectionType", "StatusType", "Level", "CurrentType"):
            if conn.get(name) is not None and not isinstance(conn[name], dict):
                raise OcmExportError(f"OCM connection has malformed {name}")


def legacy_provider_license(poi: Dict[str, Any]) -> Tuple[Optional[str], str, str]:
    """Only OCM contributor data has an explicit retrospective substitution grant."""
    provider = poi.get("DataProvider") or {}
    provider_id = _int_or_none(poi.get("DataProviderID"))
    embedded_id = _int_or_none(provider.get("ID"))
    if provider_id is None or (embedded_id is not None and embedded_id != provider_id):
        return None, str(provider.get("Title") or ""), str(provider.get("License") or "")
    if provider_id == 1:
        return "CC-BY-4.0", "Open Charge Map Contributors", "CC BY 4.0; OCM contributor license substitution announced 2022-04-01"
    if embedded_id != provider_id:
        return None, str(provider.get("Title") or ""), str(provider.get("License") or "")
    grant = provider.get("License")
    return (license_class(grant, open_licensed=provider.get("IsOpenDataLicensed")),
            str(provider.get("Title") or ""), str(grant or ""))


def read_legacy_es_rows(path: Path, *, max_bytes: int = 1024 * 1024 * 1024,
                        max_seconds: float = MAX_READ_SECONDS) -> Dict[int, Dict[str, Any]]:
    """Read Spain's rows from the pinned legacy ``poi.json.gz`` (JSON lines, nested shape).

    Corrupt lines are skipped; a file with no readable Spanish row at all fails closed.
    """
    rows: Dict[int, Dict[str, Any]] = {}
    total = 0
    deadline = time.monotonic() + max_seconds
    try:
        with gzip.open(path, "rb") as handle:
            while True:
                if time.monotonic() > deadline:
                    raise OcmExportError("legacy OCM read exceeded its time budget")
                bound = min(MAX_POI_FILE_BYTES, max_bytes - total)
                line = handle.readline(bound + 1)
                if time.monotonic() > deadline:
                    raise OcmExportError("legacy OCM read exceeded its time budget")
                if len(line) > bound:
                    raise OcmExportError("legacy OCM line or snapshot expands beyond its byte bound")
                if not line:
                    break
                total += len(line)
                if total > max_bytes:
                    raise OcmExportError("legacy OCM snapshot expands beyond its byte bound")
                line = line.strip()
                if not line:
                    continue
                try:
                    poi = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    continue
                if not isinstance(poi, dict):
                    continue
                validate_poi_shape(poi)
                country = ((poi.get("AddressInfo") or {}).get("Country") or {})
                poi_id = _int_or_none(poi.get("ID"))
                if poi_id is not None and str(country.get("ISOCode") or "").upper() == OCM_EXPORT_COUNTRY_ISO:
                    # Mongo storage IDs are not station facts or identity evidence.
                    poi.pop("_id", None)
                    if poi_id in rows and rows[poi_id] != poi:
                        raise OcmExportError("legacy OCM snapshot has conflicting duplicate POI IDs")
                    rows[poi_id] = poi
    except OcmExportError:
        raise
    except (OSError, EOFError, zlib.error) as exc:
        raise OcmExportError(f"legacy OCM snapshot is unreadable: {type(exc).__name__}") from exc
    if not rows:
        raise OcmExportError("legacy OCM snapshot has no readable Spanish rows")
    return rows
