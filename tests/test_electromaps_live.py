#!/usr/bin/env python3
"""Electromaps real-time + freshness honesty tests.

Core invariants the user asked for:
  - show real-time busy/free ONLY from genuine telemetry (realtime=true + known state),
  - never present a no-telemetry / stale connector as live,
  - last_seen reflects when we OBSERVED the live state (now), not the old state-change time.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402

FETCHED_AT = server.now_iso()


def lead(**over):
    base = {
        "id": "intel_em_test",
        "name": "Test EM",
        "lat": 39.47,
        "lon": -0.37,
        "price_min": 0.0,
        "is_free": True,
        "is_cheap": True,
        "source": "Electromaps web-map API",
        "source_url": "https://map.electromaps.com/en/p/999",
    }
    base.update(over)
    return base


def conn(status, realtime, ts="2026-06-20T04:20:36+0000", kw=7.4, typ="IEC_62196_T2"):
    return {"type": typ, "status": status, "realtime": realtime, "kw": kw, "status_updated_at": ts}


class ElectromapsLiveTests(unittest.TestCase):
    def test_parse_iso_handles_basic_offset(self) -> None:
        self.assertIsNotNone(server.parse_iso("2026-06-20T04:20:36+0000"))
        self.assertIsNotNone(server.parse_iso("2026-06-20T04:20:36Z"))
        self.assertIsNotNone(server.parse_iso("2026-06-20T04:20:36+00:00"))

    def test_realtime_available_promotes_to_live(self) -> None:
        d = {"id": 999, "name": "Test EM", "connectors": [conn("AVAILABLE", True), conn("OCCUPIED", True)]}
        st = server.build_electromaps_station(lead(), d, FETCHED_AT)
        self.assertIsNotNone(st)
        self.assertTrue(st["verification"]["live_verified"])
        self.assertEqual(st["available"], 1)
        self.assertGreaterEqual(st.get("busy_connectors", 0), 1)
        # last_seen is the observation time (now), NOT the old state-change timestamp
        self.assertEqual(st["last_seen"], FETCHED_AT)
        self.assertEqual(st["status"], "AVAILABLE")

    def test_occupied_only_reads_busy_not_available(self) -> None:
        d = {"id": 1, "connectors": [conn("OCCUPIED", True)]}
        st = server.build_electromaps_station(lead(), d, FETCHED_AT)
        self.assertIsNotNone(st)
        self.assertEqual(st["available"], 0)
        self.assertEqual(st["status"], "OCCUPIED")

    def test_no_realtime_telemetry_is_dropped(self) -> None:
        d = {"id": 1, "connectors": [conn("AVAILABLE", False), conn("WORKING", False)]}
        self.assertIsNone(server.build_electromaps_station(lead(), d, FETCHED_AT))

    def test_unknown_status_is_not_live(self) -> None:
        d = {"id": 1, "connectors": [conn("UNKNOWN", True)]}
        self.assertIsNone(server.build_electromaps_station(lead(), d, FETCHED_AT))

    def test_epoch_timestamp_connector_excluded(self) -> None:
        d = {"id": 1, "connectors": [conn("AVAILABLE", True, ts="1970-01-01T00:00:00+0000")]}
        self.assertIsNone(server.build_electromaps_station(lead(), d, FETCHED_AT))

    def test_live_aggregator_tier_is_live_only_when_live(self) -> None:
        v = server.verification_profile({"reliability": "official-live-aggregator"}, live=True)
        self.assertEqual(v["level"], "live")
        self.assertTrue(v["live_verified"])
        # As a non-live lead it must NOT claim live verification.
        v2 = server.verification_profile({"reliability": "official-live-aggregator"}, live=False)
        self.assertFalse(v2["live_verified"])

    def test_dead_all_out_of_service_not_promoted(self) -> None:
        d = {"id": 1, "connectors": [conn("OUT_OF_SERVICE", True), conn("OUT_OF_SERVICE", True)]}
        st = server.build_electromaps_station(lead(), d, FETCHED_AT)
        # out-of-service connectors are real telemetry, so it can still build, but is never "available"
        if st is not None:
            self.assertEqual(st["available"], 0)
            self.assertNotEqual(st["status"], "AVAILABLE")


if __name__ == "__main__":
    unittest.main()
