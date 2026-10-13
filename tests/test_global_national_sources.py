#!/usr/bin/env python3
"""Schema fixtures verify normalization, not a live upstream integration claim."""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts import national_sources as national
from scripts.ingest_sources import FetchArtifact


def artifact(url: str = national.FRANCE_URL) -> FetchArtifact:
    return FetchArtifact(b"", url, "schema-fixture", "2026-10-13T00:00:00Z", 0, True, False, False)


def french_row(**overrides: object) -> dict:
    return dict({"id_pdc_itinerance": "FRABCEDC001", "id_station_itinerance": "FRABCP001",
                 "nom_station": "Example charge point", "coordonneesXY": "[2.3522,48.8566]",
                 "nom_operateur": "Example operator", "code_insee_commune": "75056", "date_maj": "2026-10-01",
                 "prise_type_2": "true", "prise_type_combo_ccs": "false", "puissance_nominale": "22",
                 "gratuit": "true", "tarification": "Gratuit pendant la première heure",
                 "condition_acces": "Accès réservé", "nbre_pdc": "4"}, **overrides)


class FranceIRVETests(unittest.TestCase):
    def test_explicit_geometry_and_connector_data_preserve_uncertainty(self) -> None:
        row = next(national.parse_france_irve([french_row()], artifact()))
        self.assertEqual((row["lat"], row["lon"], row["country_code"]), (48.8566, 2.3522, "FR"))
        self.assertEqual(row["connector_labels"], ["Type 2"])
        self.assertEqual(row["total_evse"], 1)  # nbre_pdc is station-wide, unlike this record.
        self.assertEqual(row["source_record_unit"], "charging_point")
        self.assertEqual(row["max_power_kw"], 22)
        self.assertIsNone(row["price_min"])
        self.assertFalse(row["is_free"])
        self.assertEqual(row["tariff"]["free_reported"], "true")
        self.assertEqual(row["status"], "VERIFY")
        self.assertEqual(row["connectors"][0]["status"], "UNKNOWN")
        self.assertFalse(row["source_integration"]["live_status_integrated"])
        self.assertEqual(row["sources"][0]["license"], "Licence Ouverte 2.0")
        self.assertIn("transport.data.gouv.fr", row["sources"][0]["attribution"])

    def test_missing_invalid_and_zero_coordinates_are_not_pins(self) -> None:
        for value in ("", "[]", "[0,0]", "[2,95]", "[NaN,45]", "[null,45]", "[2,45,4]", "bad"):
            with self.subTest(value=value):
                self.assertEqual(list(national.parse_france_irve([french_row(coordonneesXY=value)], artifact())), [])

    def test_consolidated_and_v3_coordinates_and_overseas_country(self) -> None:
        rows = list(national.parse_france_irve([
            french_row(coordonneesXY="", Xlongitude="55.45", Ylatitude="-20.89", code_insee_commune="97411"),
            french_row(id_pdc_itinerance="v3", coordonneesXY="", station_longitude="2.1", station_latitude="48.2"),
        ], artifact()))
        self.assertEqual(rows[0]["country_code"], "RE")
        self.assertEqual(rows[0]["lat"], -20.89)
        self.assertEqual(rows[1]["lat"], 48.2)

    def test_local_identifiers_are_namespaced_and_missing_identity_rejected(self) -> None:
        rows = list(national.parse_france_irve([
            french_row(id_pdc_itinerance="", id_pdc_local="12", nom_operateur="Operator A"),
            french_row(id_pdc_itinerance="", id_pdc_local="12", nom_operateur="Operator B"),
            french_row(id_pdc_itinerance="", id_pdc_local=""),
        ], artifact()))
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["id"], rows[1]["id"])

    def test_an_excluded_source_cannot_enter_through_a_national_dataset(self) -> None:
        self.assertEqual(list(national.parse_france_irve([french_row(source="Chargemap")], artifact())), [])
        # The repository policy excludes evidence origins, not plain operators.
        self.assertEqual(len(list(national.parse_france_irve([french_row(nom_operateur="Chargemap")], artifact()))), 1)


GERMAN_ROW = {"Betreiber": "Example Stadtwerke", "Straße": "Hauptstraße", "Hausnummer": "8",
              "Postleitzahl": "10115", "Ort": "Berlin", "Breitengrad": "52,5200", "Längengrad": "13,4050",
              "Inbetriebnahmedatum": "01.10.2020", "Anzahl Ladepunkte": "2",
              "Steckertypen1": "DC Kupplung Combo, AC Steckdose Typ 2", "P1 [kW]": "50,0",
              "Steckertypen2": "AC Steckdose Typ 2", "P2 [kW]": "22,0"}


class GermanyTests(unittest.TestCase):
    def test_decimal_comma_multiple_connector_types_and_physical_point_count(self) -> None:
        row = next(national.parse_germany_bnetza([GERMAN_ROW], artifact(national.GERMANY_CSV_URL)))
        self.assertEqual((row["lat"], row["lon"], row["country_code"]), (52.52, 13.405, "DE"))
        self.assertEqual(row["address"], "Hauptstraße 8")
        self.assertEqual(row["connector_labels"], ["CCS2", "Type 2"])
        self.assertEqual(row["total_evse"], 2)
        self.assertEqual(row["max_power_kw"], 50)
        self.assertIsNone(row["price_min"])
        self.assertEqual(row["sources"][0]["attribution"], "Bundesnetzagentur.de")
        self.assertTrue(all(conn["status"] == "UNKNOWN" for conn in row["connectors"]))

    def test_identity_is_stable_across_csv_order_and_coordinate_errors_skip(self) -> None:
        first = next(national.parse_germany_bnetza([GERMAN_ROW], artifact()))
        second = next(national.parse_germany_bnetza([dict(reversed(list(GERMAN_ROW.items())))], artifact()))
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(list(national.parse_germany_bnetza([dict(GERMAN_ROW, Breitengrad="invalid")], artifact())), [])

    def test_current_month_discovery_uses_only_official_csv_host(self) -> None:
        newer = national.GERMANY_CSV_URL.replace("2026-09-01", "2026-10-01")
        evil = newer.replace("data.bundesnetzagentur.de", "data.bundesnetzagentur.de.example.com")
        page = f'<a href="{national.GERMANY_CSV_URL}">CSV</a><a href="{newer}">CSV</a><a href="{evil}">bad</a>'
        self.assertEqual(national.resolve_germany_csv(page), newer)
        with self.assertRaises(ValueError):
            national.resolve_germany_csv(f'<a href="{evil}">CSV</a>')

    def test_csv_preamble_bom_delimiter_and_windows_encoding(self) -> None:
        out = io.StringIO()
        out.write("Ladesäulenregister\nStand: Oktober 2026\n")
        writer = csv.DictWriter(out, fieldnames=list(GERMAN_ROW), delimiter=";")
        writer.writeheader()
        writer.writerow(GERMAN_ROW)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "register.csv"
            path.write_bytes(out.getvalue().encode("cp1252"))
            rows = list(national._csv_file(path, required=("Betreiber", "Breitengrad", "Längengrad")))
        self.assertEqual(rows[0]["Straße"], "Hauptstraße")
        with self.assertRaises(ValueError):
            list(national.csv_rows(io.StringIO("not,a,charger,CSV\n"), required=("Betreiber",)))


HK_PAYLOAD = {"code": 200, "data": {"geoLocationResult": [
    {"itemId": "example-1", "title": "Example CLP station", "latitude": 22.3193, "longitude": 114.1694,
     "provider": "CLP", "detailedAddress": "Example Hong Kong address", "status": "AVAILABLE",
     "totalNumberOfCharger": 2, "lastUpdate": "2026-10-01", "chargerList": [
         {"cpNo": "01", "cpStatus": "AVAILABLE", "chargerType": "Quick", "lastUpdate": "2026-10-01"},
         {"cpNo": "02", "cpStatus": "AVAILABLE", "chargerType": "SemiQuick", "lastUpdate": "2026-10-01"},
     ]}
]}}


class HongKongTests(unittest.TestCase):
    def test_speed_tier_and_cached_status_do_not_invent_a_connector_or_live(self) -> None:
        row = next(national.parse_hk_clp(HK_PAYLOAD, artifact(national.HK_CLP_URL)))
        self.assertEqual(row["country_code"], "HK")
        self.assertEqual(row["price_currency"], "HKD")
        self.assertEqual(row["total_evse"], 2)
        self.assertEqual(row["connector_labels"], [])
        self.assertIsNone(row["max_power_kw"])
        self.assertIsNone(row["price_min"])
        self.assertEqual(row["status"], "VERIFY")
        self.assertEqual(row["raw"]["chargerList"][0]["cpStatus"], "AVAILABLE")

    def test_schema_and_error_responses_fail_loudly(self) -> None:
        for payload in ({"code": 500}, {"code": 200, "data": {}}, [], None):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                list(national.parse_hk_clp(payload, artifact()))


def afdc_row(**overrides: object) -> dict:
    return dict({"id": 446568, "fuel_type_code": "ELEC", "country": "US", "latitude": 40.1522439,
                 "longitude": -77.6125612, "access_code": "public", "status_code": "E", "restricted_access": False,
                 "station_name": "Example AFDC station", "city": "Newburg", "ev_network": "Example network",
                 "ev_pricing": "$0.40 per kWh", "updated_at": "2026-10-01T00:00:00Z", "geocode_status": "GPS",
                 "ev_connector_types": ["J1772COMBO"], "ev_dc_fast_num": 2,
                 "ev_charging_units": [{"port_count": 2, "connectors": {
                     "J1772COMBO": {"power_kw": 150, "port_count": 2}, "TESLA": {"power_kw": None, "port_count": 0}}}]},
                **overrides)


class _Response(io.BytesIO):
    def __init__(self, body: bytes) -> None:
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}


class AFDCTests(unittest.TestCase):
    def test_us_ca_currencies_connector_families_and_access_remain_distinct(self) -> None:
        payload = {"total_results": 2, "fuel_stations": [
            afdc_row(), afdc_row(id=22, country="CA", latitude=45.4, longitude=-75.7, ev_pricing="$0.50 per kWh",
                                 restricted_access=True, access_detail_code="LIMITED_HOURS", ev_charging_units=[],
                                 ev_connector_types=["TESLA", "J1772"], geocode_status="200-6"),
        ]}
        us, ca = list(national.parse_afdc(payload, artifact(national.AFDC_URL)))
        self.assertEqual((us["country_code"], us["price_currency"]), ("US", "USD"))
        self.assertEqual((ca["country_code"], ca["price_currency"]), ("CA", "CAD"))
        self.assertEqual(us["connector_labels"], ["CCS1"])
        self.assertEqual(ca["connector_labels"], ["NACS", "Type 1"])
        self.assertEqual(us["max_power_kw"], 150)
        self.assertEqual(us["total_evse"], 2)
        self.assertEqual(ca["access"]["level"], "restricted")
        self.assertEqual(ca["location_claim"]["precision"], "approximate")
        for row in (us, ca):
            self.assertIsNone(row["price_min"])
            self.assertIsNone(row["tariff"]["amount"])
            self.assertTrue(row["tariff"]["raw_text"].startswith("$"))
            self.assertEqual(row["status"], "VERIFY")
            self.assertFalse(row["source_integration"]["live_status_integrated"])
            self.assertTrue(all(conn["status"] == "UNKNOWN" for conn in row["connectors"]))

    def test_planned_private_nonelectric_and_unknown_country_rows_are_rejected(self) -> None:
        raw = [afdc_row(status_code="P"), afdc_row(access_code="private"), afdc_row(country=""), afdc_row(fuel_type_code="CNG")]
        self.assertEqual(list(national.parse_afdc({"total_results": len(raw), "fuel_stations": raw}, artifact())), [])

    def test_truncated_all_query_is_not_a_complete_catalog(self) -> None:
        with self.assertRaises(ValueError):
            list(national.parse_afdc({"total_results": 200, "fuel_stations": [afdc_row()]}, artifact()))

    def test_missing_key_returns_an_explicit_gate_without_fetching(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True), patch.object(national, "_fetch_afdc") as fetch:
            report = national.run_source("afdc", Path(temporary), None, False, lambda _row: "added")
        fetch.assert_not_called()
        self.assertEqual(report["status"], "needs_credentials")
        self.assertEqual(report["seen"], 0)
        self.assertEqual(report["accepted"], 0)

    def test_credential_is_only_an_unredirected_header_not_evidence_or_cache(self) -> None:
        credential = "fixture-afdc-key-not-a-real-credential"
        body = json.dumps({"total_results": 1, "fuel_stations": [afdc_row()]}).encode()
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"NLR_API_KEY": credential}, clear=True), patch("urllib.request.build_opener") as build:
            build.return_value.open.return_value = _Response(body)
            rows = []
            report = national.run_source("afdc", Path(temporary), {"US"}, False, lambda row: rows.append(row) or "added")
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(request.full_url, national.AFDC_URL)
            self.assertEqual(request.unredirected_hdrs["X-api-key"], credential)
            self.assertNotIn("X-api-key", request.headers)
            self.assertIsInstance(build.call_args.args[0], national._AFDCNoRedirect)
            self.assertNotIn(credential, json.dumps({"report": report, "rows": rows}))
            self.assertNotIn(credential.encode(), (Path(temporary) / "afdc_us_ca.json").read_bytes())
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["accepted"], 1)

    def test_redirects_are_rejected_and_upstream_error_text_is_not_logged(self) -> None:
        credential = "fixture-afdc-key-not-a-real-credential"
        with self.assertRaises(urllib.error.HTTPError):
            national._AFDCNoRedirect().redirect_request(urllib.request.Request(national.AFDC_URL), None, 302, "redirect", {}, "https://other.example/")
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"NREL_API_KEY": credential}, clear=True), patch("urllib.request.build_opener") as build:
            build.return_value.open.side_effect = urllib.error.URLError(f"upstream echoed {credential}")
            report = national.run_source("afdc", Path(temporary), None, False, lambda _row: "added")
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["errors"], ["URLError"])
        self.assertNotIn(credential, json.dumps(report))

    def test_echoed_credentials_cannot_poison_the_cache(self) -> None:
        credential = "fixture-afdc-key-not-a-real-credential"
        body = json.dumps({"total_results": 1, "fuel_stations": [afdc_row()], "echo": credential}).encode()
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"NLR_API_KEY": credential}, clear=True), patch("urllib.request.build_opener") as build:
            build.return_value.open.return_value = _Response(body)
            report = national.run_source("afdc", Path(temporary), None, False, lambda _row: "added")
            self.assertEqual(list(Path(temporary).iterdir()), [])
        self.assertEqual(report["status"], "failed")
        self.assertNotIn(credential, json.dumps(report))


class OrchestrationTests(unittest.TestCase):
    def test_offline_valid_cache_preserves_scope_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            (cache / "hk_clp.json").write_text(json.dumps(HK_PAYLOAD))
            rows = []
            with patch("urllib.request.urlopen", side_effect=AssertionError("Offline means no requests")):
                report = national.run_source("hk_clp", cache, {"HK"}, True, lambda row: rows.append(row) or "added")
            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["records"], 1)
            self.assertEqual(report["accepted"], 1)
            self.assertEqual(report["seen"], 1)
            self.assertEqual(rows[0]["source_fetch"]["cache_name"], "hk_clp.json")
            self.assertEqual(national.run_source("hk_clp", cache, {"DE"}, True, rows.append)["status"], "not_applicable")

    def test_missing_cache_is_failure_and_never_successful_empty_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = national.run_source("france_irve", Path(temporary), None, True, lambda _row: None)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error"], "FileNotFoundError")
        self.assertEqual(report["records"], 0)

    def test_new_france_endpoint_and_new_clp_endpoint_are_selected(self) -> None:
        self.assertIn("consolidation-transport-irve-statique", national.FRANCE_URL)
        self.assertTrue(national.HK_CLP_URL.endswith("/mgmtEVChargeInfra/v1/getEVChargerList"))

    def test_german_dated_cache_retains_the_actual_monthly_source_url(self) -> None:
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=list(GERMAN_ROW), delimiter=";")
        writer.writeheader()
        writer.writerow(GERMAN_ROW)
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            (cache / "germany_bnetza_2026-10-01.csv").write_text(out.getvalue())
            report = national.run_source("germany_bnetza", cache, {"DE"}, True, lambda _row: "added")
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["accepted"], 1)
        self.assertTrue(report["source_url"].endswith("Ladesaeulenregister_BNetzA_2026-10-01.csv"))


if __name__ == "__main__":
    unittest.main()
