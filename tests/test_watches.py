#!/usr/bin/env python3
"""Transition matrix and endpoint tests for availability watches (watches.py).

Stdlib only. The pure `evaluate` step, the SQLite store, the watcher pass and the
HTTP routes are exercised with synthetic rows; nothing touches the network or APNs.
"""
from __future__ import annotations

import json
from contextlib import closing
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import agent_mcp  # noqa: E402
import server  # noqa: E402
import watches  # noqa: E402

KEY = agent_mcp._connector_key
TOKEN = "ab" * 32
OTHER_TOKEN = "cd" * 32
T0 = 1_800_000_000.0


def row(statuses: Dict[str, str], *, feed: str = "endolla", valid: bool = True, cache_stale: bool = False,
        labels: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    labels = labels or {}
    return {
        "id": "intel_endolla_boqueria",
        "name": "AP BSM La Boqueria",
        "lat": 41.38174,
        "lon": 2.17007,
        "cache_stale": cache_stale,
        "live_status": {"valid": valid, "feed": feed, "checked_at": "2027-01-15T08:00:00Z"},
        "connectors": [
            {"connector_id": cid, "status": status, "label": labels.get(cid, "CCS2"),
             "last_changed_at": "2027-01-15T09:05:00+01:00"}
            for cid, status in statuses.items()
        ],
    }


def watch(baseline: Optional[Dict[str, str]], *, types: Optional[List[str]] = None, created: float = T0,
          last_fresh: Optional[float] = T0, minutes: int = 120) -> Dict[str, Any]:
    return {"id": "w" * 24, "station_id": "intel_endolla_boqueria", "station_name": "AP BSM La Boqueria",
            "connector_types": types or [], "created_at": created, "expires_at": created + minutes * 60,
            "baseline": baseline, "last_fresh_at": last_fresh}


class TransitionMatrixTests(unittest.TestCase):
    def step(self, w: Dict[str, Any], r: Optional[Dict[str, Any]], dt: float = 60.0) -> Dict[str, Any]:
        return watches.evaluate(w, r, T0 + dt, KEY)

    def test_occupied_to_free_alerts_once_and_ends(self) -> None:
        result = self.step(watch({"c1": "CHARGING"}), row({"c1": "AVAILABLE"}))
        self.assertEqual(result["end_reason"], "alerted")
        self.assertEqual(result["alert"]["connector_id"], "c1")
        self.assertEqual(result["alert"]["from_status"], "CHARGING")

    def test_free_at_start_does_not_alert(self) -> None:
        result = self.step(watch({"c1": "AVAILABLE"}), row({"c1": "AVAILABLE"}))
        self.assertIsNone(result["alert"])
        self.assertIsNone(result["end_reason"])

    def test_unknown_to_free_does_not_alert(self) -> None:
        result = self.step(watch({"c1": "UNKNOWN"}), row({"c1": "AVAILABLE"}))
        self.assertIsNone(result["alert"])
        self.assertEqual(result["baseline"], {"c1": "AVAILABLE"})

    def test_stale_then_fresh_free_only_rebaselines(self) -> None:
        stale = self.step(watch({"c1": "CHARGING"}), row({"c1": "AVAILABLE"}, valid=False))
        self.assertIsNone(stale["alert"])
        self.assertIsNone(stale["baseline"])
        recovered = self.step(watch(stale["baseline"], last_fresh=T0), row({"c1": "AVAILABLE"}), dt=120)
        self.assertIsNone(recovered["alert"])
        self.assertEqual(recovered["baseline"], {"c1": "AVAILABLE"})

    def test_cached_row_and_ineligible_feed_are_outage_observations(self) -> None:
        for bad in (row({"c1": "AVAILABLE"}, cache_stale=True), row({"c1": "AVAILABLE"}, feed="etecnic"), None):
            result = self.step(watch({"c1": "CHARGING"}), bad)
            self.assertIsNone(result["alert"])
            self.assertIsNone(result["baseline"])

    def test_outage_longer_than_fifteen_minutes_ends_silently(self) -> None:
        result = self.step(watch({"c1": "CHARGING"}), None, dt=watches.OUTAGE_SECONDS + 1)
        self.assertEqual(result["end_reason"], "outage")
        self.assertIsNone(result["alert"])

    def test_expiry_wins_over_a_transition(self) -> None:
        result = self.step(watch({"c1": "CHARGING"}, minutes=10), row({"c1": "AVAILABLE"}), dt=601)
        self.assertEqual(result["end_reason"], "expired")
        self.assertIsNone(result["alert"])

    def test_incompatible_and_unknown_type_connectors_never_alert(self) -> None:
        labels = {"c1": "Type 2", "c2": "Wall outlet (connector unspecified)"}
        result = self.step(watch({}, types=["CCS2"]), row({"c1": "AVAILABLE", "c2": "AVAILABLE"}, labels=labels))
        self.assertEqual(result["baseline"], {})
        self.assertIsNone(result["alert"])

    def test_out_of_order_to_free_alerts(self) -> None:
        result = self.step(watch({"c1": "OUTOFORDER"}), row({"c1": "AVAILABLE"}))
        self.assertEqual(result["end_reason"], "alerted")

    def test_payload_says_not_reserved_and_uses_source_clock(self) -> None:
        payload = watches.alert_payload(watch({}), {"label": "CCS2", "observed_at": "2027-01-15T09:05:00+01:00"})
        self.assertEqual(payload["aps"]["alert"]["title"], "AP BSM La Boqueria")
        self.assertIn("showed free at 09:05", payload["aps"]["alert"]["body"])
        self.assertIn("Not reserved", payload["aps"]["alert"]["body"])
        utc = watches.alert_payload(watch({}), {"label": "CCS2", "observed_at": "2027-01-15T08:05:00Z"})
        self.assertIn("08:05 UTC", utc["aps"]["alert"]["body"])


class StoreAndWatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="volta-watches-"))
        self.original = watches._DB_PATH
        watches.configure(self.tmp / "watches.db")
        self.current: Optional[Dict[str, Any]] = row({"c1": "CHARGING", "c2": "AVAILABLE"})
        self.lookups = 0

    def tearDown(self) -> None:
        watches.configure(self.original)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lookup(self, station_id: str, lat: float, lon: float) -> Optional[Dict[str, Any]]:
        self.lookups += 1
        return deepcopy(self.current)

    def create(self, token: str = TOKEN, **extra: Any) -> Dict[str, Any]:
        body = {"station_id": "intel_endolla_boqueria", "lat": 41.38174, "lon": 2.17007, "device_token": token, **extra}
        return watches.create_watch(body, lookup=self.lookup, key_for=KEY,
                                    allowed_types=agent_mcp.CONNECTOR_TYPES, now=T0)

    def run_pass(self, sender: Any, dt: float) -> Dict[str, int]:
        return watches.run_once(lookup=self.lookup, sender=sender, key_for=KEY, now=T0 + dt)

    def test_full_journey_alerts_once_and_drops_the_token(self) -> None:
        created = self.create()
        self.assertEqual(created["free_now"], ["c2"])
        self.assertNotIn("device_token", json.dumps(created))
        sender = watches.RecordingSender()
        self.current = row({"c1": "CHARGING", "c2": "CHARGING"})
        self.assertEqual(self.run_pass(sender, 60)["alerted"], 0)
        self.current = row({"c1": "AVAILABLE", "c2": "CHARGING"})
        self.assertEqual(self.run_pass(sender, 120)["alerted"], 1)
        # Flapping after the alert: the watch has ended, so nothing more is sent.
        self.current = row({"c1": "CHARGING", "c2": "CHARGING"})
        self.run_pass(sender, 180)
        self.current = row({"c1": "AVAILABLE", "c2": "CHARGING"})
        self.run_pass(sender, 240)
        self.assertEqual(len(sender.sent), 1)
        self.assertEqual(sender.sent[0]["collapse_id"], created["id"])
        self.assertEqual(sender.sent[0]["expires_at"], T0 + 120 * 60)
        stored = watches.get_watch(created["id"])
        self.assertFalse(stored["active"])
        self.assertEqual((stored["end_reason"], stored["delivery"]), ("alerted", "sent"))
        with closing(watches._connect()) as conn:
            token = conn.execute("SELECT device_token FROM watches WHERE id = ?", (created["id"],)).fetchone()[0]
        self.assertIsNone(token)

    def test_unconfigured_sender_records_that_nothing_was_delivered(self) -> None:
        created = self.create()
        self.current = row({"c1": "AVAILABLE", "c2": "AVAILABLE"})
        self.run_pass(watches.UnconfiguredSender(), 60)
        stored = watches.get_watch(created["id"])
        self.assertEqual((stored["end_reason"], stored["delivery"]), ("alerted", "not_configured"))

    def test_invalid_token_ends_the_watch(self) -> None:
        created = self.create()
        self.current = row({"c1": "AVAILABLE", "c2": "AVAILABLE"})
        self.run_pass(watches.RecordingSender({TOKEN: "invalid_token"}), 60)
        self.assertEqual(watches.get_watch(created["id"])["end_reason"], "invalid_token")

    def test_cancel_and_restart_persistence(self) -> None:
        first = self.create()
        second = self.create(token=OTHER_TOKEN)
        watches.cancel_watch(first["id"], now=T0 + 10)
        # A "restart" is a fresh read of the same database file.
        self.assertEqual([w["id"] for w in watches.active_watches()], [second["id"]])
        self.assertEqual(watches.get_watch(first["id"])["end_reason"], "cancelled")

    def test_lookup_failure_is_an_outage_not_a_transition(self) -> None:
        created = self.create()
        sender = watches.RecordingSender()

        def broken(*_: Any) -> None:
            raise RuntimeError("feed down")

        watches.run_once(lookup=broken, sender=sender, key_for=KEY, now=T0 + watches.OUTAGE_SECONDS + 5)
        self.assertEqual(sender.sent, [])
        self.assertEqual(watches.get_watch(created["id"])["end_reason"], "outage")

    def test_watches_on_one_station_share_one_lookup_per_pass(self) -> None:
        self.create()
        self.create(token=OTHER_TOKEN)
        self.lookups = 0
        self.run_pass(watches.RecordingSender(), 60)
        self.assertEqual(self.lookups, 1)

    def test_creation_rules(self) -> None:
        for bad, code in (
            ({"device_token": "not-hex"}, "bad_device_token"),
            ({"duration_minutes": 5}, "bad_duration"),
            ({"duration_minutes": 361}, "bad_duration"),
            ({"connector_types": ["Warp"]}, "bad_connector_types"),
            ({"lat": "x"}, "bad_coordinates"),
        ):
            with self.assertRaises(watches.WatchError) as ctx:
                self.create(**bad)
            self.assertEqual(ctx.exception.code, code)
        self.current = row({"c1": "CHARGING"}, cache_stale=True)
        with self.assertRaises(watches.WatchError) as ctx:
            self.create()
        self.assertEqual(ctx.exception.code, "not_watchable")
        self.current = row({"c1": "CHARGING"}, feed="etecnic")
        with self.assertRaises(watches.WatchError) as ctx:
            self.create()
        self.assertIn("no live status", ctx.exception.message)
        self.current = row({"c1": "CHARGING"}, labels={"c1": "Type 2"})
        with self.assertRaises(watches.WatchError) as ctx:
            self.create(connector_types=["CCS2"])
        self.assertEqual(ctx.exception.code, "no_compatible_connector")

    def test_per_device_limit_and_purge(self) -> None:
        self.current = row({"c1": "CHARGING"})
        ids = [self.create()["id"] for _ in range(watches.MAX_ACTIVE_PER_DEVICE)]
        with self.assertRaises(watches.WatchError) as ctx:
            self.create()
        self.assertEqual(ctx.exception.code, "too_many_watches")
        watches.cancel_watch(ids[0], now=T0)
        with closing(watches._connect()) as conn:
            watches.purge(conn, T0 + watches.PURGE_AFTER_SECONDS + 1)
            conn.commit()
        with self.assertRaises(watches.WatchError):
            watches.get_watch(ids[0])


class WatchEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="volta-watch-http-"))
        cls.original = watches._DB_PATH
        watches.configure(cls.tmp / "watches.db")
        cls.httpd = server.VoltaHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        watches.configure(cls.original)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def http(self, method: str, path: str, body: Any = None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            with err:
                return err.code, json.loads(err.read().decode("utf-8"))

    def test_config_is_public_and_writes_are_closed_by_default(self) -> None:
        status, config = self.http("GET", "/api/watches/config")
        self.assertEqual(status, 200)
        self.assertEqual(config["enabled"], server.WATCHES_ENABLED)
        self.assertFalse(config["push_configured"])
        self.assertEqual(config["eligible_feeds"], ["endolla", "spirii"])
        with patch("server.WATCHES_ENABLED", False):
            status, payload = self.http("POST", "/api/watches", {"station_id": "x"})
        self.assertEqual((status, payload["error"]), (503, "watches_disabled"))

    def test_create_read_cancel_round_trip(self) -> None:
        live = row({"c1": "CHARGING"})
        body = {"station_id": live["id"], "lat": live["lat"], "lon": live["lon"], "device_token": TOKEN}
        with patch("server.WATCHES_ENABLED", True), patch("server.watch_lookup", return_value=live):
            status, created = self.http("POST", "/api/watches", body)
            self.assertEqual(status, 201, created)
            watch_id = created["watch"]["id"]
            self.assertFalse(created["watch"]["push_configured"])
            self.assertNotIn(TOKEN, json.dumps(created))
            status, read = self.http("GET", f"/api/watches/{watch_id}")
            self.assertEqual((status, read["watch"]["active"]), (200, True))
            status, cancelled = self.http("DELETE", f"/api/watches/{watch_id}")
            self.assertEqual((status, cancelled["watch"]["end_reason"]), (200, "cancelled"))
            status, missing = self.http("GET", "/api/watches/" + "z" * 24)
            self.assertEqual(status, 404)

    def test_watch_lookup_matches_ids_strictly(self) -> None:
        payload = {"stations": [dict(row({"c1": "AVAILABLE"}), id="near-but-other", merged_ids=["old-id"])]}
        with patch("server.live_response", return_value=(200, payload)):
            self.assertIsNone(server.watch_lookup("intel_endolla_boqueria", 41.38, 2.17))
            self.assertEqual(server.watch_lookup("old-id", 41.38, 2.17)["id"], "near-but-other")

    def test_module_is_in_the_runtime_closure(self) -> None:
        sys.path.insert(0, str(ROOT / "scripts"))
        import runtime_closure  # noqa: E402
        self.assertIn("watches.py", runtime_closure.RUNTIME_FILES)


if __name__ == "__main__":
    unittest.main()
