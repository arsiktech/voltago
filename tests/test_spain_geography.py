"""Country-boundary regressions for anonymous maps without country fields."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import ingest_sources as ing


class SpainBoundaryTests(unittest.TestCase):
    def test_spanish_border_towns_islands_and_autonomous_cities(self):
        cases = {'Verin': (41.9406, -7.4388), 'Trabazos': (41.746, -6.493),
                 'Urdax': (43.276, -1.504), 'Aldeadavila': (41.218, -6.619),
                 'Madrid': (40.4168, -3.7038), 'Puerto de la Cruz': (28.4134, -16.5489),
                 'Palma': (39.5696, 2.6502), 'Ceuta': (35.8894, -5.3213),
                 'Melilla': (35.2923, -2.9381), 'Llivia': (42.4636, 1.9806)}
        for town, coords in cases.items():
            with self.subTest(town=town): self.assertTrue(ing.likely_in_spain(*coords))

    def test_neighboring_border_towns_are_not_spain(self):
        cases = {'Valenca': (42.026, -8.645), 'Vila Real de Santo Antonio': (37.195, -7.417),
                 'Vilar Formoso': (40.612, -6.833), 'Hendaye': (43.359, -1.775),
                 'Cerbere': (42.443, 3.165), 'Gibraltar': (36.141, -5.353),
                 'Lisbon': (38.7223, -9.1393), 'Andorra la Vella': (42.5063, 1.5218)}
        for town, coords in cases.items():
            with self.subTest(town=town): self.assertFalse(ing.likely_in_spain(*coords))


if __name__ == '__main__': unittest.main()
