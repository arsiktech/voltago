"""Cached Telpark Spain directory intake, never charging prices or live status."""
from __future__ import annotations

import hashlib
import json
import math
import urllib.request
from datetime import datetime
from typing import Any, Callable

try:
    from . import source_public_station_pages as pages
except ImportError:
    import source_public_station_pages as pages
from station_truth import telpark_parking_detail_url

SOURCE = "Telpark public parking pages"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Telpark acquisition must not redirect")


def bounded_fetch(url: str, *, headers=None, timeout=pages.DEFAULT_TIMEOUT) -> bytes:
    """Anonymous, bounded GET. Neither redirects nor ambient credentials are allowed."""
    allowed_lists = {pages.TELPARK_REST_URL.format(per_page=100, page=n) for n in range(1, pages.MAX_TELPARK_PAGES + 1)}
    if url not in allowed_lists and not telpark_parking_detail_url(url):
        raise ValueError("Unapproved Telpark acquisition URL")
    headers = headers or {}
    if any(name.lower() not in {'accept', 'user-agent'} for name in headers):
        raise ValueError("Telpark acquisition accepts no credential headers")
    timeout = float(timeout)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Telpark timeout must be positive and finite")
    request = urllib.request.Request(url, headers={"User-Agent": pages.USER_AGENT, **headers}, method="GET")
    with urllib.request.build_opener(_NoRedirect()).open(request, timeout=min(timeout, pages.DEFAULT_TIMEOUT)) as response:
        body = response.read(pages.MAX_BODY_BYTES + 1)
    if len(body) > pages.MAX_BODY_BYTES:
        raise ValueError("Telpark response exceeds body budget")
    return body


def _detail(body: bytes) -> dict:
    # The shared parser ignores Organization offers. Also require the feature and
    # coordinates to belong to the parking node, not another JSON-LD entity.
    for node in pages._iter_jsonld_nodes(body.decode("utf-8", "replace")):
        types = node.get("@type")
        if types == "ParkingFacility" or isinstance(types, list) and "ParkingFacility" in types:
            return pages.parse_telpark_detail('<script type="application/ld+json">' + json.dumps(node) + '</script>')
    return {}


def _spain(detail: dict) -> bool:
    country = detail.get("country_raw")
    if isinstance(country, dict):
        country = country.get("name") or country.get("@id")
    return pages._fold(country) in {"es", "espana", "spain", "aparcar en espana"} and not str(detail.get("telephone") or "").replace(" ", "").startswith("+351")


def _coords(detail: dict) -> bool:
    lat, lon = detail.get("lat"), detail.get("lon")
    return all(type(v) in (int, float) and math.isfinite(v) for v in (lat, lon)) and -90 <= lat <= 90 and -180 <= lon <= 180 and (lat, lon) != (0, 0)


def _artifact_time(artifact):
    acquired = datetime.fromisoformat(artifact.fetched_at.replace('Z', '+00:00'))
    if acquired.tzinfo is None:
        raise ValueError('Telpark artifact acquisition time must include timezone')
    return acquired


def collect_telpark(cached_fetch: Callable, add_record: Callable, *, max_pages=pages.MAX_TELPARK_PAGES,
                    max_detail_fetches=pages.MAX_TELPARK_DETAIL_FETCHES) -> tuple[list[dict], dict]:
    """Use the upstream walk, with per-artifact ages and explicit partial coverage.

    Remaining details can be retained from cache after the network budget is used.
    Any upstream failure (including a cached fallback) prevents publication of rows.
    The caller receives diagnostics even on a failed collection.
    """
    max_pages = max(1, min(int(max_pages), pages.MAX_TELPARK_PAGES))
    max_detail_fetches = max(0, min(int(max_detail_fetches), pages.MAX_TELPARK_DETAIL_FETCHES))
    candidates: dict[str, tuple[dict, Any]] = {}
    details: dict[str, tuple[dict, Any]] = {}
    list_complete = False
    acquisition_errors = []
    attempted = set()

    def acquire(url, *, headers=None, timeout=pages.DEFAULT_TIMEOUT, cache_only=False):
        nonlocal list_complete
        is_list = url in {pages.TELPARK_REST_URL.format(per_page=100, page=n) for n in range(1, max_pages + 1)}
        if not is_list and not telpark_parking_detail_url(url):
            raise ValueError("Unapproved Telpark detail URL")
        if is_list:
            page = url.rsplit("=", 1)[1]
            cache_name = f"telpark_list_{page}.json"
        else:
            cache_name = "telpark_detail_" + hashlib.sha256(url.encode()).hexdigest() + ".html"
            attempted.add(url)
        artifact = cached_fetch(url, cache_name, ttl_hours=24, headers=headers or {}, timeout=timeout,
                                fetcher=bounded_fetch, cache_only=cache_only)
        if artifact.url != url or not artifact.fetched_at:
            raise ValueError("Telpark cache provenance mismatch")
        _artifact_time(artifact)
        if len(artifact.body) > pages.MAX_BODY_BYTES:
            raise ValueError("Telpark cached response exceeds body budget")
        if artifact.error or artifact.fallback:
            acquisition_errors.append(f"Cached fallback for {cache_name}: {artifact.error or 'upstream failure'}")
        if is_list:
            parsed = pages.parse_telpark_parkings_page(artifact.body)
            list_complete = len(parsed) < 100
            for row in parsed:
                candidates.setdefault(row['source_id'], (row, artifact))
        else:
            details[url] = (_detail(artifact.body), artifact)
        return artifact.body

    result = pages.fetch_telpark_rows(acquire, per_page=100, max_pages=max_pages,
                                     max_detail_fetches=max_detail_fetches)
    errors = list(result['errors']) + acquisition_errors
    retained = 0
    for row, _ in candidates.values():
        url = row.get('url')
        if row.get('country') != 'ES' or url in attempted or not telpark_parking_detail_url(url):
            continue
        try:
            acquire(url, cache_only=True)
            retained += 1
        except FileNotFoundError:
            pass  # Not a failed upstream request: no network was attempted.
        except Exception as exc:
            errors.append(f"Retained detail: {type(exc).__name__}: {str(exc)[:100]}")
    errors = list(dict.fromkeys(errors + acquisition_errors))
    records = []
    rejected = 0
    unconfirmed = 0
    for row, list_artifact in candidates.values():
        if row.get('country') != 'ES':
            continue
        url = row.get('url')
        detail, detail_artifact = details.get(url, ({}, None))
        if not detail or detail.get('ev_charging') is None:
            unconfirmed += 1
            continue
        if detail.get('ev_charging') is not True:
            continue
        stable_id = row.get('source_id', '').removeprefix('telpark:')
        if not stable_id.isascii() or not stable_id.isdigit() or int(stable_id) <= 0 or stable_id != str(int(stable_id)) or not _spain(detail) or not _coords(detail):
            rejected += 1
            continue
        raw = {k: v for k, v in row.items() if k not in {'retrieved_at', 'lat', 'lon'}}
        raw.update(telpark_parking_id=stable_id, telpark_detail_url=url, country='ES', ev_charging=True,
                   ev_flag_source='detail_jsonld_amenityFeature', coordinate_source='telpark_detail_jsonld_geo',
                   preserve_location_claim=True,
                   source_fetch_artifacts=[list_artifact.as_evidence(), detail_artifact.as_evidence()])
        oldest = min((list_artifact, detail_artifact), key=_artifact_time)
        parking_text = row.get('parking_price_text')
        note = 'Parking cost and access must be checked on the Telpark page and onsite.'
        if parking_text:
            note = f'Parking source says: {parking_text}. {note}'
        before = len(records)
        add_record(records, source=SOURCE, source_url=url, name=row.get('name') or detail.get('name') or 'Telpark parking',
                   lat=detail['lat'], lon=detail['lon'], address=detail.get('address') or row.get('address') or '',
                   city=row.get('city_slug', '').replace('-', ' ').title(), price_min=None,
                   connector_labels=[], connectors=[], max_power_kw=None, status='VERIFY', reliability='directory-page',
                   evidence='Telpark parking page advertises EV charging. Exact bays, plugs, power, charging tariff and availability are unknown.',
                   parking_kind='unknown', parking_note=note, raw=raw, source_artifact=oldest)
        if len(records) > before:
            # Omit unavailable evidence; runtime supplies its existing unknown placeholder.
            records[-1].pop('available', None)
            records[-1]['location_claim'].update(precision='official_parking_coordinate',
                                                summary='Telpark publishes this parking coordinate, not an exact charging bay. Verify bay position onsite.')
        else:
            rejected += 1
    complete = list_complete and unconfirmed == 0 and rejected == 0 and not errors and not result['unknown_country']
    status = {**result['stats'], 'ev_confirmed': len(records), 'ev_unconfirmed': unconfirmed,
              'evidence_rejected': rejected, 'cached_details_retained': retained, 'list_complete': list_complete,
              'coverage_complete': complete, 'errors': errors, 'ready': bool(records) and not errors,
              'coverage': 'complete' if complete else 'incomplete'}
    # Do not let a partly successful run remove a previous source from runtime.
    return (records if status['ready'] else []), status
