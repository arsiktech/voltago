#!/usr/bin/env python3
"""Cross-surface honesty-rule parity tests (plan 008, D-150/D-151).

Volta implements a handful of data-honesty rules across the backend, web,
and iOS surfaces. A 2026-07-12 audit found the copies had drifted
(D-150/D-151 below); plan 009 then deleted the legacy static/app.js shell.
The shared station-truth corpus now pins language-neutral outcomes, while
this older source-contract layer still guards the remaining literal rules
across exactly THREE surfaces:

  - station_truth.py + server.py (canonical Python truth / backend re-derivation)
  - frontend/src/lib/format.ts (the web SPA — CORRECT reference copy)
  - ios/Volta/Volta/Models.swift (the native iOS client)

Cases 1-3 read each file as TEXT and assert on the literal source (modeled on
tests/test_ios_design_contract.py, which reads Swift source as text from
Python). Case 4 imports server.py and scripts/ingest_sources.py directly and
compares live function output token-for-token.

This file is the drift alarm: when a rule legitimately changes, change ALL
surfaces + this test in the same commit. A parity-test edit without matching
edits on every surface it pins is a red flag for review.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import server  # noqa: E402
import ingest_sources as ing  # noqa: E402

FORMAT_TS = ROOT / "frontend" / "src" / "lib" / "format.ts"
MODELS_SWIFT = ROOT / "ios" / "Volta" / "Volta" / "Models.swift"


def _slice_between(text: str, start_marker: str, end_marker: str) -> str:
    """Return the text from start_marker up to (not including) end_marker.

    A simple, robust stand-in for full function-boundary parsing: both
    markers are unique anchors (a def/function/var name) that only appear
    once before the code region we care about, so this reliably isolates one
    function's source even though server.py has an unrelated, pre-existing,
    out-of-scope second Electromaps-URL regex elsewhere in the file (see the
    module docstring note in server.py's classify_source_resource — NOT
    covered by this parity test; flagged separately, not part of the
    hasExactDirectoryLocation-family rule this test pins).
    """
    start = text.index(start_marker)
    end = text.index(end_marker, start + len(start_marker))
    return text[start:end]


# Matches the Electromaps exact-station-page language alternation regardless
# of each surface's own escaping/regex-flavor style: server.py/format.ts use
# a non-capturing `(?:en|es|fr|pt)`, Models.swift uses a plain capturing
# `(en|es|fr|pt)` (harmless stylistic difference — same match behavior,
# nobody reads the capture group) and format.ts additionally backslash-
# escapes its `/` delimiters as a JS regex literal. All three forms have
# "com" + optional backslash + "/" + "(" + optional "?:" + languages + ")"
# + optional backslash + "/p" + optional backslash + "/".
LANGUAGE_SET_RE = re.compile(r"com\\?/\((?:\?:)?([a-z|]+)\)\\?/p\\?/")


def _quoted_tokens(inner: str) -> set:
    return set(re.findall(r'["\']([a-zA-Z_]+)["\']', inner))


class ElectromapsExactPageLanguageParityTests(unittest.TestCase):
    """Case 1: the Electromaps exact-station-page URL rule accepts the same
    language set (en/es/fr/pt) on every surface (plans/008 drift #1:
    Electromaps regex, was legacy-only/void — it drifted only in the
    now-deleted static/app.js, which is gone with plan 009, so only these
    three remain; no D-row for this one, unlike drift #2 below)."""

    EXPECTED_LANGUAGES = {"en", "es", "fr", "pt"}

    @classmethod
    def setUpClass(cls) -> None:
        cls.server_slice = _slice_between(
            Path(server.__file__).read_text(encoding="utf-8"),
            "def source_health_has_exact_directory_location",
            "def source_health_map_eligible",
        )
        cls.format_slice = _slice_between(
            FORMAT_TS.read_text(encoding="utf-8"),
            "function hasExactDirectoryLocation",
            "export function isMapLocationVerified",
        )
        cls.swift_slice = _slice_between(
            MODELS_SWIFT.read_text(encoding="utf-8"),
            "var hasExactDirectoryLocation",
            "var isMapLocationVerified",
        )

    def _languages(self, slice_text: str, label: str) -> set:
        m = LANGUAGE_SET_RE.search(slice_text)
        self.assertIsNotNone(m, f"{label}: could not find the Electromaps exact-page language regex")
        return set(m.group(1).split("|"))

    def test_language_set_identical_across_server_web_ios(self) -> None:
        server_langs = self._languages(self.server_slice, "server.py")
        format_langs = self._languages(self.format_slice, "format.ts")
        swift_langs = self._languages(self.swift_slice, "Models.swift")

        self.assertEqual(server_langs, self.EXPECTED_LANGUAGES, "server.py accepts an unexpected language set")
        self.assertEqual(format_langs, self.EXPECTED_LANGUAGES, "format.ts accepts an unexpected language set")
        self.assertEqual(swift_langs, self.EXPECTED_LANGUAGES, "Models.swift accepts an unexpected language set (all 3 live surfaces should share en|es|fr|pt; the historical fr/pt drift was static/app.js-only, now retired by plan 009)")


class ExactDirectoryKindListParityTests(unittest.TestCase):
    """Case 2: the exact-directory `kind` allowlist is set-equal across all
    three surfaces, including `provider_station_page` (plans/008 drift #2 =
    D-150, iOS kind list — this case is the drift-#2 regression proof: it
    FAILS if Models.swift's array is ever missing an entry again)."""

    EXPECTED_KINDS = {
        "station_page",
        "start_page",
        "provider_station_page",
        "map_object",
        "live_map",
        "official_data",
    }

    @classmethod
    def setUpClass(cls) -> None:
        cls.server_slice = _slice_between(
            Path(server.__file__).read_text(encoding="utf-8"),
            "def source_health_has_exact_directory_location",
            "def source_health_map_eligible",
        )
        cls.format_slice = _slice_between(
            FORMAT_TS.read_text(encoding="utf-8"),
            "function hasExactDirectoryLocation",
            "export function isMapLocationVerified",
        )
        cls.swift_slice = _slice_between(
            MODELS_SWIFT.read_text(encoding="utf-8"),
            "var hasExactDirectoryLocation",
            "var isMapLocationVerified",
        )

    def test_kind_list_set_equal_across_server_web_ios(self) -> None:
        server_m = re.search(r"kind in \{([^}]+)\}", self.server_slice)
        format_m = re.search(r"\[([^\]]+)\]\.includes\(kind\)", self.format_slice)
        swift_m = re.search(r"\[([^\]]+)\]\.contains\(kind\)", self.swift_slice)

        self.assertIsNotNone(server_m, "server.py: could not find the exact-directory kind set")
        self.assertIsNotNone(format_m, "format.ts: could not find the exact-directory kind list")
        self.assertIsNotNone(swift_m, "Models.swift: could not find the exact-directory kind list")

        server_kinds = _quoted_tokens(server_m.group(1))
        format_kinds = _quoted_tokens(format_m.group(1))
        swift_kinds = _quoted_tokens(swift_m.group(1))

        self.assertEqual(server_kinds, self.EXPECTED_KINDS, "server.py kind list drifted")
        self.assertEqual(format_kinds, self.EXPECTED_KINDS, "format.ts kind list drifted")
        self.assertEqual(
            swift_kinds,
            self.EXPECTED_KINDS,
            "Models.swift kind list drifted (D-150: was missing provider_station_page)",
        )


class DecisionFallbackLeadAwareParityTests(unittest.TestCase):
    """Case 3: an unknown/missing decision level falls back honestly by lead
    status: 'check' for a non-lead (live) row, 'verify' for a lead. This was
    drifted lead-blind (always 'verify') only in the now-deleted
    static/app.js (plan 009 removed it); format.ts and Models.swift already
    hold this correctly.

    server.py has no equivalent client-style "read a possibly-missing field
    with a fallback" — it is the origin of `decision.level`, always computing
    and attaching one via decision_for() before anything downstream reads it
    (see server.py:988). So instead of text-scraping a fallback that doesn't
    exist there, this pins the real structural analogue: decision_for() with
    live=False (the lead path) never returns the ambiguous "check" level —
    only "go"/"verify"/"lead"/"avoid" — while live=True (non-lead) rows CAN
    reach "check". That is the same "leads never get the softer, ambiguous
    tier" invariant the client fallback expresses, verified by calling the
    real function rather than asserting a nonexistent pattern.
    """

    def test_format_ts_decision_level_fallback_is_lead_aware(self) -> None:
        text = FORMAT_TS.read_text(encoding="utf-8")
        func = _slice_between(text, "export function decisionLevel", "export type SourceTier")
        self.assertNotIn(
            "st.decision?.level || 'verify'",
            func,
            "format.ts: decisionLevel regressed to the lead-blind fallback",
        )
        self.assertIn("if (!st.lead) return 'check'", func)
        self.assertIn("return 'verify'", func)

    def test_models_swift_decision_kind_fallback_is_lead_aware(self) -> None:
        text = MODELS_SWIFT.read_text(encoding="utf-8")
        func = _slice_between(text, "var decisionKind: DecisionKind", "var decisionLabel")
        self.assertIn("isLead ? .verify : .check", func)

    def test_server_decision_for_never_returns_check_for_a_lead(self) -> None:
        # A deliberately varied set of lead-path (live=False) inputs, chosen
        # to exercise different branches of decision_for (default/unscored,
        # cheap-but-not-live, access-restricted, not-tesla-ok, official
        # locator reliability). None of decision_for's live=False-reachable
        # branches ever return "check" — only live=True branches do.
        lead_inputs = [
            {},
            {"tesla_ok": True, "is_free": True},
            {"tesla_ok": True, "access": {"level": "restricted", "note": "members only"}},
            {"tesla_ok": False},
            {"tesla_ok": True, "reliability": "official-charger-location-price-unknown"},
        ]
        for item in lead_inputs:
            level = server.decision_for(dict(item), live=False)["level"]
            self.assertNotEqual(
                level,
                "check",
                f"decision_for(live=False) returned 'check' for {item!r} — leads must get verify/lead/avoid, never the ambiguous check tier",
            )

        # Sanity: "check" IS reachable for a non-lead (live=True) row, so the
        # assertion above is a real lead-vs-live split, not a level that's
        # simply unreachable everywhere. Cheap/free + Tesla-ok but no
        # confirmed available bay -> "check" (no `available` at all, unlike
        # the fully-qualified case above which reaches "go").
        live_item = {"tesla_ok": True, "is_free": True}
        live_level = server.decision_for(dict(live_item), live=True)["level"]
        self.assertEqual(live_level, "check", "sanity check: live=True should still be able to reach 'check'")


class TeslaCompatibilityTests(unittest.TestCase):
    """A source that names no connector type is "unknown", never "Not Tesla-safe".

    Regression: Lidl's official store list publishes no per-store connectors, so
    every Lidl pin was labelled "Not Tesla-safe". server.tesla_compatibility and
    ingest_sources.tesla_compat must agree on the three outcomes.
    """

    CASES = [
        ([], None),
        (["Wall outlet (connector unspecified)"], None),
        (["Unknown connector"], None),
        (["Unknown Connector", "other", "N/A"], None),
        (["Unknown connector", "CHAdeMO"], False),
        (["Type 2"], True),
        (["CCS2", "CHAdeMO"], True),
        (["Tesla"], True),
        (["CHAdeMO"], False),
        (["CEE 7/4 - Schuko - Type F"], False),
    ]

    def test_server_and_ingest_agree(self) -> None:
        for labels, expected in self.CASES:
            self.assertIs(server.tesla_compatibility({"tesla_ok": False, "connector_labels": labels}), expected, labels)
            self.assertIs(ing.tesla_compat(labels), expected, labels)

    def test_unknown_connectors_are_not_labelled_not_tesla_safe(self) -> None:
        lead = {
            "id": "lidl-like",
            "name": "Lidl store",
            "lat": 28.38,
            "lon": -16.69,
            "is_free": True,
            "is_cheap": True,
            "price_min": 0.0,
            "tesla_ok": False,
            "connector_labels": [],
            "connectors": [],
            "status": "VERIFY",
            "source": "Lidl official free list",
            "reliability": "official-free-policy",
        }
        row = server._normalize_lead(dict(lead), lead["source"])
        self.assertIsNone(row["tesla_ok"])
        self.assertNotEqual(row["decision"]["label"], "Not Tesla-safe")
        self.assertIn("doesn't say which connectors", row["decision"]["reason"])

    def test_operator_unknown_socket_rows_keep_compatibility_unknown(self) -> None:
        parsed = ing.parse_etecnic_chargers([{
            "id": 42, "name": "Unknown connector fixture", "show_map": True,
            "lat": 41.132473, "lon": 1.252453,
            "charger_sockets": [{"id": 1, "connector_type_id": 7, "status": 0}],
        }])[0]
        merged, _ = ing.merge_cross_source_duplicates(ing.dedupe([parsed]))
        row = server._normalize_lead(merged[0], parsed["source"])
        self.assertIsNone(row["tesla_ok"])
        self.assertNotEqual(row["decision"]["label"], "Not Tesla-safe")
        self.assertFalse(row["verification"]["live_verified"])
        self.assertEqual(row["total_evse"], 1)
        self.assertTrue(server.source_health_map_eligible(row))

    def test_known_incompatible_connectors_still_avoid(self) -> None:
        row = server._normalize_lead(
            {"id": "schuko", "name": "Schuko only", "lat": 40.0, "lon": -3.0, "is_free": True,
             "tesla_ok": False, "connector_labels": ["Schuko"], "source": "OpenStreetMap public tags"},
            "OpenStreetMap public tags",
        )
        self.assertIs(row["tesla_ok"], False)
        self.assertEqual(row["decision"]["label"], "Not Tesla-safe")


class ConnectorLabelNormalizerParityTests(unittest.TestCase):
    """Case 4: server.normalize_connector_label and
    ingest_sources.connector_label_from_token are two independent copies of
    the same alias table (ingest_sources.py does not import server.py — see
    AGENTS.md's zero-cross-import convention between the live server and the
    offline ingestion script). They must agree token-for-token, including
    both returning None for empty/missing input and neither ever defaulting
    an unrecognized token to a Tesla-ok label (D-151: Electromaps unknown
    tokens used to silently become "Type 2")."""

    TOKENS = [
        "IEC_62196_T2_COMBO",
        "CCS",
        "CHADEMO",
        "TESLA",
        "MENNEKES",
        "type 2",
        "tipo 2",
        "SCHUKO",
        "DOMESTIC",
        "SCEE",
        "T1",
        "UNKNOWN_XYZ",
        "",
        None,
    ]

    TESLA_OK_LABELS = {"CCS2", "Type 2", "Tesla"}

    def test_normalize_connector_label_matches_ingest_sources_token_for_token(self) -> None:
        for token in self.TOKENS:
            with self.subTest(token=token):
                self.assertEqual(
                    server.normalize_connector_label(token),
                    ing.connector_label_from_token(token),
                    f"server.normalize_connector_label and ingest_sources.connector_label_from_token disagree on {token!r}",
                )

    def test_unrecognized_token_never_defaults_into_tesla_ok_label(self) -> None:
        # The core D-151 regression guard: a token that matches none of the
        # known aliases must never silently become a Tesla-compatible label.
        for token in ("UNKNOWN_XYZ", "totally-unrecognized-plug-9000"):
            with self.subTest(token=token):
                label = server.normalize_connector_label(token)
                self.assertNotIn(label, self.TESLA_OK_LABELS)
                label2 = ing.connector_label_from_token(token)
                self.assertNotIn(label2, self.TESLA_OK_LABELS)


if __name__ == "__main__":
    unittest.main()
