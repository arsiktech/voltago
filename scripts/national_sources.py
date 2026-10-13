#!/usr/bin/env python3
"""Official national open-data adapters for worldwide candidate ingestion.

All transport is bounded, cached public GET. These are directory snapshots:
upstream status and free-text tariffs never become live availability or a price.
Each IRVE row is a charging point, not an independently counted station.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
import re
import tempfile
import time
import unicodedata
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

try:
    from .global_sources import FileArtifact, fetch_cached_file, finite_number, iso_timestamp, make_record, valid_coordinates
    from .ingest_sources import FetchArtifact
except ImportError:
    from global_sources import FileArtifact, fetch_cached_file, finite_number, iso_timestamp, make_record, valid_coordinates
    from ingest_sources import FetchArtifact


FRANCE_URL = "https://proxy.transport.data.gouv.fr/resource/consolidation-transport-irve-statique"
GERMANY_PAGE_URL = "https://www.bundesnetzagentur.de/DE/Fachthemen/ElektrizitaetundGas/E-Mobilitaet/DownloadundKontakt.html"
GERMANY_CSV_URL = "https://data.bundesnetzagentur.de/Bundesnetzagentur/DE/Fachthemen/ElektrizitaetundGas/E-Mobilitaet/Ladesaeulenregister_BNetzA_2026-09-01.csv"
HK_CLP_URL = "https://api.clp.com.hk/transDist/mgmtEVChargeInfra/v1/getEVChargerList"
AFDC_URL = "https://developer.nlr.gov/api/alt-fuel-stations/v1.json?fuel_type=ELEC&access=public&status=E&country=all&limit=all"
FRANCE_TERRITORIES = {"971": "GP", "972": "MQ", "973": "GF", "974": "RE", "975": "PM", "976": "YT",
                      "977": "BL", "978": "MF", "986": "WF", "987": "PF", "988": "NC"}

SOURCES = {
    "france_irve": {
        "name": "France IRVE official consolidation", "countries": ["FR", *FRANCE_TERRITORIES.values()],
        "url": FRANCE_URL, "license": "Licence Ouverte 2.0",
        "license_url": "https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
        "attribution": "transport.data.gouv.fr and original IRVE publishers", "record_unit": "charging_point",
        "cache_name": "france_irve.csv", "default": True,
    },
    "germany_bnetza": {
        "name": "Bundesnetzagentur charging register", "countries": ["DE"], "url": GERMANY_PAGE_URL,
        "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": "Bundesnetzagentur.de", "record_unit": "charging_facility",
        "cache_name": "germany_bnetza.csv", "default": True,
    },
    "hk_clp": {
        "name": "CLP Hong Kong charging stations", "countries": ["HK"], "url": HK_CLP_URL,
        "license": "DATA.GOV.HK terms and conditions",
        "license_url": "https://data.gov.hk/en/terms-and-conditions",
        "attribution": "CLP Power Hong Kong Limited; DATA.GOV.HK; Government of the Hong Kong SAR",
        "record_unit": "station", "cache_name": "hk_clp.json", "default": True,
    },
    "afdc": {
        "name": "AFDC official US and Canada station directory", "countries": ["US", "CA"], "url": AFDC_URL,
        "license": "NLR Developer Network terms of service", "license_url": "https://developer.nlr.gov/terms/",
        "attribution": "Alternative Fuels Data Center, U.S. Department of Energy and National Laboratory of the Rockies",
        "record_unit": "station", "cache_name": "afdc_us_ca.json", "default": False,
        "credential_env": ["NLR_API_KEY", "NREL_API_KEY"],
    },
}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _number(value: Any) -> float | None:
    return finite_number(value.replace(",", ".").strip()) if isinstance(value, str) else finite_number(value)


def _positive(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and number > 0 else None


def _true(value: Any) -> bool:
    return value is True or _text(value).casefold() in {"true", "1", "oui", "yes"}


def _key(value: Any) -> str:
    value = unicodedata.normalize("NFKD", _text(value)).casefold()
    return re.sub(r"[^a-z0-9]", "", value)


def csv_rows(handle: io.TextIOBase, *, required: tuple[str, ...]) -> Iterator[dict]:
    """Locate a CSV header after up to 50 explanatory/preamble lines.

    BNetzA has shipped semicolon CSV with German decimal commas and a preamble;
    IRVE uses comma CSV with quoted coordinate arrays. A wrong schema fails loud.
    """
    for _ in range(50):
        line = handle.readline()
        if not line:
            break
        for delimiter in (";", ",", "\t"):
            fields = next(csv.reader([line.lstrip("\ufeff")], delimiter=delimiter))
            keys = {_key(field) for field in fields}
            if all(_key(key) in keys for key in required):
                reader = csv.DictReader(handle, fieldnames=fields, delimiter=delimiter)
                for row in reader:
                    if None in row:
                        raise ValueError("CSV row has more columns than its header")
                    yield {str(key).strip(): value for key, value in row.items()}
                return
    raise ValueError("Source CSV does not contain the expected schema header")


def _annotate(record: dict | None, source_id: str, *, access: str = "", note: str = "") -> dict | None:
    if record is None:
        return None
    metadata = SOURCES[source_id]
    record["sources"][0].update(license_url=metadata["license_url"], attribution=metadata["attribution"])
    record["source_record_unit"] = metadata["record_unit"]
    if access:
        record["access"] = {"level": access, "label": "Public access" if access == "public" else "Check access conditions", "note": note}
    return record


def _france_coordinates(row: dict) -> tuple[float | None, float | None]:
    for latitude, longitude in (("station_latitude", "station_longitude"), ("Ylatitude", "Xlongitude"),
                                ("ylatitude", "xlongitude"), ("latitude", "longitude")):
        lat, lon = _number(row.get(latitude)), _number(row.get(longitude))
        if valid_coordinates(lat, lon):
            return lat, lon
    coords = row.get("coordonneesXY") or row.get("coordonneesxy") or row.get("coordonnees_xy")
    if isinstance(coords, str):
        try:
            coords = json.loads(coords)
        except (ValueError, TypeError):
            return None, None
    if isinstance(coords, list) and len(coords) == 2:
        lon, lat = map(_number, coords)
        if valid_coordinates(lat, lon):
            return lat, lon
    return None, None


def parse_france_irve(rows: Iterable[dict], artifact: FetchArtifact) -> Iterator[dict]:
    """Normalize documented IRVE 2.x/consolidated fields and 3.x coordinates."""
    for row in rows:
        upstream_id = _text(row.get("id_pdc_itinerance"))
        if not upstream_id or upstream_id.casefold() in {"non concerne", "non concerné", "n/a"}:
            # Local identifiers are only unique within their publisher/operator.
            local_id = _text(row.get("id_pdc_local"))
            namespace = _text(row.get("siren_amenageur") or row.get("nom_operateur"))
            if not local_id or not namespace:
                continue
            upstream_id = "local_" + hashlib.sha256(f"{namespace}:{local_id}".encode()).hexdigest()[:24]
        lat, lon = _france_coordinates(row)
        country = FRANCE_TERRITORIES.get(_text(row.get("code_insee_commune"))[:3], "FR")
        power = _positive(row.get("puissance_nominale"))
        connectors = []
        for key, label in (("prise_type_2", "Type 2"), ("prise_type_combo_ccs", "CCS2"),
                           ("prise_type_chademo", "CHAdeMO"), ("prise_type_ef", "Type E/F")):
            if _true(row.get(key)):
                connectors.append({"connector_id": f"{upstream_id}:{key}", "label": label, "type": label,
                                   "status": "UNKNOWN", "power_kw": power, "count": 1,
                                   "raw": {"upstream_field": key, "power_scope": "charging_point"}})
        # Keep unknown connector information without assigning a connector type.
        raw_keys = ("id_pdc_itinerance", "id_pdc_local", "id_station_itinerance", "id_station_local", "nbre_pdc",
                    "code_insee_commune", "nom_amenageur", "nom_operateur", "nom_enseigne", "gratuit", "tarification",
                    "condition_acces", "horaires", "restriction_gabarit", "accessibilite_pmr", "prise_type_autre",
                    "date_maj", "consolidated_is_lon_lat_correct", "consolidated_is_code_insee_verified", "source", "source_url")
        raw = {key: row[key] for key in raw_keys if key in row}
        record = make_record(source_id="france_irve", upstream_id=upstream_id,
                             source=SOURCES["france_irve"]["name"], source_url=FRANCE_URL,
                             name=row.get("nom_station") or row.get("nom_enseigne"), lat=lat, lon=lon,
                             country_code=country, artifact=artifact, connectors=connectors, raw=raw,
                             address=row.get("adresse_station"), city=row.get("consolidated_commune") or row.get("nom_commune"),
                             operator=row.get("nom_operateur") or row.get("nom_enseigne"),
                             source_updated_at=row.get("date_maj"), license_name=SOURCES["france_irve"]["license"],
                             tariff={"currency": "EUR", "raw_text": _text(row.get("tarification")),
                                     "free_reported": row.get("gratuit"), "structured": False})
        access = "public" if _key(row.get("condition_acces")) == "acceslibre" else "unknown"
        record = _annotate(record, "france_irve", access=access,
                           note="; ".join(_text(row.get(key)) for key in ("condition_acces", "horaires") if row.get(key)))
        if record:
            record["total_evse"] = 1
            record["upstream_station_id"] = _text(row.get("id_station_itinerance") or row.get("id_station_local"))
            yield record


def _german_connector_labels(value: Any) -> list[str]:
    text = _text(value).casefold()
    labels = []
    if "combo" in text or "ccs" in text:
        labels.append("CCS2")
    if "chademo" in text:
        labels.append("CHAdeMO")
    # 'DC Kupplung Combo, AC Steckdose Typ 2' has two connector standards.
    if re.search(r"(?:ac[^,;]*typ\s*2|^typ\s*2|type\s*2|mennekes)", text):
        labels.append("Type 2")
    if "schuko" in text:
        labels.append("Schuko")
    return labels


def parse_germany_bnetza(rows: Iterable[dict], artifact: FetchArtifact) -> Iterator[dict]:
    for row in rows:
        data = {_key(key): value for key, value in row.items()}
        lat, lon = _number(data.get("breitengrad")), _number(data.get("langengrad"))
        operator, city = _text(data.get("betreiber")), _text(data.get("ort"))
        street_name = _text(data.get("strasse") or data.get("strae"))
        street = " ".join(filter(None, (street_name, _text(data.get("hausnummer")))))
        connectors = []
        for key, value in data.items():
            match = re.fullmatch(r"steckertypen(\d+)", key)
            if not match:
                continue
            port = match[1]
            power = _positive(data.get(f"p{port}kw") or data.get(f"p{port}") or data.get(f"nennleistungstecker{port}"))
            for label in _german_connector_labels(value):
                connectors.append({"connector_id": f"{port}:{label}", "label": label, "type": label,
                                   "status": "UNKNOWN", "power_kw": power, "count": 1,
                                   "raw": {"steckertypen": value, "charging_point": port}})
        native_id = _text(data.get("ladesaulennr") or data.get("ladesaulennummer") or data.get("ladeeinrichtungsid"))
        if not native_id:
            # Stable across row order; exact co-located indistinguishable rows
            # remain a documented source limitation, never a guessed identity.
            identity = {key: value for key, value in data.items() if key in {
                "betreiber", "strae", "strasse", "hausnummer", "postleitzahl", "ort", "breitengrad", "langengrad",
                "inbetriebnahmedatum", "anzahlladepunkte", "artderladeeinrichtung"} or key.startswith(("steckertypen", "publickey"))}
            native_id = "derived_" + hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        raw = {key: value for key, value in row.items() if value not in (None, "")}
        record = make_record(source_id="germany_bnetza", upstream_id=native_id,
                             source=SOURCES["germany_bnetza"]["name"], source_url=GERMANY_PAGE_URL,
                             name=" · ".join(filter(None, (operator, street))) or "Charging station", lat=lat, lon=lon,
                             country_code="DE", artifact=artifact, connectors=connectors, raw=raw,
                             address=street, city=city, operator=operator,
                             license_name=SOURCES["germany_bnetza"]["license"], tariff={"currency": "EUR", "structured": False})
        record = _annotate(record, "germany_bnetza", access="public", note="Official register of publicly accessible charging facilities; check local conditions.")
        if record:
            count = _positive(data.get("anzahlladepunkte"))
            record["total_evse"] = int(count) if count and count.is_integer() else None
            yield record


def parse_hk_clp(payload: Any, artifact: FetchArtifact) -> Iterator[dict]:
    if not isinstance(payload, dict) or str(payload.get("code")) != "200":
        raise ValueError("CLP returned an unsuccessful or unrecognized response")
    data = payload.get("data")
    rows = data.get("geoLocationResult") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("CLP response lacks geoLocationResult")
    for row in rows:
        if not isinstance(row, dict):
            continue
        upstream_id = _text(row.get("itemId"))
        # Quick/SemiQuick are speed tiers, not physical connector standards.
        chargers = row.get("chargerList") if isinstance(row.get("chargerList"), list) else []
        raw = {key: row[key] for key in ("itemId", "provider", "status", "lastUpdate", "totalNumberOfCharger") if key in row}
        raw["chargerList"] = [{key: charger[key] for key in ("cpNo", "itemId", "cpStatus", "chargerType", "lastUpdate", "pointName") if key in charger}
                              for charger in chargers if isinstance(charger, dict)]
        record = make_record(source_id="hk_clp", upstream_id=upstream_id, source=SOURCES["hk_clp"]["name"],
                             source_url=HK_CLP_URL, name=row.get("title"), lat=_number(row.get("latitude")), lon=_number(row.get("longitude")),
                             country_code="HK", artifact=artifact, connectors=[], raw=raw,
                             address=row.get("detailedAddress"), city="Hong Kong", operator=row.get("provider"),
                             source_updated_at=row.get("lastUpdate"), license_name=SOURCES["hk_clp"]["license"],
                             tariff={"currency": "HKD", "structured": False})
        record = _annotate(record, "hk_clp")
        if record:
            count = _positive(row.get("totalNumberOfCharger"))
            record["total_evse"] = int(count) if count and count.is_integer() else None
            yield record


AFDC_CONNECTORS = {"J1772": "Type 1", "J1772COMBO": "CCS1", "TESLA": "NACS", "CHADEMO": "CHAdeMO",
                   "J3271": "MCS", "NEMA1450": "NEMA 14-50", "NEMA515": "NEMA 5-15", "NEMA520": "NEMA 5-20"}


def _afdc_rows(payload: Any) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("fuel_stations"), list):
        raise ValueError("AFDC response lacks fuel_stations")
    total = payload.get("total_results")
    if not isinstance(total, int) or isinstance(total, bool) or total != len(payload["fuel_stations"]):
        raise ValueError("AFDC all-stations response is incomplete")
    return payload["fuel_stations"]


def parse_afdc(payload: Any, artifact: FetchArtifact) -> Iterator[dict]:
    """AFDC active directory records preserve native currencies and access limits."""
    for row in _afdc_rows(payload):
        if not isinstance(row, dict) or row.get("fuel_type_code") != "ELEC" or row.get("access_code") != "public" or row.get("status_code") != "E":
            continue
        country = _text(row.get("country")).upper()
        identifier = row.get("id")
        if country not in {"US", "CA"} or not isinstance(identifier, int) or isinstance(identifier, bool) or identifier <= 0:
            continue
        connectors = []
        units = row.get("ev_charging_units") if isinstance(row.get("ev_charging_units"), list) else []
        unit_ports = 0
        for index, unit in enumerate(units):
            if not isinstance(unit, dict) or not isinstance(unit.get("connectors"), dict):
                continue
            unit_count = _positive(unit.get("port_count"))
            unit_ports += int(unit_count) if unit_count and unit_count.is_integer() else 0
            for token, detail in unit["connectors"].items():
                if token not in AFDC_CONNECTORS or not isinstance(detail, dict):
                    continue
                count = _positive(detail.get("port_count"))
                if not count or not count.is_integer():
                    continue
                label = AFDC_CONNECTORS[token]
                connectors.append({"connector_id": f"{index}:{token}", "label": label, "type": label,
                                   "status": "UNKNOWN", "power_kw": _positive(detail.get("power_kw")), "count": int(count),
                                   "raw": {"charging_unit": index, "connector_type": token}})
        # Station-wide connector lists contain types but no per-type counts.
        known = {connector["label"] for connector in connectors}
        for token in row.get("ev_connector_types") or []:
            label = AFDC_CONNECTORS.get(token)
            if label and label not in known:
                connectors.append({"connector_id": token, "label": label, "type": label, "status": "UNKNOWN",
                                   "power_kw": None, "count": None, "raw": {"connector_type": token}})
                known.add(label)
        raw = {key: row[key] for key in ("id", "country", "state", "zip", "status_code", "access_code", "access_detail_code",
                                        "restricted_access", "access_days_time", "ev_network", "ev_pricing", "ev_pricing_fr",
                                        "updated_at", "date_last_confirmed", "geocode_status", "ev_network_ids", "ev_charging_units") if key in row}
        source_url = f"https://afdc.energy.gov/stations/#/station/{identifier}"
        record = make_record(source_id="afdc", upstream_id=str(identifier), source=SOURCES["afdc"]["name"],
                             source_url=source_url, name=row.get("station_name"), lat=_number(row.get("latitude")), lon=_number(row.get("longitude")),
                             country_code=country, artifact=artifact, connectors=connectors, raw=raw,
                             address=row.get("street_address"), city=row.get("city"), operator=row.get("ev_network"),
                             source_updated_at=row.get("updated_at") or row.get("date_last_confirmed"), license_name=SOURCES["afdc"]["license"],
                             tariff={"currency": "USD" if country == "US" else "CAD", "structured": False,
                                     "raw_text": _text(row.get("ev_pricing") or row.get("ev_pricing_fr"))})
        restricted = row.get("restricted_access") is True or bool(row.get("access_detail_code"))
        record = _annotate(record, "afdc", access="restricted" if restricted else "public",
                           note="; ".join(_text(row.get(key)) for key in ("access_detail_code", "access_days_time") if row.get(key)))
        if record:
            station_counts = [_positive(row.get(key)) for key in ("ev_level1_evse_num", "ev_level2_evse_num", "ev_dc_fast_num")]
            record["total_evse"] = unit_ports or sum(int(count) for count in station_counts if count and count.is_integer()) or None
            geocode = _text(row.get("geocode_status"))
            record["location_claim"]["precision"] = "source_coordinate" if geocode in {"GPS", "200-9", "200-8"} else "approximate"
            record["location_claim"]["upstream_geocode_status"] = geocode
            yield record


class _AFDCNoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise urllib.error.HTTPError(AFDC_URL, code, "AFDC redirects are not permitted", headers, fp)


def _fetch_afdc(cache_dir: Path, *, api_key: str, offline: bool) -> FileArtifact:
    """Single exact documented credentialed GET; secrets never enter artifacts.

    AFDC's official API supports X-Api-Key. This narrow downloader rejects every
    redirect and uses an unredirected header as defense in depth. Errors retain
    exception types only; upstream bodies and request headers are never logged.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / SOURCES["afdc"]["cache_name"]
    ttl = 168 * 3600

    def cached(*, fallback: bool = False, error: str | None = None) -> FileArtifact:
        age = max(0, time.time() - path.stat().st_mtime)
        return FileArtifact(path, FetchArtifact(b"", AFDC_URL, path.name, iso_timestamp(path.stat().st_mtime), age,
                                               True, fallback, age >= ttl, error))

    if path.is_file() and path.stat().st_size and time.time() - path.stat().st_mtime < ttl:
        return cached()
    if offline:
        if path.is_file() and path.stat().st_size:
            return cached(fallback=True, error="offline_cache_only")
        raise FileNotFoundError("No cached AFDC source")
    if not api_key or len(api_key) > 256 or any(ord(char) < 33 or ord(char) > 126 for char in api_key):
        raise ValueError("AFDC key must be a nonempty printable token")
    temporary: Path | None = None
    try:
        request = urllib.request.Request(AFDC_URL, headers={"User-Agent": "Volta public-source EV charger research", "Accept": "application/json"}, method="GET")
        request.add_unredirected_header("X-Api-Key", api_key)
        opener = urllib.request.build_opener(_AFDCNoRedirect())
        limit = 256 * 1024 * 1024
        secret = api_key.encode("ascii")
        with opener.open(request, timeout=60) as response:
            expected = response.headers.get("Content-Length")
            if expected and int(expected) > limit:
                raise ValueError("AFDC response exceeds byte limit")
            with tempfile.NamedTemporaryFile(dir=cache_dir, prefix=".afdc-", delete=False) as output:
                temporary = Path(output.name)
                total, tail = 0, b""
                while True:
                    chunk = response.read(min(1024 * 1024, limit - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit or secret in tail + chunk:
                        raise ValueError("AFDC response cannot be safely cached")
                    tail = (tail + chunk)[-len(secret):]
                    output.write(chunk)
                if not total or (expected and total != int(expected)):
                    raise ValueError("AFDC response is empty or truncated")
        with temporary.open(encoding="utf-8-sig") as handle:
            _afdc_rows(json.load(handle))
        os.replace(temporary, path)
        return FileArtifact(path, FetchArtifact(b"", AFDC_URL, path.name, iso_timestamp(path.stat().st_mtime), 0, False, False, False))
    except Exception as exc:
        if path.is_file() and path.stat().st_size:
            return cached(fallback=True, error=type(exc).__name__)
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def resolve_germany_csv(page: str) -> str:
    """Accept only dated register CSV links hosted by the official data server."""
    links = []
    for href in re.findall(r"href\s*=\s*[\"']([^\"']+)[\"']", page, flags=re.I):
        url = urllib.parse.urljoin(GERMANY_PAGE_URL, html.unescape(href))
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme == "https" and parsed.hostname == "data.bundesnetzagentur.de" and
                not parsed.username and not parsed.password and not parsed.query and not parsed.fragment and
                re.fullmatch(r"/Bundesnetzagentur/DE/Fachthemen/ElektrizitaetundGas/E-Mobilitaet/Ladesaeulenregister_BNetzA_\d{4}-\d{2}-\d{2}\.csv", parsed.path)):
            links.append(url)
    if not links:
        raise ValueError("Official BNetzA download page has no recognized CSV link")
    return max(links)


def _csv_file(path: Path, *, required: tuple[str, ...]) -> Iterator[dict]:
    # Determine encoding without materializing the full national file. BNetzA
    # historically uses Windows-1252; IRVE uses UTF-8, optionally with a BOM.
    with path.open("rb") as handle:
        prefix = handle.read(128 * 1024)
    try:
        # A bounded read may end inside a multibyte character. The incremental
        # decoder validates complete characters without misclassifying that tail.
        import codecs
        codecs.getincrementaldecoder("utf-8-sig")().decode(prefix, final=False)
        encoding = "utf-8-sig"
    except UnicodeDecodeError:
        encoding = "cp1252"
    with path.open("r", encoding=encoding, newline="") as handle:
        yield from csv_rows(handle, required=required)


def run_source(source_id: str, cache_dir: Path, countries: set[str] | None, offline: bool,
               emit: Callable[[dict], Any]) -> dict:
    """Fetch and emit a complete national source or return a truthful failure.

    The caller stages the candidate and applies promotion checks. This function
    never changes the active database, and absence of a cache is not success.
    """
    metadata = SOURCES[source_id]
    report = {"source": source_id, "source_id": source_id, "records": 0, "record_unit": metadata["record_unit"],
              "license": metadata["license"], "attribution": metadata["attribution"], "live_status": False,
              "scope": sorted(set(metadata["countries"]) & countries) if countries is not None else list(metadata["countries"]),
              "seen": 0, "accepted": 0, "rejected": 0, "duplicates": 0, "errors": [], "fetches": []}
    if countries is not None and not set(metadata["countries"]) & countries:
        return dict(report, status="not_applicable", reason="outside_requested_countries")
    cache_dir = Path(cache_dir)
    api_key = ""
    if source_id == "afdc":
        api_key = os.environ.get("NLR_API_KEY") or os.environ.get("NREL_API_KEY") or ""
        if not api_key:
            report["errors"].append("NLR_API_KEY_or_NREL_API_KEY_required")
            return dict(report, status="needs_credentials")
    try:
        source_url = metadata["url"]
        cache_name = metadata["cache_name"]
        discovery = None
        if source_id == "germany_bnetza":
            # Offline runs can use a reviewed imported CSV without a page cache.
            if offline and not (cache_dir / "germany_bnetza_page.html").is_file():
                imports = sorted(path for path in cache_dir.glob("germany_bnetza_*.csv")
                                 if re.fullmatch(r"germany_bnetza_\d{4}-\d{2}-\d{2}\.csv", path.name))
                date = imports[-1].stem.removeprefix("germany_bnetza_") if imports else "2026-09-01"
                source_url = GERMANY_CSV_URL.replace("2026-09-01", date)
            else:
                discovery = fetch_cached_file(GERMANY_PAGE_URL, cache_dir, "germany_bnetza_page.html", max_bytes=4 * 1024 * 1024, offline=offline)
                source_url = resolve_germany_csv(discovery.path.read_text(encoding="utf-8"))
            date = Path(urllib.parse.urlsplit(source_url).path).stem.removeprefix("Ladesaeulenregister_BNetzA_")
            cache_name = f"germany_bnetza_{date}.csv"
        maximum = 16 * 1024 * 1024 if source_id == "hk_clp" else 512 * 1024 * 1024
        fetched = (_fetch_afdc(cache_dir, api_key=api_key, offline=offline) if source_id == "afdc" else
                   fetch_cached_file(source_url, cache_dir, cache_name, max_bytes=maximum, offline=offline))
        report["fetches"].append(fetched.artifact.as_evidence())

        def counted(items: Iterable[dict]) -> Iterator[dict]:
            for item in items:
                report["seen"] += 1
                yield item

        if source_id == "france_irve":
            rows = parse_france_irve(counted(_csv_file(fetched.path, required=("nom_station", "id_pdc_itinerance"))), fetched.artifact)
        elif source_id == "germany_bnetza":
            rows = parse_germany_bnetza(counted(_csv_file(fetched.path, required=("Betreiber", "Breitengrad", "Längengrad"))), fetched.artifact)
        elif source_id == "hk_clp":
            with fetched.path.open(encoding="utf-8-sig") as handle:
                payload = json.load(handle)
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, dict) and isinstance(data.get("geoLocationResult"), list):
                report["seen"] = len(data["geoLocationResult"])
            rows = parse_hk_clp(payload, fetched.artifact)
        else:
            with fetched.path.open(encoding="utf-8-sig") as handle:
                payload = json.load(handle)
            report["seen"] = len(_afdc_rows(payload))
            rows = parse_afdc(payload, fetched.artifact)
        for row in rows:
            if countries is None or row["country_code"] in countries:
                outcome = emit(row)
                if outcome == "added":
                    report["accepted"] += 1
                elif outcome == "duplicate":
                    report["duplicates"] += 1
                report["records"] += 1
        report["rejected"] = report["seen"] - report["accepted"] - report["duplicates"]
        report.update(status="stale" if fetched.artifact.stale else "ok", fetch=fetched.artifact.as_evidence(),
                      source_url=source_url, stale=fetched.artifact.stale)
        if discovery is not None:
            report["discovery_fetch"] = discovery.artifact.as_evidence()
            report["fetches"].append(discovery.artifact.as_evidence())
        if not report["records"]:
            report.update(status="failed", reason="no_accepted_records_in_scope")
            report["errors"].append("no_accepted_records_in_scope")
    except (OSError, ValueError, csv.Error) as exc:
        # URLs, content, and possible upstream credentials are not copied into
        # reports; exception class gives a safe, useful failure category.
        report["rejected"] = report["seen"] - report["accepted"] - report["duplicates"]
        report["errors"].append(type(exc).__name__)
        report.update(status="partial" if report["records"] else "failed", error=type(exc).__name__, partial=bool(report["records"]))
    return report
