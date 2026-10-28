#!/usr/bin/env python3
"""Offline paired VG-07 inventory, not a collector, pricing engine or release gate.

Consume retained canonical rows or captured /api/chargers JSON. Never import the
server or ingestion pipeline. Authority flags are read, never created; timestamps
can only downgrade them. See docs/pilot-coverage.md for scope and input contracts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeGuard

try:
    from .source_policy import load_source_policy_exclusions, matching_exclusion_ids
except ImportError:
    from source_policy import load_source_policy_exclusions, matching_exclusion_ids

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGIONS = {
    "tenerife": {
        "label": "Tenerife bounded island-area inventory",
        "bbox": [27.99, -16.95, 28.61, -16.10],
        "scope": "Geographic rectangle, not an administrative boundary or proof of island completeness.",
    },
    "valencia": {
        "label": "Valencia city and surrounding pilot area",
        "bbox": [39.29, -0.62, 39.65, -0.134],
        "scope": "Existing Valencia source-audit rectangle, not Valencia province.",
    },
}
SNAPSHOT_MODES = {"community_snapshot", "community_tags", "official_snapshot", "public_map_snapshot"}
COUNT_KEYS = (
    "sourced_numeric_price_locations", "conditional_sourced_price_locations", "source_text_only_locations",
    "unknown_primary_price_locations", "verified_current_price_locations",
    "price_annotation_locations", "fresh_live_locations", "fetch_time_snapshot_locations",
    "policy_excluded_locations", "conflicting_evidence_locations", "returned_by_api_locations",
    "explicit_map_eligible_locations", "map_eligibility_unknown_locations",
)


def obj(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def number(value: Any) -> TypeGuard[int | float]:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def integer(value: Any) -> TypeGuard[int | float]:
    return number(value) and int(value) == value


def clock(value: Any) -> datetime:
    if not isinstance(value, str) or "T" not in value:
        raise ValueError(f"Timezone-aware ISO timestamp required: {value!r}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"Timezone-aware ISO timestamp required: {value!r}")
    return parsed.astimezone(timezone.utc)


def fresh(value: Any, as_of: datetime, max_age: float) -> bool:
    try:
        age = (as_of - clock(value)).total_seconds()
        return 0 <= age <= max_age
    except (TypeError, ValueError, OverflowError):
        return False


def unexpired(value: dict, as_of: datetime) -> bool:
    for field in ("expires_at", "valid_until"):
        if field in value:
            try:
                if clock(value[field]) <= as_of:
                    return False
            except (TypeError, ValueError, OverflowError):
                return False
    return True


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def extract_rows(payload: Any) -> tuple[list[dict], dict, dict, list[str]]:
    """Only known catalogue/API envelopes. Never guess at a 150-row matrix."""
    issues: list[str] = []
    if isinstance(payload, list):
        rows, envelope = payload, {}
        shape = "array"
    elif isinstance(payload, dict):
        envelope = payload
        if "records" in payload and any(key in payload for key in ("stations", "leads")):
            raise ValueError("Ambiguous records/stations envelope")
        if "records" in payload:
            rows, shape = payload["records"], "records"
        elif "stations" in payload:
            if not isinstance(payload["stations"], list) or not isinstance(payload.get("leads", []), list):
                raise ValueError("stations and leads must be arrays")
            rows = payload["stations"] + payload.get("leads", [])
            shape = "stations+leads" if "leads" in payload else "stations"
        else:
            raise ValueError("Expected array, records, or stations with optional leads")
    else:
        raise ValueError("Expected JSON object or array")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Every input row must be an object")
    for key in ("count", "total_records"):
        if key in envelope and (not integer(envelope[key]) or envelope[key] != len(rows)):
            raise ValueError(f"Declared {key} does not match input row count {len(rows)}")
    if "total_matches" in envelope:
        total = envelope["total_matches"]
        if not integer(total) or total < len(rows):
            raise ValueError("Invalid declared total_matches")
        if total != len(rows):
            issues.append(f"total_matches={total} exceeds captured rows={len(rows)}; not a census")
    for container_name, container in (("response", envelope), ("meta", obj(envelope.get("meta")))):
        for key in ("truncated", "partial", "has_more"):
            if container.get(key):
                issues.append(f"{container_name}.{key} indicates incomplete input")
        if container.get("total_matches_is_complete") is False:
            issues.append(f"{container_name}.total_matches_is_complete=false")
    # Reject rather than silently choosing the last version of a repeated ID.
    seen: dict[str, str] = {}
    unique: list[dict] = []
    for row in rows:
        sid = row.get("id")
        if isinstance(sid, bool) or not isinstance(sid, (str, int)) or not str(sid).strip():
            raise ValueError("Every row requires a stable canonical id")
        sid = str(sid)
        encoded = canonical(row)
        if sid in seen:
            if seen[sid] != encoded:
                raise ValueError(f"Conflicting duplicate canonical id: {sid}")
        else:
            seen[sid] = encoded
            unique.append(row)
    info = {"shape": shape, "raw_rows": len(rows), "unique_canonical_ids": len(unique),
            "duplicate_identical_rows": len(rows) - len(unique),
            "declared_totals": {k: envelope[k] for k in ("count", "total_records", "total_matches") if k in envelope}}
    return unique, envelope, info, issues


def validate_regions(regions: dict) -> dict:
    if not isinstance(regions, dict) or set(regions) != {"tenerife", "valencia"}:
        raise ValueError("regions must define exactly tenerife and valencia")
    result = {}
    for name, definition in regions.items():
        if not isinstance(definition, dict):
            raise ValueError(f"Invalid region {name}")
        bbox = definition.get("bbox")
        if (not isinstance(bbox, list) or len(bbox) != 4
                or any(type(v) not in (float, int) or not math.isfinite(v) for v in bbox)):
            raise ValueError(f"Invalid bbox for {name}; use south,west,north,east")
        south, west, north, east = bbox
        if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
            raise ValueError(f"Invalid ordered bbox for {name}")
        result[name] = dict(definition, bbox=bbox)
    return result


def inside(row: dict, bbox: list) -> bool:
    lat, lon = row.get("lat"), row.get("lon")
    return (isinstance(lat, (int, float)) and not isinstance(lat, bool)
            and isinstance(lon, (int, float)) and not isinstance(lon, bool)
            and math.isfinite(lat) and math.isfinite(lon)
            and bbox[0] <= lat <= bbox[2] and bbox[1] <= lon <= bbox[3])


def source_references(row: dict) -> list[dict]:
    refs = [dict(name=row.get("source") or row.get("source_label"), url=row.get("source_url"))]
    refs += [obj(row.get("primary_source"))]
    refs += [value for value in row.get("sources") or [] if isinstance(value, dict)]
    seen, result = set(), []
    for ref in refs:
        url = ref.get("url")
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            continue
        # An exact retained URL identifies a reference, not a source listing ID.
        if url not in seen:
            seen.add(url)
            result.append(ref)
    return result


def classify(row: dict, *, mode: str, as_of: datetime, captured_fresh: bool,
             policy: dict, live_max_age: float, price_max_age: float) -> dict:
    verification = obj(row.get("verification"))
    integration = obj(row.get("source_integration"))
    status = obj(row.get("live_status"))
    claim = obj(row.get("charge_claim"))
    refs = source_references(row)
    exclusions = matching_exclusion_ids(row, policy)
    snapshot = (integration.get("mode") in SNAPSHOT_MODES
                or integration.get("live_status_integrated") is False
                or row.get("source_adapter") in {"etecnic", "zunder"})
    conflict = bool(row.get("claim_conflict") or status.get("conflict"))
    stale = bool(row.get("cache_stale") or row.get("_volta_stale_live_cache"))
    expired = not all(unexpired(value, as_of) for value in (row, status, claim))
    reasons = []
    if mode != "served-capture":
        reasons.append("bundle_is_not_runtime_evidence")
    if not captured_fresh:
        reasons.append("capture_missing_stale_or_future")
    if exclusions:
        reasons.append("source_policy_excluded")
    if snapshot:
        reasons.append("non_live_snapshot")
    if conflict:
        reasons.append("conflicting_evidence")
    if stale:
        reasons.append("stale_row")
    if expired:
        reasons.append("expired_or_unknown_expiry")
    base_ok = mode == "served-capture" and captured_fresh and not (exclusions or snapshot or conflict or stale or expired)
    # Match server.freshness_timestamp's distinction without importing the server.
    # A status feed's validation/publication clock is not a last-state-change clock.
    status_clock = (status.get("published_at") or status.get("checked_at")) if status else row.get("last_seen")
    status_fresh = fresh(status_clock, as_of, live_max_age)
    status_valid = (not status or status.get("valid") is True)
    if status:
        status_valid = status_valid and fresh(status.get("checked_at"), as_of, live_max_age)
    live = bool(base_ok and row.get("lead") is not True and verification.get("level") == "live"
                and verification.get("live_verified") is True and status_valid and status_fresh and refs)
    if not status_fresh:
        reasons.append("status_observation_missing_stale_or_future")
    if verification.get("live_verified") is not True:
        reasons.append("no_affirmative_live_authority")
    numeric = number(row.get("price_min")) and bool(refs)
    tariff = obj(row.get("tariff_details"))
    structured_tariff = (any(number(tariff.get(field)) for field in (
        "hour_eur", "minute_eur", "session_eur", "minimum_eur"))
        or bool(tariff.get("components")) or bool(row.get("pricing")))
    conditional = bool(refs and (structured_tariff or (numeric and (
        claim.get("conditions") or claim.get("type") in {"access_limited_free", "conditional"}))))
    source_text_only = bool(tariff.get("text")) and not structured_tariff
    # Do not calculate a tariff or infer zero from free flags. Consume the server's
    # current_price_min and price_verified, with conservative independent age gates.
    explicit_price_clock = claim.get("checked_at") or claim.get("source_updated_at")
    price_clock = explicit_price_clock or (row.get("last_seen") if not status else None)
    price_fresh = fresh(price_clock, as_of, price_max_age)
    live_price_guard = not status or obj(row.get("live_claim_verification")).get("price_verified") is True
    current = bool(base_ok and verification.get("level") in {"live", "official"}
                   and verification.get("price_verified") is True and live_price_guard
                   and number(row.get("current_price_min"))
                   and str(row.get("price_currency") or row.get("currency") or "EUR").upper() == "EUR"
                   and price_fresh and refs)
    price_reasons = list(reasons)
    if verification.get("price_verified") is not True:
        price_reasons.append("no_affirmative_price_authority")
    if not price_fresh:
        price_reasons.append("price_observation_missing_stale_or_future")
    if not live_price_guard:
        price_reasons.append("status_overlay_does_not_verify_inherited_price")
    if not number(row.get("current_price_min")):
        price_reasons.append("no_explicit_numeric_current_price")
    annotations = [value for value in row.get("price_evidence") or [] if isinstance(value, dict)]
    connectors = row.get("connector_count")
    if not integer(connectors):
        connectors = obj(row.get("connector_summary")).get("total")
    connectors = int(connectors) if integer(connectors) else None
    # total_evse is overwritten with connector totals in attach_connector_summary.
    # Preserve it as reported, never present it as a proven physical EVSE count.
    reported_evse = row.get("total_evse")
    return {
        "id": str(row["id"]), "name": row.get("name"), "lat": row.get("lat"), "lon": row.get("lon"),
        "source": row.get("source") or row.get("source_label"), "source_adapter": row.get("source_adapter"),
        "source_references": refs, "policy_exclusion_ids": exclusions,
        "verification": verification, "lead": row.get("lead"),
        "source_integration": integration, "decision": row.get("decision"),
        "clocks": {
            "last_seen": row.get("last_seen"), "source_fetched_at": row.get("source_fetched_at"),
            "source_fetch": row.get("source_fetch"), "source_updated_at": row.get("source_updated_at"),
            "source_last_updated": row.get("source_last_updated"), "source_published_at": row.get("source_published_at"),
            "status_validated_at": status.get("checked_at"), "status_published_at": status.get("published_at"),
            "charge_checked_at": claim.get("checked_at"), "charge_source_updated_at": claim.get("source_updated_at"),
            "status_age_basis": status_clock, "price_age_basis": price_clock,
        },
        "price_min": row.get("price_min"), "current_price_min": row.get("current_price_min"),
        "charge_claim": claim, "tariff_details": tariff, "price_evidence": annotations,
        "explicit_validity": {"row": {k: row[k] for k in ("expires_at", "valid_until") if k in row},
                              "status": {k: status[k] for k in ("expires_at", "valid_until") if k in status}},
        "connector_count": connectors, "reported_total_evse": reported_evse if integer(reported_evse) else None,
        "flags": {
            "sourced_numeric_price_locations": bool(numeric),
            "conditional_sourced_price_locations": conditional,
            "source_text_only_locations": source_text_only,
            "unknown_primary_price_locations": not numeric and not conditional,
            "verified_current_price_locations": current,
            "price_annotation_locations": bool(annotations), "fresh_live_locations": live,
            "fetch_time_snapshot_locations": snapshot, "policy_excluded_locations": bool(exclusions),
            "conflicting_evidence_locations": conflict, "returned_by_api_locations": mode == "served-capture",
            "explicit_map_eligible_locations": row.get("map_eligible") is True,
            "map_eligibility_unknown_locations": type(row.get("map_eligible")) is not bool,
        },
        "authority_rejections": sorted(set(reasons)),
        "price_authority_rejections": sorted(set(price_reasons)),
    }


def summarize(rows: list[dict], definition: dict, location_map: dict | None) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    mapping = location_map["location_ids"] if location_map else {}
    for row in rows:
        groups[mapping.get(row["id"], row["id"])].append(row)
    counts = Counter({key: 0 for key in COUNT_KEYS})
    locations, by_source, by_primary, annotation_kinds = [], Counter(), Counter(), Counter()
    connector_values, reported_evse_values = [], []
    listing_refs = 0
    ambiguous_hardware = 0
    for location_id, members in sorted(groups.items()):
        flags = {key: any(member["flags"][key] for member in members) for key in COUNT_KEYS}
        # A reviewed group with mutually inconsistent prices cannot acquire current
        # price authority from one member. This is a rejection, not price selection.
        numeric_values = {member["price_min"] for member in members if number(member["price_min"])}
        current_values = {member["current_price_min"] for member in members if number(member["current_price_min"])}
        if len(numeric_values) > 1 or len(current_values) > 1 or flags["conflicting_evidence_locations"]:
            flags["conflicting_evidence_locations"] = True
            flags["verified_current_price_locations"] = False
            flags["fresh_live_locations"] = False
        for key, value in flags.items():
            counts[key] += int(value)
        # Source references count once per location+exact URL; they are not known
        # upstream listing IDs and never an additional physical-location denominator.
        refs = {ref["url"]: ref for member in members for ref in member["source_references"]}
        listing_refs += len(refs)
        for ref in refs.values():
            by_source[str(ref.get("name") or "Unknown")] += 1
        for source in {str(member["source"] or "Unknown") for member in members}:
            by_primary[source] += 1
        for member in members:
            for annotation in member["price_evidence"]:
                annotation_kinds[str(annotation.get("kind") or "unknown")] += 1
        if len(members) != 1:
            ambiguous_hardware += 1
            connector_values.append(None)
            reported_evse_values.append(None)
        else:
            connector_values.append(members[0]["connector_count"])
            reported_evse_values.append(members[0]["reported_total_evse"])
        locations.append({"location_id": location_id, "canonical_ids": [m["id"] for m in members],
                          "flags": flags, "rows": sorted(members, key=lambda m: m["id"])})
    denominator = len(groups)
    return {
        "definition": definition,
        "denominator": {
            "unique_locations": denominator,
            "unique_canonical_ids": len(rows),
            "unique_physical_locations": denominator if location_map else None,
            "basis": location_map["basis"] if location_map else "canonical station id; physical uniqueness not independently reviewed",
            "completeness": "Input inventory only, not all chargers in the region",
        },
        "counts": dict(counts),
        "shares": {key: {"numerator": value, "denominator": denominator,
                         "fraction": value / denominator if denominator else None} for key, value in counts.items()},
        "hardware": {
            "connectors_total": sum(connector_values) if all(v is not None for v in connector_values) else None,
            "connectors_known_subtotal": sum(v for v in connector_values if v is not None),
            "connector_count_unknown_locations": sum(v is None for v in connector_values),
            "evse_total": None,
            "evse_count_note": "total_evse is overloaded by server connector summaries; no proven EVSE denominator available",
            "reported_total_evse_sum": sum(v for v in reported_evse_values if v is not None),
            "reported_total_evse_unknown_locations": sum(v is None for v in reported_evse_values),
            "ambiguous_multirow_locations": ambiguous_hardware,
        },
        "sources": {"source_listing_references": listing_refs,
                    "source_listing_reference_basis": "distinct location+exact source URL, not upstream listing IDs",
                    "by_reference_source": dict(sorted(by_source.items())),
                    "locations_by_primary_source": dict(sorted(by_primary.items())),
                    "retained_price_annotations_by_kind": dict(sorted(annotation_kinds.items()))},
        "locations": locations,
    }


def build_report(payload: Any, *, mode: str, as_of: str, source_revision: str,
                 observed_at: str | None = None, regions: dict | None = None,
                 location_map: dict | None = None, live_max_age: float = 180 * 60,
                 price_max_age: float = 180 * 60, policy: dict | None = None) -> dict:
    """Pure offline aggregation except reading the existing source policy by default."""
    if mode not in {"admitted-bundle", "served-capture"}:
        raise ValueError("mode must be admitted-bundle or served-capture")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", source_revision):
        raise ValueError("source_revision must be an exact 40-character Git revision")
    now = clock(as_of)
    if mode == "served-capture" and observed_at is None:
        raise ValueError("served-capture requires observed_at")
    if observed_at is not None:
        if clock(observed_at) > now:
            raise ValueError("observed_at must not be after as_of")
    if not number(live_max_age) or live_max_age == 0 or not number(price_max_age) or price_max_age == 0:
        raise ValueError("Age bounds must be finite positive seconds")
    definitions = validate_regions(regions if regions is not None else DEFAULT_REGIONS)
    rows, envelope, info, issues = extract_rows(payload)
    if location_map is not None:
        mapping = obj(location_map).get("location_ids")
        if (not isinstance(mapping, dict) or not isinstance(location_map.get("basis"), str)
                or not location_map["basis"].strip()
                or any(not isinstance(k, str) or not isinstance(v, str) or not v.strip() for k, v in mapping.items())
                or any(str(row["id"]) not in mapping for row in rows if any(inside(row, d["bbox"]) for d in definitions.values()))):
            raise ValueError("location map requires review basis and every regional canonical ID")
    policy = policy if policy is not None else load_source_policy_exclusions()
    captured_fresh = fresh(observed_at, now, min(live_max_age, price_max_age))
    cache = obj(envelope.get("cache"))
    capture_authority_blocked = (cache.get("fresh_live_authority") is False
                                 or cache.get("live_supported") is False
                                 or cache.get("live_failed") is True)
    if capture_authority_blocked:
        issues.append("Response explicitly denies fresh live authority; current/live counts withheld")
    regional = {}
    in_any = set()
    for name, definition in definitions.items():
        selected = [row for row in rows if inside(row, definition["bbox"])]
        in_any.update(str(row["id"]) for row in selected)
        classified = [classify(row, mode=mode, as_of=now, captured_fresh=captured_fresh and not capture_authority_blocked, policy=policy,
                               live_max_age=live_max_age, price_max_age=price_max_age) for row in selected]
        regional[name] = summarize(classified, definition, location_map)
    info["complete_inventory"] = mode == "admitted-bundle" and not issues
    info["outside_regions_or_invalid_coordinates"] = len(rows) - len(in_any)
    if mode == "served-capture":
        issues.append("Captured API rows are an observed query subset, not a regional census")
    if not location_map:
        issues.append("No reviewed physical-location map; canonical IDs are the explicit location proxy")
    if mode == "admitted-bundle":
        issues.append("Admission is caller-declared; this report does not validate the publication gate")
    if mode == "served-capture" and not captured_fresh:
        issues.append("Capture is stale relative to as_of; all current/live authority is withheld")
    return {
        "schema_version": 1, "report_kind": "offline_paired_pilot_inventory",
        "evidence_mode": mode, "runtime_acceptance": False,
        "acceptance_note": "No thresholds or independent regional census supplied; counts are not a release or runtime acceptance verdict",
        "as_of": as_of, "observed_at": observed_at,
        "age_limits_seconds": {"live": live_max_age, "current_price": price_max_age},
        "identity": {"source_revision": source_revision, "source_revision_binding": "caller-declared; not an attested deployed revision",
                     "data_sha256": None, "observed_response_sha256": None,
                     "row_content_sha256": hashlib.sha256(canonical(sorted(rows, key=lambda r: str(r["id"]))).encode()).hexdigest(),
                     "source_policy_sha256": hashlib.sha256(canonical(policy).encode()).hexdigest()},
        "input": info,
        "source_metadata": {key: envelope[key] for key in ("generated_at", "sources", "source_status", "license_notes") if key in envelope},
        "failures_and_refusals": {key: envelope[key] for key in (
            "errors", "warning", "partial", "partial_reason", "cache", "gaia", "electromaps", "meta") if key in envelope},
        "regions": regional, "issues": issues,
    }


def load_json(path: Path) -> Any:
    return decode_json(path.read_bytes())


def decode_json(content: bytes) -> Any:
    def reject_constant(value: str):
        raise ValueError(f"Non-finite JSON number: {value}")
    return json.loads(content.decode("utf-8"), parse_constant=reject_constant)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True, help="Retained bundle or captured response, never a URL")
    parser.add_argument("--mode", choices=("admitted-bundle", "served-capture"), required=True)
    parser.add_argument("--as-of", required=True, help="Explicit timezone-aware validation clock")
    parser.add_argument("--observed-at", help="Capture observation time; never a substitute for source clocks")
    parser.add_argument("--source-revision", required=True, help="Exact caller-declared Git source revision")
    parser.add_argument("--bundle", type=Path, help="Served capture's retained data bundle, hashed separately; binding remains caller-declared")
    parser.add_argument("--regions", type=Path, help="JSON object defining tenerife and valencia, each with bbox [south,west,north,east]")
    parser.add_argument("--location-map", type=Path, help="Reviewed {basis,location_ids:{canonical_id:physical_id}} JSON")
    parser.add_argument("--live-max-age-seconds", type=float, default=180 * 60)
    parser.add_argument("--price-max-age-seconds", type=float, default=180 * 60)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.bundle and args.mode != "served-capture":
            raise ValueError("--bundle is only valid for served-capture")
        if args.out.resolve() in {path.resolve() for path in (args.data, args.bundle, args.regions, args.location_map) if path}:
            raise ValueError("Report output must not overwrite an input")
        # Hash the same retained bytes that are parsed, not a later read of a file
        # which may have changed while the report was being assembled.
        inputs = {name: path.read_bytes() for name, path in (
            ("data", args.data), ("bundle", args.bundle), ("regions", args.regions),
            ("location_map", args.location_map)) if path}
        report = build_report(
            decode_json(inputs["data"]), mode=args.mode, as_of=args.as_of, observed_at=args.observed_at,
            source_revision=args.source_revision, regions=decode_json(inputs["regions"]) if args.regions else None,
            location_map=decode_json(inputs["location_map"]) if args.location_map else None,
            live_max_age=args.live_max_age_seconds, price_max_age=args.price_max_age_seconds,
        )
        identity = report["identity"]
        if args.mode == "admitted-bundle":
            identity["data_sha256"] = hashlib.sha256(inputs["data"]).hexdigest()
        else:
            identity["observed_response_sha256"] = hashlib.sha256(inputs["data"]).hexdigest()
            identity["data_sha256"] = hashlib.sha256(inputs["bundle"]).hexdigest() if args.bundle else None
        identity["reporter_sha256"] = digest_file(Path(__file__))
        identity["region_config_sha256"] = hashlib.sha256(inputs["regions"]).hexdigest() if args.regions else hashlib.sha256(canonical(DEFAULT_REGIONS).encode()).hexdigest()
        identity["location_map_sha256"] = hashlib.sha256(inputs["location_map"]).hexdigest() if args.location_map else None
        identity["input_name"] = args.data.name
        identity["associated_bundle_name"] = args.bundle.name if args.bundle else None
        identity["data_binding"] = "caller-declared associated bundle; no response-to-bundle attestation" if args.bundle else "input file" if args.mode == "admitted-bundle" else "unavailable"
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        print(json.dumps({"written": str(args.out), "evidence_mode": args.mode, "runtime_acceptance": False,
                          "unique_locations": {name: region["denominator"]["unique_locations"] for name, region in report["regions"].items()}}))
        return 0
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
