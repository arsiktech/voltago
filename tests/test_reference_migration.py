"""Adversarial reference regressions; no network, production or user-data access."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
import tempfile
import sqlite3
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import station_identity as identity
from qa_ingestion_delta import id_delta

def old_row(sid='old', poi='17', **kw):
    return dict(id=sid, source='OpenChargeMap public snapshot', lat=39., lon=-.5,
                source_url=f'https://map.openchargemap.io/?mode=details&id={poi}', sources=[], **kw)

def owner():
    return dict(id='owner', source='DGT National Access Point charging registry', lat=39., lon=-.5,
                raw={'nap_site_id':'site-1'}, sources=[])

def merged():
    return dict(owner(), merged_source_identifiers=[['ocm-station','17']], merged_ids=['old'],
                reference_inputs=[identity.anchor(old_row()), identity.anchor(owner())])

def reconcile(old, new):
    return identity.preserve_record_ids({'records':old}, {'records':new})

class ReferenceRegressionTests(unittest.TestCase):
    def test_L1_raw_merge_never_bypasses_distance_or_durable_proof(self):
        old = old_row(); current = merged(); current['lat'] += .004
        self.assertEqual(id_delta([old, owner()], [current])['missing_ids'], 1)

    def test_L2_split_including_one_unique_key_is_blocked(self):
        old = old_row(); old['sources']=[{'url':'https://map.openchargemap.io/?mode=details&id=18'}]
        current=merged(); current['merged_source_identifiers'].append(['ocm-station','18'])
        # The primary is duplicated; a unique corroborator cannot select an owner.
        _, report=reconcile([old,owner()],[current,old_row('competitor','17')])
        self.assertIn('old',report['unresolved_missing_ids'])

    def test_L3_candidate_only_alias_is_not_trusted(self):
        current=owner(); current['identity_aliases']=[{'id':'owner','lat':39.,'lon':-.5,'source_identifiers':[['ocm-station','17']]}]
        _, report=reconcile([owner()],[current])
        self.assertTrue(report['unresolved_alias_ids'])

    def test_L4_owner_proof_is_revalidated(self):
        first,_=reconcile([old_row(),owner()],[merged()])
        current=merged(); current['raw']={'nap_site_id':'another-owner'}
        _,report=identity.preserve_record_ids(first,{'records':[current]})
        self.assertIn('old', report['unresolved_alias_ids'])

    def test_L5_server_admits_proven_alias_and_attaches_community(self):
        import server
        first,_=reconcile([old_row(),owner()],[merged()])
        rows=first['records']
        store = server.community_store
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(store, '_DB_PATH', Path(tmp)/'community.db'), \
             mock.patch.object(store, '_PHOTOS_DIR', Path(tmp)/'photos'), \
             mock.patch.object(server,'load_intelligence_meta',return_value=first), \
             mock.patch.object(server,'_intel_enriched_pool',return_value=(rows,[],[],[])), \
             mock.patch.object(server,'_KNOWN_IDS_CACHE',{'ts':0,'ids':set()}):
            store.record_checkin('old','failed',known_station=lambda sid:sid in server.known_station_ids())
            store.record_checkin('owner','offline',known_station=lambda sid:sid in server.known_station_ids())
            store.save_photo('old',b'\xff\xd8\xff'+b'p'*32,known_station=lambda sid:sid in server.known_station_ids())
            self.assertIn('old',server.known_station_ids())
            output=server.attach_community_rows(deepcopy(rows))
            self.assertEqual(output[0]['community']['checkins']['total'],2)
            self.assertEqual(len(output[0]['community']['photos']),1)
            self.assertFalse(output[0].get('live_verified',False))
            with sqlite3.connect(Path(tmp)/'community.db') as db:
                self.assertEqual({r[0] for r in db.execute('SELECT station_id FROM checkins')},{'old','owner'})

if __name__=='__main__':unittest.main()

class ReferenceContractTests(unittest.TestCase):
    def test_hash_vectors_match_web_and_native(self):
        self.assertEqual(identity.reference_key('old'),'cc44436bce2af9dce4f4e0eaaabfa325587c667ef23dbef59d9ac70c378700b4')
        self.assertEqual(identity.reference_key('café/Зарядка'),'4b8eabe483059336d14d3db10d39d71a223e7c6847f24936f8c69c638e8cc6a8')

    def test_successive_refresh_split_and_original_anchors(self):
        first,_=reconcile([old_row(),owner()],[merged()])
        current=merged(); current['lat']+=.001
        second,report=identity.preserve_record_ids(first,{'records':[current]})
        self.assertFalse(report['unresolved_alias_ids'])
        self.assertEqual(second['records'][0]['identity_aliases'][0]['lat'],39.)
        current['lat']+=.002
        _,report=identity.preserve_record_ids(second,{'records':[current]})
        self.assertIn('old',report['unresolved_alias_ids'])
        split=old_row('split')
        _,report=identity.preserve_record_ids(first,{'records':[merged(),split]})
        self.assertIn('old',report['unresolved_alias_ids'])

    def test_final_canonical_conflict_blocks_even_after_reassignment(self):
        fresh=old_row('generated')
        false=owner();false['merged_ids']=['old']
        payload,report=reconcile([old_row()],[fresh,false])
        self.assertTrue(report['unresolved_alias_ids'] or report['unresolved_missing_ids'])
        self.assertTrue(id_delta([old_row()],payload['records'],payload)['reference_errors'] or id_delta([old_row()],payload['records'],payload)['missing_ids'])

    def test_candidate_alias_proof_requires_previous_anchor(self):
        first,_=reconcile([old_row(),owner()],[merged()])
        forged=deepcopy(first);forged['records'][0]['identity_aliases'][0]['lat']+=.0001
        with self.assertRaises(ValueError):identity.validate_references(forged,{'records':[old_row(),owner()]})
        with self.assertRaises(ValueError):identity.validate_references({'records':first['records']},{'records':[owner()]})

    def test_duplicate_owners_self_alias_and_malformed_source_are_rejected(self):
        first,_=reconcile([old_row(),owner()],[merged()])
        for change in ({'id':'owner'},{'source_identifiers':'17'},{'owner':{}},{'lat':float('nan')}):
            altered=deepcopy(first)
            altered['records'][0]['identity_aliases'][0].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):identity.validate_references(altered)
        second=deepcopy(first['records'][0]);second['id']='other'
        with self.assertRaises(ValueError):identity.validate_references({'records':first['records']+[second]})

    def test_public_projection_contains_no_audit_or_withheld_detail(self):
        import server,json
        payload,report=reconcile([old_row('private-old-name_39.0_0.5')],[owner()])
        model=identity.validate_references(payload)
        catalogue=model.public()
        self.assertNotIn('private-old-name',json.dumps(catalogue))
        self.assertEqual(catalogue['entries'][0]['state'],'unresolved')
        row={**payload['records'][0], 'reference_revision':model.revision, 'identity_aliases':[{'id':'secret'}], 'reference_inputs':[{'name':'secret'}]}
        with mock.patch.object(server,'load_intelligence_meta',return_value=payload):
            response=server.bind_reference_response({'leads':[row],'station_references':{'secret':'secret'}})
            self.assertNotIn('secret',json.dumps(response))
            self.assertEqual(response['references']['revision'],row['reference_revision'])
            response=server.bind_reference_response({'leads':[dict(row,reference_revision='b'*64)]})
            self.assertIsNone(response['references'])
        self.assertNotIn('identity_aliases',server.compact_station_for_ui(row))

    def test_alias_write_admission_requires_a_current_admitted_owner(self):
        import server
        payload,_=reconcile([old_row(),owner()],[merged()])
        with mock.patch.object(server,'load_intelligence_meta',return_value=payload), \
             mock.patch.object(server,'_intel_enriched_pool',return_value=([],[],[],[])), \
             mock.patch.object(server,'_KNOWN_IDS_CACHE',{'ts':0,'ids':set()}), \
             mock.patch.object(server,'CACHE',{'payload':{}}), \
             mock.patch.object(server.community_store,'summaries',return_value={'stations':{},'accepted_suggestions':[]}):
            self.assertNotIn('old',server.known_station_ids())

    def test_summary_route_preserves_seed_live_and_community_references(self):
        import server
        payload, _ = reconcile([old_row(), owner()], [merged()])
        model = identity.validate_references(payload)
        model = identity.ReferenceModel(model.revision, model.canonical, (
            *model.entries, {'id': 'held', 'state': 'withheld'}, {'id': 'unresolved', 'state': 'unresolved'}))
        independent = ['seed-fixture', 'live-fixture', 'community_suggestion_fixture']
        summaries = {sid: {'marker': sid} for sid in ['owner', *independent, 'held', 'unresolved']}
        handler = object.__new__(server.Handler)
        handler.path = '/api/community/summary?ids=old,owner,' + ','.join(independent) + ',unknown,held,unresolved'
        responses = []
        handler.send_json = lambda status, payload, extra_headers=None: responses.append((status, payload))
        with mock.patch.object(server, 'reference_model', return_value=model), \
             mock.patch.object(server, 'community_reference_summaries', return_value={'stations': summaries}), \
             mock.patch.object(server, 'known_station_ids', return_value={'owner', 'old', *independent, 'held', 'unresolved'}), \
             mock.patch.object(server.community_store, 'stats', return_value={}):
            handler._route_get()
        self.assertEqual(responses[0][0], 200)
        result = responses[0][1]['stations']
        self.assertEqual(set(result), {'old', 'owner', *independent})
        self.assertEqual(result['old'], result['owner'])
        self.assertNotIn('unknown', result)

    def test_revision_tampering_and_unknown_version_rejected(self):
        payload,_=reconcile([old_row(),owner()],[merged()])
        for change in ({'revision':'f'*64},{'version':2}):
            mutated=deepcopy(payload);mutated['station_references'].update(change)
            with self.assertRaises(ValueError):identity.validate_references(mutated)

    def test_unresolved_never_becomes_alias_without_fresh_source_evidence(self):
        first,_=reconcile([old_row()],[owner()])
        second,report=identity.preserve_record_ids(first,{'records':[owner()]})
        self.assertEqual(report['unresolved_missing_ids'],['old'])
        self.assertIsNone(identity.validate_references(second).resolve('old'))

class SourceRightsTests(unittest.TestCase):
    def fixture(self):
        from source_rights import rights_decision
        import ocm_export
        poi={'ID':17,'DataProviderID':27,'DataProvider':{'ID':27,'Title':'Fixture provider','IsOpenDataLicensed':False,'License':None},'AddressInfo':{'Country':{'ISOCode':'ES'}}}
        import gzip,hashlib,json
        content=gzip.compress((json.dumps(poi)+'\n').encode())
        artifact={'cache_name':'legacy.json.gz','sha256':hashlib.sha256(content).hexdigest()}
        decision=rights_decision(poi,poi['DataProvider'],artifact,'legacy',ocm_export.OCM_LEGACY_SHA)
        return content,{'records':[],'source_snapshots':{'openchargemap':{'artifacts':{'legacy':artifact},'rights_withdrawals':[decision]}}}

    def test_explicit_withdrawal_and_revalidation_against_actual_bytes(self):
        from source_rights import verify_rights_artifacts
        content,candidate=self.fixture()
        payload,report=identity.preserve_record_ids({'records':[old_row()]},candidate)
        self.assertEqual(report['withheld_ids'],['old'])
        self.assertFalse(report['unresolved_missing_ids'])
        self.assertIsNone(identity.validate_references(payload).resolve('old'))
        self.assertEqual(id_delta([old_row()],[],payload)['withheld_ids'],1)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'legacy.json.gz').write_bytes(content)
            verify_rights_artifacts(payload,Path(tmp))
            (Path(tmp)/'legacy.json.gz').write_bytes(b'tampered')
            with self.assertRaises(ValueError):verify_rights_artifacts(payload,Path(tmp))

    def test_licensed_unknown_and_wrong_source_never_retire(self):
        _,candidate=self.fixture()
        for edit in ({'License':'CC BY 4.0','IsOpenDataLicensed':True},{'ID':28}):
            bad=deepcopy(candidate);bad['source_snapshots']['openchargemap']['rights_withdrawals'][0]['provider'].update(edit)
            with self.assertRaises(ValueError):identity.preserve_record_ids({'records':[old_row()]},bad)
        mismatch=old_row(poi='18')
        _,report=identity.preserve_record_ids({'records':[mismatch]},candidate)
        self.assertEqual(report['unresolved_missing_ids'],['old'])

    def test_withdrawal_rechecks_source_on_next_refresh(self):
        _,candidate=self.fixture()
        first,_=identity.preserve_record_ids({'records':[old_row()]},candidate)
        second,report=identity.preserve_record_ids(first,{'records':[]})
        self.assertEqual(report['unresolved_missing_ids'],['old'])
        self.assertEqual(identity.validate_references(second).public()['entries'][0]['state'],'unresolved')
