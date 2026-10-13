"""Internal source-rights accounting. No station details in the public catalogue."""
from __future__ import annotations
import hashlib
import json
import re
try:
    from . import ocm_export
except ImportError:
    import ocm_export


def object_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def rights_decision(poi, provider, artifact, mode, commit):
    """Called only for actual rejected source rows after complete archive validation."""
    return {'source': 'openchargemap', 'poi_id': str(poi['ID']), 'provider_id': poi.get('DataProviderID'),
            'provider': provider, 'mode': mode, 'commit': commit,
            'artifact_sha256': artifact['sha256'], 'record_sha256': object_hash(poi),
            'reason': 'reuse_rights_unestablished'}


def _valid_decision(decision, payload):
    if not isinstance(decision, dict) or decision.get('source') != 'openchargemap' or decision.get('reason') != 'reuse_rights_unestablished':
        raise ValueError('unsupported withdrawal reason')
    if not isinstance(decision.get('poi_id'), str) or not decision['poi_id'].isdigit():
        raise ValueError('withdrawal lacks exact source ID')
    mode = decision.get('mode')
    snapshot = (payload.get('source_snapshots') or {}).get('openchargemap') or {}
    artifact = (snapshot.get('artifacts') or {}).get('archive' if mode == 'current' else 'legacy') or {}
    if mode not in {'current', 'legacy'} or decision.get('artifact_sha256') != artifact.get('sha256'):
        raise ValueError('withdrawal artifact mismatch')
    for field in ('artifact_sha256', 'record_sha256'):
        if not re.fullmatch('[0-9a-f]{64}', str(decision.get(field, ''))):
            raise ValueError('withdrawal lacks artifact binding')
    if decision.get('commit') != (snapshot.get('commit') if mode == 'current' else ocm_export.OCM_LEGACY_SHA):
        raise ValueError('withdrawal snapshot mismatch')
    provider = decision.get('provider')
    if not isinstance(provider, dict) or (provider and str(provider.get('ID')) != str(decision.get('provider_id'))):
        raise ValueError('withdrawal lacks provider metadata')
    if mode == 'current':
        grant = ocm_export.license_class(provider.get('License'), open_licensed=provider.get('IsOpenDataLicensed'))
    else:
        grant = ocm_export.legacy_provider_license({'DataProviderID': decision['provider_id'], 'DataProvider': provider})[0]
    if grant is not None:
        raise ValueError('permitted provider cannot be withdrawn for absent rights')


def decision_for_key(key, payload):
    """An exact denied contribution, not permission to withdraw a mixed reference."""
    if len(key) != 2 or key[0] != 'ocm-station':
        return None
    decisions = ((payload.get('source_snapshots') or {}).get('openchargemap') or {}).get('rights_withdrawals') or []
    matches = [d for d in decisions if d.get('poi_id') == key[1]]
    if len(matches) != 1:
        return None
    _valid_decision(matches[0], payload)
    return matches[0]


def withdrawal_for(old, payload):
    try:
        from .station_identity import anchor
    except ImportError:
        from station_identity import anchor
    evidence = old if 'source_identifiers' in old else anchor(old)
    # A mixed-source reference is not withdrawn just because one contributor lacks rights.
    keys = evidence.get('source_identifiers') or []
    if len(keys) != 1 or keys[0][0] != 'ocm-station':
        return None
    origins = evidence.get('contributors')
    if not origins or evidence.get('primary_identifiers') != keys or any(
        origin.get('source_identifiers') != keys for origin in origins
    ):
        return None
    decision = decision_for_key(keys[0], payload)
    if decision is None:
        return None
    return {'id': old['id'], 'state': 'withheld', 'reason': 'reuse_rights_unestablished',
            'anchor': evidence, 'rights': decision}


def validate_withdrawal(entry, payload):
    evidence = entry.get('anchor')
    if not isinstance(evidence, dict) or evidence.get('id') != entry.get('id'):
        raise ValueError('withdrawal lacks previous reference anchor')
    expected = withdrawal_for(evidence, payload)
    if expected != entry:
        raise ValueError('withdrawal is not derived from source metadata')


def verify_rights_artifacts(payload, cache):
    """Gate-time re-read of retained bytes; supplied metadata alone cannot grant retirement."""
    from pathlib import Path
    snapshot = (payload.get('source_snapshots') or {}).get('openchargemap') or {}
    decisions = snapshot.get('rights_withdrawals') or []
    if not decisions:
        return
    artifacts = snapshot.get('artifacts') or {}
    parsed = {}
    for mode in {d['mode'] for d in decisions}:
        artifact = artifacts['archive' if mode == 'current' else 'legacy']
        name = artifact.get('cache_name')
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError('invalid rights artifact cache name')
        path = Path(cache) / name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != artifact['sha256']:
            raise ValueError('rights artifact bytes changed')
        parsed[mode] = (ocm_export.read_export_archive(path, snapshot['commit']) if mode == 'current'
                        else ocm_export.read_legacy_es_rows(path))
    seen = set()
    for decision in decisions:
        _valid_decision(decision, payload)
        poi_id = int(decision['poi_id'])
        if poi_id in seen:
            raise ValueError('duplicate rights decision')
        seen.add(poi_id)
        if decision['mode'] == 'current':
            archive = parsed['current']
            poi = ocm_export.expand_poi(archive.pois[poi_id], archive.reference)
            provider = ocm_export._lookup(archive.reference.data_providers, poi.get('DataProviderID')) or {}
        else:
            poi = parsed['legacy'][poi_id]
            # License evidence uses the original provider grant, never the current substitute.
            provider = poi.get('DataProvider') or {}
        if provider != decision['provider'] or object_hash(poi) != decision['record_sha256']:
            # Submission resolution only adds non-rights fields to legacy rows; evidence hashes
            # are captured before that resolution by the adapter.
            raise ValueError('rights decision does not match retained source record')
