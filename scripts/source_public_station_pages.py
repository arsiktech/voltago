"""Pure public-page parsers adapted from the Spain source expansion.

Telpark confirms parking amenities only; Elektrum is a Baltic directory, not
live availability. The worldwide adapter owns bounded acquisition and age.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Tuple


TELPARK_SOURCE = "telpark"

ELEKTRUM_SOURCE = "elektrum_drive"

TELPARK_PORTUGAL_CITY_SLUGS = frozenset({
    "lisboa", "oporto", "coimbra", "vila-nova-de-gaia", "viseu", "aveiro", "leiria",
    "covilha", "sesimbra", "barreiro", "almada", "santarem", "povoa-do-varzim",
    "torres-vedras", "cascais", "loures", "amadora", "figueira-da-foz", "faro",
    "fundao", "albufeira", "portimao", "braga", "guimaraes", "setubal", "evora",
    "funchal", "matosinhos", "maia", "gondomar", "oeiras", "sintra", "seixal",
})

_PT_POSTCODE = re.compile(r"\b\d{4}-\d{3}\b")

_ES_POSTCODE = re.compile(r"\b\d{5}\b")

def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).casefold().strip()

def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))).strip()

def _float(value: Any) -> Optional[float]:
    try:
        number = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None

def _valid_coords(lat: Optional[float], lon: Optional[float]) -> bool:
    return lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180 \
        and not (lat == 0 and lon == 0)

def _as_text(payload: Any) -> str:
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload).decode("utf-8", errors="replace")
    if isinstance(payload, str):
        return payload
    raise TypeError("expected text payload")

def _as_json(payload: Any) -> Any:
    if isinstance(payload, (list, dict)):
        return payload
    return json.loads(_as_text(payload))

_AD_POSTCODE = re.compile(r"\bAD\d{3}\b", re.I)

_ANDORRA_BOX = (42.42, 42.66, 1.40, 1.80)

_SPAIN_BOXES = ((35.8, 43.9, -9.4, 4.4), (27.5, 29.5, -18.5, -13.3), (35.8, 35.95, -2.99, -2.9))

def _in_box(lat: float, lon: float, box: Tuple[float, float, float, float]) -> bool:
    return box[0] <= lat <= box[1] and box[2] <= lon <= box[3]

def telpark_country(city_slug: str, address: str, phone: str = "",
                    lat: Optional[float] = None, lon: Optional[float] = None) -> str:
    """'PT', 'AD', 'ES' or 'unknown'. Portugal is detected first (slug, postcode, +351)."""
    text = address or ""
    if city_slug in TELPARK_PORTUGAL_CITY_SLUGS or _PT_POSTCODE.search(text) \
            or phone.replace(" ", "").startswith("+351"):
        return "PT"
    has_coords = lat is not None and lon is not None
    if _AD_POSTCODE.search(text) or (has_coords and _in_box(lat, lon, _ANDORRA_BOX)):  # type: ignore[arg-type]
        return "AD"
    if _ES_POSTCODE.search(text):
        return "ES"
    if has_coords and any(_in_box(lat, lon, b) for b in _SPAIN_BOXES):  # type: ignore[arg-type]
        return "ES"
    return "unknown"

def _telpark_city_slug(link: str) -> str:
    match = re.search(r"/ciudades/([^/]+)/", link or "")
    return match.group(1) if match else ""

def parse_telpark_parkings_page(payload: Any) -> List[Dict[str, Any]]:
    """Normalize one ``wp/v2/parkings`` REST page into raw candidate rows.

    The REST payload has coordinates and address but no usable EV flag (``serviceList`` is
    empty); ``ev_charging`` stays ``None`` until :func:`parse_telpark_detail` confirms it.
    """
    data = _as_json(payload)
    if isinstance(data, dict) and data.get("code"):  # WP error envelope
        raise ValueError(f"Telpark REST error: {data.get('code')}")
    if not isinstance(data, list):
        raise ValueError("Telpark REST payload is not a list")
    rows: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict) or item.get("id") in (None, ""):
            continue
        meta: Dict[str, Any] = item["meta_box"] if isinstance(item.get("meta_box"), dict) else {}
        header: Dict[str, Any] = meta["header"] if isinstance(meta.get("header"), dict) else {}
        link = str(item.get("link") or "")
        slug = _telpark_city_slug(link)
        address = _clean(header.get("address"))
        lat, lon = _float(header.get("latitude")), _float(header.get("longitude"))
        raw_title = item.get("title")
        title = _clean(raw_title.get("rendered") if isinstance(raw_title, dict) else raw_title)
        rows.append({
            "source": TELPARK_SOURCE,
            "source_id": f"telpark:{item['id']}",
            "wp_id": int(item["id"]) if str(item["id"]).isdigit() else str(item["id"]),
            "slug": str(item.get("slug") or ""),
            "name": title,
            "operator": "Telpark",
            "url": link,
            "city_slug": slug,
            "address": address,
            "lat": lat if _valid_coords(lat, lon) else None,
            "lon": lon if _valid_coords(lat, lon) else None,
            "country": telpark_country(slug, address, "", lat, lon),
            "parking_price_text": _clean(header.get("prize")) or None,
            "modified_gmt": item.get("modified_gmt"),
            "ev_charging": None,
            "ev_flag_source": None,
        })
    return rows

def _iter_jsonld_nodes(page_html: str) -> Iterable[Dict[str, Any]]:
    for match in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page_html, re.S | re.I):
        try:
            doc = json.loads(match.group(1))
        except ValueError:
            continue
        stack = doc if isinstance(doc, list) else [doc]
        for entry in stack:
            if not isinstance(entry, dict):
                continue
            graph = entry.get("@graph")
            for node in (graph if isinstance(graph, list) else [entry]):
                if isinstance(node, dict):
                    yield node

def _truthy(value: Any) -> bool:
    return value is True or _fold(value) in {"true", "1", "yes", "si"}

def parse_telpark_detail(page_html: Any) -> Dict[str, Any]:
    """Extract EV flag and coordinates from a Telpark parking detail page.

    Only ``amenityFeature`` on the parking node counts. The site Organization node
    advertises 'Carga eléctrica' in ``hasOfferCatalog`` for every page and is ignored.
    ``ev_charging`` is True/False when amenity data exists, else None.
    """
    text = _as_text(page_html)
    result: Dict[str, Any] = {"ev_charging": None, "lat": None, "lon": None, "address": None,
                              "country_raw": None, "telephone": None, "name": None}
    nodes = [node for node in _iter_jsonld_nodes(text)
             if node.get("@type") == "ParkingFacility"
             or isinstance(node.get("@type"), list) and "ParkingFacility" in node["@type"]]
    # Never borrow a geo/amenity/address from another entity, or guess between
    # several parking facilities advertised by a single detail page.
    if len(nodes) != 1:
        return result
    for node in nodes:
        features = node.get("amenityFeature")
        geo = node.get("geo")
        if not isinstance(features, list) and not isinstance(geo, dict):
            continue
        if isinstance(features, list):
            ev_values = []
            for feat in features:
                if isinstance(feat, dict) and _fold(feat.get("name")) == "carga electrica":
                    ev_values.append(_truthy(feat.get("value")))
            if ev_values:
                result["ev_charging"] = all(ev_values)
        if isinstance(geo, dict):
            lat, lon = _float(geo.get("latitude")), _float(geo.get("longitude"))
            if _valid_coords(lat, lon):
                result["lat"], result["lon"] = lat, lon
        addr = node.get("address")
        if isinstance(addr, dict):
            parts = [addr.get("streetAddress"), addr.get("addressLocality")]
            result["address"] = _clean(", ".join(str(p) for p in parts if p)) or None
            result["country_raw"] = addr.get("addressCountry")
        result["telephone"] = node.get("telephone") or None
        result["name"] = _clean(node.get("name")) or None
        break
    return result

_CONNECTOR_LINE = re.compile(
    r"^\s*(?:(?P<count>\d+)\s*x\s*)?(?P<kw>\d+(?:[.,]\d+)?)\s*kW\s*(?P<current>AC|DC)?\s*(?:\((?P<plugs>[^)]*)\))?\s*$",
    re.I,
)

def parse_elektrum_connector_line(text: str) -> Dict[str, Any]:
    """'2 x 50kW DC (CHAdeMO, CCS)' -> structured dict; unparsed text is kept verbatim."""
    line = _clean(text)
    match = _CONNECTOR_LINE.match(line)
    if not match:
        return {"raw": line, "count": None, "power_kw": None, "current": None, "plugs": []}
    plugs = [p.strip() for p in (match.group("plugs") or "").split(",") if p.strip()]
    return {
        "raw": line,
        "count": int(match.group("count")) if match.group("count") else None,
        "power_kw": float(match.group("kw").replace(",", ".")),
        "current": (match.group("current") or "").upper() or None,
        "plugs": plugs,
    }

_BALTIC_BOXES = {
    "EE": (57.45, 59.75, 21.7, 28.3),
    "LV": (55.65, 58.1, 20.9, 28.3),
    "LT": (53.85, 56.5, 20.9, 26.9),
}

def elektrum_country(lat: float, lon: float, address: str = "", city: str = "") -> Tuple[str, str]:
    """Return (country, method). Bounding boxes first; address wording breaks overlaps."""
    inside = [c for c, (a, b, x, y) in _BALTIC_BOXES.items() if a <= lat <= b and x <= lon <= y]
    if len(inside) == 1:
        return inside[0], "bbox"
    text = _fold(f"{address} {city}")
    hints = []
    if re.search(r"\b(iela|novads|prospekts|bulvaris)\b", text) and "LV" in inside:
        hints.append("LV")
    if re.search(r"\b(g|pr|al|gatve|aleja|rajonas)\b", text) and "LT" in inside:
        hints.append("LT")
    if re.search(r"\b(mnt|tee|tn|pst|vald|maakond)\b", text) and "EE" in inside:
        hints.append("EE")
    if len(set(hints)) == 1:
        return hints[0], "address_hint"
    return "unknown", "ambiguous" if inside else "outside_baltic_boxes"

class _ElektrumParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stations: List[Dict[str, Any]] = []
        self.city = ""
        self._station: Optional[Dict[str, Any]] = None
        self._li_depth = 0
        self._capture: Optional[str] = None
        self._buf: List[str] = []
        self._in_city_h3 = False
        self._section_id = ""
        self._in_h1 = False

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "section" and a.get("id", "").startswith("station-"):
            self._section_id = a["id"]
        elif tag == "h3" and "heading" in a.get("class", "") and self._station is None:
            self._in_city_h3, self._buf = True, []
        elif tag == "li" and "data-latitude" in a and self._station is None:
            self._station = {"attrs": a, "city": self.city, "section": self._section_id,
                             "name": "", "address": "", "description": "", "connectors": [], "href": ""}
            self._li_depth = 1
        elif self._station is not None:
            if tag == "li":
                self._li_depth += 1
            elif tag == "h1":
                self._in_h1, self._buf = True, []
            elif tag == "a" and self._in_h1:
                self._station["href"] = a.get("href", "")
            elif tag == "p":
                cls = a.get("class", "")
                for key in ("address", "description", "connector-information"):
                    if key in cls.split():
                        self._capture, self._buf = key, []
            elif tag == "br" and self._capture == "description":
                self._buf.append(" \n ")

    def handle_data(self, data: str) -> None:
        if self._in_city_h3 or self._in_h1 or self._capture:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3" and self._in_city_h3:
            self.city = _clean("".join(self._buf))
            self._in_city_h3 = False
        elif self._station is not None:
            if tag == "h1" and self._in_h1:
                self._station["name"] = _clean("".join(self._buf))
                self._in_h1 = False
            elif tag == "p" and self._capture:
                text = _clean("".join(self._buf).replace("\n", " "))
                if self._capture == "connector-information":
                    self._station["connectors"].append(text)
                else:
                    self._station[self._capture] = text
                self._capture = None
            elif tag == "li":
                self._li_depth -= 1
                if self._li_depth == 0:
                    self.stations.append(self._station)
                    self._station = None

def parse_elektrum_public_points(page_html: Any) -> List[Dict[str, Any]]:
    """Parse the public-charging-points HTML into raw Baltic station rows (no Spain)."""
    text = _as_text(page_html)
    parser = _ElektrumParser()
    parser.feed(text)
    parser.close()
    if parser._station is not None:
        raise ValueError("Truncated Elektrum station markup")
    rows: List[Dict[str, Any]] = []
    seen: Dict[str, Dict[str, Any]] = {}
    for st in parser.stations:
        a = st["attrs"]
        lat, lon = _float(a.get("data-latitude")), _float(a.get("data-longitude"))
        if lat is None or lon is None or not _valid_coords(lat, lon):
            continue
        name = st["name"]
        country, method = elektrum_country(lat, lon, st["address"], st["city"])
        code_match = re.search(r"\((\d{3,6})\)\s*$", name)
        # Prefer printed upstream codes. Where the page has none, make the
        # derivation explicit and independent of page order or duplicate rows.
        key_src = f"{_fold(name)}|{lat:.5f}|{lon:.5f}"
        source_id = (f"{country}_{a.get('data-type') or 'unknown'}_code_{code_match.group(1)}" if code_match else
                     "derived_" + hashlib.sha256(key_src.encode("utf-8")).hexdigest()[:24])
        connectors = [parse_elektrum_connector_line(c) for c in st["connectors"]]
        powers = [c["power_kw"] for c in connectors if c["power_kw"] is not None]
        row = {
            "source": ELEKTRUM_SOURCE,
            "source_id": source_id,
            "station_code": code_match.group(1) if code_match else None,
            "identity_method": "upstream_station_code" if code_match else "derived_name_coordinate",
            "name": name,
            "operator": "Elektrum Drive",
            "network_type": a.get("data-type") or None,  # latvenergo (own) | partner
            "lat": lat,
            "lon": lon,
            "address": st["address"] or None,
            "city": st["city"] or None,
            "country": country,
            "country_method": method,
            "url": st["href"] or None,
            "hours_text": st["description"] or None,
            "has_fast_charging": a.get("data-has-fast-charging") == "1",
            "connectors": connectors,
            "power_kw_max": max(powers) if powers else None,
            "charging_tariff": None,
            "availability": None,
            "evidence": "directory",
        }
        previous = seen.get(source_id)
        if previous is not None:
            if (previous["lat"], previous["lon"], previous["country"]) != (lat, lon, country):
                raise ValueError("Conflicting Elektrum upstream station identity")
            for connector in connectors:
                if connector not in previous["connectors"]:
                    previous["connectors"].append(connector)
            continue
        seen[source_id] = row
        rows.append(row)
    return rows
