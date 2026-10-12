import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("qa_source_links", ROOT / "scripts" / "qa_source_links.py")
qa_source_links = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(qa_source_links)


class SourceLinkSamplerTests(unittest.TestCase):
    def test_collect_candidates_respects_host_and_provider_caps(self):
        rows = [
            {
                "name": "Gaia A",
                "sources": [
                    {"name": "Gaia", "url": "https://partner-next.gaiacharge.com/cpo/jump/a"},
                    {"name": "Gaia", "url": "https://partner-next.gaiacharge.com/cpo/jump/b"},
                    {"name": "Electromaps", "url": "https://map.electromaps.com/en/p/1"},
                ],
            },
            {
                "name": "Gaia B",
                "sources": [
                    {"name": "Gaia", "url": "https://partner-next.gaiacharge.com/cpo/jump/c"},
                    {"name": "Chargemap", "url": "https://chargemap.com/station.html"},
                ],
            },
            {
                "name": "Consum A",
                "sources": [
                    {"name": "Consum", "url": "https://www.consum.es/supermercados/a/"},
                    {"name": "Consum", "url": "https://www.consum.es/supermercados/b/"},
                ],
            },
        ]

        sample = qa_source_links.collect_candidates(rows, max_urls=10, per_host=1, per_provider=2)

        self.assertEqual(
            [row["provider"] for row in sample],
            ["Gaia", "Electromaps", "Chargemap", "Consum"],
        )
        self.assertEqual(len({row["host"] for row in sample}), len(sample))
        self.assertEqual(sample[0]["station"], "Gaia A")

    def test_history_summary_groups_by_provider_and_host(self):
        results = [
            {"provider": "Gaia", "host": "partner-next.gaiacharge.com", "reachable": True},
            {"provider": "Gaia", "host": "partner-next.gaiacharge.com", "reachable": False},
            {"provider": "Electromaps", "host": "map.electromaps.com", "reachable": True},
        ]

        summary = qa_source_links.summarize(results, rows=99, base_url="http://local", path="/api/chargers")

        self.assertEqual(summary["rows"], 99)
        self.assertEqual(summary["sampled"], 3)
        self.assertEqual(summary["reachable"], 2)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["by_provider"]["Gaia"], {"sampled": 2, "reachable": 1, "failed": 1})
        self.assertEqual(summary["by_host"]["map.electromaps.com"], {"sampled": 1, "reachable": 1, "failed": 0})

    def test_append_history_keeps_only_recent_entries(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "source-history.jsonl"
            for i in range(5):
                qa_source_links.append_history(str(path), {"checked_at": str(i), "sampled": i}, [], keep=3)

            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([row["checked_at"] for row in rows], ["2", "3", "4"])


if __name__ == "__main__":
    unittest.main()
