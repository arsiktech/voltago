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
               headers: Optional[Dict[str, str]] = None, version: str = MODERN):
        params = dict(params or {})
        params["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": version,
            "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "0"},
            "io.modelcontextprotocol/clientCapabilities": {},
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
        result = self.call("find_chargers", {"lat": 51.5, "lon": -0.1})
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

    def test_data_status_reports_dataset_and_live_cache(self) -> None:
        result = self.call("get_data_status", {})
        status = result["structuredContent"]
        self.assertIn("Spain", status["scope"])
        self.assertGreater(status["dataset"]["records"], 0)
        self.assertIn("mapareve", status["live_sources"])
        self.assertEqual(set(status["decision_levels"]), {"go", "check", "verify", "lead", "avoid"})


class AgentMcpUnitTests(unittest.TestCase):
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
