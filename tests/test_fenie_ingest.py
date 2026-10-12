"""Historical Feníe fixture/parser tests (direct collection retired 2026-08-23).

Uses a real excerpt of the public marker payload (verified 2026-07-18). Stdlib unittest.
No network or executable fetch adapter remains. These tests characterize old snapshots so
policy cleaning can identify them without weakening the canonical GET-only boundary.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ingest_sources as ing

MARKERS = [
    {
        "nombre_punto": "Central hidroeléctrica San Pedro de Araia SA",
        "direccion": "Santsaerreka kalea, 7, Araia, Álava", "id": 682, "code": "0459",
        "potencia": "0.0", "tarifa": 0,
        "conectores": [{"type": "Mennekes", "count": 2, "power": "18.0"},
                       {"type": "Schuko", "count": 2, "power": "2.3"}],
        "aparcamientos": 2, "gratuito": 1, "propiedad": 1, "estado": 1,
        "lat": 42.888151, "lon": -2.314698,
    },
    {  # out of service (estado 4) — kept, marked unavailable (owner decision 2026-10-11)
        "nombre_punto": "Parking Municipal", "direccion": "Calle Mayor, 1, Teruel, Teruel",
        "id": 700, "code": "0500", "tarifa": 0,
        "conectores": [{"type": "Mennekes", "count": 1, "power": "22.0"}],
        "gratuito": 1, "estado": 4, "lat": 40.34, "lon": -1.10,
    },
    {  # paid (gratuito 0) — must be dropped from a free-first DB
        "nombre_punto": "Estación de pago", "direccion": "Gran Vía, 5, Madrid, Madrid",
        "id": 701, "code": "0501", "tarifa": 0.35,
        "conectores": [{"type": "CCS", "count": 1, "power": "50.0"}],
        "gratuito": 0, "estado": 1, "lat": 40.42, "lon": -3.70,
    },
    {  # free but missing coordinates — dropped
        "nombre_punto": "Sin coordenadas", "direccion": "Nowhere", "id": 702, "code": "0502",
        "tarifa": 0, "conectores": [], "gratuito": 1, "estado": 1, "lat": None, "lon": None,
    },
    {  # internal test/QA unit — dropped even though free + in service + has coords
        "nombre_punto": "Fenie Prueba Postventa", "direccion": "Calle Y, 2, Madrid, Madrid",
        "id": 703, "code": "0503", "tarifa": 0,
        "conectores": [{"type": "Mennekes", "count": 1, "power": "22.0"}],
        "gratuito": 1, "estado": 1, "lat": 40.41, "lon": -3.70,
    },
]


class FenieAdapterTests(unittest.TestCase):
    def test_only_free_with_coords_survives_and_outage_is_marked(self) -> None:
        records = ing.fenie_records_from_markers(MARKERS)
        self.assertEqual([r["raw"]["fenie_code"] for r in records], ["0459", "0500"])
        record, down = records
        self.assertNotIn("source_reported_unavailable", record)
        # Out of service on Feníe's own map: listed with a stable ID, flagged for the server.
        self.assertEqual(down["source_reported_unavailable"]["value"], 4)
        self.assertIn("unavailable", down["source_reported_unavailable"]["summary"])
        self.assertEqual(down["raw"]["fenie_estado"], 4)
        self.assertEqual(record["price_min"], 0.0)
        self.assertTrue(record["is_free"])
        self.assertEqual(record["reliability"], "official-free")
        self.assertEqual(record["city"], "Araia")  # penultimate comma field, not "Spain"
        self.assertEqual(record["source_url"], "https://recarga.fenieenergia.es/punto-recarga/0459")
        labels = [c["label"] for c in record["connectors"]]
        self.assertIn("Type 2", labels)  # Mennekes normalized; Tesla-compatible
        self.assertIn("Schuko", labels)
        self.assertEqual(record["raw"]["fenie_gratuito"], 1)
        self.assertEqual(record["sources"][0]["attribution"], "Feníe Energía — Red de recarga (public map)")

    def test_population_shrink_guard(self) -> None:
        with self.assertRaises(RuntimeError):
            ing.parse_fenie_markers(MARKERS, minimum_markers=100)
        # A healthy-size list passes and keeps only dict markers.
        big = MARKERS * 30
        self.assertEqual(len(ing.parse_fenie_markers(big, minimum_markers=100)), len(big))

    def test_city_parser_handles_shapes(self) -> None:
        self.assertEqual(ing._fenie_city("Calle X, 3, Bilbao, Bizkaia"), "Bilbao")
        self.assertEqual(ing._fenie_city("Avenida, Sevilla"), "Sevilla")
        self.assertEqual(ing._fenie_city(""), "Spain")


if __name__ == "__main__":
    unittest.main()
