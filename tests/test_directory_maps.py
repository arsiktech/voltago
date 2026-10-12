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
import urllib.parse
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


def fake_artifact(cache_name, fetched_at, *, stale=False):
    return ing.FetchArtifact(body=b"{}", url="https://example.invalid/" + cache_name, cache_name=cache_name,
                             fetched_at=fetched_at, age_seconds=0.0, hit=True, fallback=False, stale=stale)


def em_listing(i):
    return {"id": i, "name": f"EM {i}", "latitude": 40.4 + i * 1e-4, "longitude": -3.7, "marker": "1.1.0"}


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

    def test_mixed_fee_text_is_never_a_kwh_price(self):
        for text in (
            "0,00 €/kWh + 0,05 €/min",
            "0.20€/kWh + 2€ por sesión",
            "0,35 €/kWh más 1 € de parking",
            "0,30 €/kWh, 0,10 €/min tras 30 min",
            "0.35-0.54 €/KWH",
            "0,25 €/kWh primera hora",
            "Gratis para clientes",
        ):
            self.assertIsNone(ing.electromaps_price(text), text)
        for text, expected in (("0,25€/kWh", 0.25), ("0.35 €/KWH", 0.35), ("0,35€ / KWH", 0.35), ("0,4 €/Kwh", 0.4)):
            self.assertEqual(ing.electromaps_price(text), expected, text)
        mixed = ing.parse_electromaps_location(EM_ITEM, {**EM_DETAIL, "charge_price": "0,00 €/kWh + 0,05 €/min"})[0]
        self.assertIsNone(mixed["price_min"])
        self.assertEqual(mixed["charge_claim"]["type"], "price_unknown")
        self.assertEqual(mixed["reliability"], "directory-page")
        self.assertFalse(mixed["is_free"])

    def test_detail_provenance_is_the_detail_artifact_and_list_only_stays_list(self):
        list_art = fake_artifact("electromaps_list_es_0.json", "2026-10-12T08:00:00Z")
        detail_art = fake_artifact("electromaps_location_173774.json", "2026-09-20T08:00:00Z", stale=True)
        rich = ing.parse_electromaps_location(EM_ITEM, EM_DETAIL, source_artifact=list_art, detail_artifact=detail_art)[0]
        self.assertEqual(rich["source_fetched_at"], "2026-09-20T08:00:00Z")
        self.assertEqual(rich["raw"]["list_fetched_at"], "2026-10-12T08:00:00Z")
        self.assertEqual(rich["raw"]["detail_fetched_at"], "2026-09-20T08:00:00Z")
        self.assertTrue(rich["raw"]["detail_stale"])
        bare = ing.parse_electromaps_location(EM_ITEM, None, source_artifact=list_art)[0]
        self.assertEqual(bare["source_fetched_at"], "2026-10-12T08:00:00Z")
        self.assertFalse(bare["raw"]["detail_fetched"])
        self.assertIsNone(bare["raw"]["detail_fetched_at"])

    def test_portugal_and_foreign_details_are_dropped(self):
        self.assertEqual(ing.parse_electromaps_location({**EM_ITEM, "latitude": 38.72, "longitude": -9.14}, None), [])
        foreign = {**EM_DETAIL, "address": {**EM_DETAIL["address"], "country_code": "PT"}}
        self.assertEqual(ing.parse_electromaps_location(EM_ITEM, foreign), [])


class ElectromapsDetailFairnessTests(unittest.TestCase):
    def _run(self, tmp, items, cached, budget):
        """Run the adapter offline; `cached` maps location id -> age in days of an existing detail."""
        for loc_id, age_days in cached.items():
            path = Path(tmp) / f"electromaps_location_{loc_id}.json"
            path.write_text(json.dumps({"id": loc_id, "charge_price": "Gratis"}))
            old = time.time() - age_days * 86400
            os.utime(path, (old, old))
        fetched = []

        def fake_cached_json(url, cache_name, ttl_hours=24, *, with_artifact=False, **kw):
            artifact = fake_artifact(cache_name, "2026-10-12T08:00:00Z")
            if cache_name.startswith("electromaps_location_"):
                loc_id = cache_name[len("electromaps_location_"):-len(".json")]
                fetched.append(loc_id)  # budgeted_cached_json only reaches here when it spends budget
                payload = {"id": int(loc_id), "charge_price": "0,30 €/kWh"}
            else:
                payload = items
            return (payload, artifact) if with_artifact else payload

        records = []
        env = {"VOLTA_ELECTROMAPS_DETAIL_BUDGET": str(budget)}
        with mock.patch.object(ing, "CACHE", Path(tmp)), mock.patch.object(ing, "ELECTROMAPS_MIN_ROWS", 1), \
                mock.patch.dict(os.environ, env), mock.patch.object(ing, "cached_json", fake_cached_json):
            ing.ADAPTER_STATUS.clear()
            ing.source_electromaps_national(records)
        return records, fetched

    def test_budget_goes_to_missing_details_newest_first_then_oldest_cached(self):
        items = [em_listing(i) for i in range(1, 9)]
        with tempfile.TemporaryDirectory() as tmp:
            # 1 and 2 are cached (2 is much older and past the 30-day TTL); 3..8 have no detail.
            records, fetched = self._run(tmp, items, {1: 1, 2: 40}, budget=3)
        self.assertEqual(fetched, ["8", "7", "6"], "new high ids are not starved by low ids")
        by_id = {r["raw"]["electromaps_location_id"]: r for r in records}
        self.assertTrue(by_id["8"]["raw"]["detail_fetched"])
        self.assertFalse(by_id["3"]["raw"]["detail_fetched"])
        self.assertTrue(by_id["2"]["raw"]["detail_fetched"], "an expired cache is served when no budget is left")
        self.assertTrue(by_id["2"]["raw"]["detail_stale"])
        status = ing.ADAPTER_STATUS["source_electromaps_national"]
        self.assertEqual((status["rows_with_detail"], status["rows_without_detail"], status["complete"]), (5, 3, False))
        self.assertEqual(status["detail_network_fetches"], 3)

    def test_with_no_missing_details_the_oldest_cache_is_refreshed_first(self):
        items = [em_listing(i) for i in range(1, 5)]
        with tempfile.TemporaryDirectory() as tmp:
            records, fetched = self._run(tmp, items, {1: 35, 2: 60, 3: 45, 4: 1}, budget=2)
        self.assertEqual(fetched, ["2", "3"])
        self.assertTrue(ing.ADAPTER_STATUS["source_electromaps_national"]["complete"])


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


def cm_pool(i, **extra):
    return {**CM_POOL, "id": i, "slug": f"p{i}", "name": f"Pool {i}",
            "gps_coordinates": {"lat": 40.0 + i * 1e-4, "lon": -3.7}, **extra}


class ChargemapWalkTests(unittest.TestCase):
    """Run the quadtree walk offline against a stub box response."""

    def _run(self, tmp, responder, *, previous=0, min_rows=3):
        records = []
        calls = []

        def fake_budgeted(url, cache_name, *, ttl_hours, budget, **kw):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            box = (query["NW"][0], query["SE"][0])
            calls.append(box)
            payload = responder(box, len(calls))
            if payload is None:
                return None
            return payload, fake_artifact(cache_name, "2026-10-12T08:00:00Z", stale=False)

        if previous:
            (Path(tmp) / ing.CHARGEMAP_LAST_RUN_CACHE).write_text(json.dumps({"rows": previous}))
        with mock.patch.object(ing, "CACHE", Path(tmp)), mock.patch.object(ing, "CHARGEMAP_MIN_ROWS", min_rows), \
                mock.patch.object(ing, "budgeted_cached_json", fake_budgeted):
            ing.ADAPTER_STATUS.clear()
            try:
                ing.source_chargemap_national(records)
                error = None
            except RuntimeError as exc:
                error = str(exc)
        return records, error, calls

    @staticmethod
    def _items(*pools, cluster=False):
        items = [{"type": "pool", "pool": p} for p in pools]
        if cluster:
            items.append({"type": "cluster", "count": 50})
        return {"response": {"content": {"items": items}}}

    def test_complete_walk_publishes_rows_and_records_status(self):
        pools = [cm_pool(i) for i in range(1, 6)]
        with tempfile.TemporaryDirectory() as tmp:
            records, error, _ = self._run(tmp, lambda box, n: self._items(*pools) if n <= 2 else self._items())
            self.assertIsNone(error)
            self.assertEqual(len(records), 5)
            status = ing.ADAPTER_STATUS["source_chargemap_national"]
            self.assertTrue(status["complete"])
            self.assertEqual(status["incomplete_boxes"], 0)
            baseline = json.loads((Path(tmp) / ing.CHARGEMAP_LAST_RUN_CACHE).read_text())
            self.assertEqual(baseline["rows"], 5)

    def test_clusters_at_max_depth_count_as_incomplete(self):
        pools = [cm_pool(i) for i in range(1, 6)]
        # Every box keeps reporting a cluster, so the walk bottoms out at the depth limit.
        with mock.patch.object(ing, "CHARGEMAP_MAX_DEPTH", 1), tempfile.TemporaryDirectory() as tmp:
            records, error, _ = self._run(tmp, lambda box, n: self._items(*pools, cluster=True))
            self.assertIsNone(error)
            status = ing.ADAPTER_STATUS["source_chargemap_national"]
            self.assertGreater(status["max_depth_incomplete_boxes"], 0)
            self.assertEqual(status["incomplete_boxes"], status["max_depth_incomplete_boxes"])
            self.assertFalse(status["complete"])

    def test_walk_below_the_row_floor_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            records, error, _ = self._run(tmp, lambda box, n: self._items(cm_pool(1)) if n == 1 else None, min_rows=3)
            self.assertEqual(records, [])
            self.assertIn("refusing a partial walk", error)
            self.assertFalse((Path(tmp) / ing.CHARGEMAP_LAST_RUN_CACHE).exists(), "a failed walk sets no baseline")

    def test_walk_far_below_the_previous_run_fails_closed(self):
        pools = [cm_pool(i) for i in range(1, 6)]
        with tempfile.TemporaryDirectory() as tmp:
            records, error, _ = self._run(tmp, lambda box, n: self._items(*pools) if n == 1 else None, previous=20, min_rows=3)
            self.assertEqual(records, [])
            self.assertIn("<18", error)
            records, error, _ = self._run(tmp, lambda box, n: self._items(*pools) if n == 1 else None, previous=5, min_rows=3)
            self.assertIsNone(error)
            self.assertEqual(len(records), 5)

    def test_declared_spanish_pool_near_the_border_is_kept(self):
        # Coordinates just west of the coarse outline; the pool's own country code is ES.
        pool = cm_pool(9, gps_coordinates={"lat": 42.07, "lon": -7.43}, city="Verin", country_code="ES")
        self.assertEqual(len(ing.parse_chargemap_pool(pool)), 1)
        self.assertEqual(ing.parse_chargemap_pool({**pool, "country_code": "PT"}), [])
        self.assertEqual(ing.parse_chargemap_pool({**pool, "country_code": ""}), [], "no declaration -> geometry decides")


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
