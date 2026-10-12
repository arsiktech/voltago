#!/usr/bin/env python3
"""Unit tests for scripts/qa_accessibility_spa.py's per-pair WCAG contrast math.

Ported 2026-07-12 (plans/009) from the retired static/ shell's contrast
contract: this suite proves, pair by pair, that the SPA's actual
frontend/src/index.css design tokens meet the same WCAG AA thresholds the
legacy scripts/qa_accessibility_static.py enforced against static/styles.css
(MIN_BODY_CONTRAST=4.5:1), in both the light and dark theme blocks.

Six light-theme pairs were KNOWN, FILED failures (D-149, Prime Directive
§2.6): the SPA's light-theme go/check/verify decision colors and the pressed
filter-chip text were never contrast-tested before this port and fell short
of 4.5:1 against the app's own light --bg/--surface. Per AGENTS.md §6 / plan
006's STOP-condition-C precedent, those six assertions were marked
@unittest.expectedFailure with the defect id rather than weakening the
threshold. Fixed 2026-07-12 (Iteration 176): frontend/src/index.css's
light-theme go/check/verify/brand tokens were darkened (hue/saturation
preserved) until all six reach >=4.5:1 -- see qa/defects.csv D-149 for the
before/after ratios. This flipped all six from "expected failure" to a real,
unconditional pass, exactly the "unexpected success forces closing the
defect" outcome the previous revision of this docstring anticipated;
KNOWN_FAILING is now empty so every generated pair test runs as a normal
assertion (a future regression fails loudly, not silently via a stale
expectedFailure).
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
import unittest
from typing import Dict, Optional, Tuple


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("qa_accessibility_spa", ROOT / "scripts" / "qa_accessibility_spa.py")
qa_accessibility_spa = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = qa_accessibility_spa
SPEC.loader.exec_module(qa_accessibility_spa)

RESULT = qa_accessibility_spa.evaluate()
PAIR_ROWS = {(row["theme"], row["pair"]): row for row in RESULT["pairs"]}

# (theme, pair label) -> defect id, for pairs still failing. Must match
# scripts/qa_accessibility_spa.py's WAIVED_PAIRS 1:1 (checked below). Empty
# since D-149 was fixed 2026-07-12 (Iteration 176) -- kept as a dict (not
# deleted) so a future real regression has somewhere to be filed the same
# honest way, per AGENTS.md §6.
KNOWN_FAILING: Dict[Tuple[str, str], str] = {}


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def _make_pair_test(theme: str, label: str, defect_id: Optional[str]):
    def test(self: unittest.TestCase) -> None:
        row = PAIR_ROWS.get((theme, label))
        self.assertIsNotNone(row, f"pair not evaluated: [{theme}] {label}")
        self.assertIsNotNone(
            row["ratio"], f"[{theme}] {label} is not a solid hex pair: {row['fg']} on {row['bg']}"
        )
        self.assertGreaterEqual(
            row["ratio"],
            row["minimum"],
            f"[{theme}] {label} measured {row['ratio']}:1, expected >= {row['minimum']}:1",
        )

    test.__name__ = f"test_{theme}_{_slug(label)}"
    return unittest.expectedFailure(test) if defect_id else test


class SpaContrastPairTests(unittest.TestCase):
    """One generated test method per (theme, pair) row -- see loop below."""


for _row in RESULT["pairs"]:
    _fn = _make_pair_test(_row["theme"], _row["pair"], KNOWN_FAILING.get((_row["theme"], _row["pair"])))
    setattr(SpaContrastPairTests, _fn.__name__, _fn)


class SpaAccessibilityGuardTests(unittest.TestCase):
    def test_index_css_was_found_and_parsed(self) -> None:
        self.assertTrue(RESULT["pairs"], "no pairs evaluated -- frontend/src/index.css missing or unparsable")

    def test_required_tokens_present_both_themes(self) -> None:
        token_failures = [f for f in RESULT["failures"] if "missing CSS color token" in f]
        self.assertEqual([], token_failures)

    def test_no_negative_letter_spacing(self) -> None:
        self.assertNotIn("index.css contains negative letter-spacing", RESULT["failures"])

    def test_focus_visible_rule_present(self) -> None:
        self.assertNotIn("index.css missing focus-visible styles", RESULT["failures"])

    def test_reduced_motion_media_query_present(self) -> None:
        self.assertNotIn("index.css missing reduced-motion media query", RESULT["failures"])

    def test_known_failing_pairs_match_the_script_waiver_exactly(self) -> None:
        # KNOWN_FAILING here and WAIVED_PAIRS in qa_accessibility_spa.py must name
        # the exact same pairs -- otherwise either a real regression could slip
        # through unwaived+ungoverned, or a waiver could exist with no test
        # actually pinning it as expectedFailure.
        script_waived_labels = set()
        for row in RESULT["pairs"]:
            if "waived_as" in row:
                script_waived_labels.add((row["theme"], row["pair"]))
        self.assertEqual(set(KNOWN_FAILING), script_waived_labels)

    def test_color_mix_and_alpha_gaps_stay_listed_not_silently_dropped(self) -> None:
        # The SPA's pill/badge color-mix tints and the rgba() focus ring are not
        # statically resolvable; this only checks the tracking list is never
        # silently emptied (a real browser getComputedStyle check is future work).
        self.assertEqual(4, len(RESULT["not_statically_checkable"]))
        for item in RESULT["not_statically_checkable"]:
            self.assertIn("reason", item)
            self.assertIn("not statically checkable", item["reason"])

    def test_overall_check_is_green_via_tracked_waivers_not_silence(self) -> None:
        # D-149's six pairs are fixed, not waived, as of 2026-07-12 (Iteration
        # 176) -- zero waivers now; the mechanism itself stays in place (see
        # KNOWN_FAILING/WAIVED_PAIRS) for a future real gap to use honestly.
        self.assertEqual([], RESULT["failures"])
        self.assertTrue(RESULT["ok"])
        self.assertEqual(0, len(RESULT["waived"]))


if __name__ == "__main__":
    unittest.main()
