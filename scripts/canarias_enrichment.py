"""Bounded public-page collection and identity-preserving Canarias enrichment.

This module never groups stations or changes canonical identities. The scoped
overlay is deliberately separate from a national rebuild, so another adapter's
failure cannot delete existing charger records or user references.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .canarias_details import parse_canarias_detail, safe_detail_url
except ImportError:
    from canarias_details import parse_canarias_detail, safe_detail_url

SOURCE = "Canarias te recarga public map"
MAX_BYTES = 3_000_000
MAX_PAGES = 350


class _SourceRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = safe_detail_url(newurl, req.full_url)
        if not url:
            raise ValueError("Charger page redirected outside the public source")
        return super().redirect_request(req, fp, code, msg, headers, url)


def fetch_detail(url: str, cache: Path, engine, *, timeout: float = 12,
                 deadline: float | None = None):
    """GET one same-origin page; cap size/time and retain original cache dates."""
    url = safe_detail_url(url)
    if not url:
        raise ValueError("Not a public Canarias charger URL")
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()
    body_path, meta_path = cache / (key + ".html"), cache / (key + ".json")
    body = None
    meta = {}
    try:
        if body_path.stat().st_size <= MAX_BYTES:
            body = body_path.read_bytes()
            meta = json.loads(meta_path.read_text())
            if (meta["url"] != url or not safe_detail_url(meta["final_url"])
                    or meta["body_sha256"] != hashlib.sha256(body).hexdigest()):
                body = None
            fetched = datetime.fromisoformat(meta["fetched_at"].replace("Z", "+00:00"))
            age = max(0, time.time() - fetched.timestamp())
    except (OSError, KeyError, ValueError, TypeError):
        body = None

    def artifact(data, evidence, *, hit, fallback=False, error=None):
        fetched = datetime.fromisoformat(evidence["fetched_at"].replace("Z", "+00:00"))
        age = max(0, time.time() - fetched.timestamp())
        return engine.FetchArtifact(data, url, "canarias_details/" + body_path.name,
                                    evidence["fetched_at"], age, hit, fallback,
                                    age > 86400, error)

    if body is not None and age <= 86400:
        return artifact(body, meta, hit=True)
    try:
        remaining = (deadline - time.monotonic()) if deadline is not None else timeout
        if remaining <= 0:
            raise TimeoutError("Canarias detail collection budget exhausted")
        request = urllib.request.Request(url, headers={
            "User-Agent": "VoltaGo/1.2 (public charger directory; bounded GET)",
            "Accept": "text/html", "Accept-Encoding": "identity",
        }, method="GET")
        started = time.monotonic()
        opener = urllib.request.build_opener(_SourceRedirect())
        with opener.open(request, timeout=min(timeout, remaining)) as response:
            final_url = safe_detail_url(response.geturl())
            if not final_url:
                raise ValueError("Invalid final charger page URL")
            if int(response.headers.get("Content-Length") or 0) > MAX_BYTES:
                raise ValueError("Charger page exceeds size limit")
            chunks, size = [], 0
            while True:
                if time.monotonic() - started >= timeout:
                    raise TimeoutError("Charger page exceeded time limit")
                chunk = response.read(min(65536, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("Charger page exceeds size limit")
            data = b"".join(chunks)
        fresh_meta = {"url": url, "final_url": final_url,
                "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "body_sha256": hashlib.sha256(data).hexdigest()}
        body_path.with_suffix(".html.tmp").write_bytes(data)
        body_path.with_suffix(".html.tmp").replace(body_path)
        meta_path.with_suffix(".json.tmp").write_text(json.dumps(fresh_meta))
        meta_path.with_suffix(".json.tmp").replace(meta_path)
        return artifact(data, fresh_meta, hit=False)
    except Exception as exc:
        if body is None:
            raise
        return artifact(body, meta, hit=True, fallback=True, error=type(exc).__name__)


def _set_price(record, price, summary, engine):
    record.update(price_min=price, is_free=price == 0,
                  is_cheap=price is not None and price <= .25,
                  price_claim_type=engine.price_claim_type_for(price), free_condition=summary)
    record["charge_claim"] = {
        "source": SOURCE, "url": record["source_url"], "price_min": price,
        "type": "free" if price == 0 else "cheap" if price is not None and price <= .25
        else "paid" if price is not None else "price_unknown", "summary": summary,
    }


def apply_detail(record: dict, detail: dict, artifact, engine) -> None:
    """Apply an already identity-checked page to its source marker, never live."""
    url = detail.get("canonical_url") or detail["source_url"]
    record["raw"]["detail"] = detail
    record["raw"]["detail_fetch"] = artifact.as_evidence()
    record["source_fetched_at"] = engine.oldest_contributing_source_timestamp([
        record, {"source_fetched_at": artifact.fetched_at}])
    tariff = detail.get("tariff") or {}
    if tariff.get("text") and not artifact.stale:
        components = [c for c in tariff.get("components", [])
                      if c.get("type") in {"ENERGY", "TIME", "MINIMUM", "FLAT"}]
        if tariff.get("unconditional_free") and not any(c.get("type") == "ENERGY" for c in components):
            components.append({"type": "ENERGY", "price": 0., "unit": "kWh"})
        record["tariff_details"] = {
            **{k: tariff.get(k) for k in ("text", "conditions", "energy_eur_per_kwh",
                "hour_eur", "minimum_eur", "unconditional_free")},
            "source_url": url, "components": components,
        }
        energy = tariff.get("energy_eur_per_kwh")
        # A first free hour or a mandatory fee cannot grant the Free filter.
        price = (0. if tariff.get("unconditional_free") else energy
                 if energy is not None and energy > 0 else None)
        _set_price(record, price, tariff["text"], engine)
    connectors = detail.get("connectors") or []
    if connectors:
        record.update(connectors=connectors, total_evse=detail.get("port_count"),
                      connector_count=detail.get("port_count"), max_power_kw=detail.get("max_power_kw"))
        record["connector_labels"] = sorted({c["type"] for c in connectors if c.get("type") != "Unknown"})
        record["tesla_ok"] = engine.tesla_compat(record["connector_labels"])
        record["fast"] = bool(record.get("max_power_kw") and record["max_power_kw"] >= 43)
    record["media"] = engine.photo_refs(SOURCE, url, detail.get("photos") or [])
    contact = detail.get("contact") or {}
    # The site directory's phone is not the charger operator's support number.
    source_details = {"source_url": url, "description": detail.get("description") or "",
                      "opening_hours": detail.get("hours") or "", "access": detail.get("access") or "",
                      "notes": list(detail.get("warnings") or [])}
    if connectors:
        # Keep this inventory separately when an official/other-source inventory
        # remains the station's selected hardware. Neither source is silently lost.
        source_details.update(source_name="Canarias te recarga", connectors=copy.deepcopy(connectors),
                              connector_count=detail.get("port_count"), max_power_kw=detail.get("max_power_kw"))
    if artifact.stale:
        source_details["notes"].append("Cached station-page information fetched " + artifact.fetched_at
                                        + ". The current tariff needs confirmation.")
    if contact.get("scope") != "directory_contact":
        source_details["contact"] = {"name": contact.get("name") or "",
            "phone": ", ".join(contact.get("phones") or []),
            "email": ", ".join(contact.get("emails") or [])}
    record["source_details"] = source_details
    restriction_text = " ".join(str(detail.get(k) or "") for k in ("access", "description"))
    if re.search(r"\b(?:private|staff|customers?\s+only|members?\s+only|privad[oa]s?|solo\s+(?:clientes|residentes)|uso\s+exclusivo|exclusiv[oa]\s+para\s+clientes)\b", restriction_text, re.I):
        record["access"] = {"level": "restricted", "label": "Access caveat", "note": restriction_text[:500]}
        record["access_claim"] = engine.access_claim_for(record)
        if record.get("price_min") == 0:
            record.update(is_free=False, price_claim_type="access_limited_free")
    record["hardware_claim"] = engine.hardware_claim_for(record)
    # hardware_claim_for's legacy fallback counts missing quantities as one.
    if connectors:
        record["hardware_claim"]["port_count"] = detail.get("port_count")
    for source in record["sources"]:
        source["detail_url"] = url
        source["detail_fetched_at"] = artifact.fetched_at
        source["source_fetched_at"] = record["source_fetched_at"]
        source["evidence"] = (source.get("evidence", "") + " Station page: "
                               + detail.get("station_name", "") + "; "
                               + tariff.get("text", ""))[:1000]
    record["source_integration"]["note"] = (
        "Public map and identity-checked charger-page facts; directory evidence, not live availability.")


def enrich_records(records: list[dict], engine, *, cache_dir: Path | None = None,
                   budget_seconds: float = 180) -> dict:
    from importlib import import_module
    parser = import_module(parse_canarias_detail.__module__)
    cache = cache_dir or engine.CACHE / "canarias_details"
    urls = sorted({safe_detail_url(r["source_url"]) for r in records} - {""})
    if len(urls) > MAX_PAGES:
        raise ValueError("Canarias detail request budget exceeded")
    deadline = time.monotonic() + budget_seconds
    def collect(url):
        try:
            art = fetch_detail(url, cache, engine, deadline=deadline)
            detail = parse_canarias_detail(art.decode("utf-8", "replace"), url, fetched_at=art.fetched_at)
            return url, art, detail, None
        except Exception as exc:
            return url, None, {}, type(exc).__name__
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = {url: (art, detail, error) for url, art, detail, error in pool.map(collect, urls)}
    report: dict[str, Any] = {"pages": len(urls), "enriched": 0, "held": [], "fetch_errors": []}
    for url, (art, _, error) in results.items():
        if art:
            engine.remember_fetch_artifact(art)
        if error:
            report["fetch_errors"].append({"url": url, "error": error})
    for record in records:
        art, detail, error = results.get(safe_detail_url(record["source_url"]), (None, {}, "unsafe_url"))
        matched, reason = parser.page_identity_matches(record, detail)
        if error or not matched:
            record["raw"]["detail_enrichment"] = {"status": "held", "reason": error or reason}
            report["held"].append({"id": record["raw"].get("source_record_id"),
                                   "url": record["source_url"], "reason": error or reason})
            continue
        apply_detail(record, detail, art, engine)
        record["raw"]["detail_enrichment"] = {"status": "matched", "basis": reason}
        report["enriched"] += 1
    return report


def _canarias(value: dict) -> bool:
    return value.get("source") == SOURCE or value.get("name") == SOURCE


def _hardware_quality(record: dict) -> tuple:
    connectors = record.get("connectors") or []
    return (len({c.get("type") for c in connectors if c.get("type") not in {None, "Unknown"}}),
            sum(c.get("power_kw") is not None for c in connectors), bool(record.get("max_power_kw")))


def overlay_payload(baseline: dict, fresh: list[dict], engine) -> tuple[dict, dict]:
    """Replace only the pre-existing source contribution, preserving all identities."""
    from importlib import import_module
    parser = import_module(parse_canarias_detail.__module__)
    rows = baseline["records"]
    aliases, markers = {}, {}
    for index, row in enumerate(rows):
        keys = {row.get("id"), *(row.get("merged_ids") or []),
                (row.get("identity_reconciliation") or {}).get("generated_id")} - {None, ""}
        for key in keys:
            aliases.setdefault(key, set()).add(index)
        raw = row.get("raw") or {}
        if _canarias(row) and str(raw.get("source_map_id")) == "4":
            markers.setdefault(str(raw.get("source_record_id")), set()).add(index)
    grouped = {}
    for record in fresh:
        recipients = markers.get(str(record["raw"].get("source_record_id"))) or aliases.get(record["id"], set())
        if len(recipients) != 1:
            raise ValueError("Canarias marker has no unique existing recipient: " + record["id"])
        index = next(iter(recipients))
        if not any(_canarias(s) for s in rows[index].get("sources", [])):
            raise ValueError("Recipient has no existing Canarias contribution")
        grouped.setdefault(index, []).append(record)
    candidate = dict(baseline)
    candidate["records"] = list(rows)
    report = {"markers": len(fresh), "recipients": len(grouped), "changed": 0, "primary_identity_held": []}
    for index, contributions in grouped.items():
        old = rows[index]
        # Two old aliases of one marker do not represent extra sockets.
        record = max(contributions, key=lambda r: ("detail" in r.get("raw", {}), _hardware_quality(r)))
        detail = record.get("raw", {}).get("detail")
        if not detail:
            continue
        same_site, site_reason = parser.page_identity_matches(old, detail)
        # Old GeoJSON names sometimes describe a different geocoder label, while
        # the popup has the correct site. A stable marker at the same position
        # can update that display label, after page→popup identity was checked.
        displacement = engine._dist_km(old, record)
        same_marker_position = (_canarias(old) and displacement <= .05
                                and site_reason not in {"detail_floor_conflict", "detail_explicit_coordinate_conflict"})
        if displacement > .25:
            same_site = same_marker_position = False
        if not same_site and not same_marker_position:
            report["primary_identity_held"].append(old["id"])
            continue
        new = copy.deepcopy(old)
        refreshed_sources = {s.get("url", "").rstrip("/"): s
                             for contribution in contributions if contribution.get("raw", {}).get("detail")
                             for s in contribution["sources"]}
        new["sources"] = [copy.deepcopy(refreshed_sources.pop(str(s.get("url", "")).rstrip("/"), s))
                          if _canarias(s) else s for s in new.get("sources", [])]
        new["sources"].extend(copy.deepcopy(list(refreshed_sources.values())))
        # Keep old fetch dates for all retained evidence. A rebuild is not a new fetch.
        new["source_fetched_at"] = engine.oldest_contributing_source_timestamp([old, record])
        if _canarias(old):
            new["raw"] = {**new.get("raw", {}), **copy.deepcopy(record["raw"])}
            new["name"] = detail.get("station_name") or old["name"]
            new["address"] = detail.get("address") or old.get("address", "")
        else:
            new["canarias_detail_evidence"] = {"marker_id": record["raw"].get("source_record_id"),
                "detail": detail, "fetch": record["raw"].get("detail_fetch")}
        for key in ("source_details", "tariff_details"):
            if key in record:
                new[key] = copy.deepcopy(record[key])
        claims = [c for c in engine.charge_price_candidates([old])
                  if not _canarias(c) and not _canarias(c.get("charge_claim") or {})]
        new.pop("charge_price_claims", None)
        if _canarias(new.get("charge_claim") or {}):
            for key in ("price_min", "price_max", "pricing", "price_current", "price_label"):
                new.pop(key, None)
            _set_price(new, record.get("price_min"), record.get("free_condition", ""), engine)
        engine.merge_charge_price([*claims, record], new)
        tariff = record.get("tariff_details") or {}
        has_cost_or_condition = (bool(tariff.get("conditions"))
            or any(c.get("type") in {"TIME", "MINIMUM", "FLAT"} and (c.get("price") or 0) > 0
                   for c in tariff.get("components", [])))
        conditional_conflict = (new.get("price_min") == 0 and record.get("price_min") is None
                                and has_cost_or_condition)
        if conditional_conflict:
            # Numeric-only merging cannot compare a free-energy claim with an
            # hourly/session tariff. Retain the old claim as an alternative,
            # but do not project unconditional Free over known positive costs.
            _set_price(new, None, record.get("free_condition", ""), engine)
            new["tariff_details"] = copy.deepcopy(tariff)
            for key in ("pricing", "price_current", "price_max", "price_label"):
                new.pop(key, None)
        price = new.get("price_min")
        new.update(is_free=price == 0, is_cheap=price is not None and price <= .25,
                   price_claim_type=engine.price_claim_type_for(price))
        restricted = ((old.get("access") or {}).get("level") == "restricted"
                      or (record.get("access") or {}).get("level") == "restricted"
                      or old.get("price_claim_type") == "access_limited_free")
        if (record.get("access") or {}).get("level") == "restricted":
            new["access"] = record["access"]
            new["access_claim"] = record["access_claim"]
        if restricted and price == 0:
            new.update(is_free=False, price_claim_type="access_limited_free")
        conflict = engine.charge_price_conflict([*claims, record], new)
        if conditional_conflict:
            conflict = {"level": "mixed_sources",
                "summary": "A public source lists free energy, but Canarias te recarga publishes tariff conditions or additional charges. Confirm the current tariff before routing.",
                "charge_url": record["source_url"],
                "alternate_charge_url": (claims[0].get("source_url") if claims else None),
                "charge_prices": [{"source": c.get("source"), "url": c.get("source_url"),
                                   "price_min": c.get("price_min")} for c in claims]}
        if conflict:
            new["claim_conflict"] = conflict
        elif (new.get("claim_conflict") or {}).get("charge_prices"):
            new.pop("claim_conflict", None)
        # Hourly details must not relabel a different source's selected energy tariff.
        if not _canarias(new.get("charge_claim") or {}) and new.get("price_min") is not None:
            new.pop("tariff_details", None)
            if tariff.get("text"):
                new["source_details"]["notes"].append("Canarias te recarga tariff: " + tariff["text"])
        old_hardware = new.get("hardware_claim") or {}
        replace_hardware = _canarias(old_hardware) or not new.get("connectors")
        if replace_hardware and record.get("connectors"):
            for key in ("connectors", "connector_labels", "total_evse", "connector_count",
                        "max_power_kw", "tesla_ok", "fast", "hardware_claim"):
                new[key] = copy.deepcopy(record[key])
        new["media"] = engine.merge_media([old, record])
        if _canarias(old):
            new["insight"] = record["sources"][0]["evidence"]
        new["source_count"] = len(new["sources"])
        candidate["records"][index] = new
        report["changed"] += 1
    candidate["generated_at"] = engine.now_iso()
    candidate["recordset_sha256"] = engine.recordset_sha256(candidate["records"])
    candidate["recordset_records"] = len(rows)
    candidate["canarias_enrichment"] = {"generated_at": candidate["generated_at"], **report}
    return candidate, report
