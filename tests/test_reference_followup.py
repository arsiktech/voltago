"""Primary alias ownership and local OSM pricing failures, using isolated inputs."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import ingest_sources as ing
import station_identity as identity
from test_reference_migration import old_row, owner, merged


def primary_fixture():
    old = old_row()
    secondary = old_row('secondary', '18')
    old['sources'] = [{'name': secondary['source'], 'url': secondary['source_url']}]
    return {'records': [old, owner()]}, {'records': [merged(), secondary]}


class PrimaryAliasTests(unittest.TestCase):
    def test_primary_alias_accounts_for_separate_moving_and_absent_contributor(self):
        previous, fresh = primary_fixture()
        origin = None
        for cycle in range(3):
            current = deepcopy(fresh)
            if cycle == 1:
                current['records'][1]['lat'] += .001
            if cycle == 2:
                current['records'].pop()
            result, _ = identity.preserve_record_ids(previous, current)
            self.assertEqual(identity.validate_references(result, previous).resolve('old'), 'owner')
            alias = result['records'][0]['identity_aliases'][0]
            immutable = {k:v for k,v in alias.items() if k != 'contributions'}
            if origin is not None:
                self.assertEqual(origin, immutable)
            origin = immutable
            self.assertEqual(len(alias['contributions']), 2)
            forged = deepcopy(result)
            forged.pop('station_references')
            forged['records'][0]['identity_aliases'][0]['contributions'][-1]['current_ids'] = ['owner']
            with self.assertRaisesRegex(ValueError, 'accounting'):
                identity.validate_references(forged, previous)
            previous = result

    def test_missing_contributors_own_reference_is_not_transferred(self):
        previous, current = primary_fixture()
        previous['records'].append(current['records'].pop())
        result, report = identity.preserve_record_ids(previous, current)
        model = identity.validate_references(result, previous)
        self.assertEqual(model.resolve('old'), 'owner')
        self.assertIsNone(model.resolve('secondary'))
        self.assertIn('secondary', report['unresolved_missing_ids'])
        self.assertEqual(report['withheld_ids'], [])

    def test_primary_alias_negative_proofs(self):
        baseline, current = primary_fixture()
        valid, _ = identity.preserve_record_ids(baseline, current)
        self.assertEqual(identity.validate_references(valid).resolve('old'), 'owner')
        for inherited in (False, True):
            for case in ('missing_primary', 'duplicate_primary', 'moved_primary', 'missing_owner',
                         'owner_moved', 'competing_claim', 'canonical_conflict', 'unknown_primary'):
                prior, fresh = deepcopy(valid if inherited else baseline), deepcopy(current)
                target = fresh['records'][0]
                if case == 'missing_primary':
                    target['merged_source_identifiers'] = []
                elif case == 'duplicate_primary':
                    fresh['records'].append(old_row('duplicate'))
                elif case == 'moved_primary':
                    target['reference_inputs'][0]['lat'] += .01
                elif case == 'missing_owner':
                    target['reference_inputs'] = target['reference_inputs'][:1]
                elif case == 'owner_moved':
                    target['reference_inputs'][1]['lat'] += .01
                elif case == 'competing_claim':
                    fresh['records'][1]['merged_ids'] = ['old']
                elif case == 'canonical_conflict':
                    fresh['records'].append(old_row())
                else:
                    if inherited:
                        prior['records'][0]['identity_aliases'][0]['primary_identifiers'] = []
                    else:
                        prior['records'][0]['source_url'] = 'https://example.test/'
                with self.subTest(inherited=inherited, case=case):
                    result, report = identity.preserve_record_ids(prior, fresh)
                    if not result.get('reference_errors'):
                        self.assertNotEqual(identity.validate_references(result).resolve('old'), 'owner')
                    self.assertTrue(report['unresolved_missing_ids'] or report['unresolved_alias_ids'])

    def test_forged_accounting_is_blocked_in_reconciliation_and_shared_validation(self):
        baseline, current = primary_fixture()
        valid, _ = identity.preserve_record_ids(baseline, current)
        for mode in ('alter', 'drop'):
            forged = deepcopy(valid)
            forged.pop('station_references')
            alias = forged['records'][0]['identity_aliases'][0]
            if mode == 'alter':
                alias['contributions'][0]['current_ids'] = ['unknown']
            else:
                alias.pop('contributions')
            with self.subTest(mode=mode):
                with self.assertRaises(ValueError):
                    identity.validate_references(forged, valid)
                result, report = identity.preserve_record_ids(valid, forged)
                self.assertIsNone(identity.validate_references(result).resolve('old'))
                self.assertIn('old', report['unresolved_alias_ids'])

    def test_duplicate_previous_primary_and_original_anchor_tampering_are_blocked(self):
        baseline, current = primary_fixture()
        valid, _ = identity.preserve_record_ids(baseline, current)
        duplicate = deepcopy(baseline)
        duplicate['records'].append(old_row('duplicate'))
        with self.assertRaises(ValueError):
            identity.validate_references(valid, duplicate)
        for anchor in ('source', 'owner'):
            forged = deepcopy(valid)
            forged.pop('station_references')
            a = forged['records'][0]['identity_aliases'][0]
            (a if anchor == 'source' else a['owner'])['lat'] += .0001
            with self.subTest(anchor=anchor), self.assertRaises(ValueError):
                identity.validate_references(forged, baseline)


class OSMReviewIsolationTests(unittest.TestCase):
    def elements(self):
        return [{'type':'node', 'id':n, 'lat':39.0+n*.01, 'lon':-.5,
                 'tags':{'amenity':'charging_station', 'name':name, 'fee':'no',
                         'socket:type2':'2', 'socket:type2:output':power}}
                for n,name,power in [(1,'Before','22 kW'), (2,'Red MOVEM','22 kW'),
                                     (3,'After','22 kW'), (4,'Red MOVEM high power','50 kW')]]

    def collect(self, expired):
        records = []
        real_check = ing.assert_reference_review_current
        def check(stamp, days, **kw):
            return real_check(stamp, days, now=datetime(2026,10,13,1,tzinfo=timezone.utc) if expired
                              else datetime(2026,7,16,tzinfo=timezone.utc), **kw)
        error = None
        with mock.patch.object(ing, 'spain_tiles', return_value=[(1,2,3,4)]), \
             mock.patch.object(ing, 'osm_fetch_box', return_value=([(e,None) for e in self.elements()], [])), \
             mock.patch.object(ing, 'assert_reference_review_current', side_effect=check):
            try:
                ing.source_osm(records)
            except RuntimeError as exc:
                error = str(exc)
        return records, error

    def test_expired_row_is_held_without_truncating_inventory_and_failure_is_reported(self):
        rows, error = self.collect(True)
        self.assertEqual([r['raw']['osm_id'] for r in rows], [1,3])
        self.assertIn('Red MOVEM', error)
        self.assertIn('held', error)
        self.assertIn('/node/2', error)
        self.assertFalse(any((r.get('charge_claim') or {}).get('source') == 'Red MOVEM official public-price regulation' for r in rows))

    def test_valid_review_price_and_higher_power_exclusion_unchanged(self):
        rows, error = self.collect(False)
        self.assertIsNone(error)
        self.assertEqual([r['raw']['osm_id'] for r in rows], [1,2,3])
        self.assertEqual(rows[1]['price_min'], .25)
        self.assertEqual(rows[1]['charge_claim']['checked_at'], ing.RED_MOVEM_TARIFF_CHECKED_AT)


if __name__ == '__main__':
    unittest.main()
