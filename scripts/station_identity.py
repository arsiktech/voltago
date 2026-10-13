"""Preserve station IDs across source merges and coordinate corrections.

Only unique stable source identifiers can carry an ID forward. Proximity alone,
generic source-home URLs and names are never identity evidence. Fresh rows keep
all their current facts and provenance; reconciliation changes only application
IDs and proven identity-alias metadata.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from copy import deepcopy
import math
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

try:
    from .source_policy import recordset_sha256
except ImportError:
    from source_policy import recordset_sha256
import hashlib
import json
from dataclasses import dataclass


def identity_keys(row: dict) -> set[tuple]:
    keys = set()
    raw = row.get("raw") or {}
    family = row.get("source")
    typed_fields = {
        "MapaREVE public OCPI snapshot": ("location_id",),
        "DGT National Access Point charging registry": ("nap_site_id",),
        "Feníe Energía public map (Gratuitos)": ("fenie_code",),
        "Lidl official free list": ("official_query",),
        "Barcelona Endolla open data": ("endolla_location_id",),
        "Zaragoza municipal open data": ("zaragoza_id",),
    }
    for field in typed_fields.get(family, ()):
        value = raw.get(field)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value):
            keys.add((str(family), field, str(value)))
    if family == "València OpenData":
        for item in raw.get("items") or []:
            value = item.get("objectid") if isinstance(item, dict) else None
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                keys.add((family, "objectid", str(value)))
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



VERSION = 1
MAX_REFERENCES = 100000
AUDIT_FIELDS = {"identity_aliases", "merged_ids", "merged_source_identifiers", "identity_reconciliation", "reference_inputs"}


def reference_key(identifier: str) -> str:
    return hashlib.sha256(("volta-reference-v1\0" + identifier).encode("utf-8")).hexdigest()


def primary_keys(row: dict) -> set[tuple]:
    """Only the source owning the row, excluding absorbed/corroborating sources."""
    return identity_keys({k: v for k, v in row.items() if k not in {"sources", "merged_source_identifiers"}})


def anchor(row: dict) -> dict:
    return {"id": row["id"], "source": row.get("source"), "lat": row.get("lat"), "lon": row.get("lon"),
            "source_identifiers": [list(k) for k in sorted(identity_keys(row))],
            "primary_identifiers": [list(k) for k in sorted(primary_keys(row))],
            "contributors": contributor_evidence(row),
            **({'representation': representation(row)} if representation(row) else {})}


def representation(row: dict) -> dict | None:
    """Map marker IDs are representations of a page, not independent station IDs."""
    raw = row.get('raw') or {}
    keys = primary_keys(row)
    if (row.get('source') == 'Canarias te recarga public map' and len(keys) == 1
            and next(iter(keys))[0] == 'canarias-station-page'
            and raw.get('detail_url') == row.get('source_url')
            and str(raw.get('source_record_id') or '').isdigit() and raw.get('source_map_id')):
        return {'map_id': str(raw['source_map_id']), 'record_id': str(raw['source_record_id']),
                'page': next(iter(keys))[1]}
    return None


def _copy_pair(a: dict, b: dict) -> bool:
    ar, br = a.get('representation'), b.get('representation')
    return bool(ar and br and a.get('source') == b.get('source') == 'Canarias te recarga public map'
                and ar['page'] == br['page'] and ar['map_id'] == br['map_id']
                and ar['record_id'] != br['record_id'] and distance_m(a, b) <= 250)


def _previous_copies(old: dict, owner: dict, rows: dict, index: dict) -> bool:
    if not _copy_pair(anchor(old), anchor(owner)):
        return False
    keys = primary_keys(old)
    ids = set().union(*(index.get(k, set()) for k in keys))
    evidence = [anchor(rows[sid]) for sid in sorted(ids)]
    return all(_copy_pair(a, b) for i, a in enumerate(evidence) for b in evidence[i+1:])


def contributor_evidence(row: dict) -> list[dict]:
    """Keep every origin, including origins for which no stable ID is understood."""
    origins = [{"source": row.get("source"), "url": row.get("source_url"),
                "source_identifiers": [list(k) for k in sorted(primary_keys(row))]}]
    for source in row.get("sources") or []:
        if isinstance(source, dict):
            origins.append({"source": source.get("name"), "url": source.get("url"),
                            "source_identifiers": [list(k) for k in sorted(identity_keys({"source_url": source.get("url")}))]})
    for evidence in row.get("reference_inputs") or []:
        if isinstance(evidence, dict):
            origins.extend(evidence.get("contributors") or [{"source": evidence.get("source"),
                            "source_identifiers": evidence.get("source_identifiers", [])}])
    if (row.get("source_count") or 0) > len(row.get("sources") or []):
        origins.append({"source": "incomplete source list", "source_identifiers": []})
    unique = {json.dumps(o, sort_keys=True): o for o in origins}
    return [unique[k] for k in sorted(unique)]


def evidence_keys(value: Any) -> set[tuple]:
    if not isinstance(value, list) or not value or any(
        not isinstance(k, (list, tuple)) or len(k) not in (2, 3)
        or not all(isinstance(v, str) and v for v in k) for k in value
    ):
        raise ValueError("malformed reference source evidence")
    return {tuple(k) for k in value}


def _index(rows: list) -> dict:
    index = defaultdict(set)
    for row in rows:
        for key in identity_keys(row):
            index[key].add(row["id"])
    return index


def _unique(keys: set, index: dict, target: str) -> bool:
    # Never discard duplicate keys before checking the union (L2).
    return bool(keys) and all(index.get(k) == {target} for k in keys)


def _primary_transition(old: dict, target: dict, index: dict, previous_index: dict, payload: dict) -> list:
    """Prove primary continuity; account for every separate historical contribution."""
    keys = primary_keys(old)
    if not keys or keys != primary_keys(target):
        raise ValueError("current primary does not prove previous ownership")
    if not _unique(keys, previous_index, old['id']) or not _unique(keys, index, target['id']):
        raise ValueError("primary identity missing or split")
    if distance_m(old, target) > 250:
        raise ValueError("source moved beyond 250m")
    return _contribution_accounting(identity_keys(old), index, payload)


def _contribution_accounting(keys: set, index: dict, payload: dict) -> list:
    """Account for origins without making corroborators owners of an app reference."""
    try:
        from .source_rights import decision_for_key
    except ImportError:
        from source_rights import decision_for_key
    accounting = []
    for key in sorted(keys):
        targets = sorted(index.get(key, set()))
        if targets:
            accounting.append({'source_identifier': list(key), 'current_ids': targets})
        else:
            decision = decision_for_key(key, payload)
            if decision is None:
                # Only the independently proven primary carries this app ID. An
                # absent corroborating contribution is neither a current fact nor
                # a withdrawal of any app reference belonging to that contributor.
                accounting.append({'source_identifier': list(key), 'current_ids': [],
                                   'state': 'absent_corroboration'})
            else:
                accounting.append({'source_identifier': list(key), 'rights': decision})
    return accounting


def _canonical_error(old: dict, row: dict, index: dict, previous_index: dict, payload: dict) -> str | None:
    if primary_keys(old) and primary_keys(old) != primary_keys(row):
        return "canonical primary identity changed"
    if row.get('identity_reconciliation'):
        try:
            _primary_transition(old, row, index, previous_index, payload)
        except ValueError as exc:
            return str(exc)
    return None


def _alias_origin(alias: dict) -> dict:
    return {k: v for k, v in alias.items() if k != 'contributions'}


def _alias_error(alias: dict, target: dict, index: dict, baseline_rows=None, previous_index=None, prior_aliases=None, payload=None) -> str | None:
    try:
        if not isinstance(alias.get("id"), str) or not alias["id"] or alias["id"] == target["id"]:
            return "self or malformed alias"
        origins = evidence_keys(alias.get("source_identifiers"))
        primary = evidence_keys(alias.get("primary_identifiers"))
        if not primary <= origins:
            return "primary ownership is not bound to source evidence"
        # Legacy proof remains strict. New proof keeps immutable origins and
        # separately recomputes every contribution against this refresh's rows.
        source = primary if 'contributions' in alias else origins
        if 'contributions' in alias and alias['contributions'] != _contribution_accounting(origins, index, payload or {}):
            return "alias contribution accounting mismatch"
        owner = alias.get("owner")
        if not isinstance(owner, dict) or owner.get("id") != target["id"]:
            return "absent independent owner proof"
        if owner.get("source_identifiers") == []:
            return "independent owner lacks stable source identifier"
        owner_keys = evidence_keys(owner.get("source_identifiers"))
        copies = _copy_pair(alias, owner) and alias.get('primary_identifiers') == owner.get('source_identifiers') and owner.get('representation') == representation(target)
        if not copies and (source & owner_keys or owner.get("source") == alias.get("source")):
            return "owner is not independent"
        if not _unique(source, index, target["id"]) or not _unique(owner_keys, index, target["id"]):
            return "source or owner evidence missing or split"
        if not owner_keys <= primary_keys(target):
            return "independent owner identity changed"
        inputs = target.get("reference_inputs")
        if not isinstance(inputs, list) or not inputs:
            return "missing current merge anchors"
        for key in source | owner_keys:
            supports = [a for a in inputs if isinstance(a, dict) and list(key) in a.get("primary_identifiers", [])]
            if not supports or any(distance_m(a, target) > 250 for a in supports):
                return "current source or owner anchor missing or beyond 250m"
        if copies and not any(a.get('representation') == alias.get('representation') for a in inputs):
            return "current duplicate representation absent"
        if distance_m(alias, target) > 250 or distance_m(owner, target) > 250:
            return "source or owner moved beyond 250m"
        if baseline_rows is not None:
            old = baseline_rows.get(alias["id"])
            if old is not None:
                if {k: v for k, v in _alias_origin(alias).items() if k != "owner"} != anchor(old):
                    return "alias not bound to previous source anchor"
                prior_owner = baseline_rows.get(target["id"])
                if prior_owner is None or owner != _owner_anchor(prior_owner):
                    return "alias not bound to previous owner anchor"
                prior_target = alias["id"]
            else:
                prior_alias = (prior_aliases or {}).get(alias["id"])
                if prior_alias is None or _alias_origin(prior_alias) != _alias_origin(alias):
                    return "candidate-only alias metadata"
                prior_target = target["id"]
            unique = _unique(source, previous_index, prior_target) and _unique(owner_keys, previous_index, target["id"])
            if not unique and not (old is not None and copies and _previous_copies(old, prior_owner, baseline_rows, previous_index)):
                return "ambiguous previous source or owner evidence"
        return None
    except (ValueError, TypeError, KeyError):
        return "malformed alias proof"


@dataclass(frozen=True)
class ReferenceModel:
    revision: str
    canonical: frozenset[str]
    entries: tuple[dict, ...]

    def resolve(self, identifier: str) -> str | None:
        # Blocked entries take precedence, including malformed canonical conflicts.
        entry = next((e for e in self.entries if e["id"] == identifier), None)
        if entry:
            return entry.get("canonical_id") if entry["state"] == "alias" else None
        return identifier if identifier in self.canonical else None

    def public(self) -> dict:
        return {"version": VERSION, "revision": self.revision, "entries": [
            {"key": reference_key(e["id"]), "state": e["state"],
             **({"canonical_id": e["canonical_id"]} if e["state"] == "alias" else {})}
            for e in self.entries]}


def _revision(rows: list, entries: list) -> str:
    return hashlib.sha256(json.dumps({"records": rows, "entries": entries}, sort_keys=True,
                                     ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def validate_references(payload: dict, baseline: dict | None = None) -> ReferenceModel:
    """The only admitted reference interpretation. Raises for invalid persisted proof.

    Legacy datasets have canonical IDs only. Raw merge claims never resolve IDs.
    Reconciliation creates proof; every gate and runtime reader checks it again here.
    """
    if payload.get("reference_errors"):
        raise ValueError("reconciliation errors: " + str(payload["reference_errors"]))
    rows = payload.get("records") or []
    by_id = {r["id"]: r for r in rows}
    if len(by_id) != len(rows) or any(not isinstance(k, str) or not k for k in by_id):
        raise ValueError("duplicate or malformed canonical IDs")
    index = _index(rows)
    entries = []
    seen = set(by_id)
    baseline_rows = {r["id"]: r for r in (baseline or {}).get("records", [])}
    previous_index = _index(list(baseline_rows.values()))
    for sid, row in by_id.items():
        if sid in baseline_rows and distance_m(baseline_rows[sid], row) > 250:
            raise ValueError("station ID moved beyond 250m")
        if sid in baseline_rows:
            error = _canonical_error(baseline_rows[sid], row, index, previous_index, payload)
            if error:
                raise ValueError(error)
    prior_aliases = {}
    for r in baseline_rows.values():
        for a in r.get("identity_aliases", []):
            if not isinstance(a, dict) or a.get("id") in prior_aliases:
                raise ValueError("malformed or duplicate previous alias")
            prior_aliases[a.get("id")] = a
    claims = defaultdict(set)
    for row in rows:
        for sid in row.get('merged_ids') or []:
            claims[str(sid)].add(row['id'])
    for row in rows:
        for alias in row.get("identity_aliases") or []:
            if not isinstance(alias, dict):
                raise ValueError("malformed alias")
            error = _alias_error(alias, row, index, baseline_rows if baseline is not None else None, previous_index, prior_aliases, payload)
            sid = alias.get("id")
            if claims[sid] - {row['id']}:
                error = "competing merge claims"
            if error or sid in seen:
                raise ValueError(error or "canonical/alias conflict or duplicate alias owner")
            seen.add(sid)
            entries.append({"id": sid, "state": "alias", "canonical_id": row["id"]})
    document = payload.get("station_references")
    if document is not None:
        if not isinstance(document, dict) or document.get("version") != VERSION:
            raise ValueError("unsupported reference version")
        others = document.get("unavailable")
        if not isinstance(others, list) or len(others) > MAX_REFERENCES:
            raise ValueError("invalid reference catalogue")
        for entry in others:
            if not isinstance(entry, dict) or entry.get("state") not in {"withheld", "unresolved"}:
                raise ValueError("invalid unavailable reference")
            sid = entry.get("id")
            if not isinstance(sid, str) or not sid or sid in seen or "canonical_id" in entry:
                raise ValueError("unavailable reference ownership conflict")
            if baseline is not None:
                old = baseline_rows.get(sid)
                inherited_entry = next((e for e in ((baseline.get("station_references") or {}).get("unavailable") or []) if e.get("id") == sid), None)
                expected_anchor = anchor(old) if old else (inherited_entry or {}).get("anchor")
                if entry["state"] == "withheld" and (expected_anchor is None or entry.get("anchor") != expected_anchor):
                    raise ValueError("withdrawal not bound to previous reference")
            if entry["state"] == "withheld":
                try:
                    from .source_rights import validate_withdrawal
                except ImportError:
                    from source_rights import validate_withdrawal
                validate_withdrawal(entry, payload)
            elif not isinstance(entry.get("reason"), str) or not entry["reason"]:
                raise ValueError("missing unresolved reason")
            seen.add(sid)
            entries.append(deepcopy(entry))
    entries.sort(key=lambda e: e["id"])
    if len(entries) > MAX_REFERENCES:
        raise ValueError("reference catalogue exceeds bound")
    revision = _revision(rows, entries)
    if document is not None and document.get("revision") != revision:
        raise ValueError("reference revision mismatch")
    return ReferenceModel(revision, frozenset(by_id), tuple(entries))


def _owner_anchor(row: dict) -> dict:
    return {"id": row["id"], "source": row.get("source"), "lat": row.get("lat"), "lon": row.get("lon"),
            "source_identifiers": [list(k) for k in sorted(primary_keys(row))],
            **({'representation': representation(row)} if representation(row) else {})}


def preserve_record_ids(baseline: dict, candidate: dict) -> tuple[dict, dict]:
    result = deepcopy(candidate)
    previous = baseline.get("records") or []
    rows = result.get("records") or []
    old_by_id = {r["id"]: r for r in previous}
    current_ids = {r["id"] for r in rows}
    if len(old_by_id) != len(previous) or len(current_ids) != len(rows):
        raise ValueError("duplicate station IDs cannot be reconciled")
    for row in rows:
        if row["id"] in old_by_id and distance_m(old_by_id[row["id"]], row) > 250:
            raise ValueError(f"station ID moved beyond 250m: {row['id']}")
    inherited = {}
    for row in previous:
        for alias in row.get("identity_aliases") or []:
            if not isinstance(alias, dict) or not isinstance(alias.get("id"), str):
                raise ValueError("malformed canonical alias")
            if alias["id"] in inherited:
                raise ValueError("duplicate canonical alias owners")
            inherited[alias["id"]] = alias
    failures = {}
    claims = defaultdict(set)
    for row in rows:
        for sid in row.get("merged_ids") or []:
            claims[str(sid)].add(row["id"])
        for alias in row.get("identity_aliases") or []:
            sid = alias.get("id") if isinstance(alias, dict) else "<malformed>"
            if sid not in inherited or inherited[sid] != alias:
                failures[sid or "<malformed>"] = "candidate-only alias metadata"
        row.pop("identity_aliases", None)
        row.pop("merged_ids", None)
    result.pop("station_references", None)
    result.pop("reference_errors", None)
    index = _index(rows)
    previous_index = _index(previous)
    # A source's primary identity may survive while its former corroboration splits
    # into separate pins. These pins remain separate and explicitly accounted for.
    primary_proposals = []
    for old in previous:
        if old['id'] in current_ids:
            continue
        keys = primary_keys(old)
        matches = set().union(*(index.get(k, set()) for k in keys)) if keys else set()
        if len(matches) != 1:
            continue
        target = next(r for r in rows if r['id'] in matches)
        if target['id'] in old_by_id:
            continue
        try:
            accounting = _primary_transition(old, target, index, previous_index, result)
        except ValueError:
            continue
        primary_proposals.append((old, target, accounting))
    primary_uses = Counter(target['id'] for _, target, _ in primary_proposals)
    changes = []
    for old, target, accounting in primary_proposals:
        if primary_uses[target['id']] != 1 or claims[old['id']] - {target['id']}:
            continue
        generated = target['id']
        target['id'] = old['id']
        target['identity_reconciliation'] = {'generated_id': generated,
            'basis': 'unique previous and current primary identity within 250m',
            'source_identifiers': anchor(old)['source_identifiers'], 'contributions': accounting}
        changes.append({'preserved_id': old['id'], 'generated_id': generated,
                        'source_identifiers': anchor(old)['source_identifiers'], 'contributions': accounting})
    index = _index(rows)
    current_ids = {r['id'] for r in rows}
    renamed_rows = {r['id']: r for r in rows}
    for change in changes:
        sid = change['preserved_id']
        accounting = _primary_transition(old_by_id[sid], renamed_rows[sid], index, previous_index, result)
        change['contributions'] = accounting
        renamed_rows[sid]['identity_reconciliation']['contributions'] = accounting
    proposals = []
    for old in previous:
        if old["id"] in current_ids:
            continue
        keys = primary_keys(old)
        matches = set().union(*(index.get(k, set()) for k in keys)) if keys else set()
        if len(matches) != 1:
            failures[old["id"]] = "no admitted identity" if not matches else "multiple candidate pins"
            continue
        target_id = next(iter(matches))
        target = next(r for r in rows if r["id"] == target_id)
        copies = target_id in old_by_id and _previous_copies(old, old_by_id[target_id], old_by_id, previous_index)
        if (not _unique(keys, previous_index, old["id"]) and not copies) or not _unique(keys, index, target_id):
            failures[old["id"]] = "missing or split source evidence"
            continue
        if distance_m(old, target) > 250:
            failures[old["id"]] = "source moved beyond 250m"
            continue
        if claims[old["id"]] - {target_id}:
            failures[old["id"]] = "competing merge claims"
            continue
        proposals.append((old, target))
    index = _index(rows)
    by_id = {r["id"]: r for r in rows}
    merged_changes, retained = [], []
    for old, target in proposals:
        if target["id"] == old["id"]:
            continue
        if old['id'] in failures:
            continue
        prior_owner = old_by_id.get(target["id"])
        alias = anchor(old)
        alias["owner"] = _owner_anchor(prior_owner) if prior_owner else {}
        alias['contributions'] = _contribution_accounting(identity_keys(old), index, result)
        error = _alias_error(alias, target, index, old_by_id, previous_index, inherited, result)
        if error:
            failures[old["id"]] = error
            continue
        target.setdefault("identity_aliases", []).append(alias)
        merged_changes.append({"id": old["id"], "into": target["id"]})
        failures.pop(old["id"], None)
    for sid, alias in inherited.items():
        if sid in failures:
            continue
        alias = deepcopy(alias)
        try:
            alias['contributions'] = _contribution_accounting(evidence_keys(alias.get('source_identifiers')), index, result)
        except ValueError:
            failures[sid] = 'malformed alias proof'
            continue
        owner_id = (alias.get("owner") or {}).get("id")
        target = by_id.get(owner_id)
        error = _alias_error(alias, target, index, old_by_id, previous_index, inherited, result) if target else "independent owner absent"
        if sid in by_id or claims[sid] - {owner_id}:
            error = "canonical/alias conflict or competing claims"
        if error:
            failures[sid] = error
        else:
            target.setdefault("identity_aliases", []).append(deepcopy(alias))
            retained.append({"id": sid, "into": owner_id})
    for row in rows:
        if row.get("identity_aliases"):
            row["identity_aliases"].sort(key=lambda a: a["id"])
            row["merged_ids"] = [a["id"] for a in row["identity_aliases"]]
    # Reject claims on another surviving canonical, even after ID reassignment.
    for sid, owners in claims.items():
        if sid in by_id and owners - {sid}:
            # A generated owner renamed to this same canonical is not another pin.
            renamed = {c["generated_id"] for c in changes if c["preserved_id"] == sid}
            if owners - {sid} - renamed:
                failures[sid] = "canonical/alias conflict"
    unavailable = []
    prior_unavailable = (baseline.get("station_references") or {}).get("unavailable") or []
    missing_ids = (set(old_by_id) | {e["id"] for e in prior_unavailable}) - set(by_id)
    aliases = {a["id"] for r in rows for a in r.get("identity_aliases", [])}
    try:
        from .source_rights import withdrawal_for
    except ImportError:
        from source_rights import withdrawal_for
    for sid in sorted((missing_ids | set(inherited) | set(failures)) - aliases):
        if sid in by_id:
            continue  # conflicts still block via report; never emit duplicate catalogue keys
        old = old_by_id.get(sid) or next((e.get("anchor") for e in prior_unavailable if e["id"] == sid), None)
        withdrawn = withdrawal_for(old, result) if old else None
        if withdrawn:
            unavailable.append(withdrawn)
            failures.pop(sid, None)
        else:
            unavailable.append({"id": sid, "state": "unresolved", "reason": failures.get(sid, "reference not revalidated"),
                                **({"anchor": anchor(old) if "source_identifiers" not in old else old} if old else {})})
    entries = [{"id": a["id"], "state": "alias", "canonical_id": r["id"]}
               for r in rows for a in r.get("identity_aliases", [])] + unavailable
    entries.sort(key=lambda e: e["id"])
    result["station_references"] = {"version": VERSION, "unavailable": unavailable, "revision": _revision(rows, entries)}
    result["recordset_sha256"] = recordset_sha256(rows)
    for sid in set(old_by_id) & set(by_id):
        error = _canonical_error(old_by_id[sid], by_id[sid], index, previous_index, result)
        if error:
            failures[sid] = error
    canonical_errors = {sid: reason for sid, reason in failures.items() if sid in by_id}
    if canonical_errors:
        result["reference_errors"] = canonical_errors
    else:
        validate_references(result, baseline)
    unresolved = [e["id"] for e in unavailable if e["state"] == "unresolved"]
    report = {"preserved_ids": len(changes), "changes": changes, "merged_ids": len(merged_changes)+len(retained),
              "merged_changes": merged_changes, "retained_aliases": retained,
              "unresolved_alias_ids": sorted(set(failures) - missing_ids | (set(inherited) & set(unresolved))),
              "unresolved_missing_ids": sorted(set(unresolved) & missing_ids),
              "withheld_ids": [e["id"] for e in unavailable if e["state"] == "withheld"],
              "reasons": failures, "reference_revision": result["station_references"]["revision"]}
    return result, report
