"""Qualified historical aliases retain exact identity without overwriting existing pins."""
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from station_identity import identity_keys, preserve_record_ids
from source_policy import recordset_sha256
from qa_ingestion_delta import id_delta


def osm(identifier, node, **changes):
    return dict(id=identifier, source="OpenStreetMap public tags",
                source_url=f"https://www.openstreetmap.org/node/{node}",
                sources=[], lat=39.0, lon=-0.5, **changes)


def registry(identifier, site):
    return {"id": identifier, "source": "DGT National Access Point charging registry",
            "source_url": "https://nap.dgt.es/dataset/puntos-de-recarga-electrica-para-vehiculos",
            "raw": {"nap_site_id": site}, "sources": [], "lat": 39.0, "lon": -0.5}


def split_fixture():
    old = osm("historical", 123, price_min=0.0)
    old["merged_source_identifiers"] = [["osm", "/node/456"]]
    existing = registry("existing", "site-1")
    target = dict(deepcopy(existing), price_min=0.5,
                  merged_source_identifiers=[["osm", "/node/123"]],
                  collision_base_ids=["historical"])
    other = dict(osm("split-neighbor", 456), collision_base_ids=["historical"])
    return {"records": [old, existing]}, {"records": [target, other], "recordset_sha256": "stale"}


class HistoricalAliasTests(unittest.TestCase):
    def test_proven_merged_alias_does_not_compete_with_unique_reassignment(self):
        old = osm("historical", 123)
        alias = registry("already-merged", "site-1")
        target = dict(osm("generated", 123), merged_ids=[alias["id"]],
                      merged_source_identifiers=[[alias["source"], "nap_site_id", "site-1"]])
        baseline, candidate = {"records": [old, alias]}, {"records": [target]}
        result, report = preserve_record_ids(baseline, candidate)
        self.assertEqual(report["preserved_ids"], 1)
        self.assertEqual(result["records"][0]["id"], "historical")
        self.assertEqual(result["records"][0]["merged_ids"], ["already-merged"])
        self.assertEqual(id_delta(baseline["records"], result["records"])["missing_ids"], 0)

    def test_split_primary_becomes_alias_of_consistently_owned_existing_pin(self):
        baseline, candidate = split_fixture()
        original = deepcopy(candidate)
        result, report = preserve_record_ids(baseline, candidate)
        target = result["records"][0]
        self.assertEqual(target["id"], "existing")
        self.assertEqual(target["price_min"], 0.5)
        self.assertEqual(target["merged_ids"], ["historical"])
        self.assertNotIn(("osm", "/node/456"), identity_keys(target))
        self.assertEqual(report["preserved_ids"], 0)
        self.assertEqual(report["historical_aliases_added"], 1)
        self.assertEqual(report["historical_alias_changes"][0]["merged_id"], "historical")
        self.assertEqual(report["historical_alias_changes"][0]["into"], "existing")
        self.assertEqual(result["recordset_sha256"], recordset_sha256(result["records"]))
        self.assertEqual(candidate, original)
        self.assertEqual(id_delta(baseline["records"], result["records"])["missing_ids"], 0)

    def test_inconsistent_existing_id_gets_neither_reassignment_nor_alias(self):
        baseline = {"records": [osm("historical", 123), osm("existing", 999)]}
        result, report = preserve_record_ids(baseline, {"records": [osm("existing", 123)]})
        self.assertEqual(report["preserved_ids"], 0)
        self.assertNotIn("historical", result["records"][0].get("merged_ids", []))
        # Even genuine split ancestry cannot make a repurposed ID belong to OSM 999.
        baseline, candidate = split_fixture()
        baseline["records"][1] = osm("existing", 999)
        candidate["records"][0].pop("raw")
        candidate["records"][0]["source"] = "OpenStreetMap public tags"
        candidate["records"][0]["source_url"] = "https://www.openstreetmap.org/node/123"
        result, report = preserve_record_ids(baseline, candidate)
        self.assertEqual(report["historical_aliases_added"], 0)
        self.assertNotIn("historical", result["records"][0].get("merged_ids", []))

    def test_alias_requires_every_split_witness_and_exact_existing_source_key(self):
        for mutation in ("missing-split", "generic-existing", "far-primary", "duplicate-baseline", "duplicate-current"):
            with self.subTest(mutation=mutation):
                baseline, candidate = split_fixture()
                if mutation == "missing-split":
                    candidate["records"][1].pop("collision_base_ids")
                elif mutation == "generic-existing":
                    baseline["records"][1].pop("raw")
                elif mutation == "far-primary":
                    candidate["records"][0]["lat"] = 39.01
                elif mutation == "duplicate-baseline":
                    baseline["records"].append(osm("another-baseline", 123))
                else:
                    candidate["records"].append(osm("another-current", 123))
                result, report = preserve_record_ids(baseline, candidate)
                self.assertEqual(report["historical_aliases_added"], 0)
                self.assertNotIn("historical", result["records"][0].get("merged_ids", []))

    def test_historical_alias_cannot_be_claimed_by_another_pin(self):
        for copies in (1, 2):
            with self.subTest(copies=copies):
                baseline, candidate = split_fixture()
                for i in range(copies):
                    candidate["records"].append(dict(osm(f"unrelated-{i}", 900 + i), merged_ids=["historical"]))
                result, report = preserve_record_ids(baseline, candidate)
                self.assertEqual(report["historical_aliases_added"], 0)
                self.assertNotIn("historical", result["records"][0].get("merged_ids", []))

    def test_existing_merged_claim_without_shared_stable_key_is_not_proof(self):
        baseline = {"records": [osm("historical", 123)]}
        candidate = {"records": [dict(osm("unrelated", 999), merged_ids=["historical"]), osm("generated", 123)]}
        result, report = preserve_record_ids(baseline, candidate)
        self.assertEqual(report["preserved_ids"], 1)
        self.assertEqual(result["records"][1]["id"], "historical")


if __name__ == "__main__":
    unittest.main()
