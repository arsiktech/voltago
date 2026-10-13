import json
from pathlib import Path
import sys
import unittest
from copy import deepcopy
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from station_identity import preserve_record_ids, anchor


def station(identifier, url='https://www.openstreetmap.org/node/123', **changes):
    return {'id':identifier,'name':'Fixture','source':'OpenStreetMap public tags','lat':39.0,'lon':-0.5,
            'source_url':url,'sources':[{'name':'OpenStreetMap public tags','url':url}], 'price_min':0.2, **changes}


class StableStationIdTests(unittest.TestCase):
    def reconcile(self, old, new):
        return preserve_record_ids({'records':old}, {'records':new,'total_records':len(new)})

    def test_shared_station_reference_preserves_id_not_old_fields(self):
        old, new = station('old'), station('new', name='Updated source name', price_min=0.3, status='VERIFY')
        original = deepcopy(new)
        payload, report = self.reconcile([old],[new])
        self.assertEqual(report['preserved_ids'],1)
        self.assertEqual(payload['records'][0]['id'],'old')
        self.assertEqual(payload['records'][0]['price_min'],0.3)
        self.assertEqual(payload['records'][0]['status'],'VERIFY')
        self.assertEqual(new,original)

    def test_generic_map_url_and_nearby_name_never_match(self):
        old = station('old','https://www.mapareve.es/mapa-puntos-recarga')
        new = station('new','https://www.mapareve.es/mapa-puntos-recarga')
        _, report = self.reconcile([old],[new])
        self.assertEqual(report['unresolved_missing_ids'],['old'])

    def test_typed_source_id_matches_coordinate_correction(self):
        old = station('old', '', source='MapaREVE public OCPI snapshot', raw={'location_id':'exact-id'}, sources=[])
        new = {**old,'id':'new','lat':39.001}
        payload, report = self.reconcile([old],[new])
        self.assertEqual(report['preserved_ids'],1)
        self.assertEqual(payload['records'][0]['lat'],39.001)

    def test_same_raw_id_from_different_source_family_does_not_match(self):
        old=station('old','',source='MapaREVE public OCPI snapshot',raw={'location_id':'exact-id'},sources=[])
        new={**old,'id':'new','source':'Unrelated source'}
        _, report=self.reconcile([old],[new])
        self.assertEqual(report['preserved_ids'],0)

    def test_lidl_query_survives_geocoder_change(self):
        old=station('old','',source='Lidl official free list',raw={'official_query':'Exact official address'},sources=[])
        payload,report=self.reconcile([old],[{**old,'id':'new','lat':39.0002}])
        self.assertEqual(report['preserved_ids'],1)
        self.assertEqual(payload['records'][0]['id'],'old')

    def test_ambiguous_candidate_identifiers_stay_blocked(self):
        _,report=self.reconcile([station('old')],[station('new1'),station('new2')])
        self.assertEqual(report['preserved_ids'],0)

    def test_ambiguous_baseline_identifiers_stay_blocked(self):
        _,report=self.reconcile([station('old1'),station('old2')],[station('new')])
        self.assertEqual(report['preserved_ids'],0)

    def test_primary_identity_survives_separate_corroborating_pin(self):
        old=station('old'); old['sources'].append({'url':'https://www.openstreetmap.org/node/456'})
        payload,report=self.reconcile([old],[station('new1'),station('new2','https://www.openstreetmap.org/node/456')])
        self.assertEqual(report['preserved_ids'],1)
        self.assertEqual({r['id'] for r in payload['records']},{'old','new2'})
        self.assertEqual(report['changes'][0]['contributions'][1]['current_ids'],['new2'])
        self.assertEqual(report['merged_ids'],0)

    def test_candidate_with_existing_id_is_not_reassigned(self):
        _,report=self.reconcile([station('old'),station('existing','https://www.openstreetmap.org/node/999')],[station('existing')])
        self.assertEqual(report['preserved_ids'],0)

    def test_far_away_source_reference_stays_blocked(self):
        _,report=self.reconcile([station('old')],[station('new',lat=40)])
        self.assertEqual(report['preserved_ids'],0)

    def test_matching_application_id_cannot_bypass_movement_guard(self):
        with self.assertRaisesRegex(ValueError, "beyond 250m"):
            self.reconcile([station('same')], [station('same', lat=39.12)])

    def test_primary_owner_does_not_authorize_same_family_alias(self):
        new=station('new'); new['sources'].append({'url':'https://www.openstreetmap.org/node/456'})
        _,report=self.reconcile([station('old1'),station('old2','https://www.openstreetmap.org/node/456')],[new])
        self.assertEqual(report['preserved_ids'],1)
        self.assertEqual(report['unresolved_missing_ids'],['old2'])
        self.assertEqual(report['merged_ids'],0)

    def test_existing_canonical_owner_retains_proven_cross_source_alias(self):
        old = station('old-ocm', 'https://map.openchargemap.io/?mode=details&id=17',
                      source='OpenChargeMap public snapshot')
        owner = station('registry', '', source='DGT National Access Point charging registry',
                        raw={'nap_site_id': 'site-17'}, sources=[])
        current = {**owner, 'price_min': 0.5, 'sources': old['sources'], 'reference_inputs': [anchor(old), anchor(owner)]}
        payload, report = self.reconcile([old, owner], [current])
        row = payload['records'][0]
        self.assertEqual(row['id'], 'registry')
        self.assertEqual(row['price_min'], 0.5)
        self.assertIn('old-ocm', row.get('merged_ids', []))
        self.assertEqual(report['preserved_ids'], 0)
        self.assertEqual(report['merged_ids'], 1)
        self.assertEqual(report['unresolved_missing_ids'], [])
        from qa_ingestion_delta import id_delta
        self.assertEqual(id_delta([old, owner], payload['records'])['missing_ids'], 0)
        self.assertNotIn('merged_ids', current)

    def test_canonical_alias_survives_successive_refreshes_with_current_facts(self):
        old = station('old-ocm', 'https://map.openchargemap.io/?mode=details&id=17',
                      source='OpenChargeMap public snapshot')
        owner = station('registry', '', source='DGT National Access Point charging registry',
                        raw={'nap_site_id': 'site-17'}, sources=[])
        current = {**owner, 'sources': old['sources'], 'reference_inputs': [anchor(old), anchor(owner)]}
        first, _ = self.reconcile([old, owner], [current])
        second, report = self.reconcile(first['records'], [{**current, 'price_min': 0.7}])
        self.assertIn('old-ocm', second['records'][0].get('merged_ids', []))
        self.assertEqual(second['records'][0]['price_min'], 0.7)
        self.assertEqual(report['unresolved_alias_ids'], [])
        self.assertEqual(report['merged_ids'], 1)
        third, _ = self.reconcile(second['records'], [{**current, 'price_min': None}])
        self.assertIn('old-ocm', third['records'][0].get('merged_ids', []))
        self.assertIsNone(third['records'][0]['price_min'])

    def test_lost_alias_evidence_is_reported_without_restoring_old_facts(self):
        old = station('old-ocm', 'https://map.openchargemap.io/?mode=details&id=17',
                      source='OpenChargeMap public snapshot')
        owner = station('registry', '', source='DGT National Access Point charging registry',
                        raw={'nap_site_id': 'site-17'}, sources=[])
        current = {**owner, 'sources': old['sources'], 'reference_inputs': [anchor(old), anchor(owner)]}
        first, _ = self.reconcile([old, owner], [current])
        second, report = self.reconcile(first['records'], [owner])
        self.assertNotIn('old-ocm', second['records'][0].get('merged_ids', []))
        self.assertEqual(report['unresolved_alias_ids'], ['old-ocm'])
        self.assertEqual(second['records'][0]['sources'], [])

    def test_cross_source_alias_requires_current_owner_identity(self):
        old = station('old-ocm', 'https://map.openchargemap.io/?mode=details&id=17',
                      source='OpenChargeMap public snapshot')
        owner = station('registry', '', source='DGT National Access Point charging registry',
                        raw={'nap_site_id': 'owner-site'}, sources=[])
        current = {**owner, 'raw': {'nap_site_id': 'different-site'}, 'sources': old['sources'], 'reference_inputs': [anchor(old), anchor(owner)]}
        payload, report = self.reconcile([old, owner], [current])
        self.assertNotIn('old-ocm', payload['records'][0].get('merged_ids', []))
        self.assertIn('old-ocm', report['unresolved_missing_ids'])

    def test_cross_source_alias_does_not_collapse_same_family_stations(self):
        old = station('old', 'https://map.openchargemap.io/?mode=details&id=17',
                      source='OpenChargeMap public snapshot')
        owner = station('existing', 'https://map.openchargemap.io/?mode=details&id=18',
                        source='OpenChargeMap public snapshot')
        current = {**owner, 'sources': owner['sources'] + old['sources'], 'reference_inputs': [anchor(old), anchor(owner)]}
        payload, report = self.reconcile([old, owner], [current])
        self.assertNotIn('old', payload['records'][0].get('merged_ids', []))
        self.assertIn('old', report['unresolved_missing_ids'])

    def test_cross_source_alias_keeps_distance_and_duplicate_key_guards(self):
        old = station('old-ocm', 'https://map.openchargemap.io/?mode=details&id=17',
                      source='OpenChargeMap public snapshot')
        owner = station('registry', '', source='DGT National Access Point charging registry',
                        raw={'nap_site_id': 'site-17'}, sources=[])
        current = {**owner, 'sources': old['sources'], 'reference_inputs': [anchor(old), anchor(owner)]}
        cases = [
            ([{**old, 'lat': 40}, owner], [current]),
            ([old, owner], [current, {**current, 'id': 'second-pin', 'raw': {'nap_site_id': 'site-18'}}]),
            ([old, {**old, 'id': 'duplicate-old'}, owner], [current]),
            ([old, owner], [current, station('false-claim', merged_ids=['old-ocm'])]),
        ]
        for previous, following in cases:
            with self.subTest(previous=[r['id'] for r in previous], following=[r['id'] for r in following]):
                payload, report = self.reconcile(previous, following)
                target = next(r for r in payload['records'] if r['id'] == 'registry')
                self.assertNotIn('old-ocm', target.get('merged_ids', []))
                self.assertIn('old-ocm', report['unresolved_missing_ids'])

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError): self.reconcile([station('old'),station('old')],[station('new')])

    def test_unrelated_missing_station_is_not_recreated(self):
        payload,report=self.reconcile([station('old')],[station('new','https://www.openstreetmap.org/node/456')])
        self.assertEqual(len(payload['records']),1)
        self.assertEqual(report['unresolved_missing_ids'],['old'])


if __name__=='__main__': unittest.main()
