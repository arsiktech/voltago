#!/usr/bin/env python3
"""CLI contracts for intentional source-policy removals in ingestion delta."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def row(row_id: str, source: str, source_url: str) -> dict:
    return {
        "id": row_id,
        "name": f"Station {row_id}",
        "lat": 39.47,
        "lon": -0.37,
        "source": source,
        "source_url": source_url,
        "sources": [{"name": source, "url": source_url}],
        "parking": {"kind": "unknown"},
        "reliability": "directory-map-free",
        "is_free": True,
    }


class IngestionDeltaPolicyTests(unittest.TestCase):
    def _run(self, baseline: dict, rebuilt: dict) -> tuple[subprocess.CompletedProcess[str], dict]:
        with tempfile.TemporaryDirectory() as tmp:
            baseline_path = Path(tmp) / "baseline.json"
            rebuilt_path = Path(tmp) / "rebuilt.json"
            baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
            rebuilt_path.write_text(json.dumps(rebuilt), encoding="utf-8")
            run = subprocess.run(
                [
                    sys.executable,
                    "scripts/qa_ingestion_delta.py",
                    "--baseline",
                    str(baseline_path),
                    "--rebuilt",
                    str(rebuilt_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
        return run, json.loads(run.stdout)

    def test_missing_id_absorbed_by_a_pin_with_the_same_source_id_is_merged(self) -> None:
        old = row("old-ocm", "OpenChargeMap public snapshot", "https://map.openchargemap.io/?mode=details&id=52637")
        keep = row("kept", "OpenChargeMap public snapshot", "https://map.openchargemap.io/?mode=details&id=1")
        new = row("endolla", "Barcelona Endolla open data", "https://opendata-ajuntament.barcelona.cat/x")
        new["sources"].append({"name": "OpenChargeMap public snapshot", "url": "https://map.openchargemap.io/?mode=details&id=52637"})
        new["merged_ids"] = ["old-ocm"]
        baseline = {"records": [old, keep]}
        rebuilt = {"records": [keep, new]}

        run, result = self._run(baseline, rebuilt)

        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertEqual(result["delta"]["missing_ids"], 0)
        self.assertEqual(result["delta"]["merged_ids"], 1)
        self.assertEqual(result["delta"]["merged_sample"], [{"id": "old-ocm", "into": "endolla"}])

    def test_merged_ids_claim_without_a_shared_source_id_stays_missing(self) -> None:
        old = row("old-ocm", "OpenChargeMap public snapshot", "https://map.openchargemap.io/?mode=details&id=52637")
        keep = row("kept", "OpenChargeMap public snapshot", "https://map.openchargemap.io/?mode=details&id=1")
        new = row("other", "Barcelona Endolla open data", "https://opendata-ajuntament.barcelona.cat/x")
        new["merged_ids"] = ["old-ocm"]

        _run, result = self._run({"records": [old, keep]}, {"records": [keep, new]})

        self.assertEqual(result["delta"]["missing_ids"], 1)
        self.assertEqual(result["delta"]["merged_ids"], 0)

    def test_permitted_price_annotation_cannot_replace_a_missing_station_id(self) -> None:
        old = row("old", "OpenChargeMap", "https://openchargemap.io/site/poi/details/1")
        other = row("other", "OpenChargeMap", "https://openchargemap.io/site/poi/details/3")
        keep = row("kept", "OpenChargeMap", "https://openchargemap.io/site/poi/details/2")
        claim = {"kind": "electromaps_community", "source": "Electromaps",
                 "source_url": "https://www.electromaps.com/mapi/v2/locations/1"}
        old["price_evidence"] = [claim]
        other["price_evidence"] = [claim]
        keep["price_evidence"] = [claim]
        keep["merged_ids"] = ["old", "other"]
        # Two missing IDs exceed the existing gate's one-ID allowance.
        run, result = self._run({"records": [old, other, keep]}, {"records": [keep]})
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["policy_exclusions"]["baseline"]["records"], 0)
        self.assertEqual(result["delta"]["missing_ids"], 2)
        self.assertEqual(result["delta"]["merged_ids"], 0)

    def test_intentional_exclusion_is_removed_from_baseline_before_delta(self) -> None:
        safe = row("safe", "OpenChargeMap", "https://openchargemap.io/site/poi/details/1")
        excluded = row("blocked", "Consum web-map API", "https://www.consum.es/es/punto-de-carga/1")
        run, result = self._run(
            {"records": [safe, excluded], "errors": []},
            {"records": [safe], "errors": []},
        )

        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertTrue(result["ok"])
        self.assertEqual(result["policy_exclusions"]["baseline"]["records"], 1)
        self.assertEqual(result["policy_exclusions"]["baseline"]["ids"], ["blocked"])
        self.assertEqual(result["policy_exclusions"]["rebuilt"]["records"], 0)
        self.assertEqual(result["delta"]["missing_ids"], 0)

    def test_rebuilt_candidate_with_excluded_origin_fails_closed(self) -> None:
        safe = row("safe", "OpenChargeMap", "https://openchargemap.io/site/poi/details/1")
        excluded = row("blocked", "Directory", "https://www.consum.es/es/punto-de-carga/1")
        run, result = self._run(
            {"records": [safe], "errors": []},
            {"records": [safe, excluded], "errors": []},
        )

        self.assertNotEqual(run.returncode, 0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["policy_exclusions"]["rebuilt"]["records"], 1)
        self.assertTrue(any("excluded source-policy" in failure for failure in result["failures"]))

    def test_price_unknown_official_family_may_disappear_only_with_full_adapter_proof(self) -> None:
        madrid = row(
            "madrid-old-price-match",
            "Madrid public charger open data",
            "https://geoportal.madrid.es/chargers.csv",
        )
        madrid["reliability"] = "official-charger-location-price-unknown"
        run, result = self._run(
            {"records": [madrid], "errors": []},
            {
                "records": [],
                "errors": [],
                "source_adapter_counts": {"source_madrid_official": 123},
            },
        )

        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(
            result["corroboration_only_source_losses"],
            ["Madrid public charger open data"],
        )

    def test_price_unknown_official_family_loss_without_adapter_proof_fails(self) -> None:
        madrid = row(
            "madrid-old-price-match",
            "Madrid public charger open data",
            "https://geoportal.madrid.es/chargers.csv",
        )
        madrid["reliability"] = "official-charger-location-price-unknown"
        run, result = self._run(
            {"records": [madrid], "errors": []},
            {
                "records": [],
                "errors": [],
                "source_adapter_counts": {"source_madrid_official": 0},
            },
        )

        self.assertNotEqual(run.returncode, 0)
        self.assertTrue(any("lost entire source families" in failure for failure in result["failures"]))


if __name__ == "__main__":
    unittest.main()
