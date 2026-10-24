#!/usr/bin/env python3
"""Etecnic public charger map: grouped posts, honest status, owner grant gate."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
import ingest_sources as ing  # noqa: E402

ITEMS = [
    {"id": 7, "name": "Pescador Sud - EdRSR 1", "power": 32.0, "phases": 3, "address": "Carrer Robert Gerhard, 2",
     "lat": "41.064699", "lon": "1.0576201", "test_errors": True, "show_map": True,
     "charger_sockets": [{"id": 1, "status": 0, "connector_type_id": 2}, {"id": 2, "status": 1, "connector_type_id": 2}]},
    # Second post of the same site, 5 m away.
    {"id": 8, "name": "Pescador Sud - EdRSR 2", "power": 32.0, "phases": 3, "address": "Carrer Robert Gerhard, 2",
     "lat": "41.064740", "lon": "1.0576300", "test_errors": True, "show_map": True,
     "charger_sockets": [{"id": 3, "status": 3, "connector_type_id": 2}]},
    {"id": 9, "name": "Unmonitored", "power": 16.0, "phases": 1, "address": "Plaça Major", "lat": "41.20", "lon": "1.20",
     "test_errors": False, "show_map": True, "charger_sockets": [{"id": 4, "status": 0}]},
    {"id": 10, "name": "Hidden", "power": 32.0, "phases": 3, "lat": "41.3", "lon": "1.3", "show_map": False, "charger_sockets": []},
    {"id": 11, "name": "Lisboa", "power": 32.0, "phases": 3, "lat": "38.72", "lon": "-9.14", "show_map": True, "charger_sockets": []},
]


class EtecnicIngestTests(unittest.TestCase):
    def test_posts_of_one_site_form_one_station_and_foreign_or_hidden_ones_are_dropped(self) -> None:
        rows = ing.parse_etecnic_chargers(ITEMS)
        self.assertEqual(len(rows), 2)
        site = next(row for row in rows if row["raw"]["etecnic_charger_id"] == "7")
        self.assertEqual(site["name"], "Etecnic — Pescador Sud")
        self.assertEqual(site["raw"]["etecnic_charger_ids"], ["7", "8"])
        self.assertEqual(site["total_evse"], 3)

    def test_amperes_become_kw_by_phase_count(self) -> None:
        self.assertEqual(ing.etecnic_power_kw(32, 3), 22.1)
        self.assertEqual(ing.etecnic_power_kw(16, 1), 3.7)
        self.assertIsNone(ing.etecnic_power_kw(0, 3))

    def test_status_is_fetch_time_evidence_never_live_or_free(self) -> None:
        rows = ing.parse_etecnic_chargers(ITEMS)
        site = next(row for row in rows if row["raw"]["etecnic_charger_id"] == "7")
        self.assertIn("1 available, 1 occupied, 1 out of service", site["sources"][0]["evidence"])
        self.assertIn("not live availability", site["sources"][0]["evidence"])
        self.assertTrue(all(connector["status"] == "UNKNOWN" for connector in site["connectors"]))
        self.assertIsNone(site["price_min"])
        self.assertTrue(site["raw"]["corroboration_only"])
        unmonitored = next(row for row in rows if row["raw"]["etecnic_charger_id"] == "9")
        self.assertIn("1 not monitored", unmonitored["sources"][0]["evidence"])
        self.assertEqual(unmonitored["connector_labels"], [])

    def test_merging_with_typed_osm_connectors_keeps_their_types(self) -> None:
        site = next(row for row in ing.parse_etecnic_chargers(ITEMS) if row["raw"]["etecnic_charger_id"] == "7")
        self.assertEqual(site["connectors"], [])
        osm: list = []
        ing.add_record(osm, source="OpenStreetMap public tags", source_url="https://www.openstreetmap.org/node/1",
                       name="Etecnic", lat=site["lat"], lon=site["lon"], connector_labels=["Type 2"],
                       connectors=[ing.connector_ref("Type 2", count=2, power_kw=22.0)], raw={"operator": "Etecnic"})
        (merged,) = ing.dedupe([site, osm[0]])
        self.assertEqual(merged["connector_labels"], ["Type 2"])

    def test_station_keeps_its_id_when_its_lowest_post_disappears(self) -> None:
        from station_identity import preserve_record_ids
        baseline = [row for row in ing.parse_etecnic_chargers(ITEMS) if row["raw"]["etecnic_charger_id"] == "7"]
        refreshed = ing.parse_etecnic_chargers([ITEMS[1]])  # only post 8 remains
        self.assertNotEqual(baseline[0]["id"], refreshed[0]["id"])
        payload, report = preserve_record_ids({"records": baseline}, {"records": refreshed})
        self.assertEqual(payload["records"][0]["id"], baseline[0]["id"])
        self.assertEqual(report["unresolved_missing_ids"], [])

    def test_collection_stops_when_the_owner_grant_is_removed(self) -> None:
        records: list = []
        with patch.object(ing, "SOURCE_POLICY_EXCLUSIONS", {"granted_reuse": []}), \
             patch.object(ing, "cached_fetch") as fetch:
            ing.source_etecnic(records)
        fetch.assert_not_called()
        self.assertEqual(records, [])

    def test_grant_is_recorded_with_its_honest_basis(self) -> None:
        grants = {grant["id"]: grant for grant in ing.SOURCE_POLICY_EXCLUSIONS["granted_reuse"]}
        grant = grants[ing.ETECNIC_GRANT_ID]
        self.assertEqual(grant["status"], "permitted")
        self.assertIn("aviso legal reserves redistribution", grant["basis"])


if __name__ == "__main__":
    unittest.main()
