import json
from pathlib import Path
import sys
import unittest
from copy import deepcopy
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from station_identity import preserve_record_ids


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

    def test_conflicting_distinct_identity_keys_stay_blocked(self):
        old=station('old'); old['sources'].append({'url':'https://www.openstreetmap.org/node/456'})
        _,report=self.reconcile([old],[station('new1'),station('new2','https://www.openstreetmap.org/node/456')])
        self.assertEqual(report['preserved_ids'],0)

    def test_candidate_with_existing_id_is_not_reassigned(self):
        _,report=self.reconcile([station('old'),station('existing','https://www.openstreetmap.org/node/999')],[station('existing')])
        self.assertEqual(report['preserved_ids'],0)

    def test_far_away_source_reference_stays_blocked(self):
        _,report=self.reconcile([station('old')],[station('new',lat=40)])
        self.assertEqual(report['preserved_ids'],0)

    def test_many_missing_ids_cannot_claim_one_candidate(self):
        new=station('new'); new['sources'].append({'url':'https://www.openstreetmap.org/node/456'})
        _,report=self.reconcile([station('old1'),station('old2','https://www.openstreetmap.org/node/456')],[new])
        self.assertEqual(report['preserved_ids'],0)

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError): self.reconcile([station('old'),station('old')],[station('new')])

    def test_unrelated_missing_station_is_not_recreated(self):
        payload,report=self.reconcile([station('old')],[station('new','https://www.openstreetmap.org/node/456')])
        self.assertEqual(len(payload['records']),1)
        self.assertEqual(report['unresolved_missing_ids'],['old'])


if __name__=='__main__': unittest.main()
