"""Review reproductions using isolated synthetic history and retained source shapes."""
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import station_identity as identity
import qa_ingestion_delta as delta
from test_reference_migration import old_row, owner, merged
import test_reference_migration as fixtures
from test_ingest_merge import rec


def current_merge():
    return dict(merged(), reference_inputs=[identity.anchor(old_row()), identity.anchor(owner())])


class ReviewReproductions(unittest.TestCase):
    def test_L1_missing_current_anchors_reject_first_and_inherited_alias(self):
        baseline = {'records': [old_row(), owner()]}
        first, _ = identity.preserve_record_ids(baseline, {'records': [current_merge()]})
        for prior in (baseline, first):
            with self.subTest(inherited=prior is first):
                incomplete = merged()
                incomplete.pop('reference_inputs', None)
                candidate, report = identity.preserve_record_ids(prior, {'records': [incomplete]})
                self.assertIsNone(identity.validate_references(candidate).resolve('old'))
                self.assertIn('old', report['reasons'])

    def test_L2_forged_duplicate_previous_identity_rejected_by_shared_gate(self):
        first, _ = identity.preserve_record_ids({'records': [old_row(), owner()]}, {'records': [current_merge()]})
        baseline = {'records': [old_row(), old_row('duplicate'), owner()]}
        with self.assertRaises(ValueError):
            identity.validate_references({'records': first['records']}, baseline)
        self.assertTrue(delta.id_delta(baseline['records'], first['records'])['reference_errors'])

    def test_L3_real_delta_keeps_previous_withdrawal_state(self):
        content, fresh = fixtures.SourceRightsTests().fixture()
        first, _ = identity.preserve_record_ids({'records': [old_row()]}, fresh)
        second, _ = identity.preserve_record_ids(first, fresh)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'legacy.json.gz').write_bytes(content)
            for name, payload in [('before', first), ('after', second)]:
                (root / name).write_text(json.dumps(payload))
            output = io.StringIO()
            with mock.patch.object(sys, 'argv', ['delta', '--baseline', str(root/'before'), '--rebuilt', str(root/'after'), '--reference-cache', tmp]), contextlib.redirect_stdout(output):
                delta.main()
            self.assertEqual(json.loads(output.getvalue())['delta']['reference_errors'], [])

    def test_L4_unknown_municipal_contributor_blocks_wholesale_withdrawal(self):
        from source_rights import withdrawal_for
        _, fresh = fixtures.SourceRightsTests().fixture()
        mixed = old_row()
        mixed.update(source='Barcelona Endolla public map', source_url='https://endolla.barcelona/',
                     raw={'endolla_location_id': '3738'}, sources=[{'name': 'OpenChargeMap public snapshot', 'url': old_row()['source_url']}])
        self.assertIsNone(withdrawal_for(mixed, fresh))

    def test_L5_cold_live_cache_preserves_active_history_and_photos(self):
        import server
        store = server.community_store
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(store, '_DB_PATH', Path(tmp)/'synthetic.db'), \
             mock.patch.object(store, '_PHOTOS_DIR', Path(tmp)/'photos'), \
             mock.patch.object(server, 'load_intelligence_meta', return_value={'records': []}), \
             mock.patch.object(server, 'known_station_ids', return_value=set()):
            store._invalidate_summaries()
            store.record_checkin('historical-live', 'failed', known_station=lambda sid: True)
            photo = store.save_photo('historical-live', b'\xff\xd8\xff'+b'x'*32, known_station=lambda sid: True)
            handler = object.__new__(server.Handler)
            handler.path = '/api/community/summary?ids=historical-live,unknown'
            responses = []
            handler.send_json = lambda status, payload, **kw: responses.append((status, payload))
            handler._route_get()
            with self.subTest(route='summary'):
                self.assertEqual(set(responses[0][1]['stations']), {'historical-live'})
            handler.path = photo['url']
            handler.wfile = io.BytesIO()
            handler.send_response = lambda status: responses.append((status, {}))
            handler.send_header = lambda *args: None
            handler.end_headers = lambda: None
            handler._route_get()
            with self.subTest(route='photo'):
                self.assertEqual(responses[-1][0], 200)
            store.moderate('photo', photo['id'], 'remove')
            handler._route_get()
            self.assertEqual(responses[-1][0], 404)
            store._invalidate_summaries()

    def test_L6_conflicting_charging_operators_never_merge(self):
        import ingest_sources as ing
        a = rec('registry', 'DGT National Access Point charging registry', 'https://example.test/dgt', 39., -.5,
                name='(ZUNDER) Grupo Easychargar SA — Hotel Restaurante', raw={'operator':'(ZUNDER) Grupo Easychargar SA', 'nap_site_id':'registry-1', 'corroboration_only':True})
        b = rec('osm', 'OpenStreetMap public tags', 'https://www.openstreetmap.org/node/123', 39.00001, -.5,
                raw={'operator':'Tesla'}, name='Hotel Restaurante')
        with self.subTest(stage='proximity'):
            self.assertEqual(len(ing.merge_cross_source_duplicates([a, b])[0]), 2)
        with self.subTest(stage='coordinates'):
            self.assertEqual(len(ing.dedupe([a, b])), 2)


class ProvenanceRepairTests(unittest.TestCase):
    def test_absent_corroboration_does_not_transfer_or_withdraw_its_own_reference(self):
        primary = old_row('primary', '18')
        secondary = old_row('secondary', '19')
        primary['sources'] = [{'url': secondary['source_url']}]
        baseline = {'records': [primary, secondary]}
        candidate = {'records': [old_row('generated', '18')]}
        result, report = identity.preserve_record_ids(baseline, candidate)
        model = identity.validate_references(result, baseline)
        self.assertEqual(model.resolve('primary'), 'primary')
        self.assertIsNone(model.resolve('secondary'))
        self.assertEqual(report['withheld_ids'], [])
        self.assertEqual(report['unresolved_missing_ids'], ['secondary'])
        self.assertEqual(delta.id_delta(baseline['records'], result['records'], result, baseline)['missing_ids'], 1)
        contribution = report['changes'][0]['contributions'][1]
        self.assertEqual(contribution, {'source_identifier': ['ocm-station','19'], 'current_ids': [], 'state':'absent_corroboration'})
        # The same absence cannot stand in for a missing primary.
        result, report = identity.preserve_record_ids(baseline, {'records': [old_row('generated', '19')]})
        self.assertIsNone(identity.validate_references(result, baseline).resolve('primary'))
        self.assertIn('primary', report['unresolved_missing_ids'])

    def test_current_source_and_owner_anchors_are_both_mandatory(self):
        baseline = {'records': [old_row(), owner()]}
        valid, _ = identity.preserve_record_ids(baseline, {'records': [current_merge()]})
        for previous in (baseline, valid):
            for missing in ('old', 'owner'):
                current = current_merge()
                current['reference_inputs'] = [a for a in current['reference_inputs'] if a['id'] != missing]
                result, _ = identity.preserve_record_ids(previous, {'records': [current]})
                self.assertIsNone(identity.validate_references(result).resolve('old'))
                bad = deepcopy(valid['records'][0])
                bad['reference_inputs'] = current['reference_inputs']
                with self.assertRaises(ValueError):
                    identity.validate_references({'records': [bad]}, previous)

    def test_municipal_ids_are_typed_and_do_not_cross_sources(self):
        for source, raw, count in [
            ('Barcelona Endolla open data', {'endolla_location_id': '3738'}, 1),
            ('València OpenData', {'items': [{'objectid': 15}, {'objectid': 16}, {'objectid': True}]}, 2),
            ('Zaragoza municipal open data', {'zaragoza_id': 'MU1_puntos_recarga.42'}, 1),
        ]:
            self.assertEqual(len(identity.primary_keys({'source': source, 'raw': raw})), count)
            self.assertFalse(identity.primary_keys({'source': 'unrelated', 'raw': raw}))

    def test_generated_owner_requires_unique_matching_previous_primary(self):
        primary = old_row('prior-owner', '18')
        absorbed = dict(old_row('absorbed'), source='OpenStreetMap public tags', source_url='https://www.openstreetmap.org/node/123')
        current = dict(primary, id='generated', sources=[{'url': absorbed['source_url']}],
                       reference_inputs=[identity.anchor(primary), identity.anchor(absorbed)])
        baseline = {'records': [primary, absorbed]}
        result, report = identity.preserve_record_ids(baseline, {'records': [current]})
        self.assertEqual(identity.validate_references(result, baseline).resolve('absorbed'), 'prior-owner')
        self.assertFalse(report['unresolved_missing_ids'])
        # Removing the previous primary anchor cannot turn the absorbed row into the owner.
        result, report = identity.preserve_record_ids({'records': [absorbed]}, {'records': [current]})
        self.assertEqual(report['unresolved_missing_ids'], ['absorbed'])

    def test_mixed_rights_account_for_contributor_without_reassigning_denied_primary(self):
        from source_rights import withdrawal_for
        _, fresh = fixtures.SourceRightsTests().fixture()
        for primary, contributor, succeeds in [('18', '17', True), ('17', '18', False)]:
            old = old_row('saved', primary)
            old['sources'] = [{'url': old_row(poi=contributor)['source_url']}]
            current = old_row('current', '18')
            candidate = dict(fresh, records=[current])
            result, report = identity.preserve_record_ids({'records': [old]}, candidate)
            self.assertIsNone(withdrawal_for(old, candidate))
            self.assertEqual(identity.validate_references(result).resolve('saved') == 'saved', succeeds)
            if succeeds:
                self.assertEqual(report['changes'][0]['contributions'][0]['rights']['poi_id'], '17')
            else:
                self.assertEqual(report['withheld_ids'], [])
                self.assertEqual(report['unresolved_missing_ids'], ['saved'])

    def test_proven_page_copies_share_reference_without_absorbing_neighbor(self):
        import ingest_sources as ing
        def page(sid, marker, lat):
            url = 'https://canariasterecarga.com/es/p12345'
            return rec(sid, 'Canarias te recarga public map', url, lat, -.5,
                       raw={'source_record_id': str(marker), 'source_map_id': '4', 'detail_url': url})
        a, b = page('first', 261, 39.), page('second', 269, 39.00004)
        fresh = ing.dedupe([b, a])
        self.assertEqual(len(fresh), 1)
        self.assertEqual(fresh[0]['lat'], a['lat'])
        neighbor = rec('neighbor', 'DGT National Access Point charging registry', 'https://example.test', 39.0001, -.5, raw={'nap_site_id':'another'})
        self.assertEqual(len(ing.merge_cross_source_duplicates(fresh+[neighbor])[0]), 2)
        baseline = {'records': [a, b]}
        for _ in range(3):
            result, report = identity.preserve_record_ids(baseline, {'records': fresh})
            self.assertEqual(identity.validate_references(result, baseline).resolve('second'), 'first')
            self.assertEqual(delta.id_delta(baseline['records'], result['records'], result, baseline)['missing_ids'], 0)
            self.assertFalse(report['unresolved_missing_ids'])
            baseline = result
        forged_previous = {'records': [a, dict(b, raw={})]}
        with self.assertRaises(ValueError):
            identity.validate_references({'records': result['records']}, forged_previous)

    def test_current_postcodes_are_not_quarantined_by_historical_conflicts(self):
        import ingest_sources as ing
        current = rec('current', 'OpenChargeMap public snapshot', old_row()['source_url'], 39., -.5,
                      raw={'ID': 17, 'addr:postcode':'41702', 'historical_snapshot':False})
        historical = dict(current, id='historical', raw={'ID':18, 'addr:postcode':'41703', 'historical_snapshot':True})
        self.assertEqual(ing.reject_suspicious_openchargemap_coordinate_clusters([historical, current]), [current])
        conflicting_current = dict(historical, raw=dict(historical['raw'], historical_snapshot=False))
        self.assertEqual(ing.reject_suspicious_openchargemap_coordinate_clusters([conflicting_current, current]), [])

    def test_operator_aliases_venue_owners_and_transitive_merges(self):
        import ingest_sources as ing
        a = rec('a', 'DGT National Access Point charging registry', 'https://example.test/a', 39., -.5, raw={'operator':'(ZUNDER) Grupo Easychargar SA'})
        b = rec('b', 'OpenStreetMap public tags', 'https://www.openstreetmap.org/node/123', 39.00002, -.5, raw={'operator':'Tesla'})
        bridge = rec('c', 'OpenChargeMap public snapshot', old_row()['source_url'], 39.00001, -.5)
        self.assertEqual(len(ing.merge_cross_source_duplicates([a, bridge, b])[0]), 2)
        self.assertFalse(ing.operator_conflict(a, dict(b, raw={'operator':'Easycharger'})))
        self.assertFalse(ing.operator_conflict(a, dict(b, raw={'owner':'Tesla'})))
        self.assertTrue(ing.operator_conflict(a, dict(b, raw={'operator':'Hotel Fixture','brand':'Tesla'})))
