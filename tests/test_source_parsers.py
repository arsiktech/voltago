#!/usr/bin/env python3
"""Characterization tests for the per-source ingest parsers in scripts/ingest_sources.py.

Covers four parser families end-to-end against REAL public-data payloads
(trimmed from data/cache/ to <10 KB each; commented "synthetic" where no
cache file exists) with the network entry point monkeypatched per-parser:

- source_osm                -> ingest_sources.cached_overpass
- source_openchargemap      -> commit-pinned ocm-export tarball + pinned legacy gz via
                                policy_urlopen GET (faked; CACHE monkeypatched to a temp dir)
- source_electromaps_mapi   -> ingest_sources.cached_json (locations list + per-id detail)
- source_chargemap_mappy    -> ingest_sources.cached_json (recursive box walk; one
                                non-cluster response ends the walk immediately)
Each family gets a positive case (record count + load-bearing fields on one
record) and a malformed-payload case that must return zero records WITHOUT
raising (plan 006, STOP condition C: a raise or a dropped-without-a-flag
family here would be a real bug to report, not fix in this file).
"""
from __future__ import annotations

import gzip
import json
import shutil
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import ingest_sources as ing  # noqa: E402


class SourceOsmTests(unittest.TestCase):
    """source_osm calls ingest_sources.cached_overpass(query, cache_name, ttl_hours) and
    json.loads()s the returned bytes as an Overpass {"elements": [...]} payload.

    Fixture: two REAL elements trimmed from
    data/cache/osm_valencia_chargers_meta.json (fee=no, recognized socket tags) —
    verified against the live source_osm gates before being committed here.
    """

    REAL_ELEMENTS = [
        {
            "type": "node", "id": 8154400553, "lat": 39.5726146, "lon": -1.2108652,
            "timestamp": "2020-11-23T11:28:47Z",
            "tags": {
                "amenity": "charging_station", "fee": "no", "motorcar": "yes",
                "name": "Ayuntamiento Utiel RAPIDO", "opening_hours": "24/7", "operator": "Electromaps",
                "socket:chademo": "1", "socket:type2": "1", "socket:type2_combo": "1",
                "source": "https://www.electromaps.com",
            },
        },
        {
            "type": "node", "id": 9315942751, "lat": 39.8903394, "lon": -0.2538952,
            "timestamp": "2025-10-23T08:30:26Z",
            "tags": {
                "amenity": "charging_station", "amperage": "32", "capacity": "2", "fee": "no",
                "motorcar": "yes", "name": "Artana", "operator": "Ayuntamiento de Artana",
                "operator:type": "government", "socket:type2": "2", "voltage": "400",
            },
        },
    ]

    def _patch(self, body: bytes):
        original = ing.cached_overpass
        ing.cached_overpass = lambda query, cache_name, ttl_hours=12: body
        self.addCleanup(lambda: setattr(ing, "cached_overpass", original))
        # The fixture never contacts Overpass. Keep every tile/deduplication assertion,
        # without sleeping through 24 production rate-limit pauses per parser case.
        original_pause = ing.OVERPASS_TILE_PAUSE_SECONDS
        ing.OVERPASS_TILE_PAUSE_SECONDS = 0
        self.addCleanup(lambda: setattr(ing, "OVERPASS_TILE_PAUSE_SECONDS", original_pause))

    def test_real_payload_yields_expected_records_and_load_bearing_fields(self) -> None:
        body = json.dumps({"version": 0.6, "generator": "test", "elements": self.REAL_ELEMENTS}).encode("utf-8")
        self._patch(body)

        records: list = []
        ing.source_osm(records)

        self.assertEqual(len(records), 2)
        by_id = {r["raw"]["osm_id"]: r for r in records}
        rec = by_id[8154400553]
        self.assertEqual(rec["lat"], 39.5726146)
        self.assertEqual(rec["lon"], -1.2108652)
        self.assertTrue(rec["is_free"])
        self.assertEqual(rec["price_min"], 0.0)
        self.assertEqual(set(rec["connector_labels"]), {"CHAdeMO", "Type 2", "CCS2"})
        self.assertEqual(rec["source_url"], "https://www.openstreetmap.org/node/8154400553")
        self.assertEqual(rec["source"], "OpenStreetMap public tags")

    def test_malformed_payload_missing_elements_key_returns_no_records(self) -> None:
        self._patch(b'{"version": 0.6}')  # no "elements" key at all

        records: list = []
        ing.source_osm(records)  # must not raise

        self.assertEqual(records, [])


class SourceOpenChargeMapTests(unittest.TestCase):
    """source_openchargemap now reads a commit-pinned ``ocm-export`` tarball (compact rows,
    lookup IDs) fetched via GET, layered over the pinned 2021 ``ocm-data`` history.

    The detailed licensing/continuity/archive-safety matrix lives in tests/test_ocm_export.py;
    this class keeps the parser-characterization cases. Fixtures: the two REAL Spanish POIs
    (IDs 9493, 23805) from the legacy feed (nested shape) plus a real compact current row.
    """

    REAL_LEGACY_POIS = [
        {
            "ID": 9493, "UUID": "531B47EA-3FDE-48C2-9451-AF593F0847CB", "DataProviderID": 1,
            "UsageCost": "Free",
            "AddressInfo": {"Title": "Telefonica Distrito C", "AddressLine1": "Ronda de la Comunicacion, 2",
                             "Town": "Alcobendas", "Postcode": "28050", "Latitude": 40.5154586, "Longitude": -3.66219009999998,
                             "Country": {"ISOCode": "ES"}},
            "Connections": [{"ID": 7163, "ConnectionTypeID": 28, "ConnectionType": {"Title": "CEE 7/4 - Schuko - Type F"},
                              "StatusTypeID": 0, "StatusType": {"Title": "Unknown", "IsOperational": None},
                              "LevelID": 2, "Level": {"Title": "Level 2 : Medium (Over 2kW)"},
                              "Amps": 16, "Voltage": 230, "PowerKW": 3.7, "CurrentTypeID": None, "CurrentType": None, "Quantity": 1}],
            "NumberOfPoints": 2, "StatusTypeID": 0, "StatusType": {"Title": "Unknown"},
            "DateLastStatusUpdate": {"$date": "2016-02-17T19:18:00Z"}, "DateLastConfirmed": None,
            "SubmissionStatus": {"Title": "Submission Published"}, "UsageType": {"Title": "Public"}, "MediaItems": None,
        },
        {
            "ID": 23805, "UUID": "0298B33C-89FC-4DCB-AF9C-56E1CFDA025E", "DataProviderID": 1,
            "UsageCost": "Free",
            "AddressInfo": {"Title": "B:SM 38 - Tanatori Les Corts", "AddressLine1": "Av. de Joan XXIII, 23 - 25",
                             "Town": "BARCELONA", "Postcode": "08028", "Latitude": 41.384186, "Longitude": 2.122279,
                             "Country": {"ISOCode": "ES"}},
            "Connections": [{"ID": 25877, "ConnectionTypeID": 25, "ConnectionType": {"Title": "Type 2 (Socket Only)"},
                              "StatusTypeID": 50, "StatusType": {"Title": "Operational", "IsOperational": True},
                              "LevelID": 2, "Level": {"Title": "Level 2 : Medium (Over 2kW)"},
                              "Amps": 32, "Voltage": 230, "PowerKW": 7.2, "CurrentTypeID": None, "CurrentType": None, "Quantity": 2}],
            "NumberOfPoints": 2, "StatusTypeID": 50, "StatusType": {"Title": "Operational"},
            "DateLastStatusUpdate": {"$date": "2015-09-06T18:49:00Z"}, "DateLastConfirmed": None,
            "SubmissionStatus": {"Title": "Submission Published"}, "UsageType": {"Title": "Public - Membership Required"}, "MediaItems": None,
        },
    ]

    def setUp(self) -> None:
        import test_ocm_export as fx  # shared real-shape fixtures; tests/ is on sys.path when run as a file
        self.fx = fx
        self._tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self._tmpdir, ignore_errors=True))
        self._original_cache = ing.CACHE
        self.addCleanup(lambda: setattr(ing, "CACHE", self._original_cache))
        ing.CACHE = self._tmpdir
        floor = mock.patch.object(fx.ocm, "MIN_COUNTRY_FILES", 1)
        floor.start()
        self.addCleanup(floor.stop)

    def _run(self, legacy, current=None):
        fx = self.fx
        arch = self._tmpdir / "a.tar.gz"
        fx.make_archive(arch, current if current is not None else {1: fx.compact_poi(1)})
        net = fx.Network(manifest=fx.MANIFEST, archive=arch.read_bytes(), legacy=fx.legacy_gz(legacy))
        records: list = []
        with mock.patch.object(ing, "policy_urlopen", net):
            ing.source_openchargemap(records)
        return records

    def test_real_payload_yields_expected_records_and_load_bearing_fields(self) -> None:
        records = self._run(self.REAL_LEGACY_POIS)

        by_id = {r["raw"]["ID"]: r for r in records}
        self.assertEqual(sorted(by_id), [1, 9493, 23805])
        rec = by_id[23805]
        self.assertEqual(rec["lat"], 41.384186)
        self.assertEqual(rec["lon"], 2.122279)
        self.assertTrue(rec["is_free"])
        self.assertEqual(rec["connector_labels"], ["Type 2"])
        self.assertEqual(rec["max_power_kw"], 7.2)
        self.assertEqual(rec["source_url"], "https://map.openchargemap.io/?mode=details&id=23805")
        self.assertEqual(rec["source"], "OpenChargeMap public snapshot")
        self.assertTrue(rec["raw"]["historical_snapshot"])

    def test_current_compact_row_resolves_lookup_ids_end_to_end(self) -> None:
        records = self._run(self.REAL_LEGACY_POIS, current={203975: self.fx.compact_poi(
            203975, title="Ayunt. Foios", operator=3279, lat=39.53676290559463, lon=-0.35793545094429646)})
        rec = next(r for r in records if r["raw"]["ID"] == 203975)
        self.assertEqual(rec["name"], "Ayunt. Foios")
        self.assertEqual(rec["connector_labels"], ["Type 2"])
        self.assertEqual(rec["max_power_kw"], 22)
        self.assertEqual(rec["raw"]["StatusTypeTitle"], "Operational")
        self.assertFalse(rec["raw"]["historical_snapshot"])

    def test_malformed_payload_corrupt_line_is_skipped_without_raising(self) -> None:
        gz_path = self._tmpdir / "legacy.gz"
        with gzip.open(gz_path, "wt", encoding="utf-8") as f:
            f.write("{not valid json,,,\n")  # one corrupt JSONL line, nothing else
        net = self.fx.Network(manifest=self.fx.MANIFEST, archive=(lambda a: (self.fx.make_archive(a, {1: self.fx.compact_poi(1)}), a.read_bytes())[1])(self._tmpdir / "b.tar.gz"),
                              legacy=gz_path.read_bytes())

        records: list = []
        with mock.patch.object(ing, "policy_urlopen", net):
            ing.source_openchargemap(records)  # must not raise: history is additive

        self.assertEqual([r["raw"]["ID"] for r in records], [1])

    def test_quarantined_coordinate_and_commercial_reference_are_not_exported(self) -> None:
        quarantined = deepcopy(self.REAL_LEGACY_POIS[0])
        quarantined["ID"] = 169683
        quarantined["AddressInfo"]["Title"] = "Barbastro at an incompatible Barcelona coordinate"
        allowed = deepcopy(self.REAL_LEGACY_POIS[1])
        allowed["Connections"][0]["Reference"] = "Electromaps"
        records = self._run([quarantined, allowed], current={1: self.fx.compact_poi(1, lat=39.1)})

        self.assertEqual(sorted(record["raw"]["ID"] for record in records), [1, 23805])
        rec = next(r for r in records if r["raw"]["ID"] == 23805)
        self.assertNotIn("Reference", rec["connectors"][0]["raw"])
        self.assertNotIn("electromaps", json.dumps(records, ensure_ascii=False).lower())
        self.assertEqual(rec["sources"][0]["source_last_updated"], "2015-09-06T18:49:00Z")


class SourceElectromapsMapiTests(unittest.TestCase):
    """source_electromaps_mapi calls ingest_sources.cached_json twice per station: once
    for the locations list (cache_name="electromaps_mapi_locations.json"), then once
    per id for the detail record (cache_name=f"electromaps_location_{id}.json").

    Fixture: two REAL location detail payloads trimmed from
    data/cache/electromaps_location_102933.json (free) and
    data/cache/electromaps_location_104763.json (0.25 EUR/kWh, at the price cap) —
    both confirmed to pass the live source_electromaps_mapi gates before being
    committed here.
    """

    LOCATIONS = [
        {"id": 102933, "name": "Iberdrola Benisano", "latitude": 39.611290770976, "longitude": -0.576330944896, "marker": "1.3.0"},
        {"id": 104763, "name": "Mercadona Betera", "latitude": 39.593244242282, "longitude": -0.466602779925, "marker": "1.3.0"},
    ]
    DETAILS = {
        102933: {
            "id": 102933, "name": "Iberdrola Benisano", "url": "https://map.electromaps.com/en/p/102933",
            "latitude": 39.611290770976, "longitude": -0.576330944896, "type": "ON_STREET",
            "online_status": "UNKNOWN", "offline_status": "SOME_SOCKETS_NOT_WORK",
            "charge_price": "Gratis", "instructions": "APP Iberdrola",
            "address": {"address": "Avenida del Pou, 2, 46181 Benissano, Valencia, Espana",
                        "postal_code": "46181", "city": "Benissano", "area_lvl2": "Valencia", "country_code": "ES"},
            "connectors": [
                {"id": 1288702, "type": "IEC_62196_T2", "status": "WORKING", "kw": 22, "cost": "UNKNOWN",
                 "status_updated_at": "2025-10-25T15:02:50+0000"},
                {"id": 1288703, "type": "IEC_62196_T2", "status": "OUT_OF_SERVICE", "kw": 22, "cost": "UNKNOWN",
                 "status_updated_at": "2025-01-19T06:26:52+0000"},
            ],
        },
        104763: {
            "id": 104763, "name": "Mercadona Betera", "url": "https://map.electromaps.com/en/p/104763",
            "latitude": 39.593244242282, "longitude": -0.466602779925, "type": "SHOP",
            "online_status": "UNKNOWN", "offline_status": "WORKING",
            "charge_price": "0,25€/kWh", "instructions": "app Iberdrola ",
            "address": {"address": "Carrer Mosen Vicent Roig, 8, 46117, Betera",
                        "postal_code": "46117", "city": "Betera", "area_lvl2": "Valencia", "country_code": "ES"},
            "connectors": [
                {"id": 4494045, "type": "IEC_62196_T2", "status": "UNKNOWN", "kw": 22, "cost": "UNKNOWN",
                 "status_updated_at": "1970-01-01T00:00:00+0000"},
            ],
        },
    }

    def _patch(self, locations, details):
        def fake_cached_json(url, cache_name, ttl_hours=24, headers=None, **kw):
            if cache_name == "electromaps_mapi_locations.json":
                return locations
            for loc_id, detail in details.items():
                if cache_name == f"electromaps_location_{loc_id}.json":
                    return detail
            raise AssertionError(f"unexpected cache_name {cache_name!r} in mocked cached_json")

        original = ing.cached_json
        ing.cached_json = fake_cached_json
        self.addCleanup(lambda: setattr(ing, "cached_json", original))
        # electromaps_lidl_official_match walks `records` for a prior Lidl official row;
        # irrelevant to this parser test (no such rows exist) but keep it real, not mocked.

    def test_real_payload_yields_expected_records_and_load_bearing_fields(self) -> None:
        self._patch(self.LOCATIONS, self.DETAILS)

        records: list = []
        ing.source_electromaps_mapi(records)

        self.assertEqual(len(records), 2)
        by_id = {r["raw"].get("chargemap_id") or r["source_url"]: r for r in records}
        free_row = next(r for r in records if r["price_min"] == 0.0)
        self.assertEqual(free_row["lat"], 39.611290770976)
        self.assertEqual(free_row["lon"], -0.576330944896)
        self.assertTrue(free_row["is_free"])
        self.assertEqual(free_row["connector_labels"], ["Type 2"])
        self.assertEqual(free_row["max_power_kw"], 22)
        self.assertEqual(free_row["source_url"], "https://map.electromaps.com/en/p/102933")
        self.assertEqual(free_row["source"], "Electromaps web-map API")

        cheap_row = next(r for r in records if r["price_min"] == 0.25)
        self.assertTrue(cheap_row["is_cheap"])

    def test_malformed_payload_empty_locations_list_returns_no_records(self) -> None:
        self._patch([], {})  # locations endpoint returns an empty list

        records: list = []
        ing.source_electromaps_mapi(records)  # must not raise

        self.assertEqual(records, [])


class SourceChargemapMappyTests(unittest.TestCase):
    """source_chargemap_mappy recursively walks a grid of bounding boxes via
    ingest_sources.cached_json(url, f"chargemap_mappy_{box_key}.json", ...), each
    returning {"response": {"content": {"items": [...]}}}. An item is either a
    {"pool": {...}} record or a {"type": "cluster"} marker that triggers recursion
    into 4 sub-boxes. A single non-cluster response therefore ends the walk after
    one call, which is all this test needs to mock.

    Fixture: two REAL free charging pools trimmed from
    data/cache/chargemap_mappy_39p07250_m0p36250_38p85000_0p05000.json (id 98317)
    and .../chargemap_mappy_39p29500_m0p36250_39p07250_0p05000.json (id 304289) —
    both confirmed to pass every live source_chargemap_mappy gate (is_free,
    power evidence, operational_status, availability_status, name-reject regex)
    before being committed here.
    """

    REAL_POOLS = [
        {
            "city": "Gandia", "gps_coordinates": {"lon": -0.1688799408, "lat": 38.9683106575},
            "street_name": "Avinguda de la Vital", "speed": {"id": 1}, "network": None, "number": None,
            "id": 98317, "slug": "la-vital-wallbox",
            "charging_connectors": [{"count": 1, "available_count": 1, "evse_ids": [], "type": "MENNEKES_TYPE_2",
                                      "connector_type": {"id": 14, "icon": "type2"}, "power_max": 7}],
            "is_free": True, "name": "La Vital (Wallbox)", "location_type_slug": "shop",
            "postal_code": "46700", "last_used_time": "2022-12-20T15:33:47+00:00", "country_code": "ES",
            "operational_status": "OPERATIONAL", "availability_status": "AVAILABLE", "evses": [],
        },
        {
            "city": "Beniarjo", "gps_coordinates": {"lon": -0.1873100996, "lat": 38.9362335205},
            "street_name": "Plaza del poligono 3", "speed": {"id": 1}, "network": None, "number": "",
            "id": 304289, "slug": "poligono-de-beniarjo",
            "charging_connectors": [{"count": 1, "available_count": 1, "evse_ids": [], "type": "MENNEKES_TYPE_2",
                                      "connector_type": {"id": 14, "icon": "type2"}, "power_max": 22}],
            "is_free": True, "name": "Poligono de Beniarjo", "location_type_slug": "point",
            "postal_code": "46722", "last_used_time": None, "country_code": "ES",
            "operational_status": "OPERATIONAL", "availability_status": "AVAILABLE", "evses": [],
        },
    ]

    def _patch(self, items):
        def fake_cached_json(url, cache_name, ttl_hours=24, headers=None, timeout=None, **kw):
            return {"response": {"content": {"items": items}}}

        original = ing.cached_json
        ing.cached_json = fake_cached_json
        self.addCleanup(lambda: setattr(ing, "cached_json", original))

    def test_real_payload_yields_expected_records_and_load_bearing_fields(self) -> None:
        self._patch([{"pool": pool} for pool in self.REAL_POOLS])

        records: list = []
        ing.source_chargemap_mappy(records)

        self.assertEqual(len(records), 2)
        by_id = {r["raw"]["chargemap_id"]: r for r in records}
        rec = by_id[304289]
        self.assertEqual(rec["lat"], 38.9362335205)
        self.assertEqual(rec["lon"], -0.1873100996)
        self.assertTrue(rec["is_free"])
        # D-151: this fixture's connector has no connector_type.name/label/slug,
        # so source_chargemap_mappy's `or`-chain falls through to
        # connector_type.id (14) before ever trying the sibling `type`
        # field ("MENNEKES_TYPE_2") — connector_label_from_token(14) used to
        # silently default unrecognized tokens to "Type 2" (falsely claiming
        # Tesla compatibility for a numeric ID that was never actually
        # parsed). This fixture characterized that defect. The fixed,
        # fail-closed function now returns an honest title-cased passthrough
        # of the unrecognized token ("14") instead of fabricating "Type 2".
        self.assertEqual(rec["connector_labels"], ["14"])
        self.assertEqual(rec["max_power_kw"], 22)
        self.assertEqual(rec["source_url"], "https://chargemap.com/poligono-de-beniarjo.html")
        self.assertEqual(rec["source"], "Chargemap public map API")

    def test_malformed_payload_missing_response_key_returns_no_records(self) -> None:
        original = ing.cached_json
        ing.cached_json = lambda url, cache_name, ttl_hours=24, headers=None, timeout=None, **kw: {}  # no "response" key
        self.addCleanup(lambda: setattr(ing, "cached_json", original))

        records: list = []
        ing.source_chargemap_mappy(records)  # must not raise

        self.assertEqual(records, [])


if __name__ == "__main__":
    unittest.main()
