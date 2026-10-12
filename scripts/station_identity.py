"""Preserve station IDs across source merges and coordinate corrections.

Only unique stable source identifiers can carry an ID forward. Proximity alone,
generic source-home URLs and names are never identity evidence. Fresh rows keep
all their current fields and provenance; only application IDs and qualified merged
aliases change.
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
        "Etecnic EVcharge public charger map": ("etecnic_charger_id",),
        "Atlante public charger map": ("atlante_location_id",),
        "Zunder public charger map": ("zunder_location_id",),
        "Repsol public charging point finder": ("repsol_point_id",),
        "Electromaps public web map": ("electromaps_location_id",),
        "Chargemap public web map": ("chargemap_pool_id",),
        "Place to Plug public charging zones": ("ptp_zone_id",),
        "MOT (Aparcar) — Ajuntament de Reus municipal GIS": ("reus_feature_id",),
        "MOT (Aparcar) — Ajuntament de Tarragona open data": ("tarragona_nom",),
        "PlugShare community web map": ("plugshare_location_id",),
        "P-APP (Interparking) public car park finder": ("interparking_external_id",),
    }
    for field in typed_fields.get(family, ()):
        value = raw.get(field)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value):
            keys.add((str(family), field, str(value)))
    if family == "Telpark public parking pages":
        value = raw.get("telpark_parking_id")
        if type(value) is str and re.fullmatch(r"[1-9][0-9]*", value):
            keys.add((family, "telpark_parking_id", value))
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


def primary_identity_keys(row: dict) -> set[tuple]:
    """Stable keys owned by this station, excluding identities of absorbed neighbors."""
    return identity_keys({**row, "sources": [], "merged_source_identifiers": []})


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


def collision_ancestry(row: dict) -> set[str]:
    """Generated IDs explicitly split by ingestion, never ambiguous merged aliases."""
    ids = set(row.get("collision_base_ids") or [])
    if row.get("collision_base_id"):
        ids.add(row["collision_base_id"])
    return ids


def represented_ids(row: dict) -> set[str]:
    """Exact application IDs with current counterpart evidence on this pin."""
    ids = {row["id"], *(row.get("merged_ids") or [])}
    generated = (row.get("identity_reconciliation") or {}).get("generated_id")
    if generated:
        ids.add(generated)
    return ids | collision_ancestry(row)


def preserve_record_ids(baseline: dict, candidate: dict) -> tuple[dict, dict]:
    result = deepcopy(candidate)
    previous = baseline.get("records") or []
    rows = result.get("records") or []
    baseline_ids = {row["id"] for row in previous}
    candidate_ids = {row["id"] for row in rows}
    if len(baseline_ids) != len(previous) or len(candidate_ids) != len(rows):
        raise ValueError("duplicate station IDs cannot be reconciled")
    index = defaultdict(set)
    alias_claims = defaultdict(list)
    for i, row in enumerate(rows):
        for key in identity_keys(row):
            index[key].add(i)
        for alias in row.get("merged_ids") or []:
            alias_claims[str(alias)].append(i)
    baseline_key_counts = Counter(key for row in previous for key in identity_keys(row))
    baseline_by_id = {row["id"]: row for row in previous}

    def proven_alias(old: dict) -> bool:
        # Match delta.merged_into exactly: one claimant and a shared stable key.
        claimants = alias_claims.get(old["id"]) or []
        return len(claimants) == 1 and bool(identity_keys(old) & identity_keys(rows[claimants[0]]))

    proposals = []
    alias_changes = []
    for old in previous:
        if old["id"] in candidate_ids or proven_alias(old):
            continue
        matches = set()
        evidence = {}
        split_proven = False
        own = primary_identity_keys(old)
        for key in identity_keys(old):
            # A duplicated upstream identifier never establishes a unique match.
            if len(index[key]) == 1 and baseline_key_counts[key] == 1:
                i = next(iter(index[key]))
                matches.add(i)
                evidence.setdefault(i, []).append(key)
        if len(matches) > 1:
            # Prefer the primary station only when every other match has explicit
            # split evidence. Proximity or conflicting source keys alone never suffice.
            primary_matches = {i for i in matches if any(key in own for key in evidence[i])}
            if len(primary_matches) == 1:
                primary = next(iter(primary_matches))
                primary_collision = old["id"] in collision_ancestry(rows[primary])
                historical_merged = set(old.get("merged_ids") or [])
                split_proven = all(
                    (primary_collision and old["id"] in collision_ancestry(rows[i]))
                    or bool(historical_merged & represented_ids(rows[i]))
                    for i in matches - primary_matches
                )
                if split_proven:
                    matches = primary_matches
                    evidence[primary] = [key for key in evidence[primary] if key in own]
        if len(matches) == 1:
            i = next(iter(matches))
            if distance_m(old, rows[i]) > 250:
                continue
            if rows[i]["id"] not in baseline_ids:
                proposals.append((old["id"], i, evidence[i]))
            elif split_proven:
                # An existing baseline ID cannot be overwritten. A qualified split's
                # primary station may instead retain its historical ID as one alias.
                existing = baseline_by_id[rows[i]["id"]]
                primary_evidence = [key for key in evidence[i] if key in own]
                existing_evidence = [key for key in primary_identity_keys(existing)
                                     if baseline_key_counts[key] == 1 and index[key] == {i}
                                     and key in identity_keys(rows[i])]
                claimants = alias_claims.get(old["id"]) or []
                if (primary_evidence and existing_evidence and distance_m(existing, rows[i]) <= 250
                        and (not claimants or claimants == [i])):
                    rows[i]["merged_ids"] = sorted({*(rows[i].get("merged_ids") or []), old["id"]})
                    alias_claims[old["id"]] = [i]
                    alias_changes.append({
                        "merged_id": old["id"], "into": rows[i]["id"],
                        "basis": "explicit split witnesses and unique primary source identifiers within 250m",
                        "source_identifiers": [list(key) for key in sorted(primary_evidence)],
                        "existing_id_source_identifiers": [list(key) for key in sorted(existing_evidence)],
                    })
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
    result["recordset_sha256"] = recordset_sha256(rows)
    current_ids = {row["id"] for row in rows}
    remaining = sorted(old["id"] for old in previous
                       if old["id"] not in current_ids and not proven_alias(old))
    report = {"preserved_ids": len(changes), "changes": changes,
              "historical_aliases_added": len(alias_changes), "historical_alias_changes": alias_changes,
              "unresolved_missing_ids": remaining}
    return result, report
