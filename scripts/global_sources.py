#!/usr/bin/env python3
"""Worldwide public directory ingestion primitives (stdlib, candidate builds only).

Directory status is evidence, never live availability. Tariffs retain their native
currency; only explicit structured EUR/kWh values enter the legacy euro ranking.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# Existing ingestion helpers also support direct script execution and import
# source_policy without a package prefix. Keep both documented import styles.
SCRIPTS = str(Path(__file__).resolve().parent)
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

try:
    from .ingest_sources import FetchArtifact
    from .source_policy import load_source_policy_exclusions, value_is_excluded
    from .source_transport import policy_urlopen
except ImportError:
    from ingest_sources import FetchArtifact
    from source_policy import load_source_policy_exclusions, value_is_excluded
    from source_transport import policy_urlopen


OCM_URL = "https://raw.githubusercontent.com/openchargemap/ocm-data/master/poi.json.gz"
OCM_EXPORT_REVISION = "8e3bedca48ca94807d96b2f5e7ee02cffe63ae54"
OCM_EXPORT_URL = f"https://codeload.github.com/openchargemap/ocm-export/tar.gz/{OCM_EXPORT_REVISION}"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
SOURCE_POLICY = load_source_policy_exclusions()
ISO_COUNTRIES = frozenset("AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW".split())
# OCM and other public registries use the user-assigned XK code for Kosovo.
ISO_COUNTRIES = ISO_COUNTRIES | {"XK"}


def iso_timestamp(epoch: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if epoch is None else epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def valid_country(value: Any) -> str | None:
    code = str(value or "").strip().upper()
    return code if code in ISO_COUNTRIES else None


def valid_coordinates(lat: Any, lon: Any) -> bool:
    lat, lon = finite_number(lat), finite_number(lon)
    return lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180 and (lat, lon) != (0, 0)


@dataclass(frozen=True)
class FileArtifact:
    path: Path
    artifact: FetchArtifact


def fetch_cached_file(url: str, cache_dir: Path, cache_name: str, *, ttl_hours: float = 168,
                      max_bytes: int = 768 * 1024 * 1024, timeout: float = 60,
                      offline: bool = False) -> FileArtifact:
    """Atomic, bounded streaming GET cache; failed refresh keeps original age.

    FetchArtifact intentionally has an empty body here: duplicating a worldwide
    snapshot in memory would defeat streaming. Consumers open FileArtifact.path.
    """
    if Path(cache_name).name != cache_name or not cache_name:
        raise ValueError("cache_name must be a filename")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("sources require public HTTPS GET URLs")
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / cache_name
    now = time.time()

    def cached(*, fallback: bool = False, error: str | None = None) -> FileArtifact:
        age = max(0.0, time.time() - path.stat().st_mtime)
        return FileArtifact(path, FetchArtifact(b"", url, cache_name, iso_timestamp(path.stat().st_mtime), age,
                                               True, fallback, age >= ttl_hours * 3600, error))

    if path.is_file() and path.stat().st_size and now - path.stat().st_mtime < ttl_hours * 3600:
        return cached()
    if offline:
        if path.is_file() and path.stat().st_size:
            return cached(fallback=True, error="offline_cache_only")
        raise FileNotFoundError(f"No cached source: {cache_name}")
    temporary: Path | None = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Volta public-source EV charger research", "Accept": "*/*"}, method="GET")
        with policy_urlopen(req, timeout=timeout) as response:
            expected = response.headers.get("Content-Length")
            if expected and int(expected) > max_bytes:
                raise ValueError("source exceeds download byte limit")
            with tempfile.NamedTemporaryFile(dir=cache_dir, prefix=".download-", delete=False) as dest:
                temporary = Path(dest.name)
                total = 0
                while True:
                    chunk = response.read(min(1024 * 1024, max_bytes - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("source exceeds download byte limit")
                    dest.write(chunk)
                if not total or (expected and total != int(expected)):
                    raise ValueError("empty or truncated source response")
        os.replace(temporary, path)
        fetched_at = path.stat().st_mtime
        return FileArtifact(path, FetchArtifact(b"", url, cache_name, iso_timestamp(fetched_at), 0, False, False, False))
    except Exception as exc:
        if path.is_file() and path.stat().st_size:
            return cached(fallback=True, error=type(exc).__name__)
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def iter_ocm(path: Path) -> Iterator[dict]:
    """Read legacy OCM NDJSON for migration, never selected by the new CLI.

    openchargemap/ocm-data is deprecated. Current builds use iter_ocm_export.
    """
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        while True:
            line = handle.readline(2 * 1024 * 1024 + 1)
            if not line:
                return
            if len(line) > 2 * 1024 * 1024:
                raise ValueError("OCM record exceeds 2 MiB line limit")
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError("OCM snapshot must contain one object per line")
            yield item


def _tar_json(archive: tarfile.TarFile, member: tarfile.TarInfo, max_bytes: int = 4 * 1024 * 1024) -> dict:
    if member.size > max_bytes:
        raise ValueError("OCM archive member exceeds JSON byte limit")
    handle = archive.extractfile(member)
    if handle is None:
        raise ValueError("OCM archive member is not a regular file")
    with handle:
        result = json.load(handle)
    if not isinstance(result, dict):
        raise ValueError("OCM archive JSON must be an object")
    return result


def iter_ocm_export(path: Path, countries: set[str] | None = None) -> Iterator[dict]:
    """Two bounded-memory tar passes hydrate compact IDs without extraction.

    Reference tables are small. POI bodies and tar member history are discarded
    after each record, even with older Python tarfile implementations.
    """
    reference = None
    with tarfile.open(path, "r|gz") as archive:
        for member in archive:
            archive.members.clear()
            if member.isfile() and member.name.endswith("/data/referencedata.json"):
                reference = _tar_json(archive, member, 16 * 1024 * 1024)
                break
    if reference is None:
        raise ValueError("OCM export archive lacks data/referencedata.json")
    tables = {key: {row["ID"]: row for row in value if isinstance(row, dict) and "ID" in row}
              for key, value in reference.items() if isinstance(value, list)}

    def hydrate(record: dict, field: str, identifier: str, table: str) -> None:
        if not isinstance(record.get(field), dict):
            record[field] = tables.get(table, {}).get(record.get(identifier), {})

    with tarfile.open(path, "r|gz") as archive:
        for member in archive:
            archive.members.clear()
            match = re.search(r"/data/([A-Z]{2})/OCM-\d+\.json$", member.name)
            if not member.isfile() or not match or (countries is not None and match[1] not in countries):
                continue
            poi = _tar_json(archive, member)
            addr = poi.get("AddressInfo")
            if not isinstance(addr, dict):
                yield poi
                continue
            hydrate(addr, "Country", "CountryID", "Countries")
            # Country folder is corroboration, not permission to override a
            # contradictory explicit country reference.
            if valid_country((addr.get("Country") or {}).get("ISOCode")) != match[1]:
                continue
            for args in (("DataProvider", "DataProviderID", "DataProviders"),
                         ("OperatorInfo", "OperatorID", "Operators"),
                         ("StatusType", "StatusTypeID", "StatusTypes"),
                         ("UsageType", "UsageTypeID", "UsageTypes")):
                hydrate(poi, *args)
            for conn in poi.get("Connections") or []:
                if not isinstance(conn, dict):
                    continue
                hydrate(conn, "ConnectionType", "ConnectionTypeID", "ConnectionTypes")
                hydrate(conn, "StatusType", "StatusTypeID", "StatusTypes")
            yield poi


def ocm_export_revision(path: Path) -> str:
    """Read the GitHub archive root instead of assigning a guessed revision."""
    with tarfile.open(path, "r|gz") as archive:
        member = archive.next()
        root = member.name.split("/", 1)[0] if member else ""
    match = re.fullmatch(r"ocm-export-([0-9a-f]{40})", root)
    if not match:
        raise ValueError("OCM archive root does not identify an immutable export commit")
    return match[1]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def provider_license_allowed(provider: dict) -> bool:
    if not isinstance(provider, dict) or provider.get("IsOpenDataLicensed") is not True:
        return False
    license_text = str(provider.get("License") or "").casefold()
    if re.search(r"non.?commercial|no.?deriv|\bby[\s-]+(?:nc|nd)\b|/by-(?:nc|nd)", license_text):
        return False
    return bool(re.search(r"\bcc[\s-]*0\b|\bcc[\s-]*by\b|creativecommons\.org/licenses/by/|creative commons attribution|open government licen[cs]e|\bodbl\b|licence_ouverte|any purpose whatsoever", license_text))


def normalize_connector(title: Any, identifier: Any = None) -> str | None:
    text = str(title or "").strip()
    low = text.casefold()
    if not text or low in {"unknown", "(unknown)", "other"}:
        return None
    if "nacs" in low or "j3400" in low:
        return "NACS"
    if "ccs" in low or "combo" in low:
        if str(identifier) == "32" or re.search(r"(?:type|ccs|combo)\s*1\b", low):
            return "CCS1"
        if str(identifier) == "33" or re.search(r"(?:type|ccs|combo)\s*2\b", low):
            return "CCS2"
        return "CCS (unspecified)"
    if "chademo" in low:
        return "CHAdeMO"
    if "gb/t" in low or "gbt" in low:
        return "GB/T DC" if "dc" in low else "GB/T AC" if "ac" in low else "GB/T"
    if "type 1" in low or "j1772" in low:
        return "Type 1"
    if "type 2" in low or "mennekes" in low:
        return "Type 2"
    if "schuko" in low:
        return "Schuko"
    if "tesla" in low:
        return "Tesla"
    return text


def make_record(*, source_id: str, upstream_id: str, source: str, source_url: str,
                name: str, lat: Any, lon: Any, country_code: str, artifact: FetchArtifact,
                connectors: list[dict], raw: dict, address: str = "", city: str = "",
                operator: str = "", tariff: dict | None = None,
                source_updated_at: str | None = None, license_name: str = "") -> dict | None:
    country = valid_country(country_code)
    if not country or not valid_coordinates(lat, lon) or not str(upstream_id).strip():
        return None
    tariff = dict(tariff or {})
    currency = str(tariff.get("currency") or "").upper()
    currency = currency if re.fullmatch(r"[A-Z]{3}", currency) else None
    amount = finite_number(tariff.get("amount"))
    if amount is not None and amount < 0:
        amount = None
    price = amount if (tariff.get("structured") is True and currency == "EUR" and tariff.get("unit") == "kWh"
                       and not tariff.get("conditional") and not tariff.get("additional_components")) else None
    tariff.update(currency=currency, amount=amount)
    labels = sorted({str(c["label"]) for c in connectors if c.get("label")})
    power = max((p for c in connectors if (p := finite_number(c.get("power_kw"))) is not None and p > 0), default=None)
    counts = [int(n) for c in connectors if (n := finite_number(c.get("count"))) is not None and n > 0]
    note = "Cached directory evidence; check the operator and onsite signs before travelling."
    row = {
        "id": f"global_{source_id}_{upstream_id}", "name": str(name or "Unnamed charger"),
        "address": str(address or ""), "city": str(city or ""), "lat": float(lat), "lon": float(lon),
        "country_code": country, "country": country, "operator": str(operator or ""),
        "price_min": price, "price_currency": currency, "price_native_min": amount, "tariff": tariff,
        "is_free": price == 0, "is_cheap": price is not None and price <= 0.25,
        "price_claim_type": "price_unknown_lead" if price is None else "confirmed_free" if price == 0 else "confirmed_cheap" if price <= 0.25 else "confirmed_paid",
        "free_condition": str(tariff.get("raw_text") or "Charging tariff not published in structured EUR/kWh."),
        "tesla_ok": None, "fast": power is not None and power >= 43,
        "max_power_kw": power, "connector_labels": labels, "connectors": connectors,
        "status": "VERIFY", "available": 0, "total_evse": sum(counts) or None,
        "connector_count": sum(counts) or None, "source": source, "source_url": source_url,
        "sources": [{"name": source, "url": source_url, "evidence": note, "license": license_name,
                     "source_last_updated": source_updated_at}],
        "source_integration": {"mode": "community_snapshot" if source_id == "ocm" else "community_tags" if source_id == "osm" else "official_snapshot",
                               "live_status_integrated": False, "station_status_fields": False,
                               "connector_status_fields": False, "note": note},
        "reliability": "directory-community-snapshot" if source_id == "ocm" else "community-price-unknown" if source_id == "osm" else "official-snapshot",
        "source_fetched_at": artifact.fetched_at, "source_fetch": artifact.as_evidence(),
        "last_seen": source_updated_at, "source_stale": artifact.stale,
        "charge_claim": {"type": "price_unknown" if price is None else "free" if price == 0 else "cheap" if price <= 0.25 else "paid",
                         "source": source, "url": source_url, "price_min": price, "currency": currency,
                         "raw_tariff": tariff, "summary": str(tariff.get("raw_text") or note)},
        "parking": {"kind": "unknown", "paid": False, "free_likelihood": "unknown", "note": "Parking tariff is not verified; check onsite signs."},
        "parking_claim": {"kind": "unknown", "source": source, "url": source_url, "cost_hint": None, "summary": "Parking tariff not verified."},
        "access": {"level": "unknown", "label": "Access unknown", "note": note},
        "location_claim": {"source": source, "url": source_url, "lat": float(lat), "lon": float(lon), "method": "public_source_coordinate", "precision": "source_coordinate", "summary": note},
        "hardware_claim": {"source": source, "url": source_url, "connector_types": labels, "max_power_kw": power, "live_status_integrated": False, "summary": note},
        "media": {"photos": [], "photo_status": "no_public_photo"},
        "navigation": {"google": f"https://www.google.com/maps/search/?api=1&query={lat},{lon}", "apple": f"https://maps.apple.com/?ll={lat},{lon}"},
        "insight": note, "raw": raw,
    }
    return None if value_is_excluded(row, SOURCE_POLICY) else row


def normalize_ocm(poi: dict, artifact: FetchArtifact) -> dict | None:
    addr = poi.get("AddressInfo") or {}
    if not isinstance(addr, dict):
        return None
    country = valid_country((addr.get("Country") or {}).get("ISOCode"))
    poi_id = poi.get("ID")
    provider = poi.get("DataProvider") or {}
    if not isinstance(addr, dict) or not country or not isinstance(poi_id, int) or isinstance(poi_id, bool) or poi_id <= 0 or poi_id == 169683:
        return None
    if not provider_license_allowed(provider):
        return None
    provider_source = {"source": provider.get("Title"), "source_url": provider.get("WebsiteURL") or provider.get("DataProviderStatusType", {}).get("WebsiteURL")}
    if value_is_excluded(provider_source, SOURCE_POLICY):
        return None
    source_url = f"https://map.openchargemap.io/?mode=details&id={poi_id}"
    connectors = []
    for conn in poi.get("Connections") or []:
        if not isinstance(conn, dict):
            continue
        label = normalize_connector((conn.get("ConnectionType") or {}).get("Title"), conn.get("ConnectionTypeID"))
        if not label:
            continue
        count = finite_number(conn.get("Quantity"))
        connectors.append({"connector_id": str(conn.get("ID") or ""), "label": label, "type": label,
                           "status": "UNKNOWN", "power_kw": finite_number(conn.get("PowerKW")),
                           "count": int(count) if count and count > 0 else None,
                           "source_url": source_url, "raw": {"StatusType": conn.get("StatusType"), "ConnectionTypeID": conn.get("ConnectionTypeID")}})
    # UsageCost is free text. Even '$0.20' and 'free' remain unconverted claims.
    structured = poi.get("Tariff") if isinstance(poi.get("Tariff"), dict) else {}
    tariff = {"currency": structured.get("Currency"), "amount": structured.get("PricePerKWh"),
              "unit": "kWh" if "PricePerKWh" in structured else None,
              "structured": bool(structured), "raw_text": poi.get("UsageCost") or ""}
    raw = {"ID": poi_id, "UUID": poi.get("UUID"), "UsageCost": poi.get("UsageCost"),
           "UsageType": poi.get("UsageType"), "StatusType": poi.get("StatusType"),
           "StatusTypeID": poi.get("StatusTypeID"), "StatusTypeTitle": (poi.get("StatusType") or {}).get("Title"),
           "DataProvider": provider, "source": provider.get("Title"), "source_url": provider.get("WebsiteURL"),
           "operator": (poi.get("OperatorInfo") or {}).get("Title"), "country_code": country}
    row = make_record(source_id="ocm", upstream_id=str(poi_id), source="OpenChargeMap public snapshot",
                       source_url=source_url, name=addr.get("Title"), lat=addr.get("Latitude"), lon=addr.get("Longitude"),
                       country_code=country, address=addr.get("AddressLine1"), city=addr.get("Town"),
                       operator=raw["operator"], artifact=artifact, connectors=connectors, raw=raw, tariff=tariff,
                       source_updated_at=poi.get("DateLastVerified") or poi.get("DateLastStatusUpdate"),
                       license_name=str(provider.get("License")))
    usage = poi.get("UsageType") if isinstance(poi.get("UsageType"), dict) else {}
    usage_title = str(usage.get("Title") or "")
    if row is not None and (re.search(r"\bprivate(?:ly)?\b|restricted|permit|staff|members? only|notice required", usage_title, re.I)
                            or usage.get("IsAccessKeyRequired") is True):
        row["access"] = {"level": "restricted", "label": "Restricted access", "note": usage_title or "Source requires an access key; verify eligibility with the operator."}
        row["access_claim"] = {"source": row["source"], "url": source_url, "level": "restricted", "private_or_restricted": True, "summary": row["access"]["note"]}
    station_status = poi.get("StatusType") if isinstance(poi.get("StatusType"), dict) else {}
    if row is not None and (station_status.get("IsOperational") is False or poi.get("StatusTypeID") in {30, 100, 150, 200, 210}):
        row["source_reported_unavailable"] = {"source": row["source"], "url": source_url,
                                              "value": poi.get("StatusTypeID"),
                                              "summary": "OpenChargeMap snapshot reports: " + str(station_status.get("Title") or "not operational"),
                                              "observed_at": row.get("last_seen"), "live": False}
    return row


OSM_SOCKETS = {"type2_combo": "CCS2", "type1_combo": "CCS1", "type2": "Type 2", "type2_cable": "Type 2",
               "type1": "Type 1", "type1_cable": "Type 1", "chademo": "CHAdeMO", "schuko": "Schuko",
               "tesla_supercharger": "Tesla", "tesla_destination": "Tesla", "nacs": "NACS",
               "gbt_dc": "GB/T DC", "gbt_ac": "GB/T AC"}


def normalize_osm(element: dict, artifact: FetchArtifact, country_code: str | None = None) -> dict | None:
    tags = element.get("tags") or {}
    if tags.get("amenity") != "charging_station" or tags.get("motorcar") == "no":
        return None
    if any(tags.get(key) in {"yes", "charging_station"} for key in ("disused", "abandoned", "demolished")):
        return None
    country = valid_country(tags.get("addr:country")) or valid_country(country_code)
    kind, identifier = element.get("type"), element.get("id")
    if kind not in {"node", "way", "relation"} or not isinstance(identifier, int) or isinstance(identifier, bool) or identifier <= 0:
        return None
    if country_code and valid_country(country_code) != country:
        return None
    location = element if kind == "node" else element.get("center") or {}
    source_url = f"https://www.openstreetmap.org/{kind}/{identifier}"
    connectors = []
    for suffix, label in OSM_SOCKETS.items():
        key = f"socket:{suffix}"
        value = tags.get(key)
        if value in (None, "", "no", "0", 0):
            continue
        count = finite_number(value)
        raw_power = str(tags.get(f"{key}:output") or "")
        match = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(kW|W)?\s*", raw_power, re.I)
        power = float(match[1].replace(",", ".")) / (1000 if match[2] and match[2].lower() == "w" else 1) if match else None
        connectors.append({"connector_id": key, "label": label, "type": label, "status": "UNKNOWN",
                           "count": int(count) if count and count > 0 else None, "power_kw": power,
                           "source_url": source_url, "raw": {"count": value, "output": raw_power}})
    if not connectors and (tags.get("bicycle") == "yes" or tags.get("scooter") == "yes") and tags.get("motorcar") != "yes":
        return None
    tariff = {"currency": tags.get("charge:currency"), "amount": tags.get("charge:amount"), "unit": tags.get("charge:unit"),
              "structured": bool(tags.get("charge:currency") and tags.get("charge:unit")),
              "raw_text": str(tags.get("charge") or ""), "fee": tags.get("fee"),
              "conditional": tags.get("fee:conditional") or tags.get("charge:conditional"),
              "additional_components": {key: value for key, value in tags.items()
                                        if key.startswith("charge:") and key not in {"charge:currency", "charge:amount", "charge:unit", "charge:conditional"}}}
    raw = dict(tags, osm_type=kind, osm_id=identifier, osm_last_modified=element.get("timestamp"))
    return make_record(source_id="osm", upstream_id=f"{kind}_{identifier}", source="OpenStreetMap public tags",
                       source_url=source_url, name=tags.get("name") or tags.get("operator") or "Charging station",
                       lat=location.get("lat"), lon=location.get("lon"), country_code=country,
                       address=" ".join(str(tags[k]) for k in ("addr:street", "addr:housenumber") if tags.get(k)),
                       city=tags.get("addr:city"), operator=tags.get("operator") or tags.get("network"),
                       artifact=artifact, connectors=connectors, raw=raw, tariff=tariff,
                       source_updated_at=element.get("timestamp"), license_name="OpenStreetMap contributors, ODbL 1.0")


def osm_query(country_code: str | None = None, bbox: tuple | list | None = None) -> str:
    if bbox is not None:
        if len(bbox) != 4 or any(finite_number(x) is None for x in bbox):
            raise ValueError("bbox must be south,west,north,east")
        south, west, north, east = map(float, bbox)
        if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
            raise ValueError("bbox must be valid and non-wrapping; split antimeridian requests")
        if (north - south) * (east - west) > 25:
            raise ValueError("OSM viewport must be at most 25 square degrees; split into bounded tiles")
        selector = f"({south},{west},{north},{east})"
        prefix = ""
    else:
        code = valid_country(country_code)
        if not code:
            raise ValueError("country must be an ISO 3166-1 alpha-2 code")
        prefix = f'area["ISO3166-1"="{code}"]["admin_level"="2"]->.country;'
        selector = "(area.country)"
    return f'[out:json][timeout:45][maxsize:33554432];{prefix}nwr["amenity"="charging_station"]{selector};out center meta;'


def parse_osm_payload(payload: Any) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
        raise ValueError("Overpass response lacks an elements array")
    if payload.get("remark"):
        raise ValueError("Overpass reported an incomplete query")
    return payload["elements"]


def _identity(row: dict) -> str:
    url = str(row.get("source_url") or "")
    parsed = urllib.parse.urlsplit(url)
    if parsed.hostname in {"map.openchargemap.io", "openchargemap.io"}:
        value = urllib.parse.parse_qs(parsed.query).get("id", [""])[0]
        if value.isdigit():
            return f"ocm:{value}"
    if parsed.hostname in {"www.openstreetmap.org", "openstreetmap.org"} and re.fullmatch(r"/(node|way|relation)/\d+", parsed.path):
        return f"osm:{parsed.path}"
    return str(row.get("id") or "")


def _identities(row: dict) -> set[str]:
    keys = {_identity(row)}
    for source in row.get("sources") or []:
        if isinstance(source, dict):
            key = _identity({"source_url": source.get("url")})
            if key:
                keys.add(key)
    return {key for key in keys if key}


def _distance_m(a: dict, b: dict) -> float:
    lat1, lat2 = math.radians(float(a["lat"])), math.radians(float(b["lat"]))
    dl = math.radians(float(b["lon"]) - float(a["lon"]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dl / 2) ** 2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(max(0, h))))


class CandidateStore:
    """Disk-backed append-only union. A failed source cannot erase the baseline.

    Dedup requires the same stable identity, same country and <250 m; proximity
    or similar names alone never join two sources. Conflicts remain reviewable.
    """
    def __init__(self, path: Path, policy: dict | None = None):
        self.connection = sqlite3.connect(path)
        self.connection.execute("CREATE TABLE IF NOT EXISTS stations (row_no INTEGER PRIMARY KEY, id TEXT UNIQUE, country TEXT, identity TEXT, payload TEXT, baseline INTEGER)")
        self.connection.execute("CREATE INDEX IF NOT EXISTS identity_idx ON stations(country, identity)")
        self.connection.execute("CREATE TABLE IF NOT EXISTS identities (country TEXT, identity TEXT, row_no INTEGER, PRIMARY KEY(country,identity,row_no))")
        self.policy = policy or SOURCE_POLICY
        self.counters: Counter = Counter()

    def add(self, row: dict, baseline: bool = False) -> str:
        if not isinstance(row, dict) or not row.get("id") or not valid_coordinates(row.get("lat"), row.get("lon")):
            self.counters["invalid"] += 1
            return "invalid"
        if value_is_excluded(row, self.policy):
            self.counters["excluded"] += 1
            return "excluded"
        country = valid_country(row.get("country_code") or row.get("country")) or ("ES" if baseline else None)
        if not country:
            self.counters["invalid"] += 1
            return "invalid"
        identity = _identity(row)
        identities = _identities(row)
        if not baseline:
            for key in identities:
                for (old,) in self.connection.execute("SELECT s.payload FROM identities i JOIN stations s USING(row_no) WHERE i.country=? AND i.identity=?", (country, key)):
                    if _distance_m(row, json.loads(old)) <= 250:
                        self.counters["duplicate"] += 1
                        return "duplicate"
        # A relocated or cross-country upstream ID is retained with an explicit
        # suffix; IDs remain unique without silently discarding conflicting pins.
        if self.connection.execute("SELECT 1 FROM stations WHERE id=?", (row["id"],)).fetchone():
            if baseline:
                raise ValueError("duplicate baseline station ID")
            row = dict(row)
            row["id"] += "_conflict_" + hashlib.sha256(f'{country}:{row["lat"]}:{row["lon"]}'.encode()).hexdigest()[:12]
            row["identity_conflict"] = True
            self.counters["identity_conflicts"] += 1
        cursor = self.connection.execute("INSERT INTO stations(id,country,identity,payload,baseline) VALUES(?,?,?,?,?)",
                                         (row["id"], country, identity, json.dumps(row, ensure_ascii=False, separators=(",", ":")), int(baseline)))
        self.connection.executemany("INSERT INTO identities(country,identity,row_no) VALUES(?,?,?)",
                                    ((country, key, cursor.lastrowid) for key in identities))
        self.counters["baseline" if baseline else "added"] += 1
        return "added"

    def rows(self) -> Iterator[dict]:
        for (payload,) in self.connection.execute("SELECT payload FROM stations ORDER BY row_no"):
            yield json.loads(payload)

    def fingerprint(self) -> str:
        """Match source_policy.recordset_sha256 without materialising all rows."""
        digest = hashlib.sha256(b"[")
        first = True
        for (payload,) in self.connection.execute("SELECT payload FROM stations ORDER BY id"):
            if not first:
                digest.update(b",")
            digest.update(json.dumps(json.loads(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
            first = False
        digest.update(b"]")
        return digest.hexdigest()

    def stats(self) -> dict:
        return {**dict(self.counters), "total_records": self.connection.execute("SELECT COUNT(*) FROM stations").fetchone()[0],
                "countries": dict(self.connection.execute("SELECT country,COUNT(*) FROM stations GROUP BY country ORDER BY country"))}

    def close(self) -> None:
        self.connection.close()
