import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("qa_source_history", ROOT / "scripts" / "qa_source_history.py")
qa_source_history = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(qa_source_history)


class SourceHistoryTests(unittest.TestCase):
    def test_recovered_latest_run_passes_even_if_previous_failed(self):
        entries = [
            {
                "checked_at": "2026-06-22T18:11:40Z",
                "sampled": 4,
                "reachable": 3,
                "failed": 1,
                "by_provider": {
                    "Chargemap": {"sampled": 2, "reachable": 1, "failed": 1},
                    "Gaia": {"sampled": 2, "reachable": 2, "failed": 0},
                },
            },
            {
                "checked_at": "2026-06-22T18:14:53Z",
                "sampled": 4,
                "reachable": 4,
                "failed": 0,
                "by_provider": {
                    "Chargemap": {"sampled": 2, "reachable": 2, "failed": 0},
                    "Gaia": {"sampled": 2, "reachable": 2, "failed": 0},
                },
            },
        ]

        result = qa_source_history.analyze(entries, min_entries=2)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["latest_failed"], 0)
        self.assertEqual(result["regressions"], [])

    def test_latest_provider_failure_rate_regression_fails(self):
        entries = [
            {
                "checked_at": "2026-06-22T18:14:53Z",
                "sampled": 4,
                "reachable": 4,
                "failed": 0,
                "by_provider": {
                    "Gaia": {"sampled": 2, "reachable": 2, "failed": 0},
                    "Electromaps": {"sampled": 2, "reachable": 2, "failed": 0},
                },
            },
            {
                "checked_at": "2026-06-22T19:00:00Z",
                "sampled": 4,
                "reachable": 3,
                "failed": 1,
                "by_provider": {
                    "Gaia": {"sampled": 2, "reachable": 1, "failed": 1},
                    "Electromaps": {"sampled": 2, "reachable": 2, "failed": 0},
                },
            },
        ]

        result = qa_source_history.analyze(entries, min_entries=2, max_latest_failed=1, max_regression_delta=0.34)

        self.assertFalse(result["ok"])
        self.assertEqual(result["regressions"][0]["provider"], "Gaia")


if __name__ == "__main__":
    unittest.main()
