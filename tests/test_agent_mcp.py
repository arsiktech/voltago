#!/usr/bin/env python3
"""Contract and honesty tests for the read-only agent MCP endpoint (POST /mcp).

Stdlib only. A real VoltaHTTPServer runs on an ephemeral port; every upstream
source is patched, so nothing here touches the network.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import agent_mcp  # noqa: E402
import community  # noqa: E402
import server  # noqa: E402

MODERN = "2026-07-28"
LEGACY = "2025-06-18"
ORIGIN_LAT, ORIGIN_LON = 39.47, -0.37


def live_row(station_id: str, *, lat: float, lon: float, available: int = 1, price: float = 0.0,
             labels: Optional[List[str]] = None, power: float = 50.0, last_seen: Optional[str] = None) -> Dict[str, Any]:
    labels = labels if labels is not None else ["CCS2"]
    return {
        "id": station_id,
        "name": station_id.replace("-", " ").title(),
        "lat": lat,
        "lon": lon,
        "available": available,
        "status": "AVAILABLE" if available else "UNKNOWN",
        "is_free": price == 0.0,
        "is_cheap": price <= 0.25,
        "price_min": price,
        "tesla_ok": True,
        "max_power_kw": power,
        "connector_labels": labels,
        "connectors": [{"label": label, "power_kw": power, "status": "AVAILABLE" if available else "UNKNOWN"} for label in labels],
        "parking": {"kind": "street_or_municipal", "paid": False},
        "source": "Test live source",
        "source_url": f"https://example.com/{station_id}",
        "sources": [{"name": "Test live source", "url": f"https://example.com/{station_id}"}],
        "reliability": "official-live",
        "last_seen": last_seen or server.now_iso(),
    }


def lead_row(station_id: str, *, lat: float, lon: float, price: Optional[float], labels: Optional[List[str]] = None,
             reliability: str = "official-free-policy") -> Dict[str, Any]:
    raw = {
        "id": station_id,
        "name": station_id.replace("-", " ").title(),
        "lat": lat,
        "lon": lon,
        "price_min": price,
        "is_free": price == 0.0 if price is not None else False,
        "is_cheap": price is not None and price <= 0.25,
        "connector_labels": labels or [],
        "connectors": [{"label": label} for label in labels or []],
        "source": "Municipal open data",
        "source_url": f"https://example.org/{station_id}",
        "sources": [{"name": "Municipal open data", "url": f"https://example.org/{station_id}"}],
        "reliability": reliability,
        "parking": {"kind": "street_or_municipal", "paid": False},
    }
    return server._normalize_lead(deepcopy(raw), "test lead")


def empty_live_payload() -> Dict[str, Any]:
    return {"stations": [], "raw_count": 0, "cache": {}, "errors": []}



def setUpModule():
    # Existing suites isolate their older feeds; keep the new runtime feeds offline too.
    live_patch = patch("server.fetch_public_live", return_value={"stations": [], "feeds": {}})
    live_patch.start()
    unittest.addModuleCleanup(live_patch.stop)


class AgentMcpHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="volta-agent-mcp-"))
        community.configure(db_path=cls.tmp / "community.db", photos_dir=cls.tmp / "photos")
        cls.httpd = server.VoltaHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)
        community.configure(cls.tmp / "gone.db", cls.tmp / "gone")

    def setUp(self) -> None:
        self.original_cache = deepcopy(server.CACHE)
        server.CACHE.clear()
        server.CACHE.update({"ts": 0, "payload": None, "raw_count": 0, "error": None, "assembled": None})
        self.upstream: List[Dict[str, Any]] = []
        self.leads: List[Dict[str, Any]] = []
        self.patches = [
            patch("server.fetch_mapareve", side_effect=lambda **_: {
                "stations": deepcopy(self.upstream), "raw_count": len(self.upstream), "fetched_at": server.now_iso()}),
            patch("server.fetch_gaia_live", return_value=empty_live_payload()),
            patch("server.load_all_leads", side_effect=lambda bbox=None: deepcopy(self.leads)),
            patch("server.geocode_search", return_value=[{"label": "Valencia, España", "lat": ORIGIN_LAT, "lon": ORIGIN_LON}]),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        server.CACHE.clear()
        server.CACHE.update(self.original_cache)

    # ----------------------------------------------------------------- helpers

    def http(self, method: str, path: str = "/mcp", body: Any = None, headers: Optional[Dict[str, str]] = None, raw: Optional[bytes] = None):
        data = raw if raw is not None else (json.dumps(body).encode("utf-8") if body is not None else None)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method, headers={
            "Content-Type": "application/json", "Accept": "application/json, text/event-stream", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                text = resp.read().decode("utf-8")
                return resp.status, (json.loads(text) if text else None), dict(resp.headers)
        except urllib.error.HTTPError as err:
            with err:
                text = err.read().decode("utf-8")
            try:
                parsed = json.loads(text) if text else None
            except ValueError:
                parsed = text
            return err.code, parsed, dict(err.headers)

    def legacy(self, method: str, params: Optional[Dict[str, Any]] = None, rid: Any = 1):
        body: Dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            body["params"] = params
        status, payload, _ = self.http("POST", body=body, headers={"MCP-Protocol-Version": LEGACY})
        return status, payload

    def modern(self, method: str, params: Optional[Dict[str, Any]] = None, *, name_header: Optional[str] = None,
               headers: Optional[Dict[str, str]] = None, version: str = MODERN, extra_meta: Optional[Dict[str, Any]] = None):
        params = dict(params or {})
        params["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": version,
            "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "0"},
            "io.modelcontextprotocol/clientCapabilities": {},
            **(extra_meta or {}),
        }
        sent = {"MCP-Protocol-Version": version, "Mcp-Method": method}
        if method == "tools/call":
            sent["Mcp-Name"] = name_header if name_header is not None else params.get("name", "")
        sent.update(headers or {})
        status, payload, _ = self.http("POST", body={"jsonrpc": "2.0", "id": 7, "method": method, "params": params}, headers=sent)
        return status, payload

    def call(self, tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        status, payload = self.modern("tools/call", {"name": tool, "arguments": arguments})
        self.assertEqual(status, 200, payload)
        return payload["result"]

    def find(self, **arguments: Any) -> Dict[str, Any]:
        arguments.setdefault("lat", ORIGIN_LAT)
        arguments.setdefault("lon", ORIGIN_LON)
        result = self.call("find_chargers", arguments)
        self.assertFalse(result["isError"], result["content"])
        return result["structuredContent"]

    # ---------------------------------------------------------------- protocol

    def test_legacy_client_handshake_list_and_notification(self) -> None:
        status, payload = self.legacy("initialize", {
            "protocolVersion": LEGACY, "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}})
        self.assertEqual(status, 200)
        result = payload["result"]
        self.assertEqual(result["protocolVersion"], LEGACY)
        self.assertEqual(result["serverInfo"]["name"], "voltago")
        self.assertIn("tools", result["capabilities"])
        self.assertIn("live_verified", result["instructions"])

        status, body, headers = self.http("POST", body={"jsonrpc": "2.0", "method": "notifications/initialized"},
                                          headers={"MCP-Protocol-Version": LEGACY})
        self.assertEqual(status, 202)
        self.assertIsNone(body)
        self.assertNotIn("Mcp-Session-Id", headers)

        status, payload = self.legacy("tools/list")
        self.assertEqual(status, 200)
        tools = payload["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], ["find_chargers", "get_charger", "get_data_status"])
        for tool in tools:
            self.assertTrue(tool["annotations"]["readOnlyHint"], tool["name"])
            self.assertFalse(tool["annotations"]["destructiveHint"], tool["name"])
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_unknown_legacy_version_still_gets_a_supported_version(self) -> None:
        status, payload = self.legacy("initialize", {"protocolVersion": "2099-01-01", "capabilities": {}})
        self.assertEqual(status, 200)
        self.assertIn(payload["result"]["protocolVersion"], agent_mcp.LEGACY_VERSIONS)

    def test_modern_discover_and_version_negotiation(self) -> None:
        status, payload = self.modern("server/discover")
        self.assertEqual(status, 200)
        result = payload["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertIn(MODERN, result["supportedVersions"])
        self.assertEqual(result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"], "voltago")

        status, payload = self.modern("tools/list", version="1900-01-01")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], agent_mcp.UNSUPPORTED_VERSION)
        self.assertEqual(payload["error"]["data"]["requested"], "1900-01-01")
        self.assertIn(MODERN, payload["error"]["data"]["supported"])

    def test_modern_header_validation(self) -> None:
        status, payload = self.modern("tools/call", {"name": "get_data_status", "arguments": {}}, name_header="find_chargers")
        self.assertEqual((status, payload["error"]["code"]), (400, agent_mcp.HEADER_MISMATCH))

        status, payload = self.modern("tools/list", headers={"Mcp-Method": "tools/call"})
        self.assertEqual((status, payload["error"]["code"]), (400, agent_mcp.HEADER_MISMATCH))

        status, payload = self.modern("tools/list", headers={"MCP-Protocol-Version": "2025-11-25"})
        self.assertEqual((status, payload["error"]["code"]), (400, agent_mcp.HEADER_MISMATCH))

        encoded = "=?base64?Z2V0X2RhdGFfc3RhdHVz?="  # "get_data_status"
        status, payload = self.modern("tools/call", {"name": "get_data_status", "arguments": {}}, name_header=encoded)
        self.assertEqual(status, 200, payload)

        status, payload = self.modern("resources/list")
        self.assertEqual((status, payload["error"]["code"]), (404, agent_mcp.METHOD_NOT_FOUND))

    def test_oauth_discovery_paths_are_real_404s_not_the_app_shell(self) -> None:
        # Claude and other MCP clients probe these before connecting; a 200 HTML shell would
        # look like broken auth metadata instead of "this server needs no sign-in".
        for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp",
                     "/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"):
            status, payload, headers = self.http("GET", path)
            self.assertEqual(status, 404, path)
            self.assertEqual(payload, {"error": "not_found"}, path)
            self.assertIn("application/json", headers.get("Content-Type", ""), path)

    def test_http_envelope_rules(self) -> None:
        for method in ("GET", "DELETE"):
            status, payload, headers = self.http(method)
            self.assertEqual(status, 405, method)
            self.assertIn("POST", headers.get("Allow", ""))
            self.assertEqual(payload["error"], "method_not_allowed")

        status, payload, _ = self.http("POST", body={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                       headers={"Origin": "http://rebinding.example"})
        self.assertEqual(status, 403)
        status, _, _ = self.http("POST", body={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                 headers={"Origin": "https://claude.ai"})
        self.assertEqual(status, 200)

        status, payload, _ = self.http("POST", body=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
        self.assertEqual((status, payload["error"]["code"]), (400, agent_mcp.INVALID_REQUEST))
        status, payload, _ = self.http("POST", raw=b"{nope")
        self.assertEqual((status, payload["error"]["code"]), (400, agent_mcp.PARSE_ERROR))
        status, _, _ = self.http("POST", raw=b"x" * (agent_mcp.MAX_BODY_BYTES + 1))
        self.assertEqual(status, 413)

    def test_unknown_tool_and_bad_arguments(self) -> None:
        status, payload = self.modern("tools/call", {"name": "start_charging", "arguments": {}})
        self.assertEqual((status, payload["error"]["code"]), (400, agent_mcp.INVALID_PARAMS))

        result = self.call("find_chargers", {"lat": ORIGIN_LAT})
        self.assertTrue(result["isError"])
        self.assertIn("lon", result["content"][0]["text"])
        result = self.call("find_chargers", {"lat": 91.0, "lon": -0.1})
        self.assertTrue(result["isError"])
        result = self.call("find_chargers", {"lat": ORIGIN_LAT, "lon": ORIGIN_LON, "connector": "NACS-ish"})
        self.assertTrue(result["isError"])
        result = self.call("find_chargers", {"lat": ORIGIN_LAT, "lon": ORIGIN_LON, "surprise": 1})
        self.assertTrue(result["isError"])

    # ------------------------------------------------------------------ honesty

    def test_find_orders_by_honesty_then_distance_and_never_promotes_leads(self) -> None:
        self.upstream = [
            live_row("live-go-far", lat=39.49, lon=-0.37),
            live_row("live-busy-near", lat=39.4705, lon=-0.37, available=0),
        ]
        forged = lead_row("free-lead-nearest", lat=39.4701, lon=-0.3701, price=0.0)
        forged["verification"] = dict(forged["verification"], live_verified=True, level="live")
        self.leads = [forged]

        found = self.find(radius_km=5)
        ids = [row["id"] for row in found["chargers"]]
        self.assertEqual(ids[0], "live-go-far", "route-ready live row must lead despite distance")
        self.assertEqual(ids[-1], "free-lead-nearest", "leads rank after live rows")

        by_id = {row["id"]: row for row in found["chargers"]}
        self.assertTrue(by_id["live-go-far"]["evidence"]["route_ready_now"])
        self.assertEqual(by_id["live-go-far"]["decision"]["level"], "go")
        lead = by_id["free-lead-nearest"]
        self.assertTrue(lead["evidence"]["is_lead"])
        self.assertFalse(lead["evidence"]["live_verified"], "a lead can never present as live")
        self.assertFalse(lead["evidence"]["route_ready_now"])
        self.assertIsNone(lead["hardware"]["available_connectors"])
        self.assertTrue(all("status" not in c for c in lead["hardware"]["connectors"]))
        self.assertIn("not live", self.call("find_chargers", {"lat": ORIGIN_LAT, "lon": ORIGIN_LON})["content"][0]["text"])

        ready = self.find(available_now=True)
        self.assertEqual([row["id"] for row in ready["chargers"]], ["live-go-far"])
        self.assertEqual(ready["excluded"]["not_route_ready"], 2)

    def test_filters_exclude_unknowns_instead_of_guessing(self) -> None:
        self.upstream = [live_row("cheap-ccs", lat=39.471, lon=-0.37, price=0.2, labels=["CCS2"], power=50)]
        self.leads = [
            lead_row("unknown-price", lat=39.472, lon=-0.37, price=None, labels=["Type 2"]),
            lead_row("free-no-connectors", lat=39.473, lon=-0.37, price=0.0, labels=[]),
            lead_row("too-far", lat=39.60, lon=-0.37, price=0.0, labels=["CCS2"]),
        ]

        priced = self.find(max_price_eur_kwh=0.3, radius_km=5)
        self.assertEqual({row["id"] for row in priced["chargers"]}, {"cheap-ccs", "free-no-connectors"})
        self.assertEqual(priced["excluded"]["price_unknown"], 1)
        self.assertEqual(priced["excluded"]["outside_radius"], 1)

        free = self.find(free_only=True, radius_km=5)
        self.assertEqual([row["id"] for row in free["chargers"]], ["free-no-connectors"])
        self.assertEqual(free["excluded"]["not_free"], 2)

        ccs = self.find(connector="CCS2", radius_km=5)
        self.assertEqual([row["id"] for row in ccs["chargers"]], ["cheap-ccs"])
        self.assertEqual(ccs["excluded"]["connector_unknown"], 1)
        self.assertEqual(ccs["excluded"]["connector_mismatch"], 1)

        fast = self.find(min_power_kw=40, radius_km=5)
        self.assertEqual([row["id"] for row in fast["chargers"]], ["cheap-ccs"])
        self.assertEqual(fast["excluded"]["power_unknown"], 2)

    def test_old_live_status_is_flagged(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat().replace("+00:00", "Z")
        self.upstream = [live_row("old-feed", lat=39.471, lon=-0.37, last_seen=old)]
        result = self.call("find_chargers", {"lat": ORIGIN_LAT, "lon": ORIGIN_LON})
        row = result["structuredContent"]["chargers"][0]
        self.assertTrue(row["evidence"]["live_status_stale"])
        self.assertGreater(row["evidence"]["live_status_age_minutes"], 180)
        self.assertFalse(row["evidence"]["route_ready_now"])
        self.assertIn("status 5 h old", result["content"][0]["text"])

    def test_place_lookup_uses_geocoder(self) -> None:
        self.upstream = [live_row("near-place", lat=39.471, lon=-0.37)]
        found = self.find(place="Valencia", lat=None, lon=None)
        self.assertEqual(found["query"]["place"], "Valencia, España")
        self.assertEqual([row["id"] for row in found["chargers"]], ["near-place"])

    def test_tools_declare_chatgpt_directory_annotations_and_status_lines(self) -> None:
        status, payload = self.legacy("tools/list", {})
        self.assertEqual(status, 200, payload)
        for tool in payload["result"]["tools"]:
            hints = tool["annotations"]
            self.assertIs(hints["readOnlyHint"], True, tool["name"])
            self.assertIs(hints["destructiveHint"], False, tool["name"])
            self.assertIs(hints["openWorldHint"], False, tool["name"])
            self.assertTrue(tool["_meta"]["openai/toolInvocation/invoking"])
            self.assertTrue(tool["_meta"]["openai/toolInvocation/invoked"])

    def test_approximate_chatgpt_city_is_used_only_without_an_explicit_place(self) -> None:
        self.upstream = [live_row("near-city", lat=39.471, lon=-0.37)]
        hint = {"openai/userLocation": {"city": "Valencia", "region": "Valencian Community", "country": "ES",
                                        "latitude": 1.0, "longitude": 2.0, "timezone": "Europe/Madrid"}}
        geocode = server  # server.geocode_search is the patched mock during each test
        status, payload = self.modern("tools/call", {"name": "find_chargers", "arguments": {}}, extra_meta=hint)
        self.assertEqual(status, 200, payload)
        result = payload["result"]
        self.assertFalse(result["isError"], result["content"])
        query = result["structuredContent"]["query"]
        self.assertEqual(query["origin"], "approximate_client_location")
        # The hint's coordinates are never used as the origin; the city text is geocoded instead.
        self.assertEqual((query["lat"], query["lon"]), (ORIGIN_LAT, ORIGIN_LON))
        geocode.geocode_search.assert_called_with("Valencia, Valencian Community, ES", limit=1)
        self.assertIn("approximate city", result["content"][0]["text"])

        status, payload = self.modern("tools/call", {"name": "find_chargers", "arguments": {"place": "Valencia airport"}},
                                      extra_meta=hint)
        self.assertEqual(payload["result"]["structuredContent"]["query"]["origin"], "place")
        geocode.geocode_search.assert_called_with("Valencia airport", limit=1)

        status, payload = self.modern("tools/call", {"name": "find_chargers", "arguments": {}})
        self.assertTrue(payload["result"]["isError"])
        self.assertIn("Pass a `place`", payload["result"]["content"][0]["text"])

    def test_coarse_client_place_ignores_malformed_hints(self) -> None:
        self.assertIsNone(agent_mcp.coarse_client_place(None))
        self.assertIsNone(agent_mcp.coarse_client_place({"openai/userLocation": "Valencia"}))
        self.assertIsNone(agent_mcp.coarse_client_place({"openai/userLocation": {"city": "  ", "latitude": 39.4}}))
        self.assertEqual(agent_mcp.coarse_client_place({"openai/userLocation": {"city": "Bad\nCity", "country": "ES"}}), "ES")
        self.assertEqual(len(agent_mcp.coarse_client_place({"openai/userLocation": {"city": "x" * 500}})), 60)

    def test_mcp_results_match_the_rest_api(self) -> None:
        self.upstream = [live_row(f"live-{i}", lat=39.46 + i * 0.004, lon=-0.37) for i in range(5)]
        self.leads = [lead_row(f"lead-{i}", lat=39.465 + i * 0.006, lon=-0.365, price=0.0) for i in range(4)]
        query = f"lat={ORIGIN_LAT}&lon={ORIGIN_LON}&radius_km=3&include_leads=true&max_price=5"
        status, rest, _ = self.http("GET", f"/api/chargers?{query}")
        self.assertEqual(status, 200)
        rest_ids = {row["id"] for row in rest["stations"] + rest["leads"] if row["distance_km"] <= 3}
        server.CACHE.update({"ts": 0, "payload": None, "assembled": None})
        mcp = self.find(radius_km=3, limit=30)
        self.assertEqual({row["id"] for row in mcp["chargers"]}, rest_ids)
        self.assertEqual(mcp["total_matches"], len(rest_ids))

    def test_get_charger_returns_stored_evidence_and_live_rows(self) -> None:
        stored_id = next(row["id"] for row in server.load_intelligence()["records"])
        result = self.call("get_charger", {"id": stored_id})
        self.assertFalse(result["isError"], result["content"])
        detail = result["structuredContent"]
        self.assertEqual(detail["id"], stored_id)
        self.assertFalse(detail["evidence"]["live_verified"])
        self.assertTrue(detail["sources"])
        self.assertIn("windows", detail["price"])

        self.upstream = [live_row("live-detail", lat=39.471, lon=-0.37)]
        result = self.call("get_charger", {"id": "live-detail", "lat": 39.471, "lon": -0.37})
        self.assertFalse(result["isError"], result["content"])
        self.assertTrue(result["structuredContent"]["evidence"]["live_verified"])

        result = self.call("get_charger", {"id": "does-not-exist"})
        self.assertTrue(result["isError"])
        self.assertIn("lat", result["content"][0]["text"])

    def test_historical_community_price_is_listed_not_current(self) -> None:
        row = lead_row("old-community-free", lat=39.471, lon=-0.37, price=0,
                       labels=["CCS2"], reliability="community-verified")
        row["raw"] = {"LastVerified": "2017-04-12T00:00:00Z"}
        row = server.enrich_station(row, live=False)
        self.leads = [row]
        for tool, args in (("find_chargers", {"lat": ORIGIN_LAT, "lon": ORIGIN_LON}),
                           ("get_charger", {"id": row["id"]})):
            with self.subTest(tool=tool), patch("agent_mcp._stored_row", return_value=deepcopy(row)):
                result = self.call(tool, args)
                view = result["structuredContent"]
                if tool == "find_chargers":
                    view = view["chargers"][0]
                self.assertIsNone(view["price"]["price_now_eur_kwh"])
                self.assertTrue(view["price"]["is_free"])  # the listed claim, kept as a lead
                self.assertFalse(view["price"]["price_verified"])
                self.assertEqual(view["price"]["lowest_published_eur_kwh"], 0)
                self.assertIn("Listed Free", view["price"]["label"])
                self.assertIn("2017", view["price"]["label"])
                self.assertIn("Listed Free", result["content"][0]["text"])
                self.assertNotIn("Now Free", json.dumps(result))
                self.assertFalse(view["evidence"]["route_ready_now"])
        # Free filters keep listed free leads, as the apps do; the label carries the doubt.
        free = self.find(free_only=True)
        self.assertEqual(free["count"], 1)
        self.assertIn("current price unverified", self.call("find_chargers", {"lat": ORIGIN_LAT, "lon": ORIGIN_LON, "free_only": True})["content"][0]["text"])
        self.assertEqual(self.find(max_price_eur_kwh=0.25)["count"], 1)

    def test_vehicleless_decisions_are_neutral_without_changing_app_decisions(self) -> None:
        row = lead_row("chademo-only", lat=39.471, lon=-0.37, price=0,
                       labels=["CHAdeMO"])
        self.assertEqual(row["decision"]["label"], "Not Tesla-safe")
        original = deepcopy(row)
        self.leads = [row]
        for tool, args in (("find_chargers", {"lat": ORIGIN_LAT, "lon": ORIGIN_LON}),
                           ("get_charger", {"id": row["id"]})):
            with self.subTest(tool=tool), patch("agent_mcp._stored_row", return_value=row):
                result = self.call(tool, args)
                view = result["structuredContent"]
                if tool == "find_chargers":
                    view = view["chargers"][0]
                self.assertNotIn("Tesla", json.dumps(view["decision"]))
                self.assertNotEqual(view["decision"]["level"], "avoid")
                self.assertIn("CHAdeMO", view["decision"]["reason"])
                self.assertNotIn("Not Tesla-safe", result["content"][0]["text"])
        self.assertEqual(row, original)

    def test_positive_vehicleless_decision_does_not_claim_tesla_compatibility(self) -> None:
        self.upstream = [live_row("live-ccs", lat=39.471, lon=-0.37)]
        view = self.find()["chargers"][0]
        self.assertNotIn("Tesla", json.dumps(view["decision"]))
        self.assertIn("compatibility", view["decision"]["reason"])

    def test_vehicleless_live_chademo_has_the_same_eligibility_as_ccs(self) -> None:
        chademo = live_row("near-chademo", lat=39.4701, lon=-0.37, labels=["CHAdeMO"])
        chademo["tesla_ok"] = False
        self.upstream = [chademo, live_row("far-ccs", lat=39.49, lon=-0.37)]
        found = self.find(available_now=True)
        self.assertEqual([v["id"] for v in found["chargers"]], ["near-chademo", "far-ccs"])
        self.assertTrue(all(v["evidence"]["route_ready_now"] for v in found["chargers"]))
        detail = self.call("get_charger", {"id": "near-chademo", "lat": ORIGIN_LAT, "lon": ORIGIN_LON})
        self.assertEqual(detail["structuredContent"]["decision"]["level"], "go")
        _, rest, _ = self.http("GET", f"/api/chargers?lat={ORIGIN_LAT}&lon={ORIGIN_LON}&radius_km=5&max_price=5")
        assert isinstance(rest, dict)
        app = next(v for v in rest["stations"] if v["id"] == "near-chademo")
        self.assertEqual(app["decision"]["label"], "Not Tesla-safe")

    def test_data_status_live_sources_have_uniform_fields_and_text(self) -> None:
        fields = {"age_seconds", "fresh", "stale", "error", "has_payload"}
        health = {"live_cache": {
            "endolla": {"name": "endolla", "has_payload": True, "age_seconds": 42,
                        "fresh": True, "stale": False, "error": None},
            "spirii": {"name": "spirii", "has_payload": True, "age_seconds": 301,
                       "fresh": False, "stale": True, "error": None},
            "gaia": {"name": "Gaia", "has_payload": False, "error": None},
            "mapareve": {"name": "MapaREVE", "has_payload": False, "fresh": False,
                         "stale": False, "error": "MapaReveRefused"},
        }}
        with patch.dict(agent_mcp._STATUS_CACHE, {"value": None, "ts": 0}), \
                patch("server.source_health_summary", return_value=health):
            result = self.call("get_data_status", {})
        sources = result["structuredContent"]["live_sources"]
        for name, state in sources.items():
            self.assertTrue(fields <= set(state), name)
        text = result["content"][0]["text"]
        for label in ("Live:", "Endolla fresh 42 s", "Spirii stale 301 s", "Gaia unavailable",
                      "MapaREVE unavailable (MapaReveRefused)"):
            self.assertIn(label, text)

    def test_data_status_reports_dataset_and_live_cache(self) -> None:
        result = self.call("get_data_status", {})
        status = result["structuredContent"]
        self.assertIn("Spain", status["scope"])
        self.assertGreater(status["dataset"]["records"], 0)
        self.assertIn("mapareve", status["live_sources"])
        self.assertEqual(set(status["decision_levels"]), {"go", "check", "verify", "lead", "avoid"})


class AgentMcpUnitTests(unittest.TestCase):
    def test_gaia_failed_fetch_cannot_report_fresh_status(self) -> None:
        with patch.dict(server.GAIA_CACHE, {}, clear=True), \
                patch("server.GAIA_STATION_SEEDS", [deepcopy(server.GAIA_STATION_SEEDS[0])]), \
                patch("server.gaia_urls_for_seed", return_value=[server.GAIA_DEFAULT_JUMP_URL]), \
                patch("server.fetch_gaia_public_html", side_effect=TimeoutError("offline fixture")), \
                patch.dict(agent_mcp._STATUS_CACHE, {"value": None, "ts": 0}):
            server.fetch_gaia_live(force=True)
            result = agent_mcp.get_data_status({}, server)
        gaia = result["structured"]["live_sources"]["gaia"]
        self.assertFalse(gaia["fresh"])
        self.assertIn("TimeoutError", gaia["error"])
        self.assertNotIn("Gaia fresh", result["text"])
        self.assertIn("TimeoutError", result["text"])

    def test_neutral_decisions_keep_outages_and_community_downgrades(self) -> None:
        row = server.enrich_station(live_row("reported-failure", lat=ORIGIN_LAT, lon=ORIGIN_LON), live=True)
        summary = {"recent_failure": True, "recent_failure_count": 2}
        community.apply_community_evidence(row, summary)
        original = deepcopy(row)
        neutral = agent_mcp._vehicle_neutral_row(row, server)
        self.assertEqual(neutral["decision"]["level"], "check")
        self.assertFalse(server.route_ready_now(neutral))
        self.assertEqual(row, original)
        row["source_reported_unavailable"] = {"summary": "Source reports closed"}
        self.assertEqual(agent_mcp._vehicle_neutral_row(row, server)["decision"]["level"], "avoid")
        row.pop("source_reported_unavailable")
        row.pop("community")
        row["connectors"] = []
        row["connector_labels"] = []
        self.assertNotEqual(agent_mcp._vehicle_neutral_row(row, server)["decision"]["level"], "go")

    def test_rate_limited_tool_call_is_a_tool_error(self) -> None:
        status, payload = agent_mcp.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_data_status", "arguments": {}}},
            {},
            backend=server,
            rate_limit=lambda: (False, 4.2),
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["result"]["isError"])
        self.assertIn("5 seconds", payload["result"]["content"][0]["text"])

    def test_origin_policy(self) -> None:
        self.assertTrue(agent_mcp.origin_allowed(None))
        self.assertTrue(agent_mcp.origin_allowed("https://chatgpt.com"))
        self.assertTrue(agent_mcp.origin_allowed("http://localhost:6274"))
        self.assertFalse(agent_mcp.origin_allowed("http://192.168.1.5"))
        self.assertFalse(agent_mcp.origin_allowed("null"))
        self.assertTrue(agent_mcp.origin_allowed("http://intranet.test", ("http://intranet.test",)))

    def test_connector_aliases(self) -> None:
        self.assertEqual(agent_mcp._connector_key("IEC_62196_T2_COMBO"), None)
        self.assertEqual(agent_mcp._connector_key("CCS2"), "ccs2")
        self.assertEqual(agent_mcp._connector_key("Type 2 (Socket Only)"), "type 2")
        self.assertEqual(agent_mcp._connector_key("Wall outlet (connector unspecified)"), None)

    def test_module_is_in_the_runtime_closure(self) -> None:
        sys.path.insert(0, str(ROOT / "scripts"))
        import runtime_closure  # noqa: E402
        self.assertIn("agent_mcp.py", runtime_closure.RUNTIME_FILES)


if __name__ == "__main__":
    unittest.main()
