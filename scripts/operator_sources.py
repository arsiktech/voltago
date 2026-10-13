"""Public operator directories adapted from the Spain source work.

Records retain upstream IDs, per-artifact provenance and unknown tariffs. A whole
source is parsed and validated before it can add rows to the worldwide candidate.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from . import global_sources as gs
    from .ingest_sources import FetchArtifact, connector_ref, in_bbox, SPAIN_BBOX
    from .spain_geography import point_in_spain
except ImportError:
    import global_sources as gs
    from ingest_sources import FetchArtifact, connector_ref, in_bbox, SPAIN_BBOX
    from spain_geography import point_in_spain
from station_truth import normalize_connector_label as connector_label_from_token

fnum = gs.finite_number

def likely_in_spain(lat: Optional[float], lon: Optional[float]) -> bool:
    return point_in_spain(lat, lon)

def ocpi_power_kw(connector: Dict[str, Any]) -> Optional[float]:
    watts = fnum(connector.get("max_electric_power"))
    if not watts:
        return None
    return watts / 1000 if watts > 0 else None

OPERATOR_FEED_PARKING_NOTE = "The operator's public map publishes no parking detail; check the signs onsite."

SPAIN_COUNTRY_CODES = {"ES", "ESP", "IC", "EA", "ESPAÑA", "ESPANA", "SPAIN"}

KNOWN_CONNECTOR_LABELS = {"Type 2", "CCS2", "CHAdeMO", "Tesla", "Schuko", "Type 1"}

def point_in_declared_spain(lat: Optional[float], lon: Optional[float], declared_country: Any = None) -> bool:
    """An explicit Spanish country declaration is authoritative inside the broad Spain extent.

    The coastline outline is coarse; a source that itself says ES/IC/EA must not lose
    border-area stations to it. Another declared country is never Spain. With no
    declaration, geometry alone decides.
    """
    country = str(declared_country or "").strip().upper()
    if country in SPAIN_COUNTRY_CODES:
        return in_bbox(lat, lon, SPAIN_BBOX)
    if country:
        return False
    return likely_in_spain(lat, lon)

def known_connector_label(token: Any) -> Optional[str]:
    """A recognised connector family, or None. Arbitrary tokens are never title-cased into a type."""
    label = connector_label_from_token(token)
    return label if label in KNOWN_CONNECTOR_LABELS else None

def published_connector_labels(connectors: List[Dict[str, Any]]) -> List[str]:
    """Labels for add_record that exclude "Unknown connector"; [] (not None) so no Type 2 default applies."""
    return sorted({c["label"] for c in connectors if c.get("label") in KNOWN_CONNECTOR_LABELS})

ETECNIC_SOURCE = "Etecnic EVcharge public charger map"

ETECNIC_FEED_URL = "https://etecnic.net/api/v1/chargers/index.json"

ETECNIC_MAP_PAGE_URL = "https://etecnic.es/mapa-de-recarga/"

def etecnic_network(name: str) -> str:
    """Network membership only when the row's own name says so; region never implies it."""
    if re.search(r"\bMELIB\b", name or "", re.I):
        return "MELIB (Govern de les Illes Balears), Etecnic platform"
    return "Etecnic platform (operator not established)"

def parse_etecnic_chargers(rows: List[Any], *, source_artifact: Optional[FetchArtifact] = None) -> List[Dict[str, Any]]:
    parsed: List[Dict[str, Any]] = []
    skipped: Dict[str, int] = defaultdict(int)
    for row in rows:
        if not isinstance(row, dict):
            skipped["not_object"] += 1
            continue
        if row.get("show_map") is False:
            skipped["hidden_from_operator_map"] += 1
            continue
        lat, lon = fnum(row.get("lat")), fnum(row.get("lon"))
        if not likely_in_spain(lat, lon):
            skipped["outside_spain"] += 1
            continue
        sockets = [s for s in (row.get("charger_sockets") or []) if isinstance(s, dict)]
        type_counts: Dict[Any, int] = defaultdict(int)
        for socket in sockets:
            type_counts[socket.get("connector_type_id")] += 1
        connectors = [
            connector_ref(
                "Unknown connector",
                count=count,
                source_url=ETECNIC_MAP_PAGE_URL,
                connector_id=type_id,
                raw={"etecnic_connector_type_id": type_id},
            )
            for type_id, count in sorted(type_counts.items(), key=lambda kv: str(kv[0]))
        ]
        name = str(row.get("name") or "").strip() or "Etecnic charger"
        network = etecnic_network(name)
        add_record(
            parsed,
            source=ETECNIC_SOURCE,
            source_url=ETECNIC_MAP_PAGE_URL,
            name=name,
            address=str(row.get("address") or ""),
            city="",
            lat=lat,
            lon=lon,
            price_min=None,
            free_condition="The Etecnic public map publishes no tariff; check the price in the operator app.",
            connector_labels=[],  # connector ids have no public legend; never default to Type 2
            max_power_kw=None,
            status="VERIFY",
            reliability="directory-page",
            evidence=(
                f"Etecnic public charger map ({network}): {len(sockets)} sockets, connector types not published. "
                "Socket status is a cached snapshot from the operator map, never live availability."
            ),
            parking_kind="unknown",
            parking_note=OPERATOR_FEED_PARKING_NOTE,
            raw={
                "etecnic_charger_id": row.get("id"),
                "network": network,
                "status_code": row.get("status"),
                "socket_status_codes": [s.get("status") for s in sockets],
                "power_raw": row.get("power"),
                "phases": row.get("phases"),
                "mobile_app_manage": row.get("mobile_app_manage"),
                "source": "etecnic-public-map",
            },
            connectors=connectors,
            port_count=len(sockets) or None,
            source_artifact=source_artifact,
        )
    print(f"etecnic: {len(parsed)} rows from {len(rows)} chargers (skipped: {dict(skipped)})")
    return parsed

ATLANTE_SOURCE = "Atlante public charger map"

ATLANTE_FEED_URL = "https://map.atlante.energy/geodata.json"

ATLANTE_MAP_PAGE_URL = "https://map.atlante.energy/"

ATLANTE_NOT_OPERATIONAL = {"WAITING FOR CONNECTION", "UNDER CONSTRUCTION"}

def parse_atlante_locations(rows: List[Any], *, source_artifact: Optional[FetchArtifact] = None) -> List[Dict[str, Any]]:
    parsed: List[Dict[str, Any]] = []
    skipped: Dict[str, int] = defaultdict(int)
    for row in rows:
        if not isinstance(row, dict) or str(row.get("country") or "").upper() not in {"ES", "ESP", "FR", "FRA", "IT", "ITA", "PT", "PRT"}:
            skipped["not_spain"] += 1
            continue
        if str(row.get("site_status") or "").upper() in ATLANTE_NOT_OPERATIONAL:
            skipped["not_operational"] += 1
            continue
        evses = [evse for evse in (row.get("evses") or []) if isinstance(evse, dict)]
        if not evses:
            skipped["no_evses"] += 1
            continue
        coords = row.get("coordinates") or (evses[0].get("coordinates") or {})
        lat, lon = fnum(coords.get("latitude")), fnum(coords.get("longitude"))
        if not gs.valid_coordinates(lat, lon):
            skipped["outside_spain"] += 1
            continue
        connectors: List[Dict[str, Any]] = []
        powers: List[float] = []
        status_counts: Dict[str, int] = defaultdict(int)
        for evse in evses:
            status = str(evse.get("status") or "UNKNOWN").upper()
            status_counts[status] += 1
            for connector in evse.get("connectors") or []:
                if not isinstance(connector, dict):
                    continue
                power = ocpi_power_kw(connector)
                if power:
                    powers.append(power)
                standard = str(connector.get("standard") or "")
                connectors.append(connector_ref(
                    known_connector_label(standard) or "Unknown connector",
                    power_kw=power,
                    status="UNKNOWN",
                    source_url=ATLANTE_MAP_PAGE_URL,
                    connector_id=connector.get("id"),
                    raw={"evse_id": evse.get("evse_id"), "standard": standard, "format": connector.get("format")},
                ))
        address = ", ".join(p for p in (str(row.get("street") or "").strip(), str(row.get("postal_code") or "").strip(), str(row.get("city") or "").strip()) if p)
        status_summary = ", ".join(f"{n} {s.lower()}" for s, n in sorted(status_counts.items()))
        add_record(
            parsed,
            source=ATLANTE_SOURCE,
            source_url=ATLANTE_MAP_PAGE_URL,
            name=re.sub(r"\s+", " ", str(row.get("name") or "Atlante charger")).strip(),
            address=address,
            city=str(row.get("city") or ""),
            lat=lat,
            lon=lon,
            price_min=None,
            free_condition="Atlante's public map publishes no tariff; check the price in the operator app.",
            connector_labels=published_connector_labels(connectors),  # [] when none recognised: no Type 2 default
            max_power_kw=max(powers) if powers else None,
            status="VERIFY",
            reliability="directory-page",
            evidence=(
                f"Atlante public charger map: {len(evses)} EVSEs ({status_summary}). "
                "EVSE status is a cached snapshot from the operator map, never live availability."
            ),
            parking_kind="unknown",
            parking_note=OPERATOR_FEED_PARKING_NOTE,
            raw={
                "atlante_location_id": row.get("id"),
                "country": {"ESP": "ES", "FRA": "FR", "ITA": "IT", "PRT": "PT"}.get(str(row.get("country") or "").upper(), str(row.get("country") or "").upper()),
                "party_id": row.get("party_id"),
                "postal_code": row.get("postal_code"),
                "evse_ids": [evse.get("evse_id") for evse in evses if evse.get("evse_id")][:12],
                "evse_status_counts": dict(sorted(status_counts.items())),
                "updated_at": row.get("last_updated"),
                "source": "atlante-public-geodata",
            },
            connectors=connectors,
            port_count=len(connectors) or None,
            source_artifact=source_artifact,
        )
    print(f"atlante: {len(parsed)} rows from {len(rows)} locations (skipped: {dict(skipped)})")
    return parsed

ZUNDER_SOURCE = "Zunder public charger map"

ZUNDER_MAP_PAGE_URL = "https://www.zunder.com/mapa-de-ubicaciones/"

ZUNDER_LOCATION_URL = "https://www.zunder.com/wp-content/plugins/easycharger/location.php?id={id}"

ZUNDER_MARKER_RE = re.compile(
    r"new\s+google\.maps\.Marker\(\{\s*position:\s*\{\s*lat:\s*(-?\d+(?:\.\d+)?),\s*lng:\s*(-?\d+(?:\.\d+)?)\s*\}"
    r".*?title:\s*'((?:[^'\\]|\\.)*)'",
    re.S,
)

ZUNDER_LOCATION_ID_RE = re.compile(r"easycharger/location\.php\?id=(\d+)")

def parse_zunder_map_page(page: str, *, source_artifact: Optional[FetchArtifact] = None) -> List[Dict[str, Any]]:
    parsed: List[Dict[str, Any]] = []
    skipped: Dict[str, int] = defaultdict(int)
    blocks = page.split("new google.maps.Marker(")[1:]
    for block in blocks:
        match = ZUNDER_MARKER_RE.match("new google.maps.Marker(" + block)
        id_match = ZUNDER_LOCATION_ID_RE.search(block)
        if not match or not id_match:
            skipped["unparsed_marker"] += 1
            continue
        lat, lon = fnum(match.group(1)), fnum(match.group(2))
        if not likely_in_spain(lat, lon):
            skipped["outside_spain"] += 1
            continue
        title = html.unescape(match.group(3).replace("\\'", "'")).strip()
        location_id = id_match.group(1)
        add_record(
            parsed,
            source=ZUNDER_SOURCE,
            source_url=ZUNDER_MAP_PAGE_URL,
            name=title or "Zunder charger",
            address="",
            city="",
            lat=lat,
            lon=lon,
            price_min=None,
            free_condition="Zunder's map publishes no per-station tariff; its published network tariffs apply. Check the app.",
            connector_labels=[],  # no connector evidence; never fall back to the Type 2 default
            max_power_kw=None,
            status="VERIFY",
            reliability="directory-page",
            evidence="Zunder public charger map marker. Availability is not part of this snapshot.",
            parking_kind="unknown",
            parking_note=OPERATOR_FEED_PARKING_NOTE,
            raw={
                "zunder_location_id": location_id,
                "location_json_url": ZUNDER_LOCATION_URL.format(id=location_id),
                "source": "zunder-public-map",
            },
            source_artifact=source_artifact,
        )
    print(f"zunder: {len(parsed)} Spanish rows from {len(blocks)} markers (skipped: {dict(skipped)})")
    return parsed

MOT_REUS_SOURCE = "MOT (Aparcar) — Ajuntament de Reus municipal GIS"

MOT_REUS_URL = "https://geoportal.reus.cat/apps/giscube-admin/layerserver/geojsonlayers/carregadors-municipals"

MOT_REUS_FALLBACK_PAGE = "https://www.reus.cat/serveis/vehicle-electric"

MOT_REUS_LAYER = "Carregadors municipals"

MOT_TARRAGONA_SOURCE = "MOT (Aparcar) — Ajuntament de Tarragona open data"

MOT_TARRAGONA_URL = "https://opendatafiles.tarragona.cat/03303.csv"

MOT_REUS_ENDOLLS_RE = re.compile(r"(\d+)\s*endolls?", re.I)

def parse_mot_reus_geojson(payload: Any, *, source_artifact: Optional[FetchArtifact] = None) -> List[Dict[str, Any]]:
    parsed: List[Dict[str, Any]] = []
    skipped: Dict[str, int] = defaultdict(int)
    features = payload.get("features") if isinstance(payload, dict) else None
    for feature in features or []:
        props = feature.get("properties") if isinstance(feature, dict) and isinstance(feature.get("properties"), dict) else None
        geometry = feature.get("geometry") if isinstance(feature, dict) and isinstance(feature.get("geometry"), dict) else {}
        if props is None:
            skipped["not_a_feature"] += 1
            continue
        if str(props.get("layer") or "").strip() != MOT_REUS_LAYER:
            skipped["other_layer"] += 1  # carregadors privats and the combined layer are not MOT
            continue
        coords = geometry.get("coordinates")
        if geometry.get("type") != "Point" or not isinstance(coords, (list, tuple)) or len(coords) < 2:
            skipped["no_point"] += 1
            continue
        lon, lat = fnum(coords[0]), fnum(coords[1])  # GeoJSON order
        feature_id = props.get("id") if props.get("id") not in (None, "") else feature.get("id")
        if feature_id in (None, "") or not point_in_declared_spain(lat, lon, "ES"):
            skipped["no_id_or_outside_spain"] += 1
            continue
        match = MOT_REUS_ENDOLLS_RE.search(str(props.get("info") or ""))
        plugs = int(match.group(1)) if match else None
        link = str(props.get("nid") or "").strip()
        source_url = link if re.fullmatch(r"https://(?:www\.)?reus\.cat/[^?#]*", link) else MOT_REUS_FALLBACK_PAGE
        connectors = [connector_ref("Unknown connector", count=plugs, source_url=source_url,
                                    raw={"reus_info": props.get("info")})] if plugs else []
        add_record(
            parsed,
            source=MOT_REUS_SOURCE,
            source_url=source_url,
            name=re.sub(r"\s+", " ", str(props.get("name") or "Reus municipal charger")).strip(),
            address="",
            city="Reus",
            lat=lat,
            lon=lon,
            price_min=None,
            free_condition="The Reus municipal GIS publishes no tariff; check the price onsite or with the car park.",
            connector_labels=[],  # only a plug count is published; never default to Type 2
            max_power_kw=None,
            status="VERIFY",
            reliability="official-charger-location-price-unknown",
            evidence=(
                f"Reus municipal public GIS layer '{MOT_REUS_LAYER}' (MOT-activated municipal car parks)"
                + (f": {plugs} endolls (plugs)" if plugs else "")
                + ". Power and tariff are not published. Municipal public GIS, no licence stated."
            ),
            parking_kind="unknown",
            parking_note="Municipal car park in Reus; the layer publishes no parking fee. Check the signs onsite.",
            raw={
                "reus_feature_id": str(feature_id),
                "plugs": plugs,
                "info_raw": props.get("info"),
                "code": props.get("code"),
                "category": props.get("category"),
                "subcategory": props.get("subcategory"),
                "layer": props.get("layer"),
                "source": "reus-municipal-gis",
            },
            connectors=connectors,
            port_count=plugs,
            source_artifact=source_artifact,
        )
    print(f"mot_reus: {len(parsed)} rows from {len(features or [])} features (skipped: {dict(skipped)})")
    return parsed

def parse_mot_tarragona_csv(body: Any, *, source_artifact: Optional[FetchArtifact] = None) -> List[Dict[str, Any]]:
    text = body.decode("utf-8-sig", "replace") if isinstance(body, (bytes, bytearray)) else str(body)
    parsed: List[Dict[str, Any]] = []
    skipped: Dict[str, int] = defaultdict(int)
    for row in csv.DictReader(io.StringIO(text)):
        nom = re.sub(r"\s+", " ", str(row.get("NOM") or "")).strip()
        lat, lon = fnum(row.get("LATITUD")), fnum(row.get("LONGITUD"))
        if not nom or lat is None or lon is None or not point_in_declared_spain(lat, lon, "ES"):
            skipped["no_name_or_outside_spain"] += 1
            continue
        status_raw = str(row.get("STATUS") or "").strip()
        street, postal = str(row.get("ADRECA") or "").strip(), str(row.get("CP") or "").strip()
        add_record(
            parsed,
            source=MOT_TARRAGONA_SOURCE,
            source_url=MOT_TARRAGONA_URL,
            name=nom,
            address=", ".join(p for p in (street, postal, "Tarragona") if p),
            city="Tarragona",
            lat=lat,
            lon=lon,
            price_min=None,
            free_condition="The Ajuntament de Tarragona open dataset publishes no tariff; check the price in the operator app.",
            connector_labels=[],  # AMPERATGE is amperes, not a connector type or kW
            max_power_kw=None,
            status="VERIFY",
            reliability="official-charger-location-price-unknown",
            evidence=(
                f"Ajuntament de Tarragona open data (MOT/Aparcar): status {status_raw or 'not stated'} (snapshot, never live)"
                f"; {row.get('AMPERATGE') or '?'} A. Power in kW and tariff are not published."
            ),
            parking_kind="unknown",
            parking_note=OPERATOR_FEED_PARKING_NOTE,
            raw={
                "tarragona_nom": nom,
                "status_raw": status_raw or None,
                "amperage_raw": str(row.get("AMPERATGE") or "").strip() or None,
                "postal_code": postal or None,
                "codi_ens": str(row.get("CODI_ENS") or "").strip() or None,
                "nom_ens": str(row.get("NOM_ENS") or "").strip() or None,
                "source": "tarragona-open-data-csv",
            },
            source_artifact=source_artifact,
        )
    print(f"mot_tarragona: {len(parsed)} rows (skipped: {dict(skipped)})")
    return parsed

INTERPARKING_SOURCE = "P-APP (Interparking) public car park finder"

INTERPARKING_PAGE_URL = "https://www.interparking.es/es/encuentra-un-aparcamiento/"

INTERPARKING_SITE = "https://www.interparking.es"

INTERPARKING_EV_FACILITY = "Electric charging station"

INTERPARKING_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')

def interparking_car_parks_from_page(page: str) -> List[Dict[str, Any]]:
    chunks: List[str] = []
    for literal in INTERPARKING_PUSH_RE.findall(page):
        try:
            chunks.append(json.loads(literal))
        except ValueError:
            continue
    flight = "".join(chunks)
    marker = flight.find('"carParks":')
    if marker < 0:
        raise RuntimeError("Interparking finder page carries no embedded carParks data; upstream shape change?")
    try:
        car_parks, _end = json.JSONDecoder().raw_decode(flight[marker + len('"carParks":'):])
    except ValueError as exc:
        raise RuntimeError(f"Interparking embedded carParks JSON does not parse: {exc}") from exc
    if not isinstance(car_parks, list) or not car_parks:
        raise RuntimeError("Interparking embedded carParks list is empty")
    return [c for c in car_parks if isinstance(c, dict)]

def interparking_has_ev_flag(car_park: Dict[str, Any]) -> bool:
    return any(isinstance(f, dict) and f.get("id") == INTERPARKING_EV_FACILITY for f in car_park.get("facilities") or [])

def interparking_is_spanish(car_park: Dict[str, Any]) -> bool:
    return str(car_park.get("externalId") or "").startswith("es-")

def parse_interparking_car_parks(car_parks: List[Dict[str, Any]], *, source_artifact: Optional[FetchArtifact] = None) -> List[Dict[str, Any]]:
    parsed: List[Dict[str, Any]] = []
    skipped: Dict[str, int] = defaultdict(int)
    for park in car_parks:
        if not interparking_is_spanish(park):
            skipped["not_spain"] += 1
            continue
        if not interparking_has_ev_flag(park):
            skipped["no_ev_flag"] += 1  # no charger claim: not an actionable record
            continue
        location = park.get("location") if isinstance(park.get("location"), dict) else {}
        lat, lon = fnum(location.get("lat")), fnum(location.get("lng"))
        if not point_in_declared_spain(lat, lon, "ES"):
            skipped["outside_spain"] += 1
            continue
        price = re.sub(r"\s+", " ", str(park.get("price") or "").replace("\xa0", " ")).strip()
        price = "" if price.startswith("$") else price  # "$undefined" is the RSC null marker
        path = str(park.get("url") or "")
        source_url = INTERPARKING_SITE + path if re.fullmatch(r"/es/aparcamientos/[a-z0-9/_-]+/", path) else INTERPARKING_PAGE_URL
        city = park.get("city") if isinstance(park.get("city"), dict) else {}
        add_record(
            parsed,
            source=INTERPARKING_SOURCE,
            source_url=source_url,
            name=re.sub(r"\s+", " ", str(park.get("name") or "Interparking car park")).strip(),
            address=str(park.get("address") or ""),
            city=str(city.get("title") or ""),
            lat=lat,
            lon=lon,
            price_min=None,
            free_condition=(
                f"The Interparking finder shows a parking price ({price}), not a charging tariff; charging price unknown. Check onsite or in P-APP."
                if price else
                "Charging price unknown: the Interparking finder lists only a parking price, when it lists any price. Check onsite or in P-APP."
            ),
            connector_labels=[],  # the finder flags charging only; no plug type or power is published
            max_power_kw=None,
            status="VERIFY",
            reliability="directory-page",
            evidence="Interparking public car park finder flags 'Puntos de carga eléctrica' at this car park; no plug count, connector type, power or tariff is published.",
            parking_kind="paid_or_metered",
            parking_note="Interparking car park; the finder's price is the parking price, not a charging tariff.",
            raw={
                "interparking_external_id": str(park.get("externalId")),
                "parking_price": price or None,
                "brand": park.get("brand"),
                "city_slug": city.get("slug"),
                "ev_facility_flag": True,
                "source": "interparking-public-finder",
            },
            source_artifact=source_artifact,
        )
    print(f"interparking_papp: {len(parsed)} EV-flagged Spanish rows from {len(car_parks)} car parks (skipped: {dict(skipped)})")
    return parsed

def parse_interparking_page(page: str, *, source_artifact: Optional[FetchArtifact] = None) -> List[Dict[str, Any]]:
    return parse_interparking_car_parks(interparking_car_parks_from_page(page), source_artifact=source_artifact)


SOURCES = {
    "etecnic": {"name": ETECNIC_SOURCE, "countries": ["ES"], "url": ETECNIC_FEED_URL,
                "cache_name": "etecnic_chargers.json", "minimum": 3000, "minimum_raw": True, "identity": "etecnic_charger_id"},
    "atlante": {"name": ATLANTE_SOURCE, "countries": ["ES", "FR", "IT", "PT"], "url": ATLANTE_FEED_URL,
                "cache_name": "atlante_geodata.json", "minimum": 40, "identity": "atlante_location_id"},
    "zunder": {"name": ZUNDER_SOURCE, "countries": ["ES"], "url": ZUNDER_MAP_PAGE_URL,
               "cache_name": "zunder_map_page.html", "minimum": 300, "identity": "zunder_location_id"},
    "mot_reus": {"name": MOT_REUS_SOURCE, "countries": ["ES"], "url": MOT_REUS_URL,
                 "cache_name": "mot_reus.json", "minimum": 8, "identity": "reus_feature_id"},
    "mot_tarragona": {"name": MOT_TARRAGONA_SOURCE, "countries": ["ES"], "url": MOT_TARRAGONA_URL,
                      "cache_name": "tgn_csv.csv", "minimum": 3, "identity": "tarragona_nom"},
    "interparking": {"name": INTERPARKING_SOURCE, "countries": ["ES"], "url": INTERPARKING_PAGE_URL,
                     "cache_name": "interparking_finder.html", "minimum": 30, "identity": "interparking_external_id"},
}
for _metadata in SOURCES.values():
    _metadata.update(method="Public anonymous GET; directory snapshot", live_status=False,
                     license="Public directory; no open-data licence asserted", record_unit="source_location")


def add_record(records: list[dict], *, source: str, source_artifact: FetchArtifact | None = None,
               raw: dict | None = None, **fields: Any) -> None:
    """Adapt the reviewed parsers to stable global IDs and per-claim provenance."""
    source_id, metadata = next((key, value) for key, value in SOURCES.items() if value["name"] == source)
    raw = dict(raw or {})
    upstream = raw.get(metadata["identity"])
    if isinstance(upstream, bool) or not isinstance(upstream, (str, int)) or not str(upstream).strip():
        raise ValueError("Operator row lacks a stable source identifier")
    if source_id in {"etecnic", "zunder", "mot_reus"} and (not str(upstream).isascii() or not str(upstream).isdigit() or int(upstream) <= 0):
        raise ValueError("Operator numeric identifier must be positive")
    if source_id == "mot_tarragona":
        # The municipal CSV publishes no numeric ID. Its named location is scoped
        # to that municipality; a rename becomes a new lead, never rekeys history.
        upstream = hashlib.sha256(str(upstream).strip().casefold().encode()).hexdigest()[:24]
    elif not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", str(upstream)):
        raise ValueError("Malformed operator source identifier")
    if source_artifact is None:
        raise ValueError("Operator records require a retained source artifact")
    connectors = []
    for item in fields.get("connectors") or []:
        connector = dict(item, status="UNKNOWN")
        label = gs.normalize_connector(item.get("label")) if item.get("label") in KNOWN_CONNECTOR_LABELS else None
        if label:
            connector["label"] = label
        else:
            connector.pop("label", None)
        connectors.append(connector)
    operator = {"atlante": "Atlante", "zunder": "Zunder", "interparking": "Interparking"}.get(source_id, "")
    record = gs.make_record(source_id=source_id, upstream_id=str(upstream), source=source,
        source_url=fields["source_url"], name=fields["name"], lat=fields["lat"], lon=fields["lon"],
        country_code=raw.get("country", "ES"), artifact=source_artifact, connectors=connectors,
        raw=raw, address=fields.get("address", ""), city=fields.get("city", ""), operator=operator,
        source_updated_at=raw.get("updated_at"), license_name=metadata["license"])
    if record is None:
        return
    record.update(reliability="directory-page", source_record_unit=metadata["record_unit"],
                  source_adapter=source_id, free_condition=fields.get("free_condition", ""),
                  insight=fields.get("evidence", ""))
    record["source_integration"].update(mode="public_directory", note=fields.get("evidence", ""))
    record["sources"][0].update(evidence=fields.get("evidence", ""), source_fetched_at=source_artifact.fetched_at)
    for claim in ("location_claim", "hardware_claim", "charge_claim", "parking_claim"):
        record[claim]["source_fetched_at"] = source_artifact.fetched_at
    if fields.get("parking_kind") == "paid_or_metered":
        record["parking"].update(kind="paid_or_metered", paid=True, note=fields.get("parking_note", ""))
        record["parking_claim"].update(kind="paid_or_metered", summary=fields.get("parking_note", ""))
    if source_id == "interparking":
        record["location_claim"].update(precision="parking_coordinate",
            summary="Operator parking coordinate; exact charging bays are unverified.")
    records.append(record)


def parse_source(source_id: str, body: bytes, artifact: FetchArtifact) -> list[dict]:
    if source_id == "zunder":
        return parse_zunder_map_page(body.decode("utf-8"), source_artifact=artifact)
    if source_id == "interparking":
        return parse_interparking_page(body.decode("utf-8"), source_artifact=artifact)
    if source_id == "mot_tarragona":
        return parse_mot_tarragona_csv(body, source_artifact=artifact)
    payload = json.loads(body)
    if source_id in {"etecnic", "atlante"} and not isinstance(payload, list):
        raise ValueError("Operator feed must be a list")
    if source_id == "mot_reus" and (not isinstance(payload, dict) or not isinstance(payload.get("features"), list)):
        raise ValueError("Reus feed must contain a features list")
    return {"etecnic": parse_etecnic_chargers, "atlante": parse_atlante_locations,
            "mot_reus": parse_mot_reus_geojson}[source_id](payload, source_artifact=artifact)


def run_source(source_id: str, cache_dir: Path, countries: set[str] | None, offline: bool,
               emit: Callable[[dict], Any]) -> dict:
    metadata = SOURCES[source_id]
    report = {"source": source_id, "scope": sorted(set(metadata["countries"]) & countries) if countries is not None else metadata["countries"],
              "seen": 0, "accepted": 0, "duplicates": 0, "rejected": 0, "errors": [], "fetches": [],
              "license": metadata["license"], "record_unit": metadata["record_unit"], "live_status": False}
    if not report["scope"]:
        return dict(report, status="not_applicable")
    try:
        fetched = gs.fetch_cached_file(metadata["url"], cache_dir, metadata["cache_name"],
                                       ttl_hours=24, max_bytes=64 * 1024 * 1024, offline=offline)
        report["fetches"].append(fetched.artifact.as_evidence())
        if fetched.path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("Operator cache exceeds byte limit")
        # Never emit a valid prefix of a malformed or truncated operator response.
        body = fetched.path.read_bytes()
        rows = parse_source(source_id, body, fetched.artifact)
        report["seen"] = len(rows)
        measured = len(json.loads(body)) if metadata.get("minimum_raw") else len(rows)
        if not rows or measured < metadata["minimum"]:
            raise ValueError("Operator feed below completeness threshold")
        if len({row["id"] for row in rows}) != len(rows):
            raise ValueError("Duplicate operator source identifiers")
        if fetched.artifact.fallback and not offline:
            raise ValueError("Operator refresh failed; previous cache retained without admission")
        for row in rows:
            if row["country_code"] not in report["scope"]:
                continue
            result = emit(row)
            report["accepted"] += result == "added"
            report["duplicates"] += result == "duplicate"
            report["rejected"] += result in {"invalid", "excluded"}
        report["status"] = "stale" if fetched.artifact.stale else "ok"
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, AttributeError) as exc:
        report.update(status="failed", errors=[type(exc).__name__])
    return report
