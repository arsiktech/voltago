"""Price annotations through collection, real merges and exported build payloads."""
from __future__ import annotations
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import ingest_sources as ing
from scripts import price_evidence as p
from scripts.enrich_prices import enrich_payload
from scripts import source_transport as transport
import test_source_parsers as source_fixtures

FIXTURES = ROOT / 'tests' / 'fixtures' / 'price_evidence'
NOW = 1792497600  # 2026-10-20T12:00:00Z
FETCHED = p.now_iso(NOW)


def load(name):
    value = (FIXTURES / name).read_text(encoding='utf-8')
    return json.loads(value) if name.endswith('.json') else value


class Response:
    def __init__(self, value):
        self.body = value.encode() if isinstance(value, str) else json.dumps(value).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, limit=-1):
        return self.body[:limit] if limit >= 0 else self.body


class FixtureNetwork:
    def __init__(self, *, outage=False):
        self.calls = []
        self.outage = outage
    def __call__(self, request, *, timeout):
        self.calls.append(request)
        if self.outage:
            raise TimeoutError('source unavailable')
        url = request.full_url
        if url == p.REPSOL_URL:
            return Response(load('repsol.html'))
        if url.startswith(p.EM_BASE + '?'):
            detail = load('electromaps.json')
            return Response([{k: detail[k] for k in ('id','name','latitude','longitude')}])
        if url.startswith(p.EM_BASE + '/'):
            detail = load('electromaps.json')
            detail.update(created_by={'username': 'must-not-persist'}, images=['must-not-persist'], comments=['must-not-persist'])
            return Response(detail)
        if url.startswith(p.CM_LIST):
            return Response({'response': {'content': {'items': [{'pool': load('chargemap.json')}]}}})
        if url.startswith(p.CM_PRICE):
            return Response(load('chargemap.html'))
        raise AssertionError(url)


def station(identifier='registry-em', *, lat=39.459062306723, lon=-0.388613504299,
            operator='IBERDROLA CLIENTES SAU', connector='Type 2', power=22, price=None,
            address='Avinguda de Gaspar Aguilar 7'):
    out = []
    ing.add_record(out, source=p.REGISTRY, source_url=ing.DGT_NAP_CATALOG_URL,
                   name=operator + ' — Test charging point', address=address,
                   city='València', lat=lat, lon=lon, price_min=price,
                   reliability='official-charger-location-price-unknown',
                   raw={'operator':operator, 'nap_site_id':identifier, 'corroboration_only':True},
                   connectors=[{'label':connector, 'type':connector, 'power_kw':power, 'count':1}], connector_labels=[connector])
    out[0]['id'] = identifier
    return out[0]


def fixture_stations():
    return [station(),
            station('registry-cm', lat=39.459252, lon=-0.382481, connector='CCS2', power=175, price=0.49,
                    address='Calle San Vicente Martir 171'),
            station('registry-repsol', lat=39.48, lon=-0.41, operator='REPSOL SOLUCIONES ENERGÉTICAS SA')]


class ParserTests(unittest.TestCase):
    def test_chargemap_public_street_and_number_survive_identity_projection(self):
        candidate = p.sanitize_chargemap(load('chargemap.json'))
        self.assertEqual(candidate['address'], 'SAN VICENTE MARTIR 171')
        row = fixture_stations()[1]
        self.assertIs(p.unique_match(candidate, [row], 'chargemap'), row)
        candidate['address'] = 'SAN VICENTE MARTIR 999'
        self.assertIsNone(p.unique_match(candidate, [row], 'chargemap'))

    def test_repsol_context_table_from_and_caveat_required(self):
        guide = p.parse_repsol_tariff(load('repsol.html'), FETCHED)
        self.assertEqual(guide['label'], 'Repsol published tariff · station may differ')
        self.assertIsNone(guide['price_per_kwh'])
        self.assertIsNone(guide['source_updated_at'])
        self.assertIn('desde 0,48', guide['text'])
        self.assertFalse(guide['tariff_details']['power_bands_published'])
        for bad in ('normal 0,36 rápida 0,48 ultrarrápida 0,56', load('repsol.html').replace('desde ', ''),
                    load('repsol.html').replace('0,56', '0,57'), load('repsol.html').replace('tarifas distintas.', '')):
            with self.subTest(bad=bad):
                self.assertIsNone(p.parse_repsol_tariff(bad, FETCHED))

    def test_community_observation_date_is_never_fetch_date(self):
        claim = p.parse_electromaps_price(p.sanitize_electromaps(load('electromaps.json')), FETCHED)
        self.assertEqual(claim['source_updated_at'], '2025-08-16T19:12:42+0000')
        self.assertEqual(claim['label'], 'Electromaps community price · 2025-08-16')
        self.assertEqual(claim['fetched_at'], FETCHED)
        self.assertEqual(claim['price_per_kwh'], 0.36)
        self.assertEqual(claim['parking_price'], load('electromaps.json')['parking_price'])
        self.assertEqual(claim['parking_price'], claim['tariff_details']['parking_price'])
        self.assertEqual(claim['text'], load('electromaps.json')['charge_price'])
        self.assertNotIn(claim['parking_price'], claim['text'])
        for date in (None, '', '2025-02-30', 'not-a-date', '2099-01-01', '1970-01-01'):
            detail = p.sanitize_electromaps(load('electromaps.json')); detail['updated_at'] = date
            self.assertIsNone(p.parse_electromaps_price(detail, FETCHED))

    def test_missing_parking_text_is_optional_and_cannot_donate_charging_evidence(self):
        detail = p.sanitize_electromaps(load('electromaps.json'))
        for parking in (None, '', '   '):
            with self.subTest(parking=parking):
                claim = p.parse_electromaps_price(dict(detail, parking_price=parking), FETCHED)
                self.assertNotIn('parking_price', claim)
                self.assertEqual(claim['text'], detail['charge_price'])
        detail['charge_price'] = ''
        self.assertIsNone(p.parse_electromaps_price(detail, FETCHED))

    def test_community_marker_or_app_instruction_is_not_tariff(self):
        for price in ('', 'PAID', 'UNKNOWN', 'Check app', 'cost: yes'):
            detail = p.sanitize_electromaps(load('electromaps.json')); detail['charge_price'] = price
            self.assertIsNone(p.parse_electromaps_price(detail, FETCHED))

    def test_community_price_range_keeps_text_without_a_single_numeric_tariff(self):
        detail = p.sanitize_electromaps(load('electromaps.json'))
        detail['charge_price'] = '0.35-0.54€/KWH'
        claim = p.parse_electromaps_price(detail, FETCHED)
        self.assertEqual(claim['text'], '0.35-0.54€/KWH')
        self.assertIsNone(claim['price_per_kwh'])

    def test_chargemap_started_energy_and_idle_fees_are_preserved(self):
        claim = p.parse_chargemap_price(load('chargemap.html'), FETCHED, '315448')
        self.assertEqual(claim['label'], 'Price with Chargemap Pass')
        self.assertEqual(claim['price_per_kwh'], 0.799)
        self.assertEqual(claim['tariff_details']['energy_billing_unit'], 'started_kWh')
        self.assertEqual(claim['tariff_details']['idle_fee_per_minute'], 0.111)
        self.assertEqual(claim['tariff_details']['idle_grace_minutes'], 15)
        self.assertIn('connected after', claim['tariff_details']['idle_trigger'])
        self.assertIn('15 minutos sin carga', claim['text'])
        self.assertIsNone(claim['source_updated_at'])
        self.assertNotIn('session_fee', claim['tariff_details'])
        mixed = p.parse_chargemap_price('Recarga 0,05 € por minuto. ' + load('chargemap.html'), FETCHED, '1')
        self.assertEqual(mixed['tariff_details']['idle_fee_per_minute'], 0.111)
        self.assertIsNone(p.parse_chargemap_price('<h1>Sign in</h1>', FETCHED, '1'))
        self.assertIsNone(p.parse_chargemap_price('is_free=true', FETCHED, '1'))

    def test_only_whitelisted_public_facts_survive_projection(self):
        detail = load('electromaps.json')
        detail.update(created_by={'username':'private-name'}, image='photo', comments=['private-review'])
        clean = p.sanitize_electromaps(detail)
        self.assertNotIn('private-', json.dumps(clean))
        self.assertNotIn('image', clean)
        pool = p.sanitize_chargemap(load('chargemap.json'))
        self.assertNotIn('is_free', pool)
        self.assertNotIn('available_count', json.dumps(pool))
        malformed = p.sanitize_electromaps(dict(detail, name={'username':'private-name'}, updated_at={'private':'private-value'}))
        self.assertNotIn('private-', json.dumps(malformed))

    def test_foreign_rows_are_removed_before_list_cache_persistence(self):
        detail=load('electromaps.json')
        foreign=dict(detail, latitude=41.15, longitude=-8.61)
        self.assertEqual(p.list_rows([foreign], 'electromaps'), [])
        pool=load('chargemap.json'); pool['gps_coordinates']={'lat':41.15,'lon':-8.61}
        self.assertEqual(p.list_rows({'response':{'content':{'items':[{'pool':pool}]}}}, 'chargemap'), [])


class IdentityTests(unittest.TestCase):
    def test_registry_legal_suffix_punctuation_does_not_hide_a_known_network(self):
        self.assertEqual(p.network('IBERDROLA CLIENTES S.A.U'), 'iberdrola')
        self.assertEqual(p.network('ENDESA X WAY, S.L.'), 'endesa x way')
        self.assertNotEqual(p.network('IBERDROLA BP PULSE'), p.network('IBERDROLA CLIENTES S.A.U'))
    def setUp(self):
        self.row = station()
        self.candidate = p.sanitize_electromaps(load('electromaps.json'))
    def match(self, candidate=None, rows=None):
        return p.unique_match(candidate or self.candidate, rows if rows is not None else [self.row], 'electromaps')
    def test_unique_corroborated_location_operator_and_connectors(self):
        self.assertIs(self.match(), self.row)
    def test_wrong_operator_or_related_network_does_not_match(self):
        for operator in ('REPSOL SOLUCIONES ENERGÉTICAS SA', 'IBERDROLA BP PULSE', 'Unknown company'):
            self.row['raw']['operator'] = operator
            self.assertIsNone(self.match())
    def test_nearby_address_mismatch_and_distance_do_not_match(self):
        self.row['address'] = 'Calle Other 99'
        self.assertIsNone(self.match())
        self.row = station(lat=self.row['lat'] + 0.0005)
        self.assertIsNone(self.match())
    def test_missing_or_incompatible_connectors_are_withheld(self):
        for connectors in ([], [{'label':'CCS2','power_kw':22}], [{'label':'Type 2','power_kw':150}]):
            self.row['connectors'] = connectors
            self.assertIsNone(self.match())
    def test_ambiguous_nearby_same_family_ids_are_withheld(self):
        other = station('second-registry', lat=self.row['lat']+0.00015)
        self.assertIsNone(self.match(rows=[self.row, other]))
    def test_stable_provider_id_uses_250m_guard_but_never_wrong_operator(self):
        self.row['raw']['electromaps_id'] = 757380
        self.row['address'] = 'Not enough address'; self.row['lat'] += 0.001
        self.assertIs(self.match(), self.row)
        self.row['raw']['operator'] = 'Repsol'
        self.assertIsNone(self.match())
        self.row['raw']['operator'] = 'IBERDROLA CLIENTES SAU'; self.row['lat'] += 0.003
        self.assertIsNone(self.match())
    def test_conflicting_provider_or_evse_ids_never_fall_back_to_proximity(self):
        self.row['raw']['electromaps_id'] = 999
        self.assertIsNone(self.match())
        self.row['raw'].pop('electromaps_id')
        self.row['raw']['evse_ids'] = ['ES*ABC*E123']
        self.candidate['evse_ids'] = ['ESXYZE999']
        self.assertIsNone(self.match())
    def test_unique_stable_match_takes_precedence_over_unknown_nearby_site(self):
        self.row['raw']['electromaps_id'] = 757380
        other = station('nearby', lat=self.row['lat']+0.00015)
        self.assertIs(self.match(rows=[self.row,other]), self.row)
    def test_structured_network_does_not_hide_conflicting_street_address(self):
        self.candidate['network'] = 'Iberdrola'
        self.row['address'] = 'Other street 99, 46007 Valencia'
        self.assertIsNone(self.match())
    def test_evse_keys_are_typed_and_numeric_internal_ids_are_not_evse(self):
        self.row['raw']['evse_ids'] = ['ES*ABC*E123']
        self.candidate['evse_ids'] = ['ESABCE123']; self.row['lat'] += 0.001
        self.assertIs(self.match(), self.row)
        self.row['raw']['evse_ids'] = [870334]; self.candidate['evse_ids'] = [870334]
        self.assertIsNone(self.match())
    def test_country_and_geography_remain_spain_only(self):
        self.candidate['country_code'] = 'PT'
        self.assertIsNone(self.match())
        for lat, lon in ((48.8,2.3),(41.15,-8.61),(0,0)):
            self.assertFalse(p.in_spain({'lat':lat,'lon':lon}))
            self.assertFalse(p.in_spain({'lat':lat,'lon':lon,'country_code':'ES'}))

    def test_real_chargemap_mennekes_matches_colocated_22kw_by_stable_id_or_corroboration(self):
        pool = deepcopy(source_fixtures.SourceChargemapMappyTests.REAL_POOLS[1])
        candidate = p.sanitize_chargemap(pool)
        self.assertEqual(candidate['connectors'], [{'type': 'MENNEKES_TYPE_2', 'power_kw': 22}])
        row = station('registry-mennekes', lat=candidate['lat'], lon=candidate['lon'])
        row['raw']['chargemap_id'] = 304289
        self.assertEqual(p.distance(row, candidate), 0)
        self.assertTrue(p.compatible_connectors(row, candidate))
        self.assertIs(p.unique_match(candidate, [row], 'chargemap'), row)
        # Without a stable ID, require independent network and address agreement.
        row['raw'].pop('chargemap_id')
        self.assertIsNone(p.unique_match(candidate, [row], 'chargemap'))
        pool['network'] = {'name': 'Iberdrola'}
        candidate = p.sanitize_chargemap(pool)
        row['address'] = candidate['address']
        self.assertIs(p.unique_match(candidate, [row], 'chargemap'), row)
        row['address'] = 'Other street 99'
        self.assertIsNone(p.unique_match(candidate, [row], 'chargemap'))

    def test_unknown_chargemap_connectors_fail_closed_even_with_stable_id(self):
        pool = deepcopy(source_fixtures.SourceChargemapMappyTests.REAL_POOLS[1])
        row = station('registry-mennekes', lat=pool['gps_coordinates']['lat'], lon=pool['gps_coordinates']['lon'])
        row['raw']['chargemap_id'] = 304289
        for token in ('', '14', 'MENNEKES_TYPE_3', 'MENNEKES_TYPE_2_UNKNOWN', 'UNKNOWN'):
            with self.subTest(connector=token):
                pool['charging_connectors'][0]['type'] = token
                candidate = p.sanitize_chargemap(pool)
                self.assertEqual(p.connector_type(token), '')
                self.assertFalse(p.compatible_connectors(row, candidate))
                self.assertIsNone(p.unique_match(candidate, [row], 'chargemap'))


class CollectionTests(unittest.TestCase):
    def test_detail_budget_prioritizes_a_corroboratable_network_over_anonymous_names(self):
        source = load('electromaps.json')
        requests = []
        def opener(request, *, timeout):
            requests.append(request.full_url)
            if request.full_url.startswith(p.EM_BASE + '?'):
                return Response([dict(source, id=1, name='Anonymous car park'), dict(source, id=9)])
            if request.full_url.startswith(p.EM_BASE + '/'):
                return Response(dict(source, id=int(request.full_url.rsplit('/', 1)[-1])))
            return Response({})
        rows = [station(operator='IBERDROLA CLIENTES S.A.U')]
        with tempfile.TemporaryDirectory() as directory:
            p.Collector(directory, now=NOW, opener=opener, pause=0, detail_budget=1).enrich(rows)
        self.assertIn(p.EM_BASE + '/9', requests)
        self.assertNotIn(p.EM_BASE + '/1', requests)
        self.assertEqual(rows[0]['price_evidence'][0]['source_url'], p.EM_BASE + '/9')
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name)
        self.network = FixtureNetwork()
    def collect(self, rows=None, **kwargs):
        rows = fixture_stations() if rows is None else rows
        c = p.Collector(self.cache, now=kwargs.pop('now', NOW), opener=kwargs.pop('opener', self.network), pause=0, **kwargs)
        return rows, c.enrich(rows), c
    def test_sources_attach_to_existing_rows_only_and_leave_all_primary_fields(self):
        baseline = fixture_stations(); before = deepcopy(baseline)
        rows, report, _ = self.collect(baseline)
        self.assertEqual(report['counts'], dict(stations=3,total=3,repsol_network_tariff=1,electromaps_community=1,chargemap_pass=1))
        self.assertEqual([{k:v for k,v in row.items() if k!='price_evidence'} for row in rows], before)
        self.assertEqual(rows[1]['price_min'], 0.49)
        self.assertFalse(rows[0]['is_free'])
        self.assertEqual(report['requests'], 35)
        for req in self.network.calls:
            self.assertEqual(req.get_method(), 'GET')
            self.assertNotIn('Authorization', req.headers)
        for path in self.cache.rglob('*.json'):
            self.assertNotIn('must-not-persist', path.read_text())
    def test_real_chargemap_mennekes_pool_collects_price_without_donating_free_flag(self):
        pool = deepcopy(source_fixtures.SourceChargemapMappyTests.REAL_POOLS[1])
        lat, lon = pool['gps_coordinates']['lat'], pool['gps_coordinates']['lon']
        row = station('registry-mennekes', lat=lat, lon=lon)
        row['raw']['chargemap_id'] = pool['id']
        before = deepcopy(row)
        def opener(request, *, timeout):
            if request.full_url.startswith(p.CM_LIST):
                return Response({'response': {'content': {'items': [{'pool': pool}]}}})
            return self.network(request, timeout=timeout)
        with patch.object(p, 'SPAIN_TILES', ((lat-.01, lon-.01, lat+.01, lon+.01),)):
            rows, report, _ = self.collect([row], opener=opener)
        self.assertEqual(report['counts']['chargemap_pass'], 1)
        self.assertIn('/prices/304289?', rows[0]['price_evidence'][0]['source_url'])
        self.assertEqual({k:v for k,v in rows[0].items() if k != 'price_evidence'}, before)
        self.assertFalse(rows[0]['is_free'])
    def test_ttl_reuses_sanitized_cache_and_repsol_is_fetched_every_build(self):
        self.collect()
        self.network.calls.clear()
        rows, report, _ = self.collect(now=NOW + 6*86400)
        self.assertEqual(report['sources']['electromaps']['detail_cache_hits'], 1)
        self.assertEqual(report['sources']['chargemap']['detail_cache_hits'], 1)
        self.assertEqual(sum('/757380' in req.full_url or '/prices/315448?' in req.full_url for req in self.network.calls), 0)
        self.assertEqual(sum(req.full_url == p.REPSOL_URL for req in self.network.calls), 1)
        self.assertEqual(rows[0]['price_evidence'][0]['fetched_at'], FETCHED)
        self.network.calls.clear(); self.collect(now=NOW + 7*86400)
        self.assertEqual(sum('/757380' in req.full_url or '/prices/315448?' in req.full_url for req in self.network.calls), 2)
    def test_outage_uses_stale_cache_original_dates_but_withholds_repsol(self):
        self.collect()
        rows, report, _ = self.collect(now=NOW+8*86400, opener=FixtureNetwork(outage=True))
        self.assertEqual(report['counts']['total'], 2)
        self.assertNotIn('price_evidence', rows[2])
        for row in rows[:2]:
            self.assertTrue(row['price_evidence'][0]['cache_stale'])
            self.assertEqual(row['price_evidence'][0]['fetched_at'], FETCHED)
        self.assertEqual(rows[0]['price_evidence'][0]['source_updated_at'], '2025-08-16T19:12:42+0000')
    def test_no_cache_outage_is_no_evidence_and_no_lost_rows(self):
        baseline = fixture_stations(); before = deepcopy(baseline)
        rows, report, _ = self.collect(baseline, opener=FixtureNetwork(outage=True))
        self.assertEqual(rows, before)
        self.assertEqual(report['counts']['total'], 0)
    def test_changed_repsol_page_removes_old_guide_without_changing_station(self):
        rows, _, _ = self.collect()
        changed = FixtureNetwork()
        def opener(request, *, timeout):
            return Response('old guide removed') if request.full_url == p.REPSOL_URL else changed(request, timeout=timeout)
        rows, report, _ = self.collect(rows, opener=opener)
        self.assertNotIn('price_evidence', rows[2])
        self.assertEqual(report['counts']['repsol_network_tariff'], 0)
    def test_repsol_requires_registry_actual_legal_operator_not_name(self):
        rows = [station('lookalike', operator='OTHER COMPANY SA')]
        rows[0]['name'] = 'Repsol station'; rows[0]['raw']['repsol'] = True
        rows, report, _ = self.collect(rows)
        self.assertEqual(report['counts']['repsol_network_tariff'], 0)
    def test_repsol_withholds_network_guide_when_station_price_exists(self):
        priced = station('priced-repsol', operator='REPSOL SOLUCIONES ENERGÉTICAS SA', price=0.62)
        before = deepcopy(priced)
        rows, report, _ = self.collect([priced], list_budget=0, detail_budget=0)
        self.assertEqual(rows, [before])
        self.assertEqual(report['counts']['repsol_network_tariff'], 0)
    def test_all_tiles_are_ordered_and_cover_five_spanish_regions(self):
        self.assertEqual(len(p.SPAIN_TILES), 16)
        for s,w,n,e in p.SPAIN_TILES:
            self.assertLess(s,n); self.assertLess(w,e)
        for lat,lon in ((41.39,2.17),(39.57,2.65),(28.46,-16.25),(35.89,-5.32),(35.29,-2.94)):
            self.assertTrue(any(s<=lat<=n and w<=lon<=e for s,w,n,e in p.SPAIN_TILES))
        self.assertTrue(p.in_spain({'lat': 41.98, 'lon': 2.82, 'country_code': 'ES'}))
        self.assertFalse(any(s <= 41.98 <= n and w <= 2.82 <= e for s,w,n,e in p.SPAIN_TILES))
    def test_budget_and_priority_are_deterministic(self):
        # Eight matching detail candidates; only two GETs allowed, ordered by ID.
        source = load('electromaps.json')
        def opener(request, *, timeout):
            if request.full_url.startswith(p.EM_BASE+'?'):
                return Response([dict(source,id=i) for i in (9,3,7,1,8,2,6,4)])
            if request.full_url.startswith(p.EM_BASE+'/'):
                return Response(dict(source,id=int(request.full_url.rsplit('/',1)[-1])))
            if request.full_url.startswith(p.CM_LIST):
                return Response({})
            return Response(load('repsol.html'))
        rows, report, c = self.collect([station()], opener=opener, detail_budget=2)
        ids = [claim['source_url'].rsplit('/',1)[-1] for claim in rows[0]['price_evidence']]
        self.assertEqual(ids, ['1','2'])
        self.assertEqual(report['sources']['electromaps']['detail_requests'], 2)
        self.assertLessEqual(c.requests, p.TOTAL_BUDGET)
    def test_total_wall_and_size_bounds_withhold_without_erroring_build(self):
        collector = p.Collector(self.cache, now=NOW, opener=self.network, pause=0)
        collector.deadline = 0
        rows = fixture_stations(); report = collector.enrich(rows)
        self.assertEqual(report['requests'], 0)
        self.assertEqual(report['counts']['total'], 0)
        collector.requests = p.TOTAL_BUDGET
        collector.deadline = p.time.monotonic() + 180
        with self.assertRaises(RuntimeError):
            collector.request(p.REPSOL_URL, 'repsol', 'detail')
        self.assertEqual(self.network.calls, [])
        large = lambda request, timeout: Response('x' * (p.BODY_LIMIT+1))
        _, report, _ = self.collect(opener=large, list_budget=0, detail_budget=0)
        self.assertEqual(report['counts']['total'], 0)


class BuildTests(unittest.TestCase):
    def test_old_and_new_policy_keep_same_real_osm_membership_and_primary_prices(self):
        # Before annotations, these same exclusions had no permitted kinds.
        old_policy = deepcopy(ing.SOURCE_POLICY_EXCLUSIONS)
        for exclusion in old_policy['exclusions']:
            exclusion.pop('permitted_price_evidence_kinds', None)
        osm_body = json.dumps({'elements': source_fixtures.SourceOsmTests.REAL_ELEMENTS}).encode()
        with tempfile.TemporaryDirectory() as directory, patch.object(ing, 'CACHE', Path(directory)), \
                patch.object(ing, 'ACTIVE_SOURCE_ADAPTERS', (ing.source_osm,)), \
                patch.object(ing, 'now_iso', return_value=FETCHED), \
                patch.object(ing, 'cached_overpass', return_value=osm_body), \
                patch.object(ing, 'OVERPASS_TILE_PAUSE_SECONDS', 0), \
                patch.object(ing, 'enrich_price_evidence', return_value={}) as enrich, \
                patch.object(ing, 'dedupe', wraps=ing.dedupe) as dedupe, \
                patch.object(ing, 'merge_cross_source_duplicates', wraps=ing.merge_cross_source_duplicates) as merge, \
                redirect_stdout(io.StringIO()):
            with patch.object(ing, 'SOURCE_POLICY_EXCLUSIONS', old_policy):
                old = json.loads(json.dumps(ing.build_payload()))
            new = json.loads(json.dumps(ing.build_payload()))
        self.assertEqual(dedupe.call_count, 2)
        self.assertEqual(merge.call_count, 2)
        self.assertEqual(enrich.call_count, 2)
        self.assertEqual([r['name'] for r in old['records']], ['Artana'])
        self.assertEqual(new['records'], old['records'])
        self.assertEqual(new['source_policy_excluded_records'], 1)
        # The source parser still sees Utiel's original Electromaps/free evidence;
        # the final build policy must prevent that row from becoming a pin.
        parsed = dedupe.call_args.args[0]
        utiel = next(r for r in parsed if r['name'] == 'Ayuntamiento Utiel RAPIDO')
        self.assertEqual(utiel['raw']['source'], 'https://www.electromaps.com')
        self.assertEqual(utiel['price_min'], 0)
        self.assertTrue(utiel['is_free'])

    def test_exported_build_outage_preserves_all_station_rows(self):
        rows = fixture_stations()
        expected, _ = ing.merge_cross_source_duplicates(ing.dedupe(deepcopy(rows)))
        with tempfile.TemporaryDirectory() as directory, patch.object(ing, 'CACHE', Path(directory)), \
                patch.object(ing, 'ACTIVE_SOURCE_ADAPTERS', (lambda target: target.extend(deepcopy(rows)),)), \
                patch.object(ing, 'enrich_price_evidence', lambda target, cache, **kw: p.enrich_price_evidence(target, cache, now=NOW, pause=0, opener=FixtureNetwork(outage=True), **kw)), \
                redirect_stdout(io.StringIO()):
            payload = json.loads(json.dumps(ing.build_payload()))
        self.assertEqual(payload['records'], expected)
        self.assertEqual(payload['errors'], [])
        self.assertEqual(payload['price_evidence_counts']['total'], 0)

    def test_exported_build_runs_real_merge_then_adds_evidence_without_identity_or_price_changes(self):
        rows = fixture_stations()
        # An independent source conflicts with the official price at the same site.
        donor = deepcopy(rows[1]); donor.update(id='osm-price', source='OpenStreetMap public tags', source_url='https://www.openstreetmap.org/node/123', price_min=0.1, reliability='medium')
        donor['sources'] = [{'name':donor['source'],'url':donor['source_url']}]
        donor['charge_claim'] = dict(source=donor['source'], url=donor['source_url'], price_min=0.1, summary='0.10 EUR/kWh')
        donor['raw'] = {'operator':'Iberdrola'}
        rows.append(donor)
        # Two close registry IDs must remain distinct after the real merge order.
        rows.extend([station('same-family-a', lat=39.5, lon=-0.4), station('same-family-b', lat=39.50015, lon=-0.4)])
        # Compare actual old/new builds, including the old-directory OSM reproducer.
        old_policy = deepcopy(ing.SOURCE_POLICY_EXCLUSIONS)
        for exclusion in old_policy['exclusions']:
            exclusion.pop('permitted_price_evidence_kinds', None)
        osm_body = json.dumps({'elements': source_fixtures.SourceOsmTests.REAL_ELEMENTS}).encode()
        network = FixtureNetwork()
        with tempfile.TemporaryDirectory() as directory, patch.object(ing, 'CACHE', Path(directory)), \
                patch.object(ing, 'ACTIVE_SOURCE_ADAPTERS', (lambda target: target.extend(deepcopy(rows)), ing.source_osm)), \
                patch.object(ing, 'now_iso', return_value=FETCHED), \
                patch.object(ing, 'cached_overpass', return_value=osm_body), \
                patch.object(ing, 'OVERPASS_TILE_PAUSE_SECONDS', 0), \
                patch.object(p, 'public_price_urlopen', network), patch.object(p, 'PAUSE_SECONDS', 0), \
                patch.object(p.time, 'sleep'), redirect_stdout(io.StringIO()):
            with patch.object(ing, 'SOURCE_POLICY_EXCLUSIONS', old_policy), \
                    patch.object(ing, 'enrich_price_evidence', return_value={}):
                old = ing.build_payload()
            # direct/module imports can differ; bind actual collector with deterministic time.
            with patch.object(ing, 'enrich_price_evidence', lambda target, cache, **kw: p.enrich_price_evidence(target, cache, now=NOW, pause=0, **kw)):
                payload = ing.build_payload()
            exported = Path(directory)/'candidate.json'; exported.write_text(json.dumps(payload))
            actual = json.loads(exported.read_text())
        self.assertEqual(actual['cross_source_merged'], old['cross_source_merged'])
        self.assertEqual(actual['raw_records'], len(rows) + 2)
        self.assertEqual(actual['price_evidence_counts']['total'], 3)
        self.assertEqual(actual['price_evidence_collection']['requests'], 35)
        stripped = [{k:v for k,v in row.items() if k!='price_evidence'} for row in actual['records']]
        self.assertEqual(stripped, old['records'])
        self.assertEqual(actual['source_policy_excluded_records'], 1)
        by_id = {r['id']:r for r in actual['records']}
        self.assertIn('same-family-a', by_id); self.assertIn('same-family-b', by_id)
        self.assertEqual(by_id['registry-cm']['price_min'], 0.49)
        self.assertEqual(by_id['registry-cm']['claim_conflict'], next(r for r in old['records'] if r['id']=='registry-cm')['claim_conflict'])
        self.assertEqual(actual['errors'], [])
    def test_both_existing_merge_stages_preserve_additive_field_without_price_donation(self):
        a, b = station('nap'), station('osm')
        b.update(source='OpenStreetMap public tags', source_url='https://www.openstreetmap.org/node/124')
        b['sources']=[{'name':b['source'],'url':b['source_url']}]
        claim=p.parse_chargemap_price(load('chargemap.html'), FETCHED, '1'); b['price_evidence']=[claim]
        first = ing.dedupe([deepcopy(a),deepcopy(b)])
        self.assertEqual(first[0]['price_evidence'], [claim]); self.assertIsNone(first[0]['price_min'])
        b['lat'] += 0.0001
        merged, _ = ing.merge_cross_source_duplicates([a,b])
        self.assertEqual(merged[0]['price_evidence'], [claim]); self.assertIsNone(merged[0]['price_min'])
    def test_candidate_enrichment_does_not_mutate_baseline(self):
        baseline={'records':fixture_stations(),'total_records':3,'generated_at':FETCHED}
        before=deepcopy(baseline)
        with tempfile.TemporaryDirectory() as directory:
            result, report=enrich_payload(baseline, directory, now=NOW, opener=FixtureNetwork(), pause=0)
        self.assertEqual(baseline,before)
        self.assertEqual(result['price_evidence_counts']['total'],3)
        self.assertEqual(result['generated_at'],FETCHED)
        self.assertEqual(len(result['recordset_sha256']),64)


class TransportTests(unittest.TestCase):
    def test_exact_anonymous_get_and_no_redirect_opener(self):
        with patch.object(transport.urllib.request,'build_opener') as opener:
            transport.public_price_urlopen(__import__('urllib.request',fromlist=['Request']).Request(p.REPSOL_URL), timeout=12)
        self.assertIsInstance(opener.call_args.args[0], transport._NoRedirect)
    def test_unapproved_methods_paths_headers_and_inverted_bbox_are_blocked(self):
        Request=__import__('urllib.request',fromlist=['Request']).Request
        bad=[Request(p.REPSOL_URL,data=b''),Request(p.REPSOL_URL,headers={'Cookie':'session=x'}),
             Request(p.EM_BASE+'/1',headers={'Authorization':'Bearer token'}),
             Request('https://www.electromaps.com/login'),Request('https://pro.waylet.es/api/public/rmve/commerces/1'),
             Request(p.CM_LIST+'?NW=39.4%3B-0.3&SE=39.5%3B-0.4'),
             Request(p.CM_PRICE+'1?locale=es-es&noBootstrap=true&noFooter=true&token=x')]
        with patch.object(transport.urllib.request,'build_opener') as opener:
            for request in bad:
                with self.subTest(url=request.full_url), self.assertRaises(transport.SourcePolicyBlocked):
                    transport.public_price_urlopen(request,timeout=12)
            opener.assert_not_called()


if __name__=='__main__':
    unittest.main()
