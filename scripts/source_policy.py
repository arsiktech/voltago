#!/usr/bin/env python3
"""Shared fail-closed source-policy matching (Python standard library only)."""
from __future__ import annotations

import json
import hashlib
import re
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXCLUSIONS_PATH = ROOT / "data" / "source_policy_exclusions.json"


def normalize_policy_text(value: Any) -> str:
    """Normalize complete primary and nested evidence for policy matching."""
    text = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if text.isascii():
        # NFKD and the combining-mark filter are identities on ASCII, and
        # casefold() equals lower() there; skip the per-character walk.
        return text.lower()
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


def _canonical_family_pattern(exclusion: Dict[str, Any]) -> Optional["re.Pattern[str]"]:
    """Match the canonical family token without matching compound names.

    The longer source_family_terms remain the precise matcher. This additional
    boundary-aware check closes abbreviated structured labels such as
    ``Chargemap`` and ``Consum official`` while keeping independent families
    such as ``OpenChargeMap`` allowed.
    """
    canonical = normalize_policy_text(exclusion.get("family") or "").strip()
    token = canonical.split(maxsplit=1)[0] if canonical else ""
    return re.compile(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])") if token else None


class CompiledExclusion:
    __slots__ = ("id", "terms", "family_pattern", "hosts", "kinds")

    def __init__(self, exclusion: Dict[str, Any]) -> None:
        self.id = str(exclusion["id"])
        self.terms = tuple(normalize_policy_text(term) for term in exclusion.get("source_family_terms") or [])
        self.family_pattern = _canonical_family_pattern(exclusion)
        self.hosts = tuple({normalize_policy_text(host).strip(".") for host in exclusion.get("origin_hosts") or []})
        self.kinds = exclusion.get("permitted_price_evidence_kinds") or ()

    def family_matches_text(self, text: str) -> bool:
        return bool(self.family_pattern and self.family_pattern.search(text))

    def host_matches(self, host: str) -> bool:
        return any(host == allowed or host.endswith(f".{allowed}") for allowed in self.hosts)


class CompiledSourcePolicy:
    """Policy terms, family tokens and hosts normalized once instead of per row."""

    __slots__ = ("exclusions",)

    def __init__(self, policy: Dict[str, Any]) -> None:
        self.exclusions = tuple(CompiledExclusion(exclusion) for exclusion in policy.get("exclusions") or [])


PolicyLike = Union[Dict[str, Any], "CompiledSourcePolicy"]


def compile_source_policy(policy: PolicyLike) -> CompiledSourcePolicy:
    """Accept the loaded policy dict or an already compiled policy."""
    return policy if isinstance(policy, CompiledSourcePolicy) else CompiledSourcePolicy(policy)


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
    families, urls, claims = _record_origin_parts(record)
    for kind, claim_families, claim_urls in claims:
        if kind in permitted_price_evidence_kinds:
            continue
        families.extend(claim_families)
        urls.extend(claim_urls)
    return {
        "families": [value for value in families if value not in (None, "")],
        "urls": [value for value in urls if value not in (None, "")],
    }


def _record_origin_parts(
    record: Dict[str, Any],
) -> Tuple[List[Any], List[Any], List[Tuple[Any, List[Any], List[Any]]]]:
    """Collect provenance once per record.

    Typed price_evidence claims come back separately as (kind, families, urls)
    so each exclusion can apply its own permitted-kind exemption without
    walking the record again.
    """
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

    def collect_known_provenance(
        value: Any,
        *,
        provenance_object: bool = False,
        into: Optional[Tuple[List[Any], List[Any]]] = None,
    ) -> None:
        """Collect only source-shaped fields inside connector/photo evidence.

        Captions, labels, connector names, raw status text, and arbitrary station
        metadata are deliberately ignored. URL fields are safe to collect broadly
        inside these known evidence objects because matching later uses the parsed
        origin host, never a substring from the path or title.
        """
        target_families, target_urls = into or (families, urls)
        if isinstance(value, dict):
            for key, child in value.items():
                key_normalized = str(key).casefold()
                if key_normalized in {"source_url", "url"}:
                    target_urls.append(child)
                elif key_normalized in {"source_name", "source_type"}:
                    target_families.append(child)
                elif key_normalized == "source" and not isinstance(child, (dict, list)):
                    target_families.append(child)
                elif key_normalized == "name" and provenance_object:
                    target_families.append(child)
                elif isinstance(child, (dict, list)):
                    collect_known_provenance(
                        child,
                        provenance_object=provenance_object or key_normalized in provenance_container_keys,
                        into=(target_families, target_urls),
                    )
        elif isinstance(value, list):
            for child in value:
                collect_known_provenance(child, provenance_object=provenance_object, into=(target_families, target_urls))

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
    claims: List[Tuple[Any, List[Any], List[Any]]] = []
    price_evidence = record.get("price_evidence") or []
    if isinstance(price_evidence, list):
        for claim in price_evidence:
            if isinstance(claim, dict):
                claim_families: List[Any] = []
                claim_urls: List[Any] = []
                collect_known_provenance(claim, into=(claim_families, claim_urls))
                claims.append((claim.get("kind"), claim_families, claim_urls))
            else:
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
    return families, urls, claims


def source_family_is_excluded(value: Any, policy: PolicyLike) -> bool:
    """Match a source-family label from delta/canary history, not a station row."""
    text = normalize_policy_text(value)
    for exclusion in compile_source_policy(policy).exclusions:
        if exclusion.family_matches_text(text) or any(term and term in text for term in exclusion.terms):
            return True
    return False


def _present(values: Iterable[Any]) -> List[Any]:
    return [value for value in values if value not in (None, "")]


def _normalized_families(values: Iterable[Any]) -> List[str]:
    return [normalize_policy_text(value) for value in _present(values)]


def _origin_hosts(values: Iterable[Any]) -> List[str]:
    hosts: List[str] = []
    for raw_url in _present(values):
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
        hosts.append(normalize_policy_text(hostname).strip("."))
    return hosts


def matching_exclusion_ids(value: Any, policy: PolicyLike) -> List[str]:
    if not isinstance(value, dict):
        return []
    compiled = compile_source_policy(policy)
    families, urls, claims = _record_origin_parts(value)
    # Normalize each provenance value and parse each URL once per record; every
    # exclusion then only compares precomputed strings.
    base_texts = _normalized_families(families)
    base_hosts = _origin_hosts(urls)
    claim_evidence = [(kind, _normalized_families(claim_families), _origin_hosts(claim_urls))
                      for kind, claim_families, claim_urls in claims]
    matches: List[str] = []
    for exclusion in compiled.exclusions:
        texts = list(base_texts)
        hosts = list(base_hosts)
        # Only this exclusion's exact typed annotations may omit its origin check.
        for kind, claim_texts, claim_hosts in claim_evidence:
            if kind in exclusion.kinds:
                continue
            texts.extend(claim_texts)
            hosts.extend(claim_hosts)
        family_match = bool(exclusion.terms) and any(
            exclusion.family_matches_text(text) or any(term and term in text for term in exclusion.terms)
            for text in texts
        )
        url_match = any(exclusion.host_matches(host) for host in hosts)
        if family_match or url_match:
            matches.append(exclusion.id)
    return matches


def value_is_excluded(value: Any, policy: PolicyLike) -> bool:
    return bool(matching_exclusion_ids(value, policy))


def strip_blocked_reference_metadata(value: Any, policy: PolicyLike) -> Any:
    """Copy an allowed row while removing blocked non-authoritative Reference fields."""
    return _strip_blocked_reference_metadata(value, compile_source_policy(policy))


def _strip_blocked_reference_metadata(value: Any, compiled: CompiledSourcePolicy) -> Any:
    if isinstance(value, list):
        return [_strip_blocked_reference_metadata(item, compiled) if isinstance(item, (dict, list)) else item
                for item in value]
    if not isinstance(value, dict):
        return value
    cleaned: Dict[str, Any] = {}
    for key, child in value.items():
        if (
            str(key).casefold() == "reference"
            and (
                source_family_is_excluded(child, compiled)
                or bool(matching_exclusion_ids({"source": child, "source_url": child}, compiled))
            )
        ):
            continue
        # Scalars are returned unchanged by the recursion; skip the call.
        cleaned[key] = _strip_blocked_reference_metadata(child, compiled) if isinstance(child, (dict, list)) else child
    return cleaned


def partition_records(
    rows: Iterable[Dict[str, Any]], policy: PolicyLike
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    compiled = compile_source_policy(policy)
    allowed: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    for row in rows:
        if value_is_excluded(row, compiled):
            excluded.append(row)
        else:
            allowed.append(_strip_blocked_reference_metadata(row, compiled))
    return allowed, excluded
