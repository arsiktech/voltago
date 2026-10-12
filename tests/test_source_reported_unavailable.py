"""A charger its own source reports out of service stays on the map but is never a destination,
and a row merged into a pin from another source keeps its stable identifier, so a data refresh can
prove the merge instead of reporting the row as lost. Stdlib unittest, no network."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import ingest_sources as ing  # noqa: E402
import qa_ingestion_delta as delta  # noqa: E402
import server  # noqa: E402
from station_identity import identity_keys  # noqa: E402

FENIE = {
    "nombre_punto": "Casa de Cultura", "direccion": "Calle Mayor, 1, Mutilva, Navarra",
    "id": 900, "code": "0410", "tarifa": 0, "gratuito": 1, "estado": 1,
    "conectores": [{"type": "Mennekes", "count": 2, "power": "22.0"}],
    "lat": 42.79972, "lon": -1.61889,
}
NAP_KEY = ("DGT National Access Point charging registry", "nap_site_id", "2024007482")


def nap_row():
    return {
        "id": "intel_dgt-national-access-point-charging-registry_casa-de-cultura_42.79972_-1.61889",
        "source": NAP_KEY[0], "source_url": "https://nap.dgt.es/dataset/puntos-de-recarga-electrica-para-vehiculos",
        "raw": {"nap_site_id": NAP_KEY[2]}, "lat": 42.79972, "lon": -1.61889, "sources": [],
    }


class MergedIdentifierTests(unittest.TestCase):
    def test_pin_keeps_identifier_of_row_from_another_source(self):
        pin = ing.fenie_records_from_markers([FENIE])[0]
        old = nap_row()
        self.assertFalse(identity_keys(old) & identity_keys(pin))      # the gap that blocked refreshes
        ing.record_merged_ids(pin, [pin, old])
        self.assertIn(old["id"], pin["merged_ids"])
        self.assertIn(list(NAP_KEY), pin["merged_source_identifiers"])
        self.assertTrue(identity_keys(old) & identity_keys(pin))
        # The delta gate now accepts the merge instead of counting the old id as missing.
        self.assertEqual(delta.merged_into([old], [pin], [old["id"]]), {old["id"]: pin["id"]})

    def test_identifiers_survive_a_second_merge_and_skip_own_keys(self):
        pin = ing.fenie_records_from_markers([FENIE])[0]
        ing.record_merged_ids(pin, [pin, nap_row()])
        outer = dict(pin, id="intel_outer")
        ing.record_merged_ids(outer, [outer, pin])
        carried = {tuple(k) for k in outer["merged_source_identifiers"]}
        self.assertIn(NAP_KEY, carried)
        self.assertNotIn(("Feníe Energía public map (Gratuitos)", "fenie_code", "0410"), carried - set())  # own key
        self.assertTrue(carried.isdisjoint(identity_keys({k: v for k, v in outer.items() if k != "merged_source_identifiers"})))

    def test_malformed_carried_identifiers_are_ignored(self):
        row = {"source": "x", "merged_source_identifiers": [["osm"], [1, 2], ["a", ""], "osm/node/1", ["osm", "/node/1"]]}
        self.assertEqual(identity_keys(row), {("osm", "/node/1")})


class SourceReportedUnavailableTests(unittest.TestCase):
    def test_outage_survives_merge(self):
        down = ing.fenie_records_from_markers([dict(FENIE, estado=4)])[0]
        other = nap_row()
        ing.record_merged_ids(other, [other, down])
        self.assertEqual(other["source_reported_unavailable"]["value"], 4)

    def test_never_go_now_even_when_free_live_and_available(self):
        item = {"is_free": True, "tesla_ok": True, "available": 2, "status": "AVAILABLE",
                "source_reported_unavailable": {"summary": "Feníe reports this charger unavailable."}}
        for live in (True, False):
            decision = server.decision_for(dict(item), live=live)
            self.assertEqual(decision["level"], "avoid")
            self.assertEqual(decision["label"], "Unavailable")
            self.assertIn("unavailable", decision["reason"])

    def test_without_outage_decision_is_unchanged(self):
        item = {"is_free": True, "tesla_ok": True, "available": 0, "status": "VERIFY"}
        self.assertNotEqual(server.decision_for(dict(item), live=False)["label"], "Unavailable")


if __name__ == "__main__":
    unittest.main()
