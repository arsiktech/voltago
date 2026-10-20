#!/usr/bin/env python3
"""Regression coverage for plan 003: live filter and ranking correctness.

Three defects in the live decision path (server.py):

1. `max_price` was never applied to MapaREVE rows — `fetch_mapareve`'s keep-
   predicate gated on `is_free`/`is_cheap` only (a hardcoded 0.25 concept),
   so a priced row the request explicitly allowed (e.g. max_price=5.0) was
   still dropped.
2. The cross-source corroboration boost was computed (`apply_corroboration`)
   but never re-applied to `recommendation_score` before leads were sorted,
   in both `overview_response` and the `live_response` leads loop when no
   GPS coordinates were supplied.
3. The stale "filter_fallback" path (a broader cached MapaREVE payload reused
   after a live-refresh failure) never reapplied `fast_only`/`max_price` to
   the reused rows, violating the documented fail-closed cache contract.

Modeled on tests/test_live_coverage.py (same import/patch style, same
CACHE priming pattern for the stale-fallback cases).
"""
from __future__ import annotations

import json
import sys
import time
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from scripts import source_transport as transport  # noqa: E402


EMPTY_SOURCE = {"stations": [], "raw_count": 0, "cache": {}, "errors": []}
EMPTY_ELECTROMAPS = {"stations": [], "checked_ids": set(), "promoted_ids": set(), "dead_ids": set()}



def setUpModule():
    # Existing suites isolate their older feeds; keep the new runtime feeds offline too.
    live_patch = patch("server.fetch_public_live", return_value={"stations": [], "feeds": {}})
    live_patch.start()
    unittest.addModuleCleanup(live_patch.stop)


class _FakeUrlResponse:
    """Minimal stand-in for the `with urllib.request.urlopen(...) as resp:` contract."""

    def __init__(self, payload: Dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeUrlResponse":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


def _mapareve_raw_location(loc_id: str, lat: float, lon: float, price: float) -> Dict[str, Any]:
    """A realistic-enough raw MapaREVE OCPI location so transform_location runs for real."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "id": loc_id,
        "name": f"MapaREVE test station {loc_id}",
        "address": "Carrer Test, 1",
        "postal_code": "46001",
        "country": "ES",
        "status": "AVAILABLE",
        "coordinates": {"latitude": lat, "longitude": lon},
        "owner": {"name": "Test Owner"},
        "evses": [
            {
                "status": "AVAILABLE",
                "status_updated_at": now,
                "connectors": [
                    {
                        "standard": "IEC_62196_T2",
                        "format": "SOCKET",
                        "max_electric_power": 22000,
                        "tariffs": [
                            {
                                "tariff": {
                                    "currency": "EUR",
                                    "elements": [
                                        {
                                            "restrictions": {},
                                            "price_components": [{"type": "ENERGY", "price": price}],
                                        }
                                    ],
                                }
                            }
                        ],
                    }
                ],
            }
        ],
    }


def _corroboration_lead_fixtures() -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Two leads with identical score-relevant fields except source corroboration.

    Fixture shape follows tests/test_corroboration.py: `sources` from 3 distinct
    families -> strongly_corroborated; a single source -> single tier. All other
    recommendation_score-relevant fields (is_free, lead, decision, access,
    parking, available, fast, distance) are intentionally identical/absent so the
    only score delta comes from the corroboration boost.
    """
    corroborated = {
        "id": "lead-corroborated",
        "name": "Corroborated Free Lead",
        "lat": 39.47,
        "lon": -0.37,
        "is_free": True,
        "is_cheap": True,
        "price_min": 0,
        "lead": True,
        "sources": [
            {"name": "MapaREVE", "url": "https://www.mapareve.es/loc/1"},
            {"name": "OpenChargeMap public snapshot", "url": "https://openchargemap.io/site/poi/details/1"},
            {"name": "OpenStreetMap public tags", "url": "https://www.openstreetmap.org/node/1"},
        ],
    }
    single = {
        "id": "lead-single-source",
        "name": "Single Source Free Lead",
        "lat": 39.48,
        "lon": -0.38,
        "is_free": True,
        "is_cheap": True,
        "price_min": 0,
        "lead": True,
        "sources": [
            {"name": "MapaREVE", "url": "https://www.mapareve.es/loc/2"},
        ],
    }
    return single, corroborated


# Parser tests inject fixture pages above the transport policy guard (they never touch the
# network); the policy tests below exercise the real guard.
class MapareveMaxPriceTests(unittest.TestCase):
    def test_max_price_honored_for_mapareve_rows(self) -> None:
        """Defect 1: a priced (non-free/non-cheap) MapaREVE row must survive up to
        the REQUEST's max_price, not a hardcoded 0.25 cap, and must still be
        excluded once the request narrows below its price."""
        original_cache = deepcopy(server.CACHE)
        raw_payload = {
            "data": [
                _mapareve_raw_location("mv-pricey", 39.47, -0.37, 0.28),
                _mapareve_raw_location("mv-cheap", 39.471, -0.371, 0.20),
            ],
            "pagination": {},
        }
        try:
            server.CACHE.clear()
            with (
                patch("server.public_get_urlopen", return_value=_FakeUrlResponse(raw_payload)),
                patch("server.fetch_gaia_live", return_value=dict(EMPTY_SOURCE)),
                patch("server.fetch_electromaps_live", return_value=dict(EMPTY_ELECTROMAPS)),
            ):
                _, payload_wide = server.live_response({
                    "max_price": ["5.0"],
                    "include_leads": ["false"],
                    "bbox": ["39.30,-0.55,39.62,-0.20"],
                    "force": ["true"],
                })
                ids_wide = {s["id"] for s in payload_wide["stations"]}
                self.assertIn("mv-pricey", ids_wide, "a 0.28 row must survive when the request allows up to 5.0")
                self.assertIn("mv-cheap", ids_wide)

                _, payload_narrow = server.live_response({
                    "max_price": ["0.25"],
                    "include_leads": ["false"],
                    "bbox": ["39.30,-0.55,39.62,-0.20"],
                    "force": ["true"],
                })
                ids_narrow = {s["id"] for s in payload_narrow["stations"]}
                self.assertNotIn("mv-pricey", ids_narrow, "0.28 must be excluded once the request caps at 0.25")
                self.assertIn("mv-cheap", ids_narrow)
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)


class MaparevePaginationCoverageTests(unittest.TestCase):
    def test_page_cap_marks_live_results_partial_and_degraded(self) -> None:
        """A broad viewport may need more than the bounded upstream page budget.

        Stop before requesting page two, but make the omitted continuation visible
        in both the source payload and the client coverage contract. A truncated
        successful HTTP response must never look like complete live coverage.
        """
        original_cache = deepcopy(server.CACHE)
        outbound_pages = []
        page_one = {
            "data": [_mapareve_raw_location("mv-first-page", 39.47, -0.37, 0.20)],
            "pagination": {"next": 2, "total_count": 40},
        }

        def fake_urlopen(request: Any, timeout: float) -> _FakeUrlResponse:
            outbound_pages.append(request.full_url)
            return _FakeUrlResponse(page_one)

        try:
            server.CACHE.clear()
            with (
                patch.object(server, "MAPAREVE_MAX_PAGES", 1),
                patch("server.public_get_urlopen", side_effect=fake_urlopen),
                patch("server.fetch_gaia_live", return_value=dict(EMPTY_SOURCE)),
                patch("server.fetch_electromaps_live", return_value=dict(EMPTY_ELECTROMAPS)),
            ):
                status, payload = server.live_response({
                    "include_leads": ["false"],
                    "max_price": ["5.0"],
                    "bbox": ["39.30,-0.55,39.62,-0.20"],
                    "force": ["true"],
                })

            self.assertEqual(status, 200)
            self.assertEqual(len(outbound_pages), 1, "the configured cap must prevent a page-two fetch")
            self.assertTrue(payload.get("partial"))
            self.assertEqual(payload.get("partial_reason"), "pagination_cap")
            self.assertEqual(payload["pagination"].get("fetched_pages"), 1)
            self.assertEqual(payload["pagination"].get("next_page"), 2)
            coverage = payload["meta"]["coverage"]
            self.assertEqual(coverage.get("mode"), "degraded")
            self.assertTrue(coverage.get("partial"))
            self.assertIn("page limit", str(payload.get("warning")))
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)

    def test_page_error_after_a_continuation_marks_live_results_partial_and_degraded(self) -> None:
        """A failed second page must not make the successful first page look complete."""
        original_cache = deepcopy(server.CACHE)
        outbound_pages = []
        page_one = {
            "data": [_mapareve_raw_location("mv-before-error", 39.47, -0.37, 0.20)],
            "pagination": {"next": 2, "total_count": 40},
        }

        def fake_urlopen(request: Any, timeout: float) -> _FakeUrlResponse:
            outbound_pages.append(request.full_url)
            if len(outbound_pages) == 1:
                return _FakeUrlResponse(page_one)
            raise TimeoutError("second page timed out")

        try:
            server.CACHE.clear()
            with (
                patch("server.public_get_urlopen", side_effect=fake_urlopen),
                patch("server.fetch_gaia_live", return_value=dict(EMPTY_SOURCE)),
                patch("server.fetch_electromaps_live", return_value=dict(EMPTY_ELECTROMAPS)),
            ):
                status, payload = server.live_response({
                    "include_leads": ["false"],
                    "max_price": ["5.0"],
                    "bbox": ["39.30,-0.55,39.62,-0.20"],
                    "force": ["true"],
                })

            self.assertEqual(status, 200)
            self.assertEqual(len(outbound_pages), 2)
            self.assertTrue(payload.get("partial"))
            self.assertEqual(payload.get("partial_reason"), "upstream_error")
            self.assertEqual(payload["pagination"].get("fetched_pages"), 1)
            self.assertEqual(payload["pagination"].get("next_page"), 2)
            coverage = payload["meta"]["coverage"]
            self.assertEqual(coverage.get("mode"), "degraded")
            self.assertTrue(coverage.get("partial"))
            self.assertEqual(coverage.get("partial_reason"), "upstream_error")
            self.assertIn("stopped before every page", str(payload.get("warning")))
            self.assertNotIn("TimeoutError", str(payload.get("warning")))
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)

    def test_fetch_budget_after_a_continuation_marks_the_source_payload_partial(self) -> None:
        """The bounded time budget is also a truncation, not a complete empty result."""
        outbound_pages = []
        page_one = {
            "data": [_mapareve_raw_location("mv-before-budget", 39.47, -0.37, 0.20)],
            "pagination": {"next": 2, "total_count": 40},
        }

        class Clock:
            def __init__(self) -> None:
                self.calls = 0

            def __call__(self) -> float:
                self.calls += 1
                return 0.0 if self.calls <= 2 else 2.0

        def fake_urlopen(request: Any, timeout: float) -> _FakeUrlResponse:
            outbound_pages.append(request.full_url)
            return _FakeUrlResponse(page_one)

        with (
            patch.object(server, "MAPAREVE_FETCH_BUDGET_SECONDS", 1.0),
            patch("server.time.time", side_effect=Clock()),
            patch("server.public_get_urlopen", side_effect=fake_urlopen),
        ):
            payload = server.fetch_mapareve(max_price=5.0)

        self.assertEqual(len(outbound_pages), 1)
        self.assertTrue(payload.get("partial"))
        self.assertEqual(payload.get("partial_reason"), "fetch_budget")
        self.assertEqual(payload["pagination"].get("fetched_pages"), 1)
        self.assertEqual(payload["pagination"].get("next_page"), 2)
        self.assertIn("time budget elapsed", str(payload.get("warning")))

        # Source-level budget detection is not enough: the response consumed by the
        # client must carry the same partial state into visible degraded coverage.
        original_cache = deepcopy(server.CACHE)
        try:
            server.CACHE.clear()
            with (
                patch("server.fetch_mapareve", return_value=payload),
                patch("server.fetch_gaia_live", return_value=dict(EMPTY_SOURCE)),
                patch("server.fetch_electromaps_live", return_value=dict(EMPTY_ELECTROMAPS)),
            ):
                status, response = server.live_response({
                    "include_leads": ["false"],
                    "max_price": ["5.0"],
                    "bbox": ["39.30,-0.55,39.62,-0.20"],
                    "force": ["true"],
                })

            self.assertEqual(status, 200)
            self.assertTrue(response.get("partial"))
            self.assertEqual(response.get("partial_reason"), "fetch_budget")
            coverage = response["meta"]["coverage"]
            self.assertEqual(coverage.get("mode"), "degraded")
            self.assertTrue(coverage.get("partial"))
            self.assertEqual(coverage.get("partial_reason"), "fetch_budget")
            self.assertIn("time budget elapsed", str(response.get("warning")))
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)

    def test_falsy_continuations_never_claim_the_source_payload_is_complete(self) -> None:
        """Only a missing/null continuation may end pagination cleanly.

        A falsey cursor is malformed, even if ``total_count`` shows the first page
        did not contain all locations.  Treat it as partial rather than making an
        incomplete live result look route-ready.
        """
        for continuation in (0, "", [], {}):
            with self.subTest(continuation=repr(continuation)):
                page_one = {
                    "data": [_mapareve_raw_location("mv-falsey-continuation", 39.47, -0.37, 0.20)],
                    "pagination": {"next": continuation, "total_count": 40},
                }
                with patch(
                    "server.public_get_urlopen",
                    return_value=_FakeUrlResponse(page_one),
                ):
                    payload = server.fetch_mapareve(max_price=5.0)

                self.assertTrue(payload.get("partial"))
                self.assertEqual(payload.get("partial_reason"), "invalid_continuation")
                self.assertEqual(payload["pagination"].get("fetched_pages"), 1)
                self.assertNotIn("next_page", payload["pagination"])
                self.assertIn("continuation could not be read", str(payload.get("warning")))

    def test_invalid_continuation_degrades_the_live_response(self) -> None:
        """The malformed-source flag must propagate to the visible coverage contract."""
        original_cache = deepcopy(server.CACHE)
        page_one = {
            "data": [_mapareve_raw_location("mv-invalid-continuation", 39.47, -0.37, 0.20)],
            "pagination": {"next": 0, "total_count": 40},
        }

        try:
            server.CACHE.clear()
            with (
                patch("server.public_get_urlopen", return_value=_FakeUrlResponse(page_one)),
                patch("server.fetch_gaia_live", return_value=dict(EMPTY_SOURCE)),
                patch("server.fetch_electromaps_live", return_value=dict(EMPTY_ELECTROMAPS)),
            ):
                status, payload = server.live_response({
                    "include_leads": ["false"],
                    "max_price": ["5.0"],
                    "bbox": ["39.30,-0.55,39.62,-0.20"],
                    "force": ["true"],
                })

            self.assertEqual(status, 200)
            self.assertTrue(payload.get("partial"))
            self.assertEqual(payload.get("partial_reason"), "invalid_continuation")
            coverage = payload["meta"]["coverage"]
            self.assertEqual(coverage.get("mode"), "degraded")
            self.assertTrue(coverage.get("partial"))
            self.assertEqual(coverage.get("partial_reason"), "invalid_continuation")
            self.assertIn("continuation could not be read", str(payload.get("warning")))
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)


class MapareveParallelPagesTests(unittest.TestCase):
    """Once page 1 reports total_count, the remaining pages are fetched concurrently."""

    PAGES = 6

    def _page(self, page: int) -> Dict[str, Any]:
        return {
            "data": [_mapareve_raw_location(f"mv-p{page}", 39.40 + page / 100, -0.37, 0.20)],
            "pagination": {"next": page + 1 if page < self.PAGES else None, "total_count": self.PAGES * 20},
        }

    @staticmethod
    def _page_number(request: Any) -> int:
        return int(request.full_url.split("page=")[1].split("&")[0])

    def test_all_pages_arrive_in_order_and_faster_than_sequential(self) -> None:
        delay = 0.15

        def fake_urlopen(request: Any, timeout: float) -> _FakeUrlResponse:
            time.sleep(delay)
            return _FakeUrlResponse(self._page(self._page_number(request)))

        started = time.perf_counter()
        with patch("server.public_get_urlopen", side_effect=fake_urlopen):
            payload = server.fetch_mapareve(max_price=5.0)
        elapsed = time.perf_counter() - started

        self.assertNotIn("partial", payload)
        self.assertEqual(payload["pagination"]["fetched_pages"], self.PAGES)
        self.assertNotIn("next_page", payload["pagination"])
        self.assertEqual(payload["raw_count"], self.PAGES)
        # Sequential would be PAGES * delay (0.9s); page 1 then one or two parallel rounds.
        self.assertLess(elapsed, (self.PAGES - 1) * delay)

    def test_failed_middle_page_keeps_only_the_contiguous_prefix(self) -> None:
        def fake_urlopen(request: Any, timeout: float) -> _FakeUrlResponse:
            page = self._page_number(request)
            if page == 4:
                raise TimeoutError("page four timed out")
            return _FakeUrlResponse(self._page(page))

        with patch("server.public_get_urlopen", side_effect=fake_urlopen):
            payload = server.fetch_mapareve(max_price=5.0)

        self.assertTrue(payload.get("partial"))
        self.assertEqual(payload.get("partial_reason"), "upstream_error")
        self.assertEqual(payload["pagination"]["fetched_pages"], 3)
        self.assertEqual(payload["pagination"]["next_page"], 4)
        self.assertEqual(payload["raw_count"], 3)

    def test_page_cap_still_bounds_the_parallel_fetch(self) -> None:
        requested: list = []

        def fake_urlopen(request: Any, timeout: float) -> _FakeUrlResponse:
            page = self._page_number(request)
            requested.append(page)
            return _FakeUrlResponse(self._page(page))

        with (
            patch.object(server, "MAPAREVE_MAX_PAGES", 3),
            patch("server.public_get_urlopen", side_effect=fake_urlopen),
        ):
            payload = server.fetch_mapareve(max_price=5.0)

        self.assertEqual(sorted(requested), [1, 2, 3])
        self.assertEqual(payload.get("partial_reason"), "pagination_cap")
        self.assertEqual(payload["pagination"]["fetched_pages"], 3)
        self.assertEqual(payload["pagination"]["next_page"], 4)


class MapareveRequestPolicyTests(unittest.TestCase):
    def test_runtime_query_is_the_reviewed_mapareve_post_and_passes_the_policy(self) -> None:
        """fetch_mapareve must build exactly the request source_transport approves."""
        seen: list = []

        def fake_open(request, *, timeout, cookiejar=None):
            seen.append(request)
            return _FakeUrlResponse({"data": [], "pagination": {}})

        with patch("server.policy_urlopen", side_effect=fake_open):
            server.fetch_mapareve(max_price=5.0)
        self.assertEqual(len(seen), 1)
        request = seen[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertTrue(request.full_url.startswith("https://www.mapareve.es/api/public/v1/locations?page=1"))
        # The real policy classifier accepts the real runtime request (no network involved).
        self.assertEqual(transport.classify_post_exception(request), "mapareve-locations-query")

    def test_unapproved_post_is_rejected_before_any_network_call(self) -> None:
        with patch("server.urllib.request.urlopen") as urlopen, patch("scripts.source_transport.urllib.request.build_opener") as opener:
            for url in (
                "https://example.invalid/public",
                "https://www.mapareve.es/api/public/v1/other",
                "http://www.mapareve.es/api/public/v1/locations",
                "https://evil.example/api/public/v1/locations",
            ):
                with self.subTest(url=url):
                    request = server.urllib.request.Request(url, data=b"{}", method="POST")
                    with self.assertRaises(server.SourcePolicyBlocked):
                        server.public_get_urlopen(request, timeout=3)
        urlopen.assert_not_called()
        opener.assert_not_called()

    def test_transport_guard_allows_get_and_rejects_other_methods(self) -> None:
        with patch("scripts.source_transport.urllib.request.urlopen") as urlopen:
            request = server.urllib.request.Request("https://example.invalid/public", method="GET")
            self.assertIs(server.public_get_urlopen(request, timeout=3), urlopen.return_value)
            urlopen.assert_called_once_with(request, timeout=3)
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method), patch("scripts.source_transport.urllib.request.urlopen") as urlopen:
                request = server.urllib.request.Request("https://example.invalid/public", method=method)
                with self.assertRaises(server.SourcePolicyBlocked):
                    server.public_get_urlopen(request, timeout=3)
                urlopen.assert_not_called()

    def test_policy_block_preserves_stale_rows_and_fresh_secondary_sources(self) -> None:
        original_cache = deepcopy(server.CACHE)
        cached = server.transform_location(_mapareve_raw_location("cached-reve", 39.47, -0.37, 0.20))
        fresh = server.transform_location(_mapareve_raw_location("fresh-gaia", 39.48, -0.38, 0.0))
        fresh["source"] = "Gaia public start page"
        fresh["source_url"] = "https://www.gaiacharge.com/cpo/jump/test/start-session/1"
        fresh["sources"] = [{"name": fresh["source"], "url": fresh["source_url"]}]
        fresh.pop("primary_source", None)
        fresh["reliability"] = "official-live-public-page"
        try:
            for with_cache in (False, True):
                for with_secondary in (False, True):
                    with self.subTest(cache=with_cache, secondary=with_secondary):
                        server.CACHE.clear()
                        if with_cache:
                            server.CACHE.update({
                                "ts": time.time() - server.CACHE_TTL_SECONDS - 1,
                                "key": "previous-filter",
                                "payload": {"stations": [deepcopy(cached)], "raw_count": 1},
                            })
                        secondary = {**EMPTY_SOURCE, "stations": [deepcopy(fresh)] if with_secondary else []}
                        with (
                            patch("server.public_get_urlopen", side_effect=server.SourcePolicyBlocked("blocked")) as blocked,
                            patch("server.fetch_gaia_live", return_value=secondary),
                            patch("server.fetch_electromaps_live", return_value=dict(EMPTY_ELECTROMAPS)),
                        ):
                            status, response = server.live_response({
                                "include_leads": ["false"], "max_price": ["5.0"],
                                "bbox": ["39.30,-0.55,39.62,-0.20"], "force": ["true"],
                            })
                        blocked.assert_called()
                        self.assertEqual(status, 200)
                        self.assertIn("collection disabled by source-access policy", response["warning"])
                        rows = {row["id"]: row for row in response["stations"]}
                        self.assertEqual("cached-reve" in rows, with_cache)
                        self.assertEqual("fresh-gaia" in rows, with_secondary)
                        self.assertEqual(response["cache"]["fresh_live_authority"], with_secondary)
                        if with_cache:
                            self.assertTrue(rows["cached-reve"]["cache_stale"])
                            self.assertFalse(server.route_ready_now(rows["cached-reve"]))
                        if with_secondary:
                            self.assertTrue(server.route_ready_now(rows["fresh-gaia"]))
                        else:
                            self.assertEqual(response["meta"]["coverage"]["live_route_ready"], 0)
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)


class StaleFilterFallbackReapplicationTests(unittest.TestCase):
    def test_stale_filter_fallback_reapplies_fast_and_price_to_mapareve_rows(self) -> None:
        """Defect 3: a broader stale cache reused after a live-refresh failure must
        reapply fast_only/max_price to the reused MapaREVE rows (engine honesty
        rule at server.py:2656), not just bbox."""
        original_cache = deepcopy(server.CACHE)
        try:
            server.CACHE.clear()
            server.CACHE.update({
                "ts": time.time() - 300,
                "payload": {
                    "stations": [
                        {
                            "id": "stale-slow",
                            "name": "Stale slow charger",
                            "lat": 39.47,
                            "lon": -0.37,
                            "available": 1,
                            "status": "AVAILABLE",
                            "is_free": False,
                            "is_cheap": True,
                            "price_min": 0.10,
                            "fast": False,
                            "tesla_ok": True,
                            "parking": {"kind": "street_or_municipal"},
                            "source_url": "https://example.com/slow",
                        },
                        {
                            "id": "stale-pricey",
                            "name": "Stale pricey charger",
                            "lat": 39.471,
                            "lon": -0.371,
                            "available": 1,
                            "status": "AVAILABLE",
                            "is_free": False,
                            "is_cheap": False,
                            "price_min": 0.28,
                            "fast": True,
                            "tesla_ok": True,
                            "parking": {"kind": "street_or_municipal"},
                            "source_url": "https://example.com/pricey",
                        },
                    ],
                    "raw_count": 2,
                    "fetched_at": server.now_iso(),
                },
                "raw_count": 2,
                "error": None,
                "key": "0.25:False:False",
            })
            with (
                patch("server.fetch_mapareve", side_effect=RuntimeError("upstream down")),
                patch("server.fetch_gaia_live", return_value=dict(EMPTY_SOURCE)),
                patch("server.fetch_electromaps_live", return_value=dict(EMPTY_ELECTROMAPS)),
            ):
                status, payload = server.live_response({
                    "include_leads": ["false"],
                    "max_price": ["0.25"],
                    "fast": ["true"],
                    "force": ["true"],
                })

            self.assertEqual(status, 200)
            self.assertFalse(
                payload["cache"]["stale"],
                "all stale rows were filtered out, so no stale evidence survives in the response",
            )
            self.assertTrue(payload["cache"]["live_failed"])
            self.assertTrue(payload["cache"]["filter_fallback"])
            ids = {s.get("id") for s in payload["stations"]}
            self.assertNotIn("stale-slow", ids, "non-fast reused row must be dropped once fast=true is requested")
            self.assertNotIn("stale-pricey", ids, "0.28 reused row must be dropped once max_price=0.25 is requested")
            self.assertEqual(payload["stations"], [])
            dropped = payload["meta"]["engine"]["counts"]["dropped"]
            self.assertEqual(dropped.get("route_filtered"), 2)
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)


class CorroborationRankingTests(unittest.TestCase):
    def test_corroboration_boost_affects_overview_ranking(self) -> None:
        """Defect 2, site A: overview_response computes recommendation_score before
        apply_corroboration attaches the boost, then sorts by the stale score. The
        3-family lead must outrank the single-source lead and carry a strictly
        higher recommendation_score."""
        single, corroborated = _corroboration_lead_fixtures()
        with patch("server.load_all_leads", return_value=[single, corroborated]):
            _, payload = server.overview_response({}, 5.0, False)

        leads = payload["leads"]
        by_id = {lead["id"]: lead for lead in leads}
        self.assertIn("lead-corroborated", by_id)
        self.assertIn("lead-single-source", by_id)
        self.assertEqual(leads[0]["id"], "lead-corroborated", "the corroborated lead must sort first")
        self.assertGreater(
            by_id["lead-corroborated"]["recommendation_score"],
            by_id["lead-single-source"]["recommendation_score"],
        )

    def test_corroboration_boost_affects_live_leads_ranking_without_gps(self) -> None:
        """Defect 2, site B: the live_response leads-loop re-score
        (enrich_station(lead, live=False)) sits inside the `if user_lat/user_lon`
        guard, so a request with no GPS never recomputes recommendation_score
        after apply_corroboration. Must be fixed unconditionally."""
        single, corroborated = _corroboration_lead_fixtures()
        original_cache = deepcopy(server.CACHE)
        try:
            server.CACHE.clear()
            with (
                patch("server.fetch_mapareve", return_value={"stations": [], "raw_count": 0, "fetched_at": server.now_iso(), "source": "mapareve-public-web-api"}),
                patch("server.fetch_gaia_live", return_value=dict(EMPTY_SOURCE)),
                patch("server.fetch_electromaps_live", return_value=dict(EMPTY_ELECTROMAPS)),
                patch("server.load_all_leads", return_value=[single, corroborated]),
            ):
                status, payload = server.live_response({
                    "include_leads": ["true"],
                    "max_price": ["5.0"],
                    "force": ["true"],
                })
            self.assertEqual(status, 200)
            leads = payload["leads"]
            by_id = {lead["id"]: lead for lead in leads}
            self.assertIn("lead-corroborated", by_id)
            self.assertIn("lead-single-source", by_id)
            self.assertEqual(leads[0]["id"], "lead-corroborated", "the corroborated lead must sort first with no GPS")
            self.assertGreater(
                by_id["lead-corroborated"]["recommendation_score"],
                by_id["lead-single-source"]["recommendation_score"],
            )
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)


if __name__ == "__main__":
    unittest.main()


class MapareveRecentAreasCacheTests(unittest.TestCase):
    def test_returning_to_a_recent_area_reuses_its_fetch(self) -> None:
        """A -> B -> A within the TTL crawls MapaREVE twice, not three times."""
        original_cache = deepcopy(server.CACHE)
        fetched_boxes: list = []

        def fake_fetch(**kwargs: Any) -> Dict[str, Any]:
            fetched_boxes.append(kwargs.get("bbox"))
            return {"stations": [], "raw_count": 0, "fetched_at": server.now_iso(), "source": "mapareve-public-web-api"}

        area_a = {"include_leads": ["false"], "max_price": ["5.0"], "bbox": ["39.30,-0.55,39.62,-0.20"]}
        area_b = {"include_leads": ["false"], "max_price": ["5.0"], "bbox": ["41.30,2.00,41.50,2.30"]}
        try:
            server.CACHE.clear()
            with (
                patch("server.fetch_mapareve", side_effect=fake_fetch),
                patch("server.fetch_gaia_live", return_value=dict(EMPTY_SOURCE)),
                patch("server.fetch_electromaps_live", return_value=dict(EMPTY_ELECTROMAPS)),
            ):
                server.live_response(dict(area_a))
                server.live_response(dict(area_b))
                status, payload = server.live_response(dict(area_a))

            self.assertEqual(status, 200)
            self.assertEqual(len(fetched_boxes), 2)
            self.assertTrue(payload["cache"]["hit"])
            self.assertFalse(payload["cache"].get("stale"))
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)
