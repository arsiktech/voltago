#!/usr/bin/env python3
"""Equality gates for plans/007 (request-path performance). These tests do NOT
time anything -- scripts/perf_probe.py owns the timing story; this file only
proves the perf rewrite (enrichment memo, spatial-bucket dedup, assembled-
payload reuse) preserves EXACT output, byte for byte, minus the specific
per-call-timestamp-derived fields the docstrings below name explicitly.

Golden values in MergeGoldenValueTests were captured by running the CURRENT
(pre-plans/007) remove_duplicate_leads/merge_gaia_stations/merge_nearby_source_stations
against the fixtures below and freezing the result as a literal -- they do NOT
recompute "expected" via the function under test, so a semantics-changing
rewrite will make them fail (that is the point; see plans/007 STOP conditions).
Do not "fix" a golden literal to match a changed implementation -- that is
exactly the reward-hacking failure plans/006/007 guard against. If a real bug
is found here, log qa/defects.csv and mark @unittest.expectedFailure, per
AGENTS.md §6.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import unittest
import urllib.request
from copy import deepcopy
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402

# Fields whose value is derived from wall-clock "now" at the moment enrichment
# ran (age_minutes()/pricing_window_is_current() both read datetime.now(...)).
# The plan's memo layer (step 2) enriches once per (db mtime, seeds mtime) and
# hands out copies, so these fields legitimately differ between "the instant
# the memo was populated" and "the instant a much later request reads it" --
# that is the disclosed, bounded tradeoff plans/007 makes (see qa/run-log.md).
# Two calls milliseconds apart (this test) would never actually observe a
# difference even without stripping, but we strip explicitly so the gate
# stays valid across the refactor and isn't accidentally minute-boundary
# flaky.
_TIME_DERIVED_FIELDS = ("freshness_minutes", "recommendation_score")


def _strip_time_derived(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for r in records:
        r = dict(r)
        for f in _TIME_DERIVED_FIELDS:
            r.pop(f, None)
        out.append(r)
    return out


def _sort_key(item: Dict[str, Any]) -> str:
    return str(item.get("id") or item.get("name") or "")


def station(id_, name="Station", address="", lat=39.4700, lon=-0.3700, sources=None, **extra):
    """Minimal station/lead dict -- same shape as tests/test_live_merge.py's helper."""
    row = {
        "id": id_,
        "name": name,
        "address": address,
        "lat": lat,
        "lon": lon,
        "sources": sources if sources is not None else [],
    }
    row.update(extra)
    return row



def setUpModule():
    # Existing suites isolate their older feeds; keep the new runtime feeds offline too.
    live_patch = patch("server.fetch_public_live", return_value={"stations": [], "feeds": {}})
    live_patch.start()
    unittest.addModuleCleanup(live_patch.stop)


class LoadAllLeadsSelfEqualityTests(unittest.TestCase):
    """load_all_leads(bbox) must return the same records across two consecutive
    calls (minus per-call timestamp-derived fields). This is the equality gate
    plans/007 step 2 must keep green after introducing the enrichment memo."""

    def test_spain_bbox_two_calls_are_equal_minus_timestamps(self) -> None:
        first = server.load_all_leads(server.SPAIN_BBOX)
        second = server.load_all_leads(server.SPAIN_BBOX)

        self.assertGreater(len(first), 0, "fixture DB must have at least one in-bbox lead")
        self.assertEqual(len(first), len(second))

        first_sorted = sorted(_strip_time_derived(first), key=_sort_key)
        second_sorted = sorted(_strip_time_derived(second), key=_sort_key)
        self.assertEqual(first_sorted, second_sorted)

    def test_zoomed_bbox_two_calls_are_equal_minus_timestamps(self) -> None:
        # A narrow (Valencia-scale) bbox exercises the memo's bbox-filter-on-hit
        # path distinctly from the whole-of-Spain scan above.
        first = server.load_all_leads(server.VALENCIA_BBOX)
        second = server.load_all_leads(server.VALENCIA_BBOX)

        self.assertEqual(len(first), len(second))
        first_sorted = sorted(_strip_time_derived(first), key=_sort_key)
        second_sorted = sorted(_strip_time_derived(second), key=_sort_key)
        self.assertEqual(first_sorted, second_sorted)

    def test_load_intelligence_bbox_filter_matches_unfiltered_scan(self) -> None:
        """A record present in a bbox-filtered call must appear, byte-identical
        (minus timestamps), in the unfiltered (bbox=None) call -- i.e. the memo's
        "enrich everything once, bbox-filter the cached list on hit" design
        (step 2) must not change which records exist or how they're normalized,
        only which subset is returned."""
        scoped = server.load_intelligence(server.VALENCIA_BBOX).get("records", [])
        full = server.load_intelligence(None).get("records", [])
        full_by_id = {str(r.get("id") or r.get("name")): r for r in _strip_time_derived(full)}

        self.assertGreater(len(scoped), 0)
        for record in _strip_time_derived(scoped):
            key = str(record.get("id") or record.get("name"))
            self.assertIn(key, full_by_id, f"in-bbox record {key} missing from unfiltered scan")
            self.assertEqual(record, full_by_id[key])


class MergeGoldenValueTests(unittest.TestCase):
    """Golden (frozen) outputs for remove_duplicate_leads/merge_gaia_stations,
    captured 2026-07-12 from the CURRENT (pre-step-3) implementation. See the
    module docstring: these literals are the proof the spatial-bucket rewrite
    in step 3 preserves exact merge/dedup semantics."""

    def test_merge_gaia_stations_matches_frozen_golden_output(self) -> None:
        mapareve_stations = [
            station("mr-1", "Carrefour Alfafar Parking", "Avenida Test 1", 39.4700, -0.3700,
                    sources=[{"name": "MapaREVE", "url": "https://mapareve.example/a"}]),
            station("mr-2", "Iberdrola Sagunto Plaza", "Calle Otra 9", 39.6400, -0.2700,
                    sources=[{"name": "MapaREVE", "url": "https://mapareve.example/b"}]),
            station("mr-3", "Repsol Service Station", "Autovia A-7 km 12", 40.0000, -3.7000,
                    sources=[{"name": "MapaREVE", "url": "https://mapareve.example/c"}]),
        ]
        gaia_stations = [
            # matches mr-1 by id (same coords too)
            station("mr-1", "Gaia Alfafar", "Addr", 39.4700, -0.3700,
                    sources=[{"name": "Gaia", "url": "https://gaia.example/a"}]),
            # matches mr-2 by shared URL >500km away -- the distance-blind URL
            # branch (test_live_merge.py's 1b) that a naive spatial grid would miss.
            station("gaia-far", "Totally different name", "Nowhere near", 40.5000, 1.0000,
                    sources=[{"name": "MapaREVE", "url": "https://mapareve.example/b"}]),
            # no id/url/proximity match -> appended as a new station
            station("gaia-new", "Gaia Standalone", "Addr new", 41.3800, 2.1700,
                    sources=[{"name": "Gaia", "url": "https://gaia.example/new"}]),
        ]

        merged = server.merge_gaia_stations(deepcopy(mapareve_stations), deepcopy(gaia_stations))

        expected = [
            {
                "id": "mr-1", "name": "Gaia Alfafar", "address": "Addr", "lat": 39.47, "lon": -0.37,
                "sources": [
                    {"name": "Gaia", "url": "https://gaia.example/a"},
                    {"name": "MapaREVE", "url": "https://mapareve.example/a"},
                ],
                "source_count": 2,
            },
            {
                "id": "gaia-far", "name": "Totally different name", "address": "Nowhere near",
                "lat": 40.5, "lon": 1.0,
                "sources": [{"name": "MapaREVE", "url": "https://mapareve.example/b"}],
                "source_count": 1,
            },
            {
                "id": "mr-3", "name": "Repsol Service Station", "address": "Autovia A-7 km 12",
                "lat": 40.0, "lon": -3.7,
                "sources": [{"name": "MapaREVE", "url": "https://mapareve.example/c"}],
            },
            {
                "id": "gaia-new", "name": "Gaia Standalone", "address": "Addr new",
                "lat": 41.38, "lon": 2.17,
                "sources": [{"name": "Gaia", "url": "https://gaia.example/new"}],
            },
        ]
        self.assertEqual(merged, expected)

    def test_merge_gaia_stations_id_migrating_to_a_different_slot_still_found(self) -> None:
        """Regression for a real bug caught by fuzz-comparing the step-3 spatial index
        against the pre-step-3 reference implementation (not committed -- see
        qa/run-log.md's plans/007 iteration): merge_gaia_stations can wholesale-REPLACE
        a slot (`merged[match_index] = gaia`), so an id string can migrate from the slot
        that originally carried it to a DIFFERENT slot later, via a match that has
        nothing to do with that id (a URL or proximity/name match). A naive
        "lowest-index-wins, write-once" id index goes stale in exactly this case and
        silently stops finding the id at its new location. The index must treat `ids`
        as append-only (every index that ever carried an id, like `urls`), never a
        single "current" index, so a later probe's true match is never dropped from the
        candidate set.

        Sequence: gaia1 matches station A (index 0) via URL and replaces it, so index 0
        no longer carries A's original id "shared-id". gaia2 matches station B (index 1)
        via proximity+name (NOT id) and replaces it, so id "shared-id" now MIGRATES onto
        index 1 -- a slot it never occupied during the initial index build. gaia3 (far
        from everything, no shared URL) must find its match via id "shared-id" alone,
        which by now only exists at index 1.
        """
        station_a = station("shared-id", "Alpha Station", "Addr A", 39.4700, -0.3700,
                             sources=[{"name": "MapaREVE", "url": "https://mapareve.example/site-a"}])
        station_b = station("other-id", "Beta Station", "Addr B", 40.6400, -3.7700,
                             sources=[{"name": "MapaREVE", "url": "https://mapareve.example/site-b"}])
        gaia1 = station("new-id-1", "Gaia One", "Gaia Addr 1", 10.0, 10.0,
                         sources=[{"name": "Gaia", "url": "https://mapareve.example/site-a"}])  # URL-matches A
        gaia2 = station("shared-id", "Beta Station Annex", "Addr B2", 40.6408993, -3.7700,
                         sources=[{"name": "Gaia", "url": "https://gaia.example/site-c"}])  # ~100m from B, shares "Beta"/"Station" tokens
        gaia3 = station("shared-id", "Gaia Three", "Gaia Addr 3", -5.0, -5.0,
                         sources=[{"name": "Gaia", "url": "https://gaia.example/site-d"}])  # far from everything; id-only match

        merged = server.merge_gaia_stations(
            [dict(station_a), dict(station_b)], [dict(gaia1), dict(gaia2), dict(gaia3)],
        )

        # ground truth captured from the pre-step-3 (unindexed, O(n*m)) implementation
        expected = [
            {
                "id": "new-id-1", "name": "Gaia One", "address": "Gaia Addr 1", "lat": 10.0, "lon": 10.0,
                "sources": [
                    {"name": "Gaia", "url": "https://mapareve.example/site-a"},
                    {"name": "MapaREVE", "url": "https://mapareve.example/site-a"},
                ],
                "source_count": 2,
            },
            {
                "id": "shared-id", "name": "Gaia Three", "address": "Gaia Addr 3", "lat": -5.0, "lon": -5.0,
                "sources": [
                    {"name": "Gaia", "url": "https://gaia.example/site-d"},
                    {"name": "Gaia", "url": "https://gaia.example/site-c"},
                    {"name": "MapaREVE", "url": "https://mapareve.example/site-b"},
                ],
                "source_count": 3,
            },
        ]
        self.assertEqual(len(merged), 2, "gaia3 must find station B's slot, not get appended as a 3rd row")
        self.assertEqual(merged, expected)

    def test_remove_duplicate_leads_matches_frozen_golden_output(self) -> None:
        live_stations = [
            station("live-1", "Station X", "Addr", 39.4700, -0.3700,
                    sources=[{"name": "MapaREVE", "url": "https://mapareve.example/x"}]),
            station("live-2", "Carrefour Alfafar Parking", "Avenida Test 1", 39.6400, -0.2700,
                    sources=[{"name": "MapaREVE", "url": "https://mapareve.example/y"}]),
        ]
        leads = [
            # matches live-1 by id
            station("lead-dup-id", "Lead dup by id", "Addr", 39.4700, -0.3700,
                    sources=[{"name": "LeadSrc", "url": "https://lead.example/dup-id"}]),
            # matches live-2 by ~100m proximity + shared name tokens (name affinity)
            station("lead-dup-name", "Carrefour Alfafar Entrada", "Avenida Test 2", 39.6408993, -0.2700,
                    sources=[{"name": "LeadSrc2", "url": "https://lead.example/dup-name"}]),
            # no match at all -> survives untouched
            station("lead-far", "Standalone lead far away", "Addr far", 41.3800, 2.1700,
                    sources=[{"name": "LeadSrc3", "url": "https://lead.example/far"}]),
        ]
        stations_copy = deepcopy(live_stations)

        remaining = server.remove_duplicate_leads(deepcopy(leads), stations_copy)

        expected_remaining = [
            {
                "id": "lead-dup-id", "name": "Lead dup by id", "address": "Addr", "lat": 39.47, "lon": -0.37,
                "sources": [{"name": "LeadSrc", "url": "https://lead.example/dup-id"}],
            },
            {
                "id": "lead-far", "name": "Standalone lead far away", "address": "Addr far",
                "lat": 41.38, "lon": 2.17,
                "sources": [{"name": "LeadSrc3", "url": "https://lead.example/far"}],
            },
        ]
        expected_stations_after = [
            {
                "id": "live-1", "name": "Station X", "address": "Addr", "lat": 39.47, "lon": -0.37,
                "sources": [{"name": "MapaREVE", "url": "https://mapareve.example/x"}],
            },
            {
                "id": "live-2", "name": "Carrefour Alfafar Parking", "address": "Avenida Test 1",
                "lat": 39.64, "lon": -0.27,
                "sources": [
                    {"name": "MapaREVE", "url": "https://mapareve.example/y"},
                    {"name": "LeadSrc2", "url": "https://lead.example/dup-name"},
                ],
                "source_count": 2,
            },
        ]
        self.assertEqual(remaining, expected_remaining)
        self.assertEqual(stations_copy, expected_stations_after)

    def test_merge_nearby_source_stations_matches_frozen_golden_output(self) -> None:
        """merge_nearby_source_stations uses its OWN ~80m bare-proximity predicate (no
        id/url/name-affinity check) -- see test_live_merge.py's
        MergeNearbySourceStationsTests docstring. step 3 must bucket it with its own
        radius, separate from likely_same_station's 0.12km gate."""
        existing = [
            station("mapareve-1", "Repsol Service Station", "Autovia A-7 km 12", 39.4700, -0.3700,
                    sources=[{"name": "MapaREVE", "url": "https://mapareve.example/1"}]),
        ]
        nearby = [
            # 0.079 km away -- just inside the 0.08km threshold; unrelated name,
            # merges anyway (bare proximity, characterized in plans/006).
            station("electromaps-1", "Colegio Público Rafelbunyol", "Calle Deporte 8",
                    39.4700 + 0.0007105, -0.3700,
                    sources=[{"name": "Electromaps", "url": "https://electromaps.example/1"}]),
        ]

        merged = server.merge_nearby_source_stations(deepcopy(existing), deepcopy(nearby))

        expected = [
            {
                "id": "mapareve-1", "name": "Repsol Service Station", "address": "Autovia A-7 km 12",
                "lat": 39.47, "lon": -0.37,
                "sources": [
                    {"name": "MapaREVE", "url": "https://mapareve.example/1"},
                    {"name": "Electromaps", "url": "https://electromaps.example/1"},
                ],
                "source_count": 2,
            },
        ]
        self.assertEqual(merged, expected)


def _strip_reuse_noise(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a live_response payload for the two-identical-calls equality check
    (plans/007 step 4): strip fields that legitimately differ between a fresh build
    and an assembled-cache reuse purely because of CACHE/DIAGNOSTIC/timing bookkeeping
    (whether THIS call was a raw-cache hit, how long this specific request took, which
    internal pipeline stages it actually ran through this time, how many records
    EARLIER stages dropped this time) -- never the actual station/lead/coverage/
    summary CONTENT, which must stay byte-identical. `cache.hit`/`age_seconds` differ
    on ANY two consecutive live_response calls within TTL regardless of the plans/007
    assembled-reuse optimization (the first call is always a live fetch, "hit": False;
    the second always finds the just-populated raw cache, "hit": True) -- that is
    pre-existing, correct cache semantics, not something this test is about."""
    out = deepcopy(payload)
    out.pop("cache", None)
    engine = ((out.get("meta") or {}).get("engine")) or {}
    engine.pop("elapsed_ms", None)
    engine.pop("stages", None)
    counts = engine.get("counts")
    if isinstance(counts, dict):
        counts.pop("dropped", None)
    return out


class AssembledPayloadReuseTests(unittest.TestCase):
    """plans/007 step 4: a second live_response call with the SAME cache_key/
    include_leads, within TTL, when Gaia ALSO serves a fresh non-stale hit, must
    reuse the assembled (merged+enriched) payload from the first call rather than
    re-running merges/dedup/enrich. Two invariants: (1) the two payloads are equal
    except for per-call timing/cache-age/diagnostic-stage fields (see
    _strip_reuse_noise); (2) the second call does not call enrich_station at all
    (mock-counter proof it truly skipped re-enrichment, not just got lucky)."""

    BBOX_PARAM = "39.30,-0.55,39.62,-0.20"  # matches test_live_coverage.py's explicit-bbox fixture

    def _station(self) -> Dict[str, Any]:
        return {
            "id": "assembled-1",
            "name": "Carrefour Alfafar Parking",
            "address": "Avenida Test 1",
            "lat": 39.4700,
            "lon": -0.3700,
            "available": 1,
            "total_evse": 1,
            "status": "AVAILABLE",
            "is_free": True,
            "is_cheap": True,
            "price_min": 0.0,
            "tesla_ok": True,
            "fast": False,
            "connectors": [{"label": "Type 2", "power_kw": 22, "status": "AVAILABLE"}],
            "parking": {"kind": "retail_parking", "paid": False},
            "source_url": "https://mapareve.example/assembled-1",
            "source": "MapaREVE public OCPI API",
            "reliability": "official-live",
            "sources": [{"name": "MapaREVE", "url": "https://mapareve.example/assembled-1"}],
            "last_seen": server.now_iso(),
        }

    def _lead(self) -> Dict[str, Any]:
        return {
            "id": "lead-assembled-1",
            "name": "Nearby community lead",
            "lat": 39.50,
            "lon": -0.30,
            "reliability": "manual-check-needed",
            "status": "VERIFY",
            "source": "Test lead source",
            "sources": [{"name": "Test lead source", "url": "https://example.com/lead-1"}],
        }

    def _fresh_source_cache(self) -> Dict[str, Any]:
        # hit=True, stale=False -- a genuinely fresh (not-degraded) sub-source cache
        # hit, the exact condition sub_sources_fresh_hit requires for Gaia.
        # populated_at (plans/007 REVISE, adviser Finding 1): every test below patches
        # fetch_gaia_live with a fixed `return_value` (a single dict
        # built once, reused verbatim across BOTH live_response calls in the test), so
        # a single call to this helper -> the same populated_at on both the write and
        # the read -- exactly representing "the same underlying Gaia population",
        # the identity the reuse gate now requires.
        return {"hit": True, "stale": False, "age_seconds": 12.0, "ttl_seconds": server.CACHE_TTL_SECONDS, "populated_at": time.time()}

    def _params(self) -> Dict[str, List[str]]:
        return {
            "include_leads": ["true"],
            "max_price": ["0.25"],
            "bbox": [self.BBOX_PARAM],
            "lat": ["39.4700"],
            "lon": ["-0.3700"],
        }

    def test_second_call_within_ttl_reuses_assembled_payload_without_reenrichment(self) -> None:
        original_cache = deepcopy(server.CACHE)
        try:
            server.CACHE.clear()
            server.CACHE.update({"ts": 0, "payload": None, "raw_count": 0, "error": None, "assembled": None})
            mapareve_payload = {"stations": [self._station()], "raw_count": 1, "fetched_at": server.now_iso()}
            gaia_payload = {"stations": [], "raw_count": 0, "cache": self._fresh_source_cache(), "errors": []}

            with (
                patch("server.fetch_mapareve", return_value=deepcopy(mapareve_payload)) as mock_mapareve,
                patch("server.fetch_gaia_live", return_value=deepcopy(gaia_payload)),
                patch("server.fetch_electromaps_live") as mock_electromaps,
                patch("server.load_all_leads", return_value=[self._lead()]) as mock_load_all_leads,
            ):
                params = self._params()
                status1, payload1 = server.live_response(params)
                self.assertEqual(status1, 200)
                self.assertEqual(len(payload1["stations"]), 1, "fixture station must survive the full pipeline")
                self.assertEqual(len(payload1["leads"]), 1, "fixture lead must survive the full pipeline")
                self.assertEqual(mock_mapareve.call_count, 1)
                self.assertEqual(mock_load_all_leads.call_count, 1)
                self.assertFalse(mock_electromaps.called, "no electromaps-sourced lead in this fixture -> must never be called")

                with patch("server.enrich_station", wraps=server.enrich_station) as mock_enrich:
                    status2, payload2 = server.live_response(params)
                    self.assertEqual(status2, 200)
                    self.assertEqual(
                        mock_enrich.call_count, 0,
                        "second call within TTL must reuse the assembled payload, not re-enrich",
                    )

                # Neither the raw MapaREVE fetch nor the lead memo lookup should have
                # been asked to do fresh work a second time.
                self.assertEqual(mock_mapareve.call_count, 1)
                self.assertEqual(mock_load_all_leads.call_count, 1)

            self.assertEqual(_strip_reuse_noise(payload1), _strip_reuse_noise(payload2))
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)

    def test_stale_sub_source_hit_falls_through_to_full_reenrich(self) -> None:
        """Safety rail: if Gaia reports cache.hit=True but cache.stale=True
        on the second call, the assembled entry must NOT be reused -- a stale
        sub-source hit means that source's OWN cache degraded between the write and
        this read, so the assembled snapshot can no longer be trusted to reflect it."""
        original_cache = deepcopy(server.CACHE)
        try:
            server.CACHE.clear()
            server.CACHE.update({"ts": 0, "payload": None, "raw_count": 0, "error": None, "assembled": None})
            mapareve_payload = {"stations": [self._station()], "raw_count": 1, "fetched_at": server.now_iso()}
            stale_gaia_cache = {"hit": True, "stale": True, "age_seconds": 90.0, "ttl_seconds": server.CACHE_TTL_SECONDS}

            with (
                patch("server.fetch_mapareve", return_value=deepcopy(mapareve_payload)) as mock_mapareve,
                patch("server.fetch_gaia_live", side_effect=[
                    {"stations": [], "raw_count": 0, "cache": self._fresh_source_cache(), "errors": []},
                    {"stations": [], "raw_count": 0, "cache": dict(stale_gaia_cache), "errors": []},
                ]),
                patch("server.fetch_electromaps_live"),
                patch("server.load_all_leads", return_value=[]),
            ):
                params = self._params()
                status1, payload1 = server.live_response(params)
                self.assertEqual(status1, 200)
                self.assertEqual(mock_mapareve.call_count, 1)

                with patch("server.enrich_station", wraps=server.enrich_station) as mock_enrich:
                    status2, payload2 = server.live_response(params)
                    self.assertEqual(status2, 200)
                    self.assertGreater(
                        mock_enrich.call_count, 0,
                        "a stale gaia cache hit must fall through to the full tail, not reuse the assembled entry",
                    )
                # cache_fresh (the raw MapaREVE cache) is unaffected by gaia's staleness
                # -- only the ASSEMBLED reuse must be declined.
                self.assertEqual(mock_mapareve.call_count, 1)
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)

    def test_excluded_origin_never_triggers_legacy_electromaps_refresh(self) -> None:
        """Excluded Consum evidence is removed before the old live-refresh
        seam and cannot mark an assembled payload as having performed provider work."""
        original_cache = deepcopy(server.CACHE)
        try:
            server.CACHE.clear()
            server.CACHE.update({"ts": 0, "payload": None, "raw_count": 0, "error": None, "assembled": None})
            mapareve_payload = {"stations": [self._station()], "raw_count": 1, "fetched_at": server.now_iso()}
            em_lead = {
                "id": "em-lead-1", "name": "Excluded locator lead", "lat": 39.47, "lon": -0.37,
                "reliability": "community-free", "status": "VERIFY", "source": "Consum store locator",
                "sources": [{"name": "Consum store locator", "url": "https://www.consum.es/supermercados/123"}],
                "raw": {"electromaps_id": "123"}, "last_seen": server.now_iso(),
            }

            with (
                patch("server.fetch_mapareve", return_value=deepcopy(mapareve_payload)) as mock_mapareve,
                patch("server.fetch_gaia_live", return_value={"stations": [], "raw_count": 0, "cache": self._fresh_source_cache(), "errors": []}),
                patch("server.fetch_electromaps_live", return_value={"stations": [], "checked_ids": {"123"}, "promoted_ids": set(), "dead_ids": set()}) as mock_electromaps,
                patch("server.load_all_leads", return_value=[dict(em_lead)]),
            ):
                params = self._params()
                status1, payload1 = server.live_response(params)
                self.assertEqual(status1, 200)
                self.assertFalse(mock_electromaps.called)
                self.assertNotIn("em-lead-1", {lead.get("id") for lead in payload1.get("leads") or []})
                assembled = server.CACHE.get("assembled")
                self.assertIsNotNone(assembled)
                self.assertFalse(assembled.get("electromaps_did_work"))

                status2, payload2 = server.live_response(params)
                self.assertEqual(status2, 200)
                self.assertFalse(mock_electromaps.called)
                self.assertNotIn("em-lead-1", {lead.get("id") for lead in payload2.get("leads") or []})
                self.assertEqual(mock_mapareve.call_count, 1)
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)


class MultiSlotOverviewCacheTests(unittest.TestCase):
    """plans/007 step 5: OVERVIEW_BYTES_CACHE/OVERVIEW_BBOX_BYTES_CACHE must hold
    several distinct filter/bbox combinations at once (FIFO-capped), not a single
    slot -- the pre-step-5 bug this pins: with a single "entry" slot, alternating
    between two filter combinations (A, B, A, B, ...) NEVER hit cache, because B
    always evicted A's slot before a repeat of A could reuse it."""

    def _fake_overview_response(self, params: Dict[str, List[str]], max_price: float, fast_only: bool):
        return 200, {"meta": {"max_price": max_price, "fast_only": fast_only, "bbox": (params.get("bbox") or [None])[0]}}

    def test_alternating_filter_toggle_both_serve_from_cache_on_repeat(self) -> None:
        original_cache = deepcopy(server.OVERVIEW_BYTES_CACHE)
        server.OVERVIEW_BYTES_CACHE.clear()
        try:
            with patch("server.overview_response", side_effect=self._fake_overview_response) as mocked:
                server.overview_response_bytes({}, 0.25, False)   # A: compute (miss)
                server.overview_response_bytes({}, 5.0, False)    # B: compute (miss)
                server.overview_response_bytes({}, 0.25, False)   # A again: must be a cache HIT now
                server.overview_response_bytes({}, 5.0, False)    # B again: must be a cache HIT now
            self.assertEqual(mocked.call_count, 2, "both A and B must stay cached simultaneously (no thrashing)")
        finally:
            server.OVERVIEW_BYTES_CACHE.clear()
            server.OVERVIEW_BYTES_CACHE.update(original_cache)

    def test_cache_evicts_oldest_once_over_the_cap(self) -> None:
        original_cache = deepcopy(server.OVERVIEW_BYTES_CACHE)
        server.OVERVIEW_BYTES_CACHE.clear()
        try:
            with patch("server.overview_response", side_effect=self._fake_overview_response):
                for i in range(server.OVERVIEW_BYTES_CACHE_MAX_ENTRIES + 3):
                    server.overview_response_bytes({}, float(i), False)
            self.assertLessEqual(len(server.OVERVIEW_BYTES_CACHE), server.OVERVIEW_BYTES_CACHE_MAX_ENTRIES)
        finally:
            server.OVERVIEW_BYTES_CACHE.clear()
            server.OVERVIEW_BYTES_CACHE.update(original_cache)

    def test_bbox_carrying_overview_reuses_on_repeat_in_its_own_capped_memo(self) -> None:
        """The SPA's instant first-paint layer always sends overview=true WITH a bbox
        (frontend/src/lib/api.ts chargersUrl); this used to bypass the bytes cache
        entirely. A repeat request for the SAME bbox must now be a cache hit, and must
        land in OVERVIEW_BBOX_BYTES_CACHE, not the no-bbox OVERVIEW_BYTES_CACHE."""
        original_bbox_cache = deepcopy(server.OVERVIEW_BBOX_BYTES_CACHE)
        original_default_cache = deepcopy(server.OVERVIEW_BYTES_CACHE)
        server.OVERVIEW_BBOX_BYTES_CACHE.clear()
        server.OVERVIEW_BYTES_CACHE.clear()
        try:
            bbox_params = {"bbox": ["39.30,-0.55,39.62,-0.20"]}
            with patch("server.overview_response", side_effect=self._fake_overview_response) as mocked:
                server.overview_response_bytes(bbox_params, 0.25, False)
                server.overview_response_bytes(bbox_params, 0.25, False)
            self.assertEqual(mocked.call_count, 1, "the second identical bbox-carrying request must be a cache hit")
            self.assertEqual(len(server.OVERVIEW_BBOX_BYTES_CACHE), 1)
            self.assertEqual(len(server.OVERVIEW_BYTES_CACHE), 0, "bbox-carrying requests must not pollute the no-bbox cache")
        finally:
            server.OVERVIEW_BBOX_BYTES_CACHE.clear()
            server.OVERVIEW_BBOX_BYTES_CACHE.update(original_bbox_cache)
            server.OVERVIEW_BYTES_CACHE.clear()
            server.OVERVIEW_BYTES_CACHE.update(original_default_cache)

    def test_compact_overview_reuses_within_minute_and_refreshes_derived_age_next_minute(self) -> None:
        original_bbox_cache = deepcopy(server.OVERVIEW_BBOX_BYTES_CACHE)
        original_default_cache = deepcopy(server.OVERVIEW_BYTES_CACHE)
        server.OVERVIEW_BBOX_BYTES_CACHE.clear()
        server.OVERVIEW_BYTES_CACHE.clear()
        try:
            bbox_params = {
                "bbox": ["39.30,-0.55,39.62,-0.20"],
                "compact": ["true"],
            }
            payloads = [
                (200, {"leads": [{"id": "lead", "freshness_minutes": 1}]}),
                (200, {"leads": [{"id": "lead", "freshness_minutes": 2}]}),
            ]
            with patch("server.overview_response", side_effect=payloads) as mocked:
                with patch("server.time.time", return_value=120.0):
                    first = json.loads(server.overview_response_bytes(bbox_params, 0.25, False)[1])
                with patch("server.time.time", return_value=179.999):
                    within_minute = json.loads(server.overview_response_bytes(bbox_params, 0.25, False)[1])
                with patch("server.time.time", return_value=180.0):
                    next_minute = json.loads(server.overview_response_bytes(bbox_params, 0.25, False)[1])

            self.assertEqual(first["leads"][0]["freshness_minutes"], 1)
            self.assertEqual(within_minute["leads"][0]["freshness_minutes"], 1)
            self.assertEqual(next_minute["leads"][0]["freshness_minutes"], 2)
            self.assertEqual(mocked.call_count, 2)
            self.assertLessEqual(len(server.OVERVIEW_BBOX_BYTES_CACHE), server.OVERVIEW_BBOX_BYTES_CACHE_MAX_ENTRIES)
            self.assertEqual(len(server.OVERVIEW_BYTES_CACHE), 0)
        finally:
            server.OVERVIEW_BBOX_BYTES_CACHE.clear()
            server.OVERVIEW_BBOX_BYTES_CACHE.update(original_bbox_cache)
            server.OVERVIEW_BYTES_CACHE.clear()
            server.OVERVIEW_BYTES_CACHE.update(original_default_cache)


class AssembledWriteRefusesStaleSubSourceTests(unittest.TestCase):
    """plans/007 REVISE (adviser Finding 1, HIGH; qa/defects.csv D-153): the original
    assembled-payload WRITE was gated ONLY on mapareve_fresh_fetch -- it never checked
    whether Gaia was serving a stale fallback at write time, and the
    READ gate only checked "is Gaia fresh NOW", never "is it the SAME population
    that was merged at write time". Mirrors the adviser's repro_reuse.py Repro A: call1
    merges a gaia station that only exists because gaia was serving a STALE fallback;
    call2 (within TTL) sees gaia genuinely refetched and empty (the station is gone
    from the real feed). Pre-fix, call2 incorrectly reused call1's merged snapshot and
    served the write-time-stale gaia row as AVAILABLE/go/route-ready with no stale
    warning at all."""

    def _mapareve_station(self, id_="mr-1", lat=39.37, lon=-0.37):
        return {
            "id": id_, "name": "MapaREVE Station", "address": "Addr", "lat": lat, "lon": lon,
            "available": 1, "total_evse": 1, "status": "AVAILABLE",
            "is_free": True, "is_cheap": True, "price_min": 0.0, "tesla_ok": True, "fast": False,
            "connectors": [{"label": "Type 2", "power_kw": 22, "status": "AVAILABLE"}],
            "parking": {"kind": "retail_parking", "paid": False},
            "source_url": f"https://mapareve.example/{id_}", "source": "MapaREVE public API",
            "reliability": "official-live",
            "sources": [{"name": "MapaREVE", "url": f"https://mapareve.example/{id_}"}],
            "last_seen": server.now_iso(),
        }

    def _gaia_station(self, id_="gaia-1", lat=39.36, lon=-0.36):
        return {
            "id": id_, "name": "Gaia Live Point", "address": "Addr", "lat": lat, "lon": lon,
            "available": 1, "total_evse": 1, "status": "AVAILABLE",
            "is_free": True, "is_cheap": True, "price_min": 0.0, "tesla_ok": True, "fast": False,
            "connectors": [{"label": "Type 2", "power_kw": 22, "status": "AVAILABLE"}],
            "parking": {"kind": "retail_parking", "paid": False},
            "source_url": f"https://gaia.example/{id_}", "source": "Gaia public API",
            "reliability": "official-live",
            "sources": [{"name": "Gaia", "url": f"https://gaia.example/{id_}"}],
            "last_seen": server.now_iso(),
        }

    def test_write_time_stale_gaia_row_is_never_served_as_fresh_on_a_later_reuse(self) -> None:
        original_cache = deepcopy(server.CACHE)
        try:
            server.CACHE.clear()
            server.CACHE.update({"ts": 0, "payload": None, "raw_count": 0, "error": None, "assembled": None})
            params = {"bbox": ["39.30,-0.45,39.40,-0.35"], "max_price": ["0.25"]}
            mapareve_payload = {"stations": [self._mapareve_station()], "raw_count": 1, "fetched_at": server.now_iso()}
            gaia_write = {
                "stations": [self._gaia_station()], "raw_count": 1, "errors": [],
                "cache": {"hit": True, "stale": True, "age_seconds": 700.0, "ttl_seconds": server.CACHE_TTL_SECONDS, "populated_at": 1000.0},
                "warning": "Gaia public-page refresh is slow; serving the last successful Gaia cache. Verify in the provider page before routing.",
            }
            # The real feed, refetched on call2: the gaia-1 station is GONE.
            gaia_read = {"stations": [], "raw_count": 0, "errors": [], "cache": {"hit": True, "stale": False, "age_seconds": 5.0, "ttl_seconds": server.CACHE_TTL_SECONDS, "populated_at": 2000.0}}
            with (
                patch("server.fetch_mapareve", return_value=deepcopy(mapareve_payload)) as mock_mapareve,
                patch("server.fetch_gaia_live", side_effect=[deepcopy(gaia_write), deepcopy(gaia_read)]),
                patch("server.fetch_electromaps_live"),
                patch("server.load_all_leads", return_value=[]),
            ):
                status1, payload1 = server.live_response(dict(params))
                self.assertEqual(status1, 200)
                ids1 = [st["id"] for st in payload1["stations"]]
                self.assertIn("gaia-1", ids1, "call1 must still merge the gaia row (even though write-time-stale) -- unchanged merge behavior")

                # A write-time-stale Gaia row is guarded immediately and must also
                # never be reusable on the next request.
                gaia_row_call1 = next(st for st in payload1["stations"] if st["id"] == "gaia-1")
                self.assertEqual((gaia_row_call1.get("decision") or {}).get("level"), "check")
                self.assertFalse((gaia_row_call1.get("verification") or {}).get("live_verified"))
                self.assertFalse(server.route_ready_now(gaia_row_call1))

                # The write must have been REFUSED (Finding 1): no assembled entry at
                # all, since gaia was stale this request.
                self.assertIsNone(server.CACHE.get("assembled"), "assembled must NOT be written when gaia was stale at write time")

                with patch("server.enrich_station", wraps=server.enrich_station) as mock_enrich:
                    status2, payload2 = server.live_response(dict(params))
                    self.assertEqual(status2, 200)
                    ids2 = [st["id"] for st in payload2["stations"]]
                    # With no assembled entry to reuse, call2 must fall through to the
                    # full tail (mock-counter proof, not an inferred content check).
                    self.assertGreater(mock_enrich.call_count, 0, "with no assembled entry to reuse, call2 must fall through to the full tail")

            self.assertNotIn("gaia-1", ids2, "the write-time-stale gaia row must never be served on a later read as if fresh")
            self.assertEqual(mock_mapareve.call_count, 1, "MapaREVE's OWN raw cache is unaffected by gaia's staleness -- call2 still reuses IT")
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)


class AssembledReuseRequiresBboxContainmentTests(unittest.TestCase):
    """plans/007 REVISE (adviser Finding 2, HIGH; qa/defects.csv D-154): assembled rows
    are filtered to the WRITE request's EXACT bbox, not the wider snapped cache_bbox
    envelope a stale comment used to claim. Mirrors the adviser's repro_reuse.py Repro
    B: call1 asks a NARROW bbox (only station s1 inside it); call2 asks a WIDER bbox
    sharing the same snapped 0.25-degree tile (both s1 AND s2 inside it, and both are
    in the raw MapaREVE cache, which covers the whole snapped envelope). Pre-fix,
    call2 silently dropped s2 -- a real, in-view, in-cache station -- because the
    assembled snapshot from call1 was scoped to the narrower bbox and reuse never
    re-checked coverage."""

    def _station(self, id_, lat, lon):
        return {
            "id": id_, "name": f"Station {id_}", "address": "Addr", "lat": lat, "lon": lon,
            "available": 1, "total_evse": 1, "status": "AVAILABLE",
            "is_free": True, "is_cheap": True, "price_min": 0.0, "tesla_ok": True, "fast": False,
            "connectors": [{"label": "Type 2", "power_kw": 22, "status": "AVAILABLE"}],
            "parking": {"kind": "retail_parking", "paid": False},
            "source_url": f"https://mapareve.example/{id_}", "source": "MapaREVE public API",
            "reliability": "official-live",
            "sources": [{"name": "MapaREVE", "url": f"https://mapareve.example/{id_}"}],
            "last_seen": server.now_iso(),
        }

    def _fresh_empty_sub_source(self) -> Dict[str, Any]:
        return {"stations": [], "raw_count": 0, "errors": [], "cache": {"hit": True, "stale": False, "age_seconds": 5.0, "ttl_seconds": server.CACHE_TTL_SECONDS, "populated_at": time.time()}}

    def test_wider_bbox_under_the_same_snapped_tile_still_sees_all_in_view_stations(self) -> None:
        original_cache = deepcopy(server.CACHE)
        try:
            server.CACHE.clear()
            server.CACHE.update({"ts": 0, "payload": None, "raw_count": 0, "error": None, "assembled": None})
            narrow_bbox = "39.30,-0.45,39.40,-0.35"   # only s1 inside
            wide_bbox = "39.26,-0.49,39.49,-0.26"     # s1 AND s2 inside; same snapped tile as narrow_bbox
            mapareve_payload = {
                "stations": [self._station("s1", 39.37, -0.37), self._station("s2", 39.47, -0.28)],
                "raw_count": 2, "fetched_at": server.now_iso(),
            }
            fresh_empty = self._fresh_empty_sub_source()

            with (
                patch("server.fetch_mapareve", return_value=deepcopy(mapareve_payload)) as mock_mapareve,
                patch("server.fetch_gaia_live", return_value=deepcopy(fresh_empty)),
                patch("server.fetch_electromaps_live"),
                patch("server.load_all_leads", return_value=[]),
            ):
                status1, payload1 = server.live_response({"bbox": [narrow_bbox], "max_price": ["0.25"]})
                self.assertEqual(status1, 200)
                ids1 = [st["id"] for st in payload1["stations"]]
                self.assertEqual(ids1, ["s1"], "the narrow bbox call must only see s1")

                with patch("server.enrich_station", wraps=server.enrich_station) as mock_enrich:
                    status2, payload2 = server.live_response({"bbox": [wide_bbox], "max_price": ["0.25"]})
                    self.assertEqual(status2, 200)
                    ids2 = [st["id"] for st in payload2["stations"]]
                    # A wider bbox is never contained in the narrower write bbox, so
                    # this MUST fall through to the full tail (which correctly
                    # re-derives from the full snapped-envelope raw MapaREVE cache),
                    # never reuse the narrower snapshot.
                    self.assertGreater(mock_enrich.call_count, 0, "a bbox NOT contained in the write bbox must fall through, not reuse")

            self.assertIn("s2", ids2, "s2 is in the raw cache AND inside the wider read bbox -- must not be silently dropped")
            self.assertIn("s1", ids2)
            self.assertEqual(mock_mapareve.call_count, 1, "MapaREVE's raw cache still covers the whole snapped envelope -- no new upstream fetch needed")
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)

    def test_a_bbox_contained_in_the_write_bbox_still_reuses_without_reenrichment(self) -> None:
        """Positive companion: reuse must still fire (the perf win survives) when the
        read bbox truly IS contained in the write bbox -- e.g. a small pan/zoom-in
        within the same area, the common case."""
        original_cache = deepcopy(server.CACHE)
        try:
            server.CACHE.clear()
            server.CACHE.update({"ts": 0, "payload": None, "raw_count": 0, "error": None, "assembled": None})
            wide_bbox = "39.26,-0.49,39.49,-0.26"
            narrower_bbox = "39.30,-0.45,39.40,-0.35"  # fully inside wide_bbox
            mapareve_payload = {
                "stations": [self._station("s1", 39.37, -0.37), self._station("s2", 39.47, -0.28)],
                "raw_count": 2, "fetched_at": server.now_iso(),
            }
            fresh_empty = self._fresh_empty_sub_source()

            with (
                patch("server.fetch_mapareve", return_value=deepcopy(mapareve_payload)),
                patch("server.fetch_gaia_live", return_value=deepcopy(fresh_empty)),
                patch("server.fetch_electromaps_live"),
                patch("server.load_all_leads", return_value=[]),
            ):
                status1, _ = server.live_response({"bbox": [wide_bbox], "max_price": ["0.25"]})
                self.assertEqual(status1, 200)
                self.assertIsNotNone(server.CACHE.get("assembled"), "Gaia fresh at write time -- assembled must be written")

                with patch("server.enrich_station", wraps=server.enrich_station) as mock_enrich:
                    status2, payload2 = server.live_response({"bbox": [narrower_bbox], "max_price": ["0.25"]})
                    self.assertEqual(status2, 200)
                    self.assertEqual(mock_enrich.call_count, 0, "a read bbox fully contained in the write bbox must reuse, not re-enrich")
            ids2 = [st["id"] for st in payload2["stations"]]
            self.assertEqual(ids2, ["s1"], "narrower bbox must still correctly exclude s2")
        finally:
            server.CACHE.clear()
            server.CACHE.update(original_cache)


class HealthEndpointAssembledCacheTests(unittest.TestCase):
    """plans/007 REVISE (adviser Finding 3, HIGH; qa/defects.csv D-155): /api/health
    used to dump the WHOLE CACHE (minus "payload") straight into json.dumps.
    CACHE["assembled"]["em_info"] ALWAYS holds real Python set() values
    (checked_ids/promoted_ids/dead_ids default to set() even when electromaps did no
    work this request -- see the em_info literal in live_response), so as soon as any
    assembled entry existed at all, /api/health 500'd (TypeError: set not JSON
    serializable) -- the D-144 top-level guard turned that into a clean-looking 500,
    so health reported the server DOWN exactly after its first successful live fetch.
    Mirrors the adviser's repro_health.py, but drives the REAL running Handler over a
    real socket (not a reimplementation of the endpoint's dict-building) so this
    proves the actual server code path, matching
    tests/test_request_resilience.py's Handler500GuardTests harness style."""

    def _station(self) -> Dict[str, Any]:
        return {
            "id": "health-1", "name": "Health Station", "address": "Addr", "lat": 39.3700, "lon": -0.3700,
            "available": 1, "total_evse": 1, "status": "AVAILABLE",
            "is_free": True, "is_cheap": True, "price_min": 0.0, "tesla_ok": True, "fast": False,
            "connectors": [{"label": "Type 2", "power_kw": 22, "status": "AVAILABLE"}],
            "parking": {"kind": "retail_parking", "paid": False},
            "source_url": "https://mapareve.example/health-1", "source": "MapaREVE public API",
            "reliability": "official-live",
            "sources": [{"name": "MapaREVE", "url": "https://mapareve.example/health-1"}],
            "last_seen": server.now_iso(),
        }

    def test_health_endpoint_survives_a_populated_assembled_cache_entry(self) -> None:
        original_cache = deepcopy(server.CACHE)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            server.CACHE.clear()
            server.CACHE.update({"ts": 0, "payload": None, "raw_count": 0, "error": None, "assembled": None})
            mapareve_payload = {"stations": [self._station()], "raw_count": 1, "fetched_at": server.now_iso()}
            fresh_empty = {"stations": [], "raw_count": 0, "errors": [], "cache": {"hit": True, "stale": False, "age_seconds": 5.0, "ttl_seconds": server.CACHE_TTL_SECONDS, "populated_at": time.time()}}
            with (
                patch("server.fetch_mapareve", return_value=deepcopy(mapareve_payload)),
                patch("server.fetch_gaia_live", return_value=deepcopy(fresh_empty)),
                patch("server.fetch_electromaps_live"),
                patch("server.load_all_leads", return_value=[]),
            ):
                server.live_response({"bbox": ["39.30,-0.45,39.40,-0.35"], "max_price": ["0.25"], "include_leads": ["true"]})

            assembled = server.CACHE.get("assembled")
            self.assertIsNotNone(assembled, "fixture must actually populate CACHE['assembled']")
            self.assertTrue(
                any(isinstance(v, set) for v in (assembled.get("em_info") or {}).values()),
                "fixture must reproduce the exact hazard: em_info's checked_ids/promoted_ids/dead_ids default to real Python set()s",
            )

            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                body = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(body.get("ok"))
            assembled_health = (body.get("cache") or {}).get("assembled") or {}
            self.assertNotIn("stations", assembled_health, "health must never dump station arrays")
            self.assertNotIn("leads", assembled_health, "health must never dump lead arrays")
            self.assertNotIn("em_info", assembled_health, "health must never dump the raw em_info (holds non-JSON-serializable sets)")
            self.assertEqual(assembled_health.get("stations_count"), 1)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
            server.CACHE.clear()
            server.CACHE.update(original_cache)


class TimeDerivedFieldsRecomputeOnReturnTests(unittest.TestCase):
    """plans/007 REVISE (adviser Finding 4, Medium; qa/defects.csv D-156):
    _INTEL_ENRICHED memoizes enrich_station, which stamps
    freshness_minutes=age_minutes(last_seen) (and the recommendation_score derived
    from it) exactly once per (db mtime, seeds mtime) key -- up to a full DB-refresh
    interval (about a week) later, a card's displayed evidence-age text was silently
    frozen at pool-build time. The SPA's ageText (frontend/src/lib/format.ts) prefers
    freshness_minutes over recomputing from last_seen, so this reached rendered
    lead-age text on both /api/chargers leads and /api/intel. load_intelligence/
    load_all_leads must recompute both fields on the deepcopied in-view subset they
    return, every call, without re-running enrich_station (which would defeat the
    memo)."""

    def test_freshness_minutes_reflects_a_moved_last_seen_across_two_memo_hit_calls(self) -> None:
        # Force the memo pool to build, then reach into the SAME cached list it hands
        # out (never copied internally -- only load_intelligence/load_all_leads
        # callers get deep copies) and directly age one record's last_seen, simulating
        # time passing without touching the DB file itself (which would also bump its
        # mtime and legitimately rebuild the whole pool -- not what this test isolates).
        intel_records, _intel_coords, _seed_records, _seed_coords = server._intel_enriched_pool()
        self.assertGreater(len(intel_records), 0, "fixture DB must have at least one record")
        target = intel_records[0]
        target_id = target.get("id") or target.get("name")
        original_last_seen = target.get("last_seen")
        original_source_fetched_at = target.get("source_fetched_at")
        try:
            # This test isolates per-call recomputation from one timestamp. Leads
            # with two evidence timestamps intentionally use the older one; that
            # conservative selection has its own LeadEvidenceAgeTests coverage.
            target["source_fetched_at"] = None
            target["last_seen"] = "2020-01-01T00:00:00+00:00"
            first = [r for r in server.load_intelligence(None).get("records", []) if (r.get("id") or r.get("name")) == target_id]
            self.assertEqual(len(first), 1)
            old_freshness = first[0].get("freshness_minutes")
            old_score = first[0].get("recommendation_score")
            self.assertIsNotNone(old_freshness)
            self.assertGreater(old_freshness, 60 * 24 * 365, "a 2020 timestamp must read as very old")

            target["last_seen"] = server.now_iso()
            second = [r for r in server.load_intelligence(None).get("records", []) if (r.get("id") or r.get("name")) == target_id]
            self.assertEqual(len(second), 1)
            new_freshness = second[0].get("freshness_minutes")
            new_score = second[0].get("recommendation_score")

            self.assertLess(new_freshness, 5, "a just-now last_seen must read as fresh on the very next call")
            self.assertNotEqual(old_freshness, new_freshness, "freshness_minutes must be recomputed on return, not frozen from pool-build time")
            self.assertNotEqual(old_score, new_score, "recommendation_score depends on freshness_minutes and must be recomputed too")

            # load_all_leads is the OTHER public accessor over the same memo pool.
            leads = [r for r in server.load_all_leads(None) if (r.get("id") or r.get("name")) == target_id]
            self.assertEqual(len(leads), 1)
            self.assertLess(leads[0].get("freshness_minutes"), 5)
        finally:
            target["last_seen"] = original_last_seen
            target["source_fetched_at"] = original_source_fetched_at


class LeadEvidenceAgeTests(unittest.TestCase):
    """Trust UX: a non-live lead's age uses its oldest valid evidence time,
    without rewriting source-fetch evidence as station telemetry in last_seen."""

    SOURCE_FETCHED_AT = "2026-07-13T05:30:00+00:00"

    @classmethod
    def _row(cls, **overrides: Any) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "id": "source-fetch-only",
            "name": "Source fetch only",
            "lat": 39.47,
            "lon": -0.37,
            "lead": True,
            "last_seen": None,
            "source_fetched_at": cls.SOURCE_FETCHED_AT,
            "status": "VERIFY",
            "available": 0,
            "is_free": True,
            "is_cheap": True,
            "tesla_ok": True,
            "source": "Public source",
            "source_url": "https://example.com/source",
            "sources": [{"name": "Public source", "url": "https://example.com/source"}],
            "reliability": "official-free",
            "parking": {"kind": "street_or_municipal", "paid": False},
        }
        row.update(overrides)
        return row

    def test_source_fetch_time_is_lead_evidence_age_without_mutating_last_seen(self) -> None:
        with patch(
            "server.age_minutes",
            side_effect=lambda value: 47 if value == self.SOURCE_FETCHED_AT else None,
        ):
            enriched = server.enrich_station(self._row(), live=False)

        self.assertEqual(enriched.get("freshness_minutes"), 47)
        self.assertIn("last_seen", enriched)
        self.assertIsNone(enriched.get("last_seen"))

    def test_public_lead_accessors_and_compact_payload_preserve_source_evidence_age(self) -> None:
        row = self._row(freshness_minutes=None)
        pool = ([row], [(row["lat"], row["lon"])], [], [])
        with (
            patch("server._intel_enriched_pool", return_value=pool),
            patch("server.load_intelligence_meta", return_value={"records": []}),
            patch(
                "server.age_minutes",
                side_effect=lambda value: 47 if value == self.SOURCE_FETCHED_AT else None,
            ),
        ):
            intel_row = server.load_intelligence()["records"][0]
            lead_row = server.load_all_leads()[0]

        self.assertEqual(intel_row.get("freshness_minutes"), 47)
        self.assertEqual(lead_row.get("freshness_minutes"), 47)
        self.assertIsNone(intel_row.get("last_seen"))
        self.assertIsNone(lead_row.get("last_seen"))

        compact = server.compact_station_for_ui(intel_row)
        self.assertEqual(compact.get("freshness_minutes"), 47)
        self.assertIsNone(compact.get("last_seen"))
        self.assertNotIn("source_fetched_at", compact)

    def test_merged_lead_uses_older_source_evidence_than_fresh_donor_observation(self) -> None:
        last_seen = "2026-07-13T05:45:00+00:00"
        older_source_fetch = "2025-06-01T00:00:00"
        with patch(
            "server.age_minutes",
            side_effect=lambda value: {
                last_seen: 5,
                older_source_fetch: 594_000,
            }.get(value),
        ):
            enriched = server.enrich_station(
                self._row(last_seen=last_seen, source_fetched_at=older_source_fetch),
                live=False,
            )

        self.assertEqual(enriched.get("freshness_minutes"), 594_000)
        self.assertEqual(enriched.get("last_seen"), last_seen)
        self.assertEqual(enriched.get("source_fetched_at"), older_source_fetch)

    def test_lead_keeps_older_station_observation_when_source_fetch_is_fresher(self) -> None:
        older_last_seen = "2025-06-01T00:00:00"
        newer_source_fetch = "2026-07-13T05:30:00+00:00"
        with patch(
            "server.age_minutes",
            side_effect=lambda value: {
                older_last_seen: 594_000,
                newer_source_fetch: 47,
            }.get(value),
        ):
            enriched = server.enrich_station(
                self._row(last_seen=older_last_seen, source_fetched_at=newer_source_fetch),
                live=False,
            )

        self.assertEqual(enriched.get("freshness_minutes"), 594_000)
        self.assertEqual(enriched.get("last_seen"), older_last_seen)
        self.assertEqual(enriched.get("source_fetched_at"), newer_source_fetch)

    def test_lead_ignores_an_aware_timestamp_that_overflows_utc_normalization(self) -> None:
        unusable_last_seen = "0001-01-01T00:00:00+14:00"
        with patch(
            "server.age_minutes",
            side_effect=lambda value: 47 if value == self.SOURCE_FETCHED_AT else None,
        ):
            enriched = server.enrich_station(
                self._row(last_seen=unusable_last_seen),
                live=False,
            )

        self.assertEqual(enriched.get("freshness_minutes"), 47)
        self.assertEqual(enriched.get("last_seen"), unusable_last_seen)
        self.assertEqual(enriched.get("source_fetched_at"), self.SOURCE_FETCHED_AT)

    def test_live_row_does_not_treat_source_fetch_time_as_live_freshness(self) -> None:
        with patch(
            "server.age_minutes",
            side_effect=lambda value: 47 if value == self.SOURCE_FETCHED_AT else None,
        ):
            enriched = server.enrich_station(
                self._row(lead=False, reliability="official-live"),
                live=True,
            )

        self.assertIsNone(enriched.get("freshness_minutes"))
        self.assertIsNone(enriched.get("last_seen"))


if __name__ == "__main__":
    unittest.main()


class DefaultOverviewWarmupTests(unittest.TestCase):
    def test_warmed_query_is_the_one_the_spa_preloads(self) -> None:
        """The server warms exactly the request frontend/index.html preloads."""
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        marker = '<link rel="preload" href="/api/chargers?'
        start = html.index(marker) + len(marker)
        preloaded = html[start:html.index('"', start)].replace("&amp;", "&")
        self.assertEqual(preloaded, server.DEFAULT_OVERVIEW_QUERY)

    def test_warmup_fills_the_cache_slot_a_request_reads(self) -> None:
        params = server.urllib.parse.parse_qs(server.DEFAULT_OVERVIEW_QUERY)
        cache = server.OVERVIEW_BBOX_BYTES_CACHE if params.get("bbox") else server.OVERVIEW_BYTES_CACHE
        saved = dict(cache)
        try:
            cache.clear()
            # Pin the clock so the per-minute cache key cannot roll over mid-test.
            with patch.object(server.time, "time", return_value=1_800_000_000.0):
                with patch.object(server.time, "sleep", side_effect=StopIteration):
                    with self.assertRaises(StopIteration):
                        server.warm_default_overview_forever()
                self.assertEqual(len(cache), 1)
                with patch.object(server, "overview_response", side_effect=AssertionError("cache miss")):
                    status, body, _ = server.overview_response_bytes(params, server._parse_max_price("5.0"), False)
            self.assertEqual(status, 200)
            self.assertTrue(body)
        finally:
            cache.clear()
            cache.update(saved)
