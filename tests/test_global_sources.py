#!/usr/bin/env python3
"""Offline contracts for worldwide candidate ingestion and source evidence."""
from __future__ import annotations

import io
import hashlib
import json
import os
import sys
import tarfile
import tempfile
import time
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import global_sources as global_ingestion  # noqa: E402
import build_global_intelligence as global_builder  # noqa: E402
from ingest_sources import FetchArtifact  # noqa: E402
from source_policy import recordset_sha256  # noqa: E402


def artifact(*, stale: bool = False) -> FetchArtifact:
    return FetchArtifact(
        body=b"", url="https://example.test/open-data.json", cache_name="fixture.json",
        fetched_at="2026-10-01T12:00:00Z", age_seconds=86400 if stale else 0,
        hit=stale, fallback=stale, stale=stale,
    )


def ocm_poi(country: str = "US", *, station_id: int = 1234,
            lat: float = 40.7, lon: float = -74.0) -> dict:
    return {
        "ID": station_id,
        "AddressInfo": {
            "Title": "Independent charging station", "AddressLine1": "1 Example Street",
            "Town": "Example", "Latitude": lat, "Longitude": lon,
            "Country": {"ISOCode": country},
        },
        "UsageCost": "Check the operator tariff",
        "DataProvider": {"Title": "OpenChargeMap Contributors", "IsOpenDataLicensed": True, "License": "CC BY 4.0"},
        "StatusType": {"Title": "Operational", "IsOperational": True},
        "UsageType": {"Title": "Public"},
        "NumberOfPoints": 2,
        "Connections": [{
            "ID": 2345, "ConnectionTypeID": 32,
            "ConnectionType": {"ID": 32, "Title": "CCS (Type 1)"},
            "PowerKW": 150, "Quantity": 2,
            "StatusType": {"Title": "Operational", "IsOperational": True},
        }],
    }


def osm_element(country: str = "US", *, station_id: int = 1234,
                lat: float = 40.7, lon: float = -74.0) -> dict:
    return {
        "type": "node", "id": station_id, "lat": lat, "lon": lon,
        "tags": {
            "amenity": "charging_station", "name": "Independent charging station",
            "addr:country": country, "socket:type1_combo": "2",
            "socket:type1_combo:output": "150 kW",
        },
    }


class SourceResponse(io.BytesIO):
    """A streaming HTTP body with the public metadata used by fetchers."""

    headers = {"Content-Type": "application/json"}
    status = 200


class WorldwideNormalizationTests(unittest.TestCase):
    def test_ocm_negative_snapshot_status_stays_nonactionable_even_if_operational_flag_is_true(self) -> None:
        for identifier, title, operational in ((30, "Temporarily Unavailable", True),
                                                (100, "Not Operational", False),
                                                (150, "Planned For Future Date", False),
                                                (200, "Removed (Decommissioned)", False),
                                                (210, "Removed (Duplicate Listing)", False)):
            with self.subTest(status=identifier):
                poi = ocm_poi()
                poi.update(StatusTypeID=identifier, StatusType={"Title": title, "IsOperational": operational})
                row = global_ingestion.normalize_ocm(poi, artifact())
                self.assertEqual(row["source_reported_unavailable"]["value"], identifier)
                self.assertFalse(row["source_reported_unavailable"]["live"])
                self.assertEqual(row["status"], "VERIFY")
                self.assertFalse(row["source_integration"]["live_status_integrated"])

    def test_openchargemap_keeps_stations_across_continents(self) -> None:
        locations = [
            ("US", 40.7, -74.0), ("BR", -23.55, -46.63), ("AU", -33.87, 151.21),
            ("JP", 35.68, 139.69), ("ZA", -33.92, 18.42), ("ES", 39.47, -0.37),
        ]
        for country, lat, lon in locations:
            with self.subTest(country=country):
                row = global_ingestion.normalize_ocm(ocm_poi(country, lat=lat, lon=lon), artifact())
                self.assertIsNotNone(row)
                self.assertEqual(row["country_code"], country)
                self.assertEqual((row["lat"], row["lon"]), (lat, lon))

    def test_openchargemap_preserves_supported_xk_country_reference(self) -> None:
        row = global_ingestion.normalize_ocm(ocm_poi("XK", lat=42.66, lon=21.16), artifact())
        self.assertIsNotNone(row)
        self.assertEqual(row["country_code"], "XK")
        self.assertEqual((row["lat"], row["lon"]), (42.66, 21.16))

    def test_osm_keeps_equator_and_prime_meridian_coordinates(self) -> None:
        for lat, lon in [(0, -78.45), (51.5, 0)]:
            with self.subTest(lat=lat, lon=lon):
                row = global_ingestion.normalize_osm(osm_element(lat=lat, lon=lon), artifact())
                self.assertIsNotNone(row)
                self.assertEqual((row["lat"], row["lon"]), (lat, lon))

    def test_osm_way_center_and_explicit_country_are_preserved(self) -> None:
        element = osm_element("AU", lat=-33.87, lon=151.21)
        element["type"] = "way"
        element["center"] = {"lat": element.pop("lat"), "lon": element.pop("lon")}
        element["tags"].pop("addr:country")
        row = global_ingestion.normalize_osm(element, artifact(), country_code="AU")
        self.assertIsNotNone(row)
        self.assertEqual(row["country_code"], "AU")
        self.assertEqual((row["lat"], row["lon"]), (-33.87, 151.21))

    def test_out_of_range_nonfinite_and_boolean_coordinates_are_rejected(self) -> None:
        invalid_coordinates = [(91, 0), (0, -181), (float("nan"), 1), (1, float("inf")), (True, 1), (None, 1)]
        for lat, lon in invalid_coordinates:
            with self.subTest(lat=lat, lon=lon):
                self.assertIsNone(global_ingestion.normalize_ocm(ocm_poi(lat=lat, lon=lon), artifact()))
                self.assertIsNone(global_ingestion.normalize_osm(osm_element(lat=lat, lon=lon), artifact()))

    def test_openchargemap_requires_country_and_stable_identity(self) -> None:
        for missing in ("country", "id"):
            poi = ocm_poi()
            if missing == "country":
                poi["AddressInfo"].pop("Country")
            else:
                poi.pop("ID")
            with self.subTest(missing=missing):
                self.assertIsNone(global_ingestion.normalize_ocm(poi, artifact()))

    def test_quarantined_openchargemap_station_cannot_return_through_global_import(self) -> None:
        self.assertIsNone(global_ingestion.normalize_ocm(ocm_poi(station_id=169683), artifact()))

    def test_directory_operational_status_never_becomes_live_availability(self) -> None:
        for evidence in [artifact(), artifact(stale=True)]:
            with self.subTest(stale=evidence.stale):
                for row in [
                    global_ingestion.normalize_ocm(ocm_poi(), evidence),
                    global_ingestion.normalize_osm(osm_element(), evidence),
                ]:
                    self.assertEqual(row["status"], "VERIFY")
                    self.assertTrue(row["connectors"])
                    self.assertTrue(all(connector["status"] == "UNKNOWN" for connector in row["connectors"]))
                    self.assertFalse(row["source_integration"]["live_status_integrated"])
                    self.assertEqual(row["source_fetched_at"], evidence.fetched_at)

    def test_distinct_worldwide_connector_standards_do_not_collapse(self) -> None:
        poi = ocm_poi()
        poi["Connections"] = [
            {"ConnectionType": {"Title": title}, "PowerKW": 50}
            for title in ["CCS (Type 1)", "CCS (Type 2)", "NACS (J3400)", "Tesla (Model S/X)"]
        ]
        row = global_ingestion.normalize_ocm(poi, artifact())
        labels = {connector["label"] for connector in row["connectors"]}
        self.assertTrue({"CCS1", "CCS2", "NACS", "Tesla"} <= labels, labels)

    def test_openchargemap_private_or_restricted_usage_keeps_access_constraints(self) -> None:
        for title in ("Private - Staff Only", "Restricted access - members only"):
            poi = ocm_poi()
            poi["UsageType"] = {"Title": title}
            with self.subTest(title=title):
                row = global_ingestion.normalize_ocm(poi, artifact())
                self.assertEqual(row["access"]["level"], "restricted")
                self.assertIn(title, json.dumps(row["access"]))


class WorldwidePriceEvidenceTests(unittest.TestCase):
    def test_openchargemap_free_text_is_evidence_not_a_euro_tariff(self) -> None:
        for text in ["Free", "$0.20/kWh", "0.20 EUR/kWh", "Free for hotel guests; otherwise $0.40/kWh"]:
            poi = ocm_poi()
            poi["UsageCost"] = text
            with self.subTest(text=text):
                row = global_ingestion.normalize_ocm(poi, artifact())
                self.assertIsNone(row["price_min"])
                self.assertFalse(row["is_cheap"])
                self.assertFalse(row["is_free"])
                self.assertEqual(row["raw"]["UsageCost"], text)

    def test_structured_euro_kwh_tariff_supports_the_existing_price_filter(self) -> None:
        poi = ocm_poi("FR", lat=48.85, lon=2.35)
        poi["Tariff"] = {"Currency": "EUR", "PricePerKWh": 0.22}
        row = global_ingestion.normalize_ocm(poi, artifact())
        self.assertEqual(row["price_currency"], "EUR")
        self.assertEqual(row["price_min"], 0.22)
        self.assertTrue(row["is_cheap"])
        self.assertFalse(row["is_free"])

    def test_foreign_currency_is_not_reinterpreted_as_a_cheap_euro_price(self) -> None:
        for currency in ("USD", "CAD", "JPY", "BRL"):
            poi = ocm_poi()
            poi["Tariff"] = {"Currency": currency, "PricePerKWh": 0.20}
            with self.subTest(currency=currency):
                row = global_ingestion.normalize_ocm(poi, artifact())
                self.assertIsNone(row["price_min"])
                self.assertFalse(row["is_cheap"])
                self.assertFalse(row["is_free"])
                self.assertIn(currency, json.dumps(row))

    def test_invalid_structured_prices_never_create_free_or_cheap_charging(self) -> None:
        for price in (-1, float("nan"), float("inf"), True, "unknown"):
            poi = ocm_poi()
            poi["Tariff"] = {"Currency": "EUR", "PricePerKWh": price}
            with self.subTest(price=price):
                row = global_ingestion.normalize_ocm(poi, artifact())
                self.assertIsNone(row["price_min"])
                self.assertFalse(row["is_cheap"])
                self.assertFalse(row["is_free"])

    def test_osm_requires_explicit_currency_and_energy_unit(self) -> None:
        for tags, expected_price in [
            ({"charge:currency": "EUR", "charge:amount": "0.2", "charge:unit": "kWh"}, 0.2),
            ({"charge:currency": "USD", "charge:amount": "0.2", "charge:unit": "kWh"}, None),
            ({"charge:currency": "EUR", "charge:amount": "0.2", "charge:unit": "minute"}, None),
            ({"charge:amount": "0.2", "charge:unit": "kWh"}, None),
            ({"charge": "$0.20/kWh"}, None),
        ]:
            element = osm_element()
            element["tags"].update(tags)
            with self.subTest(tags=tags):
                row = global_ingestion.normalize_osm(element, artifact())
                self.assertEqual(row["price_min"], expected_price)
                self.assertEqual(row["is_cheap"], expected_price is not None)

    def test_conditional_osm_fee_is_not_an_unconditional_cheap_tariff(self) -> None:
        element = osm_element()
        element["tags"].update({
            "charge:currency": "EUR", "charge:amount": "0.20", "charge:unit": "kWh",
            "fee:conditional": "no @ (Mo-Fr 18:00-08:00)",
        })
        row = global_ingestion.normalize_osm(element, artifact())
        self.assertIsNone(row["price_min"])
        self.assertFalse(row["is_cheap"])
        self.assertFalse(row["is_free"])
        self.assertEqual(row["raw"]["charge:amount"], "0.20")
        self.assertEqual(row["tariff"]["amount"], 0.20)
        self.assertEqual(row["tariff"]["conditional"], "no @ (Mo-Fr 18:00-08:00)")


class SourceOriginPolicyTests(unittest.TestCase):
    def test_openchargemap_upstream_provider_attribution_cannot_hide_blocked_origins(self) -> None:
        providers = [
            {"Title": "Electromaps"},
            {"Title": "Independent mirror", "WebsiteURL": "https://chargemap.com/"},
            {"Title": "Consum store locator", "WebsiteURL": "https://www.consum.es/"},
        ]
        for provider in providers:
            poi = ocm_poi()
            poi["DataProvider"] = {**provider, "IsOpenDataLicensed": True, "License": "CC BY 4.0"}
            with self.subTest(provider=provider):
                self.assertIsNone(global_ingestion.normalize_ocm(poi, artifact()))

    def test_openchargemap_requires_affirmative_provider_reuse_metadata(self) -> None:
        for provider in (
            {}, {"IsOpenDataLicensed": False, "License": "CC BY 4.0"},
            {"IsOpenDataLicensed": "true", "License": "CC BY 4.0"},
            {"IsOpenDataLicensed": True},
            {"IsOpenDataLicensed": True, "License": "CC BY-NC 4.0"},
            {"IsOpenDataLicensed": True, "License": "CC BY-ND 4.0"},
        ):
            poi = ocm_poi()
            poi["DataProvider"] = {"Title": "Independent data provider", **provider}
            with self.subTest(provider=provider):
                self.assertIsNone(global_ingestion.normalize_ocm(poi, artifact()))

    def test_osm_explicit_source_tags_cannot_launder_blocked_origins(self) -> None:
        for key, value in [
            ("source", "Electromaps"),
            ("source_url", "https://chargemap.com/example-station"),
            ("source", "Consum store locator"),
        ]:
            element = osm_element()
            element["tags"][key] = value
            with self.subTest(key=key, value=value):
                self.assertIsNone(global_ingestion.normalize_osm(element, artifact()))

    def test_operator_mentions_do_not_exclude_independent_allowed_evidence(self) -> None:
        poi = ocm_poi()
        poi["OperatorInfo"] = {"Title": "Electromaps"}
        poi["DataProvider"] = {"Title": "OpenChargeMap Contributors", "IsOpenDataLicensed": True, "License": "CC BY 4.0"}
        self.assertIsNotNone(global_ingestion.normalize_ocm(poi, artifact()))
        element = osm_element()
        element["tags"]["operator"] = "Electromaps"
        self.assertIsNotNone(global_ingestion.normalize_osm(element, artifact()))


class OpenChargeMapExportTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.archive_path = Path(temporary.name) / "ocm-export.tar.gz"
        reference = {
            "Countries": [{"ID": 2, "ISOCode": "US"}, {"ID": 5, "ISOCode": "CA"}],
            "DataProviders": [{
                "ID": 1, "Title": "OpenChargeMap Contributors",
                "IsOpenDataLicensed": True, "License": "CC BY 4.0",
            }],
            "ConnectionTypes": [{"ID": 32, "Title": "CCS (Type 1)"}],
            "StatusTypes": [{"ID": 50, "Title": "Operational", "IsOperational": True}],
            "UsageTypes": [{"ID": 1, "Title": "Public"}],
            "Operators": [{"ID": 1, "Title": "Independent operator"}],
            "SubmissionStatusTypes": [{"ID": 200, "Title": "Published", "IsLive": True}],
        }
        compact_poi = {
            "ID": 123, "DataProviderID": 1,
            "AddressInfo": {"Title": "Export fixture", "CountryID": 2, "Latitude": 40.7, "Longitude": -74},
            "Connections": [{"ID": 234, "ConnectionTypeID": 32, "PowerKW": 150, "Quantity": 2}],
        }
        canada = deepcopy(compact_poi)
        canada["ID"] = 124
        canada["AddressInfo"].update(CountryID=5, Latitude=43.65, Longitude=-79.38)
        # Place reference data after the POIs, as archive order must not decide
        # whether country, connector, and provider attribution can be resolved.
        members = [
            ("ocm-export-8e3bedca48ca94807d96b2f5e7ee02cffe63ae54/data/US/OCM-123.json", compact_poi),
            ("ocm-export-8e3bedca48ca94807d96b2f5e7ee02cffe63ae54/data/CA/OCM-124.json", canada),
            ("ocm-export-8e3bedca48ca94807d96b2f5e7ee02cffe63ae54/data/referencedata.json", reference),
            ("ocm-export-8e3bedca48ca94807d96b2f5e7ee02cffe63ae54/README.json", {"not": "a station"}),
        ]
        with tarfile.open(self.archive_path, "w:gz") as archive:
            for name, payload in members:
                data = json.dumps(payload).encode()
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))

    def test_compact_export_resolves_reference_ids_without_extracting_files(self) -> None:
        with mock.patch.object(tarfile.TarFile, "extract", side_effect=AssertionError("must stream members")), \
             mock.patch.object(tarfile.TarFile, "extractall", side_effect=AssertionError("must stream members")):
            pois = list(global_ingestion.iter_ocm_export(self.archive_path))
        self.assertEqual(len(pois), 2)
        by_country = {poi["AddressInfo"]["Country"]["ISOCode"]: poi for poi in pois}
        self.assertEqual(set(by_country), {"US", "CA"})
        us_poi = by_country["US"]
        self.assertEqual(us_poi["DataProvider"]["License"], "CC BY 4.0")
        self.assertEqual(us_poi["Connections"][0]["ConnectionType"]["Title"], "CCS (Type 1)")
        normalized = global_ingestion.normalize_ocm(us_poi, artifact())
        self.assertEqual(normalized["country_code"], "US")
        self.assertEqual(normalized["connectors"][0]["label"], "CCS1")

    def test_country_filter_limits_export_records_to_requested_countries(self) -> None:
        pois = list(global_ingestion.iter_ocm_export(self.archive_path, countries={"CA"}))
        self.assertEqual([poi["ID"] for poi in pois], [124])


class CandidateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = global_ingestion.CandidateStore(Path(temporary.name) / "candidate.sqlite")
        self.addCleanup(self.store.close)

    def test_baseline_record_remains_exact_when_reimported(self) -> None:
        baseline = global_ingestion.normalize_ocm(ocm_poi(), artifact())
        baseline["manual_evidence"] = {"parking": "User verified", "verified_at": "2026-09-30"}
        original = deepcopy(baseline)
        self.assertEqual(self.store.add(baseline, baseline=True), "added")
        self.assertEqual(self.store.add(global_ingestion.normalize_ocm(ocm_poi(), artifact())), "duplicate")
        self.assertEqual(list(self.store.rows()), [original])

    def test_legacy_spain_baseline_without_country_keeps_all_existing_evidence(self) -> None:
        baseline = global_ingestion.normalize_ocm(ocm_poi("ES", lat=39.47, lon=-0.37), artifact())
        baseline.pop("country_code")
        baseline.pop("country")
        baseline["community_evidence"] = [{"report": "Physical access verified", "parking_cost": None}]
        original = deepcopy(baseline)
        self.assertEqual(self.store.add(baseline, baseline=True), "added")
        self.assertEqual(list(self.store.rows()), [original])
        self.assertEqual(self.store.stats()["countries"], {"ES": 1})

    def test_secondary_ocm_identity_deduplicates_without_replacing_baseline_evidence(self) -> None:
        baseline = global_ingestion.normalize_ocm(ocm_poi("ES", lat=39.47, lon=-0.37), artifact())
        baseline.update(id="dgt_station_7", source="DGT public dataset", source_url="https://example.test/dgt/7")
        baseline["sources"].insert(0, {"name": "DGT public dataset", "url": baseline["source_url"]})
        original = deepcopy(baseline)
        self.assertEqual(self.store.add(baseline, baseline=True), "added")
        reimported = global_ingestion.normalize_ocm(ocm_poi("ES", lat=39.4701, lon=-0.37), artifact())
        self.assertEqual(self.store.add(reimported), "duplicate")
        self.assertEqual(list(self.store.rows()), [original])

    def test_secondary_ocm_identity_still_requires_matching_country_and_nearby_coordinates(self) -> None:
        baseline = global_ingestion.normalize_ocm(ocm_poi("ES", lat=39.47, lon=-0.37), artifact())
        baseline.update(id="dgt_station_7", source="DGT public dataset", source_url="https://example.test/dgt/7")
        original = deepcopy(baseline)
        self.assertEqual(self.store.add(baseline, baseline=True), "added")
        far_away = global_ingestion.normalize_ocm(ocm_poi("ES", lat=40.47, lon=-0.37), artifact())
        other_country = global_ingestion.normalize_ocm(ocm_poi("PT", lat=39.4701, lon=-0.37), artifact())
        self.assertEqual(self.store.add(far_away), "added")
        self.assertEqual(self.store.add(other_country), "added")
        rows = list(self.store.rows())
        self.assertEqual(rows[0], original)
        self.assertEqual(len(rows), 3)

    def test_repeat_and_nearby_same_identity_deduplicate(self) -> None:
        original = global_ingestion.normalize_ocm(ocm_poi(), artifact())
        nearby = global_ingestion.normalize_ocm(ocm_poi(lat=40.7001), artifact())
        self.assertEqual(self.store.add(original), "added")
        self.assertEqual(self.store.add(deepcopy(original)), "duplicate")
        self.assertEqual(self.store.add(nearby), "duplicate")
        self.assertEqual(len(list(self.store.rows())), 1)

    def test_same_identity_far_away_or_in_another_country_is_not_silently_merged(self) -> None:
        original = global_ingestion.normalize_ocm(ocm_poi(), artifact())
        far_away = global_ingestion.normalize_ocm(ocm_poi(lat=40.8), artifact())
        another_country = global_ingestion.normalize_ocm(ocm_poi("CA"), artifact())
        self.assertEqual(self.store.add(original), "added")
        self.assertEqual(self.store.add(far_away), "added")
        self.assertEqual(self.store.add(another_country), "added")
        self.assertEqual(len(list(self.store.rows())), 3)

    def test_independent_colocated_source_evidence_is_kept(self) -> None:
        self.assertEqual(self.store.add(global_ingestion.normalize_ocm(ocm_poi(), artifact())), "added")
        self.assertEqual(self.store.add(global_ingestion.normalize_osm(osm_element(), artifact())), "added")
        self.assertEqual(len(list(self.store.rows())), 2)

    def test_store_rechecks_nested_exclusions_and_invalid_coordinates(self) -> None:
        blocked = global_ingestion.normalize_ocm(ocm_poi(), artifact())
        blocked["sources"].append({"name": "Electromaps", "url": "https://www.electromaps.com/"})
        self.assertEqual(self.store.add(blocked), "excluded")
        invalid = global_ingestion.normalize_ocm(ocm_poi(), artifact())
        invalid["lat"] = float("nan")
        self.assertEqual(self.store.add(invalid), "invalid")
        self.assertEqual(list(self.store.rows()), [])

    def test_streaming_fingerprint_matches_canonical_recordset_fingerprint(self) -> None:
        rows = [
            global_ingestion.normalize_ocm(ocm_poi(station_id=1239), artifact()),
            global_ingestion.normalize_ocm(ocm_poi(station_id=1230), artifact()),
        ]
        for row in rows:
            self.assertEqual(self.store.add(row), "added")
        self.assertEqual(self.store.fingerprint(), recordset_sha256(rows))


class StreamingCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cache = Path(temporary.name)
        self.url = "https://example.test/open-data.json"

    def test_fresh_cache_does_not_fetch_or_reset_source_timestamp(self) -> None:
        path = self.cache / "source.json"
        path.write_bytes(b"cached evidence")
        previous = time.time() - 120
        os.utime(path, (previous, previous))
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("fresh cache must not fetch")):
            result = global_ingestion.fetch_cached_file(self.url, self.cache, path.name, ttl_hours=1)
        self.assertEqual(result.path.read_bytes(), b"cached evidence")
        self.assertTrue(result.artifact.hit)
        self.assertFalse(result.artifact.fallback)
        self.assertFalse(result.artifact.stale)
        timestamp = datetime.fromisoformat(result.artifact.fetched_at.replace("Z", "+00:00")).timestamp()
        self.assertAlmostEqual(timestamp, previous, places=3)

    def test_stale_network_failure_preserves_cache_and_labels_fallback(self) -> None:
        path = self.cache / "source.json"
        path.write_bytes(b"old source evidence")
        previous = time.time() - 7200
        os.utime(path, (previous, previous))
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("source unreachable")):
            result = global_ingestion.fetch_cached_file(self.url, self.cache, path.name, ttl_hours=1)
        self.assertEqual(result.path.read_bytes(), b"old source evidence")
        self.assertTrue(result.artifact.hit)
        self.assertTrue(result.artifact.fallback)
        self.assertTrue(result.artifact.stale)
        self.assertEqual(result.artifact.error, "TimeoutError")
        self.assertAlmostEqual(path.stat().st_mtime, previous, places=3)

    def test_offline_cache_access_never_opens_a_socket(self) -> None:
        path = self.cache / "source.json"
        path.write_bytes(b"offline evidence")
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("offline must not fetch")):
            result = global_ingestion.fetch_cached_file(self.url, self.cache, path.name, offline=True)
        self.assertEqual(result.path.read_bytes(), b"offline evidence")
        self.assertTrue(result.artifact.hit)

    def test_network_download_uses_bounded_reads_and_public_get(self) -> None:
        class BoundedResponse(SourceResponse):
            def read(self, size: int = -1) -> bytes:
                if size < 0:
                    raise AssertionError("worldwide downloads must not use an unbounded read")
                return super().read(size)

        with mock.patch("urllib.request.urlopen", return_value=BoundedResponse(b"new evidence")) as opener:
            result = global_ingestion.fetch_cached_file(self.url, self.cache, "source.json", max_bytes=64)
        self.assertEqual(result.path.read_bytes(), b"new evidence")
        self.assertEqual(opener.call_args.args[0].get_method(), "GET")
        self.assertFalse(result.artifact.hit)
        self.assertFalse(result.artifact.stale)

    def test_oversized_or_interrupted_response_never_replaces_existing_cache(self) -> None:
        class InterruptedResponse(SourceResponse):
            def read(self, size: int = -1) -> bytes:
                if self.tell():
                    raise TimeoutError("partial download")
                return super().read(min(size, 4))

        for response in (SourceResponse(b"x" * 65), InterruptedResponse(b"partial download")):
            path = self.cache / "source.json"
            path.write_bytes(b"previous")
            previous = time.time() - 7200
            os.utime(path, (previous, previous))
            with self.subTest(response=type(response).__name__):
                with mock.patch("urllib.request.urlopen", return_value=response):
                    result = global_ingestion.fetch_cached_file(self.url, self.cache, path.name, ttl_hours=1, max_bytes=64)
                self.assertEqual(path.read_bytes(), b"previous")
                self.assertTrue(result.artifact.fallback)
                self.assertTrue(result.artifact.stale)
                self.assertAlmostEqual(path.stat().st_mtime, previous, places=3)


class OverpassCompletenessTests(unittest.TestCase):
    def test_partial_overpass_response_with_remark_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            global_ingestion.parse_osm_payload({
                "remark": "runtime error: Query timed out", "elements": [osm_element()],
            })

    def test_successful_empty_response_is_distinct_from_malformed_payload(self) -> None:
        self.assertEqual(global_ingestion.parse_osm_payload({"elements": []}), [])
        for payload in ({}, [], {"elements": None}, {"elements": {}}, {"remark": "runtime error"}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                global_ingestion.parse_osm_payload(payload)

    def test_query_supports_country_or_finite_ordered_bbox(self) -> None:
        self.assertIn("US", global_ingestion.osm_query(country_code="US"))
        self.assertIn("charging_station", global_ingestion.osm_query(bbox=(-34, 150, -33, 152)))
        for country in ("USA", "U1", 'US\"];out;'):
            with self.subTest(country=country), self.assertRaises(ValueError):
                global_ingestion.osm_query(country_code=country)
        for bbox in ((float("nan"), 0, 1, 1), (-91, 0, 1, 1), (2, 0, 1, 1), (0, 2, 1, 1)):
            with self.subTest(bbox=bbox), self.assertRaises(ValueError):
                global_ingestion.osm_query(bbox=bbox)


class CandidateBuildIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.baseline_path = self.directory / "baseline.json"
        self.output_path = self.directory / "world.candidate.json"
        self.original_row = global_ingestion.normalize_ocm(
            ocm_poi("ES", station_id=9999, lat=39.47, lon=-0.37), artifact(),
        )
        self.original_row["manual_evidence"] = {"note": "Keep existing source and user evidence"}
        self.baseline_path.write_text(json.dumps({"records": [self.original_row]}), encoding="utf-8")
        self.original_bytes = self.baseline_path.read_bytes()
        national_patch = mock.patch.object(global_builder, "national_module", return_value=None)
        national_patch.start()
        self.addCleanup(national_patch.stop)

    def args(self, **overrides) -> SimpleNamespace:
        values = {
            "baseline": self.baseline_path, "out": self.output_path,
            "cache_dir": self.directory / "cache", "manifest": None,
            "all_countries": False, "countries": "US", "sources": "ocm",
            "osm_max_requests": 1, "osm_offset": 0, "osm_pause_seconds": 1,
            "ocm_archive": None, "offline": False, "bbox": None,
            "ocm_revision": global_ingestion.OCM_EXPORT_REVISION,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def fetched(self, payload: dict | None = None) -> global_ingestion.FileArtifact:
        path = self.directory / "response.json"
        path.write_text(json.dumps(payload or {"elements": []}), encoding="utf-8")
        return global_ingestion.FileArtifact(path, artifact())

    def candidate(self) -> dict:
        return json.loads(self.output_path.read_text(encoding="utf-8"))

    def test_failed_ocm_refresh_preserves_baseline_and_reports_failure(self) -> None:
        with mock.patch.object(global_ingestion, "fetch_cached_file", side_effect=TimeoutError("upstream unavailable")):
            report = global_builder.build(self.args())
        self.assertEqual(self.baseline_path.read_bytes(), self.original_bytes)
        self.assertEqual(self.candidate()["records"], [self.original_row])
        self.assertTrue(report["baseline_preserved"])
        self.assertEqual(report["source_manifests"][0]["status"], "failed")
        self.assertEqual(report["coverage_status"], "partial")
        self.assertFalse(report["coverage_complete"])
        saved_report = json.loads(self.output_path.with_suffix(".manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(saved_report, report)

    def test_baseline_mutation_during_streaming_cannot_publish_candidate(self) -> None:
        original_add = global_ingestion.CandidateStore.add

        def mutate_baseline(store, row, baseline=False):
            result = original_add(store, row, baseline=baseline)
            if baseline:
                self.baseline_path.write_text(json.dumps({"records": [dict(row, name="Changed during build")]}), encoding="utf-8")
            return result

        with mock.patch.object(global_ingestion.CandidateStore, "add", mutate_baseline), \
             self.assertRaisesRegex(ValueError, "baseline changed"):
            global_builder.build(self.args())
        self.assertFalse(self.output_path.exists())
        self.assertFalse(self.output_path.with_suffix(".manifest.json").exists())

    def test_source_failure_does_not_discard_other_sources_successful_rows(self) -> None:
        fetched = self.fetched()

        def fetch(url, *args, **kwargs):
            if "overpass" in url:
                raise TimeoutError("Overpass unavailable")
            return fetched

        with mock.patch.object(global_ingestion, "fetch_cached_file", side_effect=fetch), \
             mock.patch.object(global_ingestion, "ocm_export_revision", return_value=global_ingestion.OCM_EXPORT_REVISION), \
             mock.patch.object(global_ingestion, "iter_ocm_export", return_value=iter([ocm_poi()])):
            report = global_builder.build(self.args(sources="ocm,osm"))
        statuses = {item["source"]: item["status"] for item in report["source_manifests"]}
        self.assertEqual(statuses, {"ocm": "ok", "osm": "failed"})
        self.assertEqual(len(self.candidate()["records"]), 2)
        self.assertEqual(self.candidate()["records"][0], self.original_row)
        self.assertEqual(report["coverage_status"], "partial")

    def test_partial_ocm_stream_rolls_back_accepted_rows_and_reports_failure(self) -> None:
        def pois():
            yield ocm_poi()
            raise ValueError("truncated archive")

        with mock.patch.object(global_ingestion, "fetch_cached_file", return_value=self.fetched()), \
             mock.patch.object(global_ingestion, "ocm_export_revision", return_value=global_ingestion.OCM_EXPORT_REVISION), \
             mock.patch.object(global_ingestion, "iter_ocm_export", side_effect=lambda *args, **kwargs: pois()):
            report = global_builder.build(self.args())
        manifest = report["source_manifests"][0]
        self.assertEqual((manifest["status"], manifest["accepted"]), ("failed", 0))
        self.assertEqual(manifest["rolled_back_records"], 1)
        self.assertEqual(manifest["admission"], "rolled_back")
        self.assertEqual(manifest["accepted_by_provider_id"], {})
        self.assertIn("truncated archive", manifest["errors"][0])
        self.assertEqual(self.candidate()["records"], [self.original_row])
        self.assertEqual(report["stats"].get("added", 0), 0)
        self.assertTrue(report["baseline_preserved"])

    def test_manifest_collision_is_rejected_before_any_candidate_or_baseline_write(self) -> None:
        sentinel = b"existing candidate must stay untouched"
        for target in (self.baseline_path, self.output_path):
            self.output_path.write_bytes(sentinel)
            with self.subTest(target=target.name):
                with mock.patch.object(global_ingestion, "fetch_cached_file") as fetch:
                    with self.assertRaises(ValueError):
                        global_builder.build(self.args(manifest=target))
                fetch.assert_not_called()
                self.assertEqual(self.output_path.read_bytes(), sentinel)
                self.assertEqual(self.baseline_path.read_bytes(), self.original_bytes)

    def test_canonical_output_is_rejected_before_fetch_or_write(self) -> None:
        canonical = self.directory / "protected.candidate.json"
        canonical.write_bytes(b"canonical data")
        with mock.patch.object(global_builder, "CANONICAL", canonical), \
             mock.patch.object(global_ingestion, "fetch_cached_file") as fetch:
            with self.assertRaises(ValueError):
                global_builder.build(self.args(out=canonical))
        fetch.assert_not_called()
        self.assertEqual(canonical.read_bytes(), b"canonical data")

    def test_bbox_requires_one_country_before_fetching(self) -> None:
        for selection in ({"countries": "US,CA"}, {"all_countries": True}):
            with self.subTest(selection=selection):
                with mock.patch.object(global_ingestion, "fetch_cached_file") as fetch:
                    with self.assertRaises(ValueError):
                        global_builder.build(self.args(sources="osm", bbox=["37,-123,38,-122"], **selection))
                fetch.assert_not_called()
                self.assertFalse(self.output_path.exists())

    def test_overpass_partial_payload_is_failed_without_adding_its_rows(self) -> None:
        fetched = self.fetched({"remark": "runtime error: Query timed out", "elements": [osm_element()]})
        with mock.patch.object(global_ingestion, "fetch_cached_file", return_value=fetched):
            report = global_builder.build(self.args(sources="osm"))
        manifest = report["source_manifests"][0]
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["accepted"], 0)
        self.assertEqual(self.candidate()["records"], [self.original_row])
        self.assertFalse(report["coverage_complete"])

    def test_osm_budget_deferrals_remain_visible_and_do_not_request_data(self) -> None:
        with mock.patch.object(global_ingestion, "fetch_cached_file", return_value=self.fetched()) as fetch:
            report = global_builder.build(self.args(sources="osm", countries="US,CA", osm_max_requests=1))
        self.assertEqual(fetch.call_count, 1)
        statuses = {item["scope"]: item["status"] for item in report["source_manifests"]}
        self.assertEqual(statuses, {"CA": "ok", "US": "deferred_budget"})
        self.assertEqual(report["coverage_status"], "partial")

    def test_all_country_ocm_build_keeps_global_rows_and_explicit_scope(self) -> None:
        pois = [ocm_poi("US"), ocm_poi("AU", station_id=1235, lat=-33.87, lon=151.21)]
        with mock.patch.object(global_ingestion, "fetch_cached_file", return_value=self.fetched()), \
             mock.patch.object(global_ingestion, "ocm_export_revision", return_value=global_ingestion.OCM_EXPORT_REVISION), \
             mock.patch.object(global_ingestion, "iter_ocm_export", return_value=iter(pois)) as iterate:
            report = global_builder.build(self.args(all_countries=True))
        self.assertIsNone(iterate.call_args.kwargs["countries"])
        self.assertEqual(report["requested_countries"], "all")
        self.assertEqual(report["source_manifests"][0]["scope"], "all-countries")
        self.assertEqual(report["stats"]["countries"], {"AU": 1, "ES": 1, "US": 1})
        self.assertEqual(len(self.candidate()["records"]), 3)
        self.assertFalse(report["promotion_allowed"])
        self.assertTrue(report["baseline_preserved"])

    def ocm_archive(self, root: str) -> Path:
        path = self.directory / "fixture-export.tar.gz"
        reference = {
            "Countries": [{"ID": 2, "ISOCode": "US"}],
            "DataProviders": [{"ID": 1, "Title": "Contributors", "IsOpenDataLicensed": True, "License": "CC BY 4.0"}],
            "ConnectionTypes": [{"ID": 32, "Title": "CCS (Type 1)"}],
            "StatusTypes": [{"ID": 50, "Title": "Operational", "IsOperational": True}],
            "UsageTypes": [{"ID": 1, "Title": "Public"}],
            "Operators": [{"ID": 1, "Title": "Independent operator"}],
            "SubmissionStatusTypes": [{"ID": 200, "Title": "Published", "IsLive": True}],
        }
        poi = ocm_poi()
        poi["DataProviderID"] = 1
        poi["AddressInfo"]["CountryID"] = 2
        members = [
            (f"{root}/data/referencedata.json", reference),
            (f"{root}/data/US/OCM-1234.json", poi),
        ]
        with tarfile.open(path, "w:gz") as archive:
            for name, payload in members:
                data = json.dumps(payload).encode()
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        return path

    def test_real_ocm_archive_revision_and_content_hash_are_preserved_in_manifest(self) -> None:
        path = self.ocm_archive("ocm-export-" + global_ingestion.OCM_EXPORT_REVISION)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        report = global_builder.build(self.args(ocm_archive=path))
        manifest = report["source_manifests"][0]
        self.assertEqual(manifest["status"], "ok")
        self.assertEqual(manifest["accepted"], 1)
        self.assertEqual(manifest["snapshot"]["revision"], global_ingestion.OCM_EXPORT_REVISION)
        self.assertEqual(manifest["snapshot"]["sha256"], digest)
        self.assertEqual(manifest["snapshot"]["bytes"], path.stat().st_size)
        self.assertEqual(global_ingestion.file_sha256(path), digest)
        self.assertEqual(self.candidate()["recordset_sha256"], recordset_sha256(self.candidate()["records"]))

    def test_wrong_or_unknown_archive_revision_cannot_claim_selected_snapshot(self) -> None:
        for root in ("ocm-export-" + "f" * 40, "ocm-export-master"):
            path = self.ocm_archive(root)
            with self.subTest(root=root):
                report = global_builder.build(self.args(ocm_archive=path))
                manifest = report["source_manifests"][0]
                self.assertEqual(manifest["status"], "failed")
                self.assertEqual(manifest["accepted"], 0)
                self.assertNotIn("snapshot", manifest)
                self.assertEqual(self.candidate()["records"], [self.original_row])
                self.assertFalse(report["coverage_complete"])


if __name__ == "__main__":
    unittest.main()
