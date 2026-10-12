#!/usr/bin/env python3
from __future__ import annotations

import sys
import threading
import time
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Optional
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


def live_row(
    station_id: str,
    *,
    lat: float,
    lon: float,
    available: int = 1,
    last_seen: Optional[str] = None,
) -> dict:
    return {
        "id": station_id,
        "name": station_id.replace("-", " ").title(),
        "lat": lat,
        "lon": lon,
        "available": available,
        "status": "AVAILABLE" if available else "UNKNOWN",
        "is_free": True,
        "is_cheap": True,
        "price_min": 0,
        "tesla_ok": True,
        "parking": {"kind": "street_or_municipal", "paid": False},
        "source": "Test live source",
        "source_url": f"https://example.com/{station_id}",
        "reliability": "official-live",
        "last_seen": last_seen or server.now_iso(),
    }


def empty_live_payload() -> dict:
    return {"stations": [], "raw_count": 0, "cache": {}, "errors": []}


class NearbyBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_cache = deepcopy(server.CACHE)
        server.CACHE.clear()

    def tearDown(self) -> None:
        server.CACHE.clear()
        server.CACHE.update(self.original_cache)

    def test_nearby_sort_keeps_safety_tier_then_uses_distance(self) -> None:
        near_go = {
            **live_row("near-go", lat=39.471, lon=-0.37),
            "distance_km": 0.1,
            "decision": {"level": "go"},
            "verification": {"level": "live", "live_verified": True},
            "recommendation_score": 10,
        }
        far_go = {
            **live_row("far-go", lat=39.50, lon=-0.37),
            "distance_km": 4.0,
            "decision": {"level": "go"},
            "verification": {"level": "live", "live_verified": True},
            "recommendation_score": 100,
        }
        closest_check = {
            **live_row("closest-check", lat=39.4701, lon=-0.37, available=0),
            "distance_km": 0.01,
            "decision": {"level": "check"},
            "verification": {"level": "live", "live_verified": True},
            "recommendation_score": 200,
        }

        ordered = sorted([closest_check, far_go, near_go], key=server.nearby_sort_key)

        self.assertEqual([row["id"] for row in ordered], ["near-go", "far-go", "closest-check"])

    def test_nearby_sort_never_promotes_a_lead_into_live_results(self) -> None:
        live_check = {
            **live_row("live-check", lat=39.48, lon=-0.37, available=0),
            "distance_km": 5,
            "decision": {"level": "check"},
            "verification": {"level": "live", "live_verified": True},
            "recommendation_score": 0,
        }
        lead = {
            **live_row("lead", lat=39.47, lon=-0.37),
            "lead": True,
            "distance_km": 0.01,
            "decision": {"level": "verify"},
            "verification": {"level": "official", "live_verified": False},
            "recommendation_score": 999,
        }

        self.assertEqual(sorted([lead, live_check], key=server.nearby_sort_key)[0]["id"], "live-check")

    def test_located_response_sorts_nearest_inside_safety_tiers(self) -> None:
        upstream = {
            "stations": [
                live_row("far-go", lat=39.50, lon=-0.37),
                live_row("closest-check", lat=39.4702, lon=-0.37, available=0),
                live_row("near-go", lat=39.471, lon=-0.37),
            ],
            "raw_count": 3,
            "fetched_at": server.now_iso(),
        }
        lead = {
            **live_row("near-lead", lat=39.4701, lon=-0.369),
            "lead": True,
            "reliability": "official-free-policy",
            "source": "Municipal open data",
        }
        with (
            patch("server.fetch_mapareve", return_value=upstream),
            patch("server.fetch_gaia_live", return_value=empty_live_payload()),
            patch("server.load_all_leads", return_value=[lead]),
        ):
            status, payload = server.live_response({
                "lat": ["39.47"],
                "lon": ["-0.37"],
                "radius_km": ["10"],
                "include_leads": ["true"],
                "force": ["true"],
            })

        self.assertEqual(status, 200)
        self.assertEqual(
            [row["id"] for row in payload["stations"]],
            ["near-go", "far-go", "closest-check"],
        )
        self.assertEqual([row["id"] for row in payload["leads"]], ["near-lead"])
        self.assertNotIn("near-lead", {row["id"] for row in payload["stations"]})

    def test_neighboring_bboxes_share_only_a_full_envelope_fetch(self) -> None:
        first_bbox = {
            "latitude_sw": 39.38,
            "longitude_sw": -0.44,
            "latitude_ne": 39.48,
            "longitude_ne": -0.34,
        }
        second_bbox = {
            "latitude_sw": 39.41,
            "longitude_sw": -0.40,
            "latitude_ne": 39.49,
            "longitude_ne": -0.30,
        }
        edge_station = live_row("second-view-edge", lat=39.485, lon=-0.31)
        fetched = {"stations": [edge_station], "raw_count": 1, "fetched_at": server.now_iso()}

        self.assertEqual(
            server.viewport_tile_key(first_bbox, "explicit"),
            server.viewport_tile_key(second_bbox, "explicit"),
        )
        with (
            patch("server.fetch_mapareve", return_value=fetched) as fetch_mapareve,
            patch("server.fetch_gaia_live", return_value=empty_live_payload()),
        ):
            _, first = server.live_response({"bbox": ["39.38,-0.44,39.48,-0.34"]})
            _, second = server.live_response({"bbox": ["39.41,-0.40,39.49,-0.30"]})

        self.assertEqual(first["stations"], [])
        self.assertEqual([row["id"] for row in second["stations"]], ["second-view-edge"])
        self.assertTrue(second["cache"]["hit"])
        self.assertEqual(fetch_mapareve.call_count, 1)
        self.assertEqual(
            fetch_mapareve.call_args.kwargs["bbox"],
            server.snapped_bbox_envelope(first_bbox),
        )

    def test_recent_stale_cache_is_demoted_even_if_last_seen_is_fresh(self) -> None:
        server.CACHE.update({
            "ts": time.time() - 300,
            "payload": {
                "stations": [live_row("recent-stale-go", lat=39.47, lon=-0.37)],
                "raw_count": 1,
                "fetched_at": server.now_iso(),
            },
            "raw_count": 1,
            "error": None,
            "key": "0.25:False:False",
        })
        with (
            patch("server.fetch_mapareve", side_effect=RuntimeError("upstream down")),
            patch("server.fetch_gaia_live", return_value=empty_live_payload()),
        ):
            status, payload = server.live_response({"force": ["true"]})

        self.assertEqual(status, 200)
        self.assertTrue(payload["cache"]["stale"])
        self.assertEqual(len(payload["stations"]), 1)
        station = payload["stations"][0]
        self.assertTrue(station["cache_stale"])
        self.assertEqual(station["decision"]["level"], "check")
        self.assertEqual(station["decision"]["label"], "Refresh first")
        self.assertFalse(server.route_ready_now(station))

    def test_available_filter_excludes_recent_stale_cache_go(self) -> None:
        server.CACHE.update({
            "ts": time.time() - 300,
            "payload": {
                "stations": [live_row("recent-stale-go", lat=39.47, lon=-0.37)],
                "raw_count": 1,
                "fetched_at": server.now_iso(),
            },
            "raw_count": 1,
            "error": None,
            "key": "0.25:True:False",
        })
        with (
            patch("server.fetch_mapareve", side_effect=RuntimeError("upstream down")),
            patch("server.fetch_gaia_live", return_value=empty_live_payload()),
        ):
            status, payload = server.live_response({
                "available": ["true"],
                "force": ["true"],
            })

        self.assertEqual(status, 200)
        self.assertFalse(
            payload["cache"]["stale"],
            "the available filter removed the only stale row from the final response",
        )
        self.assertTrue(payload["cache"]["live_failed"])
        self.assertEqual(payload["stations"], [])
        self.assertEqual(payload["meta"]["engine"]["counts"]["route_ready_live"], 0)

    def test_independent_live_sources_are_fetched_in_parallel(self) -> None:
        rendezvous = threading.Barrier(2)

        def synchronized_gaia(*, force: bool) -> dict:
            self.assertTrue(force)
            rendezvous.wait(timeout=1)
            return empty_live_payload()

        def synchronized_mapareve(*, max_price: float, available_only: bool, fast_only: bool, bbox: dict) -> dict:
            self.assertEqual(max_price, 0.25)
            rendezvous.wait(timeout=1)
            return {"stations": [], "raw_count": 0, "fetched_at": server.now_iso()}

        with (
            patch("server.fetch_mapareve", side_effect=synchronized_mapareve),
            patch("server.fetch_gaia_live", side_effect=synchronized_gaia),
        ):
            results = server.fetch_primary_sources_parallel(
                fetch_mapareve_now=True,
                max_price=0.25,
                available_only=False,
                fast_only=False,
                bbox=server.VALENCIA_BBOX,
                force=True,
            )

        self.assertEqual(set(results), {"mapareve", "gaia"})
        self.assertTrue(all(result["error"] is None for result in results.values()))
        self.assertEqual(results["gaia"]["payload"], empty_live_payload())
        self.assertEqual(results["mapareve"]["payload"]["stations"], [])

    def test_compact_station_keeps_ui_facts_without_ingestion_bulk(self) -> None:
        original = {
            **live_row("compact", lat=39.47, lon=-0.37),
            "raw": {"large": "audit payload"},
            "source_integration": {"adapter": "directory"},
            "access_claim": {"value": "public"},
            "location_claim": {"value": "verified"},
            "hardware_claim": {"value": "Type 2"},
            "source_fetched_at": "2026-07-09T10:00:00Z",
            "connectors": [{
                "connector_id": "c-1",
                "label": "Type 2",
                "type": "IEC_62196_T2",
                "status": "AVAILABLE",
                "power_kw": 22,
                "count": 2,
                "raw": {"duplicate": True},
                "source_url": "https://example.com/heavy",
            }],
            "pricing": [{"price": 0, "currency": "EUR"}],
            "price_window_status": {"label": "Now", "large": {"duplicate": True}},
        }

        compact = server.compact_station_for_ui(original)

        self.assertIn("raw", original)
        self.assertNotIn("raw", compact)
        self.assertNotIn("source_integration", compact)
        self.assertNotIn("access_claim", compact)
        self.assertNotIn("location_claim", compact)
        self.assertNotIn("hardware_claim", compact)
        self.assertNotIn("source_fetched_at", compact)
        self.assertEqual(compact["connectors"], [{
            "connector_id": "c-1",
            "label": "Type 2",
            "type": "IEC_62196_T2",
            "status": "AVAILABLE",
            "power_kw": 22,
            "count": 2,
        }])
        self.assertNotIn("pricing", compact)
        self.assertNotIn("price_window_status", compact)

    def test_compact_station_keeps_time_window_label(self) -> None:
        compact = server.compact_station_for_ui({
            **live_row("window", lat=39.47, lon=-0.37),
            "pricing": [{
                "price": 0.20,
                "valid_from": "22:00",
                "valid_until": "06:00",
                "days": ["mon", "tue"],
                "large": {"duplicate": True},
            }],
            "price_window_status": {
                "label": "Cheap after 22:00",
                "is_currently_within_standard": False,
                "large": {"duplicate": True},
            },
        })

        self.assertEqual(compact["pricing"], [{
            "valid_from": "22:00",
            "valid_until": "06:00",
            "days": ["mon", "tue"],
        }])
        self.assertEqual(compact["price_window_status"], {
            "label": "Cheap after 22:00",
            "is_currently_within_standard": False,
        })

    def test_overview_serialization_cache_separates_compact_contract(self) -> None:
        original_cache = deepcopy(server.OVERVIEW_BYTES_CACHE)
        server.OVERVIEW_BYTES_CACHE.clear()
        try:
            def response(params: dict, max_price: float, fast_only: bool) -> tuple[int, dict]:
                compact = (params.get("compact") or ["false"])[0].lower() == "true"
                return 200, {"meta": {"compact": compact}}

            with patch("server.overview_response", side_effect=response) as mocked:
                _, compact_body, _ = server.overview_response_bytes(
                    {"compact": ["true"]}, 0.25, False,
                )
                _, full_body, _ = server.overview_response_bytes(
                    {"compact": ["false"]}, 0.25, False,
                )

            self.assertEqual(mocked.call_count, 2)
            self.assertNotEqual(compact_body, full_body)
        finally:
            server.OVERVIEW_BYTES_CACHE.clear()
            server.OVERVIEW_BYTES_CACHE.update(original_cache)


if __name__ == "__main__":
    unittest.main()
