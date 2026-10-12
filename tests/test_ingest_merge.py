#!/usr/bin/env python3
"""Cross-source near-duplicate merge honesty tests (ingest_sources.merge_cross_source_duplicates)."""
from __future__ import annotations

import sys
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ingest_sources as ing  # noqa: E402


def rec(id_: str, source: str, url: str, lat: float, lon: float, price=None, **over):
    r = {
        "id": id_,
        "name": f"Station {id_}",
        "lat": lat,
        "lon": lon,
        "source": source,
        "source_url": url,
        "sources": [{"name": source, "url": url, "evidence": "e"}],
        "price_min": price,
        "is_free": price == 0,
        "is_cheap": price is not None and price <= 0.25,
        "reliability": over.pop("reliability", "community-cost"),
        "parking": over.pop("parking", {"kind": "unknown"}),
        "connector_labels": over.pop("connector_labels", ["Type 2"]),
    }
    r.update(over)
    return r


class CrossSourceMergeTests(unittest.TestCase):
    def test_primary_source_contract_classifies_exact_permitted_pages(self) -> None:
        gaia = ing.primary_source_for({
            "source": "Gaia public start-session page",
            "source_url": "https://www.gaiacharge.com/cpo/jump/abc/start-session/1",
            "sources": [],
        })
        electromaps = ing.primary_source_for({
            "source": "Electromaps web-map API",
            "source_url": "https://map.electromaps.com/en/p/123",
            "sources": [],
        })
        lidl = ing.primary_source_for({
            "source": "Lidl official free list",
            "source_url": "https://www.lidl.es/es/electromovilidad/s10021341",
            "sources": [],
        })

        self.assertEqual(gaia["kind"], "start_page")
        self.assertEqual(gaia["quality"], "exact")
        self.assertEqual(gaia["action"], "Check/start Gaia")
        self.assertEqual(electromaps["kind"], "station_page")
        self.assertEqual(electromaps["quality"], "exact")
        self.assertEqual(lidl["kind"], "official_list")
        self.assertEqual(lidl["quality"], "list")
        self.assertIn("locator/list/homepage", lidl["warning"])

    def test_add_record_persists_primary_source(self) -> None:
        records = []
        ing.add_record(
            records,
            source="OpenChargeMap public snapshot",
            source_url="https://map.openchargemap.io/?mode=details&poi=123",
            name="OCM charger",
            lat=39.47,
            lon=-0.38,
            price_min=0,
            connectors=[ing.connector_ref("Type 2", power_kw=22, source_url="https://map.openchargemap.io/?mode=details&poi=123")],
            reliability="community-free",
            evidence="OpenChargeMap station page with connector details.",
        )

        self.assertEqual(records[0]["primary_source"]["kind"], "station_page")
        self.assertEqual(records[0]["primary_source"]["quality"], "exact")
        self.assertEqual(records[0]["primary_source"]["action"], "Check OCM")

    def test_add_record_persists_location_claim_for_public_source_coordinate(self) -> None:
        records = []
        ing.add_record(
            records,
            source="València municipal OpenData",
            source_url="https://geoportal.valencia.es/arcgis/rest/services/EV/MapServer/0/1",
            name="Municipal station",
            lat=39.47,
            lon=-0.38,
            price_min=0,
            connectors=[ing.connector_ref("Type 2", power_kw=22, source_url="https://geoportal.valencia.es/arcgis/rest/services/EV/MapServer/0/1")],
            reliability="official-free",
            evidence="Municipal open-data station coordinate.",
        )

        claim = records[0]["location_claim"]
        self.assertEqual(claim["precision"], "official_data_coordinate")
        self.assertEqual(claim["method"], "official_open_data_coordinate")
        self.assertEqual(claim["url"], "https://geoportal.valencia.es/arcgis/rest/services/EV/MapServer/0/1")
        self.assertEqual(claim["lat"], 39.47)
        self.assertEqual(claim["lon"], -0.38)

    def test_lidl_street_geocode_location_claim_stays_cautious(self) -> None:
        records = []
        ing.add_record(
            records,
            source="Lidl official free list",
            source_url="https://www.lidl.es/es/electromovilidad/s10021341",
            name="Lidl street geocode",
            lat=39.6836874,
            lon=-0.2702085,
            price_min=0,
            reliability="official-free-policy",
            evidence="Official Lidl charger policy.",
            raw={"coordinate_source": "nominatim_street"},
        )

        claim = records[0]["location_claim"]
        self.assertEqual(claim["precision"], "geocoded_street")
        self.assertEqual(claim["method"], "nominatim_street")
        self.assertIn("street-level geocoding", claim["summary"])

    def test_add_record_persists_hardware_claim(self) -> None:
        records = []
        ing.add_record(
            records,
            source="OpenChargeMap public snapshot",
            source_url="https://map.openchargemap.io/?mode=details&poi=520",
            name="OCM hardware station",
            lat=39.47,
            lon=-0.38,
            price_min=0,
            connectors=[
                ing.connector_ref("Type 2", count=2, power_kw=22, status="OPERATIONAL", source_url="https://map.openchargemap.io/?mode=details&poi=520"),
                ing.connector_ref("Schuko", count=1, power_kw=3.7, status="OPERATIONAL", source_url="https://map.openchargemap.io/?mode=details&poi=520"),
            ],
            reliability="community-free",
            evidence="OpenChargeMap snapshot exposes connector inventory.",
            raw={"connector_status_fields": True, "connectors": [{"type": "Type 2"}]},
        )

        claim = records[0]["hardware_claim"]
        self.assertEqual(claim["port_count"], 3)
        self.assertEqual(claim["connector_types"], ["Schuko", "Type 2"])
        self.assertEqual(claim["max_power_kw"], 22)
        self.assertEqual(claim["connector_power_kw"], [3.7, 22])
        self.assertTrue(claim["station_status_evidence"])
        self.assertTrue(claim["connector_status_evidence"])
        self.assertFalse(claim["live_status_integrated"])

    def test_cross_source_merge_recomputes_hardware_claim_from_adopted_inventory(self) -> None:
        official = rec("l", "Lidl official free list", "https://www.lidl.es/es/electromovilidad/s10021341",
                       39.68369, -0.27021, price=0,
                       name="Lidl — Avda. Palmosa",
                       reliability="official-free-policy",
                       connector_labels=[],
                       connectors=[],
                       raw={"coordinate_source": "nominatim_street", "official_query": "Lidl Avda. Palmosa 46500"})
        directory = rec("e", "Electromaps web-map API", "https://map.electromaps.com/en/p/830568",
                        39.68380, -0.27035, price=0,
                        name="Lidl Sagunto",
                        address="Avda. Palmosa 46500 Sagunto",
                        reliability="directory-map-free",
                        connector_labels=["Type 2"],
                        connectors=[ing.connector_ref("Type 2", count=1, power_kw=22, status="UNKNOWN", source_url="https://map.electromaps.com/en/p/830568")],
                        raw={"offline_status": "OTHER", "connector_status_fields": True, "addr:postcode": "46500"})

        merged, _ = ing.merge_cross_source_duplicates([official, directory])

        self.assertEqual(len(merged), 1)
        claim = merged[0]["hardware_claim"]
        self.assertEqual(claim["port_count"], 1)
        self.assertEqual(claim["connector_types"], ["Type 2"])
        self.assertEqual(claim["max_power_kw"], 22)
        self.assertEqual(claim["url"], "https://map.electromaps.com/en/p/830568")

    def test_add_record_persists_access_claim_with_paid_public_parking(self) -> None:
        records = []
        ing.add_record(
            records,
            source="València municipal OpenData",
            source_url="https://geoportal.valencia.es/arcgis/rest/services/EV/MapServer/0/2",
            name="Municipal paid parking station",
            lat=38.8186,
            lon=0.0177,
            price_min=0,
            connectors=[ing.connector_ref("Type 2", power_kw=22, source_url="https://geoportal.valencia.es/arcgis/rest/services/EV/MapServer/0/2")],
            reliability="official-free",
            evidence="Municipal open-data point.",
            parking_kind="paid_or_metered",
            parking_note="Municipal data classifies this point as a parking facility.",
            raw={"propiedad": 1},
        )

        claim = records[0]["access_claim"]
        self.assertEqual(claim["level"], "public")
        self.assertEqual(claim["parking_kind"], "paid_or_metered")
        self.assertTrue(claim["parking_paid"])
        self.assertFalse(claim["private_or_restricted"])

    def test_add_record_persists_access_claim_with_private_restriction(self) -> None:
        records = []
        ing.add_record(
            records,
            source="València municipal OpenData",
            source_url="https://geoportal.valencia.es/arcgis/rest/services/EV/MapServer/0/3",
            name="Municipal private station",
            lat=40.33499,
            lon=-3.71634,
            price_min=0,
            connectors=[ing.connector_ref("Type 2", power_kw=22, source_url="https://geoportal.valencia.es/arcgis/rest/services/EV/MapServer/0/3")],
            reliability="official-free",
            evidence="Municipal open-data point.",
            parking_kind="paid_or_metered",
            parking_note="Municipal data classifies this point as a parking facility.",
            raw={"propiedad": 2},
        )

        claim = records[0]["access_claim"]
        self.assertEqual(claim["level"], "restricted")
        self.assertEqual(claim["ownership"], "private")
        self.assertTrue(claim["parking_paid"])
        self.assertTrue(claim["private_or_restricted"])

    def test_source_integration_connector_status_flag_is_explicit_not_live(self) -> None:
        profile = ing.source_integration_profile(
            "OpenChargeMap public snapshot",
            "community-cost",
            "OPERATIONAL",
            {"connector_status_fields": True},
        )

        self.assertEqual(profile["mode"], "community_snapshot")
        self.assertTrue(profile["station_status_fields"])
        self.assertTrue(profile["connector_status_fields"])
        self.assertFalse(profile["live_status_integrated"])

    def test_ocm_connector_status_counts_as_station_evidence_when_station_unknown(self) -> None:
        profile = ing.source_integration_profile(
            "OpenChargeMap public snapshot",
            "community-cost",
            "UNKNOWN",
            {"connector_status_fields": True},
        )

        self.assertEqual(profile["mode"], "community_snapshot")
        self.assertTrue(profile["station_status_fields"])
        self.assertTrue(profile["connector_status_fields"])
        self.assertFalse(profile["live_status_integrated"])

    def test_source_integration_station_status_is_separate_from_live(self) -> None:
        profile = ing.source_integration_profile(
            "Chargemap public map API",
            "directory-map-free",
            "VERIFY",
            {"availability_status": "AVAILABLE", "connectors": [{"type": "Type 2"}]},
        )

        self.assertEqual(profile["mode"], "directory_pool_status")
        self.assertTrue(profile["station_status_fields"])
        self.assertTrue(profile["connector_status_fields"])
        self.assertFalse(profile["live_status_integrated"])

    def test_electromaps_offline_status_counts_as_station_evidence_not_live(self) -> None:
        profile = ing.source_integration_profile(
            "Electromaps web-map API",
            "directory-map-free",
            "VERIFY",
            {"offline_status": "OTHER", "online_status": "UNKNOWN", "connector_status_fields": True},
        )

        self.assertEqual(profile["mode"], "directory_connector_status")
        self.assertTrue(profile["station_status_fields"])
        self.assertTrue(profile["connector_status_fields"])
        self.assertFalse(profile["live_status_integrated"])

    def test_osm_check_date_counts_as_station_evidence_not_live(self) -> None:
        profile = ing.source_integration_profile(
            "OpenStreetMap public tags",
            "community-free",
            "VERIFY",
            {"check_date": "2026-01-08"},
        )

        self.assertEqual(profile["mode"], "community_tags")
        self.assertTrue(profile["station_status_fields"])
        self.assertFalse(profile["connector_status_fields"])
        self.assertFalse(profile["live_status_integrated"])

    def test_osm_opening_hours_counts_as_station_schedule_not_live(self) -> None:
        profile = ing.source_integration_profile(
            "OpenStreetMap public tags",
            "community-free",
            "VERIFY",
            {"opening_hours": "24/7"},
        )

        self.assertEqual(profile["mode"], "community_tags")
        self.assertTrue(profile["station_status_fields"])
        self.assertFalse(profile["connector_status_fields"])
        self.assertFalse(profile["live_status_integrated"])
        self.assertIn("schedule", profile["note"])

    def test_osm_socket_tags_count_as_connector_fields_not_live(self) -> None:
        profile = ing.source_integration_profile(
            "OpenStreetMap public tags",
            "community-free",
            "VERIFY",
            {"socket:type2": "2", "socket:type2:output": "22 kW"},
        )

        self.assertEqual(profile["mode"], "community_tags")
        self.assertFalse(profile["station_status_fields"])
        self.assertTrue(profile["connector_status_fields"])
        self.assertFalse(profile["live_status_integrated"])

    def test_osm_description_connector_text_counts_as_connector_fields_not_live(self) -> None:
        profile = ing.source_integration_profile(
            "OpenStreetMap public tags",
            "community-free",
            "VERIFY",
            {"description": "CCS Combo 50kW, CHAdeMO 50kW, TYPE 2 43kW"},
        )

        self.assertEqual(profile["mode"], "community_tags")
        self.assertFalse(profile["station_status_fields"])
        self.assertTrue(profile["connector_status_fields"])
        self.assertFalse(profile["live_status_integrated"])

    def test_osm_source_timestamp_counts_as_source_freshness_not_live(self) -> None:
        profile = ing.source_integration_profile(
            "OpenStreetMap public tags",
            "community-free",
            "VERIFY",
            {"osm_last_modified": "2026-07-01T12:34:56Z"},
        )

        self.assertEqual(profile["mode"], "community_tags")
        self.assertTrue(profile["station_status_fields"])
        self.assertFalse(profile["connector_status_fields"])
        self.assertFalse(profile["live_status_integrated"])
        self.assertIn("source freshness", profile["note"])

    def test_raw_last_seen_accepts_osm_last_modified(self) -> None:
        self.assertEqual(
            ing.raw_last_seen({"osm_last_modified": "2026-07-01T12:34:56Z"}),
            "2026-07-01T12:34:56Z",
        )

    def test_panoramax_osm_tags_resolve_to_public_photo_assets(self) -> None:
        pic_id = "2985f4a9-5ead-4259-be2d-69c0d757e45a"
        calls = []
        original_cached_json = ing.cached_json

        def fake_cached_json(url, cache_name, ttl_hours=24, **kw):
            calls.append((url, cache_name, ttl_hours, kw))
            return {
                "assets": {
                    "sd": {"href": "https://panoramax.example/sd.jpg"},
                    "thumb": {"href": "https://panoramax.example/thumb.jpg"},
                },
                "links": [{"rel": "self", "href": "https://api.panoramax.xyz/api/collections/c/items/p"}],
                "properties": {"license": "CC-BY-SA-4.0", "datetime": "2023-01-27T11:02:25+00:00"},
            }

        ing.cached_json = fake_cached_json
        try:
            photos = ing.panoramax_photos_from_tags(
                {
                    "panoramax": pic_id,
                    "panoramax:0": pic_id,
                    "panoramax:bad": "not-a-picture-id",
                },
                "https://www.openstreetmap.org/node/10096232070",
            )
        finally:
            ing.cached_json = original_cached_json

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(photos), 1)
        self.assertEqual(photos[0]["url"], "https://panoramax.example/sd.jpg")
        self.assertEqual(photos[0]["thumbnail_url"], "https://panoramax.example/thumb.jpg")
        self.assertEqual(photos[0]["source"], "Panoramax public image")
        self.assertEqual(photos[0]["source_url"], "https://api.panoramax.xyz/api/collections/c/items/p")
        self.assertEqual(photos[0]["license"], "CC-BY-SA-4.0")

    def test_osm_description_connector_rows_keep_explicit_types_and_power(self) -> None:
        rows = ing.osm_description_connector_rows(
            "compatible amb els estàndards CHAdeMO, CCS i Modo 3 amb connector de tipus 2: "
            "- CCS Combo 500V / 120A / 50.00kW - CHAdeMO 480V / 120A / 50.00kW - TYPE 2 43.00kW",
            "https://www.openstreetmap.org/node/6255533878",
        )

        by_label = {row["label"]: row for row in rows}
        self.assertEqual(set(by_label), {"CCS2", "CHAdeMO", "Type 2"})
        self.assertEqual(by_label["CCS2"]["power_kw"], 50.0)
        self.assertEqual(by_label["CHAdeMO"]["power_kw"], 50.0)
        self.assertEqual(by_label["Type 2"]["power_kw"], 43.0)
        self.assertIn("CCS Combo 500V", by_label["CCS2"]["raw"]["matched"])
        self.assertIn("CHAdeMO 480V", by_label["CHAdeMO"]["raw"]["matched"])
        self.assertIn("TYPE 2 43.00kW", by_label["Type 2"]["raw"]["matched"])
        self.assertTrue(all(row["raw"]["basis"] == "OSM description connector text" for row in rows))

    def test_osm_micromobility_only_rows_are_not_car_charger_evidence(self) -> None:
        self.assertTrue(ing.osm_is_micromobility_only({"amenity": "charging_station", "fee": "no", "bicycle": "yes"}))
        self.assertTrue(ing.osm_is_micromobility_only({"amenity": "charging_station", "fee": "no", "small_electric_vehicle": "yes", "operator": "Carrefour"}))
        self.assertFalse(ing.osm_is_micromobility_only({"amenity": "charging_station", "fee": "no", "bicycle": "yes", "motorcar": "yes"}))
        self.assertFalse(ing.osm_is_micromobility_only({"amenity": "charging_station", "fee": "no", "bicycle": "yes", "socket:type2": "2"}))
        self.assertFalse(ing.osm_is_micromobility_only({"amenity": "charging_station", "fee": "no", "bicycle": "yes", "description": "CCS and CHAdeMO charger"}))

    def test_osm_fee_only_rows_are_not_car_charger_evidence(self) -> None:
        self.assertFalse(ing.osm_has_car_charger_evidence({"amenity": "charging_station", "fee": "no", "access": "yes"}, []))
        self.assertFalse(ing.osm_has_car_charger_evidence({"amenity": "charging_station", "fee": "no", "capacity": "4"}, []))
        self.assertTrue(ing.osm_has_car_charger_evidence({"amenity": "charging_station", "fee": "no", "capacity": "4", "motorcar": "yes"}, []))
        self.assertTrue(ing.osm_has_car_charger_evidence({"amenity": "charging_station", "fee": "no", "socket:type2": "2"}, []))
        self.assertTrue(ing.osm_has_car_charger_evidence({"amenity": "charging_station", "fee": "no", "motorcar": "yes"}, []))
        self.assertTrue(ing.osm_has_car_charger_evidence({"amenity": "charging_station", "fee": "no"}, [{"label": "Type 2"}]))

    def test_openchargemap_unknown_connector_label_is_not_a_type(self) -> None:
        self.assertIsNone(ing.openchargemap_connector_label("Unknown"))
        self.assertIsNone(ing.openchargemap_connector_label(""))
        self.assertEqual(ing.openchargemap_connector_label("Type 2 (Socket Only)"), "Type 2")
        self.assertEqual(ing.openchargemap_connector_label("CCS (Type 2) Combo"), "CCS2")
        self.assertEqual(ing.openchargemap_connector_label("CEE 7/4 - Schuko - Type F"), "CEE 7/4 - Schuko - Type F")

    def test_openchargemap_empty_free_row_needs_route_evidence(self) -> None:
        weak = {
            "UsageCost": "Free",
            "AddressInfo": {"Title": "Montmeló", "Latitude": 40.46, "Longitude": -3.74},
            "Connections": [],
            "UsageType": {"Title": "(Unknown)"},
            "DateLastVerified": None,
        }
        self.assertFalse(ing.openchargemap_has_minimum_route_evidence(weak))
        self.assertTrue(ing.openchargemap_has_minimum_route_evidence({**weak, "Connections": [{"ID": 1}]}))
        self.assertTrue(ing.openchargemap_has_minimum_route_evidence({**weak, "AddressInfo": {"Title": "X", "Town": "Vilalba"}}))
        self.assertTrue(ing.openchargemap_has_minimum_route_evidence({**weak, "DateLastVerified": "2024-01-01T00:00:00Z"}))

    def test_openchargemap_weak_count_only_lead_needs_context(self) -> None:
        weak = {"Connections": [], "UsageType": {"Title": "(Unknown)"}, "DateLastVerified": None}
        self.assertTrue(ing.openchargemap_is_weak_count_only_lead(weak, "unknown"))
        self.assertFalse(ing.openchargemap_is_weak_count_only_lead({**weak, "Connections": [{"ID": 1}]}, "unknown"))
        self.assertFalse(ing.openchargemap_is_weak_count_only_lead({**weak, "DateLastVerified": "2024-01-01"}, "unknown"))
        self.assertFalse(ing.openchargemap_is_weak_count_only_lead({**weak, "UsageType": {"Title": "Public"}}, "unknown"))
        self.assertFalse(ing.openchargemap_is_weak_count_only_lead(weak, "retail_parking"))

    def test_openchargemap_needs_recognized_connector_type_evidence(self) -> None:
        self.assertFalse(ing.openchargemap_has_connector_type_evidence({"Connections": []}))
        self.assertFalse(ing.openchargemap_has_connector_type_evidence({
            "Connections": [{"ConnectionType": {"Title": "Unknown"}}],
        }))
        self.assertTrue(ing.openchargemap_has_connector_type_evidence({
            "Connections": [{"ConnectionType": {"Title": "Type 2 (Socket Only)"}}],
        }))

    def test_openchargemap_needs_exact_power_kw_evidence(self) -> None:
        self.assertFalse(ing.openchargemap_has_power_evidence({"Connections": []}))
        self.assertFalse(ing.openchargemap_has_power_evidence({
            "Connections": [{"ConnectionType": {"Title": "Type 2 (Socket Only)"}, "LevelID": 2}],
        }))
        self.assertFalse(ing.openchargemap_has_power_evidence({
            "Connections": [{"ConnectionType": {"Title": "CEE 7/4 - Schuko - Type F"}, "Voltage": 220}],
        }))
        self.assertTrue(ing.openchargemap_has_power_evidence({
            "Connections": [{"ConnectionType": {"Title": "Type 2 (Socket Only)"}, "PowerKW": 7.4}],
        }))

    def test_raw_last_seen_accepts_openchargemap_status_update(self) -> None:
        self.assertEqual(
            ing.raw_last_seen({"LastVerified": None, "DateLastStatusUpdate": {"$date": "2019-11-06T09:54:00Z"}}),
            "2019-11-06T09:54:00Z",
        )

    def test_openchargemap_connector_ref_keeps_public_status_provenance(self) -> None:
        row = ing.connector_ref(
            "CCS2",
            count=4,
            power_kw=50.0,
            status="OPERATIONAL",
            source_url="https://map.openchargemap.io/?mode=details&id=77645",
            connector_id=112169,
            raw={
                "StatusTypeID": 50,
                "StatusTypeTitle": "Operational",
                "StatusTypeIsOperational": True,
                "LevelID": 3,
                "LevelTitle": "Level 3:  High (Over 40kW)",
                "CurrentTypeID": 30,
                "CurrentTypeTitle": "DC",
            },
        )

        self.assertEqual(row["source_url"], "https://map.openchargemap.io/?mode=details&id=77645")
        self.assertEqual(row["raw"]["StatusTypeID"], 50)
        self.assertTrue(row["raw"]["StatusTypeIsOperational"])
        self.assertEqual(row["raw"]["CurrentTypeTitle"], "DC")

    def test_chargemap_needs_exact_connector_power_evidence(self) -> None:
        self.assertFalse(ing.chargemap_has_power_evidence({"charging_connectors": []}))
        self.assertFalse(ing.chargemap_has_power_evidence({
            "speed": {"id": 4, "icon": "fast.svg"},
            "charging_connectors": [{"connector_type": {"name": "Type 2"}, "count": 2}],
        }))
        self.assertTrue(ing.chargemap_has_power_evidence({
            "charging_connectors": [{"connector_type": {"name": "Type 2"}, "power_max": 7.4}],
        }))
        self.assertEqual(ing.chargemap_connector_power({"kw": "11,0"}), 11.0)

    def test_openchargemap_country_iso_uses_source_country(self) -> None:
        self.assertEqual(ing.openchargemap_country_iso({"AddressInfo": {"Country": {"ISOCode": "es"}}}), "ES")
        self.assertEqual(ing.openchargemap_country_iso({"AddressInfo": {"Country": {"ISOCode": "FR"}}}), "FR")
        self.assertEqual(ing.openchargemap_country_iso({"AddressInfo": {}}), "")

    def test_count_int_keeps_numeric_one_when_default_is_zero(self) -> None:
        self.assertEqual(ing.count_int(1, default=0), 1)
        self.assertEqual(ing.count_int("1", default=0), 1)
        self.assertEqual(ing.count_int(True, default=0), 0)

    def test_port_count_can_be_known_without_claiming_connector_type(self) -> None:
        rows = []

        ing.add_record(
            rows,
            source="OpenChargeMap public snapshot",
            source_url="https://map.openchargemap.io/?mode=details&id=1",
            name="OCM count-only charger",
            lat=39.47,
            lon=-0.37,
            price_min=0.0,
            connector_labels=[],
            connectors=[],
            port_count=2,
            evidence="OCM NumberOfPoints=2; no Connections payload",
            raw={"NumberOfPoints": 2},
        )

        self.assertEqual(rows[0]["total_evse"], 2)
        self.assertIsNone(rows[0]["connector_count"])
        self.assertEqual(rows[0]["connector_labels"], [])
        self.assertEqual(rows[0]["connectors"], [])
        self.assertFalse(rows[0]["tesla_ok"])

    def test_free_station_without_connector_evidence_is_not_tesla_compatible(self) -> None:
        rows = []

        ing.add_record(
            rows,
            source="OpenStreetMap public tags",
            source_url="https://www.openstreetmap.org/node/1",
            name="OSM socketless charger",
            lat=39.47,
            lon=-0.37,
            price_min=0.0,
            connector_labels=[],
            connectors=[],
            evidence="OSM fee=no; no socket tags",
            raw={"amenity": "charging_station", "fee": "no"},
        )

        self.assertEqual(rows[0]["connector_labels"], [])
        self.assertFalse(rows[0]["tesla_ok"])

    def test_capacity_only_station_preserves_count_without_connector_type(self) -> None:
        rows = []

        ing.add_record(
            rows,
            source="OpenStreetMap public tags",
            source_url="https://www.openstreetmap.org/node/2",
            name="OSM capacity-only charger",
            lat=39.47,
            lon=-0.37,
            price_min=0.0,
            connector_labels=[],
            connectors=[],
            port_count=4,
            evidence="OSM fee=no; capacity=4; no socket tags",
            raw={"amenity": "charging_station", "fee": "no", "capacity": "4"},
        )

        self.assertEqual(rows[0]["total_evse"], 4)
        self.assertIsNone(rows[0]["connector_count"])
        self.assertEqual(rows[0]["connector_labels"], [])
        self.assertFalse(rows[0]["tesla_ok"])

    def test_osm_access_yes_marks_free_station_public(self) -> None:
        rows = []

        ing.add_record(
            rows,
            source="OpenStreetMap public tags",
            source_url="https://www.openstreetmap.org/node/3",
            name="OSM public charger",
            lat=39.47,
            lon=-0.37,
            price_min=0.0,
            connector_labels=[],
            connectors=[],
            evidence="OSM fee=no; access=yes",
            parking_kind="street_or_municipal",
            raw={"amenity": "charging_station", "fee": "no", "access": "yes"},
        )

        self.assertEqual(rows[0]["access"]["level"], "public")
        self.assertIn("access=yes", rows[0]["access"]["note"])

    def test_customer_access_context_stays_customer_not_public(self) -> None:
        rows = []

        ing.add_record(
            rows,
            source="OpenStreetMap public tags",
            source_url="https://www.openstreetmap.org/node/4",
            name="OSM customer charger",
            lat=39.47,
            lon=-0.37,
            price_min=0.0,
            connector_labels=[],
            connectors=[],
            evidence="OSM fee=no; access=destination",
            parking_kind="retail_parking",
            raw={"amenity": "charging_station", "fee": "no", "access": "destination"},
        )

        self.assertEqual(rows[0]["access"]["level"], "customer")
        self.assertIn("access=destination", rows[0]["access"]["note"])

    def test_structured_private_ownership_marks_access_restricted_even_without_text(self) -> None:
        rows = []

        ing.add_record(
            rows,
            source="València municipal OpenData",
            source_url="https://geoportal.valencia.es/arcgis/rest/services/EV/MapServer/0/4",
            name="Municipal private point",
            lat=39.47,
            lon=-0.37,
            price_min=0.0,
            connector_labels=["Type 2"],
            evidence="Municipal structured ownership field",
            reliability="official-free",
            parking_kind="street_or_municipal",
            raw={"propiedad": 2},
        )

        self.assertEqual(rows[0]["access"]["level"], "restricted")
        self.assertEqual(rows[0]["access"]["label"], "Private point")

    def test_street_and_free_parking_context_marks_access_public(self) -> None:
        self.assertEqual(
            ing.access_from_evidence("", "community-cost", "street_or_municipal")["level"],
            "public",
        )
        self.assertEqual(
            ing.access_from_evidence("", "directory-map-free", "free_parking")["level"],
            "public",
        )

    def test_access_limited_text_still_wins_over_public_context(self) -> None:
        access = ing.access_from_evidence(
            "free for members only",
            "official-free",
            "street_or_municipal",
            {"access": "yes", "propiedad": 1},
        )

        self.assertEqual(access["level"], "restricted")

    def test_lidl_official_locator_does_not_invent_store_connector_types(self) -> None:
        html = """
        <p><strong>Valencia</strong></p><ul>
          <li><a href="https://www.google.com/maps/search/?api=1&amp;query=La+Eliana+-+C/Mar%C3%ADa+Zambrano,+8+(CP+46183)+VALENCIA">
            La Eliana - C/María Zambrano, 8 (CP 46183) - (1 cargador - Programa de Subvenciones MOVES)
          </a></li>
          <li><a href="https://www.google.com/maps/search/?api=1&amp;query=Valencia+-+Avda.+Maestro+Rodrigo,+41+(CP+46015)+VALENCIA">
            Valencia - Avda. Maestro Rodrigo, 41 (CP 46015) - (2 cargadores - Programa de Subvenciones MOVES II)
          </a></li>
        </ul>
        """.encode("utf-8")
        original_cached_fetch = ing.cached_fetch
        original_geocode = ing.nominatim_geocode

        def fake_cached_fetch(url, cache_name, ttl_hours=24, **kw):
            return html

        ing.cached_fetch = fake_cached_fetch
        ing.nominatim_geocode = lambda query: (39.48165, -0.40145)
        try:
            rows = []
            ing.source_lidl(rows)
        finally:
            ing.cached_fetch = original_cached_fetch
            ing.nominatim_geocode = original_geocode

        self.assertEqual(len(rows), 2)
        by_name = {row["name"]: row for row in rows}
        self.assertEqual(by_name["Lidl — La Eliana - C/María Zambrano, 8"]["total_evse"], 1)
        self.assertEqual(by_name["Lidl — Valencia - Avda. Maestro Rodrigo, 41"]["total_evse"], 2)
        for row in rows:
            self.assertIsNone(row["connector_count"])
            self.assertEqual(row["connector_labels"], [])
            self.assertEqual(row["connectors"], [])
            self.assertFalse(row["tesla_ok"])
            self.assertIn("store-specific connector types", row["insight"])
            self.assertIn("store-specific connector types", row["sources"][0]["evidence"])
            self.assertEqual(row["raw"]["connector_scope"], "network-level compatibility only; per-store connector types not listed")

    def test_agreeing_cross_family_pair_merges_and_unions_sources(self) -> None:
        a = rec("a", "OpenChargeMap public snapshot", "https://map.openchargemap.io/?id=1", 39.47000, -0.37000, price=0)
        b = rec("b", "Electromaps web-map API", "https://map.electromaps.com/en/p/9", 39.47010, -0.37010, price=0)
        merged, n = ing.merge_cross_source_duplicates([a, b])
        self.assertEqual(n, 1)
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]["sources"]), 2)

    def test_price_conflict_NEVER_merges(self) -> None:
        a = rec("a", "OpenChargeMap public snapshot", "https://map.openchargemap.io/?id=1", 39.47000, -0.37000, price=0)
        b = rec("b", "Electromaps web-map API", "https://map.electromaps.com/en/p/9", 39.47005, -0.37005, price=0.20)
        merged, n = ing.merge_cross_source_duplicates([a, b])
        self.assertEqual(n, 0)
        self.assertEqual(len(merged), 2)

    def test_same_family_neighbors_stay_separate(self) -> None:
        # One source listing two nearby points usually means two REAL chargers at a site.
        a = rec("a", "OpenChargeMap public snapshot", "https://map.openchargemap.io/?id=1", 39.47000, -0.37000, price=0)
        b = rec("b", "OpenChargeMap public snapshot", "https://map.openchargemap.io/?id=2", 39.47008, -0.37008, price=0)
        merged, n = ing.merge_cross_source_duplicates([a, b])
        self.assertEqual(n, 0)
        self.assertEqual(len(merged), 2)

    def test_exact_coordinate_ocm_rows_with_incompatible_localities_are_quarantined(self) -> None:
        zaragoza = rec(
            "ocm-zaragoza",
            "OpenChargeMap public snapshot",
            "https://map.openchargemap.io/?mode=details&id=85449",
            41.4088434,
            2.1397652,
            price=0,
            address="Calle Alfonso I 1, 50001 Zaragoza",
            city="Zaragoza",
            raw={"ID": 85449, "addr:postcode": "50001"},
        )
        tarragona = rec(
            "ocm-tarragona",
            "OpenChargeMap public snapshot",
            "https://map.openchargemap.io/?mode=details&id=86093",
            41.4088139,
            2.1397540,
            price=0,
            address="Rambla Nova 1, 43001 Tarragona",
            city="Tarragona",
            raw={"ID": 86093, "addr:postcode": "43001"},
        )

        self.assertEqual(ing.dedupe([zaragoza, tarragona]), [])

    def test_exact_coordinate_ocm_rows_sharing_one_postcode_survive(self) -> None:
        # OCM's Town field often holds a road marker ("Km 124"); one shared postcode is the
        # stronger place evidence, so the free listing must not vanish with its twin.
        casino = rec(
            "ocm-casino", "OpenChargeMap public snapshot", "https://map.openchargemap.io/?mode=details&id=100396",
            36.230654, -5.357473, price=0, city="San Roque", reliability="community-free",
            raw={"ID": 100396, "addr:postcode": "11360"},
        )
        road = rec(
            "ocm-road", "OpenChargeMap public snapshot", "https://map.openchargemap.io/?mode=details&id=163968",
            36.230654, -5.357473, price=None, city="Km 124", reliability="community-price-unknown",
            raw={"ID": 163968, "addr:postcode": "11360"},
        )

        merged = ing.dedupe([casino, road])

        self.assertEqual([row["id"] for row in merged], ["ocm-casino", "ocm-road"])
        self.assertEqual({key for row in merged for key in ing.identity_keys(row)},
                         {("ocm-station", "100396"), ("ocm-station", "163968")})

    def test_same_family_rows_never_merge_through_a_third_source(self) -> None:
        # Two operators in one car park, both in the national registry, plus one map pin
        # between them: the pin joins one of them and the other keeps its own station.
        endesa = rec("nap-endesa", "DGT National Access Point charging registry", "https://nap.dgt.es/dataset/x",
                     28.454124, -16.290747, price=0.11, name="ENDESA X WAY — site", raw={"corroboration_only": True})
        wenea = rec("nap-wenea", "DGT National Access Point charging registry", "https://nap.dgt.es/dataset/x",
                    28.454140, -16.290770, price=0.39, name="Wenea — Parking La Multa", raw={"corroboration_only": True})
        pin = rec("canarias", "Canarias te recarga public map", "https://canariasterecarga.com/es/p00313",
                  28.454150, -16.290775, price=0.38, name="Parking HUC")

        merged, _ = ing.merge_cross_source_duplicates([endesa, wenea, pin])

        ids = sorted(row["id"] for row in merged)
        self.assertEqual(len(merged), 2)
        self.assertIn("nap-endesa", ids)
        self.assertIn("nap-wenea", ids)

    def test_far_apart_cross_family_stays_separate(self) -> None:
        a = rec("a", "OpenChargeMap public snapshot", "https://map.openchargemap.io/?id=1", 39.4700, -0.3700, price=0)
        b = rec("b", "Electromaps web-map API", "https://map.electromaps.com/en/p/9", 39.4720, -0.3700, price=0)  # ~220 m
        merged, n = ing.merge_cross_source_duplicates([a, b])
        self.assertEqual(n, 0)

    def test_same_retail_brand_can_merge_just_outside_base_radius(self) -> None:
        osm = rec("osm", "OpenStreetMap public tags", "https://www.openstreetmap.org/node/1",
                  39.5583911, -0.5068073, price=0, name="Lidl — La Eliana",
                  raw={"brand": "Lidl", "official_query": "La Eliana - C/María Zambrano, 8"}, connector_labels=[], connectors=[])
        em = rec("em", "Electromaps web-map API", "https://map.electromaps.com/en/p/185544",
                 39.55876, -0.50446, price=0, name="Lidl La Eliana",
                 connector_labels=["Type 2"],
                 connectors=[{"connector_id": "3905837", "label": "Type 2", "power_kw": 11, "count": 1}])

        merged, n = ing.merge_cross_source_duplicates([osm, em])

        self.assertEqual(n, 1)
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]["sources"]), 2)
        self.assertEqual(merged[0]["connector_labels"], ["Type 2"])
        self.assertEqual(merged[0]["connector_count"], 1)

    def test_same_retail_brand_without_place_match_does_not_merge_at_extended_radius(self) -> None:
        official = rec("l", "Lidl official free list", "https://empresa.lidl.es/puntos",
                       39.48165, -0.40145, price=0, name="Lidl — Valencia - Avda. Maestro Rodrigo, 41",
                       raw={"official_query": "Valencia - Avda. Maestro Rodrigo, 41"}, connector_labels=[], connectors=[])
        directory = rec("e", "Electromaps web-map API", "https://map.electromaps.com/en/p/185544",
                        39.48320, -0.40100, price=0, name="Lidl La Eliana",
                        connector_labels=["Type 2"],
                        connectors=[{"connector_id": "1", "label": "Type 2", "power_kw": 11, "count": 1}])

        merged, n = ing.merge_cross_source_duplicates([official, directory])

        self.assertEqual(n, 0)
        self.assertEqual(len(merged), 2)

    def test_shared_site_tokens_can_merge_nearby_cross_family_rows_and_media(self) -> None:
        official = rec("m", "València municipal OpenData", "https://geoportal.valencia.es/arcgis/rest/services/EV/MapServer/0/364",
                       40.659927, -4.705191, price=0,
                       name="Municipal — CENTRO DE EXPOSICIONES Y CONGRESOS LIENZO NORTE",
                       reliability="official-free",
                       connector_labels=["Type 2"],
                       connectors=[{"connector_id": "Mennekes", "label": "Type 2", "power_kw": 7.4, "count": 2}])
        directory = rec("o", "OpenChargeMap public snapshot", "https://map.openchargemap.io/?mode=details&id=57807",
                        40.659818, -4.705678, price=0,
                        name="Lienzo Norte",
                        reliability="directory-map-free",
                        connector_labels=["Type 2"],
                        media={"photos": [{"url": "https://example.test/lienzo.jpg", "source_url": "https://map.openchargemap.io/?mode=details&id=57807"}]})

        merged, n = ing.merge_cross_source_duplicates([official, directory])

        self.assertEqual(n, 1)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source"], "València municipal OpenData")
        self.assertEqual(len(merged[0]["sources"]), 2)
        self.assertEqual(merged[0]["media"]["photo_status"], "public_photo_available")
        self.assertEqual(merged[0]["media"]["photos"][0]["url"], "https://example.test/lienzo.jpg")

    def test_different_retail_brands_do_not_merge_by_shared_site_tokens(self) -> None:
        consum = rec("c", "Consum store locator", "https://www.consum.es/supermercados/alfara-del-patriarca-antonio-expolio/",
                     39.54290, -0.38721, price=0,
                     name="Consum — ALFARA DEL PATRIARCA",
                     reliability="official-charger-location-price-unknown",
                     raw={"brand": "Consum"})
        mercadona = rec("m", "Electromaps web-map API", "https://map.electromaps.com/en/p/217800",
                        39.54230, -0.38761, price=0,
                        name="Mercadona Alfara del Patriarca",
                        reliability="directory-map-free",
                        raw={"brand": "Mercadona"})

        merged, n = ing.merge_cross_source_duplicates([consum, mercadona])

        self.assertEqual(n, 0)
        self.assertEqual(len(merged), 2)

    def test_opposite_direction_superchargers_do_not_merge_by_shared_site_tokens(self) -> None:
        southbound = rec("s", "OpenStreetMap public tags", "https://www.openstreetmap.org/node/8894359350",
                         39.40067, -0.49312, price=0,
                         name="Torrent Southbound Supercharger",
                         reliability="community-map")
        northbound = rec("n", "Electromaps web-map API", "https://map.electromaps.com/en/p/174606",
                         39.40021, -0.49262, price=0,
                         name="Supercharger Torrent North, Spain",
                         reliability="directory-map-free")

        merged, n = ing.merge_cross_source_duplicates([southbound, northbound])

        self.assertEqual(n, 0)
        self.assertEqual(len(merged), 2)

    def test_strong_same_site_price_conflict_merges_with_claim_warning_and_media(self) -> None:
        official = rec("m", "València municipal OpenData", "https://geoportal.valencia.es/arcgis/rest/services/EV/MapServer/0/487",
                       38.853888888889, -0.50194444444444, price=0,
                       name="Municipal — Ayuntamiento de El Palomar",
                       reliability="official-free",
                       connector_labels=["Type 2"],
                       connectors=[{"connector_id": "Mennekes", "label": "Type 2", "power_kw": 16, "count": 2}],
                       charge_claim={"type": "free", "source": "València municipal OpenData",
                                     "url": "https://geoportal.valencia.es/arcgis/rest/services/EV/MapServer/0/487", "price_min": 0})
        directory = rec("e", "Electromaps web-map API", "https://map.electromaps.com/en/p/796458",
                        38.853966941236, -0.502011626958, price=0.20,
                        name="Ayuntamiento del Palomar",
                        reliability="directory-map-cheap",
                        connector_labels=["Type 2"],
                        media={"photos": [{"url": "https://example.test/palomar.jpg",
                                            "source_url": "https://map.electromaps.com/en/p/796458"}]},
                        charge_claim={"type": "cheap", "source": "Electromaps web-map API",
                                      "url": "https://map.electromaps.com/en/p/796458", "price_min": 0.20})

        merged, n = ing.merge_cross_source_duplicates([official, directory])

        self.assertEqual(n, 1)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source"], "València municipal OpenData")
        self.assertEqual(merged[0]["media"]["photo_status"], "public_photo_available")
        self.assertEqual(merged[0]["media"]["photos"][0]["url"], "https://example.test/palomar.jpg")
        self.assertEqual(merged[0]["claim_conflict"]["level"], "mixed_sources")
        self.assertIn("tariff", merged[0]["claim_conflict"]["summary"])
        self.assertEqual(
            {claim["price_min"] for claim in merged[0]["claim_conflict"]["charge_prices"]},
            {0.0, 0.20},
        )

    def test_price_conflict_still_blocks_weak_nearby_identity(self) -> None:
        consum = rec("c", "Consum store locator", "https://www.consum.es/supermercados/alfara-del-patriarca-antonio-expolio/",
                     39.54290, -0.38721, price=0,
                     name="Consum — ALFARA DEL PATRIARCA",
                     reliability="official-charger-location-price-unknown",
                     raw={"brand": "Consum"})
        mercadona = rec("m", "Electromaps web-map API", "https://map.electromaps.com/en/p/217800",
                        39.54295, -0.38725, price=0.20,
                        name="Mercadona Alfara del Patriarca",
                        reliability="directory-map-cheap",
                        raw={"brand": "Mercadona"})

        merged, n = ing.merge_cross_source_duplicates([consum, mercadona])

        self.assertEqual(n, 0)
        self.assertEqual(len(merged), 2)

    def test_merged_locator_preserves_directory_connector_status_metadata(self) -> None:
        locator = rec("l", "Lidl official free list", "https://empresa.lidl.es/puntos",
                      39.42596, -0.40498, price=0, name="Lidl Paiporta",
                      reliability="official-free-policy",
                      connector_labels=[],
                      connectors=[],
                      source_integration={
                          "mode": "official_locator",
                          "live_status_integrated": False,
                          "connector_status_fields": False,
                          "note": "Official locator.",
                      })
        directory = rec("e", "Electromaps web-map API", "https://map.electromaps.com/en/p/135318",
                        39.42642, -0.40514, price=0, name="Lidl Paiporta",
                        connector_labels=[],
                        connectors=[{"connector_id": "1", "label": "Type 2", "status": "WORKING", "power_kw": 11, "count": 1}],
                        source_integration={
                            "mode": "directory_connector_status",
                            "live_status_integrated": False,
                            "connector_status_fields": True,
                            "note": "Directory connector fields.",
                        })

        merged, n = ing.merge_cross_source_duplicates([locator, directory])

        self.assertEqual(n, 1)
        integration = merged[0]["source_integration"]
        self.assertTrue(integration["connector_status_fields"])
        self.assertFalse(integration["live_status_integrated"])
        self.assertEqual(integration["source_modes"], ["official_locator", "directory_connector_status"])
        self.assertEqual(merged[0]["connector_labels"], ["Type 2"])
        self.assertTrue(merged[0]["tesla_ok"])

    def test_electromaps_lidl_official_match_requires_exact_official_lidl_site(self) -> None:
        official = rec("l", "Lidl official free list", "https://empresa.lidl.es/puntos",
                       39.197363, -0.436888, price=0,
                       name="Lidl — Ronda del Calvari, nº33, 46680, Algemesí",
                       address="Ronda del Calvari, nº33, 46680, Algemesí",
                       reliability="official-free-policy",
                       raw={"brand": "Lidl"})
        detail = {
            "name": "Lidl Algemesí",
            "address": {
                "address": "Ronda del Calvari",
                "street_number": "51",
                "postal_code": "46680",
                "city": "Algemesí",
            },
        }

        match = ing.electromaps_lidl_official_match([official], detail, 39.19727647351, -0.436616492602)

        self.assertIs(match, official)
        self.assertIsNone(ing.electromaps_lidl_official_match([official], {"name": "Mercadona Algemesí"}, 39.19728, -0.43662))
        self.assertIsNone(ing.electromaps_lidl_official_match([official], detail, 39.23000, -0.43662))

    def test_electromaps_lidl_official_match_accepts_museros_parking_offset_only(self) -> None:
        museros = rec("m", "Lidl official free list", "https://empresa.lidl.es/puntos",
                      39.5634047, -0.3334377, price=0,
                      name="Lidl — Museros - C/Francesc de Vinatea, 7",
                      address="Museros - C/Francesc de Vinatea, 7 (CP 46136)",
                      reliability="official-free-policy",
                      raw={"brand": "Lidl"})
        palmosa = rec("p", "Lidl official free list", "https://empresa.lidl.es/puntos",
                      39.6836874, -0.2702085, price=0,
                      name="Lidl — Avda. Palmosa, s/n 46500 Valencia",
                      address="Avda. Palmosa, s/n 46500 Valencia",
                      reliability="official-free-policy",
                      raw={"brand": "Lidl", "official_query": "Sagunto - Avenida Palmosa, s/n, 46500", "coordinate_source": "nominatim_street"})
        exact_palmosa = rec("px", "Lidl official free list", "https://empresa.lidl.es/puntos",
                            39.6836874, -0.2702085, price=0,
                            name="Lidl — Avda. Palmosa, s/n 46500 Valencia",
                            address="Avda. Palmosa, s/n 46500 Valencia",
                            reliability="official-free-policy",
                            raw={"brand": "Lidl", "official_query": "Sagunto - Avenida Palmosa, s/n, 46500", "coordinate_source": "nominatim_geocode"})
        other_palmosa = rec("p2", "Lidl official free list", "https://empresa.lidl.es/puntos",
                            39.68280, -0.26950, price=0,
                            name="Lidl — Sagunto backup 46500",
                            address="Sagunto 46500",
                            reliability="official-free-policy",
                            raw={"brand": "Lidl", "official_query": "Sagunto backup 46500", "coordinate_source": "nominatim_street"})
        museros_detail = {
            "name": "Lidl Museros",
            "address": {
                "address": "Avinguda Constitució, 51, 46136, Museros",
                "street": "Avinguda Constitució",
                "street_number": "49",
                "postal_code": "46136",
                "city": "Museros",
            },
        }
        palmosa_detail = {
            "name": "Lidl",
            "address": {
                "address": "Carrer de Sant Lluís, 25, 46500 Sagunt",
                "street": "Carrer de Sant Lluís",
                "street_number": "140",
                "postal_code": "46500",
                "city": "Sagunt",
            },
        }

        self.assertIs(
            ing.electromaps_lidl_official_match([museros], museros_detail, 39.563323315852, -0.332477779228),
            museros,
        )
        self.assertIs(
            ing.electromaps_lidl_official_match([palmosa], palmosa_detail, 39.682255112156, -0.268916934729),
            palmosa,
        )
        self.assertIsNone(
            ing.electromaps_lidl_official_match([exact_palmosa], palmosa_detail, 39.682255112156, -0.268916934729)
        )
        self.assertIsNone(
            ing.electromaps_lidl_official_match([palmosa, other_palmosa], palmosa_detail, 39.682255112156, -0.268916934729)
        )

    def test_electromaps_existing_photo_match_requires_missing_photo_and_strong_identity(self) -> None:
        official = rec("m", "València municipal OpenData", "https://geoportal.valencia.es/arcgis/rest/services/EV/MapServer/0/500",
                       38.961639, -0.148179, price=0,
                       name="Municipal — Guardamar de la Safor",
                       reliability="official-free",
                       media={"photo_status": "no_public_photo", "photos": []})
        with_photo = rec("p", "València municipal OpenData", "https://geoportal.valencia.es/arcgis/rest/services/EV/MapServer/0/501",
                         38.961639, -0.148179, price=0,
                         name="Municipal — Guardamar de la Safor",
                         reliability="official-free",
                         media={"photo_status": "public_photo_available", "photos": [{"url": "https://example.test/existing.jpg"}]})
        detail = {
            "name": "Guardamar de la Safor",
            "image": {"l": "https://example.test/electromaps.jpg"},
            "address": {"address": "Guardamar de la Safor", "city": "Guardamar de la Safor"},
        }

        self.assertIs(
            ing.electromaps_existing_photo_match([official], detail, 38.96164, -0.14818),
            official,
        )
        self.assertIsNone(ing.electromaps_existing_photo_match([with_photo], detail, 38.96164, -0.14818))
        self.assertIsNone(ing.electromaps_existing_photo_match([official], detail, 38.98000, -0.14818))

    def test_electromaps_photo_match_can_use_osm_operator_identity_tokens(self) -> None:
        osm = rec("o", "OpenStreetMap public tags", "https://www.openstreetmap.org/node/6255533878",
                  38.9215122, -0.1168462, price=0,
                  name="ChargePoint",
                  reliability="community-free",
                  media={"photo_status": "no_public_photo", "photos": []},
                  raw={"operator": "Ajuntament d'Oliva (Ingeteam)"})
        detail = {
            "name": "Ajuntament d'Oliva Passeig",
            "image": {"l": "https://example.test/oliva.jpg"},
            "address": {
                "address": "Carrer del Pare Antoni Salelles, 10, 46780, Oliva",
                "city": "Oliva",
            },
        }

        self.assertIs(
            ing.electromaps_existing_photo_match([osm], detail, 38.921566326963, -0.116894356906),
            osm,
        )

    def test_electromaps_photo_match_accepts_strong_identity_with_small_map_offset(self) -> None:
        chargemap = rec("c", "Chargemap public map API", "https://chargemap.com/exclusivas-baymar.html",
                        39.41220, -0.42867, price=0,
                        name="Exclusivas Baymar",
                        address="Calle de les Moreres Xirivella",
                        reliability="directory-map-free",
                        media={"photo_status": "no_public_photo", "photos": []})
        detail = {
            "name": "GAX Xirivella 01",
            "image": {"l": "https://example.test/baymar.jpg"},
            "address": {
                "address": "Carrer de les Moreres, Xirivella",
                "city": "Xirivella",
            },
        }

        self.assertIs(
            ing.electromaps_existing_photo_match([chargemap], detail, 39.41167, -0.42855),
            chargemap,
        )

    def test_photo_only_electromaps_merge_keeps_free_claim_and_adds_media(self) -> None:
        official = rec("m", "València municipal OpenData", "https://geoportal.valencia.es/arcgis/rest/services/EV/MapServer/0/500",
                       38.961639, -0.148179, price=0,
                       name="Municipal — Guardamar de la Safor",
                       reliability="official-free",
                       charge_claim={"type": "free", "source": "València municipal OpenData",
                                     "url": "https://geoportal.valencia.es/arcgis/rest/services/EV/MapServer/0/500", "price_min": 0},
                       media={"photo_status": "no_public_photo", "photos": []})
        photo = rec("ph", "Electromaps public photo", "https://map.electromaps.com/en/p/218065",
                    38.96164, -0.14818, price=None,
                    name="Guardamar de la Safor",
                    reliability="photo-only",
                    connector_labels=[],
                    connectors=[],
                    source_integration={
                        "mode": "public_source",
                        "live_status_integrated": False,
                        "station_status_fields": False,
                        "connector_status_fields": False,
                    },
                    media={"photos": [{"url": "https://example.test/guardamar.jpg",
                                        "source_url": "https://map.electromaps.com/en/p/218065"}]})

        merged, n = ing.merge_cross_source_duplicates([official, photo])

        self.assertEqual(n, 1)
        self.assertEqual(merged[0]["price_min"], 0)
        self.assertEqual(merged[0]["charge_claim"]["source"], "València municipal OpenData")
        self.assertEqual(merged[0]["media"]["photo_status"], "public_photo_available")
        self.assertEqual(merged[0]["media"]["photos"][0]["url"], "https://example.test/guardamar.jpg")

    def test_photo_only_electromaps_never_becomes_primary_record(self) -> None:
        community = rec("o", "OpenChargeMap public snapshot", "https://map.openchargemap.io/?mode=details&id=171975",
                        39.73071064220841, -0.825361263672221, price=0,
                        name="PDR Plaza de la Iglesia",
                        reliability="community-access-limited",
                        access={"level": "restricted", "label": "Access caveat"},
                        price_claim_type="access_limited_free",
                        media={"photo_status": "no_public_photo", "photos": []})
        photo = rec("ph", "Electromaps public photo", "https://map.electromaps.com/en/p/139836",
                    39.73072, -0.82536, price=None,
                    name="Ayuntamiento Villar del Arzobispo",
                    reliability="photo-only",
                    connector_labels=[],
                    connectors=[],
                    media={"photos": [{"url": "https://example.test/villar.jpg",
                                        "source_url": "https://map.electromaps.com/en/p/139836"}]})

        merged, n = ing.merge_cross_source_duplicates([community, photo])

        self.assertEqual(n, 1)
        self.assertEqual(merged[0]["id"], "o")
        self.assertEqual(merged[0]["source"], "OpenChargeMap public snapshot")
        self.assertEqual(merged[0]["price_min"], 0)
        self.assertFalse(merged[0]["is_free"])
        self.assertEqual(merged[0]["price_claim_type"], "access_limited_free")
        self.assertEqual(merged[0]["media"]["photo_status"], "public_photo_available")

    def test_electromaps_second_pass_can_photo_enrich_late_loaded_official_rows(self) -> None:
        official = rec("m", "València municipal OpenData", "https://geoportal.valencia.es/arcgis/rest/services/EV/MapServer/0/510",
                    38.9411406, -0.1833378, price=0,
                    name="Municipal — Ayuntamiento de Almoines",
                    reliability="official-free",
                    media={"photo_status": "no_public_photo", "photos": []})
        detail = {
            "id": 214234,
            "name": "Ayuntamiento de Almoines",
            "url": "https://map.electromaps.com/en/p/214234",
            "latitude": 38.9411406,
            "longitude": -0.1833378,
            "address": {"address": "Almoines", "city": "Almoines"},
            "image": {"l": "https://example.test/almoines-l.jpg", "s": "https://example.test/almoines-s.jpg"},
        }

        original_cache = ing.CACHE
        with tempfile.TemporaryDirectory() as tmpdir:
            ing.CACHE = Path(tmpdir)
            try:
                (ing.CACHE / "electromaps_location_214234.json").write_text(json.dumps(detail), encoding="utf-8")
                rows = [official]
                ing.source_electromaps_photo_second_pass(rows)
                merged, n = ing.merge_cross_source_duplicates(rows)
            finally:
                ing.CACHE = original_cache

        self.assertEqual(len(rows), 2)
        self.assertEqual(n, 1)
        self.assertEqual(merged[0]["source"], "València municipal OpenData")
        self.assertEqual(merged[0]["media"]["photo_status"], "public_photo_available")
        self.assertEqual(merged[0]["media"]["photos"][0]["url"], "https://example.test/almoines-l.jpg")

    def test_electromaps_photo_match_skips_when_real_electromaps_record_exists(self) -> None:
        chargemap = rec("c", "Chargemap public map API", "https://chargemap.com/gandia-gare.html",
                        38.96991, -0.17890, price=0,
                        name="Gandia Gare",
                        reliability="directory-map-free",
                        media={"photo_status": "no_public_photo", "photos": []})
        electromaps = rec("e", "Electromaps web-map API", "https://map.electromaps.com/en/p/1",
                          38.97015, -0.17911, price=0,
                          name="Gandia Estacion Ayuntamiento",
                          reliability="directory-map-free")
        detail = {
            "name": "Gandia Estacion Ayuntamiento",
            "image": {"l": "https://example.test/gandia.jpg"},
            "address": {"address": "Gandia Estacion Ayuntamiento", "city": "Gandia"},
        }

        self.assertIsNone(
            ing.electromaps_existing_photo_match([chargemap, electromaps], detail, 38.97015, -0.17911)
        )

    def test_exact_lidl_electromaps_enrichment_merges_connector_photo_without_price_claim(self) -> None:
        locator = rec("l", "Lidl official free list", "https://empresa.lidl.es/puntos",
                      39.197363, -0.436888, price=0,
                      name="Lidl — Ronda del Calvari, nº33, 46680, Algemesí",
                      address="Ronda del Calvari, nº33, 46680, Algemesí",
                      reliability="official-free-policy",
                      connector_labels=[],
                      connectors=[],
                      source_integration={
                          "mode": "official_locator",
                          "live_status_integrated": False,
                          "connector_status_fields": False,
                          "note": "Official locator.",
                      },
                      charge_claim={"type": "free", "source": "Lidl official free list",
                                    "url": "https://empresa.lidl.es/puntos", "price_min": 0})
        directory = rec("e", "Electromaps web-map API", "https://map.electromaps.com/en/p/680618",
                        39.19727647351, -0.436616492602, price=None,
                        name="Lidl Algemesí",
                        reliability="directory-page",
                        connector_labels=["Type 2"],
                        max_power_kw=22.0,
                        connectors=[
                            {"connector_id": "1", "label": "Type 2", "status": "WORKING", "power_kw": 22.0, "count": 1,
                             "source_url": "https://map.electromaps.com/en/p/680618"},
                            {"connector_id": "2", "label": "Type 2", "status": "WORKING", "power_kw": 22.0, "count": 1,
                             "source_url": "https://map.electromaps.com/en/p/680618"},
                            {"connector_id": "3", "label": "Type 2", "status": "OUT_OF_SERVICE", "power_kw": 22.0, "count": 1,
                             "source_url": "https://map.electromaps.com/en/p/680618"},
                        ],
                        source_integration={
                            "mode": "directory_connector_status",
                            "live_status_integrated": False,
                            "station_status_fields": True,
                            "connector_status_fields": True,
                            "note": "Directory connector fields.",
                        },
                        media={"photos": [{"url": "https://example.test/lidl-algemesi.jpg",
                                            "source_url": "https://map.electromaps.com/en/p/680618"}]})

        merged, n = ing.merge_cross_source_duplicates([locator, directory])

        self.assertEqual(n, 1)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source"], "Lidl official free list")
        self.assertEqual(merged[0]["price_min"], 0)
        self.assertEqual(merged[0]["charge_claim"]["source"], "Lidl official free list")
        self.assertEqual(merged[0]["connector_count"], 3)
        self.assertEqual(merged[0]["total_evse"], 3)
        self.assertEqual(merged[0]["max_power_kw"], 22.0)
        self.assertEqual(merged[0]["media"]["photo_status"], "public_photo_available")
        self.assertTrue(merged[0]["source_integration"]["connector_status_fields"])
        self.assertFalse(merged[0]["source_integration"]["live_status_integrated"])

    def test_street_geocoded_lidl_locator_can_adopt_same_postcode_electromaps_hardware(self) -> None:
        locator = rec("l", "Lidl official free list", "https://empresa.lidl.es/puntos",
                      39.6836874, -0.2702085, price=0,
                      name="Lidl — Avda. Palmosa, s/n 46500 Valencia",
                      address="Avda. Palmosa, s/n 46500 Valencia",
                      reliability="official-free-policy",
                      connector_labels=[],
                      connectors=[],
                      raw={"brand": "Lidl", "official_query": "Sagunto - Avenida Palmosa, s/n, 46500", "coordinate_source": "nominatim_street"},
                      charge_claim={"type": "free", "source": "Lidl official free list",
                                    "url": "https://empresa.lidl.es/puntos", "price_min": 0})
        directory = rec("e", "Electromaps web-map API", "https://map.electromaps.com/en/p/830568",
                        39.682255112156, -0.268916934729, price=None,
                        name="Lidl",
                        address="Carrer de Sant Lluís, 25, 46500 Sagunt",
                        reliability="directory-page",
                        connector_labels=["Type 2"],
                        max_power_kw=22.0,
                        connectors=[{"connector_id": "4551623", "label": "Type 2", "status": "UNKNOWN",
                                     "power_kw": 22.0, "count": 1, "source_url": "https://map.electromaps.com/en/p/830568"}],
                        source_integration={
                            "mode": "directory_connector_status",
                            "live_status_integrated": False,
                            "station_status_fields": True,
                            "connector_status_fields": True,
                        },
                        raw={"brand": "Lidl"})

        merged, n = ing.merge_cross_source_duplicates([locator, directory])

        self.assertEqual(n, 1)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source"], "Lidl official free list")
        self.assertEqual(merged[0]["price_min"], 0)
        self.assertEqual(merged[0]["charge_claim"]["source"], "Lidl official free list")
        self.assertEqual(merged[0]["connector_count"], 1)
        self.assertEqual(merged[0]["total_evse"], 1)
        self.assertEqual(merged[0]["max_power_kw"], 22.0)
        self.assertTrue(merged[0]["source_integration"]["connector_status_fields"])

    def test_cross_source_merge_does_not_double_count_official_connector_inventory(self) -> None:
        official = rec("m", "València municipal OpenData", "https://geoportal.valencia.es/arcgis/rest/services/EV/MapServer/0/693",
                    36.52506, -5.04489, price=0, name="Municipal — Aguas de Benahavís",
                    reliability="official-free",
                    connector_labels=["Type 2", "Schuko"],
                    max_power_kw=11.0,
                    connectors=[
                        {"connector_id": "Mennekes", "label": "Type 2", "power_kw": 11.0, "count": 2, "source_url": "https://geoportal.valencia.es/arcgis/rest/services/EV/MapServer/0/693"},
                        {"connector_id": "Schuko", "label": "Schuko", "power_kw": 2.3, "count": 2, "source_url": "https://geoportal.valencia.es/arcgis/rest/services/EV/MapServer/0/693"},
                    ])
        ocm = rec("o", "OpenChargeMap public snapshot", "https://map.openchargemap.io/?mode=details&id=85424",
                  36.52507, -5.04490, price=0, name="Aguas de Benahavís",
                  reliability="directory-map-free",
                  connector_labels=["Type 2", "CEE 7/4 - Schuko - Type F"],
                  max_power_kw=22.0,
                  connectors=[
                      {"connector_id": "1", "label": "Type 2", "power_kw": 22.0, "count": 2, "source_url": "https://map.openchargemap.io/?mode=details&id=85424"},
                      {"connector_id": "2", "label": "CEE 7/4 - Schuko - Type F", "power_kw": 3.7, "count": 2, "source_url": "https://map.openchargemap.io/?mode=details&id=85424"},
                  ])

        merged, n = ing.merge_cross_source_duplicates([official, ocm])

        self.assertEqual(n, 1)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source"], "València municipal OpenData")
        self.assertEqual(merged[0]["connector_count"], 4)
        self.assertEqual(merged[0]["total_evse"], 4)
        self.assertEqual(merged[0]["max_power_kw"], 11.0)
        self.assertEqual({c["source_url"] for c in merged[0]["connectors"]}, {"https://geoportal.valencia.es/arcgis/rest/services/EV/MapServer/0/693"})
        self.assertEqual(len(merged[0]["sources"]), 2)

    def test_cross_source_merge_adopts_powered_inventory_when_locator_has_no_kw(self) -> None:
        locator = rec("c", "Consum store locator", "https://www.consum.es/supermercados/daimus-assagador/",
                      38.97123, -0.15393, price=0, name="Consum — DAIMÚS",
                      reliability="official-charger-location-price-unknown",
                      connector_labels=["Type 2"],
                      connectors=[{"connector_id": None, "label": "Type 2", "power_kw": None, "count": 1, "source_url": "https://www.consum.es/supermercados/daimus-assagador/"}])
        directory = rec("e", "Electromaps web-map API", "https://map.electromaps.com/en/p/359493",
                        38.97124, -0.15394, price=0, name="Consum Daimus",
                        reliability="directory-map-free",
                        connector_labels=["Type 2"],
                        connectors=[{"connector_id": "1", "label": "Type 2", "power_kw": 10.0, "count": 1, "source_url": "https://map.electromaps.com/en/p/359493"}])

        merged, n = ing.merge_cross_source_duplicates([locator, directory])

        self.assertEqual(n, 1)
        self.assertEqual(merged[0]["connector_count"], 1)
        self.assertEqual(merged[0]["total_evse"], 1)
        self.assertEqual(merged[0]["max_power_kw"], 10.0)
        self.assertEqual(merged[0]["connectors"][0]["source_url"], "https://map.electromaps.com/en/p/359493")

    def test_exact_coordinate_dedupe_fills_station_kw_from_merged_connectors(self) -> None:
        locator = rec("l", "Lidl official free list", "https://www.lidl.es/c/es-ES/localizador-de-tiendas/s10023015",
                      39.41726, -0.38045, price=0, name="Lidl — Alfafar",
                      reliability="official-free-policy",
                      connector_labels=[],
                      max_power_kw=None,
                      connectors=[],
                      port_count=1)
        directory = rec("e", "Electromaps web-map API", "https://map.electromaps.com/en/p/754727",
                        39.41726, -0.38045, price=0, name="Lidl Alfafar",
                        reliability="directory-map-free",
                        connector_labels=["Type 2"],
                        max_power_kw=None,
                        connectors=[{"connector_id": "1", "label": "Type 2", "power_kw": 22.0, "count": 1,
                                     "source_url": "https://map.electromaps.com/en/p/754727"}])

        merged = ing.dedupe([locator, directory])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source"], "Lidl official free list")
        self.assertEqual(merged[0]["connector_count"], 1)
        self.assertEqual(merged[0]["max_power_kw"], 22.0)
        self.assertFalse(merged[0]["fast"])

    def test_different_retail_brand_does_not_merge_outside_base_radius(self) -> None:
        lidl = rec("l", "OpenStreetMap public tags", "https://www.openstreetmap.org/node/1",
                   39.55882, -0.50383, price=0, name="Lidl", raw={"brand": "Lidl"})
        mercadona = rec("m", "Electromaps web-map API", "https://map.electromaps.com/en/p/2",
                        39.55876, -0.50446, price=0, name="Mercadona La Eliana", raw={"brand": "Mercadona"})

        merged, n = ing.merge_cross_source_duplicates([lidl, mercadona])

        self.assertEqual(n, 0)
        self.assertEqual(len(merged), 2)

    def test_priceless_official_locator_adopts_agreeing_price_and_keeps_official_identity(self) -> None:
        consum = rec("c", "Consum store locator", "https://www.consum.es/x", 39.47000, -0.37000,
                     price=None, reliability="official-charger-location-price-unknown",
                     parking={"kind": "retail_parking"})
        ocm = rec("o", "OpenChargeMap public snapshot", "https://map.openchargemap.io/?id=3", 39.47009, -0.37009,
                  price=0, charge_claim={"type": "free", "url": "https://map.openchargemap.io/?id=3"},
                  raw={"DateLastVerified": ing.now_iso()})
        merged, n = ing.merge_cross_source_duplicates([consum, ocm])
        self.assertEqual(n, 1)
        base = merged[0]
        self.assertEqual(base["reliability"], "official-charger-location-price-unknown")  # official identity wins
        self.assertEqual(base["price_min"], 0)  # adopted the agreeing free price
        self.assertTrue(base["is_free"])
        self.assertEqual(base["source_count"], len(base["sources"]))

    def test_specific_parking_knowledge_wins(self) -> None:
        a = rec("a", "OpenChargeMap public snapshot", "https://map.openchargemap.io/?id=1", 39.47000, -0.37000,
                price=0, parking={"kind": "unknown"})
        b = rec("b", "Electromaps web-map API", "https://map.electromaps.com/en/p/9", 39.47008, -0.37008,
                price=0, parking={"kind": "retail_parking"})
        merged, _ = ing.merge_cross_source_duplicates([a, b])
        self.assertEqual(merged[0]["parking"]["kind"], "retail_parking")


if __name__ == "__main__":
    unittest.main()
