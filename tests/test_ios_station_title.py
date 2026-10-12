#!/usr/bin/env python3
"""Contract check that iOS titles follow the same conservative provider.ts rules.

Source-level wiring only: which Swift declarations exist and which views render
stationTitle. Behavioral coverage lives in frontend/src/lib/provider.test.ts
and StationTitleTests in ios/Volta/VoltaTests/PresentationHelperTests.swift.
A Python clone of those rules would not test the shipped Swift or TypeScript.
Does not run xcodebuild.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODELS = (ROOT / "ios/Volta/Volta/Models.swift").read_text()
ROOT_VIEW = (ROOT / "ios/Volta/Volta/RootView.swift").read_text()
STATION_VIEWS = (ROOT / "ios/Volta/Volta/StationViews.swift").read_text()
PROVIDER = (ROOT / "frontend/src/lib/provider.ts").read_text()


class IOSStationTitleContract(unittest.TestCase):
    def test_models_decode_optional_operator_and_owner(self):
        self.assertIn("var operatorName: String?", MODELS)
        self.assertIn('operatorName = "operator"', MODELS)
        self.assertIn("struct StationOwner", MODELS)
        self.assertIn("var owner: StationOwner?", MODELS)
        self.assertIn("var stationTitle: String", MODELS)
        self.assertIn("Tesla Supercharger", MODELS)
        self.assertIn("owner?.name", MODELS)
        self.assertNotIn("tesla_ok", MODELS.split("var stationTitle")[1][:2500])

    def test_web_rules_remain_source_reported(self):
        self.assertIn("Never infer an operator from compatibility", PROVIDER)
        self.assertIn("owner.name", PROVIDER)
        self.assertIn("Tesla Supercharger", PROVIDER)

    def test_list_and_detail_use_station_title(self):
        # A tapped pin opens the detail in the sheet; there is no separate map card.
        self.assertNotIn("struct MapPinCard", ROOT_VIEW)
        self.assertGreaterEqual(STATION_VIEWS.count("Text(station.stationTitle)"), 2)

if __name__ == "__main__":
    unittest.main()
