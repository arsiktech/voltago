#!/usr/bin/env python3
"""Every located charger reaches the map; free first and cheap next is ranking, not inclusion.

Before 2026-10-07 the build dropped any charger above €0.25/kWh and every official
registry site without a matched free/cheap price, so Puerto de la Cruz showed 5 of its
chargers. These tests pin the replacement behaviour: paid and unpriced chargers are kept
with honest labels, rank after free/cheap options, and still merge into one pin when
two sources list the same point.
"""
from __future__ import annotations

import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import ingest_sources as ing  # noqa: E402
import server  # noqa: E402

LAT, LON = 28.41543, -16.54291  # Puerto de la Cruz


def registry_row(records, *, lat=LAT, lon=LON, name="ENDESA X WAY, S.L. — 62229030490006", address="Calle Valois 1"):
    ing.add_record(
        records,
        source="DGT National Access Point charging registry",
        source_url=ing.DGT_NAP_CATALOG_URL,
        name=name,
        address=address,
        city="Puerto de la Cruz",
        lat=lat,
        lon=lon,
        price_min=None,
        connector_labels=["CCS2"],
        reliability="official-charger-location-price-unknown",
        raw={"nap_site_id": "62229030490006", "corroboration_only": True},
    )
    return records


def directory_row(records, *, lat=LAT, lon=LON, name="Plaza del Charco", price=0.45):
    ing.add_record(
        records,
        source="Canarias te recarga public map",
        source_url="https://canariasterecarga.com/es/plaza-del-charco/",
        name=name,
        address="Plaza del Charco",
        city="Puerto de la Cruz",
        lat=lat,
        lon=lon,
        price_min=price,
        connector_labels=["Type 2"],
        reliability="directory-page",
    )
    return records


class InclusionTests(unittest.TestCase):
    def test_unmatched_registry_site_survives_the_build(self) -> None:
        def registry_source(records) -> None:
            registry_row(records)

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(ing, "ACTIVE_SOURCE_ADAPTERS", (registry_source,)))
            stack.enter_context(mock.patch.object(ing, "enrich_price_evidence", return_value={}))
            payload = ing.build_payload()

        self.assertEqual(len(payload["records"]), 1)
        row = payload["records"][0]
        self.assertIsNone(row["price_min"])
        self.assertEqual(row["price_claim_type"], "price_unknown_lead")
        self.assertFalse(row["is_free"])


class DedupTests(unittest.TestCase):
    def test_registry_site_on_the_same_point_merges_into_one_pin(self) -> None:
        records = registry_row([])
        directory_row(records, lat=LAT + 0.00005)  # ~6 m
        merged, count = ing.merge_cross_source_duplicates(records)
        self.assertEqual(count, 1)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["price_min"], 0.45)
        self.assertEqual(merged[0]["price_claim_type"], "confirmed_paid")
        self.assertEqual(len(merged[0]["sources"]), 2)

    def test_registry_site_40m_away_without_address_identity_stays_separate(self) -> None:
        records = registry_row([])
        directory_row(records, lat=LAT + 0.00036)  # ~40 m
        merged, count = ing.merge_cross_source_duplicates(records)
        self.assertEqual(count, 0)
        self.assertEqual(len(merged), 2)

    def test_registry_site_does_not_merge_into_a_different_retail_brand(self) -> None:
        records = registry_row([], name="Mercadona — 1", address="Mercadona Puerto de la Cruz")
        directory_row(records, lat=LAT + 0.00005, name="Lidl Puerto de la Cruz", price=0.0)
        merged, count = ing.merge_cross_source_duplicates(records)
        self.assertEqual(count, 0)
        self.assertEqual(len(merged), 2)


class PriceConflictMergeTests(unittest.TestCase):
    def reve_row(self, records, *, lat, name, price):
        ing.add_record(
            records,
            source="MapaREVE public OCPI snapshot",
            source_url="https://www.mapareve.es/mapa-puntos-recarga",
            name=name,
            address="",
            city="Los Realejos",
            lat=lat,
            lon=LON,
            price_min=price,
            connector_labels=["Type 2"],
            reliability="directory-page",
        )
        return records

    def test_same_site_with_two_paid_prices_is_one_pin_showing_the_higher_price(self) -> None:
        records = directory_row([], name="Los Realejos - Calle Taoro", price=0.38)
        self.reve_row(records, lat=LAT + 0.0002, name="Los Realejos - Calle Taoro", price=0.35)  # ~22 m
        merged, count = ing.merge_cross_source_duplicates(records)
        self.assertEqual(count, 1)
        self.assertEqual(merged[0]["price_min"], 0.38)
        self.assertEqual(merged[0]["claim_conflict"]["level"], "mixed_sources")

    def test_free_claim_never_hides_a_paid_price_for_the_same_site(self) -> None:
        records = directory_row([], name="C.C. Mencey Bencomo", price=0.0)
        self.reve_row(records, lat=LAT + 0.0002, name="Centro Comercial Mencey Bencomo", price=0.38)
        merged, count = ing.merge_cross_source_duplicates(records)
        self.assertEqual(count, 1)
        self.assertEqual(merged[0]["price_min"], 0.38)
        self.assertFalse(merged[0]["is_free"])
        self.assertEqual(merged[0]["price_claim_type"], "confirmed_paid")
        prices = sorted(c["price_min"] for c in merged[0]["claim_conflict"]["charge_prices"])
        self.assertEqual(prices, [0.0, 0.38])

    def test_different_named_sites_with_different_prices_stay_separate(self) -> None:
        records = directory_row([], name="Calle Puerto Franco", price=0.38)
        self.reve_row(records, lat=LAT + 0.0002, name="Calle José Garcés", price=0.35)
        merged, count = ing.merge_cross_source_duplicates(records)
        self.assertEqual(count, 0)


class DecisionAndRankingTests(unittest.TestCase):
    def enrich(self, **over):
        item = {
            "id": over.pop("id", "x"),
            "name": "Charger",
            "lat": LAT,
            "lon": LON,
            "lead": True,
            "connector_labels": ["Type 2"],
            "tesla_ok": True,
            "reliability": "directory-page",
            "status": "VERIFY",
            "access": {"level": "public", "label": "Public", "note": "Public."},
        }
        item.update(over)
        return server.enrich_station(item, live=False)

    def test_paid_charger_is_a_neutral_lead_with_its_price(self) -> None:
        row = self.enrich(price_min=0.45, is_free=False, is_cheap=False)
        self.assertEqual(row["decision"]["level"], "lead")
        self.assertEqual(row["decision"]["label"], "Paid")
        self.assertIn("€0.45/kWh", row["decision"]["reason"])

    def test_unpriced_charger_is_a_price_lead_never_free(self) -> None:
        row = self.enrich(price_min=None, is_free=False, is_cheap=False)
        self.assertEqual(row["decision"]["level"], "lead")
        self.assertEqual(row["decision"]["label"], "Price lead")
        self.assertEqual(row["decision"]["reason"], "No source publishes a tariff for this charger; check the price in the operator app before charging.")

    def test_hourly_tariff_is_described_without_claiming_an_energy_price(self) -> None:
        row = self.enrich(price_min=None, is_free=False, is_cheap=False,
                          tariff_details={"hour_eur": 2, "conditions": ["First hour free"]})
        self.assertEqual(row["decision"]["level"], "lead")
        self.assertEqual(row["decision"]["label"], "Price lead")
        self.assertIn("hourly tariff", row["decision"]["reason"])
        self.assertNotIn("No source publishes a tariff", row["decision"]["reason"])
        self.assertIsNone(row["price_min"])
        self.assertFalse(row["is_free"])
        self.assertFalse(row["is_cheap"])
        self.assertFalse(row["verification"]["live_verified"])
        self.assertFalse(server.route_ready_now(row))

    def test_tariff_conditions_without_energy_price_are_not_described_as_missing_tariff(self) -> None:
        for tariff in ({"minimum_eur": 2, "text": "Minimum charge €2"},
                       {"conditions": ["Validation required"]},
                       {"components": [{"type": "FLAT", "price": 1, "unit": "session"}]}):
            with self.subTest(tariff=tariff):
                row = self.enrich(price_min=None, is_free=False, is_cheap=False, tariff_details=tariff)
                self.assertEqual(row["decision"]["level"], "lead")
                self.assertEqual(row["decision"]["label"], "Price lead")
                self.assertIn("publishes tariff conditions", row["decision"]["reason"])
                self.assertNotIn("No source publishes a tariff", row["decision"]["reason"])
                self.assertIsNone(row["price_min"])
                self.assertFalse(row["is_free"])
                self.assertFalse(row["is_cheap"])
                self.assertFalse(row["verification"]["live_verified"])

    def test_free_lead_ranks_before_a_nearer_paid_lead(self) -> None:
        free = self.enrich(id="free", price_min=0.0, is_free=True, is_cheap=True)
        paid = self.enrich(id="paid", price_min=0.45, is_free=False, is_cheap=False)
        free["distance_km"], paid["distance_km"] = 5.0, 0.2
        ordered = sorted([paid, free], key=server.nearby_sort_key)
        self.assertEqual([row["id"] for row in ordered], ["free", "paid"])


if __name__ == "__main__":
    unittest.main()
