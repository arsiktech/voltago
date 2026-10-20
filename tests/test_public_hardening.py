#!/usr/bin/env python3
"""Plan 005 regression: public-deploy hardening.

Covers: token-bucket rate limiting (+ loopback/trusted-proxy exemption),
upstream-fetch concurrency semaphore fail-closed fallback, max_price
clamp/snap, response hardening headers (+ env-gated CSP), client-visible
warning/error sanitization (exception class name only, never the message),
log-line query-string redaction, and the guarded Madrid timezone loader.

Standalone-runnable, modeled on tests/test_request_resilience.py (real
ThreadingHTTPServer + urllib client, global-state save/restore in
setUp/tearDown, sys.path.insert + import server).
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
from datetime import timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


def _start_server() -> tuple:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _stop_server(httpd: ThreadingHTTPServer, thread: threading.Thread) -> None:
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def _fake_mapareve(*, max_price: float, available_only: bool, fast_only: bool, bbox: dict) -> dict:
    return {"stations": [], "raw_count": 0, "fetched_at": server.now_iso(), "source": "test"}


def _fake_gaia(*, force: bool) -> dict:
    return {"stations": [], "raw_count": 0, "cache": {"hit": False}, "errors": []}


def _patch_live_sources():
    return (
        patch("server.fetch_mapareve", side_effect=_fake_mapareve),
        patch("server.fetch_gaia_live", side_effect=_fake_gaia),
    )



def setUpModule():
    # Existing suites isolate their older feeds; keep the new runtime feeds offline too.
    live_patch = patch("server.fetch_public_live", return_value={"stations": [], "feeds": {}})
    live_patch.start()
    unittest.addModuleCleanup(live_patch.stop)


class _LiveCacheResetMixin:
    """Shared setUp/tearDown for tests that call into live_response: save/restore
    the module-level caches so one test's state can never leak into another
    (same convention as tests/test_request_resilience.py)."""

    def setUp(self) -> None:
        self._orig_cache = deepcopy(server.CACHE)
        self._orig_gaia = deepcopy(server.GAIA_CACHE)
        self._orig_buckets = dict(server.RATE_BUCKETS)
        server.CACHE.clear()
        server.CACHE.update({"ts": 0, "payload": None, "raw_count": 0, "error": None})
        server.GAIA_CACHE.clear()
        server.RATE_BUCKETS.clear()

    def tearDown(self) -> None:
        server.CACHE.clear()
        server.CACHE.update(self._orig_cache)
        server.GAIA_CACHE.clear()
        server.GAIA_CACHE.update(self._orig_gaia)
        server.RATE_BUCKETS.clear()
        server.RATE_BUCKETS.update(self._orig_buckets)


class RateLimitTests(_LiveCacheResetMixin, unittest.TestCase):
    """Step 1 test 1: the 6th /api/chargers request from one client inside a
    minute -> HTTP 429 with a JSON body and Retry-After; loopback callers with
    no forwarded identity are exempt so local dev/QA is never throttled."""

    def test_sixth_request_from_forwarded_client_is_429_with_retry_after(self) -> None:
        httpd, thread = _start_server()
        port = httpd.server_address[1]
        try:
            patchers = _patch_live_sources()
            with patchers[0], patchers[1], \
                 patch("server.RATE_LIMIT_RPM", 5), patch("server.VOLTA_TRUST_PROXY", True):
                statuses = []
                bodies = []
                headers_seen = []
                for _ in range(6):
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/chargers?max_price=0.25",
                        headers={"X-Forwarded-For": "203.0.113.9"},
                    )
                    try:
                        with urllib.request.urlopen(req, timeout=5) as resp:
                            statuses.append(resp.status)
                            bodies.append(None)
                            headers_seen.append(dict(resp.headers))
                    except urllib.error.HTTPError as exc:
                        statuses.append(exc.code)
                        bodies.append(json.loads(exc.read().decode("utf-8")))
                        headers_seen.append(dict(exc.headers))
            self.assertEqual(statuses, [200, 200, 200, 200, 200, 429])
            self.assertIn("Retry-After", headers_seen[5])
            self.assertEqual(bodies[5].get("error"), "rate_limited")
            self.assertIn("warning", bodies[5])
        finally:
            _stop_server(httpd, thread)

    def test_loopback_caller_with_no_forwarded_header_is_never_rate_limited(self) -> None:
        httpd, thread = _start_server()
        port = httpd.server_address[1]
        try:
            patchers = _patch_live_sources()
            with patchers[0], patchers[1], patch("server.RATE_LIMIT_RPM", 2):
                for _ in range(6):
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/chargers?max_price=0.25", timeout=5
                    ) as resp:
                        self.assertEqual(resp.status, 200)
        finally:
            _stop_server(httpd, thread)


class GeocodeRateLimitTests(unittest.TestCase):
    """Step 3 verify: geocode has its own, tighter bucket."""

    def setUp(self) -> None:
        self._orig_buckets = dict(server.RATE_BUCKETS)
        self._orig_geocode_cache = dict(server.GEOCODE_CACHE)
        server.RATE_BUCKETS.clear()
        server.GEOCODE_CACHE.clear()

    def tearDown(self) -> None:
        server.RATE_BUCKETS.clear()
        server.RATE_BUCKETS.update(self._orig_buckets)
        server.GEOCODE_CACHE.clear()
        server.GEOCODE_CACHE.update(self._orig_geocode_cache)

    def test_geocode_default_limit_is_tighter_than_chargers(self) -> None:
        self.assertEqual(server.GEOCODE_RATE_LIMIT_RPM, 10)
        self.assertLess(server.GEOCODE_RATE_LIMIT_RPM, server.RATE_LIMIT_RPM)

    def test_third_geocode_request_from_forwarded_client_is_429(self) -> None:
        # Pre-seed the geocode cache so geocode_search() never needs a real network
        # call: NOTE patching server.urllib.request.urlopen would ALSO redirect this
        # test's own outer HTTP client (urllib.request is one shared module object,
        # not a server-local copy) -- cache-seeding sidesteps that collision entirely.
        server.GEOCODE_CACHE["valencia"] = {"ts": time.time(), "results": []}
        httpd, thread = _start_server()
        port = httpd.server_address[1]
        try:
            with patch("server.GEOCODE_RATE_LIMIT_RPM", 2), patch("server.VOLTA_TRUST_PROXY", True):
                statuses = []
                for _ in range(3):
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/geocode?q=Valencia",
                        headers={"X-Forwarded-For": "198.51.100.7"},
                    )
                    try:
                        with urllib.request.urlopen(req, timeout=5) as resp:
                            statuses.append(resp.status)
                    except urllib.error.HTTPError as exc:
                        statuses.append(exc.code)
                        exc.read()
            self.assertEqual(statuses, [200, 200, 429])
        finally:
            _stop_server(httpd, thread)


class MaxPriceClampTests(_LiveCacheResetMixin, unittest.TestCase):
    """Step 1 test 2: max_price is clamped to [0, 5] and snapped to 2 decimals
    at parse time, echoed via meta.engine.viewport.cache_key."""

    def _get(self, port: int, query: str) -> dict:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/chargers?{query}", timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_exact_zero_max_price_is_preserved_for_live_request(self) -> None:
        httpd, thread = _start_server()
        port = httpd.server_address[1]
        try:
            patchers = _patch_live_sources()
            with patchers[0], patchers[1]:
                payload = self._get(port, "max_price=0")
            self.assertEqual(payload["meta"]["price_cap_eur_kwh"], 0.0)
        finally:
            _stop_server(httpd, thread)

    def test_exact_zero_max_price_is_preserved_for_compact_overview_request(self) -> None:
        httpd, thread = _start_server()
        port = httpd.server_address[1]
        try:
            zero_payload = self._get(port, "overview=true&compact=true&max_price=0")
            default_payload = self._get(port, "overview=true&compact=true")
        finally:
            _stop_server(httpd, thread)

        default_positive_prices = [
            lead["price_min"]
            for lead in default_payload["leads"]
            if lead.get("price_min") is not None and lead["price_min"] > 0
        ]
        self.assertIs(zero_payload["meta"]["compact"], True)
        self.assertTrue(default_positive_prices, "default-cap fixture must include a paid cheap lead")
        self.assertFalse(
            any(
                lead.get("price_min") is not None and lead["price_min"] > 0
                for lead in zero_payload["leads"]
            )
        )

    def test_invalid_max_price_falls_back_to_default(self) -> None:
        httpd, thread = _start_server()
        port = httpd.server_address[1]
        try:
            patchers = _patch_live_sources()
            with patchers[0], patchers[1]:
                payload = self._get(port, "max_price=abc")
            self.assertEqual(payload["meta"]["price_cap_eur_kwh"], server.PRICE_MAX_DEFAULT)
        finally:
            _stop_server(httpd, thread)

    def test_non_finite_max_prices_share_the_finite_default_response_and_cache_key(self) -> None:
        httpd, thread = _start_server()
        port = httpd.server_address[1]
        try:
            patchers = _patch_live_sources()
            with patchers[0], patchers[1]:
                cache_keys = set()
                for raw_value in ("nan", "inf", "-inf"):
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/chargers?max_price={raw_value}",
                        timeout=5,
                    ) as resp:
                        raw_body = resp.read().decode("utf-8")
                    self.assertNotRegex(raw_body, r"\b(?:NaN|Infinity|-Infinity)\b")
                    payload = json.loads(
                        raw_body,
                        parse_constant=lambda token: self.fail(
                            f"non-standard JSON constant escaped into the API response: {token}"
                        ),
                    )
                    self.assertEqual(
                        payload["meta"]["price_cap_eur_kwh"],
                        server.PRICE_MAX_DEFAULT,
                    )
                    cache_keys.add(payload["meta"]["engine"]["viewport"]["cache_key"])

            self.assertEqual(len(cache_keys), 1)
            self.assertIn(str(server.PRICE_MAX_DEFAULT), cache_keys.pop())
        finally:
            _stop_server(httpd, thread)

    def test_missing_max_price_uses_default(self) -> None:
        httpd, thread = _start_server()
        port = httpd.server_address[1]
        try:
            patchers = _patch_live_sources()
            with patchers[0], patchers[1]:
                payload = self._get(port, "")
            self.assertEqual(payload["meta"]["price_cap_eur_kwh"], server.PRICE_MAX_DEFAULT)
        finally:
            _stop_server(httpd, thread)

    def test_high_max_price_is_clamped_to_five(self) -> None:
        httpd, thread = _start_server()
        port = httpd.server_address[1]
        try:
            patchers = _patch_live_sources()
            with patchers[0], patchers[1]:
                payload = self._get(port, "max_price=99")
            self.assertEqual(payload["meta"]["price_cap_eur_kwh"], 5.0)
            cache_key = payload["meta"]["engine"]["viewport"]["cache_key"]
            self.assertIn("5.0", cache_key)
            self.assertNotIn("99", cache_key)
        finally:
            _stop_server(httpd, thread)

    def test_max_price_snaps_to_two_decimals(self) -> None:
        httpd, thread = _start_server()
        port = httpd.server_address[1]
        try:
            patchers = _patch_live_sources()
            with patchers[0], patchers[1]:
                payload = self._get(port, "max_price=0.276")
            self.assertEqual(payload["meta"]["price_cap_eur_kwh"], 0.28)
        finally:
            _stop_server(httpd, thread)

    def test_negative_max_price_is_clamped_to_zero(self) -> None:
        httpd, thread = _start_server()
        port = httpd.server_address[1]
        try:
            patchers = _patch_live_sources()
            with patchers[0], patchers[1]:
                payload = self._get(port, "max_price=-3")
            self.assertEqual(payload["meta"]["price_cap_eur_kwh"], 0.0)
        finally:
            _stop_server(httpd, thread)


class ResponseHeaderTests(_LiveCacheResetMixin, unittest.TestCase):
    """Step 1 test 3: hardening headers on /api/* and the static shell; CSP is
    asserted conditionally on the VOLTA_CSP module constant so the test
    documents reality instead of assuming a default."""

    def test_api_response_carries_hardening_headers(self) -> None:
        httpd, thread = _start_server()
        port = httpd.server_address[1]
        try:
            patchers = _patch_live_sources()
            with patchers[0], patchers[1]:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/chargers?max_price=0.25", timeout=5
                ) as resp:
                    self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
                    self.assertEqual(resp.headers.get("Referrer-Policy"), "no-referrer")
                    self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")
        finally:
            _stop_server(httpd, thread)

    def test_static_html_response_carries_hardening_headers_and_conditional_csp(self) -> None:
        httpd, thread = _start_server()
        port = httpd.server_address[1]
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
                self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
                self.assertEqual(resp.headers.get("Referrer-Policy"), "no-referrer")
                self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")
                if server.VOLTA_CSP:
                    self.assertEqual(resp.headers.get("Content-Security-Policy"), server.VOLTA_CSP)
                else:
                    self.assertIsNone(resp.headers.get("Content-Security-Policy"))
        finally:
            _stop_server(httpd, thread)

    def test_csp_header_present_when_volta_csp_is_set(self) -> None:
        httpd, thread = _start_server()
        port = httpd.server_address[1]
        try:
            with patch("server.VOLTA_CSP", "default-src 'self'"):
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
                    self.assertEqual(resp.headers.get("Content-Security-Policy"), "default-src 'self'")
        finally:
            _stop_server(httpd, thread)


class WarningSanitizationTests(_LiveCacheResetMixin, unittest.TestCase):
    """Step 1 test 4: a failed upstream fetch's exception CLASS name may reach
    the client; the exception MESSAGE (which can carry upstream-internal
    detail) must never leave the server."""

    def test_mapareve_failure_warning_hides_exception_message(self) -> None:
        with patch("server.fetch_mapareve", side_effect=RuntimeError("secret-internal-detail")), \
             patch("server.fetch_gaia_live", side_effect=_fake_gaia):
            status, payload = server.live_response({"max_price": ["0.25"], "force": ["false"]})
        self.assertEqual(status, 200)
        self.assertIn("RuntimeError", payload.get("warning") or "")
        full_response_text = json.dumps(payload)
        self.assertNotIn("secret-internal-detail", full_response_text)


class UpstreamSemaphoreFallbackTests(_LiveCacheResetMixin, unittest.TestCase):
    """Step 2: when the upstream-concurrency semaphore can't be acquired within
    its timeout, live_response must fall through to the EXISTING fail-closed
    branch verbatim -- no new error shape."""

    def test_semaphore_exhaustion_reuses_fail_closed_branch(self) -> None:
        busy = threading.BoundedSemaphore(1)
        busy.acquire()  # hold the only permit for the duration of the call
        try:
            with patch("server.LIVE_FETCH_SEMAPHORE", busy), \
                 patch("server.LIVE_FETCH_SEMAPHORE_ACQUIRE_TIMEOUT_SECONDS", 0.05):
                status, payload = server.live_response({"max_price": ["0.25"], "force": ["false"]})
        finally:
            busy.release()
        self.assertEqual(status, 200)
        self.assertEqual(payload["stations"], [])
        self.assertIn("nothing is presented as live", payload.get("warning") or "")


class MadridTimezoneGuardTests(unittest.TestCase):
    """Step 1 test 5 / Step 6: _load_madrid_tz() falls back to timezone.utc if
    ZoneInfo is missing or raises (e.g. tzdata absent on a slim image)."""

    def test_falls_back_to_utc_when_zoneinfo_raises(self) -> None:
        with patch("server.ZoneInfo", side_effect=Exception("simulated missing tzdata")):
            self.assertEqual(server._load_madrid_tz(), timezone.utc)

    def test_falls_back_to_utc_when_zoneinfo_is_none(self) -> None:
        with patch("server.ZoneInfo", None):
            self.assertEqual(server._load_madrid_tz(), timezone.utc)

    def test_module_constant_is_a_valid_timezone(self) -> None:
        # Real behavior on this machine (tzdata present): must be the actual
        # Europe/Madrid zone, not silently downgraded to UTC.
        self.assertEqual(str(server.MADRID_TZ), "Europe/Madrid")


class LogSanitizationTests(unittest.TestCase):
    """Step 5: server log lines strip the query string for /api/geocode and
    /api/chargers (search terms, exact viewport) but keep everything else,
    including the client IP (the rate limiter needs it)."""

    def test_strips_geocode_query_string(self) -> None:
        raw = '"GET /api/geocode?q=SECRET_TESTQUERY HTTP/1.1" 200 -'
        sanitized = server._sanitize_log_line(raw)
        self.assertNotIn("SECRET_TESTQUERY", sanitized)
        self.assertIn("/api/geocode", sanitized)
        self.assertIn("HTTP/1.1", sanitized)

    def test_strips_chargers_query_string(self) -> None:
        raw = '"GET /api/chargers?bbox=39,-0,40,0&lat=39.47&lon=-0.37 HTTP/1.1" 200 -'
        sanitized = server._sanitize_log_line(raw)
        self.assertNotIn("bbox=", sanitized)
        self.assertNotIn("39.47", sanitized)
        self.assertIn("/api/chargers", sanitized)

    def test_leaves_unrelated_paths_untouched(self) -> None:
        raw = '"GET /api/health HTTP/1.1" 200 -'
        self.assertEqual(server._sanitize_log_line(raw), raw)


class RequestQueueSizeTests(unittest.TestCase):
    """Carry-forward P1 adviser note: the stdlib socketserver default backlog
    (5) drops connections under a 20/30-way burst. Raised via a server
    subclass class attribute."""

    def test_server_class_backlog_is_raised(self) -> None:
        self.assertGreaterEqual(server.VoltaHTTPServer.request_queue_size, 128)


if __name__ == "__main__":
    unittest.main()
