"""References keep their original identity through absent observations and merges."""
from copy import deepcopy
from itertools import permutations
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import station_identity as identity
import ingest_sources as ing
from test_reference_migration import old_row, owner, merged
import test_reference_migration as fixtures
from test_ingest_merge import rec


class ReferenceReactivationTests(unittest.TestCase):
    def histories(self):
        original = {'records': [old_row(), owner()]}
        absent, _ = identity.preserve_record_ids(original, {'records': [owner()]})
        _, denied = fixtures.SourceRightsTests().fixture()
        withheld, _ = identity.preserve_record_ids({'records': [old_row()]}, denied)
        aliased, _ = identity.preserve_record_ids(original, {'records': [merged()]})
        lost_alias, _ = identity.preserve_record_ids(aliased, {'records': [owner()]})
        return {'unresolved': absent, 'withheld': withheld, 'alias': aliased, 'lost_alias': lost_alias}

    def test_unrelated_same_id_never_reassigns_history_across_any_state(self):
        for state, baseline in self.histories().items():
            for station in (dict(old_row(poi='999999'), lat=51.0), old_row(poi='999999'), dict(old_row(), lat=51.0)):
                with self.subTest(state=state, row=station):
                    candidate = {'records': [station, owner()]}
                    # Reconciliation must flag the candidate; direct gate use must
                    # also reject it without trusting a reconciler's error report.
                    result, report = identity.preserve_record_ids(baseline, candidate)
                    self.assertIn('old', report['reasons'])
                    with self.assertRaises(ValueError):
                        identity.validate_references(result)
                    with self.assertRaises(ValueError):
                        identity.validate_references(candidate, baseline)

    def test_same_origin_can_return_as_canonical_with_current_facts(self):
        for state, baseline in self.histories().items():
            with self.subTest(state=state):
                result, report = identity.preserve_record_ids(baseline, {
                    'records': [dict(old_row(), lat=39.0001, price_min=.43), owner()]})
                self.assertNotIn('old', report['reasons'])
                self.assertEqual(identity.validate_references(result, baseline).resolve('old'), 'old')
                self.assertEqual(result['records'][0]['price_min'], .43)

    def test_returned_origin_must_be_unique_and_anchored(self):
        baseline = self.histories()['unresolved']
        duplicate = {'records': [old_row(), old_row('duplicate'), owner()]}
        result, _ = identity.preserve_record_ids(baseline, duplicate)
        with self.assertRaisesRegex(ValueError, 'split'):
            identity.validate_references(result)
        baseline['station_references']['unavailable'][0].pop('anchor')
        with self.assertRaisesRegex(ValueError, 'lacks previous reference anchor'):
            identity.validate_references({'records': [old_row(), owner()]}, baseline)

    def test_missing_alias_keeps_origin_and_can_return_only_to_original_owner(self):
        histories = self.histories()
        baseline = histories['lost_alias']
        origin = histories['alias']['records'][0]['identity_aliases'][0]
        self.assertEqual(baseline['station_references']['unavailable'][0]['anchor'],
                         {k: v for k, v in origin.items() if k != 'contributions'})
        result, report = identity.preserve_record_ids(baseline, {'records': [merged()]})
        self.assertEqual(report['unresolved_alias_ids'], [])
        self.assertEqual(identity.validate_references(result, baseline).resolve('old'), 'owner')
        moved = merged()
        moved['lat'] += .02
        with self.assertRaises(ValueError):
            identity.preserve_record_ids(baseline, {'records': [moved]})

    def test_unavailable_anchor_cannot_be_rewritten_then_reactivated(self):
        baseline = self.histories()['unresolved']
        forged = deepcopy(baseline)
        forged.pop('station_references')
        forged['station_references'] = {
            'version': 1, 'unavailable': [{'id': 'old', 'state': 'unresolved', 'reason': 'absent',
                                         'anchor': identity.anchor(dict(old_row(poi='999999'), lat=51.0))}]}
        entries = forged['station_references']['unavailable']
        forged['station_references']['revision'] = identity._revision(forged['records'], entries)
        with self.assertRaisesRegex(ValueError, 'previous anchor'):
            identity.validate_references(forged, baseline)

    def test_unavailable_or_alias_reference_cannot_be_silently_forgotten(self):
        for state, baseline in self.histories().items():
            with self.subTest(state=state), self.assertRaisesRegex(ValueError, 'disappeared'):
                identity.validate_references({'records': [owner()]}, baseline)


class OperatorContributorTests(unittest.TestCase):
    def rows(self):
        return [rec('tesla', 'OpenStreetMap public tags', 'https://www.openstreetmap.org/node/1',
                    39., -.5, raw={'operator': 'Tesla'}),
                rec('mixed', 'OpenChargeMap public snapshot', 'https://map.openchargemap.io/?mode=details&id=2',
                    39.00001, -.5, raw={'operator': 'Tesla; Ionity'}),
                rec('ionity', 'DGT National Access Point charging registry', 'https://example.test/dgt',
                    39.00002, -.5, raw={'operator': 'Ionity', 'nap_site_id': 'i'})]

    def assert_separate(self, rows):
        for row in rows:
            origins = {e['id'] for e in row.get('reference_inputs') or []} | {row['id']}
            self.assertFalse({'tesla', 'ionity'} <= origins)

    def test_mixed_operator_bridge_cannot_join_incompatible_contributors(self):
        for order in permutations(self.rows()):
            for stage in ('nearby', 'exact', 'both'):
                with self.subTest(order=[r['id'] for r in order], stage=stage):
                    rows = deepcopy(list(order))
                    if stage in {'exact', 'both'}:
                        rows = ing.dedupe(rows)
                    if stage in {'nearby', 'both'}:
                        rows = ing.merge_cross_source_duplicates(rows)[0]
                    self.assertEqual(len(rows), 2)
                    self.assert_separate(rows)

    def test_earlier_merge_evidence_cannot_be_hidden_by_mixed_primary(self):
        tesla, mixed, ionity = self.rows()
        mixed['reference_inputs'] = [dict(identity.anchor(tesla), operators=['tesla']),
                                     dict(identity.anchor(mixed), operators=['tesla', 'ionity'])]
        self.assertTrue(ing.operator_conflict(mixed, ionity))
        self.assertEqual(len(ing.dedupe([mixed, ionity])), 2)
        self.assertEqual(len(ing.merge_cross_source_duplicates([mixed, ionity])[0]), 2)

    def test_repeated_page_does_not_override_operator_conflict(self):
        tesla, _, ionity = self.rows()
        for number, row in enumerate((tesla, ionity), 1):
            row.update(source='Canarias te recarga public map',
                       source_url='https://canariasterecarga.com/es/fixture/')
            row['raw'].update(detail_url=row['source_url'], source_record_id=str(number), source_map_id='one')
        self.assertEqual(len(ing.dedupe([tesla, ionity])), 2)


if __name__ == '__main__':
    unittest.main()
