"""Crawlable Spanish pages for search engines: city lists, station pages and the sitemap.

Owner decisions (2026-10-24): Spanish URLs and text; a station gets its own page only when
two or more source families list it or a source publishes a price or free claim; a city gets
a page only with five or more stations. Pages never claim live availability or a price no
source publishes, and they link to the app for live status. Standard library only.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

try:
    from .spain_geography import point_in_spain
except ImportError:  # executed as a script from scripts/
    from spain_geography import point_in_spain

SITE_URL = os.environ.get("VOLTA_PUBLIC_URL", "https://voltago.app").rstrip("/")
CITY_MIN_STATIONS = 5
CITY_LIST_LIMIT = 400
# City labels that are not a place (placeholders, regions, address fragments).
NOT_A_CITY = {"", "spain", "españa", "espana", "valencia province", "s/n", "sn", "?", "-"}


def slugify(text: Any) -> str:
    folded = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")


def source_families(record: Dict[str, Any]) -> List[str]:
    names = []
    for source in record.get("sources") or []:
        name = str(source.get("name") or "").split(" (")[0].strip()
        if name and name not in names:
            names.append(name)
    return names


def has_page(record: Dict[str, Any], family_count: Optional[int] = None) -> bool:
    families = len(source_families(record)) if family_count is None else family_count
    return families >= 2 or record.get("price_min") is not None or bool(record.get("is_free"))


def station_digest(record: Dict[str, Any]) -> str:
    """The stable part of a station URL: a hash of the station ID, not of its name."""
    return hashlib.sha1(str(record.get("id")).encode("utf-8")).hexdigest()[:8]


def station_slug(record: Dict[str, Any]) -> str:
    return f"{slugify(record.get('name'))[:60].strip('-') or 'cargador'}-{station_digest(record)}"


def in_spain(record: Dict[str, Any]) -> bool:
    """Spanish pages only: an explicit other country, or a point outside Spain, is skipped."""
    raw = record.get("raw") or {}
    country = str(record.get("country_code") or record.get("country") or raw.get("country_code") or "").upper()
    if country and country not in {"ES", "ESP"}:
        return False
    return point_in_spain(record.get("lat"), record.get("lon"))


# Fields a city list needs. The full record is fetched only when its station page is asked for,
# so a large disk-backed catalogue is never held in memory.
COMPACT_FIELDS = ("id", "name", "address", "city", "lat", "lon", "price_min", "is_free", "connector_labels",
                  "max_power_kw", "total_evse")


def city_label(record: Dict[str, Any]) -> Optional[str]:
    city = re.sub(r"\s+", " ", str(record.get("city") or "")).strip()
    if city.casefold() in NOT_A_CITY or len(slugify(city)) < 2 or city.isdigit():
        return None
    return city


class SiteIndex:
    """Cities and station pages for one data version, held as compact summaries."""

    def __init__(
        self,
        records: Iterable[Dict[str, Any]],
        generated_at: Optional[str],
        *,
        families: Optional[Callable[[Dict[str, Any]], Iterable[str]]] = None,
        lookup: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
    ) -> None:
        self.generated_at = generated_at
        count_families = (lambda record: len(set(families(record)))) if families else (lambda record: len(source_families(record)))
        by_city: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        spellings: Dict[str, Counter] = defaultdict(Counter)
        self.stations: Dict[str, Dict[str, Any]] = {}
        kept: Dict[str, Dict[str, Any]] = {}
        for record in records:
            if not isinstance(record.get("lat"), (int, float)) or not isinstance(record.get("lon"), (int, float)):
                continue
            if not in_spain(record):
                continue
            compact = {key: record.get(key) for key in COMPACT_FIELDS}
            compact["families"] = count_families(record)
            compact["price_text"] = price_text(record)
            compact["page"] = has_page(record, compact["families"])
            compact["slug"] = station_slug(record)
            label = city_label(record)
            if label:
                key = slugify(label)
                by_city[key].append(compact)
                spellings[key][label] += 1
            if compact["page"]:
                self.stations[station_digest(record)] = compact
                if lookup is None:
                    kept[str(record.get("id"))] = record
        self.cities = {key: rows for key, rows in by_city.items() if len(rows) >= CITY_MIN_STATIONS}
        self.city_names = {key: spellings[key].most_common(1)[0][0] for key in self.cities}
        self._lookup = lookup or kept.get

    def city_of(self, record: Dict[str, Any]) -> Optional[str]:
        label = city_label(record)
        return slugify(label) if label and slugify(label) in self.cities else None

    def full_record(self, digest: str) -> Optional[Dict[str, Any]]:
        compact = self.stations.get(digest)
        return self._lookup(str(compact["id"])) if compact else None


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _date(value: Optional[str]) -> str:
    return str(value or "")[:10] or "fecha desconocida"


def price_text(record: Dict[str, Any]) -> str:
    claim = record.get("charge_claim") or {}
    source = claim.get("source") or record.get("source") or "la fuente"
    # A price is only as current as the fetch that carried it; say when that was.
    fetched = next((entry.get("source_fetched_at") for entry in record.get("sources") or []
                    if entry.get("name") == source and entry.get("source_fetched_at")), None) or record.get("source_fetched_at")
    dated = f" (dato del {_date(fetched)})" if fetched else ""
    if record.get("is_free"):
        return f"Gratis según {source}{dated}. Comprueba las condiciones antes de ir."
    if record.get("price_min") is not None:
        return f"Desde {record['price_min']:.2f} €/kWh según {source}{dated}."
    return "Ninguna fuente publica el precio. Compruébalo en la app del operador."


def _hardware(record: Dict[str, Any]) -> str:
    parts = []
    labels = record.get("connector_labels") or []
    if labels:
        parts.append(", ".join(labels))
    if record.get("max_power_kw"):
        parts.append(f"hasta {record['max_power_kw']:g} kW")
    if record.get("total_evse"):
        parts.append(f"{record['total_evse']} puntos")
    return " · ".join(parts) or "Conectores no publicados"


STYLE = """
body{font:16px/1.55 system-ui,-apple-system,sans-serif;margin:0;color:#0e1512;background:#f5f6f4}
main{max-width:860px;margin:0 auto;padding:24px 16px 48px}
a{color:#0d6b37}h1{font-size:1.6rem;line-height:1.25;margin:.2em 0 .4em}
.muted{color:#5a6560}.card{background:#fff;border:1px solid rgba(14,21,18,.1);border-radius:12px;padding:14px 16px;margin:10px 0}
ul.list{list-style:none;padding:0}ul.list li{background:#fff;border:1px solid rgba(14,21,18,.1);border-radius:10px;padding:10px 14px;margin:8px 0}
.cta{display:inline-block;background:#0e1512;color:#fff;padding:10px 16px;border-radius:10px;text-decoration:none;margin:4px 8px 4px 0}
details{margin:6px 0}footer{margin-top:32px;font-size:.9rem}
@media (prefers-color-scheme:dark){body{background:#0b0f0d;color:#edf1ee}.card,ul.list li{background:#141a17;border-color:rgba(237,241,238,.12)}a{color:#5fd393}.muted{color:#9aa59f}.cta{background:#edf1ee;color:#0b0f0d}}
"""


def _page(title: str, description: str, path: str, body: str, json_ld: Optional[Dict[str, Any]] = None) -> str:
    ld = ""
    if json_ld:
        ld = '<script type="application/ld+json">' + json.dumps(json_ld, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026") + "</script>"
    return (
        '<!doctype html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_e(title)}</title><meta name=\"description\" content=\"{_e(description)}\">"
        f'<link rel="canonical" href="{_e(SITE_URL + path)}"><style>{STYLE}</style>{ld}</head>'
        f"<body><main>{body}"
        '<footer class="muted">Volta Go reúne fuentes públicas de puntos de recarga en España. '
        "La disponibilidad en directo y los precios se comprueban en la app y en el punto de recarga. "
        '<a href="/cargadores">Todas las ciudades</a> · <a href="/">Abrir Volta Go</a></footer>'
        "</main></body></html>"
    )


def render_index(index: SiteIndex) -> str:
    rows = sorted(index.cities, key=lambda key: index.city_names[key].casefold())
    items = "".join(f'<li><a href="/cargadores/{key}">{_e(index.city_names[key])}</a> '
                    f'<span class="muted">· {len(index.cities[key])} puntos</span></li>' for key in rows)
    body = (f"<h1>Puntos de recarga por ciudad</h1><p class=\"muted\">{len(rows)} ciudades con al menos "
            f"{CITY_MIN_STATIONS} puntos de recarga. Datos de {_date(index.generated_at)}.</p><ul class=\"list\">{items}</ul>")
    return _page("Puntos de recarga para coches eléctricos por ciudad | Volta Go",
                 "Puntos de recarga para coches eléctricos en España por ciudad, con fuentes públicas.",
                 "/cargadores", body)


def render_city(index: SiteIndex, key: str) -> Optional[str]:
    rows = index.cities.get(key)
    if rows is None:
        return None
    name = index.city_names[key]
    ordered = sorted(rows, key=lambda r: (not r["page"], -r["families"], str(r.get("name") or "")))
    items = []
    for record in ordered[:CITY_LIST_LIMIT]:
        title = _e(record.get("name") or "Punto de recarga")
        if record["page"]:
            title = f'<a href="/cargador/{record["slug"]}">{title}</a>'
        families = record["families"]
        items.append(f"<li><strong>{title}</strong><br><span class=\"muted\">{_e(record.get('address') or '')}</span><br>"
                     f"{_e(_hardware(record))} · {_e(record['price_text'])} "
                     f"<span class=\"muted\">· {families} fuente{'s' if families != 1 else ''}</span></li>")
    more = (f"<p class=\"muted\">Se muestran {CITY_LIST_LIMIT} de {len(rows)}. Abre el mapa para verlos todos.</p>"
            if len(rows) > CITY_LIST_LIMIT else "")
    free = sum(1 for r in rows if r.get("is_free"))
    body = (f"<h1>Puntos de recarga en {_e(name)}</h1>"
            f"<p>{len(rows)} puntos de recarga conocidos en {_e(name)}"
            f"{f', {free} con carga gratuita según alguna fuente' if free else ''}. Datos de {_date(index.generated_at)}.</p>"
            '<p><a class="cta" href="/">Ver disponibilidad en el mapa</a></p>'
            f'<ul class="list">{"".join(items)}</ul>{more}')
    return _page(f"Puntos de recarga en {name} | Volta Go",
                 f"{len(rows)} puntos de recarga para coches eléctricos en {name}, con conectores, potencia y fuentes públicas.",
                 f"/cargadores/{key}", body)


def render_station(index: SiteIndex, digest: str) -> Optional[str]:
    compact = index.stations.get(digest)
    record = index.full_record(digest)
    if compact is None or record is None:
        return None
    slug = compact["slug"]
    name = str(record.get("name") or "Punto de recarga")
    city_key = index.city_of(record)
    city = index.city_names.get(city_key, city_label(record) or "") if city_key else (city_label(record) or "")
    city_html = f'<a href="/cargadores/{city_key}">{_e(city)}</a>' if city_key else _e(city)
    operator = (record.get("raw") or {}).get("operator")
    sources = []
    for source in record.get("sources") or []:
        label = _e(source.get("name") or "Fuente")
        url = str(source.get("url") or "")
        link = f'<a href="{_e(url)}" rel="nofollow noopener">{label}</a>' if url.startswith("https://") else label
        licence = f" · Licencia: {_e(source['license'])}" if source.get("license") else ""
        attribution = f" · {_e(source['attribution'])}" if source.get("attribution") else ""
        fetched = f" · consultado {_date(source.get('source_fetched_at') or record.get('source_fetched_at'))}"
        evidence = f"<details><summary>Detalle de la fuente (en inglés)</summary>{_e(source.get('evidence'))}</details>" \
            if source.get("evidence") else ""
        sources.append(f"<li>{link}<span class=\"muted\">{attribution}{licence}{fetched}</span>{evidence}</li>")
    directions = ((record.get("navigation") or {}).get("google")
                  or f"https://www.google.com/maps/search/?api=1&query={record['lat']:.6f},{record['lon']:.6f}")
    body = (f"<p class=\"muted\">{city_html}</p><h1>{_e(name)}</h1>"
            f"<p>{_e(record.get('address') or '')}</p>"
            f'<div class="card"><p><strong>Precio:</strong> {_e(price_text(record))}</p>'
            f"<p><strong>Conectores:</strong> {_e(_hardware(record))}</p>"
            + (f"<p><strong>Operador:</strong> {_e(operator)}</p>" if operator else "")
            + "<p class=\"muted\">La disponibilidad en directo no se muestra aquí; ábrelo en Volta Go o en la app del operador.</p></div>"
            f'<p><a class="cta" href="/">Abrir en Volta Go</a><a class="cta" href="{_e(directions)}" rel="nofollow noopener">Cómo llegar</a></p>'
            f"<h2>Fuentes ({len(sources)})</h2><ul class=\"list\">{''.join(sources)}</ul>"
            f"<p class=\"muted\">Datos de {_date(index.generated_at)}.</p>")
    address = {"@type": "PostalAddress", "streetAddress": record.get("address") or "", "addressCountry": "ES"}
    if city:
        address["addressLocality"] = city
    json_ld = {"@context": "https://schema.org", "@type": "Place", "name": name, "address": address,
               "geo": {"@type": "GeoCoordinates", "latitude": record["lat"], "longitude": record["lon"]},
               "url": f"{SITE_URL}/cargador/{slug}"}
    where = f" en {city}" if city else ""
    return _page(f"{name}{where} · punto de recarga | Volta Go",
                 f"Punto de recarga {name}{where}: conectores, potencia, precio publicado y {len(sources)} fuentes públicas.",
                 f"/cargador/{slug}", body, json_ld)


def render_sitemap(index: SiteIndex) -> str:
    lastmod = _date(index.generated_at) if index.generated_at else None
    urls = [SITE_URL + "/", SITE_URL + "/cargadores"]
    urls += [f"{SITE_URL}/cargadores/{key}" for key in sorted(index.cities)]
    urls += [f"{SITE_URL}/cargador/{compact['slug']}" for compact in sorted(index.stations.values(), key=lambda c: c["slug"])]
    tag = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "".join(f"  <url><loc>{_e(url)}</loc>{tag}</url>\n" for url in urls) + "</urlset>\n")


def not_found_page() -> str:
    return _page("Página no encontrada | Volta Go", "Página no encontrada.", "/cargadores",
                 '<h1>Página no encontrada</h1><p>Puede que este punto de recarga ya no esté en las fuentes públicas. '
                 '<a href="/cargadores">Ver todas las ciudades</a>.</p>')


def route(index: SiteIndex, path: str) -> Tuple[int, str, str, Dict[str, str]]:
    """(status, content type, body, extra headers) for a crawlable path."""
    path = path.rstrip("/") or "/"
    html_type = "text/html; charset=utf-8"
    if path == "/sitemap.xml":
        return 200, "application/xml; charset=utf-8", render_sitemap(index), {}
    if path == "/cargadores":
        return 200, html_type, render_index(index), {}
    match = re.fullmatch(r"/cargadores/([a-z0-9-]+)", path)
    if match:
        page = render_city(index, match.group(1))
        return (200, html_type, page, {}) if page else (404, html_type, not_found_page(), {})
    # A station is found by the hash of its ID; a renamed station's old URL redirects.
    match = re.fullmatch(r"/cargador/(?:[a-z0-9-]*-)?([0-9a-f]{8})", path)
    compact = index.stations.get(match.group(1)) if match else None
    if compact is not None and path != f"/cargador/{compact['slug']}":
        return 301, html_type, "", {"Location": f"/cargador/{compact['slug']}"}
    page = render_station(index, match.group(1)) if compact is not None else None
    if page is None:
        return 404, html_type, not_found_page(), {}
    return 200, html_type, page, {}
