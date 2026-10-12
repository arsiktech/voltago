#!/usr/bin/env python3
"""Offline regressions for tariff claims through the real two-stage build."""
from __future__ import annotations

import copy
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ingest_sources as ing  # noqa: E402

LAT, LON = 28.41543, -16.54291
CANARIAS = ("Canarias te recarga public map", "https://canariasterecarga.com/es/test-site")
REVE = ("MapaREVE public OCPI snapshot", "https://www.mapareve.es/mapa-puntos-recarga")
REGISTRY = ("DGT National Access Point charging registry", ing.DGT_NAP_CATALOG_URL)
OCM = ("OpenChargeMap public snapshot", "https://map.openchargemap.io/?mode=details&id=123")


def record(source, price, *, lat=LAT, reliability="directory-page", condition=None, **kwargs):
    rows = []
    ing.add_record(
        rows,
        source=source[0],
        source_url=source[1],
        name="C.C. Mencey Bencomo",
        address="Calle Valois 1",
        city="Puerto de la Cruz",
        lat=lat,
        lon=LON,
        price_min=price,
        free_condition=(f"Published tariff: {price} EUR/kWh" if price is not None else "Price unpublished")
        if condition is None else condition,
        reliability=reliability,
        **kwargs,
    )
    return rows[0]


class FullPipelineTariffConflictTests(unittest.TestCase):
    def build(self, rows):
        def fixture_source(records):
            records.extend(copy.deepcopy(rows))

        # Exercise build_payload itself, not just the second merge stage. No collector
        # runs, and an accidental transport call fails rather than accessing the network.
        with mock.patch.object(ing, "ACTIVE_SOURCE_ADAPTERS", (fixture_source,)), \
                mock.patch.object(ing, "policy_urlopen", side_effect=AssertionError("No network in fixture build")), \
                redirect_stdout(io.StringIO()):
            payload = ing.build_payload()
        self.assertEqual(payload["errors"], [])
        self.assertEqual(payload["raw_records"], len(rows))
        self.assertEqual(payload["total_records"], 1)
        return payload["records"][0]

    def assert_tariff(self, row, adopted, contributors):
        self.assertEqual(row["price_min"], adopted["price_min"])
        self.assertEqual(row["charge_claim"], adopted["charge_claim"])
        self.assertEqual(row["free_condition"], adopted["free_condition"])
        self.assertEqual(row.get("pricing"), adopted.get("pricing"))
        self.assertFalse(row["is_free"])
        self.assertFalse(row["is_cheap"])
        self.assertEqual(row["price_claim_type"], "confirmed_paid")
        conflict = row["claim_conflict"]
        self.assertEqual(conflict["level"], "mixed_sources")
        self.assertEqual(conflict["charge_url"], adopted["charge_claim"]["url"])
        expected = sorted([
            {key: item["charge_claim"][key] for key in ("source", "url", "price_min", "summary")}
            for item in contributors
        ], key=lambda claim: (claim["price_min"], claim["url"]))
        self.assertEqual(conflict["charge_prices"], expected)

    def test_exact_coordinate_zero_or_cheap_never_hides_paid_in_either_order(self):
        for low_price in (0.0, 0.25):
            for reverse in (False, True):
                with self.subTest(low_price=low_price, reverse=reverse):
                    low = record(CANARIAS, low_price, reliability="official-free")
                    high = record(REVE, 0.38)
                    low["pricing"] = [{"price": low_price, "label": "Lower tariff"}]
                    high["pricing"] = [{"price": 0.38, "label": "Paid tariff"}]
                    rows = [low, high]
                    row = self.build(rows[::-1] if reverse else rows)
                    self.assert_tariff(row, high, rows)
                    self.assertEqual(row["id"], low["id"])

    def test_nearby_conflict_replaces_or_clears_lower_tariff_metadata(self):
        for has_pricing in (False, True):
            for reverse in (False, True):
                with self.subTest(has_pricing=has_pricing, reverse=reverse):
                    low = record(CANARIAS, 0.0, reliability="official-free", condition="Free charging")
                    high = record(REVE, 0.38, lat=LAT + 0.0002, condition="")
                    low["pricing"] = [{"price": 0.0, "label": "Free charging"}]
                    if has_pricing:
                        high["pricing"] = [{"price": 0.38, "label": "Paid tariff"}]
                    rows = [low, high]
                    row = self.build(rows[::-1] if reverse else rows)
                    self.assert_tariff(row, high, rows)

    def test_two_stage_build_retains_all_three_structured_prices(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                low = record(CANARIAS, 0.25)
                high = record(REVE, 0.38)
                third = record(REGISTRY, 0.30, lat=LAT + 0.0002, reliability="official-cheap")
                rows = [low, high, third]
                row = self.build(rows[::-1] if reverse else rows)
                self.assert_tariff(row, high, rows)

    def test_existing_conflict_survives_repeated_dedupe_and_another_merge(self):
        low = record(CANARIAS, 0.0)
        high = record(REVE, 0.38)
        # Start with the already-working direct late merge so the regression also
        # catches loss of an existing conflict independently of first-stage selection.
        merged, count = ing.merge_cross_source_duplicates([low, high])
        self.assertEqual(count, 1)
        self.assertEqual(len(merged[0]["claim_conflict"]["charge_prices"]), 2)
        for next_price in (None, 0.38, 0.45):
            for reverse in (False, True):
                with self.subTest(next_price=next_price, reverse=reverse):
                    third = record(REGISTRY, next_price, lat=LAT + 0.0002,
                                   reliability="official-charger-location-price-unknown")
                    rows = [merged[0], third]
                    row = self.build(rows[::-1] if reverse else rows)
                    # Equal highest tariffs retain the higher-reliability primary.
                    adopted = third if next_price in (0.38, 0.45) else high
                    contributors = [low, high] + ([third] if next_price is not None else [])
                    self.assert_tariff(row, adopted, contributors)
                    again = self.build([row])
                    self.assert_tariff(again, adopted, contributors)

    def test_second_merge_preserves_existing_conflict_without_dedupe(self):
        low = record(CANARIAS, 0.0)
        high = record(REVE, 0.38)
        merged, _ = ing.merge_cross_source_duplicates([low, high])
        third = record(REGISTRY, None, lat=LAT + 0.0002,
                       reliability="official-charger-location-price-unknown")
        rows, count = ing.merge_cross_source_duplicates([third, merged[0]])
        self.assertEqual(count, 1)
        self.assert_tariff(rows[0], high, [low, high])

    def test_unstructured_tariff_prose_never_becomes_a_price_claim(self):
        for price in (None, 0.25):
            with self.subTest(price=price):
                primary = record(CANARIAS, price)
                unknown = record(REGISTRY, None, reliability="official-charger-location-price-unknown",
                                 evidence="Old prose mentions free charging and 0.38 EUR/kWh; not a structured tariff.")
                row = self.build([primary, unknown])
                self.assertEqual(row["price_min"], price)
                self.assertFalse(row["is_free"])
                self.assertEqual(row["is_cheap"], price is not None)
                self.assertEqual(row["price_claim_type"], "price_unknown_lead" if price is None else "confirmed_cheap")
                self.assertNotIn("claim_conflict", row)

    def test_official_unknown_lead_does_not_adopt_stale_or_undated_ocm(self):
        for offset in (0.0, 0.0002):
            for raw in ({}, {"DateLastVerified": "2018-07-13T05:28:00Z"}):
                with self.subTest(offset=offset, raw=raw):
                    official = record(REGISTRY, None, reliability="official-charger-location-price-unknown")
                    stale = record(OCM, 0.0, lat=LAT + offset, raw=raw)
                    row = self.build([stale, official])
                    self.assertIsNone(row["price_min"])
                    self.assertEqual(row["charge_claim"]["type"], "price_unknown")
                    self.assertFalse(row["is_free"])
                    self.assertFalse(row["is_cheap"])
                    self.assertEqual(row["price_claim_type"], "price_unknown_lead")

    def test_conflict_resolution_does_not_bypass_official_stale_price_guard(self):
        for offset in (0.0, 0.0002):
            with self.subTest(offset=offset):
                official = record(REGISTRY, None, reliability="official-charger-location-price-unknown")
                current = record(REVE, 0.25, lat=LAT + offset)
                stale = record(OCM, 0.38, lat=LAT + offset, raw={"DateLastVerified": "2018-07-13T05:28:00Z"})
                row = self.build([official, current, stale])
                self.assertEqual(row["price_min"], 0.25)
                self.assertEqual(row["charge_claim"], current["charge_claim"])
                self.assertFalse(row["is_free"])

    def test_current_ocm_price_can_still_enrich_official_unknown_lead(self):
        for offset in (0.0, 0.0002):
            with self.subTest(offset=offset):
                official = record(REGISTRY, None, reliability="official-charger-location-price-unknown")
                current = record(OCM, 0.25, lat=LAT + offset, raw={"DateLastVerified": ing.now_iso()})
                row = self.build([official, current])
                self.assertEqual(row["price_min"], 0.25)
                self.assertEqual(row["charge_claim"], current["charge_claim"])
                self.assertEqual(row["free_condition"], current["free_condition"])
                self.assertEqual(row["id"], official["id"])

    def test_parking_only_conflict_survives_repeated_build(self):
        charge = record(CANARIAS, 0.0, reliability="official-free")
        parking = record(REVE, None, parking_kind="paid_or_metered", parking_note="Parking costs extra")
        for rows in ([charge, parking], [parking, charge]):
            row = self.build(rows)
            again = self.build([row])
            for result in (row, again):
                self.assertEqual(result["price_min"], 0.0)
                self.assertEqual(result["parking_claim"], parking["parking_claim"])
                self.assertEqual(result["claim_conflict"]["charge_url"], charge["source_url"])
                self.assertEqual(result["claim_conflict"]["parking_url"], parking["source_url"])
                self.assertNotIn("charge_prices", result["claim_conflict"])

    def test_tariff_conflict_does_not_replace_location_hardware_or_parking_claims(self):
        official = record(REGISTRY, None, reliability="official-charger-location-price-unknown",
                          raw={"preserve_location_claim": True, "official_hardware_inventory": True},
                          connectors=[ing.connector_ref("CCS2", count=2, power_kw=50, source_url=REGISTRY[1])],
                          parking_kind="paid_or_metered", parking_note="Parking costs extra")
        low = record(CANARIAS, 0.0)
        high = record(REVE, 0.38, connectors=[ing.connector_ref("Type 2", count=4, power_kw=22, source_url=REVE[1])])
        row = self.build([low, high, official])
        self.assert_tariff(row, high, [low, high])
        self.assertEqual(row["location_claim"], official["location_claim"])
        self.assertEqual(row["connectors"], official["connectors"])
        self.assertEqual(row["connector_count"], 2)
        self.assertEqual(row["parking_claim"], official["parking_claim"])

    def test_restricted_zero_price_stays_access_limited_after_merges(self):
        public = record(CANARIAS, 0.0, reliability="official-free")
        restricted = record(REVE, 0.0, evidence="Free for members only")
        row = self.build([public, restricted])
        for result in (row, self.build([row])):
            self.assertEqual(result["price_min"], 0.0)
            self.assertFalse(result["is_free"])
            self.assertEqual(result["price_claim_type"], "access_limited_free")
            self.assertEqual(result["access"]["level"], "restricted")


if __name__ == "__main__":
    unittest.main()
