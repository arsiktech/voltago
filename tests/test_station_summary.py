"""Summary transport must preserve decisions and bind disclosures to their catalogue."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from scripts.build_intelligence_index import build_index


def directory_row(identifier="osm_node_1", **updates):
    row = {
        "id": identifier, "name": "Public charging", "lat": 28.49, "lon": -16.33,
        "source": "OpenStreetMap", "source_url": "https://www.openstreetmap.org/node/1",
        "reliability": "community map", "price_min": None,
        "primary_source": {"name": "OpenStreetMap", "url": "https://www.openstreetmap.org/node/1", "kind": "map_object"},
        "sources": [{"name": "OpenStreetMap", "url": "https://www.openstreetmap.org/node/1",
                     "evidence": "Source evidence " * 100, "license": "ODbL", "attribution": "OpenStreetMap contributors"}],
        "insight": "Inspect onsite signs", "source_fetched_at": "2026-10-01T00:00:00Z",
        "raw": {"operator": "Public operator", "ingestion_only": "bulk" * 100},
        "connectors": [{"connector_id": str(i), "type": "TYPE2", "label": "Type 2", "power_kw": 22} for i in range(8)],
        "parking": {"kind": "time_limited_or_validated", "label": "Parking limit", "paid": False, "note": "Check signs"},
        "access": {"level": "customer", "label": "Customer access", "note": "Customers only"},
    }
    row.update(updates)
    return row


class StationSummaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.source = self.directory / "free_intelligence.json"
        self.seeds = self.directory / "seed_leads.json"
        self.seeds.write_text("[]")
        (self.directory / "source_policy_exclusions.json").write_bytes((server.DATA / "source_policy_exclusions.json").read_bytes())
        self.rows = [directory_row()]
        self.write_rows()
        for patcher in (
            patch.object(server, "DATA", self.directory),
            patch.object(server, "intelligence_path", side_effect=lambda: self.source),
            patch.object(server.community_store, "generation", return_value=7),
            patch.object(server.community_store, "summaries", return_value={"stations": {}}),
            patch.dict(server._INTEL_RAW, {"mtime": None, "data": None}, clear=True),
            patch.dict(server._INTEL_ENRICHED, {"key": None}, clear=True),
            patch.dict(server.OVERVIEW_BYTES_CACHE, {}, clear=True),
            patch.dict(server.OVERVIEW_BBOX_BYTES_CACHE, {}, clear=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_rows(self):
        self.source.write_text(json.dumps({"records": self.rows}), encoding="utf-8")

    def overview(self, summary=False, compact=True):
        status, result = server.overview_response(
            {"compact": [str(compact).lower()], "summary": [str(summary).lower()]}, 5.0, False,
        )
        self.assertEqual(status, 200, result)
        return result

    def detail(self, identifier="osm_node_1", snapshot=None):
        return server.station_detail_response({"id": [identifier], "snapshot": [snapshot or server.catalogue_detail_snapshot()]})

    def test_summary_preserves_closed_card_map_safety_and_order(self):
        self.rows += [directory_row("osm_node_2", name="Second point", lat=28.5, is_free=True, price_min=0)]
        self.write_rows()
        full = self.overview()
        reduced = self.overview(summary=True)
        self.assertEqual([r["id"] for r in full["leads"]], [r["id"] for r in reduced["leads"]])
        self.assertEqual(full["meta"]["coverage"], reduced["meta"]["coverage"])
        self.assertEqual(full["meta"]["candidate_records"], reduced["meta"]["candidate_records"])
        self.assertEqual(full["meta"]["truncated"], reduced["meta"]["truncated"])
        # These are driver-facing invariants, not a mirror of the implementation's
        # field allowlist: safety, provider actions, filtering and connector overflow.
        fields = "id name operator owner city address lat lon price_min is_free is_cheap price_label free_condition tesla_ok fast max_power_kw connectors connector_labels connector_summary connector_count available total_evse lead cache_stale verification decision reliability claim_conflict parking access corroboration source_count primary_source source_url source_label source navigation last_seen freshness_minutes recommendation_score pricing price_window_status".split()
        for old, new in zip(full["leads"], reduced["leads"]):
            for field in fields:
                self.assertEqual(old.get(field), new.get(field), field)
            self.assertEqual(len(new["connectors"]), 8)
            self.assertNotIn("charge_claim", new)
            self.assertNotIn("insight", new)
            self.assertNotIn("evidence", new["sources"][0])
            self.assertEqual(new["sources"][0]["attribution"], "OpenStreetMap contributors")
            self.assertEqual(new["sources"][0]["url"], old["sources"][0]["url"])
            self.assertRegex(new["detail_snapshot"], r"^[0-9a-f]{64}$")
        self.assertLess(len(json.dumps(reduced)), len(json.dumps(full)))

    def test_summary_requires_compact_and_legacy_rows_keep_provenance(self):
        for summary, compact in ((False, False), (False, True), (True, False)):
            with self.subTest(summary=summary, compact=compact):
                response = self.overview(summary=summary, compact=compact)
                self.assertNotIn("summary", response["meta"])
                self.assertNotIn("detail_snapshot", response["leads"][0])
                self.assertIn("evidence", response["leads"][0]["sources"][0])
                self.assertIn("charge_claim", response["leads"][0])

    def test_detail_restores_full_provenance_and_stays_directory_without_network(self):
        summary = self.overview(summary=True)["leads"][0]
        with patch.object(server, "policy_urlopen", side_effect=AssertionError("upstream I/O")):
            status, result = self.detail(snapshot=summary["detail_snapshot"])
        self.assertEqual(status, 200)
        self.assertEqual(result["snapshot"], summary["detail_snapshot"])
        station = result["station"]
        self.assertTrue(station["lead"])
        self.assertFalse(station["verification"]["live_verified"])
        self.assertEqual(station["raw"]["ingestion_only"], "bulk" * 100)
        self.assertEqual(station["sources"][0]["evidence"], self.rows[0]["sources"][0]["evidence"])
        self.assertEqual(station["decision"], summary["decision"])
        self.assertIn("charge_claim", station)
        self.assertNotIn("detail_snapshot", station)
        self.assertNotIn("lead", self.rows[0], "lookup must not mutate raw records")

    def test_manual_seed_detail_uses_manual_normalization(self):
        seed = directory_row("manual_one", lat=28.6, reliability="official registry")
        self.seeds.write_text(json.dumps([seed]))
        status, result = self.detail("manual_one")
        self.assertEqual(status, 200)
        self.assertEqual(result["station"]["reliability"], "manual-check-needed")
        self.assertTrue(result["station"]["lead"])
        self.assertFalse(result["station"]["verification"]["live_verified"])

    def test_long_existing_canonical_id_remains_addressable(self):
        identifier = "intel_dgt_" + "a" * 130
        self.rows = [directory_row(identifier)]
        self.write_rows()
        summary = self.overview(summary=True)["leads"][0]
        status, detail = self.detail(identifier, summary["detail_snapshot"])
        self.assertEqual(status, 200)
        self.assertEqual(detail["station"]["id"], identifier)

    def test_missing_excluded_rejected_and_ambiguous_ids_return_no_station(self):
        self.rows += [
            directory_row("excluded", source="Chargemap", source_url="https://chargemap.com/station"),
            directory_row("removed", name="This charging station does not exist", status="UNAVAILABLE"),
            directory_row("duplicate", lat=28.7), directory_row("duplicate", lat=28.8),
        ]
        self.seeds.write_text(json.dumps([directory_row("osm_node_1", lat=28.9)]))
        self.write_rows()
        for identifier in ("missing", "excluded", "removed", "duplicate", "osm_node_1"):
            with self.subTest(identifier=identifier):
                self.assertEqual(self.detail(identifier), (404, {"error": "not_found"}))

    def test_invalid_requests_stop_before_catalogue_lookup(self):
        valid = "a" * 64
        cases = [{}, {"id": ["one"]}, {"id": ["one"], "snapshot": ["invalid"]},
                 {"id": ["../one"], "snapshot": [valid]}, {"id": ["a" * 257], "snapshot": [valid]},
                 {"id": ["one", "two"], "snapshot": [valid]},
                 {"id": ["one"], "snapshot": [valid, valid]}]
        with patch.object(server, "load_intelligence_meta", side_effect=AssertionError("unexpected lookup")):
            for query in cases:
                with self.subTest(query=query):
                    self.assertEqual(server.station_detail_response(query), (400, {"error": "invalid_station_request"}))
            self.assertEqual(self.detail(snapshot=valid), (409, {"error": "snapshot_changed"}))

    def test_snapshot_changes_on_data_seeds_policy_community_and_restart(self):
        def changed(action):
            old = server.catalogue_detail_snapshot()
            action()
            self.assertNotEqual(server.catalogue_detail_snapshot(), old)
            self.assertEqual(self.detail(snapshot=old), (409, {"error": "snapshot_changed"}))
        changed(lambda: self.source.write_text(json.dumps({"records": [directory_row(name="Changed")]})))
        changed(lambda: self.seeds.write_text("[ ]"))
        policy = self.directory / "source_policy_exclusions.json"
        changed(lambda: policy.write_text(policy.read_text() + "\n"))
        changed(lambda: setattr(server.community_store.generation, "return_value", 8))
        with patch.object(server, "_DETAIL_SNAPSHOT_PROCESS", "restart"):
            restarted = server.catalogue_detail_snapshot()
        self.assertNotEqual(restarted, server.catalogue_detail_snapshot())

    def test_detail_and_overview_reject_publication_race_without_facts(self):
        with patch.object(server, "catalogue_detail_snapshot", side_effect=["a" * 64, "b" * 64]):
            self.assertEqual(self.detail(snapshot="a" * 64), (409, {"error": "snapshot_changed"}))
        with patch.object(server, "catalogue_detail_snapshot", side_effect=["a" * 64, "b" * 64]):
            self.assertEqual(server.overview_response({"compact": ["true"], "summary": ["true"]}, 5, False),
                             (409, {"error": "snapshot_changed"}))

    def test_detail_attaches_current_community_and_recomputes_corroboration(self):
        with patch.object(server, "attach_community_rows", wraps=server.attach_community_rows) as community, \
             patch.object(server, "apply_corroboration", wraps=server.apply_corroboration) as corroboration:
            status, result = self.detail()
        self.assertEqual(status, 200)
        community.assert_called_once()
        corroboration.assert_called_once()
        self.assertEqual(result["station"]["recommendation_score"], server.recommendation_score(result["station"]))

    def test_summary_and_community_changes_have_distinct_byte_caches(self):
        for params in ({"compact": ["true"]}, {"compact": ["true"], "bbox": ["27,-18,30,-13"]}):
            with self.subTest(params=params), patch.object(server, "overview_response", wraps=server.overview_response) as response:
                legacy = server.overview_response_bytes(params, 5, False)[1]
                query = {**params, "summary": ["true"]}
                summary = server.overview_response_bytes(query, 5, False)[1]
                self.assertEqual(server.overview_response_bytes(query, 5, False)[1], summary)
                self.assertNotEqual(legacy, summary)
                self.assertEqual(response.call_count, 2)
                server.community_store.generation.return_value += 1
                changed = server.overview_response_bytes(query, 5, False)[1]
                self.assertNotEqual(changed, summary)
                self.assertEqual(response.call_count, 3)
                server.overview_response_bytes(params, 5, False)
                self.assertEqual(response.call_count, 4, "legacy cache must also expire on community writes")

    def test_transient_snapshot_conflicts_are_not_serialized_into_cache(self):
        with patch.object(server, "overview_response", side_effect=[(409, {"error": "snapshot_changed"}), (200, {"leads": []})]) as response:
            self.assertEqual(server.overview_response_bytes({}, 5, False)[0], 409)
            self.assertEqual(server.overview_response_bytes({}, 5, False)[0], 200)
        self.assertEqual(response.call_count, 2)

    def test_index_lookup_is_bounded_and_does_not_resolve_aliases(self):
        self.rows[0]["merged_ids"] = ["alias_one"]
        self.rows += [directory_row("duplicate", lat=28.7), directory_row("duplicate", lat=28.8)]
        self.write_rows()
        indexed = self.directory / "catalogue.sqlite"
        build_index(self.source, indexed, seeds_path=self.seeds, health_summary=True)
        self.source = indexed
        with patch.object(server.IndexedRecords, "__iter__", side_effect=AssertionError("catalogue scan")):
            self.assertEqual(self.detail()[0], 200)
            self.assertEqual(self.detail("alias_one"), (404, {"error": "not_found"}))
            self.assertEqual(self.detail("duplicate"), (404, {"error": "not_found"}))

    def test_route_uses_shared_chargers_budget_and_private_query_logging(self):
        handler = object.__new__(server.Handler)
        handler.path = "/api/station?id=osm_node_1&snapshot=" + server.catalogue_detail_snapshot()
        handler._enforce_rate_limit = Mock(return_value=True)
        handler.send_json = Mock()
        handler._route_get()
        handler._enforce_rate_limit.assert_called_once_with("chargers", server.RATE_LIMIT_RPM)
        self.assertEqual(handler.send_json.call_args.args[0], 200)
        handler._enforce_rate_limit.return_value = False
        handler.send_json.reset_mock()
        handler._route_get()
        handler.send_json.assert_not_called()
        self.assertEqual(server._sanitize_log_line('"GET ' + handler.path + ' HTTP/1.1" 200 -'),
                         '"GET /api/station HTTP/1.1" 200 -')


if __name__ == "__main__":
    unittest.main()
