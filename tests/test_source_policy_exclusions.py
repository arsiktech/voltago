#!/usr/bin/env python3
"""Fail-closed contracts for sources retired by explicit policy."""
from __future__ import annotations

import sys
import json
import tempfile
import time
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import server  # noqa: E402
import ingest_sources as ingestion  # noqa: E402
from source_policy import load_source_policy_exclusions, partition_records, source_family_is_excluded  # noqa: E402


class SourcePolicyArtifactTests(unittest.TestCase):
    @staticmethod
    def _live_station(station_id: str, source: str, *, nested_source: str | None = None) -> dict:
        source_url = f"https://example.invalid/{station_id}"
        sources = [{"name": source, "url": source_url}]
        if nested_source:
            sources.append({"name": nested_source, "url": "https://example.invalid/secondary"})
        return {
            "id": station_id,
            "name": f"Station {station_id}",
            "address": "Carrer Test 1, València",
            "lat": 39.47 + (0.01 if station_id == "nested" else 0),
            "lon": -0.37,
            "source": source,
            "source_url": source_url,
            "sources": sources,
            "source_type": "public-live",
            "reliability": "official-live",
            "price_min": 0.0,
            "price_max": 0.0,
            "is_free": True,
            "is_cheap": True,
            "status": "AVAILABLE",
            "connectors": [{
                "connector_id": f"{station_id}-1",
                "label": "Type 2",
                "type": "Type 2",
                "status": "AVAILABLE",
                "power_kw": 22.0,
                "price": "Free",
                "is_free": True,
                "tesla_ok": True,
                "fast": False,
            }],
        }

    def test_primary_and_nested_electromaps_evidence_match_the_explicit_exclusion(self) -> None:
        # Feníe is now a permitted, active primary source, so the blocked-mechanism
        # coverage that used Feníe as a generic example is repointed to Electromaps
        # (still rights-blocked). The independent-operator-mention allowance below
        # still uses Feníe on purpose: it must remain allowed via OpenChargeMap.
        fixtures = [
            {"id": "primary", "source": "Electromaps web-map API"},
            {
                "id": "nested",
                "source": "OpenChargeMap public snapshot",
                "sources": [{"name": "Electromaps directory", "url": "https://example.invalid/station"}],
            },
            {
                "id": "url",
                "source": "Directory",
                "source_url": "https://www.electromaps.com/es/punto-de-carga/1",
            },
            {
                "id": "media-photo",
                "source": "Directory",
                "media": {"photos": [{
                    "url": "https://images.example.invalid/photo.jpg",
                    "source_url": "https://map.electromaps.com/en/p/2",
                }]},
            },
            {
                "id": "connector-provenance",
                "source": "Directory",
                "connectors": [{
                    "label": "Type 2",
                    "provenance": {"name": "Electromaps directory"},
                }],
            },
        ]

        for fixture in fixtures:
            with self.subTest(fixture=fixture["id"]):
                self.assertFalse(server.source_policy_record_allowed(fixture))

        self.assertTrue(server.source_policy_record_allowed({
            "id": "safe",
            "source": "OpenChargeMap public snapshot",
            "source_url": "https://openchargemap.io/fenie-energia-independent-listing.html",
            "name": "Fenie Pla de na Tesa",
            "owner": {"name": "FENÍE ENERGÍA"},
            "free_condition": "Verify in the Fenie app before driving.",
            "raw": {"operator": "Fenie Energia"},
            "sources": [{"name": "OpenChargeMap", "url": "https://openchargemap.io/site/poi/details/1"}],
            "connectors": [{
                "source_url": "https://openchargemap.io/fenie-energia-independent-listing.html",
                "name": "Fenie-branded Type 2 connector",
            }],
            "media": {"photos": [{
                "url": "https://images.example.invalid/fenie-station.jpg",
                "source": "OpenChargeMap media",
                "source_url": "https://openchargemap.io/fenie-independent-photo",
                "caption": "Fenie station photographed by an independent directory",
            }]},
        }))

    def test_commercial_station_evidence_without_reuse_permission_matches_explicit_exclusions(self) -> None:
        fixtures = [
            {"id": "electromaps-primary", "source": "Electromaps web-map API"},
            {
                "id": "electromaps-photo",
                "source": "OpenChargeMap",
                "media": {"photos": [{"source_url": "https://map.electromaps.com/en/p/42"}]},
            },
            {"id": "chargemap-primary", "source": "Chargemap public map API"},
            {"id": "chargemap-bare", "source": "Chargemap"},
            {"id": "chargemap-directory", "source": "Chargemap directory"},
            {
                "id": "chargemap-nested",
                "source": "OpenStreetMap",
                "sources": [{"name": "Independent", "url": "https://chargemap.com/example.html"}],
            },
            {"id": "consum-primary", "source": "Consum store locator"},
            {"id": "consum-bare", "source": "Consum"},
            {"id": "consum-official", "source": "Consum official"},
            {
                "id": "consum-nested",
                "source": "OpenChargeMap",
                "sources": [{"name": "Consum store locator", "url": "https://www.consum.es/supermercados/test/"}],
            },
            {
                "id": "electromaps-raw-source",
                "source": "OpenStreetMap public tags",
                "raw": {"operator": "Electromaps", "source": "https://www.electromaps.com"},
            },
            {
                "id": "chargemap-raw-source-url",
                "source": "OpenStreetMap public tags",
                "raw": {"source_url": "https://chargemap.com/stations/example.html"},
            },
            {
                "id": "electromaps-raw-deep-website",
                "source": "OpenStreetMap public tags",
                "raw": {"website": "https://www.electromaps.com/puntos-de-recarga/42_station"},
            },
            {
                "id": "chargemap-pricing-source",
                "source": "OpenStreetMap",
                "pricing": [{"price": 0, "source": "Chargemap public map API"}],
            },
            {
                "id": "chargemap-pricing-url",
                "source": "OpenStreetMap",
                "pricing": [{"price": 0, "source_url": "https://chargemap.com/station/x"}],
            },
        ]

        for fixture in fixtures:
            with self.subTest(fixture=fixture["id"]):
                self.assertFalse(server.source_policy_record_allowed(fixture))

        self.assertTrue(server.source_policy_record_allowed({
            "id": "independent-operator-mention",
            "source": "OpenStreetMap public tags",
            "raw": {
                "operator": "Electromaps",
                "website": "https://www.electromaps.com/",
            },
        }))
        self.assertTrue(server.source_policy_record_allowed({
            "id": "openchargemap-boundary",
            "source": "OpenChargeMap public snapshot",
        }))

        policy = load_source_policy_exclusions()
        self.assertTrue(source_family_is_excluded("Chargemap", policy))
        self.assertTrue(source_family_is_excluded("Chargemap directory", policy))
        self.assertTrue(source_family_is_excluded("Consum", policy))
        self.assertTrue(source_family_is_excluded("Consum official", policy))
        self.assertFalse(source_family_is_excluded("OpenChargeMap", policy))

        ids = {row["id"] for row in load_source_policy_exclusions()["exclusions"]}
        self.assertIn("electromaps-reuse-rights", ids)
        self.assertIn("chargemap-reuse-and-robots", ids)
        self.assertIn("consum-locator-reuse-permission", ids)

    def test_independent_mapareve_live_row_may_keep_provider_homepage_as_owner_metadata(self) -> None:
        row = self._live_station("mapareve-owner-home", "MapaREVE public live OCPI")
        row["source_url"] = "https://www.mapareve.es/"
        row["sources"] = [{
            "name": "MapaREVE public live OCPI",
            "url": "https://www.mapareve.es/",
        }]
        row["owner"] = {
            "name": "ELECTROMAPS SL",
            "website": "https://www.electromaps.com/",
        }

        self.assertTrue(server.source_policy_record_allowed(row))

    def test_malformed_provenance_urls_do_not_crash_policy_partitioning(self) -> None:
        policy = load_source_policy_exclusions()
        rows = [
            {
                "id": "malformed-primary-url",
                "source": "Independent directory",
                "source_url": "http://[bad",
            },
            {
                "id": "malformed-nested-url",
                "source": "Independent directory",
                "sources": [{"name": "Independent mirror", "url": "http://[bad"}],
            },
            {
                "id": "blocked-family-with-malformed-url",
                "source": "Independent directory",
                "sources": [{"name": "Electromaps directory", "url": "http://[bad"}],
            },
        ]

        allowed, excluded = partition_records(rows, policy)

        self.assertEqual(
            [row["id"] for row in allowed],
            ["malformed-primary-url", "malformed-nested-url"],
        )
        self.assertEqual(
            [row["id"] for row in excluded],
            ["blocked-family-with-malformed-url"],
        )

    def test_every_claim_and_top_level_photo_provenance_is_policy_partitioned(self) -> None:
        for claim_key in (
            "charge_claim",
            "parking_claim",
            "location_claim",
            "hardware_claim",
            "access_claim",
        ):
            for evidence in (
                {"source": "Electromaps directory"},
                {"url": "https://www.electromaps.com/es/punto-de-carga/42"},
            ):
                with self.subTest(claim=claim_key, evidence=evidence):
                    self.assertFalse(server.source_policy_record_allowed({
                        "id": f"claim-{claim_key}",
                        "source": "Independent directory",
                        claim_key: evidence,
                    }))

        self.assertFalse(server.source_policy_record_allowed({
            "id": "top-level-photo",
            "source": "Independent directory",
            "photos": [{"source_url": "https://map.electromaps.com/en/p/43"}],
        }))

    def test_fenie_collector_is_active_offline_only_under_narrow_post_exception(self) -> None:
        active = {fn.__name__ for fn in ingestion.ACTIVE_SOURCE_ADAPTERS}
        self.assertIn("source_fenie", active)
        self.assertTrue(callable(ingestion._fenie_fetch_markers_bytes))
        # No runtime (request-time) Feníe fetch: it stays an offline ingestion source.
        self.assertFalse(hasattr(server, "fetch_fenie_live"))
        self.assertEqual(server.FENIE_SOURCE_STATUS, "permitted-owner-attested-snapshot")

    def test_fenie_post_exclusion_is_lifted_but_grant_and_other_exclusions_remain(self) -> None:
        fenie_free_station = {
            "id": "fenie-0459",
            "source": "Feníe Energía public map (Gratuitos)",
            "source_url": "https://recarga.fenieenergia.es/punto-recarga/0459",
            "sources": [{
                "name": "Feníe Energía public map (Gratuitos)",
                "url": "https://recarga.fenieenergia.es/punto-recarga/0459",
                "attribution": "Feníe Energía — Red de recarga (public map)",
            }],
            "is_free": True,
        }
        self.assertTrue(server.source_policy_record_allowed(fenie_free_station))

        policy = load_source_policy_exclusions()
        exclusion_ids = {row["id"] for row in policy["exclusions"]}
        self.assertNotIn("fenie-public-post-method", exclusion_ids)
        self.assertFalse(source_family_is_excluded("Feníe Energía public map (Gratuitos)", policy))
        granted = {row.get("id"): row for row in policy.get("granted_reuse", [])}
        self.assertIn("fenie-owner-attested-permission", granted)
        self.assertEqual(granted["fenie-owner-attested-permission"]["status"], "permitted")
        # The three rights-blocked families must stay excluded, unchanged and enforced.
        self.assertEqual(
            exclusion_ids,
            {"electromaps-reuse-rights", "chargemap-reuse-and-robots", "consum-locator-reuse-permission", "plugshare-unreviewed-credential"},
        )
        for blocked_row in (
            {"id": "em", "source": "Electromaps web-map API", "source_url": "https://www.electromaps.com/es/punto-de-carga/1"},
            {"id": "cm", "source": "Chargemap public map API", "source_url": "https://chargemap.com/x"},
            {"id": "cs", "source": "Consum store locator", "source_url": "https://www.consum.es/tiendas/x"},
        ):
            with self.subTest(blocked=blocked_row["id"]):
                self.assertFalse(server.source_policy_record_allowed(blocked_row))

        independent_row = {
            "id": "dgt-fenie-operator",
            "source": "DGT / MITECO Punto de Acceso Nacional",
            "source_url": "https://nap.dgt.es/electromobility.xml",
            "operator": "Feníe Energía",
            "raw": {"operator": "Feníe Energía"},
        }
        self.assertTrue(server.source_policy_record_allowed(independent_row))

    def test_retired_directory_adapters_and_live_detail_refresh_are_inactive(self) -> None:
        active = {fn.__name__ for fn in ingestion.ACTIVE_SOURCE_ADAPTERS}
        self.assertNotIn("source_electromaps_mapi", active)
        self.assertNotIn("source_electromaps_probe", active)
        self.assertNotIn("source_electromaps_photo_second_pass", active)
        self.assertNotIn("source_chargemap_mappy", active)
        self.assertNotIn("source_consum", active)
        self.assertFalse(server.ELECTROMAPS_DIRECT_ENABLED)

    def test_runtime_image_packages_required_policy_artifacts(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("COPY scripts/source_policy.py ./scripts/source_policy.py", dockerfile)
        self.assertIn("COPY scripts/source_transport.py ./scripts/source_transport.py", dockerfile)
        self.assertIn("data/source_policy_exclusions.json", dockerfile)

    def test_source_strategy_and_health_report_source_statuses(self) -> None:
        strategy = server.requested_source_strategy()
        row = next(item for item in strategy["requested_apps"] if item["name"] == "Feníe Energía Recarga")
        self.assertEqual(row["status"], "permitted-owner-attested-snapshot")
        self.assertEqual(row["records_now"]["nested_station_evidence_records"], 0)
        self.assertIsInstance(row["records_now"]["direct_station_records"], int)
        self.assertEqual(row["source_url"], "https://recarga.fenieenergia.es/")

        health = server.source_health_summary()["live_cache"]["fenie"]
        self.assertEqual(health["status"], "permitted-owner-attested-snapshot")
        self.assertFalse(health["has_payload"])  # offline snapshot: no runtime payload

        for name, status in (
            ("Electromaps", "link-only-rights-blocked"),
            ("Chargemap", "removed-rights-blocked"),
            ("Consum store locator", "link-only-rights-blocked"),
        ):
            with self.subTest(name=name):
                row = next(item for item in strategy["requested_apps"] if item["name"] == name)
                self.assertEqual(row["status"], status)
                self.assertEqual(row["records_now"], {
                    "direct_station_records": 0,
                    "nested_station_evidence_records": 0,
                })

        electromaps_health = server.source_health_summary()["live_cache"]["electromaps"]
        self.assertEqual(electromaps_health["status"], "link-only-rights-blocked")
        self.assertEqual(electromaps_health["cached_details"], 0)

    def test_public_source_catalog_matches_retired_source_policy(self) -> None:
        catalog = server.source_catalog_payload()
        moat = " ".join(catalog["moat_intelligence"])
        covered = " ".join(catalog["covered_via_primary"])

        self.assertNotIn("Electromaps public browser-map API", moat)
        self.assertNotIn("Chargemap public browser-map API", moat)
        self.assertNotIn("Consum official store locator", moat)
        self.assertIn("Barcelona Endolla", moat)
        self.assertIn("Madrid", moat)
        self.assertIn("Zaragoza", moat)
        self.assertNotIn("Electromaps", covered)
        self.assertNotIn("Chargemap", covered)

    def test_ingestion_policy_clean_payload_removes_complete_nested_evidence(self) -> None:
        payload = {
            "generated_at": "2026-07-13T00:00:00Z",
            "total_records": 3,
            "raw_records": 99,
            "fetch_evidence": {
                "summary": {"total": 2, "network": 2},
                "artifacts": [
                    {
                        "url": "https://map.electromaps.com/mapi/v2/chargers",
                        "cache_name": "electromaps.json",
                        "cache": {"hit": False, "fallback": False, "stale": False},
                    },
                    {
                        "url": "https://openchargemap.io/data",
                        "cache_name": "ocm.json",
                        "cache": {"hit": True, "fallback": False, "stale": False},
                    },
                ],
            },
            "errors": [
                {"source": "source_chargemap_mappy", "error": "RuntimeError"},
                {"source": "source_openchargemap", "error": "TimeoutError"},
            ],
            "license_notes": [
                "Electromaps evidence active",
                "OpenChargeMap attribution retained",
            ],
            "records": [
                {
                    "id": "safe",
                    "name": "Fenie station from an independent directory",
                    "source": "OpenChargeMap",
                    "owner": {"name": "FENÍE ENERGÍA"},
                    "raw": {"operator": "Fenie Energia"},
                    "sources": [{"name": "OpenChargeMap", "url": "https://openchargemap.io/site/poi/details/1"}],
                },
                {"id": "primary", "source": "Electromaps web-map API"},
                {
                    "id": "nested",
                    "source": "Directory",
                    "connectors": [{"source_url": "https://map.electromaps.com/en/p/7"}],
                },
            ],
        }

        cleaned, report = ingestion.apply_source_policy_exclusions_to_payload(payload)

        self.assertEqual([row["id"] for row in cleaned["records"]], ["safe"])
        self.assertEqual(cleaned["total_records"], 1)
        self.assertEqual(cleaned["raw_records"], 1)
        self.assertEqual(report["excluded_records"], 2)
        self.assertEqual(report["excluded_ids"], ["nested", "primary"])
        self.assertEqual(report["excluded_fetch_artifacts"], 1)
        self.assertEqual(report["excluded_errors"], 1)
        self.assertEqual(cleaned["fetch_evidence"]["summary"], {
            "total": 1,
            "network": 0,
            "cache_hits": 1,
            "fallbacks": 0,
            "stale": 0,
            "stale_fallbacks": 0,
        })
        self.assertEqual(cleaned["fetch_evidence"]["artifacts"][0]["cache_name"], "ocm.json")
        self.assertEqual(cleaned["errors"], [{"source": "source_openchargemap", "error": "TimeoutError"}])
        self.assertNotIn("Electromaps evidence active", cleaned["license_notes"])
        self.assertIn("OpenChargeMap attribution retained", cleaned["license_notes"])
        self.assertIn("fenie", json.dumps(cleaned, ensure_ascii=False).lower())
        public_metadata = {
            "fetch_evidence": cleaned["fetch_evidence"],
            "errors": cleaned["errors"],
        }
        metadata_text = json.dumps(public_metadata, ensure_ascii=False).lower()
        # Feníe is now permitted, so it is intentionally NOT in this blocked-leak list;
        # these remain the still-blocked commercial sources whose evidence must be scrubbed.
        for prohibited in (
            "map.electromaps.com",
            "electromaps evidence active",
            "source_chargemap_mappy",
            "https://chargemap.com",
            "consum store locator",
        ):
            self.assertNotIn(prohibited, metadata_text)
        self.assertTrue(all(server.source_policy_record_allowed(row) for row in cleaned["records"]))

        fresh_cleaned, _ = ingestion.apply_source_policy_exclusions_to_payload(
            payload,
            preserve_raw_records=True,
        )
        self.assertEqual(fresh_cleaned["raw_records"], 99)

    def test_policy_cleaning_strips_blocked_reference_from_independent_ocm_record(self) -> None:
        row = {
            "id": "ocm-independent",
            "source": "OpenChargeMap public snapshot",
            "source_url": "https://map.openchargemap.io/?mode=details&id=23805",
            "sources": [{
                "name": "OpenChargeMap public snapshot",
                "url": "https://map.openchargemap.io/?mode=details&id=23805",
            }],
            "connectors": [{
                "label": "Type 2",
                "source_url": "https://map.openchargemap.io/?mode=details&id=23805",
                "raw": {"Reference": "Electromaps", "ConnectionTypeID": 25},
            }],
        }
        payload = {
            "records": [row],
            "raw_records": 1,
            "fetch_evidence": {"artifacts": []},
            "errors": [],
            "license_notes": [],
        }

        cleaned, report = ingestion.apply_source_policy_exclusions_to_payload(payload)

        self.assertEqual(report["excluded_records"], 0)
        self.assertEqual([record["id"] for record in cleaned["records"]], ["ocm-independent"])
        self.assertNotIn("Reference", cleaned["records"][0]["connectors"][0]["raw"])
        self.assertEqual(row["connectors"][0]["raw"]["Reference"], "Electromaps")

    def test_intelligence_loader_drops_primary_and_nested_excluded_records(self) -> None:
        original_raw = deepcopy(server._INTEL_RAW)
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "free_intelligence.json").write_text(json.dumps({
                "generated_at": "2026-07-13T00:00:00Z",
                "total_records": 3,
                "records": [
                    {
                        "id": "safe",
                        "source": "OpenChargeMap",
                        "connectors": [{
                            "label": "Type 2",
                            "raw": {"Reference": "Electromaps", "ConnectionTypeID": 25},
                        }],
                    },
                    {"id": "primary", "source": "Electromaps web-map API"},
                    {
                        "id": "nested",
                        "source": "Directory",
                        "sources": [{"name": "Electromaps directory"}],
                    },
                ],
            }), encoding="utf-8")
            try:
                server._INTEL_RAW.clear()
                server._INTEL_RAW.update({"mtime": None, "data": None})
                with patch.object(server, "DATA", data):
                    payload = server.load_intelligence_meta()
            finally:
                server._INTEL_RAW.clear()
                server._INTEL_RAW.update(original_raw)

        self.assertEqual([row["id"] for row in payload["records"]], ["safe"])
        self.assertNotIn("Reference", payload["records"][0]["connectors"][0]["raw"])
        self.assertEqual(payload["total_records"], 1)
        self.assertEqual(payload["source_policy_excluded_records"], 2)

    def test_manual_seed_loader_applies_the_same_source_policy(self) -> None:
        original_raw = deepcopy(server._INTEL_RAW)
        original_enriched = deepcopy(server._INTEL_ENRICHED)
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "free_intelligence.json").write_text(json.dumps({
                "generated_at": "2026-07-13T00:00:00Z",
                "records": [],
            }), encoding="utf-8")
            (data / "seed_leads.json").write_text(json.dumps([
                {"id": "safe", "source": "manual research lead"},
                {"id": "blocked", "source": "Electromaps web-map API"},
            ]), encoding="utf-8")
            try:
                server._INTEL_RAW.clear()
                server._INTEL_RAW.update({"mtime": None, "data": None})
                server._INTEL_ENRICHED.clear()
                server._INTEL_ENRICHED.update({
                    "key": None,
                    "intel": None,
                    "intel_coords": None,
                    "seeds": None,
                    "seeds_coords": None,
                })
                with (
                    patch.object(server, "DATA", data),
                    patch.object(server, "_normalize_lead", side_effect=lambda lead, _source: lead),
                    patch.object(server, "reject_station_reason", return_value=None),
                ):
                    seeds = server.load_seed_leads()
            finally:
                server._INTEL_RAW.clear()
                server._INTEL_RAW.update(original_raw)
                server._INTEL_ENRICHED.clear()
                server._INTEL_ENRICHED.update(original_enriched)

        self.assertEqual([row["id"] for row in seeds], ["safe"])

    def test_live_response_never_serves_excluded_primary_or_nested_evidence(self) -> None:
        safe = self._live_station("safe-live", "MapaREVE")
        safe["name"] = "Fenie-operated station from MapaREVE"
        safe["owner"] = {"name": "FENÍE ENERGÍA"}
        primary = self._live_station("primary", "Electromaps web-map API")
        nested = self._live_station("nested", "Gaia", nested_source="Electromaps directory")
        results = {
            "mapareve": {
                "name": "mapareve",
                "payload": {"stations": [safe, primary], "raw_count": 2, "errors": []},
                "error": None,
                "duration_ms": 1.0,
            },
            "gaia": {
                "name": "gaia",
                "payload": {
                    "stations": [nested],
                    "raw_count": 1,
                    "errors": [],
                    "cache": {"hit": False, "stale": False, "populated_at": 1.0},
                },
                "error": None,
                "duration_ms": 1.0,
            },
            # Feníe is ingestion-only: there is no runtime Feníe fetch key, so
            # live_response must ignore this legacy fixture key entirely.
            "fenie": {
                "name": "fenie",
                "payload": {"stations": [], "raw_count": 0, "errors": [], "cache": {}},
                "error": None,
                "duration_ms": 0.0,
            },
        }
        original_cache = deepcopy(server.CACHE)
        try:
            server.CACHE.clear()
            server.CACHE.update({"ts": 0, "payload": None, "raw_count": 0, "error": None, "assembled": None})
            with patch("server.fetch_primary_sources_parallel", return_value=results):
                status, payload = server.live_response({
                    "bbox": ["39.30,-0.55,39.62,-0.20"],
                    "max_price": ["0.25"],
                    "include_leads": ["false"],
                    "force": ["true"],
                })
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)

        self.assertEqual(status, 200)
        self.assertEqual([station["id"] for station in payload["stations"]], ["safe-live"])
        self.assertEqual(payload["meta"]["engine"]["counts"]["dropped"]["source_policy_excluded"], 2)
        self.assertIn("source policy", payload["warning"].lower())
        provider_strategy = payload["meta"]["named_provider_strategy"]
        self.assertIn("link only", provider_strategy["Electromaps"].lower())
        self.assertNotIn("Chargemap", provider_strategy["Iberdrola/Acciona/GIC/Miio"])
        self.assertNotIn("Electromaps", provider_strategy["Iberdrola/Acciona/GIC/Miio"])

    def test_stale_raw_cache_is_repartitioned_before_serving(self) -> None:
        safe = self._live_station("safe-stale", "MapaREVE")
        blocked = self._live_station("blocked-stale", "Electromaps web-map API")
        mapareve_payload = {
            "stations": [safe],
            "raw_count": 1,
            "fetched_at": server.now_iso(),
            "errors": [],
        }
        gaia_payload = {
            "stations": [],
            "raw_count": 0,
            "errors": [],
            "cache": {
                "hit": True,
                "stale": False,
                "populated_at": time.time(),
            },
        }
        original_cache = deepcopy(server.CACHE)
        try:
            server.CACHE.clear()
            server.CACHE.update({"ts": 0, "payload": None, "raw_count": 0, "error": None, "assembled": None})
            with (
                patch("server.fetch_mapareve", side_effect=[deepcopy(mapareve_payload), RuntimeError("offline")]),
                patch("server.fetch_gaia_live", return_value=deepcopy(gaia_payload)),
                patch("server.load_all_leads", return_value=[]),
            ):
                params = {
                    "bbox": ["39.30,-0.55,39.62,-0.20"],
                    "max_price": ["0.25"],
                    "include_leads": ["false"],
                }
                first_status, _ = server.live_response(params)
                self.assertEqual(first_status, 200)
                server.CACHE["payload"]["stations"].append(blocked)
                server.CACHE["ts"] = 0

                status, payload = server.live_response(params)
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)

        self.assertEqual(status, 200)
        self.assertEqual([station["id"] for station in payload["stations"]], ["safe-stale"])
        self.assertTrue(payload["cache"]["stale"])
        self.assertEqual(payload["meta"]["engine"]["counts"]["dropped"]["source_policy_excluded"], 1)
        self.assertIn("source policy", payload["warning"].lower())

    def test_assembled_cache_is_repartitioned_and_keeps_independent_operator_mentions(self) -> None:
        safe = self._live_station("safe-assembled", "MapaREVE")
        safe["name"] = "Fenie-operated station independently observed by MapaREVE"
        safe["owner"] = {"name": "FENÍE ENERGÍA"}
        blocked_station = self._live_station("blocked-assembled", "Electromaps web-map API")
        safe_lead = {
            "id": "safe-lead",
            "name": "Independent directory lead",
            "lat": 39.50,
            "lon": -0.30,
            "source": "OpenChargeMap",
            "source_url": "https://openchargemap.io/site/poi/details/1",
            "sources": [{"name": "OpenChargeMap", "url": "https://openchargemap.io/site/poi/details/1"}],
            "reliability": "directory",
            "status": "VERIFY",
        }
        blocked_lead = deepcopy(safe_lead)
        blocked_lead.update({
            "id": "blocked-lead",
            "source": "OpenChargeMap",
            "sources": [
                {"name": "OpenChargeMap", "url": "https://openchargemap.io/site/poi/details/2"},
                {"name": "Electromaps directory", "url": "https://example.invalid/secondary"},
            ],
        })
        mapareve_payload = {
            "stations": [safe],
            "raw_count": 1,
            "fetched_at": server.now_iso(),
            "errors": [],
        }
        gaia_payload = {
            "stations": [],
            "raw_count": 0,
            "errors": [],
            "cache": {
                "hit": True,
                "stale": False,
                "populated_at": time.time(),
            },
        }
        original_cache = deepcopy(server.CACHE)
        try:
            server.CACHE.clear()
            server.CACHE.update({"ts": 0, "payload": None, "raw_count": 0, "error": None, "assembled": None})
            with (
                patch("server.fetch_mapareve", return_value=deepcopy(mapareve_payload)),
                patch("server.fetch_gaia_live", return_value=deepcopy(gaia_payload)),
                patch("server.fetch_electromaps_live"),
                patch("server.load_all_leads", return_value=[deepcopy(safe_lead)]),
            ):
                params = {
                    "bbox": ["39.30,-0.55,39.62,-0.20"],
                    "max_price": ["0.25"],
                    "include_leads": ["true"],
                }
                first_status, _ = server.live_response(params)
                self.assertEqual(first_status, 200)
                self.assertIsNotNone(server.CACHE["assembled"])
                server.CACHE["assembled"]["stations"].append(blocked_station)
                server.CACHE["assembled"]["leads"].append(blocked_lead)

                with patch("server.enrich_station", wraps=server.enrich_station) as enrich:
                    status, payload = server.live_response(params)
                    self.assertEqual(enrich.call_count, 0, "the regression must exercise assembled-cache reuse")
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)

        self.assertEqual(status, 200)
        self.assertEqual([station["id"] for station in payload["stations"]], ["safe-assembled"])
        self.assertEqual([lead["id"] for lead in payload["leads"]], ["safe-lead"])
        self.assertEqual(payload["meta"]["engine"]["counts"]["dropped"]["source_policy_excluded"], 2)
        self.assertIn("source policy", payload["warning"].lower())


if __name__ == "__main__":
    unittest.main()
