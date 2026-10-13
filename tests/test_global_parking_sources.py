"""Offline public-page fixtures: age, geographic scope and fail-closed intake."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts import public_parking_sources as source
from scripts import source_public_station_pages as pages
from scripts.ingest_sources import FetchArtifact


DETAIL_URL = "https://www.telpark.com/es/ciudades/madrid/example-parking/"


def artifact(body: bytes, url: str, *, fetched_at: str = "2026-10-01T12:00:00Z", stale=True) -> FetchArtifact:
    return FetchArtifact(body, url, source.cache_name_for_url(url), fetched_at, 100_000, True, False, stale)


def parking_item(native_id=7, url=DETAIL_URL) -> dict:
    return {"id": native_id, "link": url, "title": {"rendered": "Example parking"},
            "meta_box": {"header": {"address": "Example 1, Madrid, 28001", "latitude": "40.4",
                                    "longitude": "-3.7", "prize": "<p>2 EUR/hour parking</p>"}}}


def parking_node(**overrides) -> dict:
    return dict({"@type": "ParkingFacility", "name": "Example parking", "telephone": "+34 123456789",
                 "geo": {"latitude": 40.4379, "longitude": -3.7196},
                 "address": {"addressCountry": "Aparcar en España", "streetAddress": "Example 1"},
                 "amenityFeature": [{"name": "Carga eléctrica", "value": True}]}, **overrides)


def jsonld(*nodes) -> bytes:
    return ('<script type="application/ld+json">' + json.dumps({"@graph": list(nodes)}) + "</script>").encode()


def station(lat, lon, name, address, connectors="2 x 50kW DC (CHAdeMO, CCS)", network="partner") -> str:
    return (f'<li data-latitude="{lat}" data-longitude="{lon}" data-type="{network}">'
            f'<h1><a href="https://www.google.com/maps">{name}</a></h1>'
            f'<p class="address">{address}</p><p class="connector-information">{connectors}</p></li>')


EE = station(59.43, 24.75, "Tallinn station (0011)", "Example tee 1", "2 x 22kW AC (Type 2)")
LV = station(55.8784, 26.5144, "Daugavpils station (0012)", "Stadiona iela 1")
LT = station(54.687, 25.279, "Vilnius station", "Gedimino pr. 1", "connector information unknown")


class TelparkParsingTests(unittest.TestCase):
    def record(self, *nodes):
        item = pages.parse_telpark_parkings_page([parking_item()])[0]
        detail = jsonld(*nodes)
        listing = artifact(b"[]", source.TELPARK_LIST_URL.format(page=1), fetched_at="2026-10-10T12:00:00Z", stale=False)
        return source.parse_telpark_record(item, detail, listing, artifact(detail, DETAIL_URL))

    def test_old_detail_age_survives_new_list_and_parking_price_is_not_charging(self):
        record = self.record(parking_node())
        self.assertEqual(record["id"], "global_telpark_7")
        self.assertEqual((record["lat"], record["lon"], record["country_code"]), (40.4379, -3.7196, "ES"))
        self.assertEqual(record["source_fetched_at"], "2026-10-01T12:00:00Z")
        self.assertTrue(record["source_stale"])
        self.assertEqual(len(record["raw"]["source_fetch_artifacts"]), 2)
        self.assertEqual(record["raw"]["telpark_detail_url"], DETAIL_URL)
        self.assertEqual(record["raw"]["country"], "ES")
        self.assertEqual(record["source_record_unit"], "parking_facility")
        self.assertIsNone(record["price_min"])
        self.assertIsNone(record["tariff"]["amount"])
        self.assertFalse(record["is_free"])
        self.assertIn("2 EUR/hour", record["parking_claim"]["cost_hint"])
        self.assertEqual(record["connector_labels"], [])
        self.assertIsNone(record["max_power_kw"])
        self.assertNotIn("available", record)
        self.assertEqual(record["status"], "VERIFY")
        self.assertFalse(record["source_integration"]["live_status_integrated"])
        self.assertEqual(record["location_claim"]["precision"], "official_parking_coordinate")

    def test_organization_offer_geo_or_other_parking_cannot_confirm_this_facility(self):
        org = {"@type": "Organization", "amenityFeature": [{"name": "Carga eléctrica", "value": True}],
               "geo": {"latitude": 40.4, "longitude": -3.7}, "address": {"addressCountry": "ES"}}
        for nodes in ((org,), (org, parking_node(amenityFeature=[])),
                      (parking_node(geo={}), org), (parking_node(), parking_node(name="Other parking"))):
            with self.subTest(nodes=nodes):
                self.assertIsNone(self.record(*nodes))

    def test_explicit_country_flag_and_coordinates_are_required(self):
        for changes in ({"address": {}}, {"address": {"addressCountry": "PT"}}, {"telephone": "+351 1234"},
                        {"geo": {}}, {"geo": {"latitude": 95, "longitude": 0}},
                        {"amenityFeature": [{"name": "Carga eléctrica", "value": False}]},
                        {"amenityFeature": [{"name": "Carga eléctrica", "value": True},
                                            {"name": "Carga eléctrica", "value": False}]}):
            with self.subTest(changes=changes):
                self.assertIsNone(self.record(parking_node(**changes)))

    def test_detail_provenance_mismatch_and_timezone_missing_are_errors(self):
        item = pages.parse_telpark_parkings_page([parking_item()])[0]
        listing = artifact(b"[]", source.TELPARK_LIST_URL.format(page=1))
        body = jsonld(parking_node())
        for detail in (artifact(body, source.ELEKTRUM_URL),
                       artifact(body, DETAIL_URL, fetched_at="2026-10-01T12:00:00")):
            with self.subTest(detail=detail.url), self.assertRaises(ValueError):
                source.parse_telpark_record(item, body, listing, detail)


class ElektrumParsingTests(unittest.TestCase):
    def test_baltic_countries_plugs_and_power_preserve_directory_uncertainty(self):
        body = (EE + LV + LT).encode()
        records = source.parse_elektrum_records(body, artifact(body, source.ELEKTRUM_URL))
        self.assertEqual([row["country_code"] for row in records], ["EE", "LV", "LT"])
        self.assertEqual(records[0]["connector_labels"], ["Type 2"])
        self.assertEqual(records[0]["max_power_kw"], 22)
        self.assertEqual(records[1]["connector_labels"], ["CCS2", "CHAdeMO"])
        self.assertEqual(records[1]["max_power_kw"], 50)
        self.assertEqual(records[2]["connector_labels"], [])
        self.assertIsNone(records[2]["max_power_kw"])
        self.assertEqual(records[1]["raw"]["country_method"], "address_hint")
        for row in records:
            self.assertNotIn("available", row)
            self.assertIsNone(row["price_min"])
            self.assertIsNone(row["total_evse"])
            self.assertIsNone(row["connector_count"])
            self.assertEqual(row["status"], "VERIFY")
            self.assertTrue(all(connector["status"] == "UNKNOWN" for connector in row["connectors"]))
        self.assertTrue(all(connector["count"] is None for connector in records[1]["connectors"]))

    def test_reordering_duplicates_and_station_rename_do_not_change_native_id(self):
        first = pages.parse_elektrum_public_points(EE + LV + EE + LT)
        second = pages.parse_elektrum_public_points(LT + LV + EE.replace("Tallinn station", "Renamed station"))
        self.assertEqual(len(first), 3)
        self.assertEqual({row["source_id"] for row in first}, {row["source_id"] for row in second})
        self.assertEqual(first[0]["identity_method"], "upstream_station_code")
        self.assertEqual(first[2]["identity_method"], "derived_name_coordinate")

    def test_conflicting_native_id_or_truncated_station_rejects_whole_page(self):
        for body in (EE + EE.replace('data-latitude="59.43"', 'data-latitude="59.5"'), EE + LV[:-5]):
            with self.subTest(body=body[-30:]), self.assertRaises(ValueError):
                pages.parse_elektrum_public_points(body)

    def test_country_ambiguity_and_outside_baltics_are_not_guessed(self):
        body = (station(57.7, 24, "Border station", "Example address")
                + station(40.4, -3.7, "Spanish station", "Calle 1")).encode()
        self.assertEqual(source.parse_elektrum_records(body, artifact(body, source.ELEKTRUM_URL)), [])


class _Response(io.BytesIO):
    def __init__(self, body, expected=None):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body) if expected is None else expected)}


class TransportAndCollectionTests(unittest.TestCase):
    def cached(self, directory, url, body, age=3 * 86400):
        path = Path(directory) / source.cache_name_for_url(url)
        path.write_bytes(body)
        old = time.time() - age
        os.utime(path, (old, old))
        return path

    def run_cached(self, source_id, directory, countries=None):
        rows = []
        report = source.run_source(source_id, Path(directory), countries, True, lambda row: rows.append(row) or "added")
        return rows, report

    def test_exact_allowlist_rejects_credential_redirect_and_path_variants_before_fetch(self):
        invalid = ["http://www.telpark.com/es/ciudades/madrid/test/", DETAIL_URL + "?session=x",
                   DETAIL_URL.replace("www.telpark.com", "www.telpark.com.evil.example"),
                   DETAIL_URL.replace("https://", "https://user:pass@"),
                   "https://www.telpark.com/es/ciudades/madrid/../admin/", source.ELEKTRUM_URL + "/extra"]
        with patch.object(source.urllib.request, "build_opener") as opener:
            for url in invalid:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    source.bounded_fetch(url)
            with self.assertRaises(ValueError):
                source.bounded_fetch(DETAIL_URL, headers={"Authorization": "fixture-value"})
        opener.assert_not_called()
        with self.assertRaises(ValueError):
            source._NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")

    def test_transport_is_anonymous_get_and_rejects_truncated_or_large_responses(self):
        with patch.object(source.urllib.request, "build_opener") as build:
            build.return_value.open.return_value = _Response(b"body")
            self.assertEqual(source.bounded_fetch(DETAIL_URL), b"body")
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.get_method(), "GET")
            self.assertFalse({key.lower() for key, _ in request.header_items()} & {"authorization", "cookie"})
            self.assertIsInstance(build.call_args.args[0], source._NoRedirect)
            for expected in (10, source.MAX_BODY_BYTES + 1):
                build.return_value.open.return_value = _Response(b"body", expected)
                with self.assertRaises(ValueError):
                    source.bounded_fetch(DETAIL_URL)

    def test_offline_preserves_cache_age_and_country_filter(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(source, "bounded_fetch") as network:
            path = self.cached(directory, source.ELEKTRUM_URL, (EE + LV + LT).encode())
            original_time = path.stat().st_mtime
            rows, report = self.run_cached("elektrum_drive", directory, {"LT"})
            self.assertEqual(path.stat().st_mtime, original_time)
        network.assert_not_called()
        self.assertEqual([row["country_code"] for row in rows], ["LT"])
        self.assertEqual(report["status"], "stale")
        self.assertTrue(report["fetch"]["cache"]["stale"])
        self.assertEqual(report["accepted"], 1)
        self.assertEqual(report["scope"], ["LT"])

    def test_missing_detail_or_later_page_never_emits_partial_source(self):
        second_url = DETAIL_URL.replace("example-parking", "second-parking")
        for missing in ("detail", "page"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                items = ([parking_item(), parking_item(8, second_url)] if missing == "detail" else
                         [parking_item(i + 1, DETAIL_URL.replace("example-parking", f"parking-{i}")) for i in range(100)])
                self.cached(directory, source.TELPARK_LIST_URL.format(page=1), json.dumps(items).encode())
                self.cached(directory, DETAIL_URL, jsonld(parking_node()))
                rows, report = self.run_cached("telpark", directory)
                self.assertEqual(rows, [])
                self.assertEqual(report["status"], "failed")
                self.assertEqual(report["accepted"], 0)
                self.assertFalse(report["coverage_complete"])

    def test_page_cap_cannot_be_mistaken_for_complete_directory(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(source, "MAX_PAGES", 1):
            self.cached(directory, source.TELPARK_LIST_URL.format(page=1), json.dumps([parking_item()] * 100).encode())
            rows, report = self.run_cached("telpark", directory)
        self.assertEqual(rows, [])
        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["list_complete"])

    def test_missing_ev_flag_rejects_only_unconfirmed_parking_not_other_rows(self):
        second_url = DETAIL_URL.replace("example-parking", "second-parking")
        with tempfile.TemporaryDirectory() as directory:
            self.cached(directory, source.TELPARK_LIST_URL.format(page=1),
                        json.dumps([parking_item(), parking_item(8, second_url)]).encode())
            self.cached(directory, DETAIL_URL, jsonld(parking_node()))
            self.cached(directory, second_url, jsonld(parking_node(amenityFeature=[])))
            rows, report = self.run_cached("telpark", directory)
        self.assertEqual([row["id"] for row in rows], ["global_telpark_7"])
        self.assertEqual(report["status"], "stale")
        self.assertEqual(report["ev_unconfirmed"], 1)
        self.assertEqual(report["rejected"], 1)
        self.assertFalse(report["coverage_complete"])

    def test_failed_online_refresh_does_not_use_stale_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.cached(directory, source.ELEKTRUM_URL, EE.encode())
            old = path.stat().st_mtime
            rows = []
            with patch.object(source, "bounded_fetch", side_effect=OSError("offline")):
                report = source.run_source("elektrum_drive", Path(directory), None, False, rows.append)
            self.assertEqual(path.stat().st_mtime, old)
        self.assertEqual(rows, [])
        self.assertEqual(report["status"], "failed")

    def test_oversize_cached_artifact_and_outside_scope_do_not_fetch(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(source, "bounded_fetch") as network:
            self.cached(directory, source.ELEKTRUM_URL, EE.encode())
            with patch.object(source, "MAX_BODY_BYTES", 8):
                rows, report = self.run_cached("elektrum_drive", directory)
            self.assertEqual(rows, [])
            self.assertEqual(report["status"], "failed")
            rows, report = self.run_cached("telpark", directory, {"LV"})
            self.assertEqual(report["status"], "not_applicable")
        network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
