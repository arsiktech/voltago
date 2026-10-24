"""Preserve station IDs across source merges and coordinate corrections.

Only unique stable source identifiers can carry an ID forward. Proximity alone,
generic source-home URLs and names are never identity evidence. Fresh rows keep
all their current fields and provenance; only their application ID changes.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from copy import deepcopy
import math
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

from source_policy import recordset_sha256


def identity_keys(row: dict) -> set[tuple]:
    keys = set()
    raw = row.get("raw") or {}
    family = row.get("source")
    typed_fields = {
        "MapaREVE public OCPI snapshot": ("location_id",),
        "DGT National Access Point charging registry": ("nap_site_id",),
        "Feníe Energía public map (Gratuitos)": ("fenie_code",),
        "Lidl official free list": ("official_query",),
        "Catalonia ICAEN charging points open data": ("regional_station_key",),
        "Navarra IDENA charging points open data": ("regional_station_key",),
        "Castilla y León charging points open data": ("regional_station_key",),
    }
    for field in typed_fields.get(family, ()):
        value = raw.get(field)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value):
            keys.add((str(family), field, str(value)))
    # Identifiers of rows merged into this pin, recorded by the ingestion merge. They are the
    # same typed keys this function produces, so they carry the same strictness.
    for carried in row.get("merged_source_identifiers") or []:
        if isinstance(carried, (list, tuple)) and 2 <= len(carried) <= 3 and all(isinstance(x, str) and x for x in carried):
            keys.add(tuple(carried))
    urls = [row.get("source_url")]
    urls.extend(item.get("url") for item in row.get("sources", []) if isinstance(item, dict))
    for url in urls:
        try:
            parsed = urlsplit(str(url or ""))
            host = parsed.hostname
            if parsed.scheme not in {"https", "http"} or parsed.username or parsed.password:
                continue
            if host in {"www.openstreetmap.org", "openstreetmap.org"} and re.fullmatch(r"/(node|way|relation)/\d+", parsed.path):
                keys.add(("osm", parsed.path))
            elif host == "canariasterecarga.com" and re.fullmatch(r"/es/[^/]+/?", parsed.path):
                keys.add(("canarias-station-page", parsed.path.rstrip("/")))
            elif host == "recarga.fenieenergia.es" and re.fullmatch(r"/punto-recarga/[A-Za-z0-9_-]+/?", parsed.path):
                keys.add(("fenie-station-page", parsed.path.rstrip("/")))
            elif host == "map.openchargemap.io":
                query = parse_qs(parsed.query)
                if query.get("mode") == ["details"] and len(query.get("id", [])) == 1 and query["id"][0].isdigit():
                    keys.add(("ocm-station", query["id"][0]))
        except (ValueError, TypeError):
            continue
    return keys


def distance_m(a: dict, b: dict) -> float:
    try:
        lat1, lat2 = math.radians(float(a["lat"])), math.radians(float(b["lat"]))
        lon1, lon2 = math.radians(float(a["lon"])), math.radians(float(b["lon"]))
        if not all(math.isfinite(x) for x in (lat1, lat2, lon1, lon2)):
            return math.inf
        h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
        return 6371000 * 2 * math.asin(min(1.0, math.sqrt(max(0.0, h))))
    except (KeyError, TypeError, ValueError):
        return math.inf


def preserve_record_ids(baseline: dict, candidate: dict) -> tuple[dict, dict]:
    result = deepcopy(candidate)
    previous = baseline.get("records") or []
    rows = result.get("records") or []
    baseline_ids = {row["id"] for row in previous}
    candidate_ids = {row["id"] for row in rows}
    if len(baseline_ids) != len(previous) or len(candidate_ids) != len(rows):
        raise ValueError("duplicate station IDs cannot be reconciled")
    index = defaultdict(set)
    for i, row in enumerate(rows):
        for key in identity_keys(row):
            index[key].add(i)
    baseline_key_counts = Counter(key for row in previous for key in identity_keys(row))
    proposals = []
    absorbed = []
    for old in previous:
        if old["id"] in candidate_ids:
            continue
        matches = set()
        evidence = {}
        for key in identity_keys(old):
            # A duplicated upstream identifier never establishes a unique match.
            if len(index[key]) == 1 and baseline_key_counts[key] == 1:
                i = next(iter(index[key]))
                matches.add(i)
                evidence.setdefault(i, []).append(key)
        if len(matches) == 1:
            i = next(iter(matches))
            if distance_m(old, rows[i]) > 250:
                continue
            if rows[i]["id"] not in baseline_ids:
                proposals.append((old["id"], i, evidence[i]))
            elif rows[i].get("merged_ids"):
                # The station was merged into a station that keeps its own baseline ID,
                # for example an OSM row absorbed by a registry row. The old ID survives
                # as a merged ID, which lookups and the delta gate already honour.
                absorbed.append((old["id"], i, evidence[i]))
    uses = Counter(i for _, i, _ in proposals)
    changes = []
    for previous_id, i, evidence in proposals:
        if uses[i] != 1:
            continue
        generated_id = rows[i]["id"]
        rows[i]["id"] = previous_id
        rows[i]["identity_reconciliation"] = {
            "generated_id": generated_id,
            "basis": "unique stable source identifier within 250m",
            "source_identifiers": [list(key) for key in sorted(evidence)],
        }
        changes.append({"preserved_id": previous_id, "generated_id": generated_id,
                        "source_identifiers": [list(key) for key in sorted(evidence)]})
    merged_changes = []
    for previous_id, i, evidence in absorbed:
        merged_ids = rows[i].setdefault("merged_ids", [])
        if previous_id in merged_ids:
            continue  # the ingestion merge already recorded it
        merged_ids.append(previous_id)
        merged_changes.append({"merged_id": previous_id, "into_id": rows[i]["id"],
                               "source_identifiers": [list(key) for key in sorted(evidence)]})
    # A station that keeps its baseline ID keeps the old IDs merged into it earlier. The
    # ingestion merge rebuilds merged_ids from current upstream IDs only, so without this an
    # alias recorded by one refresh would vanish in the next and saved references would break.
    final_ids = {row["id"] for row in rows}
    claimed = {alias for row in rows for alias in row.get("merged_ids") or []}
    previous_by_id = {row["id"]: row for row in previous}
    # Each surviving station inherits the aliases of its own baseline row and of every
    # baseline station merged into it now, so an absorbed station's history moves with it.
    inherited: dict[int, list] = defaultdict(list)
    for i, row in enumerate(rows):
        inherited[i].extend((previous_by_id.get(row["id"]) or {}).get("merged_ids") or [])
    for previous_id, i, _evidence in absorbed:
        inherited[i].extend((previous_by_id.get(previous_id) or {}).get("merged_ids") or [])
    carried = []
    for i, row in enumerate(rows):
        for alias in inherited[i]:
            if alias in final_ids or alias in claimed:
                continue  # the alias is a station again, or another station holds it
            row.setdefault("merged_ids", []).append(alias)
            claimed.add(alias)
            carried.append({"merged_id": alias, "into_id": row["id"]})
    result["recordset_sha256"] = recordset_sha256(rows)
    remaining = sorted(baseline_ids - final_ids - claimed)
    report = {"preserved_ids": len(changes), "changes": changes, "merged_previous_ids": merged_changes,
              "carried_aliases": len(carried), "unresolved_missing_ids": remaining,
              "changed": bool(changes or merged_changes or carried)}
    return result, report
