"""No excluded contributor can be hidden by a permitted primary source."""
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import ingest_sources as ing
import source_policy as policy
import source_transport as transport


class SourceContributionPolicyTests(unittest.TestCase):
    def test_all_merged_evidence_representations_are_excluded(self):
        base = {"id": "allowed-primary", "source": "OpenStreetMap", "source_url": "https://www.openstreetmap.org/node/1"}
        for evidence in (
            {"sources": [{"name": "PlugShare", "url": "https://www.plugshare.com/location/1"}]},
            {"merged_source_families": ["plugshare communi"]},
            {"merged_source_families": ["electromaps"]},
            {"merged_source_identifiers": [["PlugShare community web map", "plugshare_location_id", "1"]]},
            {"reference_inputs": [{"source": "Chargemap", "source_url": "https://chargemap.com/one"}]},
            {"source_contributions": [{"source": "OpenStreetMap", "sources": [{"name": "Consum"}]}]},
        ):
            with self.subTest(evidence=evidence):
                row = {**base, **evidence}
                allowed, excluded = policy.partition_records([row], ing.SOURCE_POLICY_EXCLUSIONS)
                self.assertEqual(allowed, [])
                self.assertEqual(excluded, [row])
                cleaned, _ = ing.apply_source_policy_exclusions_to_payload({"records": [row]})
                self.assertEqual(cleaned["records"], [])

    def test_operator_mentions_from_an_independent_source_are_not_evidence(self):
        row = {"id": "independent", "source": "OpenStreetMap", "name": "PlugShare named parking",
               "raw": {"operator": "Electromaps", "website": "https://www.electromaps.com/"}}
        allowed, excluded = policy.partition_records([row], ing.SOURCE_POLICY_EXCLUSIONS)
        self.assertEqual(allowed, [row])
        self.assertEqual(excluded, [])

    def test_exclusions_are_applied_before_any_price_hardware_or_identity_merge(self):
        safe = {"id": "safe", "source": "OpenStreetMap", "price_min": None}
        blocked = {"id": "blocked", "source": "PlugShare", "price_min": 0, "is_free": True}
        def fixture_collector(records):
            records.extend(deepcopy([safe, blocked]))
        with mock.patch.object(ing, "ACTIVE_SOURCE_ADAPTERS", (fixture_collector,)), \
                mock.patch.object(ing, "dedupe", side_effect=lambda rows: rows) as dedupe, \
                mock.patch.object(ing, "merge_cross_source_duplicates", side_effect=lambda rows: (rows, 0)):
            result = ing.build_payload()
        self.assertEqual(dedupe.call_args.args[0], [safe])
        self.assertEqual(result["records"], [safe])
        self.assertEqual(result["source_policy_excluded_input_records"], 1)

    def test_excluded_directory_collectors_cannot_read_cache_or_fetch(self):
        with mock.patch.object(ing, "cached_json") as cached, \
                mock.patch.object(ing, "budgeted_cached_json") as budgeted:
            for collector in (ing.source_electromaps_national, ing.source_chargemap_national):
                with self.subTest(collector=collector.__name__):
                    rows = []
                    with self.assertRaises(transport.SourcePolicyBlocked):
                        collector(rows)
                    self.assertEqual(rows, [])
        cached.assert_not_called()
        budgeted.assert_not_called()

    def test_unreviewed_query_adapters_are_blocked_before_cache_network(self):
        with mock.patch.object(ing, "cached_json") as cached, \
                mock.patch.object(ing, "cached_fetch") as fetch, \
                mock.patch.object(ing, "budgeted_cached_json") as budgeted:
            for collector in (ing.source_repsol, ing.source_place_to_plug):
                with self.subTest(collector=collector.__name__):
                    rows = []
                    with self.assertRaises(transport.SourcePolicyBlocked):
                        collector(rows)
                    self.assertEqual(rows, [])
                    self.assertFalse(ing.ADAPTER_STATUS[collector.__name__]["enabled"])
        cached.assert_not_called()
        fetch.assert_not_called()
        budgeted.assert_not_called()

    def test_policy_cleaning_removes_stale_adapter_success_claims(self):
        payload = {"records": [], "source_adapter_counts": {"source_plugshare": 5000, "source_lidl": 3},
                   "source_adapter_status": {"source_electromaps_national": {"complete": True}, "source_lidl": {"complete": True}}}
        clean, _ = ing.apply_source_policy_exclusions_to_payload(payload)
        self.assertEqual(clean["source_adapter_counts"], {"source_lidl": 3})
        self.assertEqual(clean["source_adapter_status"], {"source_lidl": {"complete": True}})


if __name__ == "__main__":
    unittest.main()
