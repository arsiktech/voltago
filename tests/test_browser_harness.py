"""Behavioral contract for the shared browser console-noise classifier."""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BrowserHarnessClassifierTests(unittest.TestCase):
    def test_only_basemap_failures_with_maplibre_provenance_are_waived(self) -> None:
        cases = [
            {"line": "info: ready", "expected": False},
            {
                "line": "error: Failed to load resource: net::ERR_FAILED https://tiles.openfreemap.org/planet/1/2/3.pbf",
                "expected": True,
            },
            {
                "line": "error: AJAXError: Failed to fetch (0): https://tiles.openfreemap.org/planet/1/2/3.pbf [/assets/maplibre-a1.js:1]",
                "expected": True,
            },
            {
                "line": "error: TypeError: Failed to fetch https://tiles.openfreemap.org/planet/1/2/3.pbf [/assets/maplibre-a1.js:1]",
                "expected": True,
            },
            {
                "line": "error: TypeError: Failed to fetch [/assets/maplibre-a1.js:1]",
                "expected": False,
            },
            {
                "line": "error: AJAXError: Failed to fetch (0): https://tiles.openfreemap.org/planet/1/2/3.pbf",
                "expected": False,
            },
            {
                "line": "error: TypeError: Failed to fetch https://voltago.app/api/chargers [/assets/maplibre-a1.js:1]",
                "expected": False,
            },
            {
                "line": "pageerror: TypeError: Failed to fetch https://tiles.openfreemap.org/planet/1/2/3.pbf [/assets/maplibre-a1.js:1]",
                "expected": False,
            },
        ]
        script = """
const { isExpectedBasemapNoise } = require('./scripts/qa_browser_harness.js');
const cases = JSON.parse(process.argv[1]);
process.stdout.write(JSON.stringify(cases.map((item) => isExpectedBasemapNoise(item.line))));
"""
        result = subprocess.run(
            ["node", "-e", script, json.dumps(cases)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([case["expected"] for case in cases], json.loads(result.stdout))

    def test_generic_maplibre_fetch_remains_fatal_even_with_basemap_peer(self) -> None:
        batches = [
            {
                "logs": [
                    "error: TypeError: Failed to fetch [http://127.0.0.1:8773/assets/maplibre-a1.js]",
                ],
                "expected": [
                    "error: TypeError: Failed to fetch [http://127.0.0.1:8773/assets/maplibre-a1.js]",
                ],
            },
            {
                "logs": [
                    "basemap-requestfailed: https://tiles.openfreemap.org/planet/1/2/3.pbf",
                    "error: TypeError: Failed to fetch [http://127.0.0.1:8773/assets/maplibre-a1.js]",
                ],
                "expected": [
                    "error: TypeError: Failed to fetch [http://127.0.0.1:8773/assets/maplibre-a1.js]",
                ],
            },
            {
                "logs": [
                    "error: TypeError: Failed to fetch [http://127.0.0.1:8773/assets/maplibre-a1.js]",
                    "error: AJAXError: Failed to fetch (0): https://tiles.openfreemap.org/planet/1/2/3.pbf [/assets/maplibre-a1.js:1]",
                ],
                "expected": [
                    "error: TypeError: Failed to fetch [http://127.0.0.1:8773/assets/maplibre-a1.js]",
                ],
            },
            {
                "logs": [
                    "error: TypeError: Failed to fetch https://voltago.app/api/chargers [/assets/maplibre-a1.js:1]",
                    "error: AJAXError: Failed to fetch (0): https://tiles.openfreemap.org/planet/1/2/3.pbf [/assets/maplibre-a1.js:1]",
                ],
                "expected": [
                    "error: TypeError: Failed to fetch https://voltago.app/api/chargers [/assets/maplibre-a1.js:1]",
                ],
            },
            {
                "logs": [
                    "pageerror: TypeError: Failed to fetch [http://127.0.0.1:8773/assets/maplibre-a1.js]",
                    "error: Failed to load resource: net::ERR_FAILED https://tiles.openfreemap.org/planet/1/2/3.pbf",
                ],
                "expected": [
                    "pageerror: TypeError: Failed to fetch [http://127.0.0.1:8773/assets/maplibre-a1.js]",
                ],
            },
        ]
        script = """
const { unexpectedBrowserErrors } = require('./scripts/qa_browser_harness.js');
const batches = JSON.parse(process.argv[1]);
process.stdout.write(JSON.stringify(batches.map((item) => unexpectedBrowserErrors(item.logs))));
"""
        result = subprocess.run(
            ["node", "-e", script, json.dumps(batches)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([batch["expected"] for batch in batches], json.loads(result.stdout))


class ChromiumReadPixelsDiagnosticTests(unittest.TestCase):
    NATIVE = (
        "warning: [.WebGL-0x390c0161de00]GL Driver Message "
        "(OpenGL, Performance, GL_CLOSE_PATH_NV, High): GPU stall due to ReadPixels"
    )
    REPEAT_SUFFIX = " (this message will no longer repeat)"

    def run_node(self, script: str, data: object) -> object:
        result = subprocess.run(
            ["node", "-e", script, json.dumps(data)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout)

    def classify(self, lines: list[object]) -> list[bool]:
        return self.run_node("""
const { isChromiumReadPixelsDiagnostic } = require('./scripts/qa_browser_harness.js');
const lines = JSON.parse(process.argv[1]);
process.stdout.write(JSON.stringify(lines.map(isChromiumReadPixelsDiagnostic)));
""", lines)

    def test_exact_native_diagnostic_and_controlled_scenarios_are_recognized(self) -> None:
        lines = [self.NATIVE, self.NATIVE + self.REPEAT_SUFFIX]
        for scenario in ["supported", "pending-theme", "no-webgl2"]:
            tagged = self.NATIVE.replace("warning: ", f"warning: [{scenario}] ", 1)
            lines.extend([tagged, tagged + self.REPEAT_SUFFIX])
        lines.append(self.NATIVE.replace("0x390c0161de00", "0x12c001d6200"))
        self.assertEqual([True] * len(lines), self.classify(lines))

    def test_near_matches_and_app_messages_remain_unclassified(self) -> None:
        lines = [
            "warning: GPU stall due to ReadPixels",  # no native context or provenance shape
            "warning: [supported] app: " + self.NATIVE.removeprefix("warning: "),
            "warning: [app] " + self.NATIVE.removeprefix("warning: "),
            self.NATIVE.replace("warning:", "error:", 1),
            self.NATIVE.replace("warning:", "pageerror:", 1),
            self.NATIVE.replace("warning:", "info:", 1),
            self.NATIVE.replace("GPU stall due to ReadPixels", "GPU context lost"),
            self.NATIVE.replace("ReadPixels", "ReadBuffer"),
            self.NATIVE.replace("GL_CLOSE_PATH_NV", "GL_INVALID_OPERATION"),
            self.NATIVE.replace("Performance", "Error"),
            self.NATIVE.replace("High", "Low"),
            self.NATIVE.replace("OpenGL,", "Vulkan,", 1),
            self.NATIVE.replace("0x390c0161de00", "0xnothex"),
            self.NATIVE.replace("0x390c0161de00", "0x"),
            self.NATIVE.replace("0x390c0161de00", "0x12345678901234567"),
            self.NATIVE.replace("warning: ", "warning: [supported] [pending-theme] ", 1),
            "warning: layers[highway-shield-non-us].filter[1]: Expected value to be of type number, but found null instead. Falling back to false.",
            self.NATIVE + " app failed",
            self.NATIVE + self.REPEAT_SUFFIX + " app failed",
            self.NATIVE + " ",
            " " + self.NATIVE,
            self.NATIVE + "\n",
            self.NATIVE + "\r",
            self.NATIVE + "\r\n",
            self.NATIVE + "\u2028",
            self.NATIVE + "\nerror: app failed",
            self.NATIVE.replace("GL Driver Message", "GL Driver\nMessage"),
            None,
            0,
            {"line": self.NATIVE},
        ]
        actual = self.classify(lines)
        for line, accepted in zip(lines, actual, strict=True):
            with self.subTest(line=line):
                self.assertFalse(accepted)

    def test_shared_error_policy_does_not_waive_native_diagnostics(self) -> None:
        lines = [self.NATIVE, self.NATIVE + self.REPEAT_SUFFIX, "warning: app failed", "error: app failed"]
        actual = self.run_node("""
const { unexpectedBrowserErrors } = require('./scripts/qa_browser_harness.js');
const lines = JSON.parse(process.argv[1]);
process.stdout.write(JSON.stringify({
  strict: unexpectedBrowserErrors(lines, { includeWarnings: true }),
  defaults: unexpectedBrowserErrors(lines),
}));
""", lines)
        self.assertEqual(lines, actual["strict"])
        self.assertEqual(["error: app failed"], actual["defaults"])

    def test_explicit_partition_keeps_mixed_warning_and_error_batches_fatal(self) -> None:
        fatal = [
            "warning: [supported] Application result state is inconsistent",
            self.NATIVE.replace("ReadPixels", "ReadBuffer"),
            self.NATIVE.replace("warning:", "error:", 1),
            "warning: [supported] layers[road_shield_us].filter[1]: Expected value to be of type number, but found null instead. Falling back to false.",
            self.NATIVE + "\nerror: app failed",
            "pageerror: App crashed",
        ]
        batches = [[self.NATIVE, *fatal, self.NATIVE + self.REPEAT_SUFFIX], [*fatal, self.NATIVE]]
        actual = self.run_node("""
const { isChromiumReadPixelsDiagnostic, unexpectedBrowserErrors } = require('./scripts/qa_browser_harness.js');
const batches = JSON.parse(process.argv[1]);
process.stdout.write(JSON.stringify(batches.map(logs => ({
  preserved: logs.filter(isChromiumReadPixelsDiagnostic),
  fatal: unexpectedBrowserErrors(logs.filter(line => !isChromiumReadPixelsDiagnostic(line)), { includeWarnings: true }),
}))));
""", batches)
        for index, batch in enumerate(actual):
            with self.subTest(batch=index):
                self.assertEqual(fatal, batch["fatal"])
                self.assertEqual([line for line in batches[index] if line not in fatal], batch["preserved"])


if __name__ == "__main__":
    unittest.main()
