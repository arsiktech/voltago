"""Source-backed operator fixtures and append-only worldwide contracts."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from scripts import operator_sources as op, global_sources as gs


def artifact():
    return gs.FetchArtifact(b"", op.ATLANTE_FEED_URL, "fixture.json", "2026-10-01T12:00:00Z", 0, True, False, False)

ETECNIC_ROWS = [
    {"id": 1, "name": "Pescador Sud - EdRSR 1", "status": 0, "power": 22.0, "address": "Carrer Robert Gerhard, 2",
     "lat": "41.064699", "lon": "1.0576201", "phases": 3, "test_errors": True, "mobile_app_manage": True, "show_map": True,
     "charger_sockets": [{"id": 1, "socket_number": 1, "status": 0, "connector_type_id": 2},
                         {"id": 2, "socket_number": 2, "status": 0, "connector_type_id": 2}]},
    {"id": 2, "name": "Palma - MELIB 1", "status": 1, "power": 50.0, "address": "Carrer X", "lat": "39.57", "lon": "2.65",
     "show_map": True, "charger_sockets": [{"id": 3, "status": 1, "connector_type_id": 1},
                                           {"id": 4, "status": 1, "connector_type_id": 3},
                                           {"id": 5, "status": 1, "connector_type_id": 7}]},
    {"id": 3, "name": "Hidden", "lat": "40.4", "lon": "-3.7", "show_map": False, "charger_sockets": []},
    {"id": 4, "name": "Hotel Metropolis", "lat": "42.51035792", "lon": "1.54068426", "show_map": True, "charger_sockets": []},
    {"id": 5, "name": "Figueres - SELBA EdRSR 3", "lat": "0.0", "lon": "0.0", "show_map": True, "charger_sockets": []},
    {"id": 6, "name": "SANTO DOMINGO GBT 1", "lat": "25.750424", "lon": "-100.255821", "show_map": True, "charger_sockets": []},
]

def atlante_location(loc_id, country, status, lat, lon):
    return {
        "id": loc_id, "party_id": "ATE", "name": "Atlante  - Abanto Gallarta", "street": "Plaza de Eusko Gudariak",
        "city": "Abanto y Ciérvana-Abanto Zierbena", "postal_code": "48500", "country": country, "site_status": status,
        "last_updated": "2026-10-12 03:27:31",
        "evses": [
            {"evse_id": "ES*ATE*E00026*1", "status": "AVAILABLE", "coordinates": {"latitude": lat, "longitude": lon},
             "connectors": [{"id": "1", "standard": "IEC_62196_T2_COMBO", "format": "CABLE", "power_type": "DC", "max_electric_power": 60000}]},
            {"evse_id": "ES*ATE*E00026*2", "status": "CHARGING", "coordinates": {"latitude": lat, "longitude": lon},
             "connectors": [{"id": "2", "standard": "CHADEMO", "format": "CABLE", "power_type": "DC", "max_electric_power": 50000}]},
        ],
    }

ZUNDER_PAGE = """
<script>
 var marker4 = new google.maps.Marker({
   position: { lat: 42.71779934, lng: -4.29173329},
   map: map,
   title: 'Alar del Rey - Puerta de la Monta\\'ña',
   icon: { url: '/wp-content/plugins/easycharger/icons/icono-marino.png' }
 });
 marker4.addListener('click', function() {
   xmlhttp.open("GET", "/wp-content/plugins/easycharger/location.php?id=4");
 });
 var marker5 = new google.maps.Marker({
   position: { lat: 41.15, lng: -8.61},
   map: map,
   title: 'ZUNDER Porto',
 });
 marker5.addListener('click', function() {
   xmlhttp.open("GET", "/wp-content/plugins/easycharger/location.php?id=5");
 });
 var marker6 = new google.maps.Marker({ map: map });
</script>
"""


class OperatorSourceTests(unittest.TestCase):
    def test_etecnic_visible_rows_keep_unknown_hardware_and_stable_ids(self):
        rows = op.parse_etecnic_chargers(ETECNIC_ROWS, source_artifact=artifact())
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], "global_etecnic_1")
        self.assertEqual(rows[0]["connector_labels"], [])
        self.assertIsNone(rows[0]["max_power_kw"])
        self.assertIsNone(rows[0]["price_min"])
        self.assertIsNone(rows[0]["tesla_ok"])
        renamed = dict(ETECNIC_ROWS[0], name="New name")
        self.assertEqual(op.parse_etecnic_chargers([renamed], source_artifact=artifact())[0]["id"], rows[0]["id"])

    def test_atlante_retains_four_countries_and_does_not_invent_live_status(self):
        cases = [("ES", 40.4, -3.7), ("FR", 48.8, 2.3), ("IT", 41.9, 12.5), ("PT", 38.7, -9.1)]
        rows = op.parse_atlante_locations([atlante_location(country, country, "OPERATIONAL", lat, lon)
                                         for country, lat, lon in cases], source_artifact=artifact())
        self.assertEqual({r["country_code"] for r in rows}, {"ES", "FR", "IT", "PT"})
        for row in rows:
            self.assertEqual(row["max_power_kw"], 60)
            self.assertEqual(row["connector_labels"], ["CCS2", "CHAdeMO"])
            self.assertFalse(row["source_integration"]["live_status_integrated"])
            self.assertTrue(all(c["status"] == "UNKNOWN" for c in row["connectors"]))
            self.assertIsNone(row["price_min"])
        self.assertEqual(op.ocpi_power_kw({"max_electric_power": 500}), .5)

    def test_hidden_and_unfinished_atlante_sites_are_not_admitted(self):
        rows = [atlante_location("one", "ES", "UNDER CONSTRUCTION", 40.4, -3.7),
                atlante_location("two", "ES", "WAITING FOR CONNECTION", 40.4, -3.7)]
        self.assertEqual(op.parse_atlante_locations(rows, source_artifact=artifact()), [])

    def test_zunder_filters_country_without_guessing_connectors(self):
        rows = op.parse_zunder_map_page(ZUNDER_PAGE, source_artifact=artifact())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "global_zunder_4")
        self.assertEqual(rows[0]["connector_labels"], [])

    def test_interparking_flag_and_parking_price_never_become_charge_tariff(self):
        park = {"externalId": "es-123", "name": "Parking", "location": {"lat": 40.4, "lng": -3.7},
                "facilities": [{"id": "Electric charging station"}], "price": "0.00 EUR/hour"}
        rows = op.parse_interparking_car_parks([park, dict(park, externalId="pt-1"), dict(park, facilities=[])], source_artifact=artifact())
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["price_min"])
        self.assertFalse(rows[0]["is_free"])
        self.assertEqual(rows[0]["location_claim"]["precision"], "parking_coordinate")

    def test_municipal_sources_keep_identity_and_original_acquisition_date(self):
        feature = {"type": "Feature", "properties": {"id": 42, "name": "Parking Reus", "layer": op.MOT_REUS_LAYER},
                   "geometry": {"type": "Point", "coordinates": [1.1, 41.15]}}
        rows = op.parse_mot_reus_geojson({"features": [feature]}, source_artifact=artifact())
        self.assertEqual(rows[0]["id"], "global_mot_reus_42")
        csv = "NOM,LATITUD,LONGITUD,STATUS,AMPERATGE\nParking Tarragona,41.12,1.24,Actiu,32\n"
        row = op.parse_mot_tarragona_csv(csv, source_artifact=artifact())[0]
        self.assertIsNone(row["max_power_kw"])
        self.assertEqual(row["source_fetched_at"], artifact().fetched_at)

    def test_malformed_feed_does_not_emit_valid_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"feed.json"
            path.write_text(json.dumps([ETECNIC_ROWS[0], dict(ETECNIC_ROWS[0], id=None)]))
            with patch.object(op.gs, "fetch_cached_file", return_value=gs.FileArtifact(path, artifact())):
                rows = []
                report = op.run_source("etecnic", Path(tmp), {"ES"}, False, lambda row: rows.append(row) or "added")
            self.assertEqual(report["status"], "failed")
            self.assertEqual(rows, [])

    def test_missing_cache_or_other_country_is_not_successful_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(op.run_source("zunder", Path(tmp), {"US"}, True, lambda row: "added")["status"], "not_applicable")
            self.assertEqual(op.run_source("zunder", Path(tmp), {"ES"}, True, lambda row: "added")["status"], "failed")

    def test_repeated_addition_preserves_baseline_evidence_and_id(self):
        baseline = op.parse_etecnic_chargers([ETECNIC_ROWS[0]], source_artifact=artifact())[0]
        newer = deepcopy(baseline)
        newer.update(name="New name", source_fetched_at="2026-10-13T12:00:00Z")
        with tempfile.TemporaryDirectory() as tmp:
            store = gs.CandidateStore(Path(tmp)/"candidate.sqlite")
            try:
                store.add(baseline, baseline=True)
                self.assertEqual(store.add(newer), "duplicate")
                self.assertEqual(list(store.rows()), [baseline])
            finally:
                store.close()

    def test_operator_coordinates_render_without_live_or_price_authority(self):
        import server
        from station_truth import primary_source_for
        evidence = gs.FetchArtifact(b"", op.ETECNIC_FEED_URL, "etecnic_chargers.json", artifact().fetched_at, 0, True, False, False)
        row = op.parse_etecnic_chargers([ETECNIC_ROWS[0]], source_artifact=evidence)[0]
        normalized = server._normalize_lead(deepcopy(row), row["source"])
        self.assertTrue(server.source_health_map_eligible(normalized))
        self.assertFalse(normalized["verification"]["live_verified"])
        self.assertFalse(normalized["verification"]["price_verified"])
        self.assertNotEqual(normalized["decision"]["level"], "go")
        for field, replacement in (("source_adapter", "unknown"), ("source_fetch", {}), ("raw", {}),
                                   ("lat", float("nan")), ("source_url", row["source_url"] + "?id=1")):
            altered = deepcopy(row)
            altered[field] = replacement
            self.assertNotEqual((primary_source_for(altered) or {}).get("kind"), "official_data", field)

    def test_catalog_counts_new_adapters_and_retains_prior_collection(self):
        import server
        from scripts.build_global_intelligence import retained_manifests
        row = op.parse_etecnic_chargers([ETECNIC_ROWS[0]], source_artifact=artifact())[0]
        old = {"source": "ocm", "scope": "all-countries", "status": "ok", "accepted": 1}
        new = {"source": "etecnic", "scope": ["ES"], "status": "failed", "accepted": 0}
        baseline = {"global_coverage": {"source_manifests": [old]}}
        merged = retained_manifests(baseline, [new])
        self.assertEqual(merged, [old, new])
        meta = {"records": [row], "global_coverage": {"source_manifests": merged}}
        with patch.object(server, "load_intelligence_meta", return_value=meta):
            catalog = {a["id"]: a for a in server.global_adapter_catalog()}
        self.assertEqual(catalog["etecnic"]["records_added_by_global_adapter"], 1)
        self.assertEqual(catalog["etecnic"]["status"], "failed")
        self.assertEqual(catalog["ocm"]["status"], "ok")

    def test_late_adapter_failure_rolls_back_rows_and_counters(self):
        from scripts.build_global_intelligence import run_directory
        row = op.parse_etecnic_chargers([ETECNIC_ROWS[0]], source_artifact=artifact())[0]
        def partial(source, cache, countries, offline, emit):
            emit(row)
            return {"source": source, "status": "failed", "accepted": 1, "duplicates": 0}
        with tempfile.TemporaryDirectory() as tmp:
            store = gs.CandidateStore(Path(tmp)/"candidate.sqlite")
            try:
                before = store.stats()
                args = SimpleNamespace(cache_dir=Path(tmp), offline=True)
                result = run_directory(SimpleNamespace(run_source=partial), "etecnic", args, {"ES"}, store)
                self.assertEqual(result["accepted"], 0)
                self.assertEqual(result["rolled_back_records"], 1)
                self.assertEqual(store.stats(), before)
                self.assertEqual(list(store.rows()), [])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
