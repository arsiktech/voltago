"""Exact named charger handoffs, independent of price/live authority."""
from copy import deepcopy
import unittest
from unittest.mock import patch

import server
from station_truth import normalize_public_source_url, station_source_for


class StationSourceActionTests(unittest.TestCase):
    def test_directory_detail_beats_generic_map_and_uses_actual_source_name(self):
        item = {
            "source": "Canarias te recarga public map", "source_label": "Directory page",
            "source_url": "https://canariasterecarga.com/es/mapa/",
            "primary_source": {"name": "Directory page", "url": "https://canariasterecarga.com/es/mapa/", "kind": "live_map"},
            "source_details": {"source_url": "https://canariasterecarga.com/es/mapa/p61008/"},
        }
        before = deepcopy(item)
        self.assertEqual(station_source_for(item), {
            "name": "Canarias te recarga", "url": item["source_details"]["source_url"], "kind": "station_page",
        })
        self.assertEqual(item, before)

    def test_existing_exact_primary_and_nested_evidence_keep_their_own_charger_urls(self):
        for field in ("source_url", "primary_source", "source_details", "tariff_details", "charge_claim", "sources", "connectors"):
            with self.subTest(field=field):
                url = "https://canariasterecarga.com/es/p00246/"
                item = {"source": "Directory page"}
                if field == "source_url":
                    item[field] = url
                elif field in {"sources", "connectors"}:
                    item[field] = [{"url" if field == "sources" else "source_url": url}]
                else:
                    item[field] = {"url" if field in {"primary_source", "charge_claim"} else "source_url": url}
                self.assertEqual(station_source_for(item), {"name": "Canarias te recarga", "url": url, "kind": "station_page"})

    def test_real_station_url_shapes_name_the_corresponding_provider(self):
        cases = [
            ("https://canariasterecarga.com/es/punto-de-recarga-hotel-villa-del-conde/", "Canarias te recarga", "station_page"),
            ("https://partner-next.gaiacharge.com/cpo/jump/fr-grn-ees-val-alb-artesans-1/CIRCUTOR-0786-1", "Gaia", "start_page"),
            ("https://www.openstreetmap.org/way/123", "OpenStreetMap", "map_object"),
            ("https://map.openchargemap.io/?mode=details&id=123", "OpenChargeMap", "station_page"),
            ("https://map.openchargemap.io/?mode=details&poi=123", "OpenChargeMap", "station_page"),
            ("https://map.electromaps.com/en/p/123", "Electromaps", "station_page"),
            ("https://chargemap.com/public-charger.html", "Chargemap", "station_page"),
            ("https://chargemap.com/en-gb/charging-stations/123", "Chargemap", "station_page"),
            ("https://recarga.fenieenergia.es/punto-recarga/ABC123", "Feníe Energía", "provider_station_page"),
            ("https://www.tesla.com/es_es/findus/location/supercharger/valencia", "Tesla", "station_page"),
            ("https://www.telpark.com/es/ciudades/madrid/plaza-mayor/", "Telpark", "station_page"),
            ("https://www.interparking.es/es/aparcamientos/madrid/example/", "Interparking", "station_page"),
        ]
        for url, name, kind in cases:
            with self.subTest(url=url):
                self.assertEqual(station_source_for({"source": "Wrong generic label", "source_url": url}), {"name": name, "url": url, "kind": kind})

    def test_start_page_is_preferred_over_a_directory_page(self):
        gaia = "https://partner-next.gaiacharge.com/cpo/jump/site/connector"
        result = station_source_for({
            "primary_source": {"url": "https://www.openstreetmap.org/node/123", "kind": "map_object"},
            "connectors": [{"source_url": gaia}],
        })
        self.assertEqual(result, {"name": "Gaia", "url": gaia, "kind": "start_page"})

    def test_generic_maps_lists_feeds_and_unsafe_urls_never_claim_an_exact_station(self):
        urls = [
            "https://canariasterecarga.com/", "https://canariasterecarga.com/es/",
            "https://canariasterecarga.com/es/mapa/", "https://canariasterecarga.com/es/puntos-de-recarga/",
            "https://canariasterecarga.com/es/contacto/", "https://canariasterecarga.com/es/mi-cuenta/",
            "https://canariasterecarga.com/es/mapa/punto-de-recarga-hotel/",
            "https://map.openchargemap.io/", "https://map.openchargemap.io/?mode=details",
            "https://map.openchargemap.io/?mode=details&id=123&poi=456", "https://map.electromaps.com/",
            "https://chargemap.com/en-gb/charging-stations/", "https://chargemap.com/index.html", "https://chargemap.com/contact.html", "https://recarga.fenieenergia.es/punto-recarga/todos",
            "https://www.mapareve.es/", "https://www.endesaxway.com/", "https://www.tesla.com/es_es/findus",
            "https://www.interparking.es/es/encuentra-un-aparcamiento/",
            "javascript:alert(1)", "https://user:secret@canariasterecarga.com/es/p123/",
            "https://127.0.0.1/stations/123", "https://[::1]/stations/123", "https://example.local/stations/123",
            "https://canariasterecarga.com.evil.test/es/p123/", "https://evil.test/canariasterecarga.com/es/p123/",
            "https://map.electromaps.com/en/p/123/../", "https://map.electromaps.com/en/p/123/%2e%2e/",
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertIsNone(station_source_for({"source": "Directory page", "source_url": url}))

    def test_other_explicit_station_pages_require_specific_non_dataset_paths(self):
        for path in ("/", "/es/", "/en-gb/", "/map", "/stations", "/feed/data.csv", "/regulations/prices.pdf"):
            with self.subTest(path=path):
                self.assertIsNone(station_source_for({"primary_source": {"name": "City chargers", "url": "https://example.org" + path, "kind": "station_page"}}))
        self.assertEqual(station_source_for({"primary_source": {"name": "City chargers", "url": "https://example.org/stations/123", "kind": "station_page"}}),
                         {"name": "City chargers", "url": "https://example.org/stations/123", "kind": "station_page"})

    def test_ocm_normalization_cannot_extract_a_trusted_link_from_an_unrelated_url(self):
        for url in ("https://evil.test/?next=https://openchargemap.org/site/poi/details/123",
                    "https://evil.test/https://openchargemap.org/site/poi/details/123",
                    "https://openchargemap.org/site/poi/details/123evil"):
            with self.subTest(url=url):
                self.assertEqual(normalize_public_source_url(url), url)
        self.assertEqual(normalize_public_source_url("https://openchargemap.org/site/poi/details/123?foo=bar"),
                         "https://map.openchargemap.io/?mode=details&id=123")

    def test_runtime_and_compact_summaries_preserve_action_without_changing_truth(self):
        item = {
            "id": "canarias:hourly", "name": "Hourly charger", "lat": 28.4, "lon": -16.5, "lead": True,
            "source": "Canarias te recarga public map", "source_url": "https://canariasterecarga.com/es/mapa/",
            "reliability": "directory-page", "price_min": None, "is_free": False,
            "tariff_details": {"source_url": "https://canariasterecarga.com/es/p123/", "hour_eur": 2, "minimum_eur": 2},
        }
        with patch("server.station_source_for", return_value=None):
            baseline = server.enrich_station(deepcopy(item), live=False)
        actual = server.enrich_station(deepcopy(item), live=False)
        expected = {"name": "Canarias te recarga", "url": "https://canariasterecarga.com/es/p123/", "kind": "station_page"}
        self.assertEqual(actual["station_source"], expected)
        baseline.pop("station_source")
        self.assertEqual({key: value for key, value in actual.items() if key != "station_source"}, baseline)
        self.assertEqual(server.compact_station_for_ui(actual)["station_source"], expected)
        self.assertEqual(server.summary_station_for_ui(actual, "snapshot")["station_source"], expected)
        self.assertFalse(actual["verification"]["price_verified"])
        self.assertIsNone(actual["price_min"])


if __name__ == "__main__":
    unittest.main()
