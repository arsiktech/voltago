"""Regression tests from the full public-directory candidate rehearsal."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import ingest_sources as ing
from station_identity import preserve_record_ids
from tests.test_directory_maps import CM_POOL, EM_ITEM, EM_DETAIL


class DirectoryLifecycleTests(unittest.TestCase):
    def test_removed_duplicate_and_nonexistent_names_are_retained_but_not_actionable(self):
        for marker in ('DOUBLON', 'SUPPRIMÉ', 'BORNE DEMONTEE', 'INEXISTANT'):
            with self.subTest(marker=marker):
                row = ing.parse_chargemap_pool({**CM_POOL, 'name': 'Parking ' + marker})[0]
                self.assertIn('source_reported_unavailable', row)
                self.assertFalse(row['is_free'])
                self.assertFalse(row['is_cheap'])
                merged, _ = ing.merge_cross_source_duplicates(ing.dedupe([row]))
                self.assertFalse(merged[0]['is_free'])
                self.assertFalse(merged[0]['is_cheap'])

    def test_operational_status_downgrades_without_using_occupancy(self):
        row = ing.parse_chargemap_pool({**CM_POOL, 'operational_status': 'OUT_OF_ORDER'})[0]
        self.assertIn('source_reported_unavailable', row)
        occupied = ing.parse_chargemap_pool({**CM_POOL, 'availability_status': 'UNAVAILABLE'})[0]
        self.assertNotIn('source_reported_unavailable', occupied)

    def test_electromaps_not_working_is_not_a_free_recommendation(self):
        row = ing.parse_electromaps_location(EM_ITEM, {**EM_DETAIL, 'offline_status': 'NOT_WORKING'})[0]
        self.assertIn('source_reported_unavailable', row)
        self.assertFalse(row['is_free'])
        self.assertEqual(row['price_min'], 0.0, 'retain the historical price claim separately')

    def test_unavailability_survives_a_second_source_merge(self):
        broken = ing.parse_chargemap_pool({**CM_POOL, 'operational_status': 'OUT_OF_ORDER'})[0]
        second = ing.parse_electromaps_location({**EM_ITEM, 'latitude': broken['lat'], 'longitude': broken['lon']}, EM_DETAIL)[0]
        rows, _ = ing.merge_cross_source_duplicates(ing.dedupe([broken, second]))
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['is_free'])
        self.assertFalse(rows[0]['is_cheap'])
        self.assertIn('source_reported_unavailable', rows[0])


class TwoStageIdentityTests(unittest.TestCase):
    def test_first_stage_source_families_prevent_second_stage_osm_chain(self):
        # One OSM pin already sits in the exact-coordinate directory group.
        # A nearby, distinct OSM pin must not join through its directory primary.
        em = ing.parse_electromaps_location(EM_ITEM, EM_DETAIL)[0]
        def osm(node, lat):
            row = dict(em, id='fresh-' + node, source='OpenStreetMap public tags',
                       source_url='https://www.openstreetmap.org/node/' + node,
                       raw={}, lat=lat, reliability='osm-tagged-free')
            row['sources'] = [{'name': row['source'], 'url': row['source_url']}]
            return row
        first = osm('101', em['lat'])
        second = osm('102', em['lat'] + 0.00015)
        baseline = {'records': [dict(first, id='old-101'), dict(second, id='old-102')]}
        rows, _ = ing.merge_cross_source_duplicates(ing.dedupe([em, first, second]))
        self.assertEqual(len(rows), 2)
        payload, report = preserve_record_ids(baseline, {'records': rows})
        self.assertEqual(report['unresolved_missing_ids'], [])
        self.assertEqual({r['id'] for r in payload['records']}, {'old-101', 'old-102'})


if __name__ == '__main__':
    unittest.main()
