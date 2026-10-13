#!/usr/bin/env python3
"""Audit Valencia-area charger records for source and handoff completeness.

This is intentionally offline and stdlib-only: it checks the public-source data we
already ship, classifies whether a row has a public remote start/check page, and
flags rows that need more evidence before they can be treated as route-ready.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from .json_recordset import load_json_recordset
except ImportError:
    from json_recordset import load_json_recordset


VALENCIA_BBOX = {
    "south": 39.29,
    "west": -0.62,
    "north": 39.65,
    "east": -0.134,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def in_bbox(row: dict[str, Any], bbox: dict[str, float]) -> bool:
    lat = row.get("lat")
    lon = row.get("lon")
    return (
        isinstance(lat, (int, float))
        and isinstance(lon, (int, float))
        and bbox["south"] <= lat <= bbox["north"]
        and bbox["west"] <= lon <= bbox["east"]
    )


def hostname(url: str | None) -> str:
    if not url:
        return ""
    try:
        return urlparse(url).netloc.lower()
    except ValueError:
        return ""


def is_public_start_page(source: dict[str, Any] | None, fallback_url: str | None) -> bool:
    if not source:
        source = {}
    kind = str(source.get("kind") or "").lower()
    url = str(source.get("url") or fallback_url or "")
    if kind == "start_page":
        return True
    # Defensive fallback for old/generated rows before primary_source.kind existed.
    return "gaiacharge.com" in hostname(url) and "/start-session/" in url


def primary_source(row: dict[str, Any]) -> dict[str, Any]:
    primary = row.get("primary_source")
    if isinstance(primary, dict):
        return primary
    url = row.get("source_url") or row.get("url")
    return {
        "name": row.get("source") or row.get("source_label") or "",
        "url": url,
        "kind": source_kind(row.get("source") or row.get("source_label") or "", url),
        "quality": source_quality(url),
    }


def source_kind(name: str, url: str | None) -> str:
    host = hostname(url)
    lower_name = (name or "").lower()
    lower_url = (url or "").lower()
    if "gaiacharge.com" in host and "/start-session/" in lower_url:
        return "start_page"
    if "map.electromaps.com" in host or "chargemap.com" in host:
        return "station_page"
    if "openchargemap" in host or "openstreetmap" in host or "osm.org" in host:
        return "map_object"
    if "consum.es" in host or "lidl" in host or "carrefour" in host:
        return "store_page"
    if "mapareve" in host:
        return "live_map"
    if "opendata" in host or "valencia" in host:
        return "official_data"
    return "source_page" if url else ""


def source_quality(url: str | None) -> str:
    if not url:
        return ""
    lower = url.lower()
    if "/p/" in lower or "openchargemap.org/site/poi/details" in lower:
        return "exact"
    if "maps/search" in lower:
        return "search"
    return "public"


def evidence_urls(row: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    primary = primary_source(row)
    if primary.get("url"):
        urls.append(str(primary["url"]))
    if row.get("source_url"):
        urls.append(str(row["source_url"]))
    for source in row.get("sources") or []:
        if isinstance(source, dict) and source.get("url"):
            urls.append(str(source["url"]))
    # Stable order, no duplicates.
    seen: set[str] = set()
    unique: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


def classify_row(row: dict[str, Any]) -> dict[str, Any]:
    primary = primary_source(row)
    urls = evidence_urls(row)
    start = is_public_start_page(primary, urls[0] if urls else None)
    verification = row.get("verification") or {}
    parking = row.get("parking") or {}
    charge_claim = row.get("charge_claim") or {}
    parking_claim = row.get("parking_claim") or {}
    issues: list[str] = []

    if not urls:
        issues.append("missing_public_source_url")
    if row.get("price_min") is None and not row.get("is_free"):
        issues.append("missing_price")
    if not isinstance(row.get("lat"), (int, float)) or not isinstance(row.get("lon"), (int, float)):
        issues.append("invalid_coordinates")
    if verification and verification.get("location_verified") is False:
        issues.append("location_marked_unverified")
    if verification and verification.get("price_verified") is False:
        issues.append("price_marked_unverified")
    if parking.get("kind") in (None, "", "unknown"):
        issues.append("parking_unknown")
    if not charge_claim:
        issues.append("missing_charge_claim")
    if not parking_claim:
        issues.append("missing_parking_claim")
    if not start and primary.get("kind") == "start_page":
        issues.append("start_page_classification_mismatch")

    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "city": row.get("city"),
        "lat": row.get("lat"),
        "lon": row.get("lon"),
        "source": row.get("source") or row.get("source_label") or primary.get("name"),
        "reliability": row.get("reliability"),
        "primary_kind": primary.get("kind") or "",
        "primary_quality": primary.get("quality") or primary.get("exactness") or "",
        "primary_url": primary.get("url") or (urls[0] if urls else ""),
        "public_start_page": start,
        "card_handoff": "start_check" if start else "navigation_only",
        "price_min": row.get("price_min"),
        "is_free": row.get("is_free"),
        "parking_kind": parking.get("kind"),
        "parking_label": parking.get("label"),
        "charge_claim_type": charge_claim.get("type") or charge_claim.get("kind"),
        "parking_claim_type": parking_claim.get("type") or parking_claim.get("kind"),
        "evidence_url_count": len(urls),
        "issues": issues,
    }


def sample(rows: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    return rows[:limit]


def audit_records(path: Path, bbox: dict[str, float]) -> dict:
    """Stream the catalogue, retaining only counters and bounded report samples."""
    total = local = 0
    by_source, by_reliability, by_kind, by_handoff, issues = (Counter() for _ in range(5))
    issue_samples = defaultdict(list)
    remote_start, navigation_only, highest_gaps = [], [], []

    def consume(raw):
        nonlocal total, local
        total += 1
        if not isinstance(raw, dict) or not in_bbox(raw, bbox):
            return None
        row = classify_row(raw)
        local += 1
        by_source[str(row.get("source") or "Unknown")] += 1
        by_reliability[str(row.get("reliability") or "unknown")] += 1
        by_kind[str(row.get("primary_kind") or "unknown")] += 1
        by_handoff[str(row.get("card_handoff") or "unknown")] += 1
        target = remote_start if row["public_start_page"] else navigation_only
        if len(target) < 12:
            target.append(row)
        for issue in row["issues"]:
            issues[issue] += 1
            if len(issue_samples[issue]) < 8:
                issue_samples[issue].append({key: row[key] for key in ("id", "name", "source", "primary_url")})
        highest_gaps.append(row)
        highest_gaps.sort(key=lambda item: (-len(item["issues"]), str(item["name"] or "")))
        del highest_gaps[12:]
        return None

    metadata = load_json_recordset(path, record_transform=consume, record_keys=("records", "stations"),
                                   allow_root_array=True, strict_records=False)
    if not any(key in metadata for key in ("records", "stations")):
        raise SystemExit(f"{path} is not a station list or {{stations: [...]}} payload")
    if all(key in metadata for key in ("records", "stations")):
        raise ValueError("Audit input has ambiguous records and stations arrays")
    return {"total_records": total, "valencia_records": local,
            "counts": {"by_source": dict(by_source.most_common()), "by_reliability": dict(by_reliability.most_common()),
                       "by_primary_kind": dict(by_kind.most_common()), "by_card_handoff": dict(by_handoff.most_common()),
                       "issues": dict(issues.most_common())},
            "remote_start_records": remote_start, "navigation_only_samples": navigation_only,
            "highest_gap_samples": highest_gaps, "issue_samples": dict(issue_samples)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/free_intelligence.json")
    parser.add_argument(
        "--data-label",
        help="Stable path label to record when auditing an equivalent temporary candidate.",
    )
    parser.add_argument("--out", default="qa/valencia-source-audit-latest.json")
    parser.add_argument("--south", type=float, default=VALENCIA_BBOX["south"])
    parser.add_argument("--west", type=float, default=VALENCIA_BBOX["west"])
    parser.add_argument("--north", type=float, default=VALENCIA_BBOX["north"])
    parser.add_argument("--east", type=float, default=VALENCIA_BBOX["east"])
    args = parser.parse_args()

    bbox = {"south": args.south, "west": args.west, "north": args.north, "east": args.east}
    data_path = Path(args.data)
    audit = audit_records(data_path, bbox)
    result = {
        "ok": True,
        "data": args.data_label or str(data_path),
        "data_sha256": sha256_file(data_path),
        "bbox": bbox,
        **audit,
        "policy": {
            "card_start_cta": "Only rows with primary_source.kind=start_page or Gaia public start-session URLs may expose a Start/check CTA.",
            "navigation_only": "All other station/store/directory/map/live-map evidence stays in Details; the card handoff is navigation only.",
        },
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "out": str(out_path),
        "valencia_records": audit["valencia_records"],
        "handoff": audit["counts"]["by_card_handoff"],
        "top_sources": dict(list(audit["counts"]["by_source"].items())[:8]),
        "top_issues": dict(list(audit["counts"]["issues"].items())[:8]),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
