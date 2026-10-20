"""Vehicle consent, isolation, freshness and HTTP contracts. No external network."""
from __future__ import annotations

import base64
import hashlib
import http.client
import io
import json
import os
import ssl
import sys
import threading
import time
import unittest
import urllib.parse
import urllib.request
from contextlib import redirect_stdout
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import vehicles


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 19, 12, tzinfo=timezone.utc).timestamp()

    def __call__(self):
        return self.now


class FakeSmartcar:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []
        self.errors = {}
        self.overrides = {}
        self.observed = {}
        self.ids = ["owned-ev"]
        self.unit_system = "metric"

    def token(self, grant, value, deadline):
        self.calls.append(("POST", grant))
        if grant in self.errors:
            raise self.errors[grant]
        return {"access_token": "fixture-access", "refresh_token": "fixture-refresh", "expires_in": 3600, "token_type": "Bearer"}

    def get(self, path, access_token, deadline):
        self.calls.append(("GET", path))
        if path in self.errors:
            raise self.errors[path]
        if path in self.overrides:
            payload = self.overrides[path]
        elif "?limit=" in path:
            payload = {"vehicles": self.ids, "paging": {"count": len(self.ids)}}
        elif path.endswith("/battery/capacity"):
            payload = {"capacity": 77.4}
        elif path.endswith("/battery"):
            payload = {"percentRemaining": 0.57, "range": 268}
        elif path.endswith("/charge"):
            payload = {"state": "NOT_CHARGING", "isPluggedIn": False}
        else:
            payload = {"id": path.rsplit("/", 1)[-1], "make": "HYUNDAI", "model": "IONIQ 5", "year": 2024}
        observed = self.observed.get(path, vehicles._iso(self.clock()))
        return payload, {"sc-data-age": observed, "sc-unit-system": self.unit_system}


def config(origin="https://volta.example", mode="live"):
    return vehicles.Config(origin, "fixture-client", "fixture-client-secret", mode)


def cookie_value(header):
    return header.split(";", 1)[0]


class VehicleConnectionTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.provider = FakeSmartcar(self.clock)
        self.store = vehicles.VehicleConnections(config(), client=self.provider, clock=self.clock)

    def begin(self, store=None):
        store = store or self.store
        response, cookie = store.connect(None, store.config.app_origin)
        parsed = urllib.parse.urlsplit(response["authorization_url"])
        return cookie_value(cookie), urllib.parse.parse_qs(parsed.query), parsed

    def authorize(self, store=None):
        store = store or self.store
        original_cookie, query, _ = self.begin(store)
        landing, new_cookie = store.callback(original_cookie, {"state": query["state"], "code": ["fixture-code"]})
        self.assertTrue(landing.endswith("vehicle_connection=connected"))
        self.assertIsNotNone(new_cookie)
        return cookie_value(new_cookie), original_cookie

    def test_unconfigured_is_honest_and_does_not_create_a_session(self):
        store = vehicles.VehicleConnections(vehicles.Config(), client=self.provider)
        response = store.status()
        self.assertEqual(response["connectionStatus"], "unavailable")
        self.assertFalse(response["configured"])
        self.assertFalse(response["connected"])
        self.assertIsNone(response["mode"])
        self.assertEqual(response["vehicles"], [])
        with self.assertRaises(vehicles.VehicleError) as raised:
            store.connect(None, "https://volta.example")
        self.assertEqual(raised.exception.status, 503)
        self.assertEqual(store._sessions, {})
        self.assertEqual(self.provider.calls, [])

    def test_origin_configuration_is_https_or_literal_loopback_only(self):
        for origin in ("https://volta.example", "http://localhost:5183", "http://127.0.0.1:8773", "http://[::1]:8773"):
            self.assertTrue(config(origin).configured, origin)
        for origin in ("http://volta.example", "https://user@volta.example", "https://volta.example/callback", "https://volta.example?x=y", "//volta.example", "https://volta.example\\@evil.example", "https://volta.example\n"):
            self.assertFalse(config(origin).configured, origin)
        self.assertFalse(config(mode="unrecognized").configured)

    def test_connect_only_requests_read_scopes_and_pins_redirect_and_provider(self):
        cookie, query, parsed = self.begin()
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "connect.smartcar.com")
        self.assertEqual(query["redirect_uri"], ["https://volta.example/api/vehicles/callback"])
        self.assertEqual(set(query["scope"][0].split()), {"read_vehicle_info", "read_battery", "read_charge"})
        self.assertNotIn("fixture-client-secret", parsed.query)
        self.assertNotIn("fixture-access", parsed.query)
        self.assertFalse(self.store.status(cookie)["connected"])
        self.assertEqual(self.provider.calls, [])

    def test_callback_state_is_bound_to_browser_single_use_and_expires(self):
        cookie, query, _ = self.begin()
        other_cookie, _, _ = self.begin()
        for supplied_cookie, state in ((None, query["state"]), (other_cookie, query["state"]), (cookie, ["wrong"])):
            landing, replacement = self.store.callback(supplied_cookie, {"state": state, "code": ["fixture-code"]})
            self.assertTrue(landing.endswith("=error"))
            self.assertIsNone(replacement)
        self.assertEqual(self.provider.calls, [])
        self.clock.now += vehicles.STATE_SECONDS
        self.assertTrue(self.store.callback(cookie, {"state": query["state"], "code": ["fixture-code"]})[0].endswith("=error"))
        self.assertEqual(self.provider.calls, [])
        fresh_cookie, fresh_query, _ = self.begin()
        self.store.callback(fresh_cookie, {"state": fresh_query["state"], "error": ["access_denied"]})
        self.store.callback(fresh_cookie, {"state": fresh_query["state"], "code": ["fixture-code"]})
        self.assertEqual(self.provider.calls, [])

    def test_callback_rotates_session_and_keeps_all_credentials_server_side(self):
        cookie, old_cookie = self.authorize()
        self.assertNotEqual(cookie, old_cookie)
        self.assertFalse(self.store.status(old_cookie)["connected"])
        response = self.store.status(cookie)
        self.assertTrue(response["connected"])
        self.assertEqual(response["connectionStatus"], "connected")
        for secret in ("fixture-access", "fixture-refresh", "fixture-client-secret", "fixture-code", "access_token", "refresh_token"):
            self.assertNotIn(secret, json.dumps(response))
        # Replaying the old code and state cannot replace the rotated session.
        self.assertTrue(self.store.callback(old_cookie, {"state": ["replay"], "code": ["fixture-code"]})[0].endswith("=error"))
        self.assertEqual(sum(call == ("POST", "authorization_code") for call in self.provider.calls), 1)

    def test_readings_normalize_metric_units_and_never_invent_capabilities(self):
        cookie, _ = self.authorize()
        row = self.store.status(cookie)["vehicles"][0]
        self.assertEqual((row["make"], row["model"], row["year"]), ("HYUNDAI", "IONIQ 5", 2024))
        self.assertEqual(row["batteryPercent"], 57)
        self.assertEqual(row["rangeKm"], 268)
        self.assertEqual(row["batteryCapacityKwh"], 77.4)
        self.assertEqual(row["usableEnergyKwh"], 44.12)
        self.assertTrue(row["usableEnergyEstimated"])
        self.assertEqual(row["chargingState"], "unplugged")
        self.assertFalse(row["isStale"])
        for field in ("chargePowerKw", "maxAcKw", "maxDcKw", "connectors"):
            self.assertIsNone(row[field])
        self.assertNotIn("imageUrl", row)
        self.assertEqual(row["source"], {"provider": "Smartcar", "mode": "live"})
        self.assertEqual(row["fieldObservedAt"]["batteryPercent"], vehicles._iso(self.clock()))

    def test_unknown_capacity_and_mismatched_times_never_produce_energy(self):
        self.provider.errors["/v2.0/vehicles/owned-ev/battery/capacity"] = vehicles.ProviderError(403)
        cookie, _ = self.authorize()
        row = self.store.status(cookie)["vehicles"][0]
        self.assertIsNone(row["batteryCapacityKwh"])
        self.assertIsNone(row["usableEnergyKwh"])
        self.assertIsNone(row["error"])
        del self.provider.errors["/v2.0/vehicles/owned-ev/battery/capacity"]
        self.clock.now += vehicles.REFRESH_SECONDS
        self.provider.observed["/v2.0/vehicles/owned-ev/battery/capacity"] = vehicles._iso(self.clock() - 1000)
        row = self.store.refresh(cookie, self.store.config.app_origin)["vehicles"][0]
        self.assertEqual(row["batteryCapacityKwh"], 77.4)
        self.assertIsNone(row["usableEnergyKwh"])
        self.assertFalse(row["usableEnergyEstimated"])

    def test_missing_or_stale_observation_is_not_made_fresh_by_http_success(self):
        old_observation = vehicles._iso(self.clock() - 900)
        self.provider.observed["/v2.0/vehicles/owned-ev/battery"] = old_observation
        cookie, _ = self.authorize()
        row = self.store.status(cookie)["vehicles"][0]
        self.assertTrue(row["isStale"])
        self.assertEqual(row["observedAt"], old_observation)
        self.assertEqual(row["fetchedAt"], vehicles._iso(self.clock()))
        self.assertIsNone(row["usableEnergyKwh"])
        self.clock.now += vehicles.REFRESH_SECONDS
        self.provider.observed["/v2.0/vehicles/owned-ev/battery"] = "not-a-timestamp"
        row = self.store.refresh(cookie, self.store.config.app_origin)["vehicles"][0]
        self.assertTrue(row["isStale"])
        self.assertIsNone(row["observedAt"])
        self.assertIsNone(row["fieldFreshUntil"]["batteryPercent"])

    def test_status_ages_cached_data_without_any_network_request(self):
        cookie, _ = self.authorize()
        count = len(self.provider.calls)
        self.clock.now += vehicles.FRESH_SECONDS + 1
        row = self.store.status(cookie)["vehicles"][0]
        self.assertTrue(row["isStale"])
        self.assertIsNone(row["usableEnergyKwh"])
        self.assertEqual(len(self.provider.calls), count)

    def test_simulated_provider_data_is_always_labeled_simulation(self):
        for mode in ("test", "simulated"):
            store = vehicles.VehicleConnections(config(mode=mode), client=self.provider, clock=self.clock)
            cookie, _ = self.authorize(store)
            response = store.status(cookie)
            self.assertEqual(response["mode"], "simulation")
            self.assertEqual(response["vehicles"][0]["source"]["mode"], "simulation")

    def test_refresh_throttle_and_vehicle_authorization_prevent_arbitrary_queries(self):
        cookie, _ = self.authorize()
        count = len(self.provider.calls)
        self.store.refresh(cookie, self.store.config.app_origin)
        self.assertEqual(len(self.provider.calls), count)
        for value in ("unowned-ev", "../battery", ["owned-ev"]):
            with self.assertRaises(vehicles.VehicleError) as raised:
                self.store.refresh(cookie, self.store.config.app_origin, value)
            self.assertEqual(raised.exception.status, 404)
        self.clock.now += vehicles.REFRESH_SECONDS
        self.store.refresh(cookie, self.store.config.app_origin, "owned-ev")
        self.assertEqual(len(self.provider.calls), count + 3)

    def test_expired_access_token_refreshes_once_and_revocation_requires_reconnect(self):
        cookie, _ = self.authorize()
        self.clock.now += 3600
        self.store.refresh(cookie, self.store.config.app_origin)
        self.assertIn(("POST", "refresh_token"), self.provider.calls)
        self.provider.errors["/v2.0/vehicles/owned-ev/battery"] = vehicles.ProviderError(401)
        self.clock.now += vehicles.REFRESH_SECONDS
        response = self.store.refresh(cookie, self.store.config.app_origin)
        self.assertFalse(response["connected"])
        self.assertEqual(response["connectionStatus"], "reconnect")
        self.assertTrue(response["vehicles"][0]["isStale"])

    def test_provider_failure_preserves_observation_and_marks_cached_data_stale(self):
        cookie, _ = self.authorize()
        original = self.store.status(cookie)["vehicles"][0]
        self.provider.errors["/v2.0/vehicles/owned-ev/battery"] = vehicles.ProviderError(503)
        self.clock.now += vehicles.REFRESH_SECONDS
        row = self.store.refresh(cookie, self.store.config.app_origin)["vehicles"][0]
        self.assertTrue(row["isStale"])
        self.assertEqual(row["batteryPercent"], original["batteryPercent"])
        self.assertEqual(row["fieldObservedAt"]["batteryPercent"], original["fieldObservedAt"]["batteryPercent"])
        self.assertEqual(row["error"], "telemetry_unavailable")

    def test_mutations_reject_cross_origin_missing_origin_and_logout_erases_data(self):
        cookie, _ = self.authorize()
        for origin in (None, "null", "https://evil.example", "https://volta.example.evil.example"):
            for action in (lambda: self.store.connect(cookie, origin), lambda: self.store.refresh(cookie, origin), lambda: self.store.disconnect(cookie, origin)):
                with self.assertRaises(vehicles.VehicleError) as raised:
                    action()
                self.assertEqual(raised.exception.status, 403)
        response, cleared = self.store.disconnect(cookie, self.store.config.app_origin)
        self.assertFalse(response["connected"])
        self.assertEqual(response["vehicles"], [])
        self.assertFalse(response["providerRevoked"])
        self.assertIn("Max-Age=0", cleared)
        self.assertEqual(self.store._sessions, {})
        self.assertEqual(self.store.status(cookie)["vehicles"], [])

    def test_cookie_security_and_absolute_session_expiry(self):
        response, secure_cookie = self.store.connect(None, self.store.config.app_origin)
        for attribute in ("HttpOnly", "SameSite=Lax", "Secure", "Path=/"):
            self.assertIn(attribute, secure_cookie)
        self.assertNotIn("Domain=", secure_cookie)
        local = vehicles.VehicleConnections(config("http://127.0.0.1:8773"), client=self.provider, clock=self.clock)
        _, local_cookie = local.connect(None, local.config.app_origin)
        self.assertNotIn("Secure", local_cookie)
        cookie, _ = self.authorize()
        self.clock.now += vehicles.SESSION_SECONDS
        self.assertFalse(self.store.status(cookie)["connected"])
        self.assertEqual(self.store.status(cookie)["vehicles"], [])

    def native_authorize(self, verifier="a" * 43):
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        location, cookie = self.store.native_start({"challenge": [challenge]})
        state = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)["state"]
        landing, cleared = self.store.callback(cookie_value(cookie), {"state": state, "code": ["fixture-code"]})
        self.assertTrue(landing.startswith(vehicles.NATIVE_CALLBACK + "?code="))
        self.assertIn("Max-Age=0", cleared)
        code = urllib.parse.parse_qs(urllib.parse.urlsplit(landing).query)["code"][0]
        return code, verifier, cookie_value(cookie)

    def test_native_handoff_requires_matching_proof_and_is_one_use(self):
        code, verifier, browser_cookie = self.native_authorize()
        self.assertFalse(self.store.status(browser_cookie)["connected"])
        with self.assertRaises(vehicles.VehicleError):
            self.store.native_exchange(self.store.config.app_origin, code, "b" * 43)
        response, cookie = self.store.native_exchange(self.store.config.app_origin, code, verifier)
        self.assertTrue(response["connected"])
        self.assertTrue(self.store.status(cookie_value(cookie))["connected"])
        self.assertIn("HttpOnly", cookie)
        for secret in ("fixture-access", "fixture-refresh", cookie_value(cookie).split("=", 1)[1]):
            self.assertNotIn(secret, code)
        with self.assertRaises(vehicles.VehicleError):
            self.store.native_exchange(self.store.config.app_origin, code, verifier)

    def test_native_handoff_expires_and_cannot_use_another_native_verifier(self):
        first, first_verifier, _ = self.native_authorize("a" * 43)
        second, second_verifier, _ = self.native_authorize("b" * 43)
        with self.assertRaises(vehicles.VehicleError):
            self.store.native_exchange(self.store.config.app_origin, first, second_verifier)
        with self.assertRaises(vehicles.VehicleError):
            self.store.native_exchange("https://evil.example", second, second_verifier)
        self.clock.now += vehicles.HANDOFF_SECONDS
        with self.assertRaises(vehicles.VehicleError):
            self.store.native_exchange(self.store.config.app_origin, first, first_verifier)
        self.assertEqual(self.store._sessions, {})

    def test_native_start_rejects_weak_challenges_duplicate_values_and_return_urls(self):
        for params in ({}, {"challenge": ["weak"]}, {"challenge": ["a" * 43, "b" * 43]},
                       {"challenge": ["a" * 43], "return_url": ["evil://callback"]}):
            with self.assertRaises(vehicles.VehicleError) as raised:
                self.store.native_start(params)
            self.assertEqual(raised.exception.status, 400)
        self.assertEqual(self.store._sessions, {})

    def test_native_provider_denial_returns_only_fixed_app_callback(self):
        location, cookie = self.store.native_start({"challenge": ["a" * 43]})
        state = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)["state"]
        landing, _ = self.store.callback(cookie_value(cookie), {"state": state, "error": ["access_denied"]})
        self.assertEqual(landing, vehicles.NATIVE_CALLBACK + "?error=error")

    def test_session_limit_fails_closed_and_malformed_cookies_never_create_state(self):
        with patch.object(vehicles, "MAX_SESSIONS", 1):
            self.begin()
            with self.assertRaises(vehicles.VehicleError) as raised:
                self.begin()
            self.assertEqual(raised.exception.status, 503)
        for cookie in ("not a cookie", "volta_vehicle_session=../etc", "x" * 4097):
            self.assertFalse(self.store.status(cookie)["connected"])
        self.assertEqual(len(self.store._sessions), 1)


class TransportTests(unittest.TestCase):
    def test_transport_refuses_non_allowlisted_routes_before_socket(self):
        client = vehicles.SmartcarClient(config())
        with patch.object(client._opener, "open") as connection:
            for method, path in (("GET", "https://evil.example"), ("GET", "/v2.0/vehicles/../battery"),
                                 ("GET", "/v2.0/vehicles/id/location"), ("POST", "/v2.0/vehicles/id/charge"),
                                 ("DELETE", "/v2.0/vehicles/id/application")):
                with self.assertRaises(vehicles.ProviderError):
                    client._request(method, path, time.monotonic() + 5, access_token="fixture-access")
            self.assertFalse(connection.called)

    def test_transport_uses_bounded_https_metric_and_does_not_follow_redirects(self):
        client = vehicles.SmartcarClient(config())
        response = Mock(status=302)
        with patch.object(client._opener, "open", return_value=response) as opener:
            with self.assertRaises(vehicles.ProviderError):
                client.get("/v2.0/vehicles/owned-ev/battery", "fixture-access", time.monotonic() + 20)
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.smartcar.com/v2.0/vehicles/owned-ev/battery")
        self.assertLessEqual(opener.call_args.kwargs["timeout"], vehicles.REQUEST_SECONDS)
        self.assertEqual(request.get_header("Sc-unit-system"), "metric")
        self.assertEqual(opener.call_count, 1)
        response.close.assert_called_once()
        self.assertIsNone(vehicles._NoRedirect().redirect_request(request, None, 302, "Found", {}, "https://evil.example"))

    def test_transport_preserves_inherited_proxy_and_verified_tls_defaults(self):
        with patch.dict(os.environ, {"https_proxy": "http://runtime-proxy.example:8080"}):
            client = vehicles.SmartcarClient(config())
        proxy = next(handler for handler in client._opener.handlers if isinstance(handler, urllib.request.ProxyHandler))
        tls = next(handler for handler in client._opener.handlers if isinstance(handler, urllib.request.HTTPSHandler))
        self.assertEqual(proxy.proxies["https"], "http://runtime-proxy.example:8080")
        if tls._context is not None:
            self.assertEqual(tls._context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(tls._context.check_hostname)
        self.assertTrue(any(isinstance(handler, vehicles._NoRedirect) for handler in client._opener.handlers))

    def test_transport_bounds_json_response_and_token_forms(self):
        client = vehicles.SmartcarClient(config())
        response = Mock(status=200)
        response.headers = {"Content-Type": "application/json", "Content-Length": str(vehicles.MAX_RESPONSE_BYTES + 1)}
        with patch.object(client._opener, "open", return_value=response) as opener:
            with self.assertRaises(vehicles.ProviderError):
                client.token("authorization_code", "fixture-code", time.monotonic() + 20)
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, "https://auth.smartcar.com/oauth/token")
        self.assertEqual(request.get_method(), "POST")
        form = urllib.parse.parse_qs(request.data.decode())
        self.assertEqual(form["redirect_uri"], ["https://volta.example/api/vehicles/callback"])
        self.assertFalse(response.read1.called)

    def test_transport_decodes_json_and_caps_streams_without_a_content_length(self):
        client = vehicles.SmartcarClient(config())
        response = Mock(status=200)
        response.headers = {"Content-Type": "application/json", "sc-data-age": "2026-10-19T12:00:00Z"}
        response.isclosed.return_value = False
        response.read1.side_effect = [b'{"percentRemaining":0.57,"range":268}', b""]
        with patch.object(client._opener, "open", return_value=response):
            payload, headers = client.get("/v2.0/vehicles/owned-ev/battery", "fixture-access", time.monotonic() + 5)
        self.assertEqual(payload["range"], 268)
        self.assertEqual(headers["sc-data-age"], "2026-10-19T12:00:00Z")
        response.read1.side_effect = [b"x" * 32768] * 9
        with patch.object(client._opener, "open", return_value=response):
            with self.assertRaises(vehicles.ProviderError) as raised:
                client.get("/v2.0/vehicles/owned-ev/battery", "fixture-access", time.monotonic() + 5)
        self.assertEqual(raised.exception.code, "invalid_provider_data")


class VehicleHttpTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.provider = FakeSmartcar(self.clock)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.store = vehicles.VehicleConnections(config(self.origin), client=self.provider, clock=self.clock)
        self.patch = patch.object(server, "VEHICLES", self.store)
        self.patch.start()
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)
        self.patch.stop()

    def request(self, method, path, body=None, *, origin=None, cookie=None, content_type="application/json"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        if origin is not None:
            headers["Origin"] = origin
        if cookie:
            headers["Cookie"] = cookie
        if body is not None:
            headers["Content-Type"] = content_type
        try:
            connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
            response = connection.getresponse()
            data = response.read()
            return response.status, dict(response.getheaders()), json.loads(data) if data else None
        finally:
            connection.close()

    def test_status_is_private_no_store_and_does_not_issue_an_anonymous_cookie(self):
        status, headers, body = self.request("GET", "/api/vehicles")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "private, no-store")
        self.assertEqual(headers["Access-Control-Allow-Origin"], self.origin)
        self.assertIn("Cookie", headers["Vary"])
        self.assertNotIn("Set-Cookie", headers)
        self.assertEqual(body["connectionStatus"], "disconnected")

    def test_full_browser_flow_returns_fixed_callback_and_keeps_secrets_out_of_logs(self):
        status, headers, body = self.request("POST", "/api/vehicles/connect", {}, origin=self.origin)
        self.assertEqual(status, 200)
        cookie = cookie_value(headers["Set-Cookie"])
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(body["authorization_url"]).query)
        callback = "/api/vehicles/callback?" + urllib.parse.urlencode({"state": query["state"][0], "code": "private-fixture-code"})
        captured = io.StringIO()
        with redirect_stdout(captured):
            status, headers, body = self.request("GET", callback, cookie=cookie)
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], self.origin + "/?vehicle_connection=connected")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(headers["Cache-Control"], "private, no-store")
        self.assertNotIn("private-fixture-code", captured.getvalue())
        self.assertNotIn(query["state"][0], captured.getvalue())
        new_cookie = cookie_value(headers["Set-Cookie"])
        status, _, body = self.request("GET", "/api/vehicles", cookie=new_cookie)
        self.assertEqual(status, 200)
        self.assertTrue(body["connected"])
        self.assertNotIn("fixture-access", json.dumps(body))
        status, headers, body = self.request("POST", "/api/vehicles/disconnect", {}, origin=self.origin, cookie=new_cookie)
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertFalse(body["connected"])

    def test_cross_origin_posts_and_custom_redirect_fields_are_rejected(self):
        for origin in (None, "https://evil.example"):
            status, _, _ = self.request("POST", "/api/vehicles/connect", {}, origin=origin)
            self.assertEqual(status, 403)
        status, _, _ = self.request("POST", "/api/vehicles/connect", {"redirect_uri": "https://evil.example"}, origin=self.origin)
        self.assertEqual(status, 400)
        status, _, _ = self.request("POST", "/api/vehicles/connect", {}, origin=self.origin, content_type="text/plain")
        self.assertEqual(status, 415)
        self.assertEqual(self.store._sessions, {})
        self.assertEqual(self.provider.calls, [])

    def test_native_http_routes_return_proof_bound_cookie_without_oauth_tokens(self):
        verifier = "a" * 43
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        status, headers, _ = self.request("GET", "/api/vehicles/native/start?challenge=" + challenge)
        self.assertEqual(status, 303)
        self.assertTrue(headers["Location"].startswith("https://connect.smartcar.com/oauth/authorize?"))
        state = urllib.parse.parse_qs(urllib.parse.urlsplit(headers["Location"]).query)["state"][0]
        browser_cookie = cookie_value(headers["Set-Cookie"])
        callback = "/api/vehicles/callback?" + urllib.parse.urlencode({"state": state, "code": "fixture-code"})
        status, headers, _ = self.request("GET", callback, cookie=browser_cookie)
        self.assertEqual(status, 303)
        self.assertTrue(headers["Location"].startswith(vehicles.NATIVE_CALLBACK + "?code="))
        ticket = urllib.parse.parse_qs(urllib.parse.urlsplit(headers["Location"]).query)["code"][0]
        status, headers, body = self.request("POST", "/api/vehicles/native/exchange", {"code": ticket, "verifier": verifier}, origin=self.origin)
        self.assertEqual(status, 200)
        self.assertTrue(body["connected"])
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertEqual(headers["Cache-Control"], "private, no-store")
        for value in ("fixture-access", "fixture-refresh", "fixture-client-secret"):
            self.assertNotIn(value, json.dumps(body))
        status, _, _ = self.request("POST", "/api/vehicles/native/exchange", {"code": ticket, "verifier": verifier}, origin=self.origin)
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
