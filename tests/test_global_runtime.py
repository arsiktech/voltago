"""World discovery preserves regional live authority and native currency evidence."""
from __future__ import annotations

import io
import json
import math
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_mcp
import server


def directory(identifier="global", lat=35.68, lon=139.76, country="JP", currency="JPY", price=25):
    return {
        "id": identifier, "name": "Public charger", "lat": lat, "lon": lon,
        "country_code": country, "price_currency": currency, "price_min": price,
        "source": "OpenChargeMap", "source_url": f"https://openchargemap.org/site/poi/details/{identifier}",
        "reliability": "directory-location", "connector_labels": ["CHAdeMO"],
        "source_notes": "Location only; availability is unknown", "is_free": False,
    }



def setUpModule():
    # Existing suites isolate their older feeds; keep the new runtime feeds offline too.
    live_patch = patch("server.fetch_public_live", return_value={"stations": [], "feeds": {}})
    live_patch.start()
    unittest.addModuleCleanup(live_patch.stop)


class WorldViewportTests(unittest.TestCase):
    def test_world_cities_are_not_moved_into_spain(self):
        for lat, lon in [(51.5, -0.1), (35.68, 139.76), (-33.87, 151.21), (37.77, -122.42), (89.9, 179.9), (-89.9, -179.9)]:
            bbox, source, _ = server.resolve_viewport({"lat": [str(lat)], "lon": [str(lon)]})
            self.assertEqual(source, "geo")
            self.assertTrue(server.in_bbox(lat, lon, bbox))
            self.assertTrue(all(math.isfinite(x) for x in bbox.values()))
            self.assertLessEqual(bbox["latitude_ne"] - bbox["latitude_sw"], server.VIEWPORT_MAX_SPAN_DEG + 1e-8)
            self.assertTrue(server.bbox_contains(server.snapped_bbox_envelope(bbox), bbox))

    def test_dateline_filter_and_containment(self):
        outer = server.parse_bbox_param({"bbox": ["-20,170,20,-170"]})
        inner = server.parse_bbox_param({"bbox": ["-5,179,5,-179"]})
        for lon in (179.9, -179.9):
            self.assertTrue(server.in_bbox(0, lon, inner))
        self.assertFalse(server.in_bbox(0, 0, inner))
        self.assertTrue(server.bbox_contains(outer, inner))
        self.assertFalse(server.bbox_contains(inner, outer))
        self.assertEqual(server.bbox_center(inner), (0, -180))
        clamped, _ = server.clamp_bbox(inner)
        self.assertGreater(clamped["longitude_sw"], clamped["longitude_ne"])

    def test_invalid_and_nonfinite_boxes_fail_closed(self):
        for raw in ("nan,0,2,3", "-2,0,inf,3", "-91,0,2,3", "0,-181,1,1", "2,0,1,1"):
            self.assertIsNone(server.parse_bbox_param({"bbox": [raw]}))
        for value in (float("nan"), float("inf"), "-inf"):
            self.assertIsNone(server.safe_float(value))
        self.assertFalse(server.source_health_has_valid_coordinates({"lat": 95, "lon": 3}))

    def test_radius_is_positive_and_bounded_at_poles(self):
        for radius in (-1, 0, 1e9):
            box = server.bbox_from_center(90, 180, radius)
            self.assertTrue(server.in_bbox(90, -180, box))
            self.assertLessEqual(box["latitude_ne"], 90)
            self.assertGreaterEqual(box["latitude_sw"], -90)

    def test_world_geocode_does_not_send_country_restriction(self):
        response = io.BytesIO(json.dumps([{"lat": "35.68", "lon": "139.76", "display_name": "Tokyo"}]).encode())
        with patch.object(server, "GEOCODE_CACHE", {}), patch.object(server.urllib.request, "urlopen", return_value=response) as fetch:
            rows = server.geocode_search("Tokyo")
        self.assertEqual(rows[0]["lat"], 35.68)
        self.assertNotIn("countrycodes", parse_qs(urlsplit(fetch.call_args.args[0].full_url).query))


class GlobalEvidenceTests(unittest.TestCase):
    def test_non_euro_tariff_never_becomes_euros_or_cheap(self):
        row = server._normalize_lead(directory(), "directory")
        self.assertIsNone(row["price_min"])
        self.assertFalse(row["is_cheap"])
        self.assertEqual(row["price_native_min"], 25)
        self.assertEqual(row["price_currency"], "JPY")
        self.assertIn("JPY", row["price_label"])
        self.assertNotIn("€", row["price_label"])
        self.assertIsNone(row["tesla_ok"])
        self.assertNotEqual(row["decision"]["level"], "avoid")
        self.assertFalse(row["verification"]["live_verified"])
        compact = server.compact_station_for_ui(row)
        self.assertEqual(compact["country_code"], "JP")
        self.assertEqual(compact["price_currency"], "JPY")
        view = agent_mcp.charger_view(row, server)
        self.assertIsNone(view["price"]["lowest_published_eur_kwh"])
        self.assertIsNone(agent_mcp._price_now(row, server))

    def test_snapshot_coordinates_are_map_eligible_without_tariff_or_live_authority(self):
        from scripts.global_sources import normalize_ocm, make_record
        from scripts.ingest_sources import FetchArtifact
        artifact = FetchArtifact(body=b"", url="https://example.org/data", cache_name="fixture", fetched_at="2026-10-01T00:00:00Z", age_seconds=0, hit=False, fallback=False, stale=False)
        ocm = normalize_ocm({"ID": 12345, "AddressInfo": {"Title": "Tokyo", "Latitude": 35.68, "Longitude": 139.76, "Country": {"ISOCode": "JP"}}, "DataProvider": {"Title": "OpenChargeMap Contributors", "IsOpenDataLicensed": True, "License": "CC BY 4.0"}, "Connections": []}, artifact)
        official = make_record(source_id="national", upstream_id="1", source="Official registry", source_url="https://example.org/registry", artifact=artifact, country_code="FR", name="Paris", lat=48.85, lon=2.35, connectors=[], raw={})
        for raw in (ocm, official):
            self.assertIsNotNone(raw)
            row = server._normalize_lead(raw, "directory")
            self.assertTrue(server.source_health_map_eligible(row))
            self.assertTrue(row["verification"]["location_verified"])
            self.assertFalse(row["verification"]["price_verified"])
            self.assertFalse(row["verification"]["live_verified"])
            self.assertFalse(server.route_ready_now(row))

    def test_native_tariff_keeps_units_and_conditions(self):
        for unit in ("minute", "session", None):
            raw = directory(price=None)
            raw.update(price_native_min=2, tariff={"amount": 2, "unit": unit})
            row = server._normalize_lead(raw, "directory")
            self.assertNotIn("/kWh", row["price_label"])
            self.assertIn(unit or "unit unconfirmed", row["price_label"])
        raw = directory(price=None)
        raw.update(price_native_min=2, tariff={"amount": 2, "unit": "kWh", "conditional": True, "raw_text": "Members only: JPY 2/kWh"})
        row = server._normalize_lead(raw, "directory")
        self.assertEqual(row["price_label"], "Members only: JPY 2/kWh")
        self.assertIsNone(row["price_min"])

    def test_directory_socket_counts_use_explicit_quantities_and_unknown_status(self):
        raw = directory()
        raw["source_integration"] = {"mode": "community_snapshot"}
        raw["connectors"] = [{"label": "CCS1", "count": 12, "status": "OPERATIONAL"}]
        row = server._normalize_lead(raw, "directory")
        self.assertEqual(row["connector_summary"]["total"], 12)
        self.assertEqual(row["connector_summary"]["unknown"], 12)
        self.assertEqual(row["available"], 0)
        raw["connectors"] = [{"label": "CCS1"}, {"label": "NACS"}]
        row = server._normalize_lead(raw, "directory")
        self.assertIsNone(row["connector_summary"]["total"])
        self.assertIsNone(row["total_evse"])

    def test_public_map_socket_quantities_never_become_live_availability(self):
        raw = directory(lat=28.4, lon=-16.5, country="ES", currency="EUR", price=0)
        raw.update(source="Canarias te recarga public map", reliability="directory-page",
                   source_integration={"mode": "public_map_snapshot"},
                   connectors=[{"label": "Type 2", "count": 3, "status": "AVAILABLE"},
                               {"label": "CCS2", "count": 1, "status": "CHARGING"}])
        row = server._normalize_lead(raw, "directory")
        self.assertEqual(row["connector_summary"]["total"], 4)
        self.assertEqual(row["connector_summary"]["unknown"], 4)
        self.assertEqual(row["connector_count"], 4)
        self.assertEqual(row["total_evse"], 4)
        self.assertEqual(row["available"], 0)
        self.assertEqual(row["charging"], 0)
        self.assertFalse(row["verification"]["live_verified"])
        self.assertFalse(server.route_ready_now(row))

    def test_public_map_partial_socket_quantity_stays_unknown(self):
        raw = directory(lat=28.4, lon=-16.5, country="ES", currency="EUR", price=None)
        raw.update(source="Canarias te recarga public map", reliability="directory-page",
                   source_integration={"mode": "public_map_snapshot"},
                   connector_count=8, total_evse=8,
                   connectors=[{"label": "Type 2", "count": 3, "status": "AVAILABLE"},
                               {"label": "CCS2", "status": "AVAILABLE"}])
        row = server._normalize_lead(raw, "directory")
        self.assertIsNone(row["connector_summary"]["total"])
        self.assertIsNone(row["connector_count"])
        self.assertIsNone(row["total_evse"])
        self.assertEqual(row["available"], 0)
        self.assertFalse(row["verification"]["live_verified"])

    def test_nonlive_official_lead_uses_inventory_counts_and_clears_stale_availability(self):
        raw = directory(lat=28.4, lon=-16.5, country="ES", currency="EUR", price=None)
        raw.update(source="DGT National Access Point charging registry",
                   reliability="official-charger-location-price-unknown",
                   source_integration={"mode": "public_source", "live_status_integrated": False,
                                       "source_modes": ["public_source", "official_ocpi_snapshot", "public_map_snapshot"]},
                   available=2, charging=1, out_of_service=1,
                   connectors=[{"label": "Type 2", "count": 2, "status": "AVAILABLE"}])
        row = server._normalize_lead(raw, "directory")
        self.assertTrue(row["lead"])
        self.assertEqual(row["connector_summary"]["total"], 2)
        self.assertEqual(row["connector_summary"]["unknown"], 2)
        self.assertEqual(row["connector_count"], 2)
        self.assertEqual(row["total_evse"], 2)
        self.assertEqual(row["available"], 0)
        self.assertEqual(row["available_connectors"], 0)
        self.assertEqual(row["charging"], 0)
        self.assertEqual(row["out_of_service"], 0)
        self.assertFalse(row["verification"]["live_verified"])
        self.assertFalse(server.route_ready_now(row))

    def test_non_euro_pricing_windows_do_not_pass_euro_cap(self):
        status = server.pricing_status([{"price": 0.1, "currency": "USD", "unit": "kWh"}])
        self.assertFalse(status["is_cheap"])
        self.assertIsNone(status["current_price_min"])
        self.assertIsNone(status["price_min"])

    def test_unknown_currency_is_not_assumed_euro_abroad(self):
        row = directory(currency=None, price=0.1)
        row = server._normalize_lead(row, "directory")
        self.assertEqual(row["price_currency"], "UNKNOWN")
        self.assertIsNone(row["price_min"])
        self.assertFalse(row.get("is_cheap"))

    def test_world_directory_never_calls_spain_feeds_or_claims_failure(self):
        row = server._normalize_lead(directory(), "directory")
        with patch.object(server, "load_all_leads", return_value=[row]), patch.object(server, "fetch_mapareve") as reve, patch.object(server, "fetch_gaia_live") as gaia:
            status, response = server.live_response({"lat": ["35.68"], "lon": ["139.76"], "include_leads": ["true"], "max_price": ["5"]})
        self.assertEqual(status, 200)
        self.assertEqual(len(response["leads"]), 1)
        self.assertEqual(response["stations"], [])
        self.assertFalse(response["cache"]["live_failed"])
        self.assertFalse(response["meta"]["coverage"]["supported"])
        reve.assert_not_called()
        gaia.assert_not_called()

    def test_primary_sources_are_geography_gated_independently(self):
        with patch.object(server, "fetch_mapareve", return_value={"stations": []}) as reve, patch.object(server, "fetch_gaia_live") as gaia:
            result = server.fetch_primary_sources_parallel(fetch_mapareve_now=True, max_price=5, available_only=False, fast_only=False, bbox=server.bbox_from_center(40.4, -3.7, 3), force=False)
        reve.assert_called_once()
        gaia.assert_not_called()
        self.assertFalse(result["gaia"]["payload"]["supported"])
        self.assertIsNone(result["gaia"]["error"])

    def test_mcp_world_search_and_explicit_euro_filter(self):
        row = server._normalize_lead(directory("tokyo", currency="USD", price=0.1), "directory")
        with patch.object(server, "load_all_leads", return_value=[row]):
            found = agent_mcp.find_chargers({"lat": 35.68, "lon": 139.76}, server)["structured"]
            priced = agent_mcp.find_chargers({"lat": 35.68, "lon": 139.76, "max_price_eur_kwh": 1}, server)["structured"]
        self.assertEqual(found["count"], 1)
        self.assertFalse(found["live"]["supported"])
        self.assertEqual(priced["count"], 0)
        self.assertEqual(priced["excluded"]["price_unknown"], 1)
        self.assertEqual(agent_mcp._resolve_origin({"lat": 51.5, "lon": -0.1}, server)[:2], (51.5, -0.1))

    def test_mcp_connector_keys_distinguish_world_standards(self):
        self.assertIsNone(agent_mcp._connector_key("CCS (unspecified)"))
        self.assertIsNone(agent_mcp._connector_key("CCS unknown"))
        self.assertEqual(agent_mcp._connector_key("CCS"), "ccs2")
        for value, canonical in [("CCS1", "ccs1"), ("CCS2", "ccs2"), ("J1772", "type 1"), ("NACS", "nacs"), ("GB/T DC", "gb/t dc"), ("GB/T AC", "gb/t ac")]:
            self.assertEqual(agent_mcp._connector_key(value), canonical)

    def test_global_stored_detail_uses_raw_index_and_keeps_directory_evidence(self):
        data = {"records": [directory("tokyo-detail")]}
        with patch.object(server, "load_intelligence_meta", return_value=data), patch.object(server, "load_seed_leads", return_value=[]), patch.object(server, "_intel_enriched_pool", side_effect=AssertionError("whole world enrichment")), patch.object(server, "load_all_leads", return_value=[]):
            result = agent_mcp.get_charger({"id": "tokyo-detail"}, server)["structured"]
        self.assertEqual(result["country_code"], "JP")
        self.assertFalse(result["evidence"]["live_verified"])
        self.assertEqual(result["price"]["currency"], "JPY")


class WorldScaleTests(unittest.TestCase):
    def test_streamed_records_share_schema_keys_and_repeated_values(self):
        decoder = json.JSONDecoder(object_pairs_hook=server._interning_object_hook())
        first, _ = decoder.raw_decode('{"source_coordinate_verification_attribute":"shared source evidence"}')
        second, _ = decoder.raw_decode('{"source_coordinate_verification_attribute":"shared source evidence"}')
        first_key, second_key = next(iter(first)), next(iter(second))
        self.assertIs(first_key, second_key)
        self.assertIs(first[first_key], second[second_key])
        first[first_key] = "one record changes"
        self.assertEqual(second[second_key], "shared source evidence")

    def test_streamed_hash_matches_existing_canonical_fingerprint(self):
        rows = [directory("é"), directory("a"), {"id": "z", "nested": {"unicode": "東京", "value": None}}]
        self.assertEqual(server.runtime_recordset_sha256(rows), server.recordset_sha256(rows))
        self.assertEqual(server.runtime_recordset_sha256([]), server.recordset_sha256([]))

    def test_world_overview_caps_before_enrichment_and_labels_truncation(self):
        raw = {"records": [directory(str(i)) for i in range(5100)]}
        def normalized(row, source):
            return dict(row, lead=True, price_min=None, recommendation_score=0)
        with patch.object(server, "load_intelligence_meta", return_value=raw), patch.object(server, "_INTEL_SCOPE_THRESHOLD", 1), patch.object(server, "_raw_seed_records", return_value=[]), patch.object(server, "_normalize_lead", side_effect=normalized) as normalize, patch.object(server, "apply_corroboration"), patch.object(server, "attach_community_rows", side_effect=lambda rows: rows):
            _, result = server.overview_response({"bbox": ["-90,-180,90,180"]}, 5.0, False)
        self.assertEqual(normalize.call_count, 5000)
        self.assertEqual(len(result["leads"]), 2500)
        self.assertTrue(result["meta"]["truncated"])
        self.assertEqual(result["meta"]["candidate_records"], 5100)

    def test_large_bundle_enriches_only_viewport_records_and_returns_copies(self):
        rows = [directory("tokyo"), directory("sydney", -33.87, 151.21, "AU", "AUD")]
        raw = {"records": rows}
        bbox = server.bbox_from_center(35.68, 139.76, 1)
        with patch.object(server, "load_intelligence_meta", return_value=raw), patch.object(server, "_INTEL_SCOPE_THRESHOLD", 1), patch.object(server, "_INTEL_SCOPED", {}), patch.object(server, "_raw_seed_records", return_value=[]), patch.object(server, "_intel_enriched_pool", side_effect=AssertionError("whole world enrichment")), patch.object(server, "_normalize_lead", wraps=server._normalize_lead) as normalize:
            first = server.load_all_leads(bbox)
            first[0]["name"] = "Caller mutation"
            second = server.load_all_leads(bbox)
        self.assertEqual(normalize.call_count, 1)
        self.assertEqual(second[0]["name"], "Public charger")
        self.assertEqual(second[0]["id"], "tokyo")

    def test_dense_area_preserves_nearest_station_and_mcp_discloses_truncation(self):
        rows = [directory(str(i), lat=35.75) for i in range(5100)] + [directory("zzz-nearest")]
        raw = {"records": rows}
        def normalized(row, source):
            return dict(row, lead=True, price_min=None, recommendation_score=0, decision={"level": "lead"})
        with patch.object(server, "load_intelligence_meta", return_value=raw), patch.object(server, "_INTEL_SCOPE_THRESHOLD", 1), patch.object(server, "_raw_seed_records", return_value=[]), patch.object(server, "_normalize_lead", side_effect=normalized), patch.object(server, "apply_corroboration"), patch.object(server, "attach_community_rows", side_effect=lambda rows: rows):
            result = agent_mcp.find_chargers({"lat": 35.68, "lon": 139.76, "radius_km": 25}, server)["structured"]
        self.assertEqual(result["chargers"][0]["id"], "zzz-nearest")
        self.assertTrue(result["truncated"])
        self.assertFalse(result["total_matches_is_complete"])
        self.assertEqual(result["candidate_records"], 5101)

    def test_intel_inspection_pages_before_normalizing(self):
        raw = {"records": [directory(str(i)) for i in range(5)], "recordset_sha256": "proof"}
        with patch.object(server, "load_intelligence_meta", return_value=raw), patch.object(server, "_normalize_lead", wraps=server._normalize_lead) as normalize:
            result = server.intelligence_response({"limit": ["2"], "offset": ["1"]})
        self.assertEqual(normalize.call_count, 2)
        self.assertEqual([r["id"] for r in result["records"]], ["1", "2"])
        self.assertEqual(result["pagination"], {"limit": 2, "offset": 1, "returned": 2, "matched": 5, "next_offset": 3})
        self.assertEqual(result["recordset_sha256"], "proof")

    def test_source_health_counts_countries_without_full_enrichment(self):
        rows = [directory("jp"), directory("au", -33.87, 151.21, "AU", "AUD")]
        manifests = [{"source": "ocm", "status": "partial", "accepted": 2}]
        raw = {"records": rows, "global_coverage": {"source_manifests": manifests}}
        with patch.object(server, "load_intelligence_meta", return_value=raw), patch.object(server, "_SOURCE_INVENTORY", {}), patch.object(server, "_INTEL_SCOPE_THRESHOLD", 1), patch.object(server, "_raw_seed_records", return_value=[]), patch.object(server, "_normalize_lead", side_effect=AssertionError("full enrichment")), patch.object(server, "load_intelligence", side_effect=AssertionError("deep copy full DB")):
            result = server.source_health_summary()
        self.assertEqual(result["intelligence"]["by_country"], {"AU": 1, "JP": 1})
        self.assertNotIn("outside_spain", result["intelligence"]["coordinate_issues"])
        self.assertEqual(result["source_manifest"], manifests)


class IndexedRuntimeTests(unittest.TestCase):
    """Public runtime paths must query the index without decoding the catalogue."""

    def setUp(self):
        from scripts.build_intelligence_index import build_index
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        (self.folder / "source_policy_exclusions.json").write_bytes((server.DATA / "source_policy_exclusions.json").read_bytes())
        (self.folder / "seed_leads.json").write_text("[]")
        self.rows = [directory("global_ocm_tokyo"), directory("global_ocm_sydney", -33.87, 151.21, "AU", "AUD"),
                     directory("east", 0, 179.9, "FJ", "FJD"), directory("west", 0, -179.9, "FJ", "FJD")]
        self.rows[0]["merged_ids"] = ["tokyo-alias"]
        # Additive counters must be computed during indexing, not by scanning at request time.
        self.rows[1]["price_evidence"] = [{"kind": "chargemap_pass", "source": "Chargemap",
            "label": "Price with Chargemap Pass", "text": "Synthetic tariff fixture",
            "source_url": "https://map.chargemap.com/pool-prices/v3/prices/1",
            "source_updated_at": None, "fetched_at": "2026-10-20T12:00:00Z",
            "price_per_kwh": None, "currency": "EUR"}]
        self.source = self.folder / "source.json"
        self.source.write_text(json.dumps({"records": self.rows, "generated_at": "2026-10-13T00:00:00Z"}))
        self.index_path = self.folder / "catalogue.sqlite"
        with patch.object(server, "_SOURCE_INVENTORY", {}):
            build_index(self.source, self.index_path, policy_path=self.folder / "source_policy_exclusions.json",
                        seeds_path=self.folder / "seed_leads.json", health_summary=True)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for context in (patch.object(server, "DATA", self.folder),
                        patch.dict(os.environ, {"VOLTA_INTELLIGENCE_DB": str(self.index_path)}),
                        patch.object(server, "_INTEL_RAW", {"mtime": None, "data": None}),
                        patch.object(server, "_INTEL_SCOPED", {}),
                        patch.object(server, "_KNOWN_IDS_CACHE", {"ts": 0, "ids": set()}),
                        patch.object(server.IndexedRecords, "__iter__", side_effect=AssertionError("full catalogue iteration")),
                        patch.object(server, "_intel_enriched_pool", side_effect=AssertionError("full catalogue enrichment")),
                        patch.object(server, "load_json_recordset", side_effect=AssertionError("JSON load in indexed runtime"))):
            self.stack.enter_context(context)

    def test_metadata_health_and_source_catalogue_use_precomputed_summaries(self):
        raw = server.load_intelligence_meta()
        self.assertIsInstance(raw["records"], server.IndexedRecords)
        self.assertEqual(len(raw["records"]), 4)
        health = server.source_health_summary()
        self.assertEqual(health["intelligence"]["by_country"], {"AU": 1, "FJ": 2, "JP": 1})
        self.assertEqual(health["intelligence"]["price_evidence_counts"]["chargemap_pass"], 1)
        self.assertEqual(server.requested_source_strategy()["price_evidence_counts"]["total"], 1)
        self.assertEqual(server.global_source_counts(raw["records"])["adapters"], {"ocm": 2})
        self.assertEqual(server.fenie_origin_record_count(raw["records"]), 0)
        self.assertEqual(server.intelligence_counts(raw)["free_records"], 0)
        self.assertEqual(server.known_station_ids(), {row["id"] for row in self.rows})
        self.assertEqual(next(item for item in server.global_adapter_catalog() if item["id"] == "ocm")["records_added_by_global_adapter"], 2)

    def test_world_pagination_and_dateline_filter_are_indexed(self):
        with patch.object(server, "_normalize_lead", wraps=server._normalize_lead) as normalize:
            page = server.intelligence_response({"offset": ["1"], "limit": ["1"]})
            self.assertEqual(normalize.call_count, 1)
        self.assertEqual(page["records"][0]["id"], "global_ocm_sydney")
        self.assertEqual(page["pagination"]["matched"], 4)
        self.assertEqual(page["pagination"]["next_offset"], 2)
        _, dateline = server.overview_response({"bbox": ["-1,179,1,-179"]}, 5, False)
        self.assertEqual({row["id"] for row in dateline["leads"]}, {"east", "west"})
        self.assertFalse(dateline["meta"]["truncated"])

    def test_indexed_search_and_alias_detail_keep_foreign_currency_nonlive(self):
        with patch.object(server, "fetch_mapareve", side_effect=AssertionError("Spain feed abroad")), patch.object(server, "fetch_gaia_live", side_effect=AssertionError("Spain feed abroad")):
            result = agent_mcp.find_chargers({"lat": 35.68, "lon": 139.76}, server)["structured"]
            detail = agent_mcp.get_charger({"id": "tokyo-alias"}, server)["structured"]
        self.assertEqual(result["count"], 1)
        self.assertFalse(result["live"]["supported"])
        self.assertEqual(detail["id"], "global_ocm_tokyo")
        self.assertEqual(detail["price"]["currency"], "JPY")
        self.assertFalse(detail["evidence"]["live_verified"])
        _, filtered = server.overview_response({"bbox": ["35,139,36,140"]}, 1, False)
        self.assertEqual(filtered["leads"], [])

    def test_viewport_rows_remain_independent_and_unbounded_helper_is_rejected(self):
        bbox = server.bbox_from_center(35.68, 139.76, 2)
        first = server.load_all_leads(bbox)
        first[0]["name"] = "Caller changes a row"
        self.assertEqual(server.load_all_leads(bbox)[0]["name"], "Public charger")
        with self.assertRaisesRegex(ValueError, "requires a viewport"):
            server.load_all_leads()

    def test_changed_seed_or_classification_summary_fails_closed(self):
        with patch.object(server, "intelligence_health_fingerprint", return_value="changed-code"):
            with self.assertRaisesRegex(ValueError, "summary is stale"):
                server.load_intelligence_meta()
        server.load_intelligence_meta()
        (self.folder / "seed_leads.json").write_text('[{"id":"new-seed"}]')
        with self.assertRaisesRegex(ValueError, "summary is stale"):
            server.load_intelligence_meta()

    def test_changed_source_policy_fails_closed(self):
        server.load_intelligence_meta()
        policy = self.folder / "source_policy_exclusions.json"
        policy.write_bytes(policy.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "source policy differs"):
            server.load_intelligence_meta()


if __name__ == "__main__":
    unittest.main()
