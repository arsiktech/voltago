import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

import sys

sys.path.insert(0, str(SCRIPTS))

import qa_testflight_candidate as candidate  # noqa: E402


class TestFlightCandidateTests(unittest.TestCase):
    @staticmethod
    def fresh_asc_timestamp() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def test_checked_in_candidate_outranks_last_internal_assignment(self):
        state = candidate.load_state(ROOT / "docs/appstore/testflight-state.json")
        project = candidate.read_project_candidate(
            ROOT / "ios/Volta/Volta.xcodeproj/project.pbxproj"
        )

        self.assertEqual(state["last_known_internal_assignment"]["version"], "1.1")
        self.assertEqual(state["last_known_internal_assignment"]["build"], 202607101745)
        self.assertEqual(project.version, "1.2")
        self.assertGreater(project.build, state["last_known_internal_assignment"]["build"])

    def test_report_keeps_release_states_separate(self):
        report = candidate.build_report(
            root=ROOT,
            highest_asc_build=None,
            identity_output=(
                '1) ABC "Apple Development: Example (TEAM)"\n'
                "   1 valid identities found\n"
            ),
            dirty_paths=[],
            public_pages=candidate.PublicPages.unchecked(),
            asc_checked_at=None,
            archive=candidate.ArchiveEvidence.unchecked(),
        )

        self.assertTrue(report["states"]["repo_ready"])
        self.assertFalse(report["states"]["archive_ready"])
        self.assertFalse(report["states"]["upload_accepted"])
        self.assertFalse(report["states"]["testflight_ready"])
        self.assertFalse(report["states"]["friends_family_ready"])
        self.assertIn("highest App Store Connect build", " ".join(report["blockers"]))
        self.assertEqual(report["signing"]["local_distribution_identity"], False)
        self.assertIsNone(report["signing"]["organizer_cloud_signing_possible"])

    def test_app_store_connect_reconciliation_fails_closed_on_newer_build(self):
        project = candidate.read_project_candidate(
            ROOT / "ios/Volta/Volta.xcodeproj/project.pbxproj"
        )
        report = candidate.build_report(
            root=ROOT,
            highest_asc_build=project.build + 1,
            identity_output="0 valid identities found\n",
            dirty_paths=[],
            public_pages=candidate.PublicPages.unchecked(),
            asc_checked_at=self.fresh_asc_timestamp(),
            archive=candidate.ArchiveEvidence.unchecked(),
        )

        self.assertFalse(report["states"]["repo_ready"])
        self.assertFalse(report["states"]["archive_ready"])
        self.assertTrue(
            any("does not outrank" in blocker for blocker in report["blockers"])
        )

    def test_only_ios_release_changes_block_candidate_freeze(self):
        report = candidate.build_report(
            root=ROOT,
            highest_asc_build=202607101745,
            identity_output="0 valid identities found\n",
            dirty_paths=["data/free_intelligence.json"],
            public_pages=candidate.PublicPages.confirmed_for_tests(),
            asc_checked_at=self.fresh_asc_timestamp(),
            archive=candidate.ArchiveEvidence.confirmed_for_tests(),
        )
        self.assertTrue(report["states"]["repo_ready"])
        self.assertEqual(
            report["workspace"]["unrelated_dirty_paths"],
            ["data/free_intelligence.json"],
        )

        blocked = candidate.build_report(
            root=ROOT,
            highest_asc_build=202607101745,
            identity_output="0 valid identities found\n",
            dirty_paths=["ios/Volta/Volta/VoltaApp.swift"],
            public_pages=candidate.PublicPages.confirmed_for_tests(),
            asc_checked_at=self.fresh_asc_timestamp(),
            archive=candidate.ArchiveEvidence.confirmed_for_tests(),
        )
        self.assertFalse(blocked["states"]["repo_ready"])
        self.assertIn(
            "release-related uncommitted changes",
            " ".join(blocked["blockers"]),
        )

    def test_cli_writes_machine_readable_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "report.json"
            exit_code = candidate.main(
                [
                    "--root",
                    str(ROOT),
                    "--highest-asc-build",
                    "202607101745",
                    "--asc-checked-at",
                    self.fresh_asc_timestamp(),
                    "--identity-output",
                    "0 valid identities found",
                    "--dirty-path",
                    "data/free_intelligence.json",
                    "--skip-public-check",
                    "--out",
                    str(output),
                ]
            )

            self.assertEqual(exit_code, 1)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(payload["states"]["repo_ready"])
            self.assertFalse(payload["states"]["archive_ready"])
            self.assertFalse(payload["states"]["testflight_ready"])

    def test_archive_ready_requires_distinct_public_legal_pages(self):
        ready = candidate.build_report(
            root=ROOT,
            highest_asc_build=202607101745,
            identity_output="0 valid identities found\n",
            dirty_paths=[],
            public_pages=candidate.PublicPages.confirmed_for_tests(),
            asc_checked_at=self.fresh_asc_timestamp(),
            archive=candidate.ArchiveEvidence.confirmed_for_tests(),
        )
        self.assertTrue(ready["states"]["archive_ready"])
        self.assertTrue(ready["states"]["friends_family_inputs_ready"])

        shell = candidate.PublicPages(
            checked=True,
            ok=False,
            pages={"privacy": False, "terms": False, "support": False},
            failure="all three routes return the same SPA shell",
        )
        blocked = candidate.build_report(
            root=ROOT,
            highest_asc_build=202607101745,
            identity_output="0 valid identities found\n",
            dirty_paths=[],
            public_pages=shell,
            asc_checked_at=self.fresh_asc_timestamp(),
            archive=candidate.ArchiveEvidence.confirmed_for_tests(),
        )
        self.assertTrue(blocked["states"]["archive_ready"])
        self.assertFalse(blocked["states"]["friends_family_inputs_ready"])
        self.assertIn("public TestFlight-beta Privacy, Terms, and Support", " ".join(blocked["blockers"]))

    def test_archive_ready_requires_actual_signed_archive_evidence(self):
        report = candidate.build_report(
            root=ROOT,
            highest_asc_build=202607101745,
            asc_checked_at=self.fresh_asc_timestamp(),
            identity_output="0 valid identities found\n",
            dirty_paths=[],
            public_pages=candidate.PublicPages.unchecked(),
            archive=candidate.ArchiveEvidence.unchecked(),
        )
        self.assertTrue(report["states"]["archive_inputs_ready"])
        self.assertFalse(report["states"]["archive_ready"])
        self.assertIn("signed archive", " ".join(report["blockers"]))

    def test_app_store_connect_receipt_must_be_fresh(self):
        report = candidate.build_report(
            root=ROOT,
            highest_asc_build=202607101745,
            asc_checked_at="2000-01-01T00:00:00Z",
            identity_output="0 valid identities found\n",
            dirty_paths=[],
            public_pages=candidate.PublicPages.unchecked(),
            archive=candidate.ArchiveEvidence.unchecked(),
        )
        self.assertFalse(report["app_store_connect"]["reconciled"])
        self.assertFalse(report["states"]["archive_inputs_ready"])
        self.assertIn("fresh", " ".join(report["blockers"]))


if __name__ == "__main__":
    unittest.main()
