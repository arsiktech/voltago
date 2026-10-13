"""Successive reference refreshes through real delta, canary, reports and publication.

Only the source collection stage is replaced with synthetic retained inputs.
All filesystem operations use an isolated fixture tree.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import runtime_refresh as runtime
import station_identity as identity
from test_reference_migration import old_row, owner
import test_reference_migration as fixtures


def exercise_refreshes(base, *, primary_contributions=False):
    root, state = base/'repo', base/'runtime'
    root.mkdir(parents=True)
    (root/'scripts').symlink_to(ROOT/'scripts', target_is_directory=True)
    for name, relative in runtime.BUNDLE.items():
        path = root/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('' if name.endswith('.jsonl') else '{}')
    (root/'data/source_policy_exclusions.json').write_bytes((ROOT/'data/source_policy_exclusions.json').read_bytes())
    cache = root/'data/cache'
    cache.mkdir()
    content, rights = fixtures.SourceRightsTests().fixture()
    (cache/'legacy.json.gz').write_bytes(content)

    def complete(row):
        row = deepcopy(row)
        row.update(name='Reference fixture', reliability='community-price-unknown', parking={'kind':'unknown'})
        row['source_url'] = row.get('source_url') or 'https://example.test/registry'
        row['sources'] = [{'name': row['source'], 'url': row['source_url']}]
        return row
    canonical = complete(owner())
    absorbed = complete(old_row('alias', '18'))
    corroborator = {'name':'OpenStreetMap public tags', 'url':'https://www.openstreetmap.org/node/999'}
    if primary_contributions:
        absorbed['sources'].append(corroborator)
    withdrawn = complete(old_row('withdrawn', '17'))
    keep = [complete(old_row('keep-'+str(i), str(i+100))) for i in range(25)]
    baseline = {'records': [canonical, absorbed, withdrawn, *keep]}
    (root/'data/free_intelligence.json').write_text(json.dumps(baseline))
    current = dict(canonical, sources=canonical['sources']+absorbed['sources'][:1],
                   reference_inputs=[identity.anchor(canonical), identity.anchor(absorbed)])
    fresh = dict(rights, records=[current, *keep], generated_at='2026-01-01T00:00:00Z', errors=[])
    original_stage = runtime.run_stage
    commands = []
    reject = False

    def stage(repo, run, name, argv, timeout):
        commands.append({'stage':name, 'argv':argv, 'run':str(run)})
        if name == 'build':
            candidate = deepcopy(fresh)
            if reject == 'missing-proof':
                candidate['records'][0].pop('reference_inputs')
            if primary_contributions and cycle < 3:
                # A former corroborator moves to a different independently owned
                # pin, then disappears. Neither pin's own primary changes.
                candidate['records'][cycle]['sources'].append(corroborator)
            (run/'free_intelligence.json').write_text(json.dumps(candidate))
            return subprocess.CompletedProcess(argv, 0, '{}', '')
        if reject == 'accounting' and name == 'delta':
            path = run/'free_intelligence.json'
            candidate = json.loads(path.read_text())
            candidate['records'][0]['identity_aliases'][0]['contributions'][-1]['current_ids'] = ['forged-owner']
            candidate.pop('station_references')
            path.write_text(json.dumps(candidate))
        return original_stage(repo, run, name, [*argv, '--reference-cache', str(cache)], timeout)

    source = {'synthetic_collection':True, 'sha256': {
        str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (ROOT/'scripts').glob('*.py')}}
    checks = []
    with mock.patch.object(runtime, 'source_identity', return_value=source), mock.patch.object(runtime, 'run_stage', side_effect=stage):
        for cycle in (1, 2, 3):
            code = runtime.refresh(root, state, 30)
            receipt = json.loads((state/'last.json').read_text())
            assert code == 0, receipt
            assert receipt['promoted'] is True and receipt['delta']['ok'] is True and receipt['canary']['ok'] is True
            payload = json.loads((state/'current/free_intelligence.json').read_text())
            model = identity.validate_references(payload, baseline)
            assert model.resolve('alias') == 'owner'
            assert next(e for e in model.entries if e['id']=='withdrawn')['state'] == 'withheld'
            alias = payload['records'][0]['identity_aliases'][0]
            if primary_contributions:
                contribution = next(c for c in alias['contributions'] if c['source_identifier'][0]=='osm')
                assert contribution['current_ids'] == (['keep-'+str(cycle-1)] if cycle < 3 else [])
                if cycle > 1:
                    old_alias = baseline['records'][0]['identity_aliases'][0]
                    assert identity._alias_origin(alias) == identity._alias_origin(old_alias)
            checks.append({'cycle':cycle, 'exitcode':code, 'run_id':receipt['run_id'], 'aliases':1, 'withheld':1,
                           'delta':receipt['delta']['delta'], 'promoted_synthetic_bundle':receipt['promoted']})
            baseline = payload
        previous = (state/'current').resolve()
        reject = 'missing-proof'
        code = runtime.refresh(root, state, 30)
        receipt = json.loads((state/'last.json').read_text())
        assert code == 1 and receipt['promoted'] is False and (state/'current').resolve() == previous
        checks.append({'cycle':'missing-current-proof', 'exitcode':code, 'run_id':receipt['run_id'],
                       'expected_rejection':True, 'previous_bundle_unchanged':True, 'error':receipt['error']})
        if primary_contributions:
            reject = 'accounting'
            code = runtime.refresh(root, state, 30)
            receipt = json.loads((state/'last.json').read_text())
            assert code == 1 and not receipt['promoted'] and (state/'current').resolve() == previous
            run = state/'runs'/receipt['run_id']
            assert 'accounting mismatch' in (run/'delta.stdout.log').read_text()
            argv = [sys.executable, str(ROOT/'scripts/qa_ingestion_canary.py'), '--candidate', str(run/'free_intelligence.json'),
                    '--baseline', str(state/'current/free_intelligence.json'), '--reference-cache', str(cache),
                    '--history-out', str(run/'negative-canary-history.jsonl')]
            check = subprocess.run(argv, cwd=root, capture_output=True, text=True)
            (run/'negative-canary.stdout.log').write_text(check.stdout)
            (run/'negative-canary.stderr.log').write_text(check.stderr)
            commands.append({'stage':'tampered-canary','argv':argv,'exitcode':check.returncode})
            assert check.returncode == 1 and 'accounting mismatch' in check.stdout
            checks.append({'cycle':'forged-accounting','exitcode':code,'canary_exitcode':check.returncode,
                           'expected_rejection':True,'previous_bundle_unchanged':True,'error':receipt['error']})
    (base/'commands.json').write_text(json.dumps(commands, indent=2))
    (base/'checks.json').write_text(json.dumps(checks, indent=2))
    return checks


class RealRefreshGateTests(unittest.TestCase):
    def test_primary_contributions_move_and_disappear_then_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            checks = exercise_refreshes(Path(tmp), primary_contributions=True)
        self.assertEqual(len(checks), 5)

    def test_three_successful_refreshes_and_rejected_missing_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            checks = exercise_refreshes(Path(tmp))
        self.assertEqual(len(checks), 4)


if __name__ == '__main__':
    unittest.main()
