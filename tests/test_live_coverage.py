#!/usr/bin/env python3
from __future__ import annotations

import sys
import time
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


def live_station(station_id: str, decision: str = "go", available: int = 1, *, lead: bool = False, live_verified: bool = True) -> dict:
    return {
        "id": station_id,
        "decision": {"level": decision},
        "available": available,
        "status": "AVAILABLE" if available > 0 else "UNKNOWN",
        "lead": lead,
        "verification": {"level": "live" if live_verified else "directory", "live_verified": live_verified},
    }


def gaia_station(station_id: str = "gaia-live") -> dict:
    return {
        "id": station_id,
        "name": "Gaia public charger",
        "address": "Carrer Test 1, València",
        "lat": 39.47,
        "lon": -0.37,
        "source": "Gaia public start page",
        "source_url": "https://www.gaiacharge.com/cpo/jump/test/start-session/1",
        "source_type": "public-live",
        "reliability": "official-live",
        "price_min": 0.0,
        "price_max": 0.0,
        "is_free": True,
        "is_cheap": True,
        "tesla_ok": True,
        "status": "AVAILABLE",
        "available": 1,
        "total_evse": 1,
        "parking": {"kind": "street_or_municipal"},
        "connectors": [{
            "connector_id": "gaia-1",
            "label": "Type 2",
            "type": "Type 2",
            "status": "AVAILABLE",
            "power_kw": 22.0,
            "price": "Free",
            "is_free": True,
            "tesla_ok": True,
            "fast": False,
        }],
    }


class LiveCoverageTests(unittest.TestCase):
    def test_sparse_live_with_many_leads_is_degraded(self) -> None:
        profile = server.live_coverage_profile(
            [live_station("live-one")],
            [{"id": "lead-one"}, {"id": "lead-two"}],
            minimum=3,
        )

        self.assertEqual(profile["mode"], "degraded")
        self.assertTrue(profile["is_degraded"])
        self.assertEqual(profile["live_route_ready"], 1)
        self.assertEqual(profile["lead_count"], 2)
        self.assertIn("Low live coverage", profile["warning"])
        self.assertIn("provider-source verification", profile["warning"])

    def test_enough_live_stations_is_standard(self) -> None:
        profile = server.live_coverage_profile(
            [
                live_station("live-one"),
                live_station("live-two"),
                live_station("live-three"),
            ],
            [{"id": "lead-one"}],
            minimum=3,
        )

        self.assertEqual(profile["mode"], "standard")
        self.assertFalse(profile["is_degraded"])
        self.assertIsNone(profile["warning"])

    def test_coverage_counts_only_go_decisions_as_route_ready(self) -> None:
        profile = server.live_coverage_profile(
            [
                live_station("check-first", decision="check"),
                live_station("go-now"),
            ],
            [],
            minimum=2,
        )

        self.assertEqual(profile["live_route_ready"], 1)
        self.assertEqual(profile["mode"], "degraded")

    def test_route_ready_requires_live_non_lead_available_row(self) -> None:
        self.assertTrue(server.route_ready_now(live_station("good")))
        self.assertFalse(server.route_ready_now(live_station("lead", lead=True)))
        self.assertFalse(server.route_ready_now(live_station("unverified", live_verified=False)))
        self.assertFalse(server.route_ready_now(live_station("unavailable", available=0)))
        self.assertFalse(server.route_ready_now({"id": "decision-only", "decision": {"level": "go"}}))

    def test_viewport_cache_key_uses_snapped_bbox_envelope(self) -> None:
        narrow = {"latitude_sw": 39.40, "longitude_sw": -0.42, "latitude_ne": 39.50, "longitude_ne": -0.32}
        wide = {"latitude_sw": 39.20, "longitude_sw": -0.62, "latitude_ne": 39.70, "longitude_ne": -0.12}

        self.assertEqual(server.viewport_tile_key(narrow, "default"), "")
        self.assertNotEqual(
            server.viewport_tile_key(narrow, "explicit"),
            server.viewport_tile_key(wide, "explicit"),
        )
        self.assertRegex(server.viewport_tile_key(narrow, "explicit"), r"^-?\d+\.\d{2},-?\d+\.\d{2},-?\d+\.\d{2},-?\d+\.\d{2}\|$")

    def test_total_live_failure_with_no_cache_degrades_to_200_leads_only(self) -> None:
        """A bare 502 reads as "server down" on every client (the iPhone showed
        'Live source unreachable' for the whole app). Honest degradation: 200 with ZERO
        live stations (fail-closed), source-backed leads intact, explicit warning."""
        original_cache = deepcopy(server.CACHE)
        try:
            server.CACHE.clear()
            with (
                patch("server.fetch_mapareve", side_effect=RuntimeError("blocked ip")),
                patch("server.fetch_gaia_live", return_value={"stations": [], "raw_count": 0, "cache": {}, "errors": []}),
                patch("server.fetch_electromaps_live", return_value={"stations": [], "checked_ids": set(), "promoted_ids": set(), "dead_ids": set()}),
            ):
                status, payload = server.live_response({
                    "include_leads": ["true"],
                    "max_price": ["0.25"],
                    "bbox": ["39.30,-0.55,39.62,-0.20"],
                    "force": ["true"],
                })
            self.assertEqual(status, 200)
            self.assertEqual(payload["stations"], [])
            self.assertGreater(len(payload["leads"]), 0)
            self.assertTrue(payload["cache"].get("live_failed"))
            self.assertIn("Live source unavailable", payload["warning"])
            engine = payload["meta"]["engine"]
            self.assertEqual(engine["version"], server.ENGINE_VERSION)
            self.assertEqual(engine["counts"]["stations"], 0)
            self.assertEqual(engine["counts"]["route_ready_live"], 0)
            self.assertGreater(engine["counts"]["leads"], 0)
            self.assertIn("mapareve", [stage["name"] for stage in engine["stages"]])
            self.assertIn("route_ready_rule", engine["honesty"])
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)

    def test_mapareve_failure_with_fresh_gaia_is_partial_live_not_globally_failed(self) -> None:
        original_cache = deepcopy(server.CACHE)
        try:
            server.CACHE.clear()
            with (
                patch("server.fetch_mapareve", side_effect=RuntimeError("blocked ip")),
                patch("server.fetch_gaia_live", return_value={
                    "stations": [gaia_station()],
                    "raw_count": 1,
                    "cache": {"hit": False, "stale": False, "age_seconds": 0},
                    "errors": [],
                }),
                patch("server.load_all_leads", return_value=[]),
            ):
                status, payload = server.live_response({
                    "include_leads": ["false"],
                    "max_price": ["0.25"],
                    "bbox": ["39.30,-0.55,39.62,-0.20"],
                    "force": ["true"],
                })

            self.assertEqual(status, 200)
            self.assertEqual([station["id"] for station in payload["stations"]], ["gaia-live"])
            self.assertFalse(payload["cache"]["live_failed"])
            self.assertTrue(payload["cache"]["fresh_live_authority"])
            self.assertFalse(payload["cache"]["mixed_live"])
            self.assertTrue(server.route_ready_now(payload["stations"][0]))
            self.assertIn("fresh permitted secondary-source status remains live", payload["warning"])
            self.assertNotIn("nothing is presented as live", payload["warning"])
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)

    def test_stale_gaia_cache_is_never_fresh_authority_or_route_ready(self) -> None:
        original_cache = deepcopy(server.CACHE)
        try:
            server.CACHE.clear()
            with (
                patch("server.fetch_mapareve", side_effect=RuntimeError("blocked ip")),
                patch("server.fetch_gaia_live", return_value={
                    "stations": [gaia_station("gaia-stale")],
                    "raw_count": 1,
                    "cache": {"hit": True, "stale": True, "age_seconds": 180},
                    "errors": [],
                    "warning": "Gaia cache is stale.",
                }),
                patch("server.load_all_leads", return_value=[]),
            ):
                status, payload = server.live_response({
                    "include_leads": ["false"],
                    "max_price": ["0.25"],
                    "bbox": ["39.30,-0.55,39.62,-0.20"],
                    "force": ["true"],
                })

            self.assertEqual(status, 200)
            self.assertFalse(payload["cache"]["live_failed"])
            self.assertTrue(payload["cache"]["stale"])
            self.assertFalse(payload["cache"]["fresh_live_authority"])
            self.assertTrue(payload["stations"][0]["cache_stale"])
            self.assertFalse(payload["stations"][0]["verification"]["live_verified"])
            self.assertFalse(server.route_ready_now(payload["stations"][0]))
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)

    def test_fresh_mapareve_plus_distinct_stale_gaia_is_explicitly_mixed(self) -> None:
        original_cache = deepcopy(server.CACHE)
        fresh_mapareve = gaia_station("mapareve-fresh")
        fresh_mapareve.update({
            "source": "MapaREVE public live OCPI",
            "source_url": "https://www.mapareve.es/",
        })
        stale_gaia = gaia_station("gaia-stale-distinct")
        stale_gaia.update({"lat": 39.50, "lon": -0.40})
        try:
            server.CACHE.clear()
            with (
                patch("server.fetch_mapareve", return_value={
                    "stations": [fresh_mapareve],
                    "raw_count": 1,
                    "fetched_at": server.now_iso(),
                    "errors": [],
                }),
                patch("server.fetch_gaia_live", return_value={
                    "stations": [stale_gaia],
                    "raw_count": 1,
                    "cache": {"hit": True, "stale": True, "age_seconds": 180},
                    "errors": [],
                    "warning": "Gaia cache is stale.",
                }),
                patch("server.load_all_leads", return_value=[]),
            ):
                status, payload = server.live_response({
                    "include_leads": ["false"],
                    "max_price": ["0.25"],
                    "bbox": ["39.30,-0.55,39.62,-0.20"],
                    "force": ["true"],
                })

            self.assertEqual(status, 200)
            rows = {station["id"]: station for station in payload["stations"]}
            self.assertTrue(payload["cache"]["fresh_live_authority"])
            self.assertTrue(payload["cache"]["mixed_live"])
            self.assertTrue(server.route_ready_now(rows["mapareve-fresh"]))
            self.assertTrue(rows["gaia-stale-distinct"]["cache_stale"])
            self.assertFalse(server.route_ready_now(rows["gaia-stale-distinct"]))
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)

    def test_fresh_gaia_replacement_clears_stale_response_flag_when_no_cached_row_survives(self) -> None:
        original_cache = deepcopy(server.CACHE)
        stale_mapareve = gaia_station("mapareve-stale-overlap")
        stale_mapareve.update({
            "source": "MapaREVE public live OCPI",
            "source_url": "https://www.mapareve.es/",
        })
        try:
            server.CACHE.clear()
            server.CACHE.update({
                "ts": time.time() - 300,
                "payload": {
                    "stations": [stale_mapareve],
                    "raw_count": 1,
                    "fetched_at": server.now_iso(),
                },
                "raw_count": 1,
                "error": None,
                "key": "0.25:False:False",
            })
            with (
                patch("server.fetch_mapareve", side_effect=RuntimeError("upstream down")),
                patch("server.fetch_gaia_live", return_value={
                    "stations": [gaia_station("gaia-fresh-replacement")],
                    "raw_count": 1,
                    "cache": {"hit": False, "stale": False, "age_seconds": 0},
                    "errors": [],
                }),
                patch("server.load_all_leads", return_value=[]),
            ):
                status, payload = server.live_response({
                    "include_leads": ["false"],
                    "max_price": ["0.25"],
                    "force": ["true"],
                })

            self.assertEqual(status, 200)
            self.assertEqual([row["id"] for row in payload["stations"]], ["gaia-fresh-replacement"])
            self.assertTrue(payload["cache"]["fresh_live_authority"])
            self.assertFalse(payload["cache"]["mixed_live"])
            self.assertFalse(payload["cache"]["stale"])
            self.assertFalse(payload["cache"]["live_failed"])
            self.assertTrue(server.route_ready_now(payload["stations"][0]))
            self.assertIn("fresh permitted secondary-source status remains live", payload["warning"])
            self.assertNotIn("cached rows are marked stale", payload["warning"])
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)

    def test_live_response_preserves_stale_cache_and_coverage_warnings(self) -> None:
        original_cache = deepcopy(server.CACHE)
        try:
            server.CACHE.clear()
            server.CACHE.update({
                "ts": time.time() - 300,
                "payload": {"stations": [], "raw_count": 0, "fetched_at": server.now_iso()},
                "raw_count": 0,
                "error": None,
                "key": "0.25:False:False",
            })
            with (
                patch("server.fetch_mapareve", side_effect=RuntimeError("upstream down")),
                patch("server.fetch_gaia_live", return_value={"stations": [], "raw_count": 0, "cache": {}, "errors": []}),
                patch("server.fetch_electromaps_live", return_value={"stations": [], "checked_ids": set(), "promoted_ids": set(), "dead_ids": set()}),
            ):
                status, payload = server.live_response({
                    "include_leads": ["true"],
                    "max_price": ["0.25"],
                    "force": ["true"],
                })

            self.assertEqual(status, 200)
            self.assertFalse(payload["cache"]["stale"])
            self.assertTrue(payload["cache"]["live_failed"])
            self.assertIn("no cached live rows remain", payload["warning"])
            self.assertIn("Low live coverage", payload["warning"])
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)

    def test_stale_same_filter_cache_recomputes_live_decision(self) -> None:
        original_cache = deepcopy(server.CACHE)
        old_seen = (datetime.now(timezone.utc) - timedelta(hours=4, minutes=10)).isoformat().replace("+00:00", "Z")
        try:
            server.CACHE.clear()
            server.CACHE.update({
                "ts": time.time() - 300,
                "payload": {
                    "stations": [{
                        "id": "stale-go",
                        "name": "Stale available charger",
                        "lat": 39.47,
                        "lon": -0.37,
                        "available": 1,
                        "status": "AVAILABLE",
                        "is_free": True,
                        "is_cheap": True,
                        "price_min": 0,
                        "tesla_ok": True,
                        "parking": {"kind": "street_or_municipal"},
                        "source_url": "https://example.com/stale",
                        "source": "Test live source",
                        "reliability": "official-live",
                        "last_seen": old_seen,
                        "freshness_minutes": 1,
                        "decision": {
                            "level": "go",
                            "label": "Go now",
                            "reason": "Old cached decision that must be recomputed.",
                        },
                    }],
                    "raw_count": 1,
                    "fetched_at": old_seen,
                },
                "raw_count": 1,
                "error": None,
                "key": "0.25:False:False",
            })
            with (
                patch("server.fetch_mapareve", side_effect=RuntimeError("upstream down")),
                patch("server.fetch_gaia_live", return_value={"stations": [], "raw_count": 0, "cache": {}, "errors": []}),
                patch("server.fetch_electromaps_live", return_value={"stations": [], "checked_ids": set(), "promoted_ids": set(), "dead_ids": set()}),
            ):
                status, payload = server.live_response({
                    "include_leads": ["false"],
                    "max_price": ["0.25"],
                    "force": ["true"],
                })

            self.assertEqual(status, 200)
            station = payload["stations"][0]
            self.assertTrue(payload["cache"]["stale"])
            self.assertGreater(station["freshness_minutes"], 180)
            self.assertEqual(station["decision"]["level"], "check")
            self.assertEqual(station["decision"]["label"], "Check first")
            self.assertIn("last station timestamp is old", station["decision"]["reason"])
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)

    def test_live_response_reuses_broader_stale_cache_and_reapplies_filters(self) -> None:
        original_cache = deepcopy(server.CACHE)
        try:
            server.CACHE.clear()
            server.CACHE.update({
                "ts": time.time() - 300,
                "payload": {
                    "stations": [{
                        "id": "stale-closed",
                        "name": "Closed stale station",
                        "lat": 39.47,
                        "lon": -0.37,
                        "available": 0,
                        "status": "UNKNOWN",
                        "is_free": True,
                        "is_cheap": True,
                        "tesla_ok": True,
                        "parking": {"kind": "street_or_municipal"},
                        "source_url": "https://example.com",
                    }],
                    "raw_count": 1,
                    "fetched_at": server.now_iso(),
                },
                "raw_count": 1,
                "error": None,
                "key": "0.25:False:False",
            })
            with (
                patch("server.fetch_mapareve", side_effect=RuntimeError("upstream down")),
                patch("server.fetch_gaia_live", return_value={"stations": [], "raw_count": 0, "cache": {}, "errors": []}),
                patch("server.fetch_electromaps_live", return_value={"stations": [], "checked_ids": set(), "promoted_ids": set(), "dead_ids": set()}),
            ):
                status, payload = server.live_response({
                    "include_leads": ["false"],
                    "max_price": ["0.25"],
                    "available": ["true"],
                    "force": ["true"],
                })

            self.assertEqual(status, 200)
            self.assertEqual(payload["stations"], [])
            self.assertFalse(payload["cache"]["stale"])
            self.assertTrue(payload["cache"]["live_failed"])
            self.assertTrue(payload["cache"]["filter_fallback"])
            self.assertEqual(payload["cache"]["source_key"], "0.25:False:False")
            self.assertEqual(payload["cache"]["requested_key"], "0.25:True:False")
            self.assertIn("no cached live rows remain", payload["warning"])
            self.assertIn("Refresh or widen filters before routing", payload["warning"])
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)


if __name__ == "__main__":
    unittest.main()
