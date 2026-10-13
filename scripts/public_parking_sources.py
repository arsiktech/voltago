#!/usr/bin/env python3
"""Bounded public Telpark and Elektrum directory ingestion for world candidates.

Rows are staged until all requested pages succeed. Directory observations never
grant live availability, a charging price, or an exact Telpark charging-bay pin.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

try:
    from . import source_public_station_pages as pages
    from .global_sources import iso_timestamp, make_record
    from .ingest_sources import FetchArtifact
except ImportError:
    import source_public_station_pages as pages
    from global_sources import iso_timestamp, make_record
    from ingest_sources import FetchArtifact


TELPARK_LIST_URL = "https://www.telpark.com/es/wp-json/wp/v2/parkings?per_page=100&page={page}"
ELEKTRUM_URL = "https://elektrum.ee/en/elektrum-drive/charging-solutions/public-charging-points"
MAX_PAGES = 10
MAX_DETAILS = 300
MAX_BODY_BYTES = 8_000_000
MAX_COLLECTION_BYTES = 100_000_000
COLLECTION_SECONDS = 180
CACHE_TTL_SECONDS = 24 * 3600
USER_AGENT = "VoltaGoDirectory/1.0 (public GET only)"
SOURCES = {
    "telpark": {"name": "Telpark public parking pages", "countries": ["ES"],
                "url": TELPARK_LIST_URL.format(page=1), "cache_name": "telpark_list_1.json",
                "license": "Public operator directory; no open-data license asserted",
                "attribution": "Telpark", "record_unit": "parking_facility", "default": True},
    "elektrum_drive": {"name": "Elektrum Drive public charging points", "countries": ["EE", "LV", "LT"],
                       "url": ELEKTRUM_URL, "cache_name": "elektrum_drive.html",
                       "license": "Public operator directory; no open-data license asserted",
                       "attribution": "Elektrum Drive", "record_unit": "station", "default": True},
}


def telpark_detail_url(url: Any) -> bool:
    return isinstance(url, str) and re.fullmatch(
        r"https://www\.telpark\.com/es/ciudades/[a-z0-9]+(?:-[a-z0-9]+)*/[a-z0-9]+(?:-[a-z0-9]+)*/", url,
    ) is not None


def cache_name_for_url(url: str) -> str:
    if url == ELEKTRUM_URL:
        return "elektrum_drive.html"
    for page in range(1, MAX_PAGES + 1):
        if url == TELPARK_LIST_URL.format(page=page):
            return f"telpark_list_{page}.json"
    if telpark_detail_url(url):
        return "telpark_detail_" + hashlib.sha256(url.encode()).hexdigest() + ".html"
    raise ValueError("Unapproved public directory URL")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Public directory requests must not redirect")


def bounded_fetch(url: str, *, timeout: float = 30, headers: dict | None = None,
                  deadline: float | None = None) -> bytes:
    """Exact allowlist, anonymous GET, no cookies/credentials or redirects."""
    cache_name_for_url(url)
    if headers and any(name.lower() not in {"accept", "user-agent"} for name in headers):
        raise ValueError("Public directory requests accept no credential headers")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Public directory timeout must be finite and positive")
    deadline = min(deadline or time.monotonic() + 30, time.monotonic() + 30)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Public directory deadline exceeded")
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT, **(headers or {})})
    chunks = []
    total = 0
    with urllib.request.build_opener(_NoRedirect()).open(request, timeout=min(timeout, remaining)) as response:
        expected = response.headers.get("Content-Length")
        if expected and (int(expected) < 0 or int(expected) > MAX_BODY_BYTES):
            raise ValueError("Public directory response exceeds body budget")
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("Public directory deadline exceeded")
            chunk = response.read(min(64 * 1024, MAX_BODY_BYTES - total + 1))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_BODY_BYTES:
                raise ValueError("Public directory response exceeds body budget")
        if not total or expected and total != int(expected):
            raise ValueError("Empty or truncated public directory response")
    return b"".join(chunks)


def _cached_fetch(url: str, cache_dir: Path, *, offline: bool, deadline: float) -> FetchArtifact:
    """Keep artifact mtime; an online failure never publishes cached partial work."""
    name = cache_name_for_url(url)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / name
    hit = path.is_file()
    age = max(0.0, time.time() - path.stat().st_mtime) if hit else 0
    if offline or hit and age < CACHE_TTL_SECONDS:
        if not hit:
            raise FileNotFoundError("Public directory cache is missing")
        if not 0 < path.stat().st_size <= MAX_BODY_BYTES:
            raise ValueError("Public directory cached response exceeds body budget")
        body = path.read_bytes()
        stamp = path.stat().st_mtime
    else:
        body = bounded_fetch(url, deadline=deadline)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=cache_dir, prefix=".directory-", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(body)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        stamp, age, hit = path.stat().st_mtime, 0, False
    return FetchArtifact(body, url, name, iso_timestamp(stamp), age, hit, False, age >= CACHE_TTL_SECONDS)


def _observed_at(artifact: FetchArtifact) -> datetime:
    stamp = datetime.fromisoformat(artifact.fetched_at.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Public directory acquisition time must include timezone")
    return stamp


def _directory_record(source_id: str, **kwargs) -> dict | None:
    metadata = SOURCES[source_id]
    row = make_record(source_id=source_id, source=metadata["name"],
                      license_name=metadata["license"], tariff={"currency": "EUR", "structured": False}, **kwargs)
    if row is not None:
        row["source_record_unit"] = metadata["record_unit"]
        row["sources"][0]["attribution"] = metadata["attribution"]
        row["source_integration"]["mode"] = "operator_directory"
        row["reliability"] = "directory-page"
        row.pop("available", None)
    return row


def parse_telpark_record(item: dict, detail_body: bytes, list_artifact: FetchArtifact,
                         detail_artifact: FetchArtifact) -> dict | None:
    """Only one explicit Spanish ParkingFacility can supply the EV flag and geo."""
    detail = pages.parse_telpark_detail(detail_body)
    country = detail.get("country_raw")
    if isinstance(country, dict):
        country = country.get("name") or country.get("@id")
    native_id = str(item.get("wp_id") or "")
    if (not re.fullmatch(r"[1-9][0-9]*", native_id) or not telpark_detail_url(item.get("url"))
            or detail.get("ev_charging") is not True
            or pages._fold(country) not in {"es", "espana", "spain", "aparcar en espana"}
            or str(detail.get("telephone") or "").replace(" ", "").startswith("+351")
            or not pages._valid_coords(detail.get("lat"), detail.get("lon"))
            or item.get("country") in {"PT", "AD"}):
        return None
    if detail_artifact.url != item["url"]:
        raise ValueError("Telpark detail provenance mismatch")
    oldest = min((list_artifact, detail_artifact), key=_observed_at)
    raw = {**item, "ev_charging": True, "ev_flag_source": "detail_jsonld_amenityFeature",
           "coordinate_source": "telpark_detail_jsonld_geo", "telpark_parking_id": native_id,
           "telpark_detail_url": item["url"], "country": "ES",
           "source_fetch_artifacts": [list_artifact.as_evidence(), detail_artifact.as_evidence()]}
    record = _directory_record("telpark", upstream_id=native_id, source_url=item["url"],
                               name=detail.get("name") or item["name"], lat=detail["lat"], lon=detail["lon"],
                               country_code="ES", artifact=oldest, connectors=[], raw=raw,
                               address=detail.get("address") or item.get("address"),
                               city=item.get("city_slug", "").replace("-", " ").title(), operator="Telpark")
    if record is not None:
        note = "Parking cost and access must be checked on the Telpark page and onsite."
        if item.get("parking_price_text"):
            note = "Parking page says: " + item["parking_price_text"] + ". " + note
        record["parking"]["note"] = note
        record["parking_claim"].update(cost_hint=item.get("parking_price_text"), summary=note)
        record["location_claim"].update(precision="official_parking_coordinate",
                                          summary="Parking coordinate; exact charging bay position is unverified.")
        record["hardware_claim"]["summary"] = "Parking advertises EV charging; plugs, power and bay count are unknown."
    return record


def parse_elektrum_records(body: bytes, artifact: FetchArtifact) -> list[dict]:
    """Preserve Baltic scope and distinguish plug standards from connector counts."""
    rows = []
    for item in pages.parse_elektrum_public_points(body):
        if item["country"] not in {"EE", "LV", "LT"}:
            continue
        connectors = []
        for index, connector in enumerate(item["connectors"]):
            labels = []
            for plug in connector["plugs"]:
                label = {"ccs": "CCS2", "ccs2": "CCS2", "chademo": "CHAdeMO", "type 2": "Type 2"}.get(plug.casefold())
                if label and label not in labels:
                    labels.append(label)
            # A line such as '2 x 50kW (CHAdeMO, CCS)' does not say how many
            # sockets of each standard exist. Preserve its group count in raw.
            for label in labels:
                connectors.append({"connector_id": f"{item['source_id']}:{index}:{label}", "label": label,
                                   "type": label, "status": "UNKNOWN", "power_kw": connector["power_kw"],
                                   "count": connector["count"] if len(labels) == 1 else None, "raw": connector})
        record = _directory_record("elektrum_drive", upstream_id=item["source_id"], source_url=ELEKTRUM_URL,
                                   name=item["name"], lat=item["lat"], lon=item["lon"], country_code=item["country"],
                                   artifact=artifact, connectors=connectors, raw=item,
                                   address=item["address"], city=item["city"], operator="Elektrum Drive")
        if record is not None:
            # Connector groups are not independent physical EVSE identifiers.
            record["total_evse"] = None
            record["connector_count"] = None
            rows.append(record)
    return rows


def run_source(source_id: str, cache_dir: Path, countries: set[str] | None, offline: bool,
               emit: Callable[[dict], Any]) -> dict:
    metadata = SOURCES[source_id]
    report = {"source": source_id, "source_id": source_id, "records": 0, "seen": 0, "accepted": 0,
              "rejected": 0, "duplicates": 0, "errors": [], "fetches": [], "live_status": False,
              "record_unit": metadata["record_unit"], "license": metadata["license"],
              "attribution": metadata["attribution"], "source_url": metadata["url"],
              "scope": sorted(set(metadata["countries"]) & countries) if countries is not None else metadata["countries"]}
    if not report["scope"]:
        return dict(report, status="not_applicable", reason="outside_requested_countries")
    deadline = time.monotonic() + COLLECTION_SECONDS
    total_bytes = 0
    artifacts = []

    def fetch(url: str) -> FetchArtifact:
        nonlocal total_bytes
        if time.monotonic() >= deadline:
            raise TimeoutError("Public directory collection deadline exceeded")
        artifact = _cached_fetch(url, Path(cache_dir), offline=offline, deadline=deadline)
        if artifact.url != url or artifact.fallback or artifact.error:
            raise ValueError("Public directory artifact provenance or acquisition failure")
        _observed_at(artifact)
        total_bytes += len(artifact.body)
        if len(artifact.body) > MAX_BODY_BYTES or total_bytes > MAX_COLLECTION_BYTES:
            raise ValueError("Public directory collection exceeds byte budget")
        artifacts.append(artifact)
        report["fetches"].append(artifact.as_evidence())
        return artifact

    try:
        records = []
        if source_id == "elektrum_drive":
            artifact = fetch(ELEKTRUM_URL)
            records = parse_elektrum_records(artifact.body, artifact)
            parsed = pages.parse_elektrum_public_points(artifact.body)
            report["seen"] = len(parsed)
            report["country_unresolved"] = sum(item["country"] not in {"EE", "LV", "LT"} for item in parsed)
        else:
            candidates = {}
            complete = False
            for page in range(1, MAX_PAGES + 1):
                artifact = fetch(TELPARK_LIST_URL.format(page=page))
                payload = json.loads(artifact.body)
                if not isinstance(payload, list) or len(payload) > 100:
                    raise ValueError("Unexpected Telpark pagination schema")
                report["seen"] += len(payload)
                for item in pages.parse_telpark_parkings_page(payload):
                    if item["country"] in {"PT", "AD"}:
                        continue
                    if not telpark_detail_url(item["url"]):
                        raise ValueError("Unapproved Telpark detail URL")
                    previous = candidates.get(item["source_id"])
                    if previous is not None and previous[0] != item:
                        raise ValueError("Conflicting Telpark upstream identity")
                    candidates.setdefault(item["source_id"], (item, artifact))
                if len(payload) < 100:
                    complete = True
                    break
            report["list_complete"] = complete
            if not complete or len(candidates) > MAX_DETAILS:
                raise ValueError("Telpark pagination or detail budget incomplete")
            report["ev_unconfirmed"] = 0
            report["ev_false"] = 0
            for item, list_artifact in candidates.values():
                detail = fetch(item["url"])
                record = parse_telpark_record(item, detail.body, list_artifact, detail)
                if record is None:
                    if pages.parse_telpark_detail(detail.body).get("ev_charging") is False:
                        report["ev_false"] += 1
                    else:
                        report["ev_unconfirmed"] += 1
                else:
                    records.append(record)
        # No emit is possible before all acquisition, parsing and identity checks.
        for row in records:
            if countries is not None and row["country_code"] not in countries:
                continue
            outcome = emit(row)
            report["accepted"] += outcome == "added"
            report["duplicates"] += outcome == "duplicate"
            report["records"] += 1
        report["rejected"] = report["seen"] - report["records"]
        stale = any(artifact.stale for artifact in artifacts)
        complete = not (report.get("ev_unconfirmed") or report.get("country_unresolved"))
        report.update(status="stale" if stale else "ok", stale=stale, coverage_complete=complete,
                      fetch=min(artifacts, key=_observed_at).as_evidence())
        if not report["records"]:
            report.update(status="failed", reason="no_accepted_records_in_scope")
            report["errors"].append("no_accepted_records_in_scope")
    except (OSError, ValueError, TypeError) as exc:
        report.update(status="failed", error=type(exc).__name__, coverage_complete=False)
        report["rejected"] = report["seen"]
        report["errors"].append(type(exc).__name__)
    return report
