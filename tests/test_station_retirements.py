"""Contract for the reviewed station-ID retirement list honored by the delta gate."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from station_retirements import RetirementError, load_retirements  # noqa: E402

OLD = "intel_fenie_old_40.4379_-3.6264"
NEW = "intel_fenie_old_40.46817_-3.62643"


def entry(**changes) -> dict:
    base = {
        "id": OLD,
        "successor_id": NEW,
        "retired_on": "2026-10-24",
        "reason": "Upstream corrected the station latitude, so the coordinate-derived ID changed.",
        "evidence": "Same source code, name and station page before and after.",
        "approved_by": "repository owner, 2026-10-24",
    }
    base.update(changes)
    return base


class LoadRetirementsTests(unittest.TestCase):
    def load(self, payload, *, required: bool = True):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "retirements.json"
            if payload is not None:
                text = payload if isinstance(payload, str) else json.dumps(payload)
                path.write_text(text, encoding="utf-8")
            return load_retirements(path, required=required)

    def test_valid_entry_is_returned_by_retired_id(self) -> None:
        loaded = self.load({"version": 1, "retirements": [entry()]})
        self.assertEqual(list(loaded), [OLD])
        self.assertEqual(loaded[OLD]["successor_id"], NEW)

    def test_entry_without_a_successor_is_a_genuine_removal(self) -> None:
        item = entry()
        del item["successor_id"]
        self.assertIn(OLD, self.load({"version": 1, "retirements": [item]}))

    def test_missing_file_is_empty_only_when_not_required(self) -> None:
        self.assertEqual(self.load(None, required=False), {})
        with self.assertRaises(RetirementError):
            self.load(None, required=True)

    def test_empty_list_is_valid(self) -> None:
        self.assertEqual(self.load({"version": 1, "retirements": []}), {})

    def test_malformed_input_is_rejected(self) -> None:
        bad = {
            "not json": "{",
            "top level list": [],
            "wrong version": {"version": 2, "retirements": []},
            "unknown top-level key": {"version": 1, "retirements": [], "extra": 1},
            "retirements not a list": {"version": 1, "retirements": {}},
            "entry not an object": {"version": 1, "retirements": ["x"]},
            "unknown entry key": {"version": 1, "retirements": [entry(note="x")]},
            "empty id": {"version": 1, "retirements": [entry(id="")]},
            "id with whitespace": {"version": 1, "retirements": [entry(id=" " + OLD)]},
            "non-string id": {"version": 1, "retirements": [entry(id=5)]},
            "empty reason": {"version": 1, "retirements": [entry(reason="  ")]},
            "empty evidence": {"version": 1, "retirements": [entry(evidence="")]},
            "empty approval": {"version": 1, "retirements": [entry(approved_by="")]},
            "missing approval": {"version": 1, "retirements": [{k: v for k, v in entry().items() if k != "approved_by"}]},
            "bad date": {"version": 1, "retirements": [entry(retired_on="24/10/2026")]},
            "impossible date": {"version": 1, "retirements": [entry(retired_on="2026-02-31")]},
            "successor equals id": {"version": 1, "retirements": [entry(successor_id=OLD)]},
            "empty successor": {"version": 1, "retirements": [entry(successor_id="")]},
            "duplicate id": {"version": 1, "retirements": [entry(), entry()]},
            "successor is itself retired": {
                "version": 1,
                "retirements": [entry(), entry(id=NEW, successor_id="intel_other")],
            },
        }
        for label, payload in bad.items():
            with self.subTest(label):
                with self.assertRaises(RetirementError):
                    self.load(payload)


class RepositoryRetirementsTests(unittest.TestCase):
    def test_committed_list_validates_and_names_the_corrected_fenie_station(self) -> None:
        loaded = load_retirements(ROOT / "data" / "station_id_retirements.json", required=True)
        self.assertIn(
            "intel_fen-e-energ-a-public-map-gratuitos_fen-e-evbox-estancion-recarga-santalucia-ribera-del-loira_40.4379_-3.6264",
            loaded,
        )
        for item in loaded.values():
            self.assertTrue(item["reason"] and item["evidence"] and item["approved_by"])


if __name__ == "__main__":
    unittest.main()
