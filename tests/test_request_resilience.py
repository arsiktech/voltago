#!/usr/bin/env python3
"""Plan 004 regression: a bad timestamp or a race must never kill a request.

Three independent reliability defects, each with its own test class:

1. `age_minutes` raises TypeError on a timezone-naive `last_seen` value
   (reachable from external feed data via `enrich_station`/`decision_for`).
2. `Handler.do_GET` has no top-level exception guard, so an uncaught
   exception aborts the TCP connection with no HTTP response at all.
3. Seven module-level caches are mutated by concurrent request threads with
   no locking: unlocked check-then-act races can serve one filter-set's
   cached payload under another's cache key, and unlocked eviction loops
   (`for k in list(D.keys())[:N]: D.pop(k)`) can raise
   `RuntimeError: dictionary changed size during iteration` under concurrent
   insert.

Standalone-runnable, modeled on tests/test_live_coverage.py and
tests/test_nearby_backend.py (global-cache save/restore in setUp/tearDown,
`sys.path.insert` + `import server`).
"""
from __future__ import annotations

import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from copy import deepcopy
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import List
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402



def setUpModule():
    # Existing suites isolate their older feeds; keep the new runtime feeds offline too.
    live_patch = patch("server.fetch_public_live", return_value={"stations": [], "feeds": {}})
    live_patch.start()
    unittest.addModuleCleanup(live_patch.stop)


class AgeMinutesNaiveTimestampTests(unittest.TestCase):
    """Defect 1: parse_iso returns a naive datetime for offset-less input;
    age_minutes then subtracts it from an aware datetime.now(timezone.utc)."""

    def test_age_minutes_accepts_naive_iso_timestamp(self) -> None:
        # Electromaps/community feed timestamps are sometimes offset-less
        # ("naive"). This must not raise, or one malformed feed row kills the
        # whole request (defect 1 compounds with defect 2 in production).
        result = server.age_minutes("2026-07-01T10:00:00")
        self.assertIsInstance(result, int)
        self.assertGreaterEqual(result, 0)

    def test_age_minutes_still_handles_aware_timestamp(self) -> None:
        # Non-regression: offset-bearing input must keep working exactly as
        # before (this is the common case from now_iso()-stamped rows).
        result = server.age_minutes(server.now_iso())
        self.assertIsInstance(result, int)
        self.assertLessEqual(result, 1)


class Handler500GuardTests(unittest.TestCase):
    """Defect 2: an uncaught exception in do_GET aborts the connection with
    no HTTP response (BaseHTTPRequestHandler emits nothing on handler
    exceptions); the client sees a bare connection reset, not a 500."""

    def test_do_get_exception_returns_500_json_without_exception_detail(self) -> None:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with patch("server.live_response", side_effect=RuntimeError("boom - must never reach the client")):
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/chargers?max_price=0.25", timeout=5) as resp:
                        self.fail(f"expected HTTP 500, got a clean {resp.status} response")
                except urllib.error.HTTPError as exc:
                    self.assertEqual(exc.code, 500)
                    body = exc.read().decode("utf-8")
                    payload = json.loads(body)  # must be JSON, not a raw traceback
                    self.assertEqual(payload.get("error"), "internal_error")
                    self.assertIn("warning", payload)
                    # Fail-closed voice, consistent with existing warnings: no
                    # exception detail in the client-facing body.
                    self.assertNotIn("boom", body)
                    self.assertNotIn("Traceback", body)
                    self.assertNotIn("RuntimeError", body)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_do_head_exception_falls_back_to_bare_500(self) -> None:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            # web_root() is called unconditionally by do_HEAD for any non-/api/
            # path; forcing it to raise exercises do_HEAD's own guard without
            # depending on do_GET's.
            with patch("server.web_root", side_effect=RuntimeError("boom")):
                try:
                    req = urllib.request.Request(f"http://127.0.0.1:{port}/index.html", method="HEAD")
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        self.fail(f"expected HTTP 500, got a clean {resp.status} response")
                except urllib.error.HTTPError as exc:
                    self.assertEqual(exc.code, 500)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


def _empty_live_payload() -> dict:
    return {"stations": [], "raw_count": 0, "cache": {}, "errors": []}


class GaiaFailureCacheRecoveryTests(unittest.TestCase):
    """An empty failed fetch must not use the 15-minute stale-evidence window."""

    CONNECTOR_HTML = """<div id="jump-page-connector" x-data='{"connector": {
        "connectorId": "CIRCUTOR-0786-1", "status": "AVAILABLE", "price": "Free",
        "connector_type": "Type 2", "power_kw": 11
    }}'></div>"""

    def setUp(self) -> None:
        for patcher in (
            patch.dict(server.GAIA_CACHE, {}, clear=True),
            patch("server.GAIA_CACHE_TTL_SECONDS", 60),
            patch("server.GAIA_STALE_TTL_SECONDS", 900),
            patch("server.GAIA_STATION_SEEDS", [deepcopy(server.GAIA_STATION_SEEDS[0])]),
            patch("server.gaia_urls_for_seed", return_value=[server.GAIA_DEFAULT_JUMP_URL]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_empty_fetch_retries_after_normal_ttl_and_recovers(self) -> None:
        first_responses = (
            urllib.error.URLError(TimeoutError("TLS handshake timed out")),
            "<html>No public connector block</html>",
        )
        for first_response in first_responses:
            with self.subTest(first_response=type(first_response).__name__):
                server.GAIA_CACHE.clear()
                with (
                    patch("server.time.time", return_value=1000) as clock,
                    patch("server._log_upstream_error"),
                    patch("server.fetch_gaia_public_html", side_effect=[first_response, self.CONNECTOR_HTML]) as fetch,
                ):
                    failed = server.fetch_gaia_live()
                    self.assertEqual(failed["stations"], [])
                    self.assertEqual(failed["raw_count"], 0)
                    self.assertTrue(failed["errors"])
                    self.assertEqual(fetch.call_count, 1)

                    clock.return_value = 1059
                    for force in (False, True):
                        throttled = server.fetch_gaia_live(force=force)
                        self.assertEqual(throttled["stations"], [])
                        self.assertTrue(throttled["cache"]["hit"])
                        self.assertFalse(throttled["cache"]["stale"])
                    self.assertEqual(fetch.call_count, 1)

                    clock.return_value = 1061
                    recovered = server.fetch_gaia_live()
                    self.assertEqual(fetch.call_count, 2)
                    self.assertEqual(recovered["raw_count"], 1)
                    self.assertEqual(recovered["stations"][0]["available"], 1)
                    self.assertEqual(recovered["errors"], [])
                    self.assertFalse(recovered["cache"]["hit"])

    def test_station_evidence_keeps_stale_reuse_and_explicit_refresh(self) -> None:
        busy_html = self.CONNECTOR_HTML.replace('"AVAILABLE"', '"CHARGING"')
        with (
            patch("server.time.time", return_value=1000) as clock,
            patch("server.fetch_gaia_public_html", side_effect=[self.CONNECTOR_HTML, busy_html]) as fetch,
        ):
            fresh = server.fetch_gaia_live()
            clock.return_value = 1061
            stale = server.fetch_gaia_live()
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(stale["stations"], fresh["stations"])
            self.assertTrue(stale["cache"]["hit"])
            self.assertTrue(stale["cache"]["stale"])
            self.assertEqual(stale["cache"]["populated_at"], fresh["cache"]["populated_at"])
            self.assertIn("last successful Gaia cache", stale["warning"])

            refreshed = server.fetch_gaia_live(force=True)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(refreshed["stations"][0]["status"], "CHARGING")
            self.assertFalse(refreshed["cache"]["hit"])
            self.assertNotIn("warning", refreshed)


class ConcurrentLiveCacheStressTests(unittest.TestCase):
    """Defect 3a: unlocked check-then-act on the module-level live CACHE.

    16 threads x 30 iterations, alternating between two distinct filter sets
    (distinct cache_keys), assert (a) no exceptions anywhere and (b) every
    returned payload's station set matches the price it was requested with
    (a mismatch means one request served another's cached payload -- the
    torn-read/cross-key-bleed race)."""

    def setUp(self) -> None:
        self.original_cache = deepcopy(server.CACHE)
        self.original_gaia = deepcopy(server.GAIA_CACHE)
        server.CACHE.clear()
        server.GAIA_CACHE.clear()
        # Force much more aggressive GIL switching so the read-decide/write
        # interleaving this test targets isn't left to chance -- a race that
        # depends on default scheduling can pass "clean" on a fast machine
        # while still being live in production under real network jitter.
        self.original_switch_interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)

    def tearDown(self) -> None:
        sys.setswitchinterval(self.original_switch_interval)
        server.CACHE.clear()
        server.CACHE.update(self.original_cache)
        server.GAIA_CACHE.clear()
        server.GAIA_CACHE.update(self.original_gaia)

    def test_live_response_concurrent_requests_never_cross_filter_sets(self) -> None:
        filter_prices = [0.21, 0.24]

        def fake_mapareve(*, max_price, available_only, fast_only, bbox):
            time.sleep(0.001)  # widen the read/fetch/write race window
            return {
                "stations": [{
                    "id": f"station-{max_price}",
                    "name": f"Station for {max_price}",
                    "lat": 39.47,
                    "lon": -0.37,
                    "available": 1,
                    "status": "AVAILABLE",
                    "is_free": False,
                    "is_cheap": True,
                    "price_min": max_price,
                    "tesla_ok": True,
                    "parking": {"kind": "street_or_municipal", "paid": False},
                    "source": "Test live source",
                    "source_url": "https://example.com/stress",
                    "reliability": "official-live",
                    "last_seen": server.now_iso(),
                }],
                "raw_count": 1,
                "fetched_at": server.now_iso(),
            }

        def fake_gaia(*, force):
            time.sleep(0.001)
            return _empty_live_payload()

        errors: List[str] = []
        errors_lock = threading.Lock()

        def worker(thread_id: int) -> None:
            for i in range(30):
                price = filter_prices[(thread_id + i) % 2]
                try:
                    _status, payload = server.live_response({
                        "max_price": [str(price)],
                        "force": ["false"],
                    })
                    for st in payload.get("stations") or []:
                        expected_id = f"station-{price}"
                        if st.get("id") != expected_id:
                            raise AssertionError(
                                f"cross-key bleed: requested max_price={price} but got station {st.get('id')!r}"
                            )
                except Exception as exc:  # collected, not re-raised: keep other threads running
                    with errors_lock:
                        errors.append(f"{type(exc).__name__}: {exc}")

        with (
            patch("server.fetch_mapareve", side_effect=fake_mapareve),
            patch("server.fetch_gaia_live", side_effect=fake_gaia),
        ):
            threads = [threading.Thread(target=worker, args=(t,)) for t in range(16)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)
            self.assertFalse(any(t.is_alive() for t in threads), "worker thread(s) did not finish (deadlock?)")

        self.assertEqual(errors, [], f"{len(errors)} concurrency error(s); first few: {errors[:5]}")


class ConcurrentGeocodeCacheEvictionTests(unittest.TestCase):
    """Defect 3b: GEOCODE_CACHE's bounded-eviction loop
    (`for k in list(D.keys())[:N]: D.pop(k)`) iterates a snapshot of the
    dict's keys while other threads concurrently insert new keys, which can
    raise `RuntimeError: dictionary changed size during iteration`."""

    def setUp(self) -> None:
        self.original = dict(server.GEOCODE_CACHE)
        server.GEOCODE_CACHE.clear()
        self.original_switch_interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)

    def tearDown(self) -> None:
        sys.setswitchinterval(self.original_switch_interval)
        server.GEOCODE_CACHE.clear()
        server.GEOCODE_CACHE.update(self.original)

    def test_geocode_search_eviction_survives_concurrent_unique_inserts(self) -> None:
        class _FakeGeocodeResponse:
            def __enter__(self) -> "_FakeGeocodeResponse":
                return self

            def __exit__(self, *exc: object) -> bool:
                return False

            def read(self) -> bytes:
                return b"[]"

        errors: List[str] = []
        errors_lock = threading.Lock()

        def worker(thread_id: int) -> None:
            for i in range(100):
                # Unique query per call -> unique GEOCODE_CACHE key per call,
                # so 8 x 100 = 800 unique inserts comfortably cross the
                # 500-entry eviction threshold while other threads are still
                # inserting.
                query = f"volta-eviction-probe-{thread_id}-{i}"
                try:
                    server.geocode_search(query)
                except Exception as exc:
                    with errors_lock:
                        errors.append(f"{type(exc).__name__}: {exc}")

        with patch("server.urllib.request.urlopen", side_effect=lambda *a, **kw: _FakeGeocodeResponse()):
            threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)
            self.assertFalse(any(t.is_alive() for t in threads), "worker thread(s) did not finish (deadlock?)")

        self.assertEqual(errors, [], f"{len(errors)} eviction-race error(s); first few: {errors[:5]}")


if __name__ == "__main__":
    unittest.main()
