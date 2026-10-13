"""Worldwide connector evidence must not silently change the physical standard."""
import unittest

from station_truth import normalize_connector_label


class GlobalConnectorTests(unittest.TestCase):
    def test_ccs_type_one_and_two_remain_distinct(self):
        for label in ("CCS1", "CCS (Type 1)", "IEC_62196_T1_COMBO", "Combo J1772"):
            with self.subTest(label=label):
                self.assertEqual(normalize_connector_label(label), "CCS1")
        for label in ("CCS2", "CCS (Type 2)", "IEC_62196_T2_COMBO"):
            with self.subTest(label=label):
                self.assertEqual(normalize_connector_label(label), "CCS2")

    def test_north_american_and_chinese_standards(self):
        cases = {
            "SAE J3400": "NACS", "Tesla (North America)": "NACS",
            "NACS": "NACS", "Type 1 (J1772)": "Type 1",
            "IEC_62196_T1": "Type 1", "GB/T 20234.2": "GB/T AC",
            "GB_T_DC": "GB/T DC", "GB/T 20234.3": "GB/T DC",
            "GB/T": "GB/T",
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertEqual(normalize_connector_label(label), expected)

    def test_unknown_standard_stays_unknown(self):
        self.assertEqual(normalize_connector_label("CCS (unspecified)"), "CCS (unspecified)")
        self.assertEqual(normalize_connector_label("NEMA 14-50"), "Nema 14-50")
        self.assertIsNone(normalize_connector_label(None))


if __name__ == "__main__":
    unittest.main()
