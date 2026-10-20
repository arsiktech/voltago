"""Offline Endolla/Spirii fixtures, reduced from public exports; no tariffs."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import public_live_status as feeds

FIXTURES = Path(__file__).parent / "fixtures" / "public_live"


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


class ParserTests(unittest.TestCase):
    def test_endolla_triple_join_and_attribution(self):
        result = feeds.parse_endolla(fixture("endolla_static"), fixture("endolla_status"))
        self.assertEqual(len(result["stations"]), 2)
        row = result["stations"][0]
        self.assertEqual(row["connectors"][0]["connector_id"], "3713:11258:1")
        self.assertEqual(row["connectors"][0]["status"], "OUTOFORDER")
        self.assertEqual(row["sources"][0]["license"], "CC BY 4.0")
        self.assertIsNone(row["price_min"])
        self.assertFalse(row["is_free"])

    def test_spirii_reference_join_preserves_evse_and_ignores_tariffs(self):
        result = feeds.parse_spirii(fixture("spirii_static"), fixture("spirii_status"))
        row = result["stations"][0]
        self.assertEqual(row["connectors"][0]["evse_id"], "ES.PGO.AUT00003*1")
        self.assertEqual(row["connectors"][0]["status"], "CHARGING")
        self.assertEqual(row["owner"]["name"], "PowerGo")
        self.assertIsNone(row["price_min"])
        self.assertNotIn("energyPrice", json.dumps(result))


# Integration tests use the common HTTP/MCP path; only feed transport is replaced.
import concurrent.futures
import gzip
import threading
import time
import urllib.error
import urllib.request
from copy import deepcopy
from unittest.mock import patch

import agent_mcp
import server
from scripts import source_transport as transport

NOW = feeds.epoch('2026-10-20T16:26:00Z')


def parsed(feed='endolla'):
    return getattr(feeds, f'parse_{feed}')(fixture(f'{feed}_static'), fixture(f'{feed}_status'))


def document(value, stamp=NOW, modified='Tue, 20 Oct 2026 16:25:00 GMT'):
    return {'document': value, 'fetched_at': feeds.iso(stamp), 'validated_at': feeds.iso(stamp),
            'last_modified': modified, 'not_modified': False}


def cache(feed='endolla'):
    obj = feeds.FeedCache(feed)
    obj.documents = {kind: document(fixture(f'{feed}_{kind}')) for kind in ('static', 'status')}
    obj.parsed = parsed(feed)
    obj.next_refresh = NOW + feeds.STATUS_TTL
    obj.generation = 1
    return obj


def snapshot(feed='endolla', at=NOW):
    with patch.object(feeds.time, 'time', return_value=at):
        return cache(feed).get()


class JoinAndStatusTests(unittest.TestCase):
    def test_endolla_all_known_and_unknown_status_values(self):
        for raw, expected in dict(feeds.STATUS_MAP['endolla'], NEW_STATE='UNKNOWN').items():
            with self.subTest(raw=raw):
                s = fixture('endolla_status')
                s['locations'][0]['stations'][0]['ports'][0]['port_status'] = [{'status': raw}]
                row = feeds.parse_endolla(fixture('endolla_static'), s)['stations'][0]
                self.assertEqual(row['connectors'][0]['status'], expected)

    def test_spirii_all_known_and_unknown_status_values(self):
        for raw, expected in dict(feeds.STATUS_MAP['spirii'], NEW_STATE='UNKNOWN').items():
            with self.subTest(raw=raw):
                s = fixture('spirii_status')
                point = s['messageContainer']['payload'][0]['aegiEnergyInfrastructureStatusPublication']['energyInfrastructureSiteStatus'][0]['energyInfrastructureStationStatus'][0]['refillPointStatus'][0]['aegiElectricChargingPointStatus']
                point['status']['value'] = raw
                row = feeds.parse_spirii(fixture('spirii_static'), s)['stations'][0]
                self.assertEqual(row['connectors'][0]['status'], expected)

    def test_full_triple_separates_repeated_port_ids_and_holds_unlocated(self):
        static, status = fixture('endolla_static'), fixture('endolla_status')
        copied = deepcopy(status['locations'][0])
        copied['id'] = 'unlocated'
        status['locations'].append(copied)
        result = feeds.parse_endolla(static, status)
        self.assertEqual(result['unlocated_ports'], 2)
        self.assertEqual(len(result['stations']), 2)
        keys = [c['connector_id'] for r in result['stations'] for c in r['connectors']]
        self.assertEqual(len(keys), len(set(keys)))

    def test_missing_status_never_uses_static_available(self):
        static, status = fixture('endolla_static'), fixture('endolla_status')
        static['locations'][0]['stations'][0]['ports'][0]['port_status'] = [{'status': 'AVAILABLE'}]
        status['locations'][0]['stations'][0]['ports'].pop(0)
        row = feeds.parse_endolla(static, status)['stations'][0]
        self.assertEqual(row['connectors'][0]['status'], 'UNKNOWN')

    def test_conflicting_endolla_states_fail_closed(self):
        status = fixture('endolla_status')
        status['locations'][0]['stations'][0]['ports'][0]['port_status'] = [{'status':'AVAILABLE'}, {'status':'OUT_OF_ORDER'}]
        row = feeds.parse_endolla(fixture('endolla_static'), status)['stations'][0]
        self.assertEqual(row['connectors'][0]['status'], 'UNKNOWN')

    def test_duplicate_identities_reject_feed(self):
        for feed in ('endolla', 'spirii'):
            with self.subTest(feed=feed):
                static, status = fixture(f'{feed}_static'), fixture(f'{feed}_status')
                if feed == 'endolla':
                    ports = status['locations'][0]['stations'][0]['ports']
                else:
                    ports = status['messageContainer']['payload'][0]['aegiEnergyInfrastructureStatusPublication']['energyInfrastructureSiteStatus'][0]['energyInfrastructureStationStatus'][0]['refillPointStatus']
                ports.append(deepcopy(ports[0]))
                with self.assertRaises(ValueError):
                    getattr(feeds, f'parse_{feed}')(static, status)

    def test_spirii_rejects_non_spanish_addresses(self):
        static = fixture('spirii_static')
        for site in static['payload']['aegiEnergyInfrastructureTablePublication']['energyInfrastructureTable'][0]['energyInfrastructureSite']:
            site['locationReference']['locAreaLocation']['locLocationExtensionG']['FacilityLocation']['address']['countryCode'] = 'PT'
        with self.assertRaises(ValueError):
            feeds.parse_spirii(static, fixture('spirii_status'))

    def test_last_change_is_distinct_from_fetch_and_publication(self):
        row = snapshot('spirii')['stations'][0]
        self.assertNotEqual(row['source_last_updated'], row['source_fetched_at'])
        self.assertNotEqual(row['source_last_updated'], row['source_published_at'])
        self.assertEqual(row['connectors'][0]['last_changed_at'], '2026-10-20T15:38:14.000Z')
        endolla = snapshot()['stations'][0]
        self.assertIsNone(endolla['source_published_at'])
        self.assertTrue(endolla['live_status']['valid'])  # old transition is not a broken heartbeat

    def test_payment_required_does_not_create_price(self):
        static = fixture('endolla_static')
        for loc in static['locations']:
            for st in loc['stations']:
                for port in st['ports']:
                    port['authentications'] = [{'payment_required': False}]
        for row in feeds.parse_endolla(static, fixture('endolla_status'))['stations']:
            self.assertIsNone(row['price_min'])
            self.assertFalse(row['is_free'])
            self.assertFalse(row['is_cheap'])


class CacheTests(unittest.TestCase):
    def test_import_and_health_do_not_fetch(self):
        with patch.object(feeds, 'fetch_document', side_effect=AssertionError('network')):
            self.assertEqual(set(feeds.health()), {'endolla', 'spirii'})

    def test_stale_publication_invalidates_recently_validated_cache(self):
        obj = cache('spirii')
        obj.parsed['publication_time'] = '2020-01-01T00:00:00Z'
        with patch.object(feeds.time, 'time', return_value=NOW):
            payload = obj.get()
        self.assertTrue(payload['cache']['stale'])
        self.assertTrue(all(c['status'] == 'UNKNOWN' for r in payload['stations'] for c in r['connectors']))

    def test_expired_validation_invalidates_cache(self):
        obj = cache()
        obj.next_refresh = NOW + 1000
        with patch.object(feeds.time, 'time', return_value=NOW + 200):
            self.assertTrue(obj.get()['cache']['stale'])

    def test_outage_retains_evidence_but_revokes_available_and_backs_off(self):
        obj = cache()
        obj.next_refresh = NOW - 1
        with patch.object(feeds.time, 'time', return_value=NOW + 61), patch.object(feeds, 'fetch_document', side_effect=TimeoutError) as fetch:
            one, two = obj.get(), obj.get(force=True)
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(one['cache']['stale'])
        self.assertEqual(one['stations'], two['stations'])
        self.assertTrue(all(c['status'] == 'UNKNOWN' for r in one['stations'] for c in r['connectors']))

    def test_malformed_feed_retains_prior_timestamps_without_authority(self):
        obj = cache()
        obj.next_refresh = NOW - 1
        with patch.object(feeds.time, 'time', return_value=NOW + 61), patch.object(feeds, 'fetch_document', return_value=document({'locations': []}, NOW+61)):
            result = obj.get()
        self.assertEqual(result['cache']['fetched_at'], feeds.iso(NOW))
        self.assertTrue(result['cache']['stale'])

    def test_single_flight_and_static_cache_reuse(self):
        obj = cache()
        obj.next_refresh = NOW - 1
        entered, release = threading.Event(), threading.Event()
        def fetch(*args):
            entered.set()
            release.wait(2)
            return document(fixture('endolla_status'), NOW + 61)
        with patch.object(feeds.time, 'time', return_value=NOW + 61), patch.object(feeds, 'fetch_document', side_effect=fetch) as mocked:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(obj.get) for _ in range(4)]
                self.assertTrue(entered.wait(1))
                release.set()
                values = [future.result() for future in futures]
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual({v['cache']['generation'] for v in values}, {2})

    def test_successful_304_keeps_fetch_publication_and_change_times(self):
        previous = document(fixture('spirii_status'))
        err = urllib.error.HTTPError(feeds.PUBLIC_LIVE_URLS['spirii-status'], 304, 'Not Modified', {}, None)
        with patch.object(feeds.time, 'time', return_value=NOW + 61), patch.object(feeds, 'public_live_urlopen', side_effect=err) as fetch:
            result = feeds.fetch_document(feeds.PUBLIC_LIVE_URLS['spirii-status'], previous)
        self.assertEqual(result['fetched_at'], previous['fetched_at'])
        self.assertEqual(result['document'], previous['document'])
        self.assertEqual(result['validated_at'], feeds.iso(NOW + 61))
        self.assertEqual(fetch.call_args.args[0].get_header('If-modified-since'), previous['last_modified'])

    def test_unsolicited_304_has_no_cache_to_validate(self):
        err = urllib.error.HTTPError(feeds.PUBLIC_LIVE_URLS['spirii-status'], 304, 'Not Modified', {}, None)
        with patch.object(feeds, 'public_live_urlopen', side_effect=err), self.assertRaises(urllib.error.HTTPError):
            feeds.fetch_document(feeds.PUBLIC_LIVE_URLS['spirii-status'])
        err.close()

    def test_gzip_and_tariff_subtrees_are_discarded(self):
        d = fixture('spirii_status')
        d['energyRateUpdate'] = [{'secretTariffSchedule': 123}]
        class Response:
            headers = {'Content-Encoding': 'gzip'}
            def read(self, limit): return gzip.compress(json.dumps(d).encode())
            def __enter__(self): return self
            def __exit__(self, *args): pass
        with patch.object(feeds, 'public_live_urlopen', return_value=Response()):
            result = feeds.fetch_document(feeds.PUBLIC_LIVE_URLS['spirii-status'])
        self.assertNotIn('secretTariffSchedule', json.dumps(result))
        self.assertEqual(feeds.parse_spirii(fixture('spirii_static'), result['document'])['raw_count'], 14)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.row = snapshot()['stations'][0]
        self.row['connectors'][0]['status'] = 'AVAILABLE'
        self.canonical = deepcopy(self.row)
        self.canonical.update(id='existing-bundle-id', lead=True, reliability='official-cheap', price_min=0.22,
                              is_cheap=True, parking={'kind':'paid_or_metered'}, access={'level':'restricted','note':'Subscription'})
        self.canonical.pop('live_status')
        self.canonical.pop('live_feed')
        self.canonical['sources'] = [{'name':'Existing tariff','url':'https://example.org/tariff'}]

    def test_existing_id_claims_and_attribution_survive_shared_enrichment(self):
        row = server.merge_public_live_stations([], [self.row], [self.canonical])[0]
        server.enrich_station(row, live=True)
        self.assertEqual(row['id'], 'existing-bundle-id')
        self.assertEqual(row['price_min'], 0.22)
        self.assertEqual(row['parking']['kind'], 'paid_or_metered')
        self.assertEqual(row['access']['level'], 'restricted')
        self.assertTrue(any(s.get('license_url') == 'https://creativecommons.org/licenses/by/4.0/' for s in row['sources']))
        self.assertFalse(server.route_ready_now(row))

    def test_shared_dataset_url_never_merges_distinct_sites(self):
        other = snapshot()['stations'][1]
        self.assertFalse(server.likely_same_station(self.row, other))
        self.assertEqual(len(server.merge_public_live_stations([], [self.row, other], [])), 2)

    def test_distinct_spirii_site_ids_never_merge_by_operator_and_proximity(self):
        first = snapshot('spirii')['stations'][0]
        second = deepcopy(first)
        second.update(id='live_spirii_other-site', live_site_id='other-site')
        second['raw'] = {'spirii_location_id': 'other-site'}
        for connector in second['connectors']:
            connector['connector_id'] += '-other'
            connector['evse_id'] = 'ES*TEST*OTHER'
            connector['external_evse_ids'] = ['ES*TEST*OTHER']
        self.assertFalse(server.public_live_identity(first, second))
        self.assertEqual(len(server.merge_public_live_stations([], [first, second], [])), 2)

    def test_ambiguous_canonical_identity_is_held(self):
        other = dict(self.canonical, id='second-id')
        self.assertEqual(server.merge_public_live_stations([], [self.row], [self.canonical, other]), [])

    def test_conflicting_exact_identity_coordinates_are_held(self):
        self.canonical['lat'] += 1
        self.assertEqual(server.merge_public_live_stations([], [self.row], [self.canonical]), [])

    def test_proximity_requires_independent_operator_and_name_guard(self):
        other = deepcopy(self.row)
        other.update(id='independent', raw={}, live_feed=None)
        other['owner'] = {'name':'Different operator'}
        self.assertFalse(server.public_live_identity(self.row, other))
        other['owner'] = deepcopy(self.row['owner'])
        self.assertTrue(server.public_live_identity(self.row, other))

    def test_stale_secondary_does_not_poison_fresh_independent_primary(self):
        self.canonical.update(lead=False, reliability='official-live', cache_stale=False, last_seen=server.now_iso())
        self.row['live_status']['valid'] = False
        self.row['_volta_stale_live_cache'] = True
        result = server.merge_public_live_stations([self.canonical], [self.row], [])[0]
        self.assertEqual(result['reliability'], 'official-live')
        self.assertNotIn('_volta_stale_live_cache', result)

    def test_fresh_overlay_replaces_stale_primary_without_inheriting_staleness(self):
        self.canonical.update(lead=False, reliability='official-live', cache_stale=True, _volta_stale_live_cache=True)
        result = server.merge_public_live_stations([self.canonical], [self.row], [])[0]
        self.assertFalse(result['cache_stale'])
        self.assertNotIn('_volta_stale_live_cache', result)

    def test_conflicting_fresh_status_fails_closed(self):
        self.canonical.update(lead=False, reliability='official-live', cache_stale=False, last_seen=server.now_iso())
        self.canonical['connectors'][0]['status'] = 'OUTOFORDER'
        result = server.merge_public_live_stations([self.canonical], [self.row], [])[0]
        server.enrich_station(result, live=True)
        self.assertTrue(result['live_status']['conflict'])
        self.assertEqual(result['available'], 0)
        self.assertFalse(result['verification']['live_verified'])

    def test_swapped_identified_ports_conflict_even_with_equal_status_counts(self):
        self.canonical.update(lead=False, reliability='official-live', cache_stale=False, last_seen=server.now_iso())
        for row, statuses in ((self.canonical, ('AVAILABLE', 'CHARGING')), (self.row, ('CHARGING', 'AVAILABLE'))):
            for connector, status in zip(row['connectors'], statuses):
                connector['status'] = status
        result = server.merge_public_live_stations([self.canonical], [self.row], [])[0]
        server.enrich_station(result, live=True)
        self.assertTrue(result['live_status']['conflict'])
        self.assertEqual(result['available'], 0)
        self.assertFalse(result['verification']['live_verified'])

    def test_different_availability_counts_conflict_even_with_equal_status_sets(self):
        before = {'connectors': [{'status': 'AVAILABLE'}, {'status': 'CHARGING'}]}
        after = {'connectors': [{'status': 'AVAILABLE'}, {'status': 'AVAILABLE'}, {'status': 'CHARGING'}]}
        self.assertTrue(server.public_live_status_conflicts(before, after))

    def test_old_primary_transition_without_cache_marker_does_not_poison_fresh_feed(self):
        self.canonical.update(lead=False, reliability='official-live', cache_stale=False,
                              last_seen='2020-01-01T00:00:00Z')
        self.canonical['connectors'][0]['status'] = 'OUTOFORDER'
        result = server.merge_public_live_stations([self.canonical], [self.row], [])[0]
        self.assertTrue(result['live_status']['valid'])
        self.assertEqual(result['connectors'][0]['status'], 'AVAILABLE')

    def test_existing_tariff_port_subset_is_not_expanded(self):
        self.canonical['connectors'] = self.canonical['connectors'][:1]
        result = server.merge_public_live_stations([], [self.row], [self.canonical])[0]
        self.assertEqual(len(result['connectors']), 1)

    def test_old_transition_can_use_shared_go_decision_with_independent_price(self):
        for row in (self.canonical, self.row):
            row['connectors'][0]['label'] = 'Type 2'
        self.canonical.update(access={'level':'public'}, parking={'kind':'street_or_municipal','paid':False})
        result = server.merge_public_live_stations([], [self.row], [self.canonical])[0]
        server.enrich_station(result, live=True)
        self.assertTrue(result['verification']['live_verified'])
        self.assertTrue(server.route_ready_now(result))

    def test_restricted_access_cannot_get_go_from_new_status(self):
        for row in (self.canonical, self.row):
            row['connectors'][0]['label'] = 'Type 2'
        self.canonical['parking'] = {'kind':'street_or_municipal','paid':False}
        result = server.merge_public_live_stations([], [self.row], [self.canonical])[0]
        server.enrich_station(result, live=True)
        self.assertFalse(server.route_ready_now(result))


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.original_cache = deepcopy(server.CACHE)
        server.CACHE.clear()
        self.rows = snapshot()['stations']
        self.patches = [patch('server.fetch_mapareve', side_effect=TimeoutError),
                        patch('server.fetch_gaia_live', return_value={'stations':[], 'cache':{}}),
                        patch('server.fetch_public_live', side_effect=lambda **_: {'stations':deepcopy(self.rows), 'feeds':{'endolla':{'generation':1}}}),
                        patch('server.load_all_leads', return_value=[])]
        for p in self.patches: p.start()
        self.addCleanup(self.cleanup)
        self.params = {'bbox':['41,1.8,41.7,2.5'], 'max_price':['5'], 'include_leads':['true']}

    def cleanup(self):
        for p in reversed(self.patches): p.stop()
        server.CACHE.clear()
        server.CACHE.update(self.original_cache)

    def test_live_outage_does_not_break_http_data_path(self):
        code, result = server.live_response(self.params)
        self.assertEqual(code, 200)
        self.assertEqual(len(result['stations']), 2)
        self.assertTrue(result['cache']['fresh_live_authority'])
        self.assertFalse(result['cache']['live_failed'])
        self.assertTrue(all(r['verification']['live_verified'] for r in result['stations']))

    def test_feed_failure_never_grants_available_or_go(self):
        for row in self.rows:
            row['live_status']['valid'] = False
            row['_volta_stale_live_cache'] = True
            for c in row['connectors']: c['status'] = 'UNKNOWN'
        _, result = server.live_response(self.params)
        self.assertFalse(result['cache']['fresh_live_authority'])
        self.assertTrue(all(r['available'] == 0 and not server.route_ready_now(r) for r in result['stations']))

    def test_assembled_cache_cannot_replay_previous_feed_identity(self):
        with patch('server.fetch_mapareve', return_value={'stations':[], 'raw_count':0}):
            _, first = server.live_response(self.params)
            self.rows[0]['connectors'][0]['status'] = 'AVAILABLE'
            _, second = server.live_response(self.params)
        self.assertNotEqual(first['stations'][0]['connectors'], second['stations'][0]['connectors'])
        self.assertIsNone(server.CACHE.get('assembled'))

    def test_mcp_projection_uses_same_runtime_rows_and_transition_semantics(self):
        _, result = server.live_response(self.params)
        row = result['stations'][0]
        view = agent_mcp.charger_view(row, server, detail=True)
        self.assertEqual(view['id'], row['id'])
        self.assertEqual(view['hardware']['connectors'][0]['status'], row['connectors'][0]['status'])
        self.assertEqual(view['evidence']['live_verified'], row['verification']['live_verified'])
        self.assertFalse(view['evidence']['live_status_stale'])

    def test_health_and_strategy_expose_licences_and_cache_health_without_fetch(self):
        health = server.source_health_summary()
        self.assertIn('endolla', health['live_cache'])
        self.assertIn('spirii', health['live_cache'])
        names = {r['name'] for r in server.requested_source_strategy()['requested_apps']}
        self.assertTrue({'Barcelona Endolla','Spirii AFIR Spain'} <= names)

class TransportTests(unittest.TestCase):
    def setUp(self):
        transport.reset_budgets()
        self.addCleanup(transport.reset_budgets)

    def test_exact_get_endpoints_only(self):
        urls = [feeds.PUBLIC_LIVE_URLS['spirii-status'].replace('ES', 'PT'),
                feeds.PUBLIC_LIVE_URLS['spirii-status'].replace('https:', 'http:'),
                feeds.PUBLIC_LIVE_URLS['endolla-status'] + '?unreviewed=1',
                'https://api.spirii.com/v2/afir/other',
                'https://api.spirii.com.evil.example/v2/afir/energy-infrastructure-statuses?countryIsos=ES']
        with patch.object(transport.urllib.request, 'build_opener', side_effect=AssertionError('socket')):
            for url in urls:
                with self.subTest(url=url), self.assertRaises(transport.SourcePolicyBlocked):
                    transport.public_live_urlopen(urllib.request.Request(url), timeout=5)

    def test_method_credentials_and_timeout_bounds(self):
        url = feeds.PUBLIC_LIVE_URLS['spirii-status']
        for method, headers, timeout in [('POST',{},5), ('GET',{'Authorization':'test'},5), ('GET',{'Cookie':'test'},5), ('GET',{},7)]:
            with self.subTest(method=method, headers=headers, timeout=timeout), self.assertRaises(transport.SourcePolicyBlocked):
                transport.public_live_urlopen(urllib.request.Request(url, headers=headers, method=method), timeout=timeout)

    def test_get_budget_is_per_endpoint_and_bounded(self):
        opener = unittest.mock.Mock()
        with patch.object(transport.urllib.request, 'build_opener', return_value=opener):
            for _ in range(24):
                transport.public_live_urlopen(urllib.request.Request(feeds.PUBLIC_LIVE_URLS['endolla-status']), timeout=5)
            with self.assertRaises(transport.SourcePolicyBlocked):
                transport.public_live_urlopen(urllib.request.Request(feeds.PUBLIC_LIVE_URLS['endolla-status']), timeout=5)
            transport.public_live_urlopen(urllib.request.Request(feeds.PUBLIC_LIVE_URLS['spirii-status']), timeout=5)
        self.assertEqual(opener.open.call_count, 25)

    def test_redirect_handler_refuses_even_same_origin(self):
        req = urllib.request.Request(feeds.PUBLIC_LIVE_URLS['spirii-status'])
        self.assertIsNone(transport._NoRedirect().redirect_request(req, None, 302, 'Moved', {}, feeds.PUBLIC_LIVE_URLS['spirii-static']))

    def test_revoked_grant_prevents_collection(self):
        with patch.object(server, 'SOURCE_POLICY_EXCLUSIONS', {'granted_reuse': []}), patch.object(feeds, 'fetch_public_live', return_value={}) as fetch:
            server.fetch_public_live()
        self.assertEqual(fetch.call_args.kwargs['enabled'], [])


class AdditionalFailureTests(unittest.TestCase):
    def test_invalid_or_future_last_change_never_grants_port_availability(self):
        for changed in (None, 'not-a-time', '2099-01-01T00:00:00Z'):
            obj = cache()
            port = obj.documents['status']['document']['locations'][0]['stations'][0]['ports'][0]
            port.update(last_updated=changed, port_status=[{'status':'AVAILABLE'}])
            obj.parsed = feeds.parse_endolla(obj.documents['static']['document'], obj.documents['status']['document'])
            with patch.object(feeds.time, 'time', return_value=NOW):
                result = obj.get()
            self.assertEqual(result['stations'][0]['connectors'][0]['status'], 'UNKNOWN')

    def test_static_expiry_failure_revokes_status_authority(self):
        obj = cache()
        obj.next_refresh = 0
        with patch.object(feeds.time, 'time', return_value=NOW + feeds.STATIC_TTL + 1), patch.object(feeds, 'fetch_document', side_effect=TimeoutError):
            self.assertTrue(obj.get()['cache']['stale'])

    def test_304_cannot_refresh_expired_spirii_publication(self):
        obj = cache('spirii')
        obj.next_refresh = 0
        previous = obj.documents['status']
        with patch.object(feeds.time, 'time', return_value=NOW + 600), patch.object(feeds, 'fetch_document', return_value=dict(previous, validated_at=feeds.iso(NOW + 600), not_modified=True)):
            result = obj.get()
        self.assertTrue(result['cache']['stale'])
        self.assertEqual(result['publication_time'], obj.parsed['publication_time'])
        self.assertEqual(result['cache']['fetched_at'], previous['fetched_at'])

    def test_gzip_expansion_and_http_age_bounds(self):
        class Response:
            headers = {'Content-Encoding':'gzip'}
            def read(self, limit): return gzip.compress(b' ' * 1025)
            def __enter__(self): return self
            def __exit__(self, *args): pass
        with patch.object(feeds, 'MAX_BYTES', 1024), patch.object(feeds, 'public_live_urlopen', return_value=Response()), self.assertRaises(ValueError):
            feeds.fetch_document(feeds.PUBLIC_LIVE_URLS['spirii-status'])
        Response.headers = {'Age':'1000'}
        Response.read = lambda self, limit: b'{}'
        with patch.object(feeds, 'public_live_urlopen', return_value=Response()), self.assertRaises(ValueError):
            feeds.fetch_document(feeds.PUBLIC_LIVE_URLS['endolla-status'])

    def test_source_timestamps_and_evse_ids_survive_compaction(self):
        row = snapshot('spirii')['stations'][0]
        compact = server.compact_station_for_ui(row)
        self.assertEqual(compact['connectors'][0]['last_changed_at'], row['connectors'][0]['last_changed_at'])
        self.assertEqual(compact['connectors'][0]['evse_id'], row['connectors'][0]['evse_id'])
        summary = server.summary_station_for_ui(row, 'fixture')
        for field in ('attribution', 'transformation', 'source_fetched_at', 'source_published_at', 'source_last_updated'):
            self.assertEqual(summary['sources'][0].get(field), row['sources'][0].get(field))
        self.assertNotIn('evidence', summary['sources'][0])

    def test_spirii_owner_approval_is_not_presented_as_a_named_licence(self):
        for source in feeds.source_refs('spirii', 'PowerGo'):
            self.assertIn('Spirii', source['attribution'])
            self.assertIn('PowerGo', source['attribution'])
            self.assertNotIn('license', source)


class ReviewRegressionTests(unittest.TestCase):
    row: dict
    canonical: dict
    setUp = IdentityTests.setUp

    def test_live_status_cannot_verify_inherited_directory_price_or_parking(self):
        for row in (self.row, self.canonical):
            for connector in row['connectors']:
                connector['label'] = 'Type 2'
        self.canonical.update(reliability='directory-map-free', access={'level':'public'},
                              parking={'kind':'street_or_municipal', 'paid':False})
        self.canonical['verification'] = server.verification_profile(self.canonical, live=False)
        self.assertFalse(self.canonical['verification']['price_verified'])
        self.assertFalse(self.canonical['verification']['parking_verified'])
        result = server.merge_public_live_stations([], [self.row], [self.canonical])[0]
        server.enrich_station(result, live=True)
        self.assertTrue(result['verification']['live_verified'])
        self.assertFalse(result['verification']['price_verified'])
        self.assertFalse(result['verification']['parking_verified'])
        self.assertFalse(server.route_ready_now(result))

    def test_replaced_hardware_cannot_retain_compatible_fast_flags(self):
        self.canonical.update(tesla_ok=True, fast=True, max_power_kw=150,
                              access={'level':'public'}, parking={'kind':'street_or_municipal','paid':False})
        for connector in self.row['connectors']:
            connector.update(label='CHAdeMO', power_kw=22, status='AVAILABLE')
        self.row['connector_labels'] = ['CHAdeMO']
        result = server.merge_public_live_stations([], [self.row], [self.canonical])[0]
        self.assertFalse(server.station_passes_route_filters(result, max_price=5, fast_only=True))
        server.enrich_station(result, live=True)
        self.assertFalse(result['tesla_ok'])
        self.assertEqual(result['max_power_kw'], 22)
        self.assertFalse(server.route_ready_now(result))

    def test_attached_source_key_survives_downstream_lead_deduplication(self):
        self.canonical['raw'] = {}
        distinct = deepcopy(self.canonical)
        distinct.update(id='distinct-endolla-site', raw={'endolla_location_id':'different'})
        self.assertFalse(server.public_live_identity(self.row, distinct))
        result = server.merge_public_live_stations([self.canonical], [self.row], [])[0]
        self.assertFalse(server.public_live_identity(result, distinct))
        self.assertEqual(len(server.remove_duplicate_leads([distinct], [result])), 1)

    def test_stale_http_304_cannot_revalidate_endolla(self):
        previous = document(fixture('endolla_status'))
        headers = {'Age':'1000', 'Date':'Tue, 20 Oct 2026 15:00:00 GMT'}
        err = urllib.error.HTTPError(feeds.PUBLIC_LIVE_URLS['endolla-status'], 304, 'Not Modified', headers, None)
        with patch.object(feeds.time, 'time', return_value=NOW + 61), patch.object(feeds, 'public_live_urlopen', side_effect=err), self.assertRaises(ValueError):
            feeds.fetch_document(feeds.PUBLIC_LIVE_URLS['endolla-status'], previous)

    def test_oldest_publication_is_selected_by_instant_not_text(self):
        dynamic = fixture('spirii_status')
        pubs = dynamic['messageContainer']['payload']
        pubs[0]['aegiEnergyInfrastructureStatusPublication']['publicationTime'] = '2026-10-20T18:26:00+02:00'
        pubs.append({'aegiEnergyInfrastructureStatusPublication': {
            'publicationTime':'2026-10-20T16:27:00Z', 'energyInfrastructureSiteStatus':[]}})
        result = feeds.parse_spirii(fixture('spirii_static'), dynamic)
        self.assertEqual(result['publication_time'], '2026-10-20T18:26:00+02:00')

    def test_health_attributes_actual_cached_operators(self):
        health = cache('spirii').health()
        self.assertEqual(health['operators'], ['PowerGo'])
        self.assertTrue(any('PowerGo' in ref['attribution'] for ref in health['sources']))


class HttpMcpParityTests(unittest.TestCase):
    setUp = RuntimeTests.setUp
    cleanup = RuntimeTests.cleanup
    # Reuse the fixture setup, not the inherited tests, for one real transport proof.
    def test_http_and_mcp_search_share_ids_status_and_attribution(self):
        row = self.rows[0]
        lat, lon = row['lat'], row['lon']
        httpd = server.VoltaHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        def request(path, payload=None, headers=None):
            req = urllib.request.Request(f'http://127.0.0.1:{httpd.server_port}{path}',
                                         data=json.dumps(payload).encode() if payload else None,
                                         headers=headers or {})
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.load(response)
        try:
            api = request(f'/api/chargers?lat={lat}&lon={lon}&radius_km=5&max_price=5&include_leads=true')
            protocol = '2026-07-28'
            mcp = request('/mcp', {'jsonrpc':'2.0','id':1,'method':'tools/call',
                'params':{'name':'find_chargers','arguments':{'lat':lat,'lon':lon,'radius_km':5},
                          '_meta':{'io.modelcontextprotocol/protocolVersion':protocol,
                                   'io.modelcontextprotocol/clientCapabilities':{}}}},
                          {'Content-Type':'application/json','MCP-Protocol-Version':protocol,
                           'Mcp-Method':'tools/call','Mcp-Name':'find_chargers'})
            results = mcp['result']['structuredContent']['chargers']
            self.assertTrue(results)
            api_rows = {r['id']:r for r in api['stations']}
            for result in results:
                shared = api_rows[result['id']]
                self.assertEqual(result['hardware']['connectors'][0]['status'], shared['connectors'][0]['status'])
                self.assertEqual(result['sources'][0]['attribution'], shared['sources'][0]['attribution'])
                self.assertEqual(result['evidence']['live_verified'], shared['verification']['live_verified'])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(2)


if __name__ == '__main__':
    unittest.main()
