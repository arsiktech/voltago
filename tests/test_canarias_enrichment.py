"""Source refresh regressions: preserve identity, attribution, costs and cache age."""
import copy
import hashlib
import json
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from scripts import ingest_sources as ing
from scripts import canarias_enrichment as enrichment
from scripts.canarias_details import parse_canarias_detail

FIXTURES = Path(__file__).parent / "fixtures" / "canarias_details"
URL = "https://canariasterecarga.com/es/p00246/"


def marker(name="C.C. Pasarela", number="1", url=URL):
    rows = []
    ing.add_record(rows, source=enrichment.SOURCE, source_url=url, name=name,
        lat=28.05, lon=-16.71, price_min=0, connector_labels=[], reliability="directory-page",
        raw={"source_map_id": "4", "source_record_id": number},
        source_artifact=ing.FetchArtifact(b"", url, "map.json", "2026-10-18T00:00:00Z", 0, True, False, False))
    return rows[0]


def enriched(code="p00246", name="C.C. Pasarela"):
    record = marker(name, url=f"https://canariasterecarga.com/es/{code}/")
    page = parse_canarias_detail((FIXTURES / (code + ".html")).read_text(), record["source_url"])
    artifact = ing.FetchArtifact(b"", record["source_url"], "detail.html", "2026-10-19T00:00:00Z", 0, False, False, False)
    enrichment.apply_detail(record, page, artifact, ing)
    return record


class DetailProjectionTests(unittest.TestCase):
    def test_full_rebuild_merge_retains_hourly_costs_and_avoids_free_projection(self):
        fresh = enriched("p53018", "Ayuntamiento de La Matanza")
        old = marker(name="Ayuntamiento de La Matanza")
        old.update(source="Official", reliability="official", source_url="https://official.example/1",
                   charge_claim={"source": "Official", "url": "https://official.example/1", "price_min": 0})
        with patch("scripts.canarias_details.page_identity_matches", return_value=(True, "same_site")):
            result = ing.dedupe([old, fresh])[0]
        self.assertIsNone(result["price_min"])
        self.assertFalse(result["is_free"])
        self.assertEqual(result["tariff_details"]["hour_eur"], 2)
        self.assertEqual(result["claim_conflict"]["level"], "mixed_sources")

    def test_full_inventory_and_minimum_spend_are_separate(self):
        row = enriched()
        self.assertEqual(row["connector_count"], 9)
        self.assertEqual(row["hardware_claim"]["port_count"], 9)
        self.assertEqual(row["max_power_kw"], 50)
        self.assertEqual(row["source_details"]["connector_count"], 9)
        self.assertEqual(len(row["source_details"]["connectors"]), 4)
        self.assertEqual(row["price_min"], .38)
        self.assertEqual(row["tariff_details"]["minimum_eur"], 2)
        self.assertNotIn("FREE_PERIOD", str(row["tariff_details"]["components"]))
        self.assertFalse(row["source_integration"]["live_status_integrated"])
        self.assertEqual(row["available"], 0)

    def test_first_hour_free_is_hourly_and_never_free_energy(self):
        row = enriched("p53018", "Ayuntamiento de La Matanza")
        self.assertIsNone(row["price_min"])
        self.assertFalse(row["is_free"])
        self.assertFalse(row["is_cheap"])
        self.assertEqual(row["tariff_details"]["hour_eur"], 2)
        self.assertIn("Gratis 1ªhora", row["tariff_details"]["conditions"])
        self.assertTrue(all(c["type"] in {"ENERGY", "TIME", "MINIMUM", "FLAT"}
                            for c in row["tariff_details"]["components"]))

    def test_photo_references_do_not_grant_display_rights_or_operator_phone(self):
        row = enriched("p00275", "C.C. Martianez (Planta -2)")
        self.assertTrue(row["media"]["photos"])
        self.assertFalse(any(p.get("display_rights_approved") for p in row["media"]["photos"]))
        self.assertNotIn("contact", row["source_details"])

    def test_map_icon_without_published_quantity_keeps_unknown_total(self):
        feature = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-16.71, 28.05]},
                   "properties": {"id": "1", "name": "Test", "popup": '<img src="https://canariasterecarga.com/ev-plug-type2.svg">'}}
        row = ing.parse_canarias_tecarga_geojson({"features": [feature]}, minimum_features=1)[0]
        self.assertIsNone(row["connectors"][0]["count"])
        self.assertIsNone(row["total_evse"])
        self.assertIsNone(row["hardware_claim"]["port_count"])
        merged = ing.dedupe([row])[0]
        self.assertIsNone(merged["total_evse"])
        self.assertIsNone(merged["hardware_claim"]["port_count"])


class OverlayTests(unittest.TestCase):
    def test_access_limited_free_and_other_source_hardware_are_preserved(self):
        fresh = enriched("p61008", "Ayuntamiento Santa Cruz de Tenerife")
        old = marker()
        old.update(access={"level": "restricted"}, is_free=False, price_claim_type="access_limited_free",
                   connectors=[{"type": "Type 2", "count": 2, "power_kw": 22, "source_url": "https://osm.example/1"}],
                   hardware_claim={"source": "OpenStreetMap public tags", "url": "https://osm.example/1"})
        row = enrichment.overlay_payload({"records": [old]}, [fresh], ing)[0]["records"][0]
        self.assertFalse(row["is_free"])
        self.assertEqual(row["price_claim_type"], "access_limited_free")
        self.assertEqual(row["connectors"], old["connectors"])
        self.assertEqual(row["source_details"]["connector_count"], 1)

    def test_free_other_source_cannot_hide_known_hourly_tariff(self):
        fresh = enriched("p53018", "Ayuntamiento de La Matanza")
        old = marker()
        old["charge_claim"] = {"source": "Official", "url": "https://official.example/1", "price_min": 0}
        old["sources"].append({"name": "Official", "url": "https://official.example/1"})
        row = enrichment.overlay_payload({"records": [old]}, [fresh], ing)[0]["records"][0]
        self.assertIsNone(row["price_min"])
        self.assertFalse(row["is_free"])
        self.assertEqual(row["tariff_details"]["hour_eur"], 2)
        self.assertEqual(row["charge_price_claims"][0]["price_min"], 0)
        self.assertIn("additional charges", row["claim_conflict"]["summary"])

    def test_stale_page_does_not_override_new_paid_map_tariff(self):
        row = marker()
        enrichment._set_price(row, .5, "Current map tariff", ing)
        detail = parse_canarias_detail((FIXTURES / "p61008.html").read_text(), URL)
        old_artifact = ing.FetchArtifact(b"", URL, "cached.html", "2020-01-01T00:00:00Z", 999999, True, True, True)
        enrichment.apply_detail(row, detail, old_artifact, ing)
        self.assertEqual(row["price_min"], .5)
        self.assertEqual(row["source_fetched_at"], "2020-01-01T00:00:00Z")
        self.assertNotIn("tariff_details", row)

    def test_stable_marker_preserves_ids_aliases_coords_other_records_and_age(self):
        old = marker()
        old.update(id="saved-favourite-id", merged_ids=["an-old-id"], lat=28.0501,
                   identity_reconciliation={"generated_id": "another-alias"})
        untouched = {"id": "unrelated", "source": "Official", "lat": 40, "private": "retained"}
        baseline = {"records": [old, untouched], "total_records": 2}
        before = copy.deepcopy(baseline)
        candidate, report = enrichment.overlay_payload(baseline, [enriched()], ing)
        row = candidate["records"][0]
        for key in ("id", "merged_ids", "lat", "lon", "identity_reconciliation"):
            self.assertEqual(row[key], old[key])
        self.assertEqual(row["source_fetched_at"], old["source_fetched_at"])
        self.assertEqual(candidate["records"][1], untouched)
        self.assertEqual(baseline, before)
        self.assertEqual(report["changed"], 1)
        self.assertEqual(row["price_min"], .38)

    def test_alias_match_does_not_overwrite_higher_authority_inventory_or_price(self):
        fresh = enriched()
        old = marker()
        old.update(id="official-id", source="Official", source_url="https://official.example/station",
                   merged_ids=[fresh["id"]], reliability="official", connectors=[{"type": "CCS2", "count": 2, "power_kw": 150}],
                   hardware_claim={"source": "Official"}, price_min=.5,
                   charge_claim={"source": "Official", "url": "https://official.example/station", "price_min": .5},
                   sources=[*old["sources"], {"name": "Official", "url": "https://official.example/station"}])
        with patch("scripts.canarias_details.page_identity_matches", return_value=(True, "same_site")):
            row = enrichment.overlay_payload({"records": [old]}, [fresh], ing)[0]["records"][0]
        self.assertEqual(row["connectors"], old["connectors"])
        self.assertEqual(row["price_min"], .5)
        self.assertEqual(row["charge_claim"]["source"], "Official")
        self.assertEqual(row["claim_conflict"]["level"], "mixed_sources")
        self.assertNotIn("tariff_details", row)

    def test_url_alone_and_ambiguous_alias_cannot_match(self):
        old = marker(number="different")
        fresh = enriched()
        old["id"] = "unrelated-id"
        with self.assertRaises(ValueError):
            enrichment.overlay_payload({"records": [old]}, [fresh], ing)
        one, two = copy.deepcopy(old), copy.deepcopy(old)
        one["merged_ids"] = two["merged_ids"] = [fresh["id"]]
        two["id"] = "second"
        with self.assertRaises(ValueError):
            enrichment.overlay_payload({"records": [one, two]}, [fresh], ing)

    def test_wrong_canonical_site_is_held_and_baseline_preserved(self):
        fresh = enriched()
        old = marker()
        old.update(source="Official", id="official-id", merged_ids=[fresh["id"]], name="Different site")
        with patch("scripts.canarias_details.page_identity_matches", return_value=(False, "wrong_site")):
            candidate, report = enrichment.overlay_payload({"records": [old]}, [fresh], ing)
        self.assertEqual(candidate["records"], [old])
        self.assertEqual(report["primary_identity_held"], ["official-id"])


class FetchBoundsTests(unittest.TestCase):
    def test_cache_hit_retains_time_and_stale_fallback_is_labeled(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder)
            key = hashlib.sha256(URL.encode()).hexdigest()
            body = b"<html>cached</html>"
            (cache / (key + ".html")).write_bytes(body)
            fetched = "2000-01-01T00:00:00Z"
            (cache / (key + ".json")).write_text(json.dumps({"url": URL, "final_url": URL,
                "fetched_at": fetched, "body_sha256": hashlib.sha256(body).hexdigest()}))
            with patch("urllib.request.OpenerDirector.open", side_effect=TimeoutError):
                artifact = enrichment.fetch_detail(URL, cache, ing)
            self.assertEqual(artifact.body, body)
            self.assertEqual(artifact.fetched_at, fetched)
            self.assertTrue(artifact.hit and artifact.fallback and artifact.stale)
            self.assertEqual(artifact.error, "TimeoutError")

    def test_unsafe_get_or_redirect_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                enrichment.fetch_detail("https://other.example/charger", Path(folder), ing)
        handler = enrichment._SourceRedirect()
        request = __import__("urllib.request", fromlist=["Request"]).Request(URL)
        with self.assertRaises(ValueError):
            handler.redirect_request(request, None, 302, "Moved", {}, "https://other.example/login")


if __name__ == "__main__":
    unittest.main()
