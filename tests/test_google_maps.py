"""Listing identity, safe fallbacks, and the compact client handoff contract."""
import unittest
from urllib.parse import parse_qs, urlsplit

import server
from google_maps import attach_google_maps_navigation, google_listing_url


class GoogleMapsTests(unittest.TestCase):
    def station(self, **overrides):
        return {"id": "maps-test", "name": "Buenavista", "address": "C/ La Cuesta, 519",
                "city": "Buenavista del Norte", "lat": 28.362, "lon": -16.848, **overrides}

    def test_unmatched_station_searches_name_address_and_coordinates(self):
        station = self.station(navigation={"google": "https://www.google.com/maps/search/?api=1&query=28.362,-16.848"})
        directions = station["navigation"]["google"]
        attach_google_maps_navigation(station)
        self.assertNotIn("google_listing", station["navigation"])
        self.assertEqual(station["navigation"]["google"], directions)
        query = parse_qs(urlsplit(station["navigation"]["google_search"]).query)
        self.assertEqual(query["api"], ["1"])
        for expected in ("EV charging station", "Buenavista", "C/ La Cuesta, 519", "28.362000,-16.848000"):
            self.assertIn(expected, query["query"][0])

    def test_source_listing_and_place_id_survive_compact_summary(self):
        for metadata in (
            {"google_place_id": "ChIJ_test_place"},
            {"google_maps": {"place_id": "ChIJ_test_place"}},
            {"navigation": {"google_listing": "https://maps.app.goo.gl/YXKoYi8GzoUeYwiF7"}},
            {"source_url": "https://www.google.com/maps?cid=123456"},
        ):
            with self.subTest(metadata=metadata):
                station = self.station(**metadata)
                attach_google_maps_navigation(station)
                compact = server.compact_station_for_ui(station)
                summary = server.summary_station_for_ui(compact, "snapshot")
                self.assertIsNotNone(google_listing_url(summary["navigation"]["google_listing"]))
                self.assertEqual(summary["navigation"], station["navigation"])

    def test_buenavista_reviewed_share_link_binds_to_its_station_only(self):
        for identity in (
            {"id": "intel_canarias-te-recarga-public-map_buenavista_28.36181_-16.84731"},
            {"source_url": "https://canariasterecarga.com/es/p00261"},
        ):
            station = self.station(**identity)
            attach_google_maps_navigation(station)
            self.assertEqual(station["navigation"]["google_listing"], "https://maps.app.goo.gl/YXKoYi8GzoUeYwiF7")
        # A same-name station or nearby charger must not inherit this association.
        other = self.station(source_url="https://canariasterecarga.com/es/p00262")
        attach_google_maps_navigation(other)
        self.assertNotIn("google_listing", other["navigation"])

    def test_identified_google_url_formats(self):
        for url in (
            "https://maps.app.goo.gl/YXKoYi8GzoUeYwiF7",
            "https://goo.gl/maps/exampleCode",
            "https://www.google.com/maps?cid=123456",
            "https://maps.google.es/?q=place_id:ChIJ_example",
            "https://www.google.com/maps/search/?api=1&query=charger&query_place_id=ChIJ_example",
            "https://www.google.com/maps/place/Charger/data=!4m5!3m4!1s0xc:0xd!8m2!3d28.3!4d-16.8",
        ):
            with self.subTest(url=url):
                self.assertEqual(google_listing_url(url), url)

    def test_nonlisting_and_unsafe_links_never_claim_an_exact_match(self):
        for url in (
            "https://www.google.com/maps/search/?api=1&query=28.362,-16.848",
            "https://www.google.com/maps/place/EV+charger",
            "https://www.google.com/maps/dir/?api=1&destination=Charger&query_place_id=ChIJ_test",
            "https://www.google.com/redirect?cid=123456",
            "https://maps.app.goo.gl/", "https://goo.gl/maps/",
            "https://google.com.evil.example/maps?cid=123456",
            "https://evil.example/maps?cid=123456",
            "https://user:password@www.google.com/maps?cid=123456",
            "https://www.google.com:444/maps?cid=123456",
            "http://www.google.com/maps?cid=123456", "javascript:alert(1)",
        ):
            with self.subTest(url=url):
                station = self.station(navigation={"google_listing": url, "google_search": url})
                attach_google_maps_navigation(station)
                self.assertNotIn("google_listing", station["navigation"])
                self.assertIsNone(google_listing_url(url))
                self.assertTrue(station["navigation"]["google_search"].startswith("https://www.google.com/maps/search/?"))

    def test_listing_does_not_upgrade_station_evidence(self):
        station = self.station(google_place_id="ChIJ_test", decision={"level": "verify"},
                               verification={"location_verified": False, "price_verified": False})
        attach_google_maps_navigation(station)
        self.assertEqual(station["decision"], {"level": "verify"})
        self.assertFalse(station["verification"]["location_verified"])
        self.assertFalse(station["verification"]["price_verified"])
        for field in ("rating", "reviews", "user_rating_count"):
            self.assertNotIn(field, station)


if __name__ == "__main__":
    unittest.main()
