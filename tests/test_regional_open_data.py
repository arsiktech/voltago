#!/usr/bin/env python3
"""Catalonia, Navarra and Castilla y León open-data charger registries."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
import ingest_sources as ing  # noqa: E402

CATALONIA = [
    {"id": "1", "promotor_gestor": "Ajuntament de Sabadell", "acces": "targeta", "tipus_connexi": "CCS Combo2+ChadeMO+MENNEKES.M",
     "latitud": "41.556168", "longitud": "2.112276", "kw": "50", "adre_a": "Parc Taulí, 9", "municipi": "Sabadell"},
    # A second post of the same station, 4 m away.
    {"id": "2", "promotor_gestor": "Ajuntament de Sabadell", "acces": "targeta", "tipus_connexi": "MENNEKES.M",
     "latitud": "41.556200", "longitud": "2.112290", "kw": "22", "adre_a": "Parc Taulí, 9", "municipi": "Sabadell"},
    # A different operator at the same spot stays a separate station.
    {"id": "3", "promotor_gestor": "Endesa X Way", "tipus_connexi": "MENNEKES.M",
     "latitud": "41.556168", "longitud": "2.112276", "kw": "22", "adre_a": "Parc Taulí, 9", "municipi": "Sabadell"},
    {"id": "4", "promotor_gestor": "Bad row", "latitud": "", "longitud": "2.1"},
]
NAVARRA = {"features": [
    {"geometry": {"type": "Point", "coordinates": [-2.17954, 42.88353]},
     "properties": {"FEATURE": 8000071, "MUNICIPIO": "Olazti / Olazagutía", "OPERADOR": "CHARGING TOGETHER SL",
                    "DIRECCION": "A-1,Km. 396", "HORARIO": "24h", "CONEXION": "IEC_62196_T2_COMBO", "POTENCIA": "180,00 kW"}},
]}
CASTILLA_LEON = [
    {"nombre": "Ayuntamiento de Arévalo", "direccion": "Plaza del Real 12 - Arévalo (ÁVILA)", "operador": "Ayuntamiento de Arévalo",
     "no": "1", "tipo": ["Mennekes (Type 2)", "Schuko"], "dd": {"lon": -4.7203146, "lat": 41.0648968}},
]


class RegionalOpenDataTests(unittest.TestCase):
    def test_catalonia_groups_posts_of_one_operator_and_keeps_operators_apart(self) -> None:
        rows = ing.parse_catalonia_icaen(CATALONIA)
        self.assertEqual(len(rows), 2)
        sabadell = next(row for row in rows if row["raw"]["operator"] == "Ajuntament de Sabadell")
        self.assertEqual(sabadell["total_evse"], 4)
        self.assertEqual(sabadell["max_power_kw"], 50)
        self.assertEqual(set(sabadell["connector_labels"]), {"CCS2", "CHAdeMO", "Type 2"})
        self.assertEqual(sabadell["raw"]["regional_row_ids"], ["1", "2"])

    def test_station_ids_do_not_depend_on_registry_row_order(self) -> None:
        forward = {row["id"] for row in ing.parse_catalonia_icaen(CATALONIA)}
        backward = {row["id"] for row in ing.parse_catalonia_icaen(list(reversed(CATALONIA)))}
        self.assertEqual(forward, backward)
        sabadell = next(row for row in ing.parse_catalonia_icaen(list(reversed(CATALONIA)))
                        if row["raw"]["operator"] == "Ajuntament de Sabadell")
        self.assertEqual(sabadell["raw"]["regional_station_key"], "catalonia:1")

    def test_a_moved_regional_station_keeps_its_id_through_its_row_key(self) -> None:
        from station_identity import preserve_record_ids
        old = ing.parse_catalonia_icaen(CATALONIA[:1])
        moved = dict(CATALONIA[0], latitud="41.557000")  # coordinate correction, about 90 m
        new = ing.parse_catalonia_icaen([moved])
        self.assertNotEqual(old[0]["id"], new[0]["id"])
        payload, report = preserve_record_ids({"records": old}, {"records": new})
        self.assertEqual(payload["records"][0]["id"], old[0]["id"])
        self.assertEqual(report["unresolved_missing_ids"], [])

    def test_a_post_without_a_connector_type_adds_no_untyped_inventory(self) -> None:
        rows = ing.parse_catalonia_icaen([{"id": "9", "promotor_gestor": "X", "tipus_connexi": "", "kw": "7",
                                            "latitud": "41.5", "longitud": "2.0", "adre_a": "C/ A", "municipi": "B"}])
        self.assertEqual(rows[0]["connectors"], [])
        self.assertEqual(rows[0]["connector_labels"], [])
        self.assertEqual(rows[0]["total_evse"], 1)
        self.assertEqual(rows[0]["max_power_kw"], 7)

    def test_dedupe_keeps_two_operators_at_one_spot_apart(self) -> None:
        stations = ing.dedupe(ing.parse_catalonia_icaen(CATALONIA))
        self.assertEqual(sorted(row["raw"]["operator"] for row in stations), ["Ajuntament de Sabadell", "Endesa X Way"])
        self.assertTrue(all(not row.get("merged_ids") for row in stations))

    def test_a_partly_typed_station_keeps_its_full_post_count(self) -> None:
        rows = [dict(CATALONIA[0], tipus_connexi="MENNEKES.M"), dict(CATALONIA[1], tipus_connexi="")]
        (station,) = ing.parse_catalonia_icaen(rows)
        self.assertEqual(station["total_evse"], 2)
        self.assertEqual(station["connector_labels"], ["Type 2"])
        self.assertEqual(station["raw"]["regional_row_ids"], ["1", "2"])
        (deduped,) = ing.dedupe([station])
        self.assertEqual(deduped["total_evse"], 2)

    def test_rows_are_corroboration_only_with_no_free_or_live_claim(self) -> None:
        for row in ing.parse_catalonia_icaen(CATALONIA) + ing.parse_navarra_idena(NAVARRA) + ing.parse_castilla_leon(CASTILLA_LEON):
            self.assertTrue(row["raw"]["corroboration_only"])
            self.assertIsNone(row["price_min"])
            self.assertFalse(row["is_free"])
            self.assertEqual(row["reliability"], "official-charger-location-price-unknown")
            self.assertTrue(all(connector["status"] == "UNKNOWN" for connector in row["connectors"]))

    def test_each_source_carries_its_licence_and_attribution(self) -> None:
        expected = {
            "Catalonia ICAEN charging points open data": "Llicència oberta d'ús d'informació – Catalunya",
            "Navarra IDENA charging points open data": "CC BY 4.0",
            "Castilla y León charging points open data": "CC BY 4.0",
        }
        rows = ing.parse_catalonia_icaen(CATALONIA) + ing.parse_navarra_idena(NAVARRA) + ing.parse_castilla_leon(CASTILLA_LEON)
        for row in rows:
            source = row["sources"][0]
            self.assertEqual(source["license"], expected[source["name"]])
            self.assertTrue(source["attribution"])

    def test_navarra_reads_comma_decimal_power_and_hours(self) -> None:
        (row,) = ing.parse_navarra_idena(NAVARRA)
        self.assertEqual(row["max_power_kw"], 180.0)
        self.assertEqual(row["raw"]["opening_hours"], "24h")
        self.assertEqual(row["connector_labels"], ["CCS2"])
        self.assertAlmostEqual(row["lat"], 42.88353)

    def test_castilla_leon_reads_connector_list(self) -> None:
        (row,) = ing.parse_castilla_leon(CASTILLA_LEON)
        self.assertEqual(set(row["connector_labels"]), {"Type 2", "Schuko"})
        self.assertEqual(row["raw"]["operator"], "Ayuntamiento de Arévalo")


if __name__ == "__main__":
    unittest.main()
