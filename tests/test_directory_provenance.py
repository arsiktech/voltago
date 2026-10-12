"""Offline regressions for directory acquisition dates and unsupported attribution."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts"), str(Path(__file__).resolve().parent)]
import ingest_sources as ing
from test_directory_maps import EM_ITEM, EM_DETAIL
from test_operator_feeds import ETECNIC_ROWS

FRESH = "2026-10-12T12:00:00Z"
OLD = "2026-09-14T12:00:00Z"
OBSERVED = "2026-09-10T11:00:00Z"


def artifact(name, fetched_at, *, stale=False, fallback=False, error=None):
    return ing.FetchArtifact(body=b"{}", url="https://www.electromaps.com/mapi/v2/" + name,
                             cache_name=name, fetched_at=fetched_at, age_seconds=0,
                             hit=True, fallback=fallback, stale=stale, error=error)


class DirectoryProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.evidence = mock.patch.object(ing, "FETCH_EVIDENCE", [])
        self.evidence.start()
        self.addCleanup(self.evidence.stop)

    def test_fresh_list_old_detail_keeps_both_fetches_and_original_observation(self):
        listing = artifact("list.json", FRESH)
        detail = artifact("detail.json", OLD)
        row = ing.parse_electromaps_location(EM_ITEM, {**EM_DETAIL, "updated_at": OBSERVED},
                                           source_artifact=listing, detail_artifact=detail)[0]
        self.assertEqual(row["source_fetched_at"], OLD)
        self.assertEqual(row["last_seen"], OBSERVED)
        self.assertEqual(row["raw"]["updated_at"], OBSERVED)
        self.assertEqual(row["raw"]["list_fetch"], listing.as_evidence())
        self.assertEqual(row["raw"]["detail_fetch"], detail.as_evidence())
        self.assertEqual(row["location_claim"]["source_fetched_at"], FRESH)
        for claim in ("charge_claim", "hardware_claim", "parking_claim"):
            self.assertEqual(row[claim]["source_fetched_at"], OLD)
        self.assertEqual({s["source_fetched_at"] for s in row["sources"]}, {FRESH, OLD})

    def test_old_list_fresh_detail_uses_oldest_contribution(self):
        row = ing.parse_electromaps_location(EM_ITEM, EM_DETAIL,
                                           source_artifact=artifact("list.json", OLD),
                                           detail_artifact=artifact("detail.json", FRESH))[0]
        self.assertEqual(row["source_fetched_at"], OLD)
        self.assertEqual(row["hardware_claim"]["source_fetched_at"], FRESH)

    def test_list_only_and_invalid_detail_cannot_acquire_fresh_claims(self):
        for detail in (None, [], {**EM_DETAIL, "address": "invalid"}, {**EM_DETAIL, "address": []},
                       {**EM_DETAIL, "connectors": {}}, {**EM_DETAIL, "connectors": {"kw": 50}}):
            with self.subTest(detail=detail):
                row = ing.parse_electromaps_location(EM_ITEM, detail,
                                                   source_artifact=artifact("list.json", OLD),
                                                   detail_artifact=artifact("detail.json", FRESH))[0]
                self.assertEqual(row["source_fetched_at"], OLD)
                self.assertIsNone(row["price_min"])
                self.assertIsNone(row["max_power_kw"])
                self.assertFalse(row["raw"]["detail_fetched"])
                self.assertIsNone(row["last_seen"])
                self.assertEqual(len(row["sources"]), 1)

    def test_collection_preserves_each_regions_artifact_and_detail_artifact(self):
        mainland = {**EM_ITEM, "id": 10, "name": "Madrid", "latitude": 40.42, "longitude": -3.70}
        island = {**EM_ITEM, "id": 20}
        responses = [([mainland], artifact("mainland.json", FRESH)),
                     ([island], artifact("island.json", OLD))]
        detail = artifact("detail.json", OLD)
        with mock.patch.object(ing, "cached_json", side_effect=responses), \
             mock.patch.object(ing, "budgeted_cached_json", side_effect=lambda url, cache_name, **kw: ({**EM_DETAIL, "id": 10}, detail) if cache_name == "electromaps_location_10.json" else None), \
             mock.patch.object(ing, "ELECTROMAPS_MIN_ROWS", 2):
            rows = []
            ing.source_electromaps_national(rows)
        by_id = {r["raw"]["electromaps_location_id"]: r for r in rows}
        self.assertEqual(by_id["10"]["source_fetched_at"], OLD)
        self.assertEqual(by_id["20"]["source_fetched_at"], OLD)
        self.assertEqual(by_id["10"]["price_min"], 0.0)
        self.assertIsNone(by_id["20"]["price_min"])
        self.assertEqual(by_id["10"]["raw"]["list_fetch"]["fetched_at"], FRESH)
        self.assertEqual(by_id["20"]["raw"]["list_fetch"]["fetched_at"], OLD)

    def test_fresh_detail_cache_propagates_actual_artifact_without_network(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ing, "CACHE", Path(tmp)), \
             mock.patch.object(ing, "fetch", side_effect=AssertionError("offline test")):
            path = Path(tmp) / "electromaps_location_173774.json"
            path.write_text(json.dumps(EM_DETAIL))
            old = time.time() - 20 * 86400
            os.utime(path, (old, old))
            budget = [1]
            payload, detail = ing.electromaps_cached_detail(173774, budget)
            self.assertEqual(payload, EM_DETAIL)
            self.assertEqual(detail.fetched_at, ing.epoch_iso(old))
            self.assertEqual(budget, [1])

    def test_stale_fallback_retains_date_error_and_cache_flags_on_record(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ing, "CACHE", Path(tmp)), \
             mock.patch.object(ing, "fetch", side_effect=TimeoutError("fixture timeout")):
            path = Path(tmp) / "electromaps_location_173774.json"
            path.write_text(json.dumps(EM_DETAIL))
            old = time.time() - 40 * 86400
            os.utime(path, (old, old))
            payload, detail = ing.electromaps_cached_detail(173774, [1])
            row = ing.parse_electromaps_location(EM_ITEM, payload,
                                               source_artifact=artifact("list.json", FRESH), detail_artifact=detail)[0]
            self.assertEqual(row["source_fetched_at"], ing.epoch_iso(old))
            self.assertTrue(row["raw"]["detail_fetch"]["cache"]["fallback"])
            self.assertTrue(row["raw"]["detail_fetch"]["cache"]["stale"])
            self.assertEqual(row["raw"]["detail_fetch"]["error"], "TimeoutError")

    def test_exhausted_budget_reuses_stale_detail_without_refreshing_its_date(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ing, "CACHE", Path(tmp)), \
             mock.patch.object(ing, "fetch", side_effect=AssertionError("offline test")):
            path = Path(tmp) / "electromaps_location_173774.json"
            path.write_text(json.dumps(EM_DETAIL))
            old = time.time() - 40 * 86400
            os.utime(path, (old, old))
            payload, detail = ing.electromaps_cached_detail(173774, [0])
            row = ing.parse_electromaps_location(EM_ITEM, payload,
                                               source_artifact=artifact("list.json", FRESH), detail_artifact=detail)[0]
            self.assertEqual(row["source_fetched_at"], ing.epoch_iso(old))
            self.assertTrue(row["raw"]["detail_fetch"]["cache"]["stale"])
            self.assertFalse(row["raw"]["detail_fetch"]["cache"]["fallback"])
            self.assertNotIn("error", row["raw"]["detail_fetch"])

    def test_missing_detail_cache_without_budget_is_not_a_fetch(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ing, "CACHE", Path(tmp)), \
             mock.patch.object(ing, "fetch", side_effect=AssertionError("offline test")):
            self.assertEqual(ing.electromaps_cached_detail(173774, [0]), (None, None))
            self.assertEqual(ing.FETCH_EVIDENCE, [])

    def test_missing_detail_fetch_error_is_recorded_without_acquisition_date(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ing, "CACHE", Path(tmp)), \
             mock.patch.object(ing, "fetch", side_effect=TimeoutError("fixture timeout")):
            self.assertEqual(ing.electromaps_cached_detail(173774, [1]), (None, None))
            self.assertEqual(ing.FETCH_EVIDENCE[-1]["error"], "TimeoutError")
            self.assertNotIn("fetched_at", ing.FETCH_EVIDENCE[-1])

    def test_malformed_detail_keeps_fetch_evidence_but_no_claims(self):
        for body in (b"not json", b"[]", b'{"address": "invalid"}', b'{"connectors": {"kw": 50}}'):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as tmp, \
                 mock.patch.object(ing, "CACHE", Path(tmp)), \
                 mock.patch.object(ing, "fetch", return_value=body):
                payload, detail = ing.electromaps_cached_detail(173774, [1])
                self.assertIsNone(payload)
                self.assertTrue(ing.FETCH_EVIDENCE[-1].get("error") or ing.FETCH_EVIDENCE[-1].get("parse_error"))
                row = ing.parse_electromaps_location(EM_ITEM, payload,
                                                   source_artifact=artifact("list.json", OLD), detail_artifact=detail)[0]
                self.assertEqual(row["source_fetched_at"], OLD)
                self.assertIsNone(row["price_min"])

    def test_balearic_private_point_is_not_attributed_to_melib_or_evcharge_operator(self):
        private = {**ETECNIC_ROWS[1], "name": "SIXT Palma"}
        row = ing.parse_etecnic_chargers([private])[0]
        self.assertEqual(row["raw"]["network"], "Etecnic platform (operator not established)")
        self.assertNotIn("MELIB", row["insight"])
        self.assertNotIn("Govern", json.dumps(row))
        self.assertNotIn("operator_app", row["raw"])


if __name__ == "__main__":
    unittest.main()
