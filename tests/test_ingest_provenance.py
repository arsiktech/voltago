#!/usr/bin/env python3
"""Cache/source provenance contracts for the public-source ingestion pipeline."""

from __future__ import annotations

import dataclasses
import gzip
import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import ingest_sources as ing  # noqa: E402
import price_evidence  # noqa: E402
import source_matrix  # noqa: E402
from test_tenerife_sources import ct_feature, ct_payload, reve_row  # noqa: E402


def iso_from_epoch(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_artifact_at(fetched_at: str) -> ing.FetchArtifact:
    return ing.FetchArtifact(
        body=b"fixture",
        url="https://example.test/source",
        cache_name="fixture.json",
        fetched_at=fetched_at,
        age_seconds=0,
        hit=False,
        fallback=False,
        stale=False,
    )


class CachedFetchProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._original_cache = ing.CACHE
        self.addCleanup(lambda: setattr(ing, "CACHE", self._original_cache))
        ing.CACHE = Path(self._tmp.name)
        self._original_out = ing.OUT
        self.addCleanup(lambda: setattr(ing, "OUT", self._original_out))
        ing.OUT = Path(self._tmp.name) / "free_intelligence.json"
        self._original_evidence = list(ing.FETCH_EVIDENCE)
        self.addCleanup(lambda: ing.FETCH_EVIDENCE.__setitem__(slice(None), self._original_evidence))
        ing.FETCH_EVIDENCE.clear()

    def test_fresh_cache_keeps_original_file_timestamp(self) -> None:
        path = ing.CACHE / "fresh.bin"
        path.write_bytes(b"cached evidence")
        cached_at = time.time() - 30
        os.utime(path, (cached_at, cached_at))

        with mock.patch.object(ing, "fetch", side_effect=AssertionError("fresh cache must not fetch")):
            artifact = ing.cached_fetch("https://example.test/source", "fresh.bin", ttl_hours=1)

        self.assertEqual(artifact.body, b"cached evidence")
        self.assertEqual(artifact.fetched_at, iso_from_epoch(cached_at))
        self.assertGreaterEqual(artifact.age_seconds, 30)
        self.assertLess(artifact.age_seconds, 35)
        self.assertTrue(artifact.hit)
        self.assertFalse(artifact.fallback)
        self.assertFalse(artifact.stale)

    def test_stale_fallback_retains_old_timestamp_and_is_flagged(self) -> None:
        path = ing.CACHE / "stale.bin"
        path.write_bytes(b"old but usable evidence")
        cached_at = time.time() - 7200
        os.utime(path, (cached_at, cached_at))

        with mock.patch.object(ing, "fetch", side_effect=TimeoutError("upstream unavailable")):
            artifact = ing.cached_fetch("https://example.test/source", "stale.bin", ttl_hours=1)

        self.assertEqual(artifact.body, b"old but usable evidence")
        self.assertEqual(artifact.fetched_at, iso_from_epoch(cached_at))
        self.assertGreaterEqual(artifact.age_seconds, 7200)
        self.assertTrue(artifact.hit)
        self.assertTrue(artifact.fallback)
        self.assertTrue(artifact.stale)
        self.assertEqual(artifact.error, "TimeoutError")
        self.assertEqual(
            artifact.as_evidence()["cache"],
            {"hit": True, "fallback": True, "stale": True},
        )

    def test_network_response_gets_current_timestamp(self) -> None:
        before = time.time()
        with mock.patch.object(ing, "fetch", return_value=b"new evidence"):
            artifact = ing.cached_fetch("https://example.test/source", "network.bin", ttl_hours=1)
        after = time.time()

        fetched_epoch = datetime.fromisoformat(artifact.fetched_at.replace("Z", "+00:00")).timestamp()
        self.assertEqual(artifact.body, b"new evidence")
        self.assertGreaterEqual(fetched_epoch, before)
        self.assertLessEqual(fetched_epoch, after)
        self.assertEqual(artifact.fetched_at, iso_from_epoch((ing.CACHE / "network.bin").stat().st_mtime))
        self.assertEqual(artifact.age_seconds, 0)
        self.assertFalse(artifact.hit)
        self.assertFalse(artifact.fallback)
        self.assertFalse(artifact.stale)

    def test_overpass_network_response_uses_same_provenance_contract(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"elements":[]}'
        before = time.time()
        with mock.patch.object(ing.urllib.request, "urlopen", return_value=response):
            artifact = ing.cached_overpass("[out:json];out;", "overpass.json", ttl_hours=1)
        after = time.time()

        fetched_epoch = datetime.fromisoformat(artifact.fetched_at.replace("Z", "+00:00")).timestamp()
        self.assertEqual(artifact.body, b'{"elements":[]}')
        self.assertGreaterEqual(fetched_epoch, before)
        self.assertLessEqual(fetched_epoch, after)
        self.assertFalse(artifact.hit)
        self.assertFalse(artifact.fallback)
        self.assertFalse(artifact.stale)

    def test_openchargemap_record_inherits_cache_mtime(self) -> None:
        poi = {
            "ID": 123,
            "UsageCost": "Free",
            "AddressInfo": {
                "Title": "OCM provenance charger",
                "AddressLine1": "Carrer de la Prova",
                "Town": "València",
                "Latitude": 39.47,
                "Longitude": -0.37,
                "Country": {"ISOCode": "ES"},
            },
            "Connections": [{
                "ID": 1,
                "ConnectionType": {"Title": "Type 2 (Socket Only)"},
                "PowerKW": 22,
                "Quantity": 1,
                "StatusType": {"Title": "Operational", "IsOperational": True},
            }],
            "NumberOfPoints": 1,
            "StatusType": {"Title": "Operational"},
            "UsageType": {"Title": "Public"},
        }
        path = ing.CACHE / "ocm_poi.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(json.dumps(poi) + "\n")
        cached_at = time.time() - 2 * 86400
        os.utime(path, (cached_at, cached_at))

        records = []
        ing.source_openchargemap(records)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["source_fetched_at"], iso_from_epoch(cached_at))
        self.assertEqual(records[0]["sources"][0]["source_fetched_at"], iso_from_epoch(cached_at))
        self.assertNotIn("source_cache_fallback", records[0]["sources"][0])

    def test_build_evidence_resets_per_run_and_surfaces_stale_fallback(self) -> None:
        path = ing.CACHE / "build-source.json"
        path.write_bytes(b'{"ok":true}')
        cached_at = time.time() - 7200
        os.utime(path, (cached_at, cached_at))
        ing.FETCH_EVIDENCE.append({"sentinel": True})  # must not leak into this build

        def cached_source(records) -> None:
            artifact = ing.cached_fetch(
                "https://example.test/build-source",
                "build-source.json",
                ttl_hours=1,
            )
            ing.add_record(
                records,
                source="Test cached source",
                source_url="https://example.test/station",
                name="Provenance station",
                lat=39.47,
                lon=-0.37,
                price_min=0.0,
                source_artifact=artifact,
            )

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(ing, "ACTIVE_SOURCE_ADAPTERS", (cached_source,)))
            stack.enter_context(mock.patch.object(ing, "enrich_price_evidence", return_value={}))
            stack.enter_context(mock.patch.object(ing, "fetch", side_effect=TimeoutError("offline")))
            payload = ing.build_payload()

        evidence = payload["fetch_evidence"]
        self.assertEqual(
            evidence["summary"],
            {
                "total": 1,
                "network": 0,
                "cache_hits": 1,
                "fallbacks": 1,
                "stale": 1,
                "stale_fallbacks": 1,
            },
        )
        self.assertEqual(len(evidence["artifacts"]), 1)
        self.assertEqual(
            evidence["artifacts"][0]["cache"],
            {"hit": True, "fallback": True, "stale": True},
        )
        self.assertEqual(evidence["artifacts"][0]["fetched_at"], iso_from_epoch(cached_at))
        self.assertEqual(payload["records"][0]["source_fetched_at"], iso_from_epoch(cached_at))


class MergedRecordProvenanceTests(unittest.TestCase):
    def test_oldest_timestamp_ignores_unusable_values_without_fabricating(self) -> None:
        unusable = [
            {"source_fetched_at": "0001-01-01T00:00:00+14:00"},
            {"source_fetched_at": "not-a-timestamp"},
            {"source_fetched_at": "2026-07-13T12:00:00"},
            {"sources": [{"source_fetched_at": None}, {"fetched_at": ""}]},
            {},
        ]

        self.assertIsNone(ing.oldest_contributing_source_timestamp(unusable))
        self.assertEqual(
            ing.oldest_contributing_source_timestamp(
                unusable
                + [
                    {"source_fetched_at": "2026-07-13T12:00:00Z"},
                    {"sources": [{"fetched_at": "2026-07-09T07:00:00+00:00"}]},
                ]
            ),
            "2026-07-09T07:00:00+00:00",
        )

    def test_dedupe_uses_oldest_timestamp_when_stale_row_supplies_price(self) -> None:
        fresh_at = "2026-07-13T12:00:00Z"
        stale_at = "2026-07-10T08:00:00Z"
        records = []
        ing.add_record(
            records,
            source="Test public source",
            source_url="https://example.test/locator",
            name="Fresh locator identity",
            lat=39.47,
            lon=-0.37,
            price_min=None,
            connector_labels=[],
            reliability="official-charger-location-price-unknown",
            source_artifact=fetch_artifact_at(fresh_at),
        )
        ing.add_record(
            records,
            source="Test public source",
            source_url="https://example.test/stale-station",
            name="Stale priced evidence",
            lat=39.47,
            lon=-0.37,
            price_min=0.0,
            connectors=[
                ing.connector_ref(
                    "Type 2",
                    power_kw=22,
                    source_url="https://example.test/stale-station",
                )
            ],
            photos=[{"url": "https://example.test/stale-photo.jpg"}],
            reliability="community-free",
            source_artifact=fetch_artifact_at(stale_at),
        )
        records[1]["sources"][0]["source_fetched_at"] = stale_at

        merged = ing.dedupe(records)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source_url"], "https://example.test/locator")
        self.assertEqual(merged[0]["price_min"], 0.0)
        self.assertEqual(merged[0]["charge_claim"]["url"], "https://example.test/stale-station")
        self.assertEqual(merged[0]["media"]["photos"][0]["url"], "https://example.test/stale-photo.jpg")
        self.assertEqual(merged[0]["source_fetched_at"], stale_at)
        self.assertEqual(merged[0]["sources"][1]["source_fetched_at"], stale_at)

    def test_cross_source_merge_uses_oldest_timestamp_when_stale_row_supplies_hardware(self) -> None:
        fresh_at = "2026-07-13T12:00:00Z"
        stale_at = "2026-07-09T07:00:00Z"
        records = []
        ing.add_record(
            records,
            source="València OpenData",
            source_url="https://valencia.opendatasoft.com/explore/dataset/punts-de-recarrega/",
            name="Fresh official identity",
            lat=39.47,
            lon=-0.37,
            price_min=0.0,
            connector_labels=[],
            reliability="official-free",
            source_artifact=fetch_artifact_at(fresh_at),
        )
        ing.add_record(
            records,
            source="Electromaps web-map API",
            source_url="https://map.electromaps.com/en/p/fixture",
            name="Stale directory hardware",
            lat=39.47001,
            lon=-0.37001,
            price_min=None,
            connectors=[
                ing.connector_ref(
                    "CCS2",
                    power_kw=50,
                    source_url="https://map.electromaps.com/en/p/fixture",
                )
            ],
            photos=[{"url": "https://example.test/stale-cross-source-photo.jpg"}],
            parking_kind="paid_or_metered",
            reliability="directory-page",
            source_artifact=fetch_artifact_at(stale_at),
        )
        records[1]["sources"][0]["fetched_at"] = stale_at

        merged, merge_count = ing.merge_cross_source_duplicates(records)

        self.assertEqual(merge_count, 1)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source"], "València OpenData")
        self.assertEqual(merged[0]["connectors"][0]["source_url"], "https://map.electromaps.com/en/p/fixture")
        self.assertEqual(merged[0]["media"]["photos"][0]["url"], "https://example.test/stale-cross-source-photo.jpg")
        self.assertEqual(merged[0]["parking"]["kind"], "paid_or_metered")
        self.assertEqual(merged[0]["source_fetched_at"], stale_at)
        self.assertEqual(merged[0]["sources"][1]["fetched_at"], stale_at)


FETCHED_AT = "2026-11-04T10:00:00Z"


def listing_clocks(record: dict, column: str) -> dict:
    return source_matrix._listed_columns(record)[column][0]["clocks"]


class ListingClockTests(unittest.TestCase):
    """Each sources[] entry keeps its own clock, so a /matrix cell never needs the record's."""

    def add(self, **kwargs) -> dict:
        records = []
        ing.add_record(records, source="Madrid public charger open data", source_url="https://example.test/m",
                       name="Clock station", lat=40.4, lon=-3.7, **kwargs)
        return records[0]

    def test_a_network_fetch_reaches_the_listing_without_a_fallback_flag(self) -> None:
        source = self.add(source_artifact=fetch_artifact_at(FETCHED_AT))["sources"][0]
        self.assertEqual(source["source_fetched_at"], FETCHED_AT)
        self.assertNotIn("source_cache_fallback", source)

    def test_a_cached_copy_keeps_its_old_clock_and_stays_flagged(self) -> None:
        artifact = dataclasses.replace(fetch_artifact_at("2026-10-01T00:00:00Z"), fallback=True, hit=True, stale=True)
        source = self.add(source_artifact=artifact)["sources"][0]
        self.assertEqual(source["source_fetched_at"], "2026-10-01T00:00:00Z")
        self.assertIs(source["source_cache_fallback"], True)

    def test_no_fetch_evidence_means_no_clock_and_never_the_build_time(self) -> None:
        with mock.patch.object(ing, "now_iso", side_effect=AssertionError("build time used as a fetch time")):
            record = self.add()
        self.assertIsNone(record["source_fetched_at"])
        self.assertNotIn("source_fetched_at", record["sources"][0])
        self.assertIsNone(listing_clocks(record, "madrid")["fetched_at"])

    def test_listing_clock_survives_both_merge_stages_into_the_matrix_cell(self) -> None:
        records = []
        for source, url, at, lon in (("Madrid public charger open data", "https://example.test/m", "2026-11-04T10:00:00Z", -3.7),
                                     ("OpenChargeMap public snapshot", "https://example.test/o", "2026-10-28T10:00:00Z", -3.70001)):
            ing.add_record(records, source=source, source_url=url, name="Same site", lat=40.4, lon=lon,
                           source_artifact=fetch_artifact_at(at))
        merged, _ = ing.merge_cross_source_duplicates(ing.dedupe(records))
        self.assertEqual(len(merged), 1)
        self.assertEqual(listing_clocks(merged[0], "madrid")["fetched_at"], "2026-11-04T10:00:00Z")
        self.assertEqual(listing_clocks(merged[0], "ocm")["fetched_at"], "2026-10-28T10:00:00Z")

    def test_merging_two_rows_of_one_listing_keeps_the_older_clock_and_the_flag(self) -> None:
        fresh = {"name": "MapaREVE public", "url": "https://example.test/r", "source_fetched_at": "2026-11-04T10:00:00Z"}
        older = {"name": "MapaREVE public", "url": "https://example.test/r", "source_fetched_at": "2026-11-01T10:00:00Z",
                 "source_cache_fallback": True}
        kept = ing.source_with_duplicate_clock(fresh, older)
        self.assertEqual(kept["source_fetched_at"], "2026-11-01T10:00:00Z")
        self.assertIs(kept["source_cache_fallback"], True)
        self.assertEqual(fresh["source_fetched_at"], "2026-11-04T10:00:00Z")  # the donor row is not mutated
        self.assertIs(ing.source_with_duplicate_clock(older, {**fresh}), older)

    def test_regional_registries_keep_the_fetch_time(self) -> None:
        artifact = fetch_artifact_at(FETCHED_AT)
        catalonia = [{"id": "1", "promotor_gestor": "Op", "tipus_connexi": "MENNEKES.M", "latitud": "41.5", "longitud": "2.1",
                      "kw": "22", "adre_a": "Carrer, 1", "municipi": "Sabadell"}]
        navarra = {"features": [{"geometry": {"type": "Point", "coordinates": [-2.17954, 42.88353]},
                                 "properties": {"FEATURE": 1, "MUNICIPIO": "Olazti", "OPERADOR": "Op", "DIRECCION": "A-1",
                                                "CONEXION": "IEC_62196_T2_COMBO", "POTENCIA": "180,00 kW"}}]}
        leon = [{"nombre": "Op", "direccion": "Plaza 1 - Arévalo (ÁVILA)", "operador": "Op", "no": "1",
                 "tipo": ["Mennekes (Type 2)"], "dd": {"lon": -4.72, "lat": 41.06}}]
        for rows in (ing.parse_catalonia_icaen(catalonia, source_artifact=artifact),
                     ing.parse_navarra_idena(navarra, source_artifact=artifact),
                     ing.parse_castilla_leon(leon, source_artifact=artifact)):
            self.assertEqual(rows[0]["sources"][0]["source_fetched_at"], FETCHED_AT)
            self.assertNotIn("source_published_at", rows[0]["sources"][0])

    def test_mapareve_rows_keep_the_clock_of_their_own_page(self) -> None:
        first = fetch_artifact_at("2026-11-04T10:00:00Z")
        second = dataclasses.replace(fetch_artifact_at("2026-11-02T10:00:00Z"), fallback=True, hit=True)
        energy = [{"type": "ENERGY", "price": 0.2}]
        pages = [{"data": [reve_row("a", "Page one", 28.1, -16.5, energy)]},
                 {"data": [reve_row("b", "Page two", 28.2, -16.5, energy)]}]
        rows = {row["name"]: row["sources"][0] for row in ing.parse_mapareve_public_pages(pages, source_artifacts=[first, second])}
        page_one = next(value for name, value in rows.items() if "one" in name.lower())
        page_two = next(value for name, value in rows.items() if "two" in name.lower())
        self.assertEqual(page_one["source_fetched_at"], "2026-11-04T10:00:00Z")
        self.assertEqual(page_two["source_fetched_at"], "2026-11-02T10:00:00Z")
        self.assertIs(page_two["source_cache_fallback"], True)
        self.assertEqual(page_one["source_last_updated"], "2026-09-16T13:48:10.288Z")

    def test_zunder_listing_is_no_fresher_than_the_detail_it_used(self) -> None:
        page = dataclasses.replace(fetch_artifact_at(FETCHED_AT), body=b"")
        marker = ("var marker4 = new google.maps.Marker({\n position: { lat: 42.7, lng: -4.2},\n map: map,\n"
                  " title: 'Alar',\n icon: {\n url: '/wp-content/plugins/easycharger/icons/icono-marino.png',\n")
        detail = {"id": 4, "status": {"id": 1, "name": "Activo"}, "_fetched_at": "2026-09-20T08:00:00Z",
                  "connectors": [{"connector_type": "CCS", "max_electric_power": 150.0, "available": 1, "in_use": 0, "unavailable": 0}]}
        sites = ing.parse_zunder_markers(marker)
        with_detail = ing.parse_zunder_sites(sites, {"4": detail}, source_artifact=page)[0]["sources"][0]
        map_only = ing.parse_zunder_sites(sites, {}, source_artifact=page)[0]["sources"][0]
        self.assertEqual(with_detail["source_fetched_at"], "2026-09-20T08:00:00Z")
        self.assertEqual(map_only["source_fetched_at"], FETCHED_AT)

    def test_openstreetmap_keeps_fetch_edit_and_database_times(self) -> None:
        payload = {"osm3s": {"timestamp_osm_base": "2026-11-04T09:58:00Z"}, "elements": [{
            "type": "node", "id": 1, "lat": 39.47, "lon": -0.37, "timestamp": "2024-05-06T07:08:09Z",
            "tags": {"amenity": "charging_station", "name": "OSM clock", "fee": "no", "socket:type2": "2", "capacity": "2"}}]}
        artifact = dataclasses.replace(fetch_artifact_at(FETCHED_AT), body=json.dumps(payload).encode())
        records = []
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(ing, "spain_tiles", return_value=[(39, -1, 40, 0)]))
            stack.enter_context(mock.patch.object(ing, "cached_overpass", return_value=artifact))
            stack.enter_context(mock.patch.object(ing.time, "sleep"))
            ing.source_osm(records)
        self.assertEqual(len(records), 1)
        self.assertEqual(listing_clocks(records[0], "osm"), {
            "fetched_at": FETCHED_AT, "published_at": "2026-11-04T09:58:00Z", "source_last_updated": "2024-05-06T07:08:09Z"})

    def test_dgt_keeps_the_feed_publication_time_and_the_site_update_time(self) -> None:
        feed = """<?xml version="1.0"?><p xmlns:com="urn:com" xmlns:fac="urn:fac" xmlns:egi="urn:egi">
          <com:publicationTime>2026-11-06T10:16:16.849+01:00</com:publicationTime>
          <egi:energyInfrastructureSite id="1"><fac:name><com:values><com:value>S</com:value></com:values></fac:name>
          <fac:lastUpdated>2023-11-21T11:39:04.000+01:00</fac:lastUpdated>
          <fac:locationReference><latitude>40.4</latitude><longitude>-3.7</longitude></fac:locationReference>
          <egi:energyInfrastructureStation><egi:refillPoint/></egi:energyInfrastructureStation>
          </egi:energyInfrastructureSite></p>"""
        sites = ing.parse_dgt_nap_feed(io.BytesIO(feed.encode()), minimum_sites=1)
        record = ing.nap_records_from_sites(sites, source_artifact=fetch_artifact_at(FETCHED_AT))[0]
        self.assertEqual(listing_clocks(record, "dgt"), {
            "fetched_at": FETCHED_AT, "published_at": "2026-11-06T09:16:16Z", "source_last_updated": "2023-11-21T10:39:04Z"})

    def test_canarias_map_edit_time_is_the_source_update_time(self) -> None:
        feature = ct_feature("9", "Sin precio", -16.25, 28.46, "marcador-4.png", "<p>x</p>")
        feature["properties"]["modified"] = "1762000000"
        without = ct_feature("10", "Otro", -16.26, 28.47, "marcador-4.png", "<p>y</p>")
        rows = ing.parse_canarias_tecarga_geojson(ct_payload([feature, without]), minimum_features=1,
                                                  source_artifact=fetch_artifact_at(FETCHED_AT))
        by_name = {row["name"]: row["sources"][0] for row in rows}
        self.assertEqual(by_name["Sin precio"]["source_last_updated"], iso_from_epoch(1762000000))
        self.assertNotIn("source_last_updated", by_name["Otro"])


class DatasetPublishedAtTests(unittest.TestCase):
    def published(self, metadata, read, rows_at=FETCHED_AT):
        side_effect = metadata if isinstance(metadata, Exception) else None
        with mock.patch.object(ing, "cached_json", return_value=metadata, side_effect=side_effect):
            return ing.dataset_published_at("https://example.test/meta", "meta.json", 24, fetch_artifact_at(rows_at), read)

    def test_catalonia_reads_the_socrata_row_update_time(self) -> None:
        self.assertEqual(self.published({"rowsUpdatedAt": 1762000000}, ing.socrata_rows_updated_at), iso_from_epoch(1762000000))

    def test_castilla_leon_reads_the_dataset_data_time(self) -> None:
        metadata = {"metas": {"default": {"data_processed": "2022-01-12T11:47:05.200000+00:00"}}}
        self.assertEqual(self.published(metadata, ing.opendatasoft_data_processed), "2022-01-12T11:47:05.200000+00:00")

    def test_absent_or_failed_metadata_leaves_the_time_unknown(self) -> None:
        self.assertIsNone(self.published({}, ing.socrata_rows_updated_at))
        self.assertIsNone(self.published({"metas": {}}, ing.opendatasoft_data_processed))
        self.assertIsNone(self.published(TimeoutError("down"), ing.socrata_rows_updated_at))

    def test_a_publication_after_the_rows_were_fetched_is_not_shown_against_them(self) -> None:
        self.assertIsNone(self.published({"rowsUpdatedAt": 1762000000}, ing.socrata_rows_updated_at, rows_at="2025-01-01T00:00:00Z"))

    def test_a_failed_metadata_fetch_still_keeps_the_rows(self) -> None:
        rows = [{"id": "1", "promotor_gestor": "Op", "tipus_connexi": "MENNEKES.M", "latitud": "41.5", "longitud": "2.1",
                 "kw": "22", "adre_a": "Carrer, 1", "municipi": "Sabadell"}]
        artifact = dataclasses.replace(fetch_artifact_at(FETCHED_AT), body=json.dumps(rows).encode())
        records = []
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(ing, "cached_fetch", return_value=artifact))
            stack.enter_context(mock.patch.object(ing, "cached_json", side_effect=TimeoutError("down")))
            ing.source_catalonia_icaen(records)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["sources"][0]["source_fetched_at"], FETCHED_AT)
        self.assertNotIn("source_published_at", records[0]["sources"][0])


class PriceSourceFetchClockTests(unittest.TestCase):
    NOW = 1_793_000_000.0

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.body = "[]"

    def opener(self, request, *, timeout):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = self.body.encode()
        return response

    def collector(self, opener=None, **kwargs) -> price_evidence.Collector:
        kwargs.setdefault("now", self.NOW)
        return price_evidence.Collector(self._tmp.name, opener=opener or self.opener, pause=0, **kwargs)

    def seed_cache(self, key: str, age_seconds: float) -> str:
        fetched_at = iso_from_epoch(self.NOW - age_seconds)
        directory = Path(self._tmp.name) / "price_evidence_v1"
        directory.mkdir(exist_ok=True)
        (directory / f"{key}.json").write_text(json.dumps(
            {"schema": 1, "url": "https://example.test/a", "fetched_at": fetched_at, "data": []}))
        return fetched_at

    def read(self, collector: price_evidence.Collector, key: str, source: str = "electromaps"):
        return collector.cached(key, "https://example.test/a", source, "list", json.loads)

    def test_a_network_fetch_records_its_own_time(self) -> None:
        collector = self.collector()
        self.read(collector, "electromaps_tile_0")
        self.assertEqual(collector.fetch_clocks()["electromaps"], {
            "at": iso_from_epoch(self.NOW), "oldest_part_at": iso_from_epoch(self.NOW),
            "parts": 1, "failed_parts": 0, "oldest_failed_at": None})

    def test_a_fetch_is_stamped_when_it_happens_not_when_the_collector_started(self) -> None:
        with mock.patch.object(price_evidence.time, "time", side_effect=[self.NOW, self.NOW + 90]):
            collector = self.collector(now=None)
            self.assertEqual(collector.fetched_now(), iso_from_epoch(self.NOW + 90))

    def test_a_fresh_cached_copy_reports_the_time_it_was_fetched(self) -> None:
        fetched_at = self.seed_cache("chargemap_tile_0", 3600)
        collector = self.collector()
        self.read(collector, "chargemap_tile_0", "chargemap")
        self.assertEqual(collector.fetch_clocks()["chargemap"]["at"], fetched_at)

    def test_a_failed_fetch_counts_as_failed_and_keeps_the_old_time(self) -> None:
        fetched_at = self.seed_cache("electromaps_tile_0", 200_000)
        collector = self.collector(opener=mock.Mock(side_effect=TimeoutError("down")))
        self.read(collector, "electromaps_tile_0")
        clocks = collector.fetch_clocks()["electromaps"]
        self.assertEqual((clocks["at"], clocks["failed_parts"], clocks["oldest_failed_at"]), (fetched_at, 1, fetched_at))

    def test_a_request_the_budget_withheld_is_not_a_failed_fetch(self) -> None:
        fetched_at = self.seed_cache("electromaps_tile_0", 200_000)
        collector = self.collector(list_budget=0)
        self.read(collector, "electromaps_tile_0")
        clocks = collector.fetch_clocks()["electromaps"]
        self.assertEqual((clocks["at"], clocks["failed_parts"]), (fetched_at, 0))

    def test_no_successful_fetch_means_no_clock(self) -> None:
        collector = self.collector(opener=mock.Mock(side_effect=TimeoutError("down")))
        self.read(collector, "electromaps_tile_0")
        self.assertNotIn("electromaps", collector.fetch_clocks())

    def test_repsol_records_the_page_fetch_in_the_collection_report(self) -> None:
        self.body = "<html>no tariff here</html>"
        report = self.collector(detail_budget=0, list_budget=0).enrich([])
        self.assertEqual(report["fetch_clocks"]["repsol"]["at"], iso_from_epoch(self.NOW))
        self.assertNotIn("electromaps", report["fetch_clocks"])

    def test_matrix_column_reads_the_report_and_says_when_a_part_failed(self) -> None:
        clocks = {"electromaps": {"at": "2026-11-04T10:00:00Z", "oldest_part_at": "2026-10-01T10:00:00Z", "parts": 3,
                                  "failed_parts": 1, "oldest_failed_at": "2026-10-01T10:00:00Z"},
                  "repsol": {"at": "not-a-time", "oldest_part_at": "not-a-time", "parts": 1, "failed_parts": 0},
                  "osm": {"at": "2026-11-04T10:00:00Z", "oldest_part_at": "2026-11-04T10:00:00Z", "parts": 1, "failed_parts": 0}}
        intel = {"records": [], "price_evidence_collection": {"fetch_clocks": clocks}}
        fetches = source_matrix.column_fetches(intel)
        self.assertEqual(fetches["electromaps"], {"at": "2026-11-04T10:00:00Z", "oldest_part_at": "2026-10-01T10:00:00Z",
                                                  "parts": 3, "failed_parts": 1})
        self.assertNotIn("repsol", fetches)
        self.assertNotIn("osm", fetches)  # only price columns read this report
        self.assertEqual(source_matrix.degraded_columns(intel, [])["electromaps"],
                         "Latest fetch failed; cached copy from 2026-10-01.")

    def test_clocks_survive_the_source_policy_pass_and_name_no_host(self) -> None:
        collector = self.collector()
        self.read(collector, "electromaps_tile_0")
        payload = {"records": [], "errors": [], "license_notes": [], "fetch_evidence": {"artifacts": []},
                   "price_evidence_collection": {"fetch_clocks": collector.fetch_clocks()}}
        cleaned, _ = ing.apply_source_policy_exclusions_to_payload(payload)
        clocks = cleaned["price_evidence_collection"]["fetch_clocks"]
        self.assertEqual(clocks["electromaps"]["parts"], 1)
        self.assertNotIn("http", json.dumps(clocks))


if __name__ == "__main__":
    unittest.main()
