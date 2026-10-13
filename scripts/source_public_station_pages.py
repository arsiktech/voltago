#!/usr/bin/env python3
"""Public station-page parsers: Telpark (ES parking + EV flag) and Elektrum Drive (Baltics).

Standard library only. Parsers are pure functions over already-fetched text; the
``fetch_*`` orchestrators take an injectable ``fetch(url, headers=..., timeout=...)``
callable (returning ``str``, ``bytes`` or already-parsed JSON) and are bounded by page
and request caps. Nothing here writes to the repository's intelligence files; the
caller integrates the returned raw rows.

Evidence contract (do not strengthen downstream):

* Rows are ``directory`` evidence: a public operator web page says a place exists and
  (Telpark) that it advertises EV charging, or (Elektrum) lists connectors and power.
* No live availability, no charging tariff, no invented plugs/power. Telpark gives a
  boolean EV flag only. Elektrum gives per-station connector text but no price.
* Telpark's parking price text is parking cost, kept in ``parking_price_text`` and never
  mapped to a charging tariff.
* Telpark rows for Portugal (and Andorra) are excluded from the Spain list. Elektrum Drive rows are
  Baltic (EE/LV/LT) and are returned separately; there are no Spain rows by design.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
import unicodedata
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

try:  # package import (tests / ingest_sources) and script import both work
    from .source_transport import policy_urlopen
except ImportError:  # pragma: no cover - script execution
    from source_transport import policy_urlopen  # type: ignore

USER_AGENT = "Mozilla/5.0 (compatible; VoltaGoDirectory/1.0; public GET only)"
TELPARK_HOST = "www.telpark.com"
TELPARK_REST_URL = "https://www.telpark.com/es/wp-json/wp/v2/parkings?per_page={per_page}&page={page}"
ELEKTRUM_URL = "https://elektrum.ee/en/elektrum-drive/charging-solutions/public-charging-points"
ELEKTRUM_ALLOWED_HOSTS = {"elektrum.ee", "www.elektrum.ee"}

TELPARK_SOURCE = "telpark"
ELEKTRUM_SOURCE = "elektrum_drive"
DEFAULT_TIMEOUT = 30.0
MAX_TELPARK_PAGES = 10
MAX_TELPARK_DETAIL_FETCHES = 300
MAX_BODY_BYTES = 8_000_000

Fetch = Callable[..., Any]

# Telpark city slugs (the /es/ciudades/<slug>/ path segment) that are in Portugal.
TELPARK_PORTUGAL_CITY_SLUGS = frozenset({
    "lisboa", "oporto", "coimbra", "vila-nova-de-gaia", "viseu", "aveiro", "leiria",
    "covilha", "sesimbra", "barreiro", "almada", "santarem", "povoa-do-varzim",
    "torres-vedras", "cascais", "loures", "amadora", "figueira-da-foz", "faro",
    "fundao", "albufeira", "portimao", "braga", "guimaraes", "setubal", "evora",
    "funchal", "matosinhos", "maia", "gondomar", "oeiras", "sintra", "seixal",
})
_PT_POSTCODE = re.compile(r"\b\d{4}-\d{3}\b")
_ES_POSTCODE = re.compile(r"\b\d{5}\b")


# --------------------------------------------------------------------------- helpers

def _now_iso(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def default_fetch(url: str, *, headers: Optional[Dict[str, str]] = None, timeout: float = DEFAULT_TIMEOUT) -> bytes:
    """Bounded anonymous GET through the reviewed transport policy."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})}, method="GET")
    with policy_urlopen(request, timeout=timeout) as response:
        return response.read(MAX_BODY_BYTES + 1)[:MAX_BODY_BYTES]


# --------------------------------------------------------------------------- Telpark

_AD_POSTCODE = re.compile(r"\bAD\d{3}\b", re.I)
_ANDORRA_BOX = (42.42, 42.66, 1.40, 1.80)
# Broad Spain boxes: mainland+Balearics, Canaries, Ceuta/Melilla. Portugal is excluded first by
# slug/postcode/phone, so mainland overlap with Portugal does not matter here.
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
    for node in _iter_jsonld_nodes(text):
        features = node.get("amenityFeature")
        geo = node.get("geo")
        if not isinstance(features, list) and not isinstance(geo, dict):
            continue
        if isinstance(features, list):
            names = {}
            for feat in features:
                if isinstance(feat, dict) and feat.get("name"):
                    names[_fold(feat["name"])] = _truthy(feat.get("value"))
            if names:
                result["ev_charging"] = bool(names.get("carga electrica", False))
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


def merge_telpark_detail(row: Dict[str, Any], detail: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    if detail.get("ev_charging") is not None:
        out["ev_charging"] = detail["ev_charging"]
        out["ev_flag_source"] = "detail_jsonld_amenityFeature"
    if _valid_coords(detail.get("lat"), detail.get("lon")):
        out["lat"], out["lon"] = detail["lat"], detail["lon"]
        out["coords_source"] = "detail_jsonld_geo"
    elif out.get("lat") is not None:
        out["coords_source"] = "rest_meta_box_header"
    phone = str(detail.get("telephone") or "")
    if out.get("country") != "PT" and phone.replace(" ", "").startswith("+351"):
        out["country"] = "PT"
    elif out.get("country") == "unknown":
        out["country"] = telpark_country(out.get("city_slug") or "", out.get("address") or "", phone,
                                         out.get("lat"), out.get("lon"))
    return out


def telpark_station_row(row: Dict[str, Any], *, retrieved_at: str) -> Dict[str, Any]:
    """Final raw source row for a confirmed EV-capable Telpark parking."""
    return {
        "source": TELPARK_SOURCE,
        "source_id": row["source_id"],
        "name": row["name"],
        "operator": "Telpark",
        "lat": row["lat"],
        "lon": row["lon"],
        "address": row.get("address"),
        "country": row.get("country"),
        "city_slug": row.get("city_slug"),
        "url": row.get("url"),
        "ev_charging": True,
        "ev_flag_source": row.get("ev_flag_source"),
        "coords_source": row.get("coords_source"),
        "connectors": [],          # not published: never infer plugs
        "power_kw": None,          # not published: never infer power
        "charging_tariff": None,   # not published
        "availability": None,      # no live status
        "parking_price_text": row.get("parking_price_text"),  # parking cost only
        "evidence": "directory",
        "retrieved_at": retrieved_at,
    }


def fetch_telpark_rows(
    fetch: Optional[Fetch] = None,
    *,
    per_page: int = 100,
    max_pages: int = 5,
    max_detail_fetches: int = 250,
    pause_seconds: float = 0.0,
    timeout: float = DEFAULT_TIMEOUT,
    now: Optional[datetime] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    """REST list -> Spain filter -> detail JSON-LD EV flag. Bounded, partial-failure tolerant.

    Returns ``rows`` (Spain, EV confirmed), ``excluded_portugal`` (count + ids), and
    ``stats``/``errors``. Parkings whose detail could not be fetched stay unconfirmed and
    are not emitted as charging rows.
    """
    fetch = fetch or default_fetch
    per_page = max(1, min(int(per_page), 100))
    max_pages = max(1, min(int(max_pages), MAX_TELPARK_PAGES))
    max_detail_fetches = max(0, min(int(max_detail_fetches), MAX_TELPARK_DETAIL_FETCHES))
    retrieved_at = _now_iso(now)
    errors: List[str] = []
    candidates: Dict[str, Dict[str, Any]] = {}
    pages = 0
    for page in range(1, max_pages + 1):
        url = TELPARK_REST_URL.format(per_page=per_page, page=page)
        try:
            parsed = parse_telpark_parkings_page(fetch(url, headers={"Accept": "application/json"}, timeout=timeout))
        except Exception as exc:  # noqa: BLE001 - report and stop paging
            errors.append(f"rest page {page}: {type(exc).__name__}: {str(exc)[:120]}")
            break
        pages += 1
        for row in parsed:
            candidates.setdefault(row["source_id"], row)
        if len(parsed) < per_page:
            break
        if pause_seconds:
            sleep(pause_seconds)

    excluded_pt = sorted(r["source_id"] for r in candidates.values() if r["country"] == "PT")
    spain = [r for r in candidates.values() if r["country"] == "ES"]
    unknown_country = sorted(r["source_id"] for r in candidates.values() if r["country"] == "unknown")
    excluded_other = sorted(r["source_id"] for r in candidates.values() if r["country"] == "AD")

    confirmed: List[Dict[str, Any]] = []
    ev_false = 0
    unconfirmed = 0
    detail_fetched = 0
    for row in spain:
        if detail_fetched >= max_detail_fetches or not row.get("url"):
            unconfirmed += 1
            continue
        if pause_seconds:
            sleep(pause_seconds)
        detail_fetched += 1
        try:
            merged = merge_telpark_detail(row, parse_telpark_detail(fetch(row["url"], timeout=timeout)))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"detail {row['source_id']}: {type(exc).__name__}: {str(exc)[:100]}")
            unconfirmed += 1
            continue
        if merged["country"] == "PT":  # phone disagreed with the slug/postcode guess
            excluded_pt.append(merged["source_id"])
            continue
        if merged["ev_charging"] is True and merged["lat"] is not None:
            confirmed.append(telpark_station_row(merged, retrieved_at=retrieved_at))
        elif merged["ev_charging"] is False:
            ev_false += 1
        else:
            unconfirmed += 1
    confirmed.sort(key=lambda r: r["source_id"])
    return {
        "source": TELPARK_SOURCE,
        "geography": "ES",
        "rows": confirmed,
        "excluded_portugal": sorted(set(excluded_pt)),
        "excluded_andorra": excluded_other,
        "unknown_country": unknown_country,
        "errors": errors,
        "stats": {
            "rest_pages": pages,
            "rest_unique": len(candidates),
            "spain_candidates": len(spain),
            "portugal_excluded": len(set(excluded_pt)),
            "andorra_excluded": len(excluded_other),
            "unknown_country": len(unknown_country),
            "detail_fetched": detail_fetched,
            "ev_confirmed": len(confirmed),
            "ev_false": ev_false,
            "ev_unconfirmed": unconfirmed,
        },
        "retrieved_at": retrieved_at,
    }


# --------------------------------------------------------------------------- Elektrum

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


# Rough national boxes (lat_min, lat_max, lon_min, lon_max); they overlap at borders.
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
    rows: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    for st in parser.stations:
        a = st["attrs"]
        lat, lon = _float(a.get("data-latitude")), _float(a.get("data-longitude"))
        if lat is None or lon is None or not _valid_coords(lat, lon):
            continue
        name = st["name"]
        country, method = elektrum_country(lat, lon, st["address"], st["city"])
        code_match = re.search(r"\((\d{3,6})\)\s*$", name)
        # Stable: partner stations carry a printed code; own stations are keyed by name + coords.
        key_src = f"{_fold(name)}|{lat:.5f}|{lon:.5f}"
        digest = hashlib.sha1(key_src.encode("utf-8")).hexdigest()[:10]
        base = f"elektrum-drive:{digest}"
        seen[base] = seen.get(base, 0) + 1
        source_id = base if seen[base] == 1 else f"{base}-{seen[base]}"
        connectors = [parse_elektrum_connector_line(c) for c in st["connectors"]]
        powers = [c["power_kw"] for c in connectors if c["power_kw"] is not None]
        rows.append({
            "source": ELEKTRUM_SOURCE,
            "source_id": source_id,
            "station_code": code_match.group(1) if code_match else None,
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
        })
    return rows


def fetch_elektrum_rows(
    fetch: Optional[Fetch] = None,
    *,
    url: str = ELEKTRUM_URL,
    timeout: float = DEFAULT_TIMEOUT,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """One bounded GET of the public list. Baltic rows only; ``spain_rows`` is always 0."""
    fetch = fetch or default_fetch
    host = re.sub(r"^https://([^/]+)/.*$", r"\1", url)
    if not url.startswith("https://") or host not in ELEKTRUM_ALLOWED_HOSTS:
        raise ValueError("Elektrum URL must be https on elektrum.ee")
    retrieved_at = _now_iso(now)
    errors: List[str] = []
    rows: List[Dict[str, Any]] = []
    try:
        rows = parse_elektrum_public_points(fetch(url, timeout=timeout))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{type(exc).__name__}: {str(exc)[:120]}")
    for row in rows:
        row["retrieved_at"] = retrieved_at
    by_country: Dict[str, int] = {}
    for row in rows:
        by_country[row["country"]] = by_country.get(row["country"], 0) + 1
    return {
        "source": ELEKTRUM_SOURCE,
        "geography": "EE/LV/LT (Baltics)",
        "rows": rows,
        "spain_rows": 0,
        "errors": errors,
        "stats": {"rows": len(rows), "by_country": dict(sorted(by_country.items())),
                  "unique_ids": len({r["source_id"] for r in rows})},
        "retrieved_at": retrieved_at,
    }


# --------------------------------------------------------------------------- CLI smoke

def fetch_public_station_pages(fetch: Optional[Fetch] = None, **kwargs: Any) -> Dict[str, Any]:
    telpark_keys = {"per_page", "max_pages", "max_detail_fetches", "pause_seconds", "timeout", "now", "sleep"}
    return {
        "telpark": fetch_telpark_rows(fetch, **{k: v for k, v in kwargs.items() if k in telpark_keys}),
        "elektrum_drive": fetch_elektrum_rows(fetch, **{k: v for k, v in kwargs.items() if k in {"timeout", "now"}}),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Public smoke fetch for Telpark and Elektrum Drive.")
    ap.add_argument("--out-dir", required=True, help="private directory for full rows + samples (outside the repo)")
    ap.add_argument("--max-detail", type=int, default=250)
    ap.add_argument("--pause", type=float, default=0.15)
    args = ap.parse_args(argv)
    import os
    os.makedirs(args.out_dir, exist_ok=True)
    result = fetch_public_station_pages(max_detail_fetches=args.max_detail, pause_seconds=args.pause)
    summary: Dict[str, Any] = {}
    for key, block in result.items():
        with open(os.path.join(args.out_dir, f"{key}_rows.json"), "w", encoding="utf-8") as fh:
            json.dump(block, fh, ensure_ascii=False, indent=1)
        summary[key] = {"stats": block["stats"], "errors": block["errors"][:5], "sample": block["rows"][:3]}
    with open(os.path.join(args.out_dir, "smoke_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    print(json.dumps({k: {"stats": v["stats"], "errors": len(v["errors"])} for k, v in summary.items()}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
