#!/usr/bin/env python3
"""Offline VG-07 report contracts. Fixtures are not regional acceptance evidence."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import qa_pilot_coverage as coverage

AS_OF = "2026-10-28T12:00:00Z"
RECENT = "2026-10-28T11:59:00Z"
OLD = "2026-10-20T12:00:00Z"
REVISION = "a" * 40


def station(sid="vlc-1", *, tenerife=False):
    return {
        "id": sid, "name": "Fixture charger", "lat": 28.46 if tenerife else 39.47,
        "lon": -16.25 if tenerife else -0.37, "source": "MapaREVE public live OCPI",
        "source_url": "https://www.mapareve.es/", "source_fetched_at": RECENT,
        "last_seen": RECENT, "cache_stale": False, "lead": False,
        "verification": {"level": "live", "live_verified": True, "price_verified": True},
        "price_min": 0.0, "current_price_min": 0.0, "currency": "EUR",
        "status": "AVAILABLE", "available": 1,
        "connectors": [{"label": "CCS2", "status": "AVAILABLE"}],
        "connector_count": 1, "total_evse": 1,
    }


class PilotCoverageTests(unittest.TestCase):
    def report(self, rows, *, mode="served-capture", **kwargs):
        return coverage.build_report(
            {"stations": rows, "leads": []}, mode=mode, as_of=AS_OF,
            observed_at=RECENT if mode == "served-capture" else None,
            source_revision=REVISION, **kwargs,
        )

    def counts(self, row, **kwargs):
        return self.report([row], **kwargs)["regions"]["valencia"]["counts"]

    def test_regions_have_separate_unique_location_denominators(self):
        row = station()
        report = self.report([row, copy.deepcopy(row), station("tf-1", tenerife=True)])
        for region in ("tenerife", "valencia"):
            # Distinct canonical location IDs, not input copies or connector count.
            self.assertEqual(report["regions"][region]["denominator"]["unique_locations"], 1)
        self.assertEqual(report["input"]["duplicate_identical_rows"], 1)
        self.assertFalse(report["runtime_acceptance"])

    def test_coincident_coordinates_do_not_prove_physical_identity(self):
        report = self.report([station("a"), station("b")])
        denominator = report["regions"]["valencia"]["denominator"]
        self.assertEqual(denominator["unique_locations"], 2)
        self.assertIsNone(denominator["unique_physical_locations"])
        self.assertIn("canonical", denominator["basis"])

    def test_reviewed_location_map_can_establish_a_physical_denominator(self):
        result = self.report([station("a"), station("b")], location_map={
            "basis": "Reviewed site identities, fixture only",
            "location_ids": {"a": "physical-1", "b": "physical-1"},
        })
        region = result["regions"]["valencia"]
        self.assertEqual(region["denominator"]["unique_physical_locations"], 1)
        self.assertEqual(region["denominator"]["unique_locations"], 1)
        self.assertEqual(region["counts"]["fresh_live_locations"], 1)
        self.assertEqual(region["hardware"]["ambiguous_multirow_locations"], 1)
        self.assertIsNone(region["hardware"]["connectors_total"])

    def test_partial_location_map_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "location map"):
            self.report([station("a"), station("b")], location_map={
                "basis": "Partial review", "location_ids": {"a": "physical-1"},
            })

    def test_numeric_zero_requires_affirmative_runtime_price_authority(self):
        for verification in ({}, {"price_verified": None}, {"price_verified": False}):
            with self.subTest(verification=verification):
                row = station()
                row["verification"] = verification
                counts = self.counts(row)
                self.assertEqual(counts["sourced_numeric_price_locations"], 1)
                self.assertEqual(counts["verified_current_price_locations"], 0)
        self.assertEqual(self.counts(station())["verified_current_price_locations"], 1)

    def test_absent_price_never_defaults_to_zero(self):
        row = station()
        row.update(price_min=None, current_price_min=None, is_free=True)
        counts = self.counts(row)
        self.assertEqual(counts["sourced_numeric_price_locations"], 0)
        self.assertEqual(counts["verified_current_price_locations"], 0)
        self.assertEqual(counts["unknown_primary_price_locations"], 1)

    def test_price_annotations_do_not_become_primary_or_current_prices(self):
        row = station()
        row.update(price_min=None, current_price_min=None)
        row["price_evidence"] = [{
            "kind": "electromaps_community", "source": "Electromaps",
            "source_url": "https://www.electromaps.com/en/p/fixture",
            "text": "0 €/kWh", "price_per_kwh": 0,
            "rates": [{"min": 0, "max": None, "unit": "kWh", "kind": "energy"}],
            "source_updated_at": OLD, "fetched_at": RECENT,
        }]
        region = self.report([row])["regions"]["valencia"]
        self.assertEqual(region["counts"]["price_annotation_locations"], 1)
        self.assertEqual(region["counts"]["unknown_primary_price_locations"], 1)
        self.assertEqual(region["counts"]["verified_current_price_locations"], 0)
        annotation = region["locations"][0]["rows"][0]["price_evidence"][0]
        self.assertEqual(annotation["source_updated_at"], OLD)
        self.assertEqual(annotation["fetched_at"], RECENT)

    def test_conditional_tariff_remains_separate(self):
        row = station()
        row.update(price_min=None, current_price_min=None,
                   tariff_details={"hour_eur": 1.5, "conditions": ["First hour only"]})
        counts = self.counts(row)
        self.assertEqual(counts["conditional_sourced_price_locations"], 1)
        self.assertEqual(counts["sourced_numeric_price_locations"], 0)
        self.assertEqual(counts["verified_current_price_locations"], 0)

    def test_contact_only_source_text_is_not_a_sourced_tariff(self):
        row = station()
        row.update(price_min=None, current_price_min=None,
                   tariff_details={"text": "Call the operator for tariff information"})
        counts = self.counts(row)
        self.assertEqual(counts["conditional_sourced_price_locations"], 0)
        self.assertEqual(counts["source_text_only_locations"], 1)
        self.assertEqual(counts["unknown_primary_price_locations"], 1)

    def test_stale_future_missing_and_conflicting_evidence_fail_closed(self):
        variants = [
            {"last_seen": OLD}, {"last_seen": None},
            {"last_seen": "2026-10-28T12:01:00Z"}, {"cache_stale": True},
            {"claim_conflict": {"level": "conflict"}},
            {"live_status": {"valid": True, "checked_at": RECENT, "conflict": True}},
            {"verification": {"live_verified": None, "price_verified": None}},
        ]
        for patch in variants:
            with self.subTest(patch=patch):
                row = station()
                row.update(patch)
                counts = self.counts(row)
                self.assertEqual(counts["fresh_live_locations"], 0)
                self.assertEqual(counts["verified_current_price_locations"], 0)

    def test_fetch_time_etecnic_and_zunder_socket_counts_are_not_live(self):
        for adapter in ("etecnic", "zunder"):
            row = station()
            row.update(source_adapter=adapter, source=f"{adapter} public map",
                       source_integration={"mode": "public_map_snapshot", "live_status_integrated": False})
            row["raw"] = {"available": 4, "socket_status": "available"}
            counts = self.counts(row)
            self.assertEqual(counts["fresh_live_locations"], 0)
            self.assertEqual(counts["fetch_time_snapshot_locations"], 1)
            self.assertEqual(counts["verified_current_price_locations"], 0)

    def test_expired_and_unknown_expiry_withhold_live_and_current_price(self):
        for expiry in (OLD, AS_OF, None, "unknown"):
            with self.subTest(expiry=expiry):
                row = station()
                row["expires_at"] = expiry
                counts = self.counts(row)
                self.assertEqual(counts["fresh_live_locations"], 0)
                self.assertEqual(counts["verified_current_price_locations"], 0)

    def test_new_live_status_does_not_refresh_old_price_claim(self):
        row = station()
        row["charge_claim"] = {"checked_at": OLD, "price_min": 0.0}
        counts = self.counts(row)
        self.assertEqual(counts["fresh_live_locations"], 1)
        self.assertEqual(counts["verified_current_price_locations"], 0)

    def test_source_fetch_without_status_observation_is_not_live(self):
        row = station()
        row["last_seen"] = None
        counts = self.counts(row)
        self.assertEqual(counts["fresh_live_locations"], 0)
        self.assertEqual(counts["verified_current_price_locations"], 0)

    def test_bundle_never_grants_runtime_acceptance_or_current_live(self):
        result = self.report([station()], mode="admitted-bundle")
        self.assertFalse(result["runtime_acceptance"])
        counts = result["regions"]["valencia"]["counts"]
        self.assertEqual(counts["fresh_live_locations"], 0)
        self.assertEqual(counts["verified_current_price_locations"], 0)
        self.assertIsNone(result["identity"]["observed_response_sha256"])

    def test_public_status_validation_is_not_the_last_state_change(self):
        row = station()
        row["last_seen"] = OLD
        row["live_status"] = {"valid": True, "checked_at": RECENT, "published_at": None, "feed": "endolla"}
        row["live_claim_verification"] = {"price_verified": False}
        counts = self.counts(row)
        self.assertEqual(counts["fresh_live_locations"], 1)
        self.assertEqual(counts["verified_current_price_locations"], 0)
        clocks = self.report([row])["regions"]["valencia"]["locations"][0]["rows"][0]["clocks"]
        self.assertEqual(clocks["last_seen"], OLD)
        self.assertEqual(clocks["status_validated_at"], RECENT)

    def test_conflicting_duplicates_are_rejected_instead_of_last_write_wins(self):
        a, b = station(), station()
        b["price_min"] = 0.5
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.report([a, b])

    def test_truncated_matrix_never_becomes_census(self):
        payload = {"stations": [station()], "leads": [], "truncated": False,
                   "count": 1, "total_matches": 150}
        report = coverage.build_report(payload, mode="served-capture", as_of=AS_OF,
                                       observed_at=RECENT, source_revision=REVISION)
        self.assertFalse(report["input"]["complete_inventory"])
        self.assertTrue(any("total_matches" in issue for issue in report["issues"]))
        self.assertFalse(report["runtime_acceptance"])

    def test_response_denial_of_live_authority_is_not_overridden_by_rows(self):
        report = coverage.build_report(
            {"stations": [station()], "leads": [],
             "cache": {"live_supported": False, "fresh_live_authority": False}},
            mode="served-capture", as_of=AS_OF, observed_at=RECENT, source_revision=REVISION,
        )
        counts = report["regions"]["valencia"]["counts"]
        self.assertEqual(counts["fresh_live_locations"], 0)
        self.assertEqual(counts["verified_current_price_locations"], 0)

    def test_bad_counts_and_ambiguous_envelopes_are_rejected(self):
        for payload in ({"records": [], "stations": []}, {"stations": [station()], "count": 2}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                coverage.build_report(payload, mode="served-capture", as_of=AS_OF,
                                      observed_at=RECENT, source_revision=REVISION)

    def test_hardware_keeps_evse_connector_and_source_listing_units_separate(self):
        row = station()
        row.update(total_evse=2, connector_count=4,
                   connectors=[{"label": "Type 2", "count": 4}])
        row["sources"] = [{"name": row["source"], "url": row["source_url"]},
                          {"name": "Independent directory", "url": "https://example.invalid/station"}]
        region = self.report([row])["regions"]["valencia"]
        self.assertEqual(region["hardware"]["connectors_total"], 4)
        # Runtime overloads total_evse with connector totals; it is not a proven EVSE denominator.
        self.assertIsNone(region["hardware"]["evse_total"])
        self.assertEqual(region["hardware"]["reported_total_evse_sum"], 2)
        self.assertEqual(region["sources"]["source_listing_references"], 2)
        self.assertEqual(region["denominator"]["unique_locations"], 1)

    def test_source_exclusions_are_counted_not_silently_admitted(self):
        row = station()
        row.update(source="Consum store locator", source_url="https://www.consum.es/stores/fixture")
        region = self.report([row])["regions"]["valencia"]
        self.assertEqual(region["counts"]["policy_excluded_locations"], 1)
        self.assertEqual(region["counts"]["fresh_live_locations"], 0)
        self.assertEqual(region["counts"]["verified_current_price_locations"], 0)

    def test_explicit_timezone_clock_and_finite_bbox_required(self):
        for clock in ("2026-10-28", "2026-10-28T12:00:00", "invalid"):
            with self.subTest(clock=clock), self.assertRaises(ValueError):
                coverage.build_report([], mode="admitted-bundle", as_of=clock, source_revision=REVISION)
        with self.assertRaises(ValueError):
            self.report([station()], regions={"valencia": {"bbox": [40, -1, 39, 0]}})

    def test_cli_hashes_exact_response_bytes_separately_from_bundle_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            response = directory / "response.json"
            bundle = directory / "bundle.json"
            output = directory / "report.json"
            response.write_text(json.dumps({"stations": [station()], "leads": []}), encoding="utf-8")
            bundle.write_text(json.dumps({"records": [station()]}), encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(ROOT / "scripts/qa_pilot_coverage.py"),
                "--data", str(response), "--bundle", str(bundle), "--mode", "served-capture",
                "--source-revision", REVISION, "--as-of", AS_OF, "--observed-at", RECENT,
                "--out", str(output),
            ], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["identity"]["data_sha256"], hashlib.sha256(bundle.read_bytes()).hexdigest())
            self.assertEqual(report["identity"]["observed_response_sha256"], hashlib.sha256(response.read_bytes()).hexdigest())
            self.assertNotEqual(report["identity"]["data_sha256"], report["identity"]["observed_response_sha256"])
            self.assertFalse(report["runtime_acceptance"])


if __name__ == "__main__":
    unittest.main()
