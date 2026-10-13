#!/usr/bin/env python3
"""Cache/source provenance contracts for the public-source ingestion pipeline."""

from __future__ import annotations

import gzip
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

    def test_openchargemap_record_dates_are_snapshot_not_download_time(self) -> None:
        import test_ocm_export as fx

        archive = ing.CACHE / "fixture.tar.gz"
        fx.make_archive(archive, {123: fx.compact_poi(123, title="OCM provenance charger", lat=39.47, lon=-0.37, cost="Free")})
        net = fx.Network(manifest=fx.MANIFEST, archive=archive.read_bytes(), legacy=None)
        before = time.time()
        records = []
        with mock.patch.object(fx.ocm, "MIN_COUNTRY_FILES", 1), mock.patch.object(ing, "policy_urlopen", net):
            ing.source_openchargemap(records)
        after = time.time()

        self.assertEqual(len(records), 1)
        # The record is dated by the export commit (a bulk-export date), never by when we downloaded it.
        self.assertEqual(records[0]["source_fetched_at"], fx.COMMIT_DATE)
        lookup = datetime.fromisoformat(records[0]["sources"][0]["source_lookup_at"].replace("Z", "+00:00")).timestamp()
        self.assertGreaterEqual(lookup, before - 1)
        self.assertLessEqual(lookup, after + 1)
        self.assertEqual(records[0]["last_seen"], "2025-10-30T18:01:00Z")
        # Cached rebuild: the archive provenance records the cache hit and its original fetch time.
        again = []
        with mock.patch.object(fx.ocm, "MIN_COUNTRY_FILES", 1), mock.patch.object(ing, "policy_urlopen", side_effect=AssertionError("cache hit must not fetch")):
            ing.source_openchargemap(again)
        self.assertEqual(again[0]["source_fetched_at"], fx.COMMIT_DATE)

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


if __name__ == "__main__":
    unittest.main()
