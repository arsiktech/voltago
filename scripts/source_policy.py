#!/usr/bin/env python3
"""Shared fail-closed source-policy matching (Python standard library only)."""
from __future__ import annotations

import json
import hashlib
import re
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXCLUSIONS_PATH = ROOT / "data" / "source_policy_exclusions.json"


def normalize_policy_text(value: Any) -> str:
    """Normalize complete primary and nested evidence for policy matching."""
    text = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def recordset_sha256(records: Iterable[Dict[str, Any]]) -> str:
    """Stable fingerprint of raw canonical station rows, independent of file order."""
    ordered = sorted(
        (record for record in records if isinstance(record, dict)),
        key=lambda record: str(record.get("id") or ""),
    )
    digest = hashlib.sha256(b"[")
    for index, record in enumerate(ordered):
        if index:
            digest.update(b",")
        digest.update(json.dumps(record, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")).encode("utf-8"))
    digest.update(b"]")
    return digest.hexdigest()


def _canonical_family_matches(value: Any, exclusion: Dict[str, Any]) -> bool:
    """Match the canonical family token without matching compound names.

    The longer source_family_terms remain the precise matcher. This additional
    boundary-aware check closes abbreviated structured labels such as
    ``Chargemap`` and ``Consum official`` while keeping independent families
    such as ``OpenChargeMap`` allowed.
    """
    text = normalize_policy_text(value)
    canonical = normalize_policy_text(exclusion.get("family") or "").strip()
    token = canonical.split(maxsplit=1)[0] if canonical else ""
    return bool(
        token
        and re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text)
    )


def load_source_policy_exclusions(path: Optional[Path] = None) -> Dict[str, Any]:
    policy_path = (path or DEFAULT_EXCLUSIONS_PATH).expanduser()
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    exclusions = payload.get("exclusions")
    if not isinstance(exclusions, list) or not exclusions:
        raise RuntimeError(f"source policy has no exclusions: {policy_path}")
    for exclusion in exclusions:
        if (
            not isinstance(exclusion, dict)
            or not exclusion.get("id")
            or not exclusion.get("source_family_terms")
            or not exclusion.get("origin_hosts")
        ):
            raise RuntimeError(f"invalid source policy exclusion in {policy_path}")
    return payload


def _record_origin_values(
    record: Dict[str, Any], *, permitted_price_evidence_kinds: Iterable[str] = ()
) -> Dict[str, List[Any]]:
    """Return provenance fields only, never station/operator/instruction text."""
    families: List[Any] = [record.get("source"), record.get("source_type")]
    urls: List[Any] = [record.get("source_url")]
    primary = record.get("primary_source")
    if isinstance(primary, dict):
        families.extend(primary.get(key) for key in ("name", "source_type"))
        urls.append(primary.get("url"))
    for source in record.get("sources") or []:
        if isinstance(source, dict):
            families.extend(source.get(key) for key in ("name", "source_type"))
            urls.append(source.get("url"))
    for claim_key in ("charge_claim", "parking_claim", "location_claim", "hardware_claim", "access_claim"):
        claim = record.get(claim_key)
        if isinstance(claim, dict):
            families.extend(claim.get(key) for key in ("source", "source_type"))
            urls.extend(claim.get(key) for key in ("source_url", "url"))

    provenance_container_keys = {"origin", "provenance", "source", "source_evidence", "source_ref"}

    def collect_known_provenance(value: Any, *, provenance_object: bool = False) -> None:
        """Collect only source-shaped fields inside connector/photo evidence.

        Captions, labels, connector names, raw status text, and arbitrary station
        metadata are deliberately ignored. URL fields are safe to collect broadly
        inside these known evidence objects because matching later uses the parsed
        origin host, never a substring from the path or title.
        """
        if isinstance(value, dict):
            for key, child in value.items():
                key_normalized = str(key).casefold()
                if key_normalized in {"source_url", "url"}:
                    urls.append(child)
                elif key_normalized in {"source_name", "source_type"}:
                    families.append(child)
                elif key_normalized == "source" and not isinstance(child, (dict, list)):
                    families.append(child)
                elif key_normalized == "name" and provenance_object:
                    families.append(child)
                elif isinstance(child, (dict, list)):
                    collect_known_provenance(
                        child,
                        provenance_object=provenance_object or key_normalized in provenance_container_keys,
                    )
        elif isinstance(value, list):
            for child in value:
                collect_known_provenance(child, provenance_object=provenance_object)

    # Connector/photo provenance commonly stores evidence outside sources[].
    collect_known_provenance(record.get("connectors") or [])
    collect_known_provenance(record.get("photos") or [])
    # Top-level pricing windows directly drive rendered price and tariff-window
    # claims, so any structured provenance they carry is source evidence too.
    collect_known_provenance(record.get("pricing") or [])
    # Only this exclusion's exact typed annotations may omit its origin check.
    # Other exclusions still inspect the entire annotation, including nested
    # provenance. A kind on primary, pricing, hardware or media evidence has
    # no effect. Malformed non-list price_evidence has no annotation exemption.
    price_evidence = record.get("price_evidence") or []
    if isinstance(price_evidence, list):
        for claim in price_evidence:
            if isinstance(claim, dict) and claim.get("kind") in permitted_price_evidence_kinds:
                continue
            collect_known_provenance(claim)
    else:
        collect_known_provenance(price_evidence)
    media = record.get("media")
    if isinstance(media, dict):
        collect_known_provenance(media.get("photos") or [])

    # Several reusable directories preserve upstream provenance inside their
    # raw source payload. Treat explicit source fields as evidence, while
    # leaving plain operator/name mentions alone. A deep raw website URL is
    # likewise station-level evidence; a bare provider homepage remains usable
    # as conventional owner metadata under the link-only policy.
    raw = record.get("raw")
    if isinstance(raw, dict):
        for key in ("source", "source_url"):
            value = raw.get(key)
            families.append(value)
            urls.append(value)
        for key in ("website", "url"):
            value = raw.get(key)
            if value in (None, ""):
                continue
            try:
                parsed = urllib.parse.urlsplit(str(value) if "://" in str(value) else f"//{value}")
            except ValueError:
                continue
            if parsed.hostname and (parsed.path not in ("", "/") or parsed.query or parsed.fragment):
                urls.append(value)
    return {
        "families": [value for value in families if value not in (None, "")],
        "urls": [value for value in urls if value not in (None, "")],
    }


def source_family_is_excluded(value: Any, policy: Dict[str, Any]) -> bool:
    """Match a source-family label from delta/canary history, not a station row."""
    text = normalize_policy_text(value)
    for exclusion in policy.get("exclusions") or []:
        if _canonical_family_matches(value, exclusion) or any(
            term and term in text
            for term in (normalize_policy_text(raw) for raw in exclusion.get("source_family_terms") or [])
        ):
            return True
    return False


def matching_exclusion_ids(value: Any, policy: Dict[str, Any]) -> List[str]:
    if not isinstance(value, dict):
        return []
    matches: List[str] = []
    for exclusion in policy.get("exclusions") or []:
        origin_values = _record_origin_values(
            value,
            permitted_price_evidence_kinds=exclusion.get("permitted_price_evidence_kinds") or (),
        )
        terms = [normalize_policy_text(term) for term in exclusion.get("source_family_terms") or []]
        family_match = any(
            _canonical_family_matches(origin, exclusion)
            or (term and term in normalize_policy_text(origin))
            for origin in origin_values["families"]
            for term in terms
        )
        allowed_hosts = {normalize_policy_text(host).strip(".") for host in exclusion.get("origin_hosts") or []}
        url_match = False
        for raw_url in origin_values["urls"]:
            url = str(raw_url or "").strip()
            if not url:
                continue
            try:
                parsed = urllib.parse.urlsplit(url if "://" in url else f"//{url}")
                hostname = parsed.hostname or ""
            except ValueError:
                # Provenance is untrusted input. An unusable URL cannot prove an
                # excluded origin, but it must not abort partitioning or prevent
                # other structured family/URL evidence from being evaluated.
                continue
            host = normalize_policy_text(hostname).strip(".")
            if any(host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts):
                url_match = True
                break
        if family_match or url_match:
            matches.append(str(exclusion["id"]))
    return matches


def value_is_excluded(value: Any, policy: Dict[str, Any]) -> bool:
    return bool(matching_exclusion_ids(value, policy))


def strip_blocked_reference_metadata(value: Any, policy: Dict[str, Any]) -> Any:
    """Copy an allowed row while removing blocked non-authoritative Reference fields."""
    if isinstance(value, list):
        return [strip_blocked_reference_metadata(item, policy) for item in value]
    if not isinstance(value, dict):
        return value
    cleaned: Dict[str, Any] = {}
    for key, child in value.items():
        if (
            str(key).casefold() == "reference"
            and (
                source_family_is_excluded(child, policy)
                or bool(matching_exclusion_ids({"source": child, "source_url": child}, policy))
            )
        ):
            continue
        cleaned[key] = strip_blocked_reference_metadata(child, policy)
    return cleaned


def partition_records(
    rows: Iterable[Dict[str, Any]], policy: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    allowed: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    for row in rows:
        if value_is_excluded(row, policy):
            excluded.append(row)
        else:
            allowed.append(strip_blocked_reference_metadata(row, policy))
    return allowed, excluded
