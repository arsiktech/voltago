#!/usr/bin/env python3
"""Rows built from an old cached copy, after a failed fetch, say so."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import ingest_sources as ing  # noqa: E402
import server  # noqa: E402


def artifact(*, fallback: bool) -> ing.FetchArtifact:
    return ing.FetchArtifact(
        body=b"fixture", url="https://example.test/source", cache_name="fixture.json",
        fetched_at="2026-10-20T19:38:36Z", age_seconds=334797, hit=True,
        fallback=fallback, stale=fallback, error="HTTPError" if fallback else None,
    )


def build(source_artifact: ing.FetchArtifact, evidence: str = "Original evidence.") -> dict:
    records: list = []
    ing.add_record(records, source="Fixture source", source_url="https://example.test/source",
                   name="Fixture charger", lat=28.46, lon=-16.25, evidence=evidence,
                   source_artifact=source_artifact)
    return records[0]


class CacheFallbackLabelTests(unittest.TestCase):
    def test_fallback_row_says_it_is_an_old_cached_copy(self) -> None:
        row = build(artifact(fallback=True))
        source = row["sources"][0]
        self.assertTrue(source["evidence"].startswith("Cached copy from 2026-10-20: the latest fetch"))
        self.assertIn("Original evidence.", source["evidence"])
        self.assertTrue(source["source_cache_fallback"])
        self.assertEqual(source["source_fetched_at"], "2026-10-20T19:38:36Z")
        self.assertEqual(row["source_fetched_at"], "2026-10-20T19:38:36Z")

    def test_fresh_row_is_unchanged(self) -> None:
        row = build(artifact(fallback=False))
        self.assertEqual(row["sources"][0]["evidence"], "Original evidence.")
        self.assertNotIn("source_cache_fallback", row["sources"][0])

    def test_label_survives_evidence_truncation(self) -> None:
        row = build(artifact(fallback=True), evidence="x" * 2000)
        self.assertTrue(row["sources"][0]["evidence"].startswith("Cached copy from"))

    def test_source_health_lists_cache_fallbacks(self) -> None:
        intel = {"records": [], "errors": [], "fetch_evidence": {"artifacts": [
            {"cache_name": "fresh.json", "url": "https://example.test/a", "cache": {"fallback": False}},
            {"cache_name": "old.json", "url": "https://example.test/b", "fetched_at": "2026-10-20T19:38:36Z",
             "error": "HTTPError", "cache": {"fallback": True, "stale": True}},
        ]}}
        with patch("server.load_intelligence_meta", return_value=intel):
            health = server.source_health_summary()
        self.assertEqual(health["cache_fallbacks"], [{
            "cache_name": "old.json", "url": "https://example.test/b",
            "fetched_at": "2026-10-20T19:38:36Z", "error": "HTTPError",
        }])


if __name__ == "__main__":
    unittest.main()
