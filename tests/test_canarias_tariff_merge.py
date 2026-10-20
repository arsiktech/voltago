"""Repeated merging must not resurrect free prices over correlated hourly costs."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from scripts import canarias_enrichment as enrichment
from scripts import ingest_sources as ing
from scripts.canarias_details import parse_canarias_detail


FIXTURES = Path(__file__).parent / "fixtures" / "canarias_details"


def detail_row(code="p53018", *, stale=False):
    url = "https://canariasterecarga.com/es/" + code + "/"
    detail = parse_canarias_detail((FIXTURES / (code + ".html")).read_text(), url)
    rows = []
    ing.add_record(rows, source=enrichment.SOURCE, source_url=url, name=detail["station_name"],
                   address=detail["address"], lat=28.05, lon=-16.71,
                   price_min=.5 if stale else 0, connector_labels=[], reliability="directory-page",
                   raw={"source_map_id": "4", "source_record_id": "1"},
                   source_artifact=ing.FetchArtifact(b"", url, "map.json", "2026-10-18T00:00:00Z", 0, True, False, False))
    artifact = ing.FetchArtifact(b"", url, "detail.html", "2000-01-01T00:00:00Z" if stale else "2026-10-19T00:00:00Z",
                                 999999 if stale else 0, stale, stale, stale)
    enrichment.apply_detail(rows[0], detail, artifact, ing)
    return rows[0]


def other_row(site, source="Municipal public page", *, price=0, restricted=False):
    rows = []
    url = "https://example.org/" + source.replace(" ", "-").lower()
    ing.add_record(rows, source=source, source_url=url, name=site["name"], address=site["address"],
                   lat=site["lat"], lon=site["lon"], price_min=price, connector_labels=[],
                   reliability="official-free",
                   source_artifact=ing.FetchArtifact(b"", url, "municipal.html", "2026-10-18T12:00:00Z", 0, True, False, False))
    if restricted:
        rows[0].update(access={"level": "restricted", "note": "Customers only"},
                       is_free=False, price_claim_type="access_limited_free")
    return rows[0]


def next_merge(row, extra, stage):
    rows = [copy.deepcopy(row), copy.deepcopy(extra)]
    if stage == "dedupe":
        result = ing.dedupe(rows)
    else:
        result, count = ing.merge_cross_source_duplicates(rows)
        assert count == 1
    assert len(result) == 1
    return result[0]


class RepeatedCanariasTariffMergeTests(unittest.TestCase):
    def test_hourly_cost_survives_dedupe_then_cross_source_or_repeated_dedupe(self):
        fresh = detail_row()
        first = ing.dedupe([other_row(fresh), fresh])[0]
        self.assertNotIn("detail", first.get("raw", {}))
        self.assertIn("source_details_evidence", first)
        for stage in ("dedupe", "cross_source"):
            with self.subTest(stage=stage):
                merged = next_merge(first, other_row(first, "Another public page"), stage)
                merged = next_merge(merged, other_row(first, "Third public page"), stage)
                self.assertIsNone(merged["price_min"])
                self.assertFalse(merged["is_free"])
                self.assertFalse(merged["is_cheap"])
                self.assertEqual(merged["tariff_details"]["hour_eur"], 2)
                self.assertEqual(merged["tariff_details"]["conditions"], ["Gratis 1ªhora"])
                self.assertTrue(any(c["price_min"] == 0 for c in merged["charge_price_claims"]))
                self.assertEqual(merged["claim_conflict"]["charge_url"], fresh["source_url"])

    def test_metadata_only_candidate_does_not_hide_known_costs(self):
        fresh = detail_row()
        first = ing.dedupe([other_row(fresh), fresh])[0]
        info_only = copy.deepcopy(first)
        info_only.update(id="metadata-only", source="Another public page")
        info_only.pop("tariff_details")
        info_only["source_details"]["notes"] = ["Unpriced location information"]
        result = ing.dedupe([info_only, first, other_row(first, "Third public page")])[0]
        self.assertIsNone(result["price_min"])
        self.assertFalse(result["is_free"])
        self.assertEqual(result["tariff_details"]["hour_eur"], 2)

    def test_energy_and_minimum_spend_keep_their_own_units_across_merges(self):
        fresh = detail_row("p00246")
        first = ing.dedupe([other_row(fresh), fresh])[0]
        for stage in ("dedupe", "cross_source"):
            with self.subTest(stage=stage):
                result = next_merge(first, other_row(first, "Another public page"), stage)
                self.assertEqual(result["price_min"], .38)
                self.assertFalse(result["is_free"])
                components = result["tariff_details"]["components"]
                self.assertEqual([(c["type"], c["price"], c["unit"]) for c in components],
                                 [("ENERGY", .38, "kWh"), ("MINIMUM", 2., "session")])
                self.assertNotIn("FLAT", [c["type"] for c in components])

    def test_restricted_free_is_not_reopened_by_another_free_claim(self):
        fresh = detail_row("p61008")
        first = ing.dedupe([other_row(fresh, restricted=True), fresh])[0]
        result = next_merge(first, other_row(first, "Another public page"), "cross_source")
        self.assertEqual(result["price_min"], 0)
        self.assertFalse(result["is_free"])
        self.assertEqual(result["price_claim_type"], "access_limited_free")

    def test_source_facts_and_original_fetch_time_survive_without_raw_detail(self):
        fresh = detail_row()
        fresh["source_details"].update(connectors=[{"type": "Type 2", "count": 2, "power_kw": 7.4}],
                                       connector_count=2, max_power_kw=7.4, source_name=enrichment.SOURCE)
        first = ing.dedupe([other_row(fresh), fresh])[0]
        result = next_merge(first, other_row(first, "Another public page"), "cross_source")
        self.assertEqual(result["source_details"]["connectors"], fresh["source_details"]["connectors"])
        self.assertEqual(result["source_details"]["connector_count"], 2)
        self.assertEqual(result["source_details"]["max_power_kw"], 7.4)
        self.assertEqual(result["source_details_evidence"]["fetched_at"], "2026-10-19T00:00:00Z")
        self.assertEqual(result["source_fetched_at"], "2026-10-18T00:00:00Z")
        self.assertFalse(result["source_details_evidence"]["stale"])

    def test_stale_page_is_never_promoted_into_a_current_tariff(self):
        fresh = detail_row("p61008", stale=True)
        self.assertNotIn("tariff_details", fresh)
        first = ing.dedupe([other_row(fresh, price=None), fresh])[0]
        result = next_merge(first, other_row(first, "Another public page", price=None), "cross_source")
        self.assertEqual(result["price_min"], .5)
        self.assertNotIn("tariff_details", result)
        self.assertEqual(result["source_fetched_at"], "2000-01-01T00:00:00Z")
        self.assertEqual(result["source_details_evidence"]["fetched_at"], "2000-01-01T00:00:00Z")
        self.assertTrue(result["source_details_evidence"]["stale"])
        self.assertTrue(any("2000-01-01" in note for note in result["source_details"]["notes"]))

    def test_unknown_page_fetch_time_is_not_invented_from_a_newer_map_or_merge(self):
        fresh = detail_row()
        fresh["raw"].pop("detail_fetch")
        first = ing.dedupe([other_row(fresh), fresh])[0]
        result = next_merge(first, other_row(first, "Another public page"), "cross_source")
        proof = result["source_details_evidence"]
        self.assertNotIn("fetched_at", proof)
        self.assertNotIn("stale", proof)
        self.assertIsNone(result["price_min"])
        self.assertFalse(result["is_free"])

    def test_carried_identity_is_rechecked_for_a_different_canonical_station(self):
        fresh = detail_row()
        first = ing.dedupe([other_row(fresh), fresh])[0]
        other = other_row(first, "Another public page")
        other.update(name="Different charger", address="Calle Other, 35610 Caleta de Fuste")
        ing.merge_source_details([first], other)
        self.assertEqual(other["price_min"], 0)
        self.assertNotIn("source_details", other)
        self.assertNotIn("source_details_evidence", other)


if __name__ == "__main__":
    unittest.main()
