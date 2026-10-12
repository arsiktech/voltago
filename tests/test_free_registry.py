"""Endpoint + honesty tests for the plan-013 free registry and community API.

Stdlib only. Spins a real VoltaHTTPServer on an ephemeral port; never touches
live upstream sources (the live /api/chargers path is exercised elsewhere —
here apply_free_only_filter is tested as a pure function).
"""

import json
import shutil
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import community
import server


class FreeRegistryTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="volta-free-registry-"))
        community.configure(db_path=cls.tmp / "community.db", photos_dir=cls.tmp / "photos")
        cls.httpd = server.VoltaHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        records = server.load_intelligence()["records"]
        cls.free_id = next(r["id"] for r in records if server.free_row(r))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)
        community.configure(cls.tmp / "gone.db", cls.tmp / "gone")

    def request(self, path: str, method: str = "GET", body: bytes = None, headers: dict = None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=body, method=method,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as err:
            return err.code, err.read(), dict(err.headers)

    def get_json(self, path: str):
        status, body, _ = self.request(path)
        return status, json.loads(body.decode("utf-8"))

    def post_json(self, path: str, payload: dict):
        status, body, _ = self.request(path, "POST", json.dumps(payload).encode("utf-8"))
        return status, json.loads(body.decode("utf-8"))

    # --- registry --------------------------------------------------------

    def test_registry_serves_only_free_rows_with_ladder(self) -> None:
        status, payload = self.get_json("/api/free-registry")
        self.assertEqual(status, 200)
        self.assertGreater(payload["stats"]["free_total"], 0)
        self.assertEqual(payload["stats"]["free_total"], len(payload["stations"]))
        for row in payload["stations"]:
            self.assertTrue(row.get("is_free") is True or row.get("price_min") == 0, row.get("id"))
            level = (row.get("free_verification") or {}).get("level")
            self.assertIn(level, ("live", "community", "corroborated", "reported"))
            self.assertNotIn("raw", row)  # compact by default
        # Ladder totals agree with rows.
        by_level = payload["stats"]["by_level"]
        self.assertEqual(sum(by_level.values()), payload["stats"]["free_total"])
        self.assertIn("honesty", payload)

    def test_registry_bbox_narrows(self) -> None:
        _, national = self.get_json("/api/free-registry")
        _, valencia = self.get_json("/api/free-registry?bbox=39.2,-0.7,39.8,0.0")
        self.assertLess(valencia["stats"]["free_total"], national["stats"]["free_total"])
        self.assertGreater(valencia["stats"]["free_total"], 0)

    # --- community flow --------------------------------------------------

    def test_checkin_flow_reaches_registry_ladder(self) -> None:
        status, response = self.post_json(
            "/api/community/checkin",
            {"station_id": self.free_id, "verdict": "charged", "note": "worked"},
        )
        self.assertEqual(status, 201, response)
        status, summary = self.get_json(f"/api/community/summary?ids={self.free_id}")
        self.assertEqual(status, 200)
        self.assertEqual(summary["stations"][self.free_id]["checkins"]["charged"], 1)

        status, registry = self.get_json("/api/free-registry")
        self.assertEqual(status, 200)
        row = next(r for r in registry["stations"] if r["id"] == self.free_id)
        self.assertTrue(row["community"]["recent_success"])
        self.assertEqual(row["free_verification"]["level"], "community")
        # Honesty invariant: community success NEVER becomes live authority.
        self.assertFalse((row.get("verification") or {}).get("live_verified", False))

    def test_checkin_unknown_station_404(self) -> None:
        status, response = self.post_json(
            "/api/community/checkin", {"station_id": "ghost-station", "verdict": "charged"}
        )
        self.assertEqual(status, 404)
        self.assertEqual(response["error"], "unknown_station")

    def test_checkin_bad_verdict_400(self) -> None:
        status, response = self.post_json(
            "/api/community/checkin", {"station_id": self.free_id, "verdict": "amazing"}
        )
        self.assertEqual(status, 400)

    def test_photo_upload_and_serving(self) -> None:
        jpeg = b"\xff\xd8\xff\xe0" + b"volta" * 10
        status, body, _ = self.request(
            f"/api/community/photo?station_id={self.free_id}", "POST", jpeg,
            headers={"Content-Type": "image/jpeg"},
        )
        self.assertEqual(status, 201, body)
        photo = json.loads(body)["photo"]
        status, served, headers = self.request(photo["url"])
        self.assertEqual(status, 200)
        self.assertEqual(served, jpeg)
        self.assertEqual(headers.get("Content-Type"), "image/jpeg")

    def test_suggestion_pending_and_moderation_fail_closed(self) -> None:
        status, response = self.post_json(
            "/api/community/station",
            {"name": "Ayuntamiento free charger", "lat": 39.47, "lon": -0.38, "note": "2x Type2"},
        )
        self.assertEqual(status, 201, response)
        self.assertEqual(response["suggestion"]["status"], "pending")
        # Without VOLTA_ADMIN_TOKEN, HTTP moderation is disabled — fail closed.
        status, response = self.post_json(
            "/api/community/moderate", {"kind": "suggestion", "id": 1, "action": "accept"}
        )
        self.assertEqual(status, 403)
        self.assertEqual(response["error"], "moderation_disabled")
        # Pending suggestions are counted but NOT merged into registry stations.
        _, registry = self.get_json("/api/free-registry")
        self.assertGreaterEqual(registry["community_suggestions"]["pending_count"], 1)
        self.assertFalse(any(str(r.get("id", "")).startswith("community_suggestion") for r in registry["stations"]))

    def test_community_writes_fail_closed_without_persistent_storage(self) -> None:
        before = community.station_summary(self.free_id)
        before_total = ((before or {}).get("checkins") or {}).get("total", 0)
        original = server.COMMUNITY_WRITES_ENABLED
        server.COMMUNITY_WRITES_ENABLED = False
        try:
            status, response = self.post_json(
                "/api/community/checkin",
                {"station_id": self.free_id, "verdict": "charged"},
            )
            moderation_status, moderation_response = self.post_json(
                "/api/community/moderate",
                {"kind": "suggestion", "id": 1, "action": "accept"},
            )
        finally:
            server.COMMUNITY_WRITES_ENABLED = original

        self.assertEqual(status, 503)
        self.assertEqual(response["error"], "community_writes_disabled")
        self.assertEqual(moderation_status, 503)
        self.assertEqual(moderation_response["error"], "community_writes_disabled")
        after = community.station_summary(self.free_id)
        after_total = ((after or {}).get("checkins") or {}).get("total", 0)
        self.assertEqual(before_total, after_total)

    def test_fly_deploy_explicitly_disables_ephemeral_community_writes(self) -> None:
        fly = (Path(__file__).resolve().parents[1] / "fly.toml").read_text(encoding="utf-8")
        self.assertIn('VOLTA_COMMUNITY_WRITES_ENABLED = "false"', fly)

    # --- pure helpers ----------------------------------------------------

    def test_apply_free_only_filter_is_honest(self) -> None:
        payload = {
            "stations": [
                {"id": "a", "is_free": True, "price_min": 0},
                {"id": "b", "price_min": 0.18},
                {"id": "c", "price_min": None},  # unknown price is NOT free
            ],
            "leads": [{"id": "d", "price_min": 0}],
            "meta": {},
        }
        out = server.apply_free_only_filter(payload)
        self.assertEqual([r["id"] for r in out["stations"]], ["a"])
        self.assertEqual([r["id"] for r in out["leads"]], ["d"])
        self.assertEqual(out["meta"]["free_only"]["dropped_stations"], 2)

    def test_known_station_ids_covers_intel(self) -> None:
        self.assertIn(self.free_id, server.known_station_ids())


if __name__ == "__main__":
    unittest.main()
