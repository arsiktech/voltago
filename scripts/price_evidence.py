"""Bounded Spain-only price annotations. Never station, price or live authority.

Snapshots are projected onto explicit allowlists before they reach disk. Collection
uses anonymous GETs through source_transport and never calls legacy map adapters.
"""
from __future__ import annotations

import json
import math
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

try:
    from .source_transport import public_price_urlopen
except ImportError:
    from source_transport import public_price_urlopen

REPSOL_URL = 'https://www.repsol.es/particulares/vehiculos/movilidad-electrica/recarga-fuera-de-casa/'
EM_BASE = 'https://www.electromaps.com/mapi/v2/locations'
CM_LIST = 'https://map.chargemap.com/mappy/charging_pools.json'
CM_PRICE = 'https://map.chargemap.com/pool-prices/v3/prices/'
KINDS = ('repsol_network_tariff', 'electromaps_community', 'chargemap_pass')
REGISTRY = 'DGT National Access Point charging registry'
LIST_TTL = 86400
DETAIL_TTL = 7 * 86400
LIST_BUDGET = 16
DETAIL_BUDGET = 24
TOTAL_BUDGET = 81
REFRESH_SECONDS = 180
PAUSE_SECONDS = 0.25
BODY_LIMIT = 3_000_000
EM_TYPES = 'ON_STREET,PARKING,AIRPORT,CAMPING,HOTEL,RESTAURANT,SHOP,WORKSHOP,FUEL_STATION,CAR_DEALER,MALL,TAXI'

# south, west, north, east; fixed region order, never a transposed NW/SE.
SPAIN_REGIONS = tuple(
    (round(35.85 + i * 2.7, 6), round(-9.45 + j * (13.9 / 3), 6),
     round(35.85 + (i + 1) * 2.7, 6), round(-9.45 + (j + 1) * (13.9 / 3), 6))
    for i in range(3) for j in range(3)
) + (
    (38.6, 1.0, 40.2, 3.0), (38.6, 3.0, 40.2, 4.5),
    (27.6, -18.2, 29.5, -16.5), (27.6, -16.5, 29.5, -14.9), (27.6, -14.9, 29.5, -13.3),
    (35.85, -5.4, 35.92, -5.28), (35.26, -2.98, 35.33, -2.9),
)

# Compact city pilot tiles avoid country-scale cluster-only map responses.
# Registry guide annotations use SPAIN_REGIONS, not this limited directory sample.
SPAIN_TILES = (
    (39.43, -0.42, 39.50, -0.34),  # Valencia
    (40.38, -3.75, 40.46, -3.66),  # Madrid
    (41.35, 2.12, 41.43, 2.21),  # Barcelona
    (37.35, -6.02, 37.42, -5.94),  # Seville
    (36.69, -4.46, 36.76, -4.38),  # Malaga
    (43.23, -2.97, 43.29, -2.89),  # Bilbao
    (41.62, -0.93, 41.69, -0.85),  # Zaragoza
    (38.31, -0.53, 38.38, -0.45),  # Alicante
    (41.62, -4.77, 41.69, -4.69),  # Valladolid
    (39.54, 2.61, 39.61, 2.70),  # Palma
    (38.88, 1.39, 38.94, 1.46),  # Ibiza
    (28.43, -16.29, 28.50, -16.21),  # Santa Cruz de Tenerife
    (28.09, -15.47, 28.16, -15.39),  # Las Palmas
    (28.93, -13.59, 29.00, -13.51),  # Arrecife
    (35.85, -5.40, 35.92, -5.28),  # Ceuta
    (35.26, -2.98, 35.33, -2.90),  # Melilla
)


def norm(value):
    text = unicodedata.normalize('NFKD', str(value or '').casefold())
    return re.sub(r'[^a-z0-9]+', ' ', ''.join(c for c in text if not unicodedata.combining(c))).strip()


def number(value):
    try:
        n = float(str(value).replace(',', '.'))
        return n if math.isfinite(n) and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def stamp(value):
    if not isinstance(value, str) or not re.match(r'^\d{4}-\d{2}-\d{2}(?:T|$)', value):
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt.replace(tzinfo=dt.tzinfo or timezone.utc)
    except ValueError:
        return None


def now_iso(now):
    return datetime.fromtimestamp(now, timezone.utc).isoformat().replace('+00:00', 'Z')


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        elif tag in ('p', 'div', 'br', 'li', 'tr', 'td', 'h1', 'h2', 'h3', 'h4'):
            self.parts.append(' ')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)
        else:
            self.parts.append(' ')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(document):
    parser = TextParser()
    parser.feed(str(document or ''))
    return re.sub(r'\s+', ' ', ''.join(parser.parts)).strip()


def evidence(kind, source, label, text, url, fetched_at, *, updated_at=None, price=None, details=None):
    if not stamp(fetched_at) or not text:
        return None
    return dict(kind=kind, source=source, label=label, text=text, source_url=url,
                source_updated_at=updated_at, fetched_at=fetched_at, price_per_kwh=price,
                currency='EUR', **({'tariff_details': details} if details else {}))


def parse_repsol_tariff(document, fetched_at):
    text = plain_text(document)
    # Require the contextual table and the caveat, not three unrelated numbers.
    patterns = (
        r'Recarga de coches normal:\s*0[,.]36\s*€/kWh',
        r'Recarga de coches r[aá]pida:\s*desde\s*0[,.]48\s*€/kWh',
        r'Recarga de coches ultrarr[aá]pida:\s*0[,.]56\s*€/kWh',
    )
    positions = [re.search(p, text, re.I) for p in patterns]
    caveat = re.search(r'Ten en cuenta que puede haber puntos de recarga tanto de Repsol como de otros operadores con tarifas distintas\.', text, re.I)
    if not all(positions) or not caveat:
        return None
    start, end = positions[0].start(), caveat.end()
    if not (start < positions[1].start() < positions[2].start() < caveat.start()) or end - start > 800:
        return None
    heading = re.search(r'Los precios de los puntos de recarga de Repsol son:', text[:start], re.I)
    if heading and start - heading.end() < 200:
        start = heading.start()
    return evidence(KINDS[0], 'Repsol', 'Repsol published tariff · station may differ',
                    text[start:end], REPSOL_URL, fetched_at,
                    details={'scope': 'network guide', 'speed_labels': ['normal', 'fast', 'ultrafast'],
                             'fast_price_is_from': True, 'power_bands_published': False})


def parse_electromaps_price(detail, fetched_at):
    updated = detail.get('updated_at')
    source_date, fetch_date = stamp(updated), stamp(fetched_at)
    if not source_date or not fetch_date or source_date.year < 2000 or source_date > fetch_date:
        return None
    text = str(detail.get('charge_price') or '').strip()
    # A paid/free marker, an empty field or an app instruction is not tariff evidence.
    if not re.search(r'(?:\d[\d,.]*\s*(?:€|EUR)|(?:€|EUR)\s*\d|gratis|gratuit[oa]|free)', text, re.I):
        return None
    prices = re.findall(r'(\d+(?:[,.]\d+)?)\s*(?:€|EUR)\s*/\s*kWh\b', text, re.I)
    price = number(prices[0]) if len(prices) == 1 else None
    parking_price = str(detail.get('parking_price') or '').strip()
    details = {'parking_price': parking_price, 'raw_tariff_text': text}
    claim = evidence(KINDS[1], 'Electromaps', 'Electromaps community price · ' + source_date.date().isoformat(),
                     text, EM_BASE + '/' + str(detail['id']), fetched_at,
                     updated_at=updated, price=price, details=details)
    if parking_price:
        claim['parking_price'] = parking_price
    return claim


def parse_chargemap_price(document, fetched_at, pool_id):
    text = plain_text(document)
    # Keep all billing/fee prose. Require an explicit currency + billing unit.
    if not re.search(r'\d+(?:[,.]\d+)?\s*€\s*(?:por|/)\s*(?:kWh|minuto|hora|sesi[oó]n)', text, re.I):
        return None
    energies = re.findall(r'(\d+(?:[,.]\d+)?)\s*€\s*por\s*kWh\b', text, re.I)
    started = bool(re.search(r'kWh\s+comenzado', text, re.I))
    idle = re.search(r'(?:Penalidad|penalizaci[oó]n|ocupaci[oó]n|inactividad)\b.*', text, re.I)
    idle_text = idle[0] if idle else ''
    fees = re.findall(r'(\d+(?:[,.]\d+)?)\s*€\s*por minuto', idle_text, re.I)
    grace = re.search(r'a partir de\s+(\d+)\s+minutos sin carga', idle_text, re.I)
    details = {'raw_tariff_text': text, 'payment_route': 'Chargemap Pass',
               'energy_billing_unit': 'started_kWh' if started else 'kWh' if energies else None}
    if len(fees) == 1 and re.search(r'(?:recarga terminada|sin carga)', idle_text, re.I):
        details['idle_fee_per_minute'] = number(fees[0])
        details['idle_trigger'] = 'connected after charging ends'
        if grace:
            details['idle_grace_minutes'] = int(grace[1])
    return evidence(KINDS[2], 'Chargemap', 'Price with Chargemap Pass', text,
                    CM_PRICE + str(pool_id) + '?locale=es-es&noBootstrap=true&noFooter=true', fetched_at,
                    price=number(energies[0]) if len(energies) == 1 else None, details=details)


def _dict(value):
    return value if isinstance(value, dict) else {}


def _safe_id(value):
    return str(value) if re.fullmatch(r'\d{1,12}', str(value)) else None


def _text(value, limit):
    return value[:limit] if isinstance(value, str) else ''


def _evse_ids(values):
    return sorted({str(v).upper().replace('*', '') for v in (values or [])
                   if isinstance(v, str) and re.fullmatch(r'[A-Za-z]{2}\*?[A-Za-z0-9]{3}\*?E[A-Za-z0-9*]+', v)})


def sanitize_electromaps(item):
    item = _dict(item)
    address = _dict(item.get('address'))
    cpo = _dict(item.get('cpo'))
    return dict(id=_safe_id(item.get('id')), name=_text(item.get('name'), 300),
                lat=number(item.get('latitude')), lon=number(item.get('longitude')),
                address=_text(address.get('address'), 500), country_code=_text(address.get('country_code'), 3),
                network=_text(cpo.get('name') or _dict(item.get('network')).get('name') or item.get('network'), 200),
                charge_price=_text(item.get('charge_price'), 4000),
                parking_price=_text(item.get('parking_price'), 2000), updated_at=_text(item.get('updated_at'), 40) or None,
                evse_ids=_evse_ids(item.get('evse_emi3_ids')),
                connectors=[dict(id=_safe_id(c.get('id')), type=_text(c.get('type'), 80),
                                 power_kw=number(c.get('kw'))) for c in item.get('connectors') or [] if isinstance(c, dict)])


def sanitize_chargemap(pool):
    pool = _dict(pool)
    coords = _dict(pool.get('gps_coordinates'))
    street = _text(pool.get('street_name'), 450)
    street_number = _text(pool.get('number'), 40)
    address = ' '.join(value for value in (street, street_number) if value) or _text(pool.get('address'), 500)
    return dict(id=_safe_id(pool.get('id')), name=_text(pool.get('name'), 300),
                lat=number(coords.get('lat')), lon=number(coords.get('lon')),
                address=address,
                country_code=_text(pool.get('country_code'), 3), network=_text(_dict(pool.get('network')).get('name'), 200),
                evse_ids=_evse_ids(pool.get('evse_emi3_ids')),
                connectors=[dict(type=_text(c.get('type'), 80), power_kw=number(c.get('power_max')))
                            for c in pool.get('charging_connectors') or [] if isinstance(c, dict)])


def list_rows(payload, source):
    if source == 'electromaps':
        values = payload if isinstance(payload, list) else _dict(payload).get('data', [])
        projected = [sanitize_electromaps(v) for v in values if isinstance(v, dict)] if isinstance(values, list) else []
        return [row for row in projected if in_spain(row)]
    values = _dict(_dict(_dict(payload).get('response')).get('content')).get('items') or []
    # Clusters have no pool: bounded pilot never recurses into extra requests.
    projected = [sanitize_chargemap(v['pool']) for v in values if isinstance(v, dict) and isinstance(v.get('pool'), dict)]
    return [row for row in projected if in_spain(row)]


def in_spain(row):
    country = str(row.get('country_code') or row.get('country') or '').upper()
    if country and country not in ('ES', 'ESP'):
        return False
    lat, lon = number(row.get('lat')), number(row.get('lon'))
    if lat is None or lon is None:
        return False
    if not any(s <= lat <= n and w <= lon <= e for s, w, n, e in SPAIN_REGIONS):
        return False
    # A broad tile/country label cannot donate a Spanish claim across a border.
    try:
        from .spain_geography import point_in_spain
    except ImportError:
        from spain_geography import point_in_spain
    return point_in_spain(lat, lon)


def distance(a, b):
    try:
        p, q = math.radians(float(a['lat'])), math.radians(float(b['lat']))
        d = math.radians(float(a['lon']) - float(b['lon']))
        h = math.sin((p-q)/2)**2 + math.cos(p)*math.cos(q)*math.sin(d/2)**2
        return 12742000 * math.asin(min(1, math.sqrt(h)))
    except (TypeError, ValueError, KeyError):
        return math.inf


def network(value):
    value = norm(value)
    aliases = {
        'repsol': ('repsol', 'repsol soluciones energeticas sa'),
        'iberdrola': ('iberdrola', 'iberdrola clientes sau', 'iberdrola clientes sa'),
        'iberdrola bp pulse': ('iberdrola bp pulse', 'charging together'),
        'endesa x way': ('endesa', 'endesa x way', 'endesa x way sl', 'endesa movilidad electrica slu'),
    }
    for key, names in aliases.items():
        if value.replace(' ', '') in {norm(name).replace(' ', '') for name in names}:
            return key
    return value


def row_network(row):
    raw = _dict(row.get('raw'))
    for value in (raw.get('operator'), row.get('operator'), _dict(row.get('owner')).get('name'), row.get('network')):
        if value and not isinstance(value, dict):
            return network(value)
    return ''


def directory_network(row):
    if row.get('network'):
        return network(row['network'])
    # Electromaps often omits cpo. Explicit brand in the station name can only
    # corroborate a nearby address + hardware match, never prove membership alone.
    name = norm(row.get('name'))
    for label in ('iberdrola bp pulse', 'endesa x way', 'iberdrola', 'repsol', 'endesa'):
        if name == label or name.startswith(label + ' '):
            return network(label)
    return ''


def connector_type(value):
    token = re.sub(r'[^a-z0-9]', '', str(value or '').lower())
    if token in ('iec62196t2combo', 'combotype2', 'ccs2', 'ccstype2'):
        return 'CCS2'
    if token in ('iec62196t2', 'type2', 'type2socketonly', 'type2tetheredconnector', 'mennekestype2'):
        return 'Type 2'
    if token in ('chademo',):
        return 'CHAdeMO'
    return ''


def compatible_connectors(a, b):
    def hardware(row):
        return [(connector_type(c.get('type') or c.get('label') or c.get('standard')),
                 number(c.get('power_kw') or c.get('kw') or c.get('power_max')))
                for c in row.get('connectors') or [] if isinstance(c, dict)]
    return any(ta and ta == tb and (pa is None or pb is None or abs(pa-pb) <= max(1, max(pa, pb)*0.1))
               for ta, pa in hardware(a) for tb, pb in hardware(b))


def stable_keys(row, source):
    raw = _dict(row.get('raw'))
    keys = {('evse', x) for x in _evse_ids(raw.get('evse_ids') or row.get('evse_ids'))}
    value = raw.get(source + '_id')
    if value:
        keys.add((source, str(value)))
    for k in row.get('merged_source_identifiers') or []:
        if len(k) >= 2 and source in str(k[0]).lower():
            keys.add((source, str(k[-1])))
    return keys


def address_agrees(a, b):
    ta, tb = set(norm(a.get('address')).split()), set(norm(b.get('address')).split())
    generic = {'calle', 'carrer', 'avenida', 'avda', 'avinguda', 'carretera', 'de', 'del', 'la', 'el', 'españa', 'espana'}
    generic |= set(norm(a.get('city')).split()) | set(norm(b.get('city')).split())
    shared = ta & tb
    # A shared postcode is not a shared house number or street.
    return bool(any(t.isdigit() and len(t) <= 4 for t in shared)
                and any(len(t) >= 4 and t not in generic and not t.isdigit() for t in shared))


def unique_match(candidate, rows, source):
    if not in_spain(candidate):
        return None
    matches, stable_matches = [], []
    candidate_keys = {('evse', x) for x in candidate.get('evse_ids') or []} | {(source, str(candidate['id']))}
    for row in rows:
        if not in_spain(row) or not compatible_connectors(row, candidate):
            continue
        dist = distance(row, candidate)
        operator = row_network(row)
        candidate_operator = directory_network(candidate)
        if operator and candidate_operator and operator != candidate_operator:
            continue
        keys = stable_keys(row, source)
        # A known different provider ID or EVSE cannot be repaired by proximity.
        for family in (source, 'evse'):
            left, right = {k for k in candidate_keys if k[0] == family}, {k for k in keys if k[0] == family}
            if left and right and not left & right:
                break
        else:
            stable = bool(candidate_keys & keys)
            if stable and dist <= 250:
                stable_matches.append(row)
            elif dist <= 40 and operator and operator == candidate_operator:
                addresses_present = bool(row.get('address') and candidate.get('address'))
                if addresses_present and not address_agrees(row, candidate):
                    continue
                # Name-only network needs an independently agreeing street address.
                if candidate.get('network') or address_agrees(row, candidate):
                    matches.append(row)
    if stable_matches:
        return stable_matches[0] if len(stable_matches) == 1 else None
    return matches[0] if len(matches) == 1 else None


def price_evidence_counts(rows):
    counts = dict(stations=0, total=0, **dict.fromkeys(KINDS, 0))
    for row in rows:
        claims = [c for c in row.get('price_evidence') or [] if isinstance(c, dict) and c.get('kind') in KINDS]
        counts['stations'] += bool(claims)
        for claim in claims:
            counts[claim['kind']] += 1
            counts['total'] += 1
    return counts


class Collector:
    def __init__(self, cache_dir, *, now=None, opener=None, pause=PAUSE_SECONDS, list_budget=LIST_BUDGET, detail_budget=DETAIL_BUDGET):
        self.cache_dir = Path(cache_dir) / 'price_evidence_v1'
        self.now = time.time() if now is None else now
        self.opener = opener or public_price_urlopen
        self.pause = pause
        self.list_budget = min(LIST_BUDGET, max(0, list_budget))
        self.detail_budget = min(DETAIL_BUDGET, max(0, detail_budget))
        self.deadline = time.monotonic() + REFRESH_SECONDS
        self.requests = 0
        self.stats = {s: defaultdict(int) for s in ('repsol', 'electromaps', 'chargemap')}

    def request(self, url, source, phase):
        stat = self.stats[source]
        bound = 1 if source == 'repsol' else self.list_budget if phase == 'list' else self.detail_budget
        if stat[phase + '_requests'] >= bound or self.requests >= TOTAL_BUDGET or time.monotonic() >= self.deadline:
            stat['budget_withheld'] += 1
            raise RuntimeError('price evidence request budget exhausted')
        if self.requests and self.pause:
            time.sleep(min(self.pause, max(0, self.deadline-time.monotonic())))
        stat[phase + '_requests'] += 1
        self.requests += 1
        req = urllib.request.Request(url, headers={'User-Agent': 'VoltaGo public price evidence', 'Accept': 'application/json,text/html'}, method='GET')
        with self.opener(req, timeout=min(12, max(0.1, self.deadline-time.monotonic()))) as response:
            body = response.read(BODY_LIMIT + 1)
            if len(body) > BODY_LIMIT:
                raise ValueError('price evidence response exceeds size bound')
            return body.decode('utf-8')

    def cached(self, key, url, source, phase, project):
        path = self.cache_dir / (key + '.json')
        old = None
        try:
            old = json.loads(path.read_text(encoding='utf-8'))
            if old.get('schema') != 1 or old.get('url') != url or not stamp(old.get('fetched_at')) or 'data' not in old:
                old = None
        except (OSError, ValueError, TypeError):
            pass
        ttl = LIST_TTL if phase == 'list' else DETAIL_TTL
        if old and 0 <= self.now - stamp(old['fetched_at']).timestamp() < ttl:
            self.stats[source][phase + '_cache_hits'] += 1
            return old['data'], old['fetched_at'], False
        try:
            data = project(self.request(url, source, phase))
            fetched_at = now_iso(self.now)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix('.tmp')
            tmp.write_text(json.dumps(dict(schema=1, url=url, fetched_at=fetched_at, data=data), ensure_ascii=False), encoding='utf-8')
            tmp.replace(path)
            return data, fetched_at, False
        except Exception:
            self.stats[source]['errors'] += 1
            if old:
                self.stats[source]['stale_fallbacks'] += 1
                return old['data'], old['fetched_at'], True
            return None, None, False

    def enrich(self, rows, *, registry_rows=None):
        # Owned annotations must be rechecked or restored from dated sanitized
        # cache. In particular, a changed Repsol page cannot leave an old guide.
        for row in rows:
            if isinstance(row.get('price_evidence'), list):
                kept = [c for c in row['price_evidence'] if not isinstance(c, dict) or c.get('kind') not in KINDS]
                if kept:
                    row['price_evidence'] = kept
                else:
                    row.pop('price_evidence', None)
        # Snapshot the NAP membership before merges erase a donor raw payload.
        # Only original/merged typed IDs can carry that fact onto a surviving pin.
        registry_ids = set()
        for row in registry_rows if registry_rows is not None else rows:
            raw = _dict(row.get('raw'))
            if row.get('source') == REGISTRY and raw.get('nap_site_id') and norm(raw.get('operator')).replace(' ', '') == 'repsolsolucionesenergeticassa' and in_spain(row):
                registry_ids.add(str(row['id']))
        try:
            guide = parse_repsol_tariff(self.request(REPSOL_URL, 'repsol', 'detail'), now_iso(self.now))
        except Exception:
            self.stats['repsol']['errors'] += 1
            guide = None
        if guide:
            for row in rows:
                ids = {str(row.get('id'))} | {str(v) for v in row.get('merged_ids') or []}
                if ids & registry_ids and row.get('price_min') is None and in_spain(row):
                    self.attach(row, guide, 'repsol', False)
        for source in ('electromaps', 'chargemap'):
            try:
                self.collect_directory(rows, source)
            except Exception:
                # An upstream shape/cache failure withholds annotations, never rows.
                self.stats[source]['errors'] += 1
        return dict(scope='Spain; bounded existing-station annotations only',
                    counts=price_evidence_counts(rows), requests=self.requests,
                    sources={s: dict(v) for s, v in self.stats.items()},
                    budgets=dict(list_per_source=self.list_budget, detail_per_source=self.detail_budget,
                                 total_requests=TOTAL_BUDGET, request_start_deadline_seconds=REFRESH_SECONDS,
                                 detail_ttl_seconds=DETAIL_TTL, list_ttl_seconds=LIST_TTL, pause_seconds=self.pause))

    def attach(self, row, claim, source, stale):
        if not claim:
            return
        claim = dict(claim)
        if stale:
            claim['cache_stale'] = True
        claims = row.setdefault('price_evidence', [])
        # Refresh this source annotation without changing other evidence or fields.
        claims[:] = [c for c in claims if not (isinstance(c, dict) and c.get('kind') == claim['kind'] and c.get('source_url') == claim['source_url'])]
        claims.append(claim)
        self.stats[source]['attached'] += 1

    def collect_directory(self, rows, source):
        grid = defaultdict(list)
        for row in rows:
            if in_spain(row):
                grid[(math.floor(row['lat'] * 200), math.floor(row['lon'] * 200))].append(row)

        def nearby(candidate):
            gx, gy = math.floor(candidate['lat'] * 200), math.floor(candidate['lon'] * 200)
            return [row for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                    for row in grid.get((gx+dx, gy+dy), []) if distance(row, candidate) <= 250]

        candidates = {}
        collisions = set()
        for tile_index, (s, w, n, e) in enumerate(SPAIN_TILES):
            if source == 'electromaps':
                query = urllib.parse.urlencode(dict(latNE=n, lngNE=e, latSW=s, lngSW=w, realtime='false', connectors='', types=EM_TYPES, power=3, app='false', rfid='false', favorites='false', skipAuthTokenValidation='true'))
                url = EM_BASE + '?' + query
            else:
                url = CM_LIST + '?' + urllib.parse.urlencode(dict(NW=f'{n};{w}', SE=f'{s};{e}'))
            values, _, list_stale = self.cached(source + '_tile_' + str(tile_index), url, source, 'list', lambda body: list_rows(json.loads(body), source))
            for candidate in values or []:
                key = candidate.get('id')
                if not key or not (s <= (candidate.get('lat') or -90) <= n and w <= (candidate.get('lon') or -180) <= e):
                    continue
                if key in candidates and distance(candidates[key][0], candidate) > 1:
                    collisions.add(key)
                candidates[key] = candidate, list_stale
        self.stats[source]['listed'] = len(candidates)
        # Spend the small detail budget on corroboratable network candidates
        # before anonymous nearby names; actual identity still needs detail checks.
        ranked = []
        for key, (candidate, stale) in candidates.items():
            if key in collisions:
                self.stats[source]['ambiguous_ids'] += 1
                continue
            near = nearby(candidate)
            if not near:
                continue
            candidate_network = directory_network(candidate)
            network_hint = bool(candidate_network and any(
                row_network(row) == candidate_network and distance(row, candidate) <= 40 for row in near))
            ranked.append((not network_hint, all(r.get('price_min') is not None for r in near), key, candidate, stale))
        for _, _, key, candidate, list_stale in sorted(ranked, key=lambda item: item[:3]):
            if source == 'electromaps':
                url = EM_BASE + '/' + key
                detail, fetched_at, stale = self.cached(source + '_detail_' + key, url, source, 'detail', lambda body: sanitize_electromaps(json.loads(body)))
                if not detail or detail.get('id') != key or distance(candidate, detail) > 40:
                    self.stats[source]['withheld'] += 1
                    continue
                row = unique_match(detail, nearby(detail), source)
                claim = parse_electromaps_price(detail, fetched_at)
            else:
                row = unique_match(candidate, nearby(candidate), source)
                if row is None:
                    self.stats[source]['withheld'] += 1
                    continue
                url = CM_PRICE + key + '?locale=es-es&noBootstrap=true&noFooter=true'
                claim, fetched_at, stale = self.cached(source + '_detail_' + key, url, source, 'detail', lambda body: parse_chargemap_price(body, now_iso(self.now), key))
            if row is None or not claim:
                self.stats[source]['withheld'] += 1
                continue
            self.stats[source]['matched'] += 1
            self.attach(row, claim, source, stale or list_stale)


def enrich_price_evidence(rows, cache_dir, *, registry_rows=None, **kwargs):
    return Collector(cache_dir, **kwargs).enrich(rows, registry_rows=registry_rows)
