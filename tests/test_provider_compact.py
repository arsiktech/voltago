import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: E402
from tests import _support  # noqa: E402


def setUpModule():
    _support.isolate_community_store()


class CompactProviderTests(unittest.TestCase):
    def test_compact_promotes_unambiguous_operator_and_removes_raw_bulk(self) -> None:
        row = {
            'id': 'dgt:42',
            'name': 'Station',
            'raw': {'operator': 'Iberdrola', 'unrelated': 'bulk'},
            # Captured API observation fixture; no invented live assertion.
            'owner': {'name': 'ENDESA X WAY, S.L.', 'website': 'www.endesaxway.com', 'phone': '800760255', 'logo': None},
        }
        result = server.compact_station_for_ui(row)
        self.assertEqual(result.get('operator'), 'Iberdrola', result)
        self.assertNotIn('raw', result, result)
        self.assertEqual(result.get('owner'), row['owner'], result)
        self.assertEqual(row['raw']['unrelated'], 'bulk')

    def test_conflicting_operator_names_fail_closed(self) -> None:
        # Conflicting source-reported names are not safe branding. The compact surface must
        # fail closed instead of retaining either source's operator text.
        conflict = server.compact_station_for_ui({
            'id': 'dgt:conflict',
            'name': 'Conflict station',
            'operator': 'Iberdrola',
            'raw': {'operator': 'Endesa X Way'},
        })
        self.assertNotIn('operator', conflict, conflict)
        self.assertNotIn('raw', conflict, conflict)


if __name__ == '__main__':
    unittest.main()
