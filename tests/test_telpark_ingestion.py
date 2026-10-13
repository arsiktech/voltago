"""Offline Telpark collector, identity and runtime-honesty regression tests."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import ingest_sources as ing
import ingest_telpark as telpark
import server
from station_identity import identity_keys
from station_truth import primary_source_for, classify_source_resource

URL = 'https://www.telpark.com/es/ciudades/madrid/parking-central/'
LIST_TIME = '2026-10-10T12:00:00Z'
DETAIL_TIME = '2026-10-09T12:00:00Z'


def item(pid=42, url=URL, country_address='Calle Uno, 28001', lat=40.4, lon=-3.7):
    return {'id': pid, 'link': url, 'title': {'rendered': 'Parking Central'},
            'meta_box': {'header': {'address': country_address, 'latitude': lat, 'longitude': lon,
                                    'prize': '0€ parking / 2€ per hour'}}}


def detail(ev=True, country='Aparcar en España', lat=40.437925, lon=-3.719618, kind='ParkingFacility'):
    node = {'@type': kind, 'name': 'Parking Central',
            'geo': {'latitude': lat, 'longitude': lon},
            'address': {'streetAddress': 'Calle Uno, 28001', 'addressLocality': 'Madrid', 'addressCountry': country},
            'amenityFeature': [{'name': 'Carga eléctrica', 'value': ev}]}
    return ('<script type="application/ld+json">' + json.dumps(node) + '</script>').encode()


class CacheFixture:
    def __init__(self, items=None, details=None):
        self.items = [item()] if items is None else items
        self.details = {URL: detail()} if details is None else details
        self.calls = []
        self.fallback = set()
        self.errors = set()

    def __call__(self, url, name, **kwargs):
        self.calls.append((url, name, kwargs))
        if url in self.errors:
            raise OSError('fixture upstream unavailable')
        is_list = '/wp-json/' in url
        if not is_list and url not in self.details:
            if kwargs.get('cache_only'):
                raise FileNotFoundError(name)
            raise OSError('missing detail fixture')
        body = json.dumps(self.items).encode() if is_list else self.details[url]
        return ing.FetchArtifact(body, url, name, LIST_TIME if is_list else DETAIL_TIME,
                                 100.0, True, url in self.fallback, False,
                                 'OSError' if url in self.fallback else None)


class TelparkIngestionTests(unittest.TestCase):
    def collect(self, fixture=None, **kwargs):
        return telpark.collect_telpark(fixture or CacheFixture(), ing.add_record, **kwargs)

    def record(self):
        rows, status = self.collect()
        self.assertTrue(status['ready'])
        return rows[0]

    def test_confirmed_detail_is_visible_only_as_directory_location(self):
        row = self.record()
        self.assertEqual((row['lat'], row['lon']), (40.437925, -3.719618))
        self.assertEqual(row['source_url'], URL)
        self.assertEqual(row['raw']['telpark_parking_id'], '42')
        self.assertEqual(row['primary_source']['kind'], 'official_data')
        self.assertEqual(row['primary_source']['label'], 'Parking with EV charging')
        self.assertIn('not an exact charging bay', row['primary_source']['warning'])
        normalized = server._normalize_lead(deepcopy(row), row['source'])
        self.assertTrue(server.source_health_map_eligible(normalized))
        self.assertEqual(server.source_health_tier(normalized), 'directory')
        self.assertTrue(normalized['verification']['location_verified'])
        for flag in ('price_verified', 'live_verified', 'route_checked'):
            self.assertFalse(normalized['verification'][flag])
        self.assertFalse(server.route_ready_now(normalized))
        self.assertNotEqual(normalized['decision']['level'], 'go')

    def test_parking_price_never_becomes_energy_price_or_hardware(self):
        row = self.record()
        self.assertIsNone(row['price_min'])
        self.assertFalse(row['is_free'])
        self.assertFalse(row['is_cheap'])
        self.assertEqual(row['price_claim_type'], 'price_unknown_lead')
        self.assertIn('0€ parking', row['parking']['note'])
        self.assertEqual(row['connector_labels'], [])
        self.assertEqual(row['connectors'], [])
        self.assertIsNone(row['max_power_kw'])
        self.assertNotIn('available', row)
        self.assertEqual(row['status'], 'VERIFY')
        self.assertIsNone(row['source_integration']['remote_start_public_url'])
        merged, _ = ing.merge_cross_source_duplicates(ing.dedupe([row]))
        self.assertIsNone(merged[0]['price_min'])
        self.assertFalse(merged[0]['is_free'])
        self.assertEqual(merged[0]['connector_labels'], [])
        self.assertEqual(merged[0]['location_claim']['precision'], 'official_parking_coordinate')
        self.assertIn('not an exact charging bay', merged[0]['location_claim']['summary'])

    def test_oldest_retained_artifact_time_survives_export_merge(self):
        row = self.record()
        self.assertEqual(row['source_fetched_at'], DETAIL_TIME)
        self.assertNotIn('retrieved_at', row['raw'])
        artifacts = row['raw']['source_fetch_artifacts']
        self.assertEqual([a['fetched_at'] for a in artifacts], [LIST_TIME, DETAIL_TIME])
        merged, _ = ing.merge_cross_source_duplicates(ing.dedupe([row]))
        self.assertEqual(merged[0]['source_fetched_at'], DETAIL_TIME)
        fixture = CacheFixture()
        with patch.object(ing, 'cached_fetch', fixture):
            records = []
            status = ing.source_telpark(records)
        self.assertEqual(records[0]['source_fetched_at'], DETAIL_TIME)
        self.assertTrue(status['coverage_complete'])
        self.assertIsNone(records[0]['last_seen'])

    def test_acquisitions_are_cached_bounded_and_only_telpark(self):
        fixture = CacheFixture()
        self.collect(fixture)
        self.assertEqual(len(fixture.calls), 2)
        for url, name, kw in fixture.calls:
            self.assertTrue(url.startswith('https://www.telpark.com/'))
            self.assertTrue(name.startswith('telpark_'))
            self.assertIs(kw['fetcher'], telpark.bounded_fetch)
            self.assertLessEqual(kw['timeout'], 30)
            self.assertFalse(kw['cache_only'])

    def test_false_missing_and_organization_only_ev_flags_are_excluded(self):
        for payload in (detail(False), '<html>Carga eléctrica</html>'.encode(), detail(True, kind='Organization')):
            with self.subTest(payload=payload):
                rows, status = self.collect(CacheFixture(details={URL: payload}))
                self.assertFalse(rows)
                self.assertFalse(status['ready'])

    def test_rest_coordinates_do_not_replace_missing_detail_coordinates(self):
        rows, status = self.collect(CacheFixture(details={URL: detail(lat=None, lon=None)}))
        self.assertFalse(rows)
        self.assertFalse(status['ready'])
        self.assertEqual(status['evidence_rejected'], 1)

    def test_no_portugal_andorra_or_baltic_collection(self):
        items = [item(), item(2, URL.replace('madrid', 'lisboa'), 'Rua Uno 1000-123', 38.7, -9.1),
                 item(3, URL.replace('madrid', 'andorra'), 'AD700', 42.51, 1.54),
                 item(4, URL.replace('madrid', 'riga'), 'Riga', 56.9, 24.1)]
        fixture = CacheFixture(items=items)
        rows, status = self.collect(fixture)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(fixture.calls), 2)
        self.assertFalse(status['coverage_complete'])
        self.assertFalse(any('elektrum' in fn.__name__ for fn in ing.ACTIVE_SOURCE_ADAPTERS))

    def test_detail_country_must_be_known_spain_even_when_list_says_spain(self):
        for country in ('PT', 'Andorra', 'LV', None, 'unknown'):
            rows, status = self.collect(CacheFixture(details={URL: detail(country=country)}))
            self.assertFalse(rows, country)
            self.assertFalse(status['ready'])

    def test_zero_nonfinite_and_out_of_range_detail_coordinates(self):
        for lat, lon in ((0, 0), (float('nan'), -3.7), (40.4, float('inf')), (91, -3.7), (40.4, 181)):
            rows, _ = self.collect(CacheFixture(details={URL: detail(lat=lat, lon=lon)}))
            self.assertFalse(rows)

    def test_upstream_failure_and_cached_fallback_fail_closed_atomically(self):
        url2 = URL.replace('parking-central', 'parking-second')
        for fallback in (False, True):
            fixture = CacheFixture(items=[item(), item(43, url2)], details={URL: detail(), url2: detail()})
            (fixture.fallback if fallback else fixture.errors).add(url2)
            rows, status = self.collect(fixture)
            self.assertFalse(rows)
            self.assertFalse(status['ready'])
            self.assertTrue(status['errors'])
            with patch.object(ing, 'cached_fetch', fixture):
                prior = [{'id': 'previous'}]
                with self.assertRaises(RuntimeError):
                    ing.source_telpark(prior)
                self.assertEqual(prior, [{'id': 'previous'}])
                self.assertTrue(ing.TELPARK_STATUS['errors'])

    def test_cached_details_survive_exhausted_network_budget(self):
        fixture = CacheFixture()
        rows, status = self.collect(fixture, max_detail_fetches=0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(status['cached_details_retained'], 1)
        self.assertTrue(fixture.calls[-1][2]['cache_only'])
        self.assertEqual(rows[0]['source_fetched_at'], DETAIL_TIME)

    def test_uncached_details_after_budget_are_not_silently_complete(self):
        url2 = URL.replace('parking-central', 'parking-second')
        fixture = CacheFixture(items=[item(), item(43, url2)])
        rows, status = self.collect(fixture, max_detail_fetches=1)
        self.assertEqual(len(rows), 1)
        self.assertTrue(status['ready'])
        self.assertFalse(status['coverage_complete'])
        self.assertEqual(status['ev_unconfirmed'], 1)
        self.assertFalse(status['errors'])

    def test_full_last_rest_page_reports_incomplete_coverage(self):
        items = [item(pid=i, url=URL.replace('parking-central', f'parking-{i}')) for i in range(1, 101)]
        details = {r['link']: detail(ev=r['id'] == 1) for r in items}
        rows, status = self.collect(CacheFixture(items=items, details=details), max_pages=1)
        self.assertEqual(len(rows), 1)
        self.assertFalse(status['list_complete'])
        self.assertEqual(status['coverage'], 'incomplete')

    def test_registered_adapter_updates_optional_status_without_requiring_it(self):
        self.assertIn(ing.source_telpark, ing.ACTIVE_SOURCE_ADAPTERS)
        with patch.object(ing, 'ADAPTER_STATUS', {}, create=True), patch.object(ing, 'cached_fetch', CacheFixture()):
            status = ing.source_telpark([])
            self.assertEqual(ing.ADAPTER_STATUS['source_telpark'], status)

    def test_positive_typed_identity_is_strict(self):
        row = self.record()
        expected = (telpark.SOURCE, 'telpark_parking_id', '42')
        self.assertIn(expected, identity_keys(row))
        for value in (42, True, False, '', '0', '-1', ' 42', '042', '4.2', [], {}):
            changed = deepcopy(row)
            changed['raw']['telpark_parking_id'] = value
            self.assertFalse(identity_keys(changed), repr(value))
            self.assertNotEqual((primary_source_for(changed) or {}).get('kind'), 'official_data')

    def test_malicious_or_altered_urls_are_not_generic_host_allowlist(self):
        row = self.record()
        for url in (URL.replace('https:', 'http:'), URL + '?x=1', URL + '#x',
                    URL.replace('www.telpark.com', 'www.telpark.com.evil.test'),
                    URL.replace('www.telpark.com', 'evil@www.telpark.com'),
                    URL.replace('www.telpark.com', 'www.telpark.com:444'),
                    'https://www.telpark.com/', URL.replace('parking-central', '%2e%2e'),
                    URL.replace('/es/', '/pt/'), URL.replace('parking-central', '../parking-central')):
            changed = deepcopy(row)
            changed['source_url'] = url
            changed['raw']['telpark_detail_url'] = url
            changed['sources'] = []
            self.assertNotEqual((primary_source_for(changed) or {}).get('kind'), 'official_data', url)
            self.assertFalse(server.source_health_map_eligible(server._normalize_lead(changed, changed['source'])), url)
        self.assertEqual(classify_source_resource({'name': telpark.SOURCE, 'url': URL})['quality'], 'generic')

    def test_unrelated_child_or_no_ev_provenance_cannot_supply_map_proof(self):
        row = self.record()
        for field, value in (('ev_charging', False), ('ev_charging', 'true'), ('telpark_detail_url', URL + 'changed/'),
                             ('ev_flag_source', 'organization'), ('coordinate_source', 'rest_meta_box_header'), ('country', 'PT')):
            changed = deepcopy(row)
            changed['raw'][field] = value
            self.assertNotEqual((primary_source_for(changed) or {}).get('kind'), 'official_data')
        changed = deepcopy(row)
        changed['source'] = 'Unrelated directory'
        self.assertNotEqual((primary_source_for(changed) or {}).get('kind'), 'official_data')

    def test_invalid_primary_coordinates_are_not_map_proof(self):
        for field, value in (('lat', float('nan')), ('lon', float('inf')), ('lat', True), ('lat', 91), ('lon', 181)):
            row = self.record()
            row[field] = value
            self.assertNotEqual((primary_source_for(row) or {}).get('kind'), 'official_data')

    def test_unsafe_list_url_is_rejected_before_detail_fetch(self):
        url = 'https://evil.test/es/ciudades/madrid/parking-central/'
        fixture = CacheFixture(items=[item(url=url)])
        rows, status = self.collect(fixture)
        self.assertFalse(rows)
        self.assertTrue(status['errors'])
        self.assertEqual(len(fixture.calls), 1)

    def test_cached_fetch_extension_uses_scoped_fetcher_and_never_network_in_cache_only(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ing, 'CACHE', Path(tmp)):
            with patch.object(ing, 'fetch', side_effect=AssertionError('generic fetch must not run')):
                fetcher = lambda url, **kw: b'fixture'
                first = ing.cached_fetch(URL, 'telpark_test.html', fetcher=fetcher)
                second = ing.cached_fetch(URL, 'telpark_test.html', fetcher=fetcher, cache_only=True)
                self.assertEqual(second.body, first.body)
                self.assertEqual(second.fetched_at, first.fetched_at)
                self.assertTrue(second.hit)
                with self.assertRaises(FileNotFoundError):
                    ing.cached_fetch(URL, 'missing.html', fetcher=fetcher, cache_only=True)

    def test_fixture_build_admits_telpark_without_a_new_policy_gate(self):
        with patch.object(ing, 'ACTIVE_SOURCE_ADAPTERS', (ing.source_telpark,)), patch.object(ing, 'cached_fetch', CacheFixture()):
            payload = ing.build_payload()
        self.assertEqual(payload['source_adapter_counts']['source_telpark'], 1)
        self.assertEqual(len(payload['records']), 1)
        self.assertFalse(payload['errors'])
        row = payload['records'][0]
        self.assertEqual(row['primary_source']['url'], URL)
        self.assertEqual(row['source_fetched_at'], DETAIL_TIME)
        self.assertEqual(row['location_claim']['precision'], 'official_parking_coordinate')
        self.assertIsNone(row['price_min'])
        self.assertTrue(server.source_health_map_eligible(server._normalize_lead(deepcopy(row), row['source'])))

    def test_oversized_cached_body_is_an_error(self):
        fixture = CacheFixture(details={URL: b'x' * (telpark.pages.MAX_BODY_BYTES + 1)})
        rows, status = self.collect(fixture)
        self.assertFalse(rows)
        self.assertTrue(status['errors'])

    def test_transport_is_bounded_anonymous_get_and_refuses_redirects(self):
        from unittest.mock import MagicMock
        opener = MagicMock()
        response = opener.open.return_value.__enter__.return_value
        response.read.return_value = b'fixture'
        with patch.object(telpark.urllib.request, 'build_opener', return_value=opener):
            self.assertEqual(telpark.bounded_fetch(URL, timeout=120), b'fixture')
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertIsNone(request.data)
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 30)
        response.read.assert_called_once_with(telpark.pages.MAX_BODY_BYTES + 1)
        with self.assertRaises(ValueError):
            telpark._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test/')
        for headers in ({'Authorization': 'fixture'}, {'Cookie': 'fixture'}, {'X-API-Key': 'fixture'}):
            with self.assertRaises(ValueError):
                telpark.bounded_fetch(URL, headers=headers)
        for url in ('https://evil.test/', URL + '?x=1', telpark.pages.TELPARK_REST_URL.format(per_page=100, page=11)):
            with self.assertRaises(ValueError):
                telpark.bounded_fetch(url)
        response.read.return_value = b'x' * (telpark.pages.MAX_BODY_BYTES + 1)
        with patch.object(telpark.urllib.request, 'build_opener', return_value=opener), self.assertRaises(ValueError):
            telpark.bounded_fetch(URL)

    def test_distinct_wp_ids_stay_distinct_through_both_merges(self):
        url2 = URL.replace('parking-central', 'parking-second')
        fixture = CacheFixture(items=[item(), item(43, url2)], details={URL: detail(), url2: detail()})
        rows, _ = self.collect(fixture)
        merged, _ = ing.merge_cross_source_duplicates(ing.dedupe(rows))
        self.assertEqual(len(merged), 2)
        self.assertEqual({r['raw']['telpark_parking_id'] for r in merged}, {'42', '43'})


if __name__ == '__main__':
    unittest.main()
