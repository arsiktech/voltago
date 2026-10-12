"""Directory retirement and station identity through the real ingestion merge order."""
import sys
import unittest
from copy import deepcopy
from itertools import permutations
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import ingest_sources as ing
import server
from station_identity import identity_keys, preserve_record_ids


def pool(**changes):
    row = {"id": 226616, "name": "Betancuria charger", "slug": "betancuria",
           "country_code": "ES", "gps_coordinates": {"lat": 28.42575, "lon": -14.05739},
           "operational_status": "OPERATIONAL", "availability_status": "AVAILABLE",
           "is_free": True, "charging_connectors": [{"type": "CCS", "count": 2, "power_max": 150}]}
    row.update(changes)
    return row


def osm(node, lat=28.42575, lon=-14.05739, name="Enelsa"):
    rows = []
    ing.add_record(rows, source="OpenStreetMap public tags",
                   source_url=f"https://www.openstreetmap.org/node/{node}", name=name,
                   lat=lat, lon=lon, price_min=None, connector_labels=[], connectors=[],
                   reliability="community-price-unknown", evidence="OSM station identity")
    rows[0]["id"] = f"osm-{node}"
    return rows[0]


def em(location, lat=28.42578, lon=-14.05743, name="LP PM BDE BETANCURIA 02"):
    return ing.parse_electromaps_location(
        {"id": location, "latitude": lat, "longitude": lon, "name": name}, None)[0]


def build(rows):
    with patch.object(ing, "ACTIVE_SOURCE_ADAPTERS", (lambda output: output.extend(deepcopy(rows)),)):
        return ing.build_payload()


class DirectoryRetirementTests(unittest.TestCase):
    def test_removed_nonexistent_duplicate_names_are_non_actionable_history(self):
        for name in ("olocau DOUBLON", "Gasolwin - Nàquera DOUBLON", "Station removed",
                     "Station no existe", "Station INEXISTANT", "Station NEXISTE PAS",
                     "Avinguda de Roses, 94 supprimée", "Station duplicate", "Station démontée"):
            with self.subTest(name=name):
                row = ing.parse_chargemap_pool(pool(name=name))[0]
                self.assertEqual(row["source_reported_unavailable"]["value"], name)
                self.assertTrue(ing.directory_history_only(row))
                self.assertFalse(row["is_free"])
                self.assertFalse(row["is_cheap"])

    def test_known_dead_slug_is_non_actionable_history(self):
        row = ing.parse_chargemap_pool(pool(name="Casa Ermita", slug="casa-ermita"))[0]
        self.assertEqual(row["source_reported_unavailable"]["field"], "slug")
        self.assertEqual(row["source_reported_unavailable"]["value"], "casa-ermita")
        self.assertTrue(ing.directory_history_only(row))

    def test_retired_listing_cannot_donate_price_or_hardware_in_build(self):
        for lat in (28.42575, 28.42585):
            with self.subTest(lat=lat):
                rows = [osm(13464829575)]
                ing.parse_chargemap_pool(pool(name="Station removed", gps_coordinates={"lat": lat, "lon": -14.05739}), out=rows)
                payload = build(rows)
                self.assertEqual(len(payload["records"]), 2)
                row = next(r for r in payload["records"] if r["source"] == "OpenStreetMap public tags")
                self.assertIsNone(row["price_min"])
                self.assertEqual(row["connectors"], [])
                self.assertFalse(row["source_integration"]["live_status_integrated"])
                self.assertNotIn("Chargemap", str(row["sources"]))

    def test_inactive_history_is_isolated_in_both_merge_stages_and_keeps_baseline_id(self):
        for lat in (28.42575, 28.42585):
            removed = ing.parse_chargemap_pool(pool(name="Station removed", gps_coordinates={"lat": lat, "lon": -14.05739}))[0]
            active = osm(13464829575)
            directory = em(672287, lat=active["lat"], lon=active["lon"])
            for ordering in permutations([removed, active, directory]):
                with self.subTest(lat=lat, sources=[r["source"] for r in ordering]):
                    payload = build(ordering)
                    self.assertEqual(len(payload["records"]), 2)
                    history = next(r for r in payload["records"] if ing.directory_history_only(r))
                    pin = next(r for r in payload["records"] if not ing.directory_history_only(r))
                    self.assertIsNone(pin["price_min"])
                    self.assertEqual(pin["connectors"], [])
                    self.assertNotIn("source_reported_unavailable", pin)
                    self.assertNotIn(identity_keys(removed).pop(), identity_keys(pin))
                    self.assertFalse(history["is_free"])
                    self.assertFalse(history["is_cheap"])
                    self.assertEqual(server.decision_for(history, live=False)["label"], "Unavailable")
                    _, report = preserve_record_ids({"records": [deepcopy(removed), deepcopy(active)]}, payload)
                    self.assertEqual(report["unresolved_missing_ids"], [])

    def test_history_retains_previously_active_directory_id_without_copying_its_facts(self):
        baseline_row = ing.parse_chargemap_pool(pool(name="Station", is_free=False))[0]
        history = ing.parse_chargemap_pool(pool(name="Station removed"))[0]
        active = osm(13464829575)
        payload, report = preserve_record_ids({"records": [baseline_row, deepcopy(active)]}, build([history, active]))
        self.assertEqual(report["unresolved_missing_ids"], [])
        self.assertEqual({r["id"] for r in payload["records"]}, {baseline_row["id"], active["id"]})
        retained = next(r for r in payload["records"] if r["id"] == baseline_row["id"])
        self.assertTrue(ing.directory_history_only(retained))
        self.assertEqual(retained["source_reported_unavailable"]["value"], "Station removed")
        self.assertFalse(retained["is_free"])
        self.assertFalse(retained["is_cheap"])

    def test_out_of_order_keeps_identity_and_unavailable_semantics_after_merge(self):
        down = ing.parse_chargemap_pool(pool(operational_status="OUT_OF_ORDER", availability_status="UNKNOWN"))[0]
        payload = build([down, osm(13464829575)])
        self.assertEqual(len(payload["records"]), 1)
        row = payload["records"][0]
        self.assertIn((ing.CHARGEMAP_NATIONAL_SOURCE, "chargemap_pool_id", "226616"), identity_keys(row))
        self.assertEqual(row["source_reported_unavailable"]["field"], "operational_status")
        self.assertEqual(server.decision_for(row, live=False)["label"], "Unavailable")
        self.assertFalse(row["source_integration"]["live_status_integrated"])

    def test_suppressed_supercharger_does_not_mark_previous_row_unavailable(self):
        rows = [osm(13464829575)]
        original = deepcopy(rows)
        ing.parse_chargemap_pool(pool(name="Tesla Supercharger", operational_status="OUT_OF_ORDER"), out=rows)
        self.assertEqual(rows, original)
        self.assertEqual(ing.parse_chargemap_pool(pool(name="Tesla Supercharger", operational_status="OUT_OF_ORDER")), [])

    def test_occupancy_unavailable_is_not_removal_or_station_outage(self):
        row = ing.parse_chargemap_pool(pool(availability_status="UNAVAILABLE"))[0]
        self.assertNotIn("source_reported_unavailable", row)
        self.assertEqual(row["status"], "VERIFY")
        self.assertTrue(all(c["status"] == "UNKNOWN" for c in row["connectors"]))
        self.assertFalse(row["source_integration"]["live_status_integrated"])

    def test_electromaps_retirement_names_retain_list_and_detail_witnesses(self):
        item = {"id": 672287, "latitude": 28.42578, "longitude": -14.05743, "name": "Station"}
        for list_name, detail_name in (("Station removed", "Station"), ("Station", "Station duplicate")):
            with self.subTest(list_name=list_name, detail_name=detail_name):
                row = ing.parse_electromaps_location(dict(item, name=list_name),
                    {"name": detail_name, "charge_price": "Gratis", "connectors": [{"type": "CCS", "kw": 150}]})[0]
                self.assertTrue(ing.directory_history_only(row))
                self.assertFalse(row["is_free"])
                self.assertFalse(row["is_cheap"])


class StationIdentityMergeTests(unittest.TestCase):
    def test_distinct_same_family_nodes_in_one_coordinate_cell_stay_separate(self):
        rows = [osm(13464829575), osm(11083800313)]
        payload = build(rows)
        self.assertEqual(len(payload["records"]), 2)
        self.assertEqual({frozenset(identity_keys(r)) for r in payload["records"]},
                         {frozenset(identity_keys(r)) for r in rows})

    def test_colliding_generated_ids_are_disambiguated_by_stable_station(self):
        rows = ing.parse_chargemap_pool(pool(id=1)) + ing.parse_chargemap_pool(pool(id=2))
        self.assertEqual(rows[0]["id"], rows[1]["id"])
        ambiguous_id = rows[0]["id"]
        expected = None
        for ordering in (rows, list(reversed(rows))):
            payload = build(ordering)
            self.assertEqual(len({r["id"] for r in payload["records"]}), 2)
            actual = {tuple(sorted(identity_keys(r))): r["id"] for r in payload["records"]}
            if expected is not None:
                self.assertEqual(actual, expected)
            expected = actual
            self.assertTrue(all(ambiguous_id not in r.get("merged_ids", []) for r in payload["records"]))

    def test_historical_collapsed_id_follows_primary_station_not_absorbed_neighbor(self):
        rows = ing.parse_chargemap_pool(pool(id=1)) + ing.parse_chargemap_pool(pool(id=2))
        old = deepcopy(rows[0])
        old["merged_source_identifiers"] = [[ing.CHARGEMAP_NATIONAL_SOURCE, "chargemap_pool_id", "2"]]
        reconciled, report = preserve_record_ids({"records": [old]}, build(rows))
        self.assertEqual(report["unresolved_missing_ids"], [])
        retained = next(r for r in reconciled["records"] if r["id"] == old["id"])
        self.assertEqual(retained["raw"]["chargemap_pool_id"], "1")
        self.assertNotIn((ing.CHARGEMAP_NATIONAL_SOURCE, "chargemap_pool_id", "2"), identity_keys(retained))
        self.assertEqual(len(reconciled["records"]), 2)

    def test_collision_split_witness_survives_another_source_winning_both_stages(self):
        rows = ing.parse_chargemap_pool(pool(id=1)) + ing.parse_chargemap_pool(pool(id=2))
        old = deepcopy(rows[0])
        old["merged_source_identifiers"] = [[ing.CHARGEMAP_NATIONAL_SOURCE, "chargemap_pool_id", "2"]]
        directory = em(672287, lat=28.42575, lon=-14.05739)
        directory["reliability"] = "official-free-policy"
        deduped = ing.dedupe([rows[0], directory, rows[1]])
        primary = next(r for r in deduped if (ing.CHARGEMAP_NATIONAL_SOURCE, "chargemap_pool_id", "1") in identity_keys(r))
        self.assertEqual(primary["source"], ing.ELECTROMAPS_NATIONAL_SOURCE)
        self.assertIn(old["id"], primary.get("collision_base_ids", []))
        outer = osm(13464829575, lat=28.42585)
        outer["reliability"] = "official-free"
        merged, _ = ing.merge_cross_source_duplicates([*deduped, outer])
        primary = next(r for r in merged if (ing.CHARGEMAP_NATIONAL_SOURCE, "chargemap_pool_id", "1") in identity_keys(r))
        self.assertEqual(primary["source"], "OpenStreetMap public tags")
        self.assertIn(old["id"], primary.get("collision_base_ids", []))
        reconciled, report = preserve_record_ids({"records": [old]}, {"records": merged})
        self.assertEqual(report["unresolved_missing_ids"], [])
        retained = next(r for r in reconciled["records"] if r["id"] == old["id"])
        self.assertNotIn((ing.CHARGEMAP_NATIONAL_SOURCE, "chargemap_pool_id", "2"), identity_keys(retained))
        self.assertEqual(len(reconciled["records"]), 2)

    def test_explicit_historical_merge_can_split_with_counterpart_evidence(self):
        old = osm(1)
        old["merged_ids"] = ["osm-2"]
        old["merged_source_identifiers"] = [["osm", "/node/2"]]
        current = osm(1, lat=28.42585)
        current["id"] = "renamed-primary"
        other = osm(2, lat=28.426)
        payload = build([current, other])
        reconciled, report = preserve_record_ids({"records": [old]}, payload)
        self.assertEqual(report["unresolved_missing_ids"], [])
        retained = next(r for r in reconciled["records"] if r["id"] == old["id"])
        self.assertEqual(identity_keys(retained), {("osm", "/node/1")})
        self.assertEqual(len(reconciled["records"]), 2)

    def test_unproven_historical_merge_does_not_resolve_conflicting_keys(self):
        for aliases in ([], ["unrelated-id"]):
            with self.subTest(aliases=aliases):
                old = osm(1)
                old["merged_ids"] = aliases
                old["merged_source_identifiers"] = [["osm", "/node/2"]]
                current = dict(osm(1), id="renamed-primary")
                _, report = preserve_record_ids({"records": [old]}, build([current, osm(2)]))
                self.assertEqual(report["unresolved_missing_ids"], [old["id"]])

    def test_every_conflicting_match_needs_a_split_witness(self):
        old = osm(1)
        old["merged_ids"] = ["osm-2"]
        old["merged_source_identifiers"] = [["osm", "/node/2"], ["osm", "/node/3"]]
        current = dict(osm(1), id="renamed-primary")
        _, report = preserve_record_ids({"records": [old]}, build([current, osm(2), osm(3)]))
        self.assertEqual(report["unresolved_missing_ids"], [old["id"]])

    def test_historical_merge_witness_survives_current_counterpart_merge(self):
        old = osm(1)
        old["merged_ids"] = ["osm-2"]
        old["merged_source_identifiers"] = [["osm", "/node/2"]]
        current = dict(osm(1), id="renamed-primary")
        other = osm(2, lat=28.427)
        directory = em(672287, lat=28.427, lon=-14.05739)
        reconciled, report = preserve_record_ids({"records": [old]}, build([current, other, directory]))
        self.assertEqual(report["unresolved_missing_ids"], [])
        self.assertEqual(len(reconciled["records"]), 2)

    def test_split_witness_never_overrides_primary_identity_distance_limit(self):
        old = osm(1)
        old["merged_ids"] = ["osm-2"]
        old["merged_source_identifiers"] = [["osm", "/node/2"]]
        current = dict(osm(1, lat=28.43), id="renamed-primary")
        _, report = preserve_record_ids({"records": [old]}, build([current, osm(2)]))
        self.assertEqual(report["unresolved_missing_ids"], [old["id"]])

    def test_duplicate_copies_of_same_stable_station_can_dedupe(self):
        row = osm(13464829575)
        copy = deepcopy(row)
        copy["id"] = "older-generated-id"
        merged = ing.dedupe([row, copy])
        self.assertEqual(len(merged), 1)
        self.assertEqual({merged[0]["id"], *merged[0]["merged_ids"]}, {row["id"], copy["id"]})
        self.assertEqual(identity_keys(merged[0]), identity_keys(row))

    def test_distinct_same_family_rows_without_stable_keys_stay_separate(self):
        rows = [dict(osm(1, name="A"), source_url="https://example.test/map", sources=[]),
                dict(osm(2, name="B"), source_url="https://example.test/map", sources=[])]
        self.assertEqual(len(ing.dedupe(rows)), 2)

    def test_absorbed_family_prevents_later_cross_source_bridge(self):
        a = osm(13464829575, lat=28.42567, lon=-14.05730)
        b = osm(11083800313)
        directory = em(672287)
        # Give the directory the winning rank, hiding OSM as a secondary family.
        premerged = ing.dedupe([b, directory])
        self.assertEqual(len(premerged), 1)
        self.assertEqual(premerged[0]["source"], ing.ELECTROMAPS_NATIONAL_SOURCE)
        merged, _ = ing.merge_cross_source_duplicates([a, *premerged])
        self.assertEqual(len(merged), 2)
        self.assertTrue(all(not {("osm", "/node/13464829575"), ("osm", "/node/11083800313")} <= identity_keys(r) for r in merged))

    def test_betancuria_build_preserves_two_historical_osm_ids_without_invalid_aliases(self):
        a = osm(13464829575, lat=28.42567, lon=-14.05730)
        b = osm(11083800313)
        baseline = {"records": [deepcopy(a), deepcopy(b)]}
        baseline["records"][0]["id"] = "intel_openstreetmap-public-tags_osm-free-charging-station_28.42567_-14.0573"
        baseline["records"][1]["id"] = "intel_openstreetmap-public-tags_osm-free-charging-station_28.42575_-14.05739"
        rows = [a, b, em(672287), em(684549, lat=28.42577, lon=-14.05741)]
        for ordering in permutations(rows):
            with self.subTest(ids=[r["id"] for r in ordering]):
                payload = build(ordering)
                self.assertEqual(len(payload["records"]), 2)
                for family_keys in ({("osm", "/node/13464829575"), ("osm", "/node/11083800313")},
                                    {(ing.ELECTROMAPS_NATIONAL_SOURCE, "electromaps_location_id", "672287"),
                                     (ing.ELECTROMAPS_NATIONAL_SOURCE, "electromaps_location_id", "684549")}):
                    self.assertTrue(all(len(identity_keys(r) & family_keys) == 1 for r in payload["records"]))
                reconciled, report = preserve_record_ids(baseline, payload)
                self.assertEqual(report["unresolved_missing_ids"], [])
                self.assertEqual({r["id"] for r in reconciled["records"]}, {r["id"] for r in baseline["records"]})
                for original in rows:
                    claimants = [r for r in reconciled["records"] if identity_keys(original) & identity_keys(r)]
                    self.assertEqual(len(claimants), 1)
                    claimant = claimants[0]
                    generated = (claimant.get("identity_reconciliation") or {}).get("generated_id")
                    self.assertIn(original["id"], {claimant["id"], generated, *claimant.get("merged_ids", [])})

    def test_carried_family_survives_another_dedupe_pass(self):
        merged = ing.dedupe([osm(1), em(1)])[0]
        # Source evidence is display-bounded; family tracking must not depend on that list.
        merged["sources"] = []
        other = osm(2, lat=28.42585)
        first = ing.dedupe([merged, other])
        self.assertEqual(len(first), 2)
        second, _ = ing.merge_cross_source_duplicates(first)
        self.assertEqual(len(second), 2)


if __name__ == "__main__":
    unittest.main()
