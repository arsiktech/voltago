#!/usr/bin/env python3
"""Regression tests for the narrow read-only POST exceptions (MapaREVE query, Feníe list).

No test here touches the public internet: policy decisions are exercised on real
``urllib.request.Request`` objects, and redirect behaviour is exercised against a real
loopback HTTP server. Stdlib unittest.
"""
from __future__ import annotations

import http.cookiejar
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import server  # noqa: E402
import ingest_sources as ing  # noqa: E402
from scripts import source_transport as transport  # noqa: E402
import source_transport as ingest_transport  # noqa: E402  (module instance ingest_sources uses)

MAPAREVE_URL = "https://www.mapareve.es/api/public/v1/locations?page=1&per_page=20"
FENIE_URL = "https://recarga.fenieenergia.es/punto-recarga/todos"


def _mapareve_body(**overrides) -> bytes:
    return json.dumps({**server.mapareve_payload(0.25, bbox=server.VALENCIA_BBOX), **overrides}).encode()


def _post(url: str, data: bytes | None, **headers) -> urllib.request.Request:
    return urllib.request.Request(url, data=data, headers=headers, method="POST")


class MapareveExceptionTests(unittest.TestCase):
    def test_runtime_and_ingestion_queries_are_both_approved(self) -> None:
        runtime = _post(MAPAREVE_URL, _mapareve_body())
        self.assertEqual(transport.classify_post_exception(runtime), "mapareve-locations-query")
        ingestion_body = json.dumps(
            {**ing.MAPAREVE_PUBLIC_TENERIFE_BBOX, "zoom": 9, "only_ocpi": True, "available": False}
        ).encode()
        ingestion = _post(f"{ing.MAPAREVE_PUBLIC_LOCATIONS_URL}?page=3&per_page=20", ingestion_body)
        self.assertEqual(ingest_transport.classify_post_exception(ingestion), "mapareve-locations-query")

    def test_right_origin_wrong_path_is_blocked(self) -> None:
        for path in ("/api/public/v1/locations/export", "/api/public/v1/location", "/api/private/v1/locations",
                     "/api/public/v1/locations/", "//api/public/v1/locations", "/"):
            with self.subTest(path=path):
                with self.assertRaises(transport.SourcePolicyBlocked):
                    transport.classify_post_exception(_post(f"https://www.mapareve.es{path}?page=1", _mapareve_body()))

    def test_wrong_origin_scheme_port_userinfo_are_blocked(self) -> None:
        for url in (
            "http://www.mapareve.es/api/public/v1/locations?page=1",
            "https://mapareve.es/api/public/v1/locations?page=1",
            "https://www.mapareve.es.evil.example/api/public/v1/locations?page=1",
            "https://evil.example/www.mapareve.es/api/public/v1/locations",
            "https://www.mapareve.es:8443/api/public/v1/locations?page=1",
            "https://user:pw@www.mapareve.es/api/public/v1/locations?page=1",
        ):
            with self.subTest(url=url), self.assertRaises(transport.SourcePolicyBlocked):
                transport.classify_post_exception(_post(url, _mapareve_body()))

    def test_unreviewed_query_parameters_and_bounds_are_blocked(self) -> None:
        for url in (
            MAPAREVE_URL + "&action=delete",
            "https://www.mapareve.es/api/public/v1/locations?page=0",
            "https://www.mapareve.es/api/public/v1/locations?page=abc",
            "https://www.mapareve.es/api/public/v1/locations?page=1&page=2",
            "https://www.mapareve.es/api/public/v1/locations?page=1&per_page=500",
            "https://www.mapareve.es/api/public/v1/locations?page=9999",
        ):
            with self.subTest(url=url), self.assertRaises(transport.SourcePolicyBlocked):
                transport.classify_post_exception(_post(url, _mapareve_body()))

    def test_body_must_be_a_read_only_bbox_query(self) -> None:
        good = json.loads(_mapareve_body())
        mutated = [
            b"", b"not json", b"[]", b"null",
            json.dumps({**good, "delete": True}).encode(),                       # extra field
            json.dumps({**good, "password": "x"}).encode(),
            json.dumps({**good, "cpo_ids": ["x"]}).encode(),                      # must stay empty
            json.dumps({**good, "connector_types": ["CHADEMO_X"]}).encode(),      # unreviewed type
            json.dumps({**good, "available": "yes"}).encode(),                    # not boolean
            json.dumps({**good, "latitude_ne": "39"}).encode(),                   # not a number
            json.dumps({k: v for k, v in good.items() if k != "zoom"}).encode(),  # no zoom
            b"{" + b'"x":' + b"1" * 5000 + b"}",                                  # oversized
        ]
        for body in mutated:
            with self.subTest(body=body[:40]), self.assertRaises(transport.SourcePolicyBlocked):
                transport.classify_post_exception(_post(MAPAREVE_URL, body))

    def test_credentials_never_ride_a_post_exception(self) -> None:
        for header in ("Authorization", "X-Api-Key", "Cookie", "Proxy-Authorization"):
            with self.subTest(header=header), self.assertRaises(transport.SourcePolicyBlocked):
                transport.classify_post_exception(_post(MAPAREVE_URL, _mapareve_body(), **{header: "secret"}))


class FenieExceptionTests(unittest.TestCase):
    def test_exact_marker_list_post_is_approved(self) -> None:
        self.assertEqual(transport.classify_post_exception(_post(FENIE_URL, b"", **{"X-CSRF-TOKEN": "t"})), "fenie-markers-list")

    def test_fenie_wrong_path_body_query_origin_blocked(self) -> None:
        for url, body in (
            ("https://recarga.fenieenergia.es/punto-recarga/todos/", b""),
            ("https://recarga.fenieenergia.es/punto-recarga/0459", b""),
            ("https://recarga.fenieenergia.es/login", b""),
            ("https://recarga.fenieenergia.es/reservar", b"x=1"),
            ("https://recarga.fenieenergia.es/punto-recarga/todos?x=1", b""),
            (FENIE_URL, b"user=a&password=b"),
            ("http://recarga.fenieenergia.es/punto-recarga/todos", b""),
            ("https://fenieenergia.es/punto-recarga/todos", b""),
            ("https://recarga.fenieenergia.es.evil.example/punto-recarga/todos", b""),
        ):
            with self.subTest(url=url, body=body), self.assertRaises(transport.SourcePolicyBlocked):
                transport.classify_post_exception(_post(url, body))

    def test_mapareve_body_cannot_be_posted_to_fenie_and_vice_versa(self) -> None:
        with self.assertRaises(transport.SourcePolicyBlocked):
            transport.classify_post_exception(_post(FENIE_URL, _mapareve_body()))
        with self.assertRaises(transport.SourcePolicyBlocked):
            transport.classify_post_exception(_post(MAPAREVE_URL, b""))


class NonAllowlistedMethodsStillBlockedTests(unittest.TestCase):
    def test_other_post_and_methods_are_refused_before_a_socket_opens(self) -> None:
        with mock.patch("urllib.request.urlopen") as urlopen, mock.patch("urllib.request.build_opener") as builder:
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                for url in (
                    "https://example.invalid/anything",
                    "https://overpass-api.de/api/interpreter",
                    "https://api.openchargemap.io/v3/poi",
                    "https://www.electromaps.com/api",
                ):
                    with self.subTest(method=method, url=url):
                        req = urllib.request.Request(url, data=b"x=1", method=method)
                        with self.assertRaises(transport.SourcePolicyBlocked):
                            transport.policy_urlopen(req, timeout=5)
        urlopen.assert_not_called()
        builder.assert_not_called()

    def test_allowed_family_with_non_post_unsafe_method_is_blocked(self) -> None:
        for method in ("PUT", "DELETE", "PATCH"):
            for url in (MAPAREVE_URL, FENIE_URL):
                req = urllib.request.Request(url, data=_mapareve_body() if "mapareve" in url else b"", method=method)
                with self.subTest(method=method, url=url), self.assertRaises(transport.SourcePolicyBlocked):
                    transport.policy_urlopen(req, timeout=5)

    def test_ingestion_fetch_cannot_silently_post(self) -> None:
        with mock.patch("urllib.request.build_opener") as builder, mock.patch("urllib.request.urlopen") as urlopen:
            with self.assertRaises(ingest_transport.SourcePolicyBlocked):
                ing.fetch("https://example.invalid/query", data=b"q=1")
            with self.assertRaises(ingest_transport.SourcePolicyBlocked):
                ing.fetch("https://overpass-api.de/api/interpreter", data=b"data=[out:json];")
        builder.assert_not_called()
        urlopen.assert_not_called()

    def test_ingestion_fetch_without_body_is_an_explicit_get(self) -> None:
        with mock.patch.object(ing, "policy_urlopen") as opener:
            opener.return_value.__enter__.return_value.read.return_value = b"ok"
            self.assertEqual(ing.fetch("https://example.test/x"), b"ok")
        self.assertEqual(opener.call_args.args[0].get_method(), "GET")

    def test_overpass_uses_get_not_post(self) -> None:
        captured = {}

        def fake_cached_fetch(url, cache_name, ttl_hours=24, **kw):
            captured.update(url=url, kw=kw)
            return mock.sentinel.artifact

        with mock.patch.object(ing, "cached_fetch", side_effect=fake_cached_fetch):
            self.assertIs(ing.cached_overpass("[out:json];node(1);out;", "x.json"), mock.sentinel.artifact)
        self.assertTrue(captured["url"].startswith("https://overpass-api.de/api/interpreter?data="))
        self.assertNotIn("data", captured["kw"])  # no request body => GET


class BudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        transport.reset_budgets()
        self.addCleanup(transport.reset_budgets)

    def test_fenie_budget_is_tiny_and_enforced(self) -> None:
        limit = transport.POST_BUDGETS["fenie-markers-list"][1]
        for _ in range(limit):
            transport._charge_budget("fenie-markers-list", now=100.0)
        with self.assertRaises(transport.SourcePolicyBlocked):
            transport._charge_budget("fenie-markers-list", now=101.0)
        # a later window frees the budget again
        transport._charge_budget("fenie-markers-list", now=100.0 + transport.POST_BUDGETS["fenie-markers-list"][0] + 1)

    def test_timeout_must_be_bounded(self) -> None:
        for timeout in (0, -1, transport.MAX_POST_TIMEOUT_SECONDS + 1):
            with self.subTest(timeout=timeout), self.assertRaises(transport.SourcePolicyBlocked):
                transport.policy_urlopen(_post(FENIE_URL, b""), timeout=timeout)


class _RedirectingHandler(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self) -> None:  # noqa: N802
        type(self).seen.append(("POST", self.path))
        self.send_response(307)
        self.send_header("Location", "/elsewhere")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        type(self).seen.append(("GET", self.path))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"followed")

    def log_message(self, *args) -> None:  # silence
        pass


class RedirectTests(unittest.TestCase):
    def test_post_exception_opener_never_follows_redirects(self) -> None:
        _RedirectingHandler.seen = []
        httpd = HTTPServer(("127.0.0.1", 0), _RedirectingHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (httpd.shutdown(), httpd.server_close()))
        url = f"http://127.0.0.1:{httpd.server_address[1]}/api/public/v1/locations?page=1"
        opener = transport._build_post_opener(http.cookiejar.CookieJar())
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code):
                _RedirectingHandler.seen = []
                with mock.patch.object(_RedirectingHandler, "do_POST", self._make_post(code)):
                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        opener.open(_post(url, b"{}"), timeout=5)
                self.assertEqual(ctx.exception.code, code)
                self.assertEqual([m for m, _ in _RedirectingHandler.seen], ["POST"], "redirect target must never be requested")

    @staticmethod
    def _make_post(code: int):
        def do_post(handler) -> None:
            _RedirectingHandler.seen.append(("POST", handler.path))
            handler.send_response(code)
            handler.send_header("Location", "https://evil.example/steal")
            handler.end_headers()
        return do_post

    def test_redirect_cannot_activate_provider_writes_via_policy_urlopen(self) -> None:
        """Right origin + redirect: the real policy opener raises and the caller records nothing."""
        redirect = urllib.error.HTTPError(MAPAREVE_URL, 302, "Found", {"Location": "https://www.mapareve.es/other"}, None)
        opener = mock.MagicMock()
        opener.open.side_effect = redirect
        transport.reset_budgets()
        with mock.patch.object(transport, "_build_post_opener", return_value=opener):
            with self.assertRaises(urllib.error.HTTPError):
                transport.policy_urlopen(_post(MAPAREVE_URL, _mapareve_body()), timeout=5)
        transport.reset_budgets()
        # fetch_mapareve surfaces the failure and writes no CACHE / provider state.
        original = dict(server.CACHE)
        with mock.patch("server.policy_urlopen", side_effect=redirect):
            with self.assertRaises(urllib.error.HTTPError):
                server.fetch_mapareve(max_price=5.0)
        self.assertEqual(dict(server.CACHE), original)


class _FakeResponse:
    def __init__(self, body: bytes, url: str = "https://recarga.fenieenergia.es/") -> None:
        self._body, self._url = body, url

    def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


MARKER = {
    "nombre_punto": "Central San Pedro", "direccion": "Calle 7, Araia, Álava", "id": 682, "code": "0459",
    "tarifa": 0, "conectores": [{"type": "Mennekes", "count": 2, "power": "18.0"}],
    "gratuito": 1, "estado": 1, "lat": 42.888151, "lon": -2.314698,
}


class FenieCollectorTests(unittest.TestCase):
    def _flow(self, page: bytes, markers: bytes, page_url: str = "https://recarga.fenieenergia.es/"):
        calls = []

        def fake_policy_urlopen(request, *, timeout, cookiejar=None):
            calls.append((request.get_method(), request.full_url, dict(request.header_items()), request.data))
            if request.get_method() == "GET":
                return _FakeResponse(page, page_url)
            return _FakeResponse(markers)

        return calls, fake_policy_urlopen

    def test_fetch_is_one_get_then_one_exact_post_with_csrf(self) -> None:
        page = b'<meta name="csrf-token" content="TOK123">'
        calls, fake = self._flow(page, b"[1]")
        with mock.patch.object(ing, "policy_urlopen", side_effect=fake):
            self.assertEqual(ing._fenie_fetch_markers_bytes(), b"[1]")
        self.assertEqual([c[0] for c in calls], ["GET", "POST"])
        self.assertEqual(calls[1][1], FENIE_URL)
        self.assertEqual(calls[1][3], b"")
        self.assertEqual(calls[1][2].get("X-csrf-token"), "TOK123")
        # The request the collector builds is exactly what the real policy approves.
        self.assertEqual(
            ingest_transport.classify_post_exception(_post(calls[1][1], calls[1][3], **{"X-CSRF-TOKEN": "TOK123"})),
            "fenie-markers-list",
        )
        self.assertNotIn("Cookie", calls[1][2])  # cookies come from the jar, never a manual header

    def test_missing_csrf_or_off_origin_page_makes_no_post(self) -> None:
        calls, fake = self._flow(b"<html>Just a moment... challenge</html>", b"[]")
        with mock.patch.object(ing, "policy_urlopen", side_effect=fake):
            with self.assertRaises(RuntimeError):
                ing._fenie_fetch_markers_bytes()
        self.assertEqual([c[0] for c in calls], ["GET"])
        calls, fake = self._flow(b'<meta name="csrf-token" content="T">', b"[]", page_url="https://evil.example/")
        with mock.patch.object(ing, "policy_urlopen", side_effect=fake):
            with self.assertRaises(ingest_transport.SourcePolicyBlocked):
                ing._fenie_fetch_markers_bytes()
        self.assertEqual([c[0] for c in calls], ["GET"])

    def test_source_fenie_ingests_fresh_snapshot_then_labels_stale_fallback(self) -> None:
        markers = [dict(MARKER, id=i, code=f"{i:04d}") for i in range(120)]
        body = json.dumps(markers).encode()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ing, "CACHE", Path(tmp)):
            records: list = []
            with mock.patch.object(ing, "_fenie_fetch_markers_bytes", return_value=body):
                ing.source_fenie(records)
            self.assertEqual(len(records), 120)
            self.assertTrue(all(r["is_free"] and r["reliability"] == "official-free" for r in records))
            self.assertTrue((Path(tmp) / "fenie_markers.json").exists())

            # Challenge/empty response must not poison a two-day-old cache.
            cached = Path(tmp) / "fenie_markers.json"
            old_time = ing.time.time() - 2 * 24 * 3600
            ing.os.utime(cached, (old_time, old_time))
            records2: list = []
            with mock.patch.object(ing, "_fenie_fetch_markers_bytes", return_value=b"<html>challenge</html>"):
                ing.source_fenie(records2)
            self.assertEqual(len(records2), 120)
            self.assertEqual(cached.read_bytes(), body)
            self.assertTrue(ing.FETCH_EVIDENCE[-1]["cache"]["fallback"])
            self.assertTrue(ing.FETCH_EVIDENCE[-1]["cache"]["stale"])
            self.assertEqual(records2[0]["source_fetched_at"], ing.epoch_iso(old_time))

    def test_fenie_cache_is_reused_within_one_day_without_network(self) -> None:
        body = json.dumps([dict(MARKER, id=i) for i in range(120)]).encode()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ing, "CACHE", Path(tmp)):
            (Path(tmp) / "fenie_markers.json").write_bytes(body)
            records = []
            with mock.patch.object(ing, "_fenie_fetch_markers_bytes") as fetch:
                ing.source_fenie(records)
            fetch.assert_not_called()
            self.assertEqual(len(records), 120)

    def test_expired_fenie_fallback_fails_instead_of_refreshing_timestamp(self) -> None:
        body = json.dumps([dict(MARKER, id=i) for i in range(120)]).encode()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ing, "CACHE", Path(tmp)):
            path = Path(tmp) / "fenie_markers.json"
            path.write_bytes(body)
            old_time = ing.time.time() - 8 * 24 * 3600
            ing.os.utime(path, (old_time, old_time))
            records = []
            with mock.patch.object(ing, "_fenie_fetch_markers_bytes", side_effect=OSError("offline")):
                with self.assertRaisesRegex(RuntimeError, "seven-day"):
                    ing.source_fenie(records)
            self.assertEqual(records, [])
            self.assertEqual(path.read_bytes(), body)

    def test_source_fenie_fails_closed_without_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ing, "CACHE", Path(tmp)):
            with mock.patch.object(ing, "_fenie_fetch_markers_bytes", side_effect=OSError("down")):
                with self.assertRaises(OSError):
                    ing.source_fenie([])

    def test_shrunken_population_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ing, "CACHE", Path(tmp)):
            with mock.patch.object(ing, "_fenie_fetch_markers_bytes", return_value=json.dumps([MARKER]).encode()):
                with self.assertRaises(RuntimeError):
                    ing.source_fenie([])
            self.assertFalse((Path(tmp) / "fenie_markers.json").exists())

    def test_fenie_rows_pass_policy_but_cached_nonlive(self) -> None:
        rows = ing.fenie_records_from_markers([MARKER])
        self.assertEqual(len(rows), 1)
        self.assertTrue(server.source_policy_record_allowed(rows[0]))
        self.assertEqual(rows[0]["status"], "VERIFY")  # never live


class MapareveIngestionTests(unittest.TestCase):
    def test_source_mapareve_public_sends_only_reviewed_queries(self) -> None:
        sent = []

        def fake_cached_fetch(url, cache_name, ttl_hours=24, **kw):
            req = urllib.request.Request(url, data=kw.get("data"), headers=kw.get("headers") or {},
                                         method="POST" if kw.get("data") is not None else "GET")
            sent.append(req)
            raise RuntimeError("stop after first request")

        with mock.patch.object(ing, "cached_fetch", side_effect=fake_cached_fetch), mock.patch.object(ing.time, "sleep"):
            with self.assertRaises(RuntimeError):
                ing.source_mapareve_public([])
        self.assertTrue(sent)
        for request in sent:
            self.assertEqual(ingest_transport.classify_post_exception(request), "mapareve-locations-query")


if __name__ == "__main__":
    unittest.main()
