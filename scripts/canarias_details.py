#!/usr/bin/env python3
"""Parse the public Canarias te recarga charger pages, without network requests.

``parse_canarias_detail(html, source_url, fetched_at="...")`` returns plain JSON
facts. An unrecognised/oversized/malformed page returns ``recognized=False``.
Every field is directory evidence: no page text grants live availability.
``tariff.components`` retains energy, hourly, minimum-spend and session charges
separately. ``energy_eur_per_kwh`` is a component, not a total session price.
``usable_as_simple_energy_price`` is false when conditions or other costs exist.
Connector ``port_count`` counts sockets; ``bay_count`` counts published spaces.

The current site uses Elementor and an older text/column template. Only the
station's #page-content is examined. Navigation, page headers, site footers,
scripts and embeds cannot supply station facts. URLs are normalised back to the
exact public HTTPS source origin; WordPress image-proxy URLs are unwrapped only
when their underlying image belongs to that same origin.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Iterator


SOURCE_ORIGIN = "https://canariasterecarga.com"
SOURCE_NAME = "Canarias te recarga public charger page"
MAX_HTML_CHARS = 2_000_000
MAX_NODES = 40_000
MAX_DEPTH = 100
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_SKIP = frozenset("script style noscript nav header footer iframe object template".split())
_SKIP_CLASSES = frozenset({"article-footer", "article-navigation", "breadcrumbs", "related-posts", "comments-area"})
_TEXT_BLOCKS = frozenset("div section p li h1 h2 h3 h4 h5 br tr td ul ol".split())
_DISALLOWED_PAGES = frozenset({
    "mi-cuenta", "mi-cuenta1", "my-account", "checkout", "carrito", "cart",
    "finalizar-compra", "tienda", "shop", "pago", "payment", "login", "logout",
    "wp-login", "wp-admin", "contacto", "inicio", "inicio-english", "mapa",
})
_ICON_LABELS = {
    "ev-plug-type2.svg": "Type 2", "ev-plug-ccs2.svg": "CCS2",
    "ev-plug-ccs2-1.svg": "CCS2", "ev-plug-chademo.svg": "CHAdeMO",
    "ev-plug-type1.svg": "Type 1",
}
# Exact path fingerprints, verified against the source's named public SVG files
# under /wp-content/uploads/2022/07/ev-plug-*.svg (ccs2-1: 2022/09).
# Unknown inline graphics never become a connector type.
_SVG_LABELS = {
    "5db03b1155b654ac9545acd72f130a620ec7eaadb1ca162ddcebbcaab57ed74f": "Type 2",
    "1514c785417010ccb1faa54b1fcc8e4438b6a6f38bbeb2d5661fd9f22d95d77f": "CCS2",
    "fc39791fbaf03ac411cad5e41c4c14b2286ea5648db9de6a058163bc1121bc46": "CHAdeMO",
    "9f4c1f00b70251180fa16ea4aa6f7da2efd303c2af48250fa197703da937e1d2": "Type 1",
}


def _clean(value: str, limit: int = 2000) -> str:
    return re.sub(r"\s+", " ", value or "").strip()[:limit]


def _fold(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold())
                   if not unicodedata.combining(c))


def _source_url(value: str, base_url: str) -> urllib.parse.SplitResult | None:
    value = str(value or "").strip()
    if not value or len(value) > 2048 or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        return None
    try:
        raw_path = urllib.parse.unquote(urllib.parse.urlsplit(value).path)
        if any(part in {".", ".."} for part in raw_path.split("/")):
            return None
        url = urllib.parse.urlsplit(urllib.parse.urljoin(base_url, value))
        if (url.scheme != "https" or url.netloc.casefold() != "canariasterecarga.com"
                or url.username or url.password or url.port or url.fragment):
            return None
    except (TypeError, ValueError):
        return None
    decoded = urllib.parse.unquote(url.path)
    if re.search(r"[\\\x00-\x1f\x7f]", decoded) or any(
            part in {".", ".."} for part in decoded.split("/")):
        return None
    return url


def safe_detail_url(value: str, base_url: str = SOURCE_ORIGIN + "/es/mapa/") -> str:
    """Return a public same-origin detail URL, or an empty string.

    Old charger pages have descriptive (occasionally misspelled) slugs, rather
    than pNNNNN codes. No query, fragment, account, store or payment path is used.
    """
    parsed = _source_url(value, base_url)
    if not parsed or parsed.query or "%" in parsed.path:
        return ""
    match = re.fullmatch(r"/(es|en)/(?:mapa/)?([a-z0-9]+(?:-[a-z0-9]+)*)/?", parsed.path)
    if not match or match.group(2) in _DISALLOWED_PAGES:
        return ""
    if "/mapa/" in parsed.path and not re.fullmatch(r"p\d{1,10}", match.group(2)):
        return ""
    return SOURCE_ORIGIN + parsed.path.rstrip("/") + "/"


def safe_photo_url(value: str, base_url: str = SOURCE_ORIGIN + "/") -> str:
    """Keep only source-hosted upload images; unwrap the site's WP image proxy."""
    value = str(value or "").strip()
    if len(value) > 2048 or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        return ""
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.fragment:
            return ""
        if (parsed.scheme == "https" and parsed.netloc in {"i0.wp.com", "i1.wp.com", "i2.wp.com"}
                and parsed.path.startswith("/canariasterecarga.com/wp-content/uploads/")):
            value = SOURCE_ORIGIN + parsed.path[len("/canariasterecarga.com"):]
    except ValueError:
        return ""
    parsed = _source_url(value, base_url)
    if not parsed or not re.fullmatch(
            r"/wp-content/uploads/(?:[^/?#]+/)+[^/?#]+\.(?:jpe?g|png|webp|avif)",
            parsed.path, re.I):
        return ""
    return SOURCE_ORIGIN + parsed.path


_IDENTITY_STOP = frozenset({
    "a", "al", "con", "de", "del", "el", "en", "la", "las", "lo", "los", "un", "una", "y",
    "punto", "puntos", "punta", "recarga", "reacarga", "reacrga", "ecarga", "carga", "charging", "point",
    "cc", "centro", "comercial", "comerial", "ve", "s", "n", "sn", "sa",
})
_IDENTITY_GENERIC = frozenset({
    "hotel", "parking", "aparcamiento", "apartamentos", "supermercado", "parque", "ayuntamiento",
    "puerto", "calle", "avenida", "carretera", "plaza", "centro", "comercial", "poligono", "industrial",
})
_CHAIN_BRANDS = frozenset({"ikea", "bp", "repsol", "cepsa", "wenea", "endesa", "iberdrola", "tesla",
                            "mercadona", "alcampo", "decathlon", "disa", "pcan", "sagulpa", "canaryoil"})


def _identity_terms(value: str, *, street: bool = False) -> set[str]:
    value = _fold(str(value or ""))
    value = re.sub(r"(?<=[a-z])['’](?=[a-z])", "", value)
    value = re.sub(r"\bs\s*/\s*c\b", "santa cruz", value)
    for pattern, replacement in [
        (r"\bayto\b", "ayuntamiento"), (r"\b(?:avda|avd|av)\b", "avenida"),
        (r"\b(?:ctra|carr)\b", "carretera"), (r"\bc(?=\s*[/.,])", "calle"),
        (r"\baparcamientos\b", "aparcamiento"), (r"\btres\b", "3"),
    ]:
        value = re.sub(pattern, replacement, value)
    if street:
        value = re.split(r"\b(?:35|38)\d{3}\b", value, maxsplit=1)[0]
    terms = {t for t in re.findall(r"[a-z0-9]+", value) if len(t) > 1 and t not in _IDENTITY_STOP and not t.isdigit()}
    if street:
        terms -= _IDENTITY_GENERIC | {"santa", "san", "cruz", "tenerife", "gran", "canaria", "palmas", "canarias", "espana"}
    return terms


def _canary_postcodes(value: str) -> set[str]:
    return set(re.findall(r"\b(?:35|38)\d{3}\b", str(value or "")))


def _postcode_island(postcode: str) -> str:
    if postcode.startswith("355"):
        return "lanzarote"
    if postcode.startswith("356"):
        return "fuerteventura"
    if postcode.startswith("387"):
        return "la_palma"
    if postcode.startswith("388"):
        return "la_gomera"
    if postcode.startswith("389"):
        return "el_hierro"
    if postcode.startswith("35"):
        return "gran_canaria"
    if postcode.startswith("38"):
        return "tenerife"
    return ""


def _page_identity_matches_one(record: dict[str, Any], detail: dict[str, Any]) -> tuple[bool, str]:
    """Require independent station identity evidence before using a detail page.

    The map reuses several detail URLs for geographically different chargers.
    A URL is therefore never identity proof. Exact/abbreviated names need a
    corroborating street, postcode or nearby explicit map marker. Different
    islands, distant explicit markers and contradictory street numbers fail
    closed. Minor spelling, punctuation and common address abbreviations do
    not hide otherwise corroborated stations. The reason is suitable for QA.
    """
    if not isinstance(record, dict) or not isinstance(detail, dict) or not detail.get("recognized"):
        return False, "detail_page_unrecognized"
    marker_name = str(record.get("name") or "")
    detail_name = str(detail.get("station_name") or detail.get("name") or "")
    marker_address = str(record.get("address") or "")
    detail_address = str(detail.get("address") or "")
    marker_terms, detail_terms = _identity_terms(marker_name), _identity_terms(detail_name)
    marker_street, detail_street = _identity_terms(marker_address, street=True), _identity_terms(detail_address, street=True)
    shared_name = marker_terms & detail_terms
    distinctive = shared_name - _IDENTITY_GENERIC
    name_coverage = len(shared_name) / min(len(marker_terms), len(detail_terms)) if marker_terms and detail_terms else 0.0
    street_shared = marker_street & detail_street
    street_coverage = len(street_shared) / min(len(marker_street), len(detail_street)) if marker_street and detail_street else 0.0
    marker_codes, detail_codes = _canary_postcodes(marker_address), _canary_postcodes(detail_address)
    same_postcode = bool(marker_codes & detail_codes)
    postcode_conflict = bool(marker_codes and detail_codes and not same_postcode)
    if postcode_conflict and not ({_postcode_island(c) for c in marker_codes} & {_postcode_island(c) for c in detail_codes}):
        return False, "detail_postcode_island_conflict"

    def street_numbers(address: str) -> set[str]:
        before_postcode = re.split(r"\b(?:35|38)\d{3}\b", address, maxsplit=1)[0]
        # "Avenida 3 de Mayo" names a street; 3 is not its building number.
        before_postcode = re.sub(r"\b\d+\s+de\s+(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\b", "", _fold(before_postcode))
        return {n for n in re.findall(r"(?<!\d)\d{1,4}(?!\d)", before_postcode) if n != "0"}
    marker_numbers, detail_numbers = street_numbers(marker_address), street_numbers(detail_address)
    number_conflict = bool(marker_numbers and detail_numbers and not marker_numbers & detail_numbers)
    # Distinct named floors identify different installations in the same building.
    floor_pattern = r"\b(?:planta|floor|nivel)\s*([+-]?\d+)\b"
    marker_floor = re.search(floor_pattern, _fold(marker_name))
    detail_floor = re.search(floor_pattern, _fold(detail_name))
    if marker_floor and detail_floor and marker_floor.group(1) != detail_floor.group(1):
        return False, "detail_floor_conflict"

    distance_m = None
    point = detail.get("coordinates")
    if isinstance(point, dict):
        try:
            lat1, lon1 = float(record.get("lat")), float(record.get("lon"))
            lat2, lon2 = float(point.get("lat")), float(point.get("lon"))
            if all(math.isfinite(n) for n in (lat1, lon1, lat2, lon2)) and -90 <= lat1 <= 90 and -90 <= lat2 <= 90 and -180 <= lon1 <= 180 and -180 <= lon2 <= 180:
                dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
                hav = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
                distance_m = 6_371_000 * 2 * math.asin(min(1, math.sqrt(hav)))
        except (ValueError, TypeError):
            pass
    if distance_m is not None and distance_m > 1500:
        return False, "detail_explicit_coordinate_conflict"
    if distance_m is not None and distance_m <= 250 and ((distinctive and name_coverage >= 0.5) or (street_shared and street_coverage >= 0.8)):
        return True, "detail_identity_corroborated_by_nearby_explicit_coordinates"

    street_match = bool(street_shared and street_coverage >= 0.8)
    name_match = bool(distinctive and name_coverage >= 0.8)
    if number_conflict and street_match:
        return False, "detail_street_number_conflict"
    if postcode_conflict:
        if name_match and street_match and not number_conflict:
            return True, "detail_name_and_street_match_with_postcode_discrepancy"
        return False, "detail_postcode_and_street_conflict"
    if street_match and not number_conflict and (name_match or len(street_shared) >= 2 or (same_postcode and bool(marker_numbers & detail_numbers))):
        return True, "detail_identity_corroborated_by_street_address"
    if name_match and same_postcode and bool(distinctive - _CHAIN_BRANDS):
        return True, "detail_identity_corroborated_by_name_and_postcode"
    return False, "detail_identity_not_corroborated"


def page_identity_matches(record: dict[str, Any], detail: dict[str, Any]) -> tuple[bool, str]:
    """Match station identity, never a URL alone; return (matched, QA reason).

    A primary Canarias map record may corroborate its page using either the
    marker's own property identity or its explicit popup heading/address pair.
    Both contain occasional source errors, so neither pair automatically wins.
    Other-source canonical stations must pass on their own name/address/point;
    an attached Canarias popup cannot grant that independent identity proof.
    """
    if not isinstance(record, dict) or not isinstance(detail, dict):
        return False, "detail_page_unrecognized"
    raw = record.get("raw") if isinstance(record.get("raw"), dict) else {}
    candidates = [(record, "marker_properties")]
    if "canarias te recarga" in _fold(str(record.get("source") or "")) and (raw.get("popup_name") or raw.get("popup_address")):
        popup_record = dict(record)
        popup_record["name"] = raw.get("popup_name") or record.get("name") or ""
        popup_record["address"] = raw.get("popup_address") or record.get("address") or ""
        candidates.insert(0, (popup_record, "marker_popup"))
    reasons = []
    for candidate, basis in candidates:
        matched, reason = _page_identity_matches_one(candidate, detail)
        if matched:
            return True, reason + "_" + basis
        reasons.append(reason)
    return False, reasons[0] if reasons else "detail_identity_not_corroborated"


def popup_station_identity(popup_html: str) -> dict[str, str]:
    """Read only the marker's station heading and following address paragraph.

    GeoJSON ``properties.name/address`` can be stale geocoder labels. The popup
    may publish newer station identity text, but its tariff, connector/bay text,
    links and buttons are never treated as an address or identity evidence.
    """
    result = {"name": "", "address": ""}
    if not isinstance(popup_html, str) or len(popup_html) > 30_000:
        return result
    tree = _Tree()
    try:
        tree.feed(popup_html)
        tree.finish()
    except (ValueError, RecursionError):
        return result
    nodes = list(_nodes(tree.root))
    heading = next((n for n in nodes if n.tag in {"h1", "h2", "h3"} and _text(n)), None)
    if heading is None:
        return result
    result["name"] = _clean(_text(heading), 240)
    for node in nodes:
        if node.tag != "p" or node.start < heading.end:
            continue
        text = _text(node)
        if not text or re.search(r"€|\b(?:gratis|disponible|conectores|tarifa|informacion|x\d+)\b|\b\d+\s+plazas?\b", _fold(text)):
            continue
        if any(n.tag in {"button", "img"} for n in _nodes(node)):
            continue
        # A full postcode, street prefix or descriptive locality is an address
        # paragraph. Do not require a house number: many chargers use s/n.
        if _canary_postcodes(text) or re.search(
                r"\b(?:c[/.,]|calle|av[./]|avda|avenida|ctra|carretera|plaza|paseo|puerto|muelle|poligono|camino|estacion|km|s/n)\b", _fold(text)):
            result["address"] = _clean(text, 500)
            break
    return result


@dataclass
class _Text:
    value: str
    start: int


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    start: int
    end: int = 0
    children: list[Any] = field(default_factory=list)
    parent: Any = None

    def has_class(self, name: str) -> bool:
        return name in self.attrs.get("class", "").split()


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", {}, 0)
        self.stack = [self.root]
        self.order = 0

    def _tick(self) -> int:
        self.order += 1
        if self.order > MAX_NODES:
            raise ValueError("HTML node budget exceeded")
        return self.order

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, {k: v or "" for k, v in attrs}, self._tick(), parent=self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)
            if len(self.stack) > MAX_DEPTH:
                raise ValueError("HTML depth budget exceeded")
        else:
            node.end = self.order

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                for node in self.stack[index:]:
                    node.end = self._tick()
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.stack[-1].children.append(_Text(data, self._tick()))

    def finish(self) -> None:
        self.close()
        for node in self.stack:
            node.end = self.order + 1


def _nodes(node: _Node, *, include_skipped: bool = False) -> Iterator[_Node]:
    if _SKIP_CLASSES.intersection(node.attrs.get("class", "").split()):
        return
    if node.tag in _SKIP and not (include_skipped and node.tag == "iframe"):
        return
    yield node
    for child in node.children:
        if isinstance(child, _Node):
            yield from _nodes(child, include_skipped=include_skipped)


def _texts(node: _Node) -> Iterator[_Text]:
    if node.tag in _SKIP or _SKIP_CLASSES.intersection(node.attrs.get("class", "").split()):
        return
    for child in node.children:
        if isinstance(child, _Text):
            yield child
        else:
            yield from _texts(child)


def _text(node: _Node) -> str:
    def render(current: _Node) -> str:
        if current.tag in _SKIP or _SKIP_CLASSES.intersection(current.attrs.get("class", "").split()):
            return ""
        pieces = []
        for child in current.children:
            if isinstance(child, _Text):
                pieces.append(child.value)
            else:
                value = render(child)
                pieces.append(" " + value + " " if child.tag in _TEXT_BLOCKS else value)
        return "".join(pieces)
    return _clean(render(node))


def _class_text(node: _Node, name: str) -> str:
    return next((_text(n) for n in _nodes(node) if n.has_class(name)), "")


def _section_kind(text: str) -> str:
    text = _fold(_clean(text))
    if re.match(r"^(localizacion|ubicacion|indicaciones|location|directions)\b", text):
        return "location"
    if re.match(r"^(?:tomas\s*/\s*)?(conectores|connectors|sockets)\b", text):
        return "connectors"
    if re.match(r"^(tarifas?|tariffs?|prices?)\b", text):
        return "tariff"
    if re.match(r"^(?:informacion de )?contacto\b|^contact\b", text):
        return "contact"
    if re.match(r"^(horarios?|opening hours|hours)\b", text):
        return "hours"
    if re.match(r"^(acceso|access)\b", text):
        return "access"
    if re.match(r"^(descripcion|observaciones|informacion(?: adicional)?|description|information|details)\b", text):
        return "description"
    return ""


def _number(value: str) -> float | None:
    if not re.fullmatch(r"\d{1,6}(?:[.,]\d{1,4})?", value.strip()):
        return None
    return float(value.replace(",", "."))


def _icon_label(node: _Node, source_url: str) -> tuple[str, str]:
    for item in _nodes(node):
        if item.tag == "img":
            parsed = _source_url(item.attrs.get("src", ""), source_url)
            if parsed:
                label = _ICON_LABELS.get(parsed.path.rsplit("/", 1)[-1])
                if label:
                    return label, "named_source_connector_icon"
        if item.tag == "svg":
            paths = [n.attrs.get("d", "") for n in _nodes(item) if n.tag == "path"]
            normalized = "|".join(re.sub(r"\s+", "", p).lower() for p in paths)
            label = _SVG_LABELS.get(hashlib.sha256(normalized.encode()).hexdigest())
            if label:
                return label, "verified_source_svg_fingerprint"
    text = _fold(_text(node))
    for label, pattern in [("CCS2", r"\bccs(?:\s*2|\s*combo\s*2)\b"), ("CCS", r"\bccs\b"),
                           ("CHAdeMO", r"\bchademo\b"), ("Type 2", r"\b(?:type|tipo)\s*2\b"),
                           ("Type 1", r"\b(?:type|tipo)\s*1\b"), ("Schuko", r"\bschuko\b")]:
        if re.search(pattern, text):
            return label, "explicit_source_connector_text"
    return "Unknown", "unidentified_source_connector"


def _connector(node: _Node, source_url: str, fetched_at: str) -> dict[str, Any] | None:
    text = _text(node)
    label, basis = _icon_label(node, source_url)
    count_match = re.search(r"\bx\s*(\d{1,3})\b", text, re.I)
    count = int(count_match.group(1)) if count_match else None
    if count is not None and not 1 <= count <= 100:
        return None
    power_match = re.search(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{1,2})?)\s*(kWh|kW)\b", text, re.I)
    power_kw = None
    raw: dict[str, Any] = {"basis": basis, "source_text": text, "count_known": count is not None}
    if power_match:
        value = _number(power_match.group(1))
        unit = power_match.group(2)
        raw.update(power_source_value=value, power_source_unit=unit,
                   power_source_text=power_match.group(0))
        if value is not None and 0 < value <= 500:
            if unit.casefold() == "kw":
                power_kw = value
                raw["power_basis"] = "source_literal_kw"
            elif (label != "Unknown" or (count is not None and node.has_class("elementor-icon-box-wrapper"))) and not re.search(r"bater|batter|capacidad|capacity|energia|energy", _fold(text)):
                # The source repeatedly prints kWh inside individual connector
                # rating rows (7.4, 22, 43, 50). This is an explicit contextual
                # interpretation, never a silent energy-to-power conversion.
                power_kw = value
                raw.update(power_basis="connector_rating_context_unit_normalized",
                           power_unit_inferred=True,
                           power_normalization_note=(
                               f"Source prints {power_match.group(0)} in the connector specification; "
                               f"interpreted as a {value:g} kW connector rating. "
                               "Directory evidence; output has not been measured."))
    if label == "Unknown" and not power_match and count is None:
        return None
    return {"label": label, "type": label, "count": count, "power_kw": power_kw,
            "status": "UNKNOWN", "source_url": source_url, "raw": raw,
            "provenance": _provenance(source_url, fetched_at, text)}


def _provenance(source_url: str, fetched_at: str, text: str) -> dict[str, Any]:
    return {"source": SOURCE_NAME, "source_url": source_url, "source_type": "directory_page",
            "fetched_at": fetched_at, "source_text": _clean(text), "live_authority": False}


def _map_coordinates(root: _Node) -> dict[str, Any] | None:
    """Read an explicit numeric map marker; never geocode a query or viewport."""
    for node in _nodes(root, include_skipped=True):
        if node.tag not in {"iframe", "a"}:
            continue
        value = node.attrs.get("src" if node.tag == "iframe" else "href", "")
        try:
            parsed = urllib.parse.urlsplit(value)
            if (parsed.scheme != "https" or parsed.netloc not in
                    {"maps.google.com", "www.google.com", "google.com"}):
                continue
            query = urllib.parse.parse_qs(parsed.query)
            coordinate_query = (query.get("q") or [""])[0]
            match = re.fullmatch(r"\s*(?:loc:)?(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)\s*", coordinate_query)
            if not match:
                # Embedded place data explicitly identifies a point, unlike an
                # @lat,lon zoom viewport, ll= or center= map-position setting.
                pairs = re.findall(r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)", value)
                if len(set(pairs)) != 1:
                    continue
                lat_text, lon_text = pairs[0]
            else:
                lat_text, lon_text = match.groups()
            lat, lon = float(lat_text), float(lon_text)
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return {"lat": lat, "lon": lon, "basis": "explicit_source_map_marker",
                        "source_map_url": value}
        except (ValueError, TypeError):
            continue
    return None


def _legacy_chunks(node: _Node) -> list[_Node]:
    """A legacy paragraph can contain several img/xN/kW socket rows."""
    if len(re.findall(r"\bx\s*\d+\b", _text(node), re.I)) < 2:
        return [node]
    chunks: list[_Node] = []
    current: list[Any] = []
    for child in node.children:
        if isinstance(child, _Node) and child.tag == "img" and current:
            if any(isinstance(c, _Node) and c.tag == "img" for c in current):
                chunks.append(_Node(node.tag, node.attrs, node.start, node.end, current))
                current = []
        current.append(child)
    if current:
        chunks.append(_Node(node.tag, node.attrs, node.start, node.end, current))
    return chunks if len(chunks) > 1 else [node]


def _parse_tariff(nodes: list[_Node], text: str, source_url: str, fetched_at: str) -> dict[str, Any]:
    components: list[dict[str, Any]] = []
    conditions: list[str] = []
    def component(kind: str, amount: float, unit: str, evidence: str, **extra: Any) -> None:
        key = (kind, amount, unit, _clean(evidence))
        if any((c["type"], c["amount"], c["unit"], c["text"]) == key for c in components):
            return
        components.append({"type": kind, "amount": amount, "price": amount, "currency": "EUR", "unit": unit,
                           "text": _clean(evidence), "source_url": source_url,
                           "provenance": _provenance(source_url, fetched_at, evidence), **extra})

    tables = [n for n in nodes if n.has_class("elementor-price-table")]
    for table in tables:
        label = _class_text(table, "elementor-price-table__subheading")
        integer = _class_text(table, "elementor-price-table__integer-part")
        fraction = _class_text(table, "elementor-price-table__fractional-part")
        currency = _class_text(table, "elementor-price-table__currency")
        period = _class_text(table, "elementor-price-table__period")
        amount = _number(integer + ("." + fraction if fraction and fraction.isdigit() else ""))
        table_text = _text(table)
        features = _class_text(table, "elementor-price-table__features-list")
        if features:
            conditions.append(features)
        conditional = bool(re.search(
            r"\b(?:hora|minut|primer|cliente|socio|solo|hasta|desde|despues|tras|condicion)", _fold(label)))
        # Ordinal labels such as "Gratis 1ªhora" lack a word boundary at hora.
        conditional = conditional or bool(re.search(r"\d\s*[ªºa-z]*\s*(?:hora|minut)", _fold(label)))
        if conditional and label:
            conditions.append(label)
        if amount is None or not re.search(r"€|EUR", currency, re.I):
            continue
        if amount == 0 and conditional:
            component("FREE_PERIOD", amount, "condition", label, condition=label)
        elif re.fullmatch(r"/?\s*kwh", period, re.I):
            component("ENERGY", amount, "kWh", f"{label}: {amount:g} €/kWh".strip(": "))
        elif re.fullmatch(r"/?\s*(?:hora|hour|h)", period, re.I):
            component("TIME", amount, "hour", f"{label}: {amount:g} €/hour".strip(": "))
        elif period or amount:
            conditions.append(table_text)

    # Legacy prose prices are parsed only inside the dedicated tariff section.
    if not tables:
        for match in re.finditer(r"(?<![\d.,-])(\d+(?:[.,]\d{1,4})?)\s*€\s*/\s*(kWh|hora|hour|h)\b", text, re.I):
            amount = _number(match.group(1))
            if amount is not None:
                energy = match.group(2).casefold() == "kwh"
                component("ENERGY" if energy else "TIME", amount, "kWh" if energy else "hour", match.group(0))
        if re.fullmatch(r"\s*(?:gratis|gratuito|0(?:[.,]0+)?\s*€)\s*", text, re.I):
            component("FREE", 0.0, "session", text)
        elif re.search(r"gratis|gratuito|free", text, re.I):
            component("FREE_PERIOD", 0.0, "condition", text, condition=text)
            conditions.append(text)
        if re.search(r"\b(?:solo|clientes?|socios?|segun|depende|horario|primer|partir|desde|hasta|after|members?|customers?)\b", _fold(text)):
            conditions.append(text)

    for match in re.finditer(r"\b[Mm][íi]nimo\s*(?::|de)?\s*(\d+(?:[.,]\d{1,3})?)\s*€", text):
        amount = _number(match.group(1))
        if amount is not None:
            component("MINIMUM", amount, "session", match.group(0))
            conditions.append(match.group(0))
    for match in re.finditer(
            r"(?:inicio|activaci[oó]n|por\s+sesi[oó]n|sesi[oó]n|session)\s*(?::|de)?\s*(\d+(?:[.,]\d{1,3})?)\s*€"
            r"|(\d+(?:[.,]\d{1,3})?)\s*€\s*(?:por\s+)?(?:sesi[oó]n|session|inicio|activaci[oó]n)", text, re.I):
        amount = _number(match.group(1) or match.group(2))
        if amount is not None:
            component("FLAT", amount, "session", match.group(0))
            conditions.append(match.group(0))

    conditions = list(dict.fromkeys(_clean(s).lstrip("* ") for s in conditions if _clean(s)))
    energy_rates = sorted({c["amount"] for c in components if c["type"] == "ENERGY"})
    hourly_rates = sorted({c["amount"] for c in components if c["type"] == "TIME"})
    minimums = sorted({c["amount"] for c in components if c["type"] == "MINIMUM"})
    non_energy = any(c["type"] in {"TIME", "MINIMUM", "FLAT"} and c["amount"] > 0
                     for c in components)
    conditional_free = any(c["type"] == "FREE_PERIOD" for c in components)
    unconditional_free = bool(components and not conditions and not non_energy and not conditional_free
                              and all(c["amount"] == 0 for c in components))
    energy_rate = energy_rates[0] if len(energy_rates) == 1 else None
    return {"components": components, "conditions": conditions, "text": text,
            "energy_eur_per_kwh": energy_rate, "energy_rates_eur_per_kwh": energy_rates,
            "hour_eur": hourly_rates[0] if len(hourly_rates) == 1 else None,
            "minimum_eur": minimums[0] if len(minimums) == 1 else None,
            "unconditional_free": unconditional_free, "has_non_energy_cost": non_energy,
            "conditional_free": conditional_free,
            "usable_as_simple_energy_price": bool(
                (energy_rate is not None or unconditional_free) and not conditions and not non_energy
                and not conditional_free),
            "source_url": source_url, "provenance": _provenance(source_url, fetched_at, text)}


def parse_canarias_detail(html_text: str, source_url: str, *, fetched_at: str = "") -> dict[str, Any]:
    """Return bounded, attributed charger-page facts; missing facts remain empty.

    ``warnings`` records source unit discrepancies and socket/bay differences.
    ``contact`` is the contact printed in the station section, often the map
    publisher's contact rather than an operator support telephone.
    """
    url = safe_detail_url(source_url)
    result: dict[str, Any] = {"recognized": False, "source_url": url, "canonical_url": "", "name": "",
                             "station_name": "", "address": "", "coordinates": None,
                             "description": "", "access": "", "hours": "", "contact": {},
                             "connectors": [], "port_count": None, "bay_count": None,
                             "max_power_kw": None, "photos": [], "tariff": {},
                             "warnings": [], "field_provenance": {}}
    if not url or not isinstance(html_text, str) or len(html_text) > MAX_HTML_CHARS:
        result["warnings"].append("unsafe_source_url_or_html_budget")
        return result
    tree = _Tree()
    try:
        tree.feed(html_text)
        tree.finish()
    except (ValueError, RecursionError):
        result["warnings"].append("malformed_html_or_parser_budget")
        return result
    all_nodes = list(_nodes(tree.root))
    roots = [n for n in all_nodes if n.attrs.get("id") == "page-content"]
    if len(roots) != 1:
        return result
    root = roots[0]
    nodes = list(_nodes(root))
    title_nodes = [n for n in all_nodes if n.has_class("page-title")]
    if not title_nodes:
        title_nodes = [n for n in all_nodes if n.attrs.get("id") == "page-header"]
    title = next((_text(h) for n in title_nodes for h in _nodes(n) if h.tag == "h1" and _text(h)), "")
    if not title:
        # Two station URLs are WordPress posts, whose body lacks #page-header.
        # The browser title still names the station; strip only the site suffix.
        title = next((_text(n).split(" | ")[0] for n in all_nodes if n.tag == "title"), "")
    # The older museum/parking pages may omit the conventional charger title.
    # A location, connector section and tariff section must still be present.
    headings = [(n, _section_kind(_text(n))) for n in nodes if n.tag in {"h1", "h2", "h3", "h4"} or (
        n.tag == "p" and _fold(_text(n)) in {"indicaciones", "localizacion", "ubicacion"})]
    headings = [(n, kind) for n, kind in headings if kind and not (
        kind == "tariff" and n.has_class("elementor-price-table__heading"))]
    kinds = {kind for _, kind in headings}
    charger_title = bool(re.search(r"puntos? de (?:re)?carga|charging point|cargador", _fold(title)))
    if not {"location", "connectors"}.issubset(kinds) or not ("tariff" in kinds or charger_title):
        return result
    if re.search(r"\b(?:404|not found|no encontrada?|no existe|pagina de error)\b", _fold(title)):
        return result
    sections: dict[str, list[tuple[int, int]]] = {}
    for index, (heading, kind) in enumerate(headings):
        end = headings[index + 1][0].start if index + 1 < len(headings) else root.end
        sections.setdefault(kind, []).append((heading.end, end))
        if kind == "connectors":
            match = re.search(r"\b(\d{1,3})\s+plazas?\b", _text(heading), re.I)
            if match:
                result["bay_count"] = int(match.group(1))

    text_tokens = list(_texts(root))
    def section_text(kind: str) -> str:
        return _clean(" ".join(token.value for lo, hi in sections.get(kind, [])
                               for token in text_tokens if lo < token.start < hi))

    def section_nodes(kind: str) -> list[_Node]:
        return [n for n in nodes if any(lo < n.start < hi for lo, hi in sections.get(kind, []))]

    canonical = next((safe_detail_url(n.attrs.get("href", "")) for n in all_nodes
                      if n.tag == "link" and "canonical" in n.attrs.get("rel", "").split()), "")
    station_name = re.sub(r"^(?:Puntos? de (?:re)?carga|Charging point)\s*[-–—:]?\s*", "", title, flags=re.I)
    result.update(recognized=True, name=_clean(title), station_name=_clean(station_name),
                  canonical_url=canonical or url, address=section_text("location"),
                  coordinates=_map_coordinates(root),
                  description=section_text("description"), access=section_text("access"),
                  hours=section_text("hours"))
    construction = re.match(r"^\s*(EN\s+CONSTRUCCI[ÓO]N)\s*", result["address"], re.I)
    if construction:
        result["access"] = _clean(" ".join(filter(None, [result["access"], construction.group(1)])))
        result["address"] = result["address"][construction.end():]
        result["warnings"].append("The source explicitly marks this charger EN CONSTRUCCIÓN; no usable-now claim is made.")
    for key, kind in [("name", "title"), ("address", "location"), ("description", "description"),
                      ("access", "access"), ("hours", "hours")]:
        if result[key]:
            result["field_provenance"][key] = _provenance(url, fetched_at, result[key])
    if result["coordinates"]:
        result["field_provenance"]["coordinates"] = _provenance(
            url, fetched_at, result["coordinates"]["source_map_url"])

    contact_text = section_text("contact")
    if contact_text:
        emails = re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", contact_text)
        phones = re.findall(r"(?<!\d)(?:\+34\s*)?(?:\d[ ()-]?){8,12}\d(?!\d)", contact_text)
        result["contact"] = {"text": contact_text, "emails": list(dict.fromkeys(emails)),
                             "phones": list(dict.fromkeys(_clean(p) for p in phones)),
                             "scope": "directory_contact" if "canariasterecarga" in _fold(contact_text).replace(" ", "")
                             else "source_published_contact",
                             "provenance": _provenance(url, fetched_at, contact_text)}
        result["field_provenance"]["contact"] = _provenance(url, fetched_at, contact_text)

    connector_nodes = section_nodes("connectors")
    # One real page puts its contact heading before the connector widgets.
    # Explicit icon-box rating rows still supply hardware, while unrelated
    # decorative/contact boxes without connector facts do not.
    rows = [n for n in nodes if n.has_class("elementor-icon-box-wrapper") and (
        (re.search(r"\bx\s*\d+\b", _text(n), re.I) and re.search(r"\d\s*kWh?\b", _text(n), re.I))
        or _icon_label(n, url)[0] != "Unknown")]
    if not rows:
        # Legacy rows pair a pictogram, xN and a kW rating inside a table cell
        # or paragraph. Select the smallest such block, never the whole column.
        candidates = [n for n in connector_nodes if n.tag in {"p", "td", "li", "div"}
                      and re.search(r"\bx\s*\d+\b", _text(n), re.I)
                      and re.search(r"\d\s*kWh?\b", _text(n), re.I)]
        rows = [n for n in candidates if not any(
            other is not n and n.start < other.start < n.end for other in candidates)]
        rows = [chunk for row in rows for chunk in _legacy_chunks(row)]
    for row in rows[:100]:
        connector = _connector(row, url, fetched_at)
        if connector:
            result["connectors"].append(connector)
            if connector["raw"].get("power_unit_inferred"):
                result["warnings"].append(connector["raw"]["power_normalization_note"])
    connectors = result["connectors"]
    if connectors and all(c["count"] is not None for c in connectors):
        result["port_count"] = sum(c["count"] for c in connectors)
    if result["bay_count"] is None:
        match = re.search(r"\b(?:Disponible\s+)?(\d{1,3})\s+plazas?\b", section_text("connectors"), re.I)
        if match:
            result["bay_count"] = int(match.group(1))
    if result["bay_count"] is not None and result["port_count"] is not None and result["bay_count"] != result["port_count"]:
        result["warnings"].append("Published parking-space count differs from connector count; spaces are not sockets or live availability.")
    powers = [c["power_kw"] for c in connectors if c["power_kw"] is not None]
    result["max_power_kw"] = max(powers) if powers else None
    if connectors:
        result["field_provenance"]["connectors"] = _provenance(url, fetched_at, " ".join(c["raw"]["source_text"] for c in connectors))
    result["tariff"] = _parse_tariff(section_nodes("tariff"), section_text("tariff"), url, fetched_at)
    result["field_provenance"]["tariff"] = result["tariff"]["provenance"]

    seen_photos: set[str] = set()
    for image in (n for n in nodes if n.tag == "img"):
        raw_url = image.attrs.get("src") or image.attrs.get("data-src") or ""
        photo_url = safe_photo_url(raw_url, url)
        if not photo_url or photo_url in seen_photos:
            continue
        filename = urllib.parse.unquote(photo_url.rsplit("/", 1)[-1]).casefold()
        if re.search(r"(?:conector|connector|ev-plug|icono|logo|g7526|badge|banner)", filename):
            continue
        try:
            width, height = int(image.attrs.get("width", "0")), int(image.attrs.get("height", "0"))
        except ValueError:
            width = height = 0
        if (width and width < 100) or (height and height < 100):
            continue
        # An image inside the connector section is a socket pictogram, even
        # when its filename is unhelpful. It cannot become a charger photo.
        if any(lo < image.start < hi for lo, hi in sections.get("connectors", [])):
            continue
        in_tariff = any(lo < image.start < hi for lo, hi in sections.get("tariff", []))
        caption = _clean(image.attrs.get("alt", ""), 240)
        if not caption:
            caption = "Source image published beside tariff information" if in_tariff else "Image published on the charger page"
        seen_photos.add(photo_url)
        result["photos"].append({"url": photo_url, "thumbnail_url": photo_url, "caption": caption,
                                 "kind": "source_published_image", "section": "tariff" if in_tariff else "charger_page",
                                 "source": SOURCE_NAME, "source_url": url,
                                 "license": "source-published", "provenance": _provenance(url, fetched_at, caption)})
        if len(result["photos"]) >= 4:
            break
    if result["photos"]:
        result["field_provenance"]["photos"] = _provenance(url, fetched_at, "Images inside the charger page content")
    result["warnings"] = list(dict.fromkeys(result["warnings"]))
    return result
