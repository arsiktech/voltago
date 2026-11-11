"""Validate the tracked QA ledgers offline, with no generated receipt or server.

tests/test_artifact_integrity.py runs the full integrity report only when the
locally generated regression receipt exists, so on a clean clone (CI) the
ledgers would otherwise go unchecked.
"""
import importlib.util
import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
LEDGERS = ("features.csv", "test-cases.csv", "defects.csv")
SPEC = importlib.util.spec_from_file_location("qa_artifact_integrity_ledgers", ROOT / "scripts" / "qa_artifact_integrity.py")
qa_artifact_integrity = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(qa_artifact_integrity)


class TrackedQaLedgerTests(unittest.TestCase):
    def test_tracked_ledgers_pass_the_offline_validators(self):
        qa = ROOT / "qa"
        failures = []
        features = qa_artifact_integrity.read_csv(qa / "features.csv", qa_artifact_integrity.FEATURE_FIELDS, failures)
        tests = qa_artifact_integrity.read_csv(qa / "test-cases.csv", qa_artifact_integrity.TEST_FIELDS, failures)
        defects = qa_artifact_integrity.read_csv(qa / "defects.csv", qa_artifact_integrity.DEFECT_FIELDS, failures)
        self.assertEqual(failures, [])
        self.assertTrue(features and tests and defects)

        feature_ids = {row["Feature ID"] for row in features}
        test_ids = {row["Test Case ID"] for row in tests}
        qa_artifact_integrity.validate_features(features, failures)
        qa_artifact_integrity.validate_tests(tests, feature_ids, failures)
        qa_artifact_integrity.validate_defects(defects, feature_ids, test_ids, failures)
        qa_artifact_integrity.validate_feature_defect_counts(features, defects, failures)

        self.assertEqual(failures, [])

    def test_ledgers_cite_no_private_origin_or_tool_path(self):
        private_origin = re.compile(r"https?://(?:192\.168|10|172\.(?:1[6-9]|2[0-9]|3[01]))\.[0-9]+\.[0-9]+")
        hits = []
        for name in LEDGERS:
            text = (ROOT / "qa" / name).read_text(encoding="utf-8")
            hits += [f"{name}: private LAN origin" for _ in private_origin.finditer(text)]
            hits += [f"{name}: private tool runtime path" for _ in re.finditer(r"codex-runtimes", text)]
        self.assertEqual(hits, [])

    def test_cited_storekit_configurations_exist(self):
        cited = set()
        for name in LEDGERS:
            cited.update(re.findall(r"ios/[\w./-]+\.storekit", (ROOT / "qa" / name).read_text(encoding="utf-8")))
        self.assertTrue(cited)
        self.assertEqual([path for path in sorted(cited) if not (ROOT / path).is_file()], [])


if __name__ == "__main__":
    unittest.main()
