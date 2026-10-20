"""Public Endolla GELFS and Spirii AFIR status overlays (stdlib, no tariffs).

Parsers retain identity and last-change times. FeedCache grants status authority only
while independently validated status and static metadata remain fresh. No I/O at import.
"""
from __future__ import annotations

import concurrent.futures
import gzip
import io
import json
import math
import threading
import time
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from scripts.source_transport import PUBLIC_LIVE_URLS, public_live_urlopen

STATUS_TTL = 60
STATUS_MAX_AGE = 180
PUBLICATION_MAX_AGE = 300
STATIC_TTL = 86400
MAX_BYTES = 8_000_000
TIMEOUT = 5.0
STATUS_MAP = {
    "endolla": {"AVAILABLE": "AVAILABLE", "IN_USE": "CHARGING", "RESERVED": "RESERVED",
                "OUT_OF_ORDER": "OUTOFORDER", "UNAVAILABLE": "UNAVAILABLE", "UNKNOWN": "UNKNOWN"},
    "spirii": {"available": "AVAILABLE", "occupied": "CHARGING", "reserved": "RESERVED",
               "outOfOrder": "OUTOFORDER", "unavailable": "UNAVAILABLE", "unknown": "UNKNOWN",
               "closed": "UNAVAILABLE", "planned": "UNAVAILABLE", "removed": "UNAVAILABLE"},
}


def iso(epoch=None):
    return datetime.fromtimestamp(time.time() if epoch is None else epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def epoch(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.timestamp() if dt.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def recent(value, now, limit):
    stamp = epoch(value)
    return stamp is not None and -60 <= now - stamp <= limit


def rows(value):
    if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
        raise ValueError("Invalid feed collection")
    return value


def ident(value):
    if not isinstance(value, (str, int)) or isinstance(value, bool) or not str(value).strip():
        raise ValueError("Missing feed identity")
    return str(value)


def coordinates(value):
    try:
        lat, lon = float(value["latitude"]), float(value["longitude"])
        if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180 and (lat or lon):
            return lat, lon
    except (KeyError, TypeError, ValueError):
        pass
    return None


def status_value(feed, values):
    states = {STATUS_MAP[feed].get(v, "UNKNOWN") for v in values}
    return next(iter(states)) if len(states) == 1 else "UNKNOWN"


def source_refs(feed, operator):
    attribution = "Ajuntament de Barcelona / Endolla" if feed == "endolla" else "Spirii" + (f" / {operator}" if operator else "")
    return [{"name": f"{'Barcelona Endolla' if feed == 'endolla' else 'Spirii AFIR Spain'} {kind}",
             "url": PUBLIC_LIVE_URLS[f"{feed}-{kind}"], "attribution": attribution,
             **({"license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/"} if feed == "endolla" else {}),
             "evidence": "Official connector inventory and status; charging and parking prices are separate evidence.",
             "transformation": "Joined static identities to dynamic status and normalized connector/status fields by Volta Go; tariffs omitted."}
            for kind in ("status", "static")]


def station(feed, site_id, name, address, city, coords, operator, connectors):
    # Source keys are stable, independent of names, coordinates and current status.
    return {"id": f"live_{feed}_{site_id}", "name": name, "address": address, "city": city,
            "lat": coords[0], "lon": coords[1], "country_code": "ES", "owner": {"name": operator}, "operator": operator,
            "source": "Barcelona Endolla open data" if feed == "endolla" else "Spirii AFIR Spain",
            "source_url": PUBLIC_LIVE_URLS[f"{feed}-status"], "sources": source_refs(feed, operator),
            "connectors": connectors, "connector_labels": sorted({c["label"] for c in connectors}),
            "price_min": None, "is_free": False, "is_cheap": False, "lead": False,
            "parking": {"kind": "unknown", "note": "Parking costs are not published by this status feed."},
            "reliability": "official-live-status", "live_feed": feed, "live_site_id": site_id,
            "raw": {f"{feed}_location_id": site_id},
            "source_integration": {"mode": "public_live_status", "live_status_integrated": True},
            "fast": any((c.get("power_kw") or 0) >= 50 for c in connectors)}


def _endolla_ports(payload):
    locations = rows(payload["locations"])
    if not locations:
        raise ValueError("Empty Endolla feed")
    for loc in locations:
        lid = ident(loc.get("id"))
        for st in rows(loc.get("stations")):
            sid = ident(st.get("id"))
            for port in rows(st.get("ports")):
                yield (lid, sid, ident(port.get("id"))), loc, st, port


def parse_endolla(static, dynamic):
    status = {}
    for key, _, _, port in _endolla_ports(dynamic):
        if key in status:
            raise ValueError("Duplicate Endolla status identity")
        status[key] = port
    sites, seen = {}, set()
    labels = {"TYPE_2": "Type 2", "MENNEKES": "Type 2", "IEC_62196_T2": "Type 2", "CCS": "CCS2",
              "COMBO_2": "CCS2", "CCS_COMBO_2": "CCS2", "CCS_TYPE_2": "CCS2", "CCS2": "CCS2",
              "CHADEMO": "CHAdeMO", "WALL_OUTLET": "Wall outlet (connector unspecified)"}
    matched = 0
    for key, loc, st, port in _endolla_ports(static):
        if key in seen:
            raise ValueError("Duplicate Endolla static identity")
        seen.add(key)
        coords = coordinates(loc.get("coordinates"))
        if not coords or (loc.get("address") or {}).get("country_code") != "ES":
            continue
        observed = status.get(key) or {}
        matched += bool(observed)
        states = [x.get("status") for x in rows(observed.get("port_status", []))]
        changed = observed.get("last_updated")
        normalized = status_value("endolla", states) if epoch(changed) is not None else "UNKNOWN"
        connector = {"connector_id": ":".join(key), "location_id": key[0], "station_id": key[1], "port_id": key[2],
                     "status": normalized, "observed_status": normalized, "last_changed_at": changed,
                     "source_last_updated": changed, "source_url": PUBLIC_LIVE_URLS["endolla-status"],
                     "label": labels.get(str(port.get("connector_type")), "Unknown"),
                     "power_kw": _power(port.get("power_kw")), "count": 1}
        if key[0] not in sites:
            addr = loc.get("address") or {}
            sites[key[0]] = station("endolla", key[0], str(st.get("notes") or st.get("label") or "Endolla Barcelona"),
                                   str(addr.get("address_string") or ""), str(addr.get("locality") or "Barcelona"),
                                   coords, str(loc.get("network_name") or "Endolla Barcelona"), [])
            sites[key[0]]["access"] = {"level": "public" if loc.get("access_restriction") == "PUBLIC" else "unknown",
                                       "note": "Check source access restrictions and onsite signs."}
            sites[key[0]]["sources"][1]["source_last_updated"] = loc.get("last_updated")
        sites[key[0]]["connectors"].append(connector)
    result = _parsed(list(sites.values()), None, len(status), matched, len(set(status) - seen))
    result.update(static_sites=len(static["locations"]), static_stations=sum(len(loc["stations"]) for loc in static["locations"]))
    return result


def _power(value, divisor=1):
    try:
        n = float(value) / divisor
        return n if math.isfinite(n) and 0 < n <= 2000 else None
    except (TypeError, ValueError):
        return None


def text(value):
    return str(next((x.get("value") for x in rows((value or {}).get("values", [])) if x.get("value")), ""))


def parse_spirii(static, dynamic):
    pub = static["payload"]["aegiEnergyInfrastructureTablePublication"]
    publications = [p["aegiEnergyInfrastructureStatusPublication"] for p in rows(dynamic["messageContainer"]["payload"])]
    if not publications or any(epoch(p.get("publicationTime")) is None for p in publications):
        raise ValueError("Missing Spirii publication time")
    published = min((p["publicationTime"] for p in publications), key=epoch)
    statuses = {}
    for publication in publications:
        for site in rows(publication.get("energyInfrastructureSiteStatus")):
            for st in rows(site.get("energyInfrastructureStationStatus")):
                for wrapped in rows(st.get("refillPointStatus")):
                    point = wrapped["aegiElectricChargingPointStatus"]
                    key = ident(point["reference"].get("idG"))
                    value = (point.get("status") or {}).get("value")
                    changed = point.get("lastUpdated")
                    normalized = status_value("spirii", [value]) if epoch(changed) is not None else "UNKNOWN"
                    if key in statuses:
                        raise ValueError("Duplicate Spirii status identity")
                    statuses[key] = (normalized, changed)
    sites, seen, matched, static_sites, static_stations = [], set(), 0, 0, 0
    labels = {"iec62196T2COMBO": "CCS2", "iec62196T2": "Type 2", "chademo": "CHAdeMO", "domesticF": "Schuko"}
    for table in rows(pub.get("energyInfrastructureTable")):
        for site in rows(table.get("energyInfrastructureSite")):
            loc = site["locationReference"]["locAreaLocation"]
            addr = loc["locLocationExtensionG"]["FacilityLocation"]["address"]
            # Do not trust the query parameter or publicationCreator country.
            if addr.get("countryCode") != "ES":
                continue
            static_sites += 1
            static_stations += len(rows(site.get("energyInfrastructureStation")))
            coords = coordinates(loc.get("coordinatesForDisplay"))
            if not coords:
                continue
            operator = text(((site.get("operator") or {}).get("afacAnOrganisation") or {}).get("name")) or "Operator unreported"
            connectors = []
            for st in rows(site.get("energyInfrastructureStation")):
                for wrapped in rows(st.get("refillPoint")):
                    cp = wrapped["aegiElectricChargingPoint"]
                    key = ident(cp.get("idG"))
                    if key in seen:
                        raise ValueError("Duplicate Spirii static identity")
                    seen.add(key)
                    normalized, changed = statuses.get(key, ("UNKNOWN", None))
                    matched += key in statuses
                    evse_ids = [x["identifier"] for x in rows(cp.get("externalIdentifier", []))
                                if (x.get("typeOfIdentifier") or {}).get("extendedValueG") == "evseId" and x.get("identifier")]
                    # One refill point is one EVSE; multiple connector alternatives do not add available bays.
                    hardware = rows(cp.get("connector", []))
                    connectors.append({"connector_id": key, "evse_id": evse_ids[0] if evse_ids else None,
                                       "external_evse_ids": evse_ids, "station_id": st.get("idG"),
                                       "status": normalized, "observed_status": normalized, "last_changed_at": changed,
                                       "source_last_updated": changed, "source_url": PUBLIC_LIVE_URLS["spirii-status"],
                                       "label": labels.get((hardware[0].get("connectorType") or {}).get("value"), "Unknown") if hardware else "Unknown",
                                       "power_kw": max((_power(c.get("maxPowerAtSocket"), 1000) or 0 for c in hardware), default=0) or None,
                                       "count": 1})
            if connectors:
                sites.append(station("spirii", ident(site.get("idG")), text(site.get("name")) or "Spirii charger",
                                     ", ".join(text(x.get("text")) for x in rows(addr.get("addressLine", []))),
                                     text(addr.get("city")), coords, operator, connectors))
    result = _parsed(sites, published, len(statuses), matched, len(set(statuses) - seen))
    result.update(static_sites=static_sites, static_stations=static_stations,
                  static_publication_time=pub.get("publicationTime"))
    return result


def _parsed(stations, published, count, matched, unlocated):
    if not stations or not count:
        raise ValueError("Empty live feed")
    for row in stations:
        row["connector_labels"] = sorted({c["label"] for c in row["connectors"]})
        row["fast"] = any((c.get("power_kw") or 0) >= 50 for c in row["connectors"])
        changes = [c["last_changed_at"] for c in row["connectors"] if epoch(c.get("last_changed_at")) is not None]
        row["last_seen"] = row["source_last_updated"] = max(changes, key=epoch) if changes else None
        row["source_published_at"] = published
    return {"stations": stations, "publication_time": published, "raw_count": count,
            "matched_ports": matched, "unlocated_ports": unlocated}


def validate_http_freshness(headers, now):
    # HTTP Date validates a response; it is never the provider's publication time.
    date = headers.get("Date")
    response_at = parsedate_to_datetime(date).timestamp() if date else None
    age = float(headers.get("Age", "0"))
    if (not math.isfinite(age) or age < 0 or age > PUBLICATION_MAX_AGE
            or (response_at is not None and not -60 <= now - response_at <= PUBLICATION_MAX_AGE)):
        raise ValueError("Stale HTTP response")


def fetch_document(url, previous=None):
    """Bound both compressed and expanded bytes; preserve 304 entity timestamps."""
    headers = {"Accept": "application/json", "Accept-Encoding": "gzip", "User-Agent": "VoltaGo-public-live/1.0"}
    if previous and previous.get("last_modified"):
        headers["If-Modified-Since"] = previous["last_modified"]
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        response = public_live_urlopen(request, timeout=TIMEOUT)
    except urllib.error.HTTPError as exc:
        if exc.code != 304 or not previous:
            raise
        try:
            now = time.time()
            validate_http_freshness(exc.headers or {}, now)
            return dict(previous, validated_at=iso(now), not_modified=True)
        finally:
            exc.close()
    with response:
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("Feed exceeds byte bound")
        if response.headers.get("Content-Encoding", "").lower() == "gzip" or body[:2] == b"\x1f\x8b":
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
                body = stream.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ValueError("Expanded feed exceeds byte bound")
        now = time.time()
        validate_http_freshness(response.headers, now)
        document = json.loads(body)
        if url.startswith("https://api.spirii.com/"):
            document = _without_tariffs(document)
        return {"document": document, "fetched_at": iso(now), "validated_at": iso(now),
                "last_modified": response.headers.get("Last-Modified"), "not_modified": False}


def _without_tariffs(value):
    """Discard tariff subtrees immediately; never retain or interpret price schedules."""
    if isinstance(value, dict):
        return {key: _without_tariffs(child) for key, child in value.items()
                if key not in {"electricEnergy", "energyRateUpdate", "energyPrice", "tariff", "tariffs"}}
    if isinstance(value, list):
        return [_without_tariffs(child) for child in value]
    return value


class FeedCache:
    """Per-source single flight; immutable snapshots, bounded wait, exponential backoff."""
    def __init__(self, name):
        self.name = name
        self.lock = threading.Lock()
        self.flight = None
        self.documents = {}
        self.parsed = None
        self.error = None
        self.failures = 0
        self.next_refresh = 0.0
        self.generation = 0

    def get(self, force=False):
        # force is deliberately unable to bypass this feed's TTL/backoff/budget.
        now = time.time()
        with self.lock:
            if now < self.next_refresh:
                return self._snapshot(now, True)
            if self.flight:
                flight, owner = self.flight, False
            else:
                self.flight = flight = threading.Event()
                owner = True
        if not owner:
            flight.wait(2 * TIMEOUT + 1)
            with self.lock:
                return self._snapshot(time.time(), True)
        try:
            docs = dict(self.documents)
            for kind in ("static", "status"):
                prev = docs.get(kind)
                limit = STATIC_TTL if kind == "static" else STATUS_TTL
                if not prev or time.time() - (epoch(prev["validated_at"]) or 0) >= limit:
                    docs[kind] = fetch_document(PUBLIC_LIVE_URLS[f"{self.name}-{kind}"], prev)
            parser = parse_endolla if self.name == "endolla" else parse_spirii
            parsed = parser(docs["static"]["document"], docs["status"]["document"])
            # Reject a regressing publication rather than hiding an upstream rollback.
            if (self.parsed and parsed["publication_time"] and self.parsed["publication_time"]
                    and epoch(parsed["publication_time"]) < epoch(self.parsed["publication_time"])):
                raise ValueError("Feed publication regressed")
            with self.lock:
                self.documents, self.parsed, self.error, self.failures = docs, parsed, None, 0
                self.next_refresh = time.time() + STATUS_TTL
                self.generation += 1
        except Exception as exc:
            with self.lock:
                self.error = type(exc).__name__
                self.failures += 1
                self.next_refresh = time.time() + min(900, STATUS_TTL * 2 ** min(4, self.failures - 1))
        finally:
            with self.lock:
                self.flight = None
                flight.set()
        with self.lock:
            return self._snapshot(time.time(), False)

    def _snapshot(self, now, hit):
        payload = deepcopy(self.parsed or {"stations": [], "raw_count": 0})
        doc = self.documents.get("status") or {}
        static = self.documents.get("static") or {}
        valid = bool(self.parsed and not self.error
                     and recent(doc.get("validated_at"), now, STATUS_MAX_AGE)
                     and recent(static.get("validated_at"), now, STATIC_TTL)
                     and (self.name == "endolla" or recent(payload.get("publication_time"), now, PUBLICATION_MAX_AGE)))
        for row in payload["stations"]:
            row["source_fetched_at"] = doc.get("fetched_at")
            row["live_status"] = {"valid": valid, "checked_at": doc.get("validated_at"),
                                  "published_at": payload.get("publication_time"), "feed": self.name}
            row["cache_stale"] = not valid
            if not valid:
                row["_volta_stale_live_cache"] = True
            for connector in row["connectors"]:
                if not valid or (epoch(connector.get("last_changed_at")) or 0) > now + 60:
                    connector["status"] = "UNKNOWN"
            for ref in row["sources"]:
                kind = "status" if ref["url"] == PUBLIC_LIVE_URLS[f"{self.name}-status"] else "static"
                ref["source_fetched_at"] = (self.documents.get(kind) or {}).get("fetched_at")
                if kind == "status":
                    ref["source_published_at"] = payload.get("publication_time")
                    ref["source_last_updated"] = row["source_last_updated"]
                elif payload.get("static_publication_time"):
                    ref["source_published_at"] = payload["static_publication_time"]
        payload["cache"] = {"hit": hit, "stale": not valid, "generation": self.generation,
                            "populated_at": self.generation, "checked_at": doc.get("validated_at"),
                            "fetched_at": doc.get("fetched_at"), "publication_time": payload.get("publication_time"),
                            "retry_in_seconds": max(0, round(self.next_refresh - now)), "error": self.error,
                            "refreshing": self.flight is not None, "ttl_seconds": STATUS_TTL}
        if not valid:
            payload["warning"] = f"{self.name.title()} live status unavailable or stale; cached status cannot confirm availability."
        return payload

    def health(self):
        with self.lock:
            payload = self._snapshot(time.time(), True)
        operators = sorted({row["owner"]["name"] for row in payload["stations"]})
        return {"name": self.name, "has_payload": bool(payload["stations"]), "stations": len(payload["stations"]),
                "static_sites": payload.get("static_sites", 0), "static_stations": payload.get("static_stations", 0),
                "ports": payload["raw_count"], "matched_ports": payload.get("matched_ports", 0),
                "unlocated_ports": payload.get("unlocated_ports", 0), "cache": payload["cache"],
                "operators": operators,
                "sources": [ref for operator in (operators or [None]) for ref in source_refs(self.name, operator)]}


CACHES = {name: FeedCache(name) for name in ("endolla", "spirii")}


def fetch_public_live(force=False, enabled=None):
    selected = {key: cache for key, cache in CACHES.items() if enabled is None or key in enabled}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="public-live") as pool:
        results = dict(zip(selected, pool.map(lambda cache: cache.get(force=force), selected.values())))
    return {"stations": [r for p in results.values() for r in p["stations"]],
            "raw_count": sum(p["raw_count"] for p in results.values()), "feeds": {k: p["cache"] for k, p in results.items()},
            "warning": " ".join(p["warning"] for p in results.values() if p.get("warning"))}


def health():
    return {name: cache.health() for name, cache in CACHES.items()}
