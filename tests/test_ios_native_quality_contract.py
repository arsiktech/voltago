"""VG-23 source/resource contracts, independent of Xcode extraction.

The catalog is the shipped translation input. Compiled-resource lookup and Swift
interpolation are also exercised by OfflineWatchLocalizationTests on iOS.
"""

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
IOS = ROOT / "ios" / "Volta"
OFFLINE_KEYS = {
    "Offline. Showing chargers saved on this iPhone.",
    "Offline. Showing chargers saved on this iPhone on %@. Availability and prices may have changed.",
}
WATCH_KEYS = {
    "Notifications are off for Volta Go. Turn them on in Settings to get charger alerts.",
    "This build can't receive alerts yet. Push notifications are not set up.",
    "Couldn't start the watch. Try again.",
    "Couldn't reach Volta Go. Try again.",
    "A connector showed free. Check the current status before driving.",
    "The watch ended without a free connector.",
    "Watch stopped: live status unavailable.",
    "The watch has ended.",
    "Alert me when a connector is free",
    "Stop watching",
    "An alert never reserves a connector. It may be taken before you arrive.",
    "Watching this charger.",
    "Watching until %@.",
}


class IOSNativeQualityContractTests(unittest.TestCase):
    def test_offline_and_watch_copy_is_manual_and_translated_in_each_locale(self):
        catalog = json.loads(
            (IOS / "Volta" / "Localizable.xcstrings").read_text(encoding="utf-8")
        )
        self.assertEqual(catalog["sourceLanguage"], "en")
        strings = catalog["strings"]
        for key in sorted(OFFLINE_KEYS | WATCH_KEYS):
            with self.subTest(key=key):
                self.assertIn(key, strings)
                entry = strings[key]
                self.assertEqual(entry.get("extractionState"), "manual")
                for locale in ("en", "es", "ru"):
                    with self.subTest(key=key, locale=locale):
                        unit = entry["localizations"][locale]["stringUnit"]
                        self.assertEqual(unit["state"], "translated")
                        self.assertTrue(unit["value"].strip())
                        if locale == "en":
                            self.assertEqual(unit["value"], key)
                        else:
                            self.assertNotEqual(unit["value"], key)

    def test_interpolated_offline_date_and_watch_time_keep_one_string_argument(self):
        strings = json.loads(
            (IOS / "Volta" / "Localizable.xcstrings").read_text(encoding="utf-8")
        )["strings"]
        keys = {
            "Offline. Showing chargers saved on this iPhone on %@. Availability and prices may have changed.",
            "Watching until %@.",
        }
        for key in sorted(keys):
            for locale in ("en", "es", "ru"):
                with self.subTest(key=key, locale=locale):
                    self.assertIn(key, strings)
                    value = strings[key]["localizations"][locale]["stringUnit"]["value"]
                    # Exactly one object argument, optionally positional. Reject a
                    # dropped, duplicated, numeric or raw Swift interpolation.
                    placeholders = re.findall(r"%(?:\d+\$)?@", value)
                    self.assertEqual(len(placeholders), 1)
                    self.assertEqual(re.sub(r"%(?:1\$)?@", "", value).count("%"), 0)
                    self.assertNotIn("\\(", value)

    def test_header_symbols_scale_inside_their_reserved_frames(self):
        # Source guard complements the rendered XXXL inspection; it is not pixel proof.
        for filename, symbol, side in [
            ("AIConnectionView.swift", r'copySucceeded \? "checkmark" : "doc.on.doc"', 16),
            ("SheetView.swift", r'"gearshape"', 18),
        ]:
            with self.subTest(filename=filename):
                source = (IOS / "Volta" / filename).read_text(encoding="utf-8")
                self.assertRegex(source, rf'Image\(systemName: {symbol}\)\s*'
                    rf'\.resizable\(\)\s*\.scaledToFit\(\)\s*\.frame\(width: {side}, height: {side}\)')

    def test_all_four_header_cases_pin_location_before_launch(self):
        source = (IOS / "VoltaUITests" / "DiscoveryHeaderUITests.swift").read_text(
            encoding="utf-8"
        )
        launch = re.search(
            r"private func launch\(style: String, category: String\).*?\n    }",
            source,
            re.S,
        )
        if launch is None:
            self.fail("Discovery header launch helper is missing")
        before_launch = launch.group(0).split("app.launch()", maxsplit=1)[0]
        self.assertRegex(
            before_launch, r'"-VoltaUITestLocationState"\s*,\s*"denied"'
        )
        cases = re.findall(r"func (testPromptHeader\w+)\(\) \{(.*?)\n    }", source, re.S)
        self.assertEqual(
            {name for name, _ in cases},
            {
                "testPromptHeaderLightAccessibilityXXXL",
                "testPromptHeaderDarkAccessibilityXXXL",
                "testPromptHeaderLightDefault",
                "testPromptHeaderDarkDefault",
            },
        )
        for name, body in cases:
            self.assertIn("checkPromptHeader(style:", body, name)
        self.assertIn("let app = launch(style: style, category: category)", source)


if __name__ == "__main__":
    unittest.main()
