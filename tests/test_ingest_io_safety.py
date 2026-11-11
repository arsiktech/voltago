#!/usr/bin/env python3
"""ingest_sources I/O safety: bounded reads, no needless Overpass pauses, atomic output."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
import ingest_sources as ing  # noqa: E402


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, amount: int = -1) -> bytes:
        return self.body if amount is None or amount < 0 else self.body[:amount]


def overpass_artifact(*, hit: bool, fallback: bool = False) -> ing.FetchArtifact:
    return ing.FetchArtifact(
        body=b'{"elements":[],"osm3s":{"timestamp_osm_base":"2026-11-11T00:00:00Z"}}',
        url="https://overpass.example.test/api/interpreter",
        cache_name="osm_es_area_chargers_00.json",
        fetched_at="2026-11-11T00:00:00Z",
        age_seconds=60.0 if hit else 0.0,
        hit=hit,
        fallback=fallback,
        stale=False,
    )


class FetchSizeLimitTests(unittest.TestCase):
    def test_a_body_over_the_limit_raises_a_clear_error(self) -> None:
        with mock.patch.object(ing, "policy_urlopen", return_value=FakeResponse(b"x" * 20)):
            with self.assertRaisesRegex(RuntimeError, "larger than 8 bytes"):
                ing.fetch("https://example.test/feed", max_bytes=8)

    def test_a_body_at_the_limit_is_returned_whole(self) -> None:
        with mock.patch.object(ing, "policy_urlopen", return_value=FakeResponse(b"x" * 8)):
            self.assertEqual(ing.fetch("https://example.test/feed", max_bytes=8), b"x" * 8)

    def test_the_default_limit_leaves_room_for_the_largest_feeds(self) -> None:
        with mock.patch.object(ing, "policy_urlopen", return_value=FakeResponse(b'{"ok":true}')):
            self.assertEqual(ing.fetch("https://example.test/feed"), b'{"ok":true}')
        self.assertGreaterEqual(ing.FETCH_MAX_BYTES, 256 * 1024 * 1024)


class OverpassTilePauseTests(unittest.TestCase):
    def tile(self, artifact: ing.FetchArtifact) -> mock.MagicMock:
        with mock.patch.object(ing, "cached_overpass", return_value=artifact), \
             mock.patch.object(ing.time, "sleep") as sleep:
            elements, failures = ing.osm_fetch_box(39.0, -1.0, 40.0, 0.0, "00")
        self.assertEqual((elements, failures), ([], []))
        return sleep

    def test_a_cached_tile_does_not_pause(self) -> None:
        self.tile(overpass_artifact(hit=True)).assert_not_called()

    def test_a_network_tile_pauses_once(self) -> None:
        self.tile(overpass_artifact(hit=False)).assert_called_once_with(ing.OVERPASS_TILE_PAUSE_SECONDS)

    def test_a_cache_fallback_after_a_failed_request_still_pauses(self) -> None:
        self.tile(overpass_artifact(hit=True, fallback=True)).assert_called_once_with(ing.OVERPASS_TILE_PAUSE_SECONDS)


class AtomicOutputTests(unittest.TestCase):
    def test_a_failed_write_keeps_the_previous_dataset_whole(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / "in.json"
            source.write_text(json.dumps({"records": [], "raw_records": 0, "errors": []}), encoding="utf-8")
            out = folder / "free_intelligence.json"
            previous = '{"records":["previous complete dataset"]}'
            out.write_text(previous, encoding="utf-8")
            original_write_text = Path.write_text

            def crash_half_way(path: Path, data: str, *args: object, **kwargs: object) -> int:
                original_write_text(path, data[: len(data) // 2], *args, **kwargs)
                raise OSError("disk full")

            argv = ["ingest_sources.py", "--policy-clean-from", str(source), "--out", str(out)]
            with mock.patch.object(sys, "argv", argv), \
                 mock.patch.object(Path, "write_text", crash_half_way), \
                 mock.patch("builtins.print"):
                with self.assertRaises(OSError):
                    ing.main()
            self.assertEqual(out.read_text(encoding="utf-8"), previous)
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ["free_intelligence.json", "in.json"])

    def test_a_successful_write_replaces_the_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / "in.json"
            source.write_text(json.dumps({"records": [], "raw_records": 0, "errors": []}), encoding="utf-8")
            out = folder / "free_intelligence.json"
            out.write_text("old", encoding="utf-8")
            argv = ["ingest_sources.py", "--policy-clean-from", str(source), "--out", str(out)]
            with mock.patch.object(sys, "argv", argv), mock.patch("builtins.print"):
                ing.main()
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["records"], [])
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ["free_intelligence.json", "in.json"])


if __name__ == "__main__":
    unittest.main()
