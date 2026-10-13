import importlib.util
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("server", ROOT / "server.py")
server = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(server)


class SourceHealthTests(unittest.TestCase):
    def test_summary_exposes_policy_counts_and_live_cache_state(self):
        summary = server.source_health_summary()
        policy_text = str(summary.get("policy", "")).lower()
        intel = summary.get("intelligence") or {}
        hidden = intel.get("hidden_from_map") or {}

        self.assertIn("get-only", policy_text)
        self.assertIn("two owner-approved read-only data-query posts", policy_text)
        self.assertIn("any other post or non-get request", policy_text)
        self.assertIn("login bypass", policy_text)
        self.assertIn("private tokens", policy_text)
        self.assertGreater(int(intel.get("deduped_leads") or 0), 0)
        self.assertGreater(int(intel.get("map_eligible_records") or 0), 0)
        for key in ("official_price_unknown", "directory", "community", "unverified", "invalid_coordinates"):
            self.assertIn(key, hidden)
        self.assertIsInstance(intel.get("by_tier"), dict)
        self.assertIsInstance(intel.get("by_source"), dict)
        self.assertIsInstance(intel.get("coordinate_issues"), dict)
        self.assertIn("mapareve", summary.get("live_cache") or {})
        engine = summary.get("engine") or {}
        self.assertEqual(engine.get("version"), server.ENGINE_VERSION)
        self.assertIn("route-ready", str(engine.get("route_ready_rule")).lower())
        self.assertIn("snapped bbox", str(engine.get("cache_key_rule")).lower())
        self.assertIn("meta.engine.stages", engine.get("diagnostic_fields") or [])

    def test_map_eligibility_allows_exact_directory_but_not_generic_or_community(self):
        official_verified = {
            "lat": 39.47,
            "lon": -0.37,
            "lead": True,
            "reliability": "official-free-policy",
            "verification": {"location_verified": True, "price_verified": True, "route_checked": True},
        }
        exact_directory_lead = {
            "lat": 39.47,
            "lon": -0.37,
            "lead": True,
            "reliability": "directory-map-free",
            "verification": {"location_verified": True, "price_verified": False, "route_checked": False},
            "primary_source": {"kind": "station_page", "quality": "exact", "url": "https://map.electromaps.com/en/p/123"},
        }
        generic_directory_lead = {
            "lat": 39.47,
            "lon": -0.37,
            "lead": True,
            "reliability": "directory-map-free",
            "verification": {"location_verified": True, "price_verified": False, "route_checked": False},
            "primary_source": {"kind": "provider_homepage", "url": "https://example.com"},
        }
        community_lead = {
            "lat": 39.47,
            "lon": -0.37,
            "lead": True,
            "reliability": "community-free",
            "verification": {"location_verified": False, "price_verified": False, "route_checked": False},
        }
        osm_community_lead = {
            "lat": 28.4631761,
            "lon": -16.2531091,
            "lead": True,
            "reliability": "community-free",
            "verification": {"location_verified": False, "price_verified": False, "route_checked": False, "live_verified": False},
            "source_url": "https://www.openstreetmap.org/node/8906632362",
            "primary_source": {"kind": "map_object", "quality": "map", "url": "https://www.openstreetmap.org/node/8906632362"},
        }
        official_unknown_price = {
            "lat": 39.47,
            "lon": -0.37,
            "lead": True,
            "reliability": "official-charger-location-price-unknown",
            "verification": {"location_verified": True, "price_verified": False, "route_checked": False},
        }

        self.assertTrue(server.source_health_map_eligible(official_verified))
        self.assertTrue(server.source_health_map_eligible(exact_directory_lead))
        self.assertFalse(server.source_health_map_eligible(generic_directory_lead))
        self.assertFalse(server.source_health_map_eligible(community_lead))
        self.assertTrue(server.source_health_map_eligible(osm_community_lead))
        # Official registry coordinates are a location claim on their own; the pin shows no price.
        self.assertTrue(server.source_health_map_eligible(official_unknown_price))
        self.assertFalse(server.source_health_map_eligible({"lat": None, "lon": None, "lead": False}))

    def test_official_free_records_are_verified_official_leads(self):
        item = server.enrich_station({
            "id": "municipal-official-free-example",
            "name": "Municipal official free example",
            "lat": 41.56,
            "lon": 2.15,
            "lead": True,
            "reliability": "official-free",
            "price_min": 0.0,
            "is_free": True,
            "is_cheap": True,
            "tesla_ok": True,
            "source": "València municipal OpenData",
            "source_url": "https://geoportal.valencia.es/arcgis/rest/services/EV/MapServer/0/1",
            "sources": [{"name": "València municipal OpenData", "url": "https://geoportal.valencia.es/arcgis/rest/services/EV/MapServer/0/1"}],
        }, live=False)

        self.assertEqual(item["verification"]["level"], "official")
        self.assertTrue(item["verification"]["price_verified"])
        self.assertTrue(item["verification"]["location_verified"])
        self.assertTrue(server.source_health_map_eligible(item))

    def test_manual_seed_cannot_claim_official_map_eligibility(self):
        item = server._normalize_lead({
            "id": "manual-gaia-official-claim",
            "name": "Manual Gaia official claim",
            "lat": 39.489,
            "lon": -0.3291,
            "price_min": 0.0,
            "is_free": True,
            "is_cheap": True,
            "tesla_ok": True,
            "max_power_kw": 22,
            "source": "Gaia public start-session page",
            "source_url": "https://partner.gaiacharge.com/cpo/jump/example-1",
            "reliability": "official-free-policy",
        }, "manual research lead")

        self.assertEqual(item["reliability"], "manual-check-needed")
        self.assertEqual(item["verification"]["level"], "unverified")
        self.assertFalse(item["verification"]["price_verified"])
        self.assertFalse(item["verification"]["location_verified"])
        self.assertFalse(server.source_health_map_eligible(item))
        self.assertEqual(item["raw"]["manual_reliability_downgraded_from"], "official-free-policy")

    def test_removed_gaia_patacona_manual_seed_is_not_loaded(self):
        self.assertFalse(any(
            item.get("id") == "lead_gaia_patacona"
            for item in server.load_seed_leads()
        ))


if __name__ == "__main__":
    unittest.main()
