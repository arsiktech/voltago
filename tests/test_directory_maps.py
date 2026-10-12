"""Fixture tests for the national Electromaps and Chargemap directory adapters.

Fixtures are trimmed copies of the public web-map responses fetched on 2026-10-12.
Stdlib unittest only, no network.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import ingest_sources as ing  # noqa: E402
from station_identity import identity_keys  # noqa: E402

EM_ITEM = {"id": 173774, "name": "Pozo de la Salud", "latitude": 27.755894619677, "longitude": -18.10444247717, "marker": "1.3.0"}
EM_DETAIL = {
    "id": 173774, "name": "Pozo de la Salud (renamed)", "charge_price": "Gratis ", "parking_price": "Gratis",
    "rating": 3.75, "count_ratings": 8, "offline_status": "SOME_SOCKETS_NOT_WORK", "only_tesla": False,
    "address": {"address": "Pozo de la Salud, 3, 38912, Frontera", "postal_code": "38912", "city": "Frontera", "country_code": "IC"},
    "connectors": [
        {"id": 4495956, "type": "IEC_62196_T2", "status": "AVAILABLE", "kw": 20, "power_type": "DC"},
        {"id": 4495957, "type": "IEC_62196_T2", "status": "UNKNOWN", "kw": 20, "power_type": "DC"},
    ],
}
CM_POOL = {
    "id": 91584, "slug": "bmw-4", "name": "BMW - La laguna", "city": "La laguna", "street_name": "Calle de las Industrias",
    "number": "10", "postal_code": "38108", "country_code": "ES", "is_free": True,
    "gps_coordinates": {"lon": -16.2945919037, "lat": 28.4497299194}, "location_type_slug": "car-dealership",
    "operational_status": "OPERATIONAL", "availability_status": "AVAILABLE", "evse_emi3_ids": [],
    "charging_connectors": [{"count": 2, "available_count": 2, "type": "MENNEKES_TYPE_2", "connector_type": {"id": 14}, "power_max": 11}],
}


class ElectromapsNationalTests(unittest.TestCase):
    def test_list_only_row_is_a_price_unknown_lead_without_invented_connectors(self):
        row = ing.parse_electromaps_location(EM_ITEM, None)[0]
        self.assertIsNone(row["price_min"])
        self.assertEqual(row["connector_labels"], [])
        self.assertEqual(row["reliability"], "directory-page")
        self.assertEqual(row["source_url"], "https://map.electromaps.com/es/p/173774")
        self.assertIn(("Electromaps public web map", "electromaps_location_id", "173774"), identity_keys(row))

    def test_detail_adds_directory_free_claim_but_keeps_the_list_name_and_id(self):
        bare = ing.parse_electromaps_location(EM_ITEM, None)[0]
        rich = ing.parse_electromaps_location(EM_ITEM, EM_DETAIL)[0]
        self.assertEqual(rich["id"], bare["id"], "a detail arriving later must not change the row id")
        self.assertEqual(rich["name"], "Pozo de la Salud")
        self.assertEqual(rich["price_min"], 0.0)
        self.assertEqual(rich["reliability"], "directory-map-free")
        self.assertIn("Directory claim", rich["free_condition"])
        self.assertEqual(rich["max_power_kw"], 20.0)
        self.assertTrue(all(c["status"] == "UNKNOWN" for c in rich["connectors"]), "directory status is never live")
        self.assertEqual(rich["raw"]["charge_price_text"], "Gratis")

    def test_price_text_parsing_is_conservative(self):
        self.assertEqual(ing.electromaps_price("Gratis"), 0.0)
        self.assertEqual(ing.electromaps_price("0,35 €/kWh"), 0.35)
        self.assertIsNone(ing.electromaps_price("0,35 €/kWh día, 0,20 €/kWh noche"))
        self.assertIsNone(ing.electromaps_price("Gratis la primera hora"))
        self.assertIsNone(ing.electromaps_price("Desconocido"))

    def test_portugal_and_foreign_details_are_dropped(self):
        self.assertEqual(ing.parse_electromaps_location({**EM_ITEM, "latitude": 38.72, "longitude": -9.14}, None), [])
        foreign = {**EM_DETAIL, "address": {**EM_DETAIL["address"], "country_code": "PT"}}
        self.assertEqual(ing.parse_electromaps_location(EM_ITEM, foreign), [])


class ChargemapNationalTests(unittest.TestCase):
    def test_pool_becomes_a_directory_lead_with_station_page(self):
        row = ing.parse_chargemap_pool(CM_POOL)[0]
        self.assertEqual(row["source_url"], "https://chargemap.com/bmw-4.html")
        self.assertEqual(row["price_min"], 0.0)
        self.assertEqual(row["reliability"], "directory-map-free")
        self.assertEqual(row["total_evse"], 2)
        self.assertEqual(row["max_power_kw"], 11.0)
        self.assertEqual(row["status"], "VERIFY")
        self.assertEqual(row["address"], "Calle de las Industrias 10, 38108, La laguna")
        self.assertIn(("Chargemap public web map", "chargemap_pool_id", "91584"), identity_keys(row))

    def test_paid_or_foreign_pools(self):
        paid = ing.parse_chargemap_pool({**CM_POOL, "is_free": False})[0]
        self.assertIsNone(paid["price_min"])
        self.assertEqual(paid["reliability"], "directory-page")
        self.assertEqual(ing.parse_chargemap_pool({**CM_POOL, "country_code": "PT"}), [])


class BudgetedCacheTests(unittest.TestCase):
    def test_exhausted_budget_reuses_stale_cache_and_skips_missing(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ing, "CACHE", Path(tmp)):
            stale = Path(tmp) / "stale.json"
            stale.write_text(json.dumps({"ok": 1}))
            old = time.time() - 10 * 24 * 3600
            os.utime(stale, (old, old))
            with mock.patch.object(ing, "fetch", side_effect=AssertionError("no network when budget is spent")):
                payload, _artifact = ing.budgeted_cached_json("https://example.invalid/a", "stale.json", ttl_hours=24, budget=[0])
                self.assertEqual(payload, {"ok": 1})
                self.assertIsNone(ing.budgeted_cached_json("https://example.invalid/b", "missing.json", ttl_hours=24, budget=[0]))

    def test_missing_cache_spends_budget(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ing, "CACHE", Path(tmp)):
            budget = [1]
            with mock.patch.object(ing, "fetch", return_value=b'{"n": 2}'):
                payload, _artifact = ing.budgeted_cached_json("https://example.invalid/c", "c.json", ttl_hours=24, budget=budget)
            self.assertEqual(payload, {"n": 2})
            self.assertEqual(budget, [0])


class RegistrationTests(unittest.TestCase):
    def test_national_directory_adapters_are_active_and_old_valencia_ones_stay_retired(self):
        names = [fn.__name__ for fn in ing.ACTIVE_SOURCE_ADAPTERS]
        self.assertIn("source_electromaps_national", names)
        self.assertIn("source_chargemap_national", names)
        self.assertNotIn("source_electromaps_mapi", names)
        self.assertNotIn("source_chargemap_mappy", names)


if __name__ == "__main__":
    unittest.main()
