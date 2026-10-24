#!/usr/bin/env python3
"""The delta gate honors only reviewed, verifiable station-ID retirements."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import qa_ingestion_delta as delta  # noqa: E402


def station(row_id: str, lat: float = 40.4379) -> dict:
    url = "https://recarga.fenieenergia.es/punto-recarga/0870"
    return {
        "id": row_id,
        "name": "Station 0870",
        "lat": lat,
        "lon": -3.6264,
        "source": "Feníe Energía public map (Gratuitos)",
        "source_url": url,
        "sources": [{"name": "Feníe Energía public map (Gratuitos)", "url": url}],
        "parking": {"kind": "unknown"},
        "reliability": "directory-map-free",
        "is_free": True,
        "raw": {"fenie_code": "0870"},
    }


def keeper(row_id: str = "kept") -> dict:
    row = station(row_id)
    row["source_url"] = "https://recarga.fenieenergia.es/punto-recarga/0001"
    row["sources"][0]["url"] = row["source_url"]
    row["raw"] = {"fenie_code": "0001"}
    return row


def retirement(old: str, **changes) -> dict:
    item = {
        "id": old,
        "successor_id": "new",
        "retired_on": "2026-10-24",
        "reason": "Upstream corrected the station latitude, so the coordinate-derived ID changed.",
        "evidence": "Same source code, name and station page before and after.",
        "approved_by": "repository owner, 2026-10-24",
    }
    item.update(changes)
    return item


class RetiredIdGateTests(unittest.TestCase):
    def run_gate(self, baseline: list, rebuilt: list, retirements=None, *, raw_retirements: str | None = None):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "baseline.json").write_text(json.dumps({"records": baseline}), encoding="utf-8")
            (tmp_path / "rebuilt.json").write_text(json.dumps({"records": rebuilt}), encoding="utf-8")
            command = [sys.executable, "scripts/qa_ingestion_delta.py",
                       "--baseline", str(tmp_path / "baseline.json"), "--rebuilt", str(tmp_path / "rebuilt.json")]
            if retirements is not None or raw_retirements is not None:
                path = tmp_path / "retirements.json"
                path.write_text(raw_retirements if raw_retirements is not None
                                else json.dumps({"version": 1, "retirements": retirements}), encoding="utf-8")
                command += ["--retirements", str(path)]
            run = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        return run, json.loads(run.stdout)

    def test_without_a_list_the_moved_id_is_missing(self) -> None:
        _run, result = self.run_gate([station("old"), keeper()], [keeper(), station("new", 40.468)])
        self.assertEqual(result["delta"]["missing_ids"], 1)
        self.assertEqual(result["delta"]["retired_ids"], 0)

    def test_reviewed_entry_with_a_new_successor_is_honored(self) -> None:
        run, result = self.run_gate([station("old"), keeper()], [keeper(), station("new", 40.468)], [retirement("old")])
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertTrue(result["ok"])
        self.assertEqual(result["delta"]["missing_ids"], 0)
        self.assertEqual(result["delta"]["retired_ids"], 1)
        self.assertEqual(result["delta"]["retired_sample"], [{"id": "old", "successor_id": "new"}])

    def test_entry_is_not_honored_when_the_successor_is_absent(self) -> None:
        _run, result = self.run_gate([station("old"), keeper()], [keeper()], [retirement("old")])
        self.assertEqual(result["delta"]["missing_ids"], 1)
        self.assertEqual(result["delta"]["retired_ids"], 0)
        self.assertEqual([item["id"] for item in result["delta"]["retirements_unmet"]], ["old"])

    def test_successor_that_already_existed_cannot_hide_a_loss(self) -> None:
        run, result = self.run_gate(
            [station("old"), station("new", 40.468), keeper()], [keeper(), station("new", 40.468)], [retirement("old")])
        self.assertEqual(result["delta"]["missing_ids"], 1)
        self.assertEqual(result["delta"]["retired_ids"], 0)
        self.assertEqual(len(result["delta"]["retirements_unmet"]), 1)
        self.assertIn("baseline", result["delta"]["retirements_unmet"][0]["reason"])

    def test_genuine_removal_entry_without_a_successor_is_honored(self) -> None:
        item = retirement("old")
        del item["successor_id"]
        _run, result = self.run_gate([station("old"), keeper()], [keeper()], [item])
        self.assertEqual(result["delta"]["missing_ids"], 0)
        self.assertEqual(result["delta"]["retired_sample"], [{"id": "old", "successor_id": None}])

    def test_other_missing_ids_still_count(self) -> None:
        other = station("other")
        other["raw"] = {"fenie_code": "0002"}
        other["source_url"] = "https://recarga.fenieenergia.es/punto-recarga/0002"
        other["sources"][0]["url"] = other["source_url"]
        _run, result = self.run_gate(
            [station("old"), other, keeper()], [keeper(), station("new", 40.468)], [retirement("old")])
        self.assertEqual(result["delta"]["missing_ids"], 1)
        self.assertEqual(result["delta"]["missing_sample"], ["other"])
        self.assertEqual(result["delta"]["retired_ids"], 1)

    def test_entry_for_an_id_that_is_still_present_is_reported_stale_and_changes_nothing(self) -> None:
        _run, result = self.run_gate([station("old"), keeper()], [station("old"), keeper()], [retirement("old")])
        self.assertEqual(result["delta"]["missing_ids"], 0)
        self.assertEqual(result["delta"]["retired_ids"], 0)
        self.assertEqual(result["delta"]["retirements_stale"], ["old"])

    def test_malformed_list_fails_the_gate_and_honors_nothing(self) -> None:
        run, result = self.run_gate([station("old"), keeper()], [keeper(), station("new", 40.468)],
                                    raw_retirements='{"version": 1, "retirements": [{"id": "old"}]}')
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse(result["ok"])
        self.assertTrue(any("retirement" in failure for failure in result["failures"]))
        self.assertEqual(result["delta"]["retired_ids"], 0)

    def test_explicitly_named_missing_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "baseline.json").write_text(json.dumps({"records": [keeper()]}), encoding="utf-8")
            (tmp_path / "rebuilt.json").write_text(json.dumps({"records": [keeper()]}), encoding="utf-8")
            run = subprocess.run(
                [sys.executable, "scripts/qa_ingestion_delta.py", "--baseline", str(tmp_path / "baseline.json"),
                 "--rebuilt", str(tmp_path / "rebuilt.json"), "--retirements", str(tmp_path / "absent.json")],
                cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse(json.loads(run.stdout)["ok"])

    def test_a_lost_merged_alias_fails_the_gate(self) -> None:
        holder = keeper()
        holder["merged_ids"] = ["old-alias"]
        run, result = self.run_gate([holder], [keeper()])
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["delta"]["missing_aliases"], 1)
        self.assertEqual(result["delta"]["missing_alias_sample"], ["old-alias"])

    def test_a_lost_alias_needs_a_retirement_whose_evidence_holds(self) -> None:
        holder = keeper()
        holder["merged_ids"] = ["old-alias"]
        run, result = self.run_gate([holder], [keeper()], [retirement("old-alias")])  # successor "new" absent
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(result["delta"]["missing_aliases"], 1)
        self.assertEqual([item["id"] for item in result["delta"]["retirements_unmet"]], ["old-alias"])
        self.assertEqual(result["delta"]["retirements_stale"], [])

    def test_a_lost_alias_with_a_supported_retirement_passes(self) -> None:
        holder = keeper()
        holder["merged_ids"] = ["old-alias"]
        run, result = self.run_gate([holder], [keeper(), station("new", 40.468)], [retirement("old-alias")])
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertEqual(result["delta"]["missing_aliases"], 0)
        self.assertEqual(result["delta"]["retirements_unmet"], [])

    def test_an_alias_kept_moved_or_restored_as_a_station_passes(self) -> None:
        holder = keeper()
        holder["merged_ids"] = ["a", "b", "c"]
        other = station("other")
        other["raw"] = {"fenie_code": "0002"}
        rebuilt_holder = keeper()
        rebuilt_holder["merged_ids"] = ["a"]
        other_rebuilt = dict(other, merged_ids=["b"])
        restored = station("c", 40.5)
        restored["raw"] = {"fenie_code": "0003"}
        _run, result = self.run_gate([holder, other], [rebuilt_holder, other_rebuilt, restored])
        self.assertEqual(result["delta"]["missing_aliases"], 0)

    def test_id_delta_keeps_streaming_records_equivalent(self) -> None:
        old, new, kept = station("old"), station("new", 40.468), keeper()
        retirements = {"old": retirement("old")}
        full = delta.id_delta([old, kept], [kept, new], retirements)
        compact = delta.id_delta([delta.compact_gate_record(old), delta.compact_gate_record(kept)],
                                 [delta.compact_gate_record(kept), delta.compact_gate_record(new)], retirements)
        self.assertEqual(full, compact)
        self.assertEqual(full["missing_ids"], 0)


if __name__ == "__main__":
    unittest.main()
