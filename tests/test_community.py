"""Unit tests for community.py (plan 013 slice B) — stdlib unittest only."""

import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import community


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class CommunityTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="volta-community-"))
        community.configure(db_path=self.tmp / "community.db", photos_dir=self.tmp / "photos")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(community.configure, self.tmp / "gone.db", self.tmp / "gone")

    def set_created_at(self, table: str, row_id: int, when: datetime) -> None:
        conn = sqlite3.connect(str(self.tmp / "community.db"))
        try:
            conn.execute(f"UPDATE {table} SET created_at=? WHERE id=?", (iso(when), row_id))
            conn.commit()
        finally:
            conn.close()
        community.configure()  # reset summary cache

    # --- check-ins -------------------------------------------------------

    def test_checkin_roundtrip_and_summary(self) -> None:
        result = community.record_checkin("st-1", "charged", "worked fine", client_ip="10.0.0.1")
        self.assertEqual(result["verdict"], "charged")
        summary = community.station_summary("st-1")
        self.assertIsNotNone(summary)
        self.assertEqual(summary["checkins"]["charged"], 1)
        self.assertEqual(summary["checkins"]["total"], 1)
        self.assertTrue(summary["recent_success"])
        self.assertFalse(summary["recent_failure"])

    def test_checkin_rejects_bad_verdict_and_missing_station(self) -> None:
        with self.assertRaises(community.CommunityError) as ctx:
            community.record_checkin("st-1", "great")
        self.assertEqual(ctx.exception.status, 400)
        with self.assertRaises(community.CommunityError):
            community.record_checkin("", "charged")

    def test_checkin_unknown_station_via_validator(self) -> None:
        with self.assertRaises(community.CommunityError) as ctx:
            community.record_checkin("ghost", "charged", known_station=lambda sid: sid == "real")
        self.assertEqual(ctx.exception.status, 404)
        community.record_checkin("real", "charged", known_station=lambda sid: sid == "real")

    def test_note_sanitized_and_capped(self) -> None:
        community.record_checkin("st-2", "failed", "bad\x00\x1fnote  " + "x" * 500)
        conn = sqlite3.connect(str(self.tmp / "community.db"))
        try:
            note = conn.execute("SELECT note FROM checkins").fetchone()[0]
        finally:
            conn.close()
        self.assertNotIn("\x00", note)
        self.assertLessEqual(len(note), community.NOTE_MAX)
        self.assertTrue(note.startswith("bad note"))

    def test_daily_cap_enforced(self) -> None:
        original = community.CHECKIN_DAILY_CAP
        community.CHECKIN_DAILY_CAP = 2
        try:
            community.record_checkin("st-3", "charged", client_ip="1.1.1.1")
            community.record_checkin("st-3", "charged", client_ip="1.1.1.1")
            with self.assertRaises(community.CommunityError) as ctx:
                community.record_checkin("st-3", "charged", client_ip="1.1.1.1")
            self.assertEqual(ctx.exception.status, 429)
            # A different client is not affected.
            community.record_checkin("st-3", "charged", client_ip="2.2.2.2")
        finally:
            community.CHECKIN_DAILY_CAP = original

    def test_client_ip_never_stored_raw(self) -> None:
        community.record_checkin("st-4", "charged", client_ip="203.0.113.9")
        conn = sqlite3.connect(str(self.tmp / "community.db"))
        try:
            client = conn.execute("SELECT client FROM checkins").fetchone()[0]
        finally:
            conn.close()
        self.assertNotIn("203.0.113.9", client)
        self.assertEqual(len(client), 16)

    def test_daily_caps_hold_for_concurrent_submissions(self) -> None:
        conn = community._connect()
        try:
            community.client_token(conn, "concurrent-client")
        finally:
            conn.close()
        original_token = community.client_token
        original_cap = community._enforce_daily_cap
        writers = (
            ("CHECKIN_DAILY_CAP", lambda: community.record_checkin("st-cap", "offline", client_ip="concurrent-client")),
            ("PHOTO_DAILY_CAP", lambda: community.save_photo("st-cap", b"\xff\xd8\xff" + b"p" * 32, client_ip="concurrent-client")),
            ("SUGGESTION_DAILY_CAP", lambda: community.record_suggestion("Suggested charger", 39.47, -0.38, client_ip="concurrent-client")),
        )
        for cap_name, submit in writers:
            with self.subTest(kind=cap_name):
                ready = threading.Barrier(4)

                def synchronized_token(*args):
                    token = original_token(*args)
                    ready.wait(timeout=5)
                    return token

                def delayed_cap(*args):
                    original_cap(*args)
                    # Widen the interval between checking the cap and inserting.
                    time.sleep(0.05)

                def write(_):
                    try:
                        submit()
                        return "accepted"
                    except community.CommunityError as exc:
                        return exc.code

                with (
                    patch.object(community, cap_name, 1),
                    patch.object(community, "client_token", side_effect=synchronized_token),
                    patch.object(community, "_enforce_daily_cap", side_effect=delayed_cap),
                    ThreadPoolExecutor(max_workers=4) as pool,
                ):
                    outcomes = list(pool.map(write, range(4)))
                self.assertEqual(outcomes.count("accepted"), 1)
                self.assertEqual(outcomes.count("daily_cap"), 3)

    # --- photos ----------------------------------------------------------

    def test_photo_magic_bytes_and_serving(self) -> None:
        jpeg = b"\xff\xd8\xff\xe0" + b"j" * 64
        saved = community.save_photo("st-5", jpeg)
        self.assertTrue(saved["url"].endswith(f"{saved['id']}.jpg"))
        resolved = community.photo_file(f"{saved['id']}.jpg")
        self.assertIsNotNone(resolved)
        path, mime = resolved
        self.assertEqual(mime, "image/jpeg")
        self.assertEqual(path.read_bytes(), jpeg)
        # GIF (unsupported) is rejected.
        with self.assertRaises(community.CommunityError) as ctx:
            community.save_photo("st-5", b"GIF89a" + b"g" * 32)
        self.assertEqual(ctx.exception.status, 415)

    def test_photo_size_cap(self) -> None:
        original = community.PHOTO_MAX_BYTES
        community.PHOTO_MAX_BYTES = 100
        try:
            with self.assertRaises(community.CommunityError) as ctx:
                community.save_photo("st-5", b"\xff\xd8\xff" + b"x" * 200)
            self.assertEqual(ctx.exception.status, 413)
        finally:
            community.PHOTO_MAX_BYTES = original

    def test_removed_photo_not_served(self) -> None:
        saved = community.save_photo("st-6", b"\x89PNG\r\n\x1a\n" + b"p" * 16)
        community.moderate("photo", saved["id"], "remove")
        self.assertIsNone(community.photo_file(f"{saved['id']}.png"))
        # Traversal-shaped names never resolve.
        self.assertIsNone(community.photo_file("../secret.jpg"))

    # --- suggestions -----------------------------------------------------

    def test_suggestion_validation_and_moderation(self) -> None:
        with self.assertRaises(community.CommunityError):
            community.record_suggestion("ab", 39.5, -0.4)
        with self.assertRaises(community.CommunityError) as ctx:
            community.record_suggestion("Paris point", 48.85, 2.35)
        self.assertEqual(ctx.exception.code, "outside_spain")

        pending = community.record_suggestion("Mercadona parking charger", 39.47, -0.38, note="2x Type2 free")
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(community.stats()["suggestions_pending"], 1)

        community.moderate("suggestion", pending["id"], "accept")
        data = community.summaries()
        self.assertEqual(data["totals"]["suggestions_accepted"], 1)
        self.assertEqual(data["accepted_suggestions"][0]["name"], "Mercadona parking charger")
        with self.assertRaises(community.CommunityError):
            community.moderate("suggestion", 9999, "accept")
        with self.assertRaises(community.CommunityError):
            community.moderate("suggestion", pending["id"], "explode")

    # --- honesty wiring --------------------------------------------------

    def test_failures_downgrade_go_but_success_never_upgrades(self) -> None:
        now = datetime.now(timezone.utc)
        ok = community.record_checkin("st-7", "charged")
        self.set_created_at("checkins", ok["id"], now - timedelta(days=5))
        first = community.record_checkin("st-7", "failed")
        self.set_created_at("checkins", first["id"], now - timedelta(days=2))
        second = community.record_checkin("st-7", "offline")
        self.set_created_at("checkins", second["id"], now - timedelta(days=1))

        summary = community.station_summary("st-7")
        self.assertTrue(summary["recent_failure"])
        self.assertGreaterEqual(summary["recent_failure_count"], 2)

        go_item = {"decision": {"level": "go", "label": "Go now", "reason": "live"}, "verification": {"live_verified": True}}
        community.apply_community_evidence(go_item, summary)
        self.assertEqual(go_item["decision"]["level"], "check")
        self.assertTrue(go_item["community_caution"])
        # live_verified untouched — community evidence is never live authority.
        self.assertTrue(go_item["verification"]["live_verified"])

        verify_item = {"decision": {"level": "verify", "label": "Verify first", "reason": "lead"}, "verification": {}}
        success_summary = {"checkins": {"total": 1}, "recent_success": True, "recent_failure": False, "photos": []}
        community.apply_community_evidence(verify_item, success_summary)
        self.assertEqual(verify_item["decision"]["level"], "verify")

    def test_success_after_failures_clears_downgrade(self) -> None:
        now = datetime.now(timezone.utc)
        for days, verdict in ((6, "failed"), (5, "failed")):
            row = community.record_checkin("st-8", verdict)
            self.set_created_at("checkins", row["id"], now - timedelta(days=days))
        ok = community.record_checkin("st-8", "charged")
        self.set_created_at("checkins", ok["id"], now - timedelta(days=1))
        summary = community.station_summary("st-8")
        self.assertFalse(summary["recent_failure"])
        self.assertTrue(summary["recent_success"])

    def test_failures_before_latest_success_do_not_count_toward_new_downgrade(self) -> None:
        now = datetime.now(timezone.utc)
        for days, verdict in ((6, "failed"), (4, "charged"), (2, "offline")):
            row = community.record_checkin("st-recovered", verdict)
            self.set_created_at("checkins", row["id"], now - timedelta(days=days))

        summary = community.station_summary("st-recovered")
        self.assertTrue(summary["recent_failure"])
        self.assertEqual(summary["recent_failure_count"], 1)
        item = {"decision": {"level": "go"}, "verification": {"live_verified": True}}
        community.apply_community_evidence(item, summary)
        self.assertEqual(item["decision"]["level"], "go")
        self.assertTrue(item["community_caution"])

        row = community.record_checkin("st-recovered", "blocked")
        self.set_created_at("checkins", row["id"], now - timedelta(days=1))
        summary = community.station_summary("st-recovered")
        self.assertEqual(summary["recent_failure_count"], 2)
        community.apply_community_evidence(item, summary)
        self.assertEqual(item["decision"]["level"], "check")
        self.assertTrue(item["verification"]["live_verified"])

    def test_inflight_summary_cannot_recache_evidence_from_before_write(self) -> None:
        community.record_checkin("st-race", "offline")
        snapshot_ready = threading.Event()
        release_read = threading.Event()
        read_summary = community._summaries_uncached

        def delayed_summary():
            data = read_summary()
            snapshot_ready.set()
            if not release_read.wait(timeout=5):
                raise AssertionError("summary test did not release the reader")
            return data

        with ThreadPoolExecutor(max_workers=1) as pool:
            with patch.object(community, "_summaries_uncached", side_effect=delayed_summary):
                pending = pool.submit(community.summaries)
                try:
                    self.assertTrue(snapshot_ready.wait(timeout=5))
                    community.record_checkin("st-race", "failed")
                finally:
                    release_read.set()
                pending.result(timeout=5)

        # A request that started before the write may finish with its snapshot.
        # Requests after the write must not reuse that superseded snapshot.
        summary = community.station_summary("st-race")
        self.assertEqual(summary["checkins"]["total"], 2)
        self.assertEqual(summary["recent_failure_count"], 2)

    def test_failure_aggregation_preserves_window_recovery_and_moderation(self) -> None:
        now = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
        cutoff = now - timedelta(days=community.DOWNGRADE_WINDOW_DAYS)
        recent = now - timedelta(days=1)
        cases = {
            "window": [("failed", cutoff - timedelta(seconds=1), "active"),
                       ("offline", cutoff, "active"),
                       ("blocked", cutoff + timedelta(seconds=1), "active")],
            "equal-success": [("charged", recent, "active"), ("failed", recent, "active")],
            "recovered": [("charged", cutoff, "active"), ("failed", recent, "active"),
                          ("charged", recent + timedelta(seconds=1), "active"),
                          ("offline", recent + timedelta(seconds=2), "active")],
            "hidden-success": [("failed", recent, "active"), ("charged", now, "hidden")],
            "hidden-failure": [("occupied", recent, "active"), ("failed", now, "hidden")],
            "old-only": [("failed", cutoff - timedelta(seconds=1), "active")],
        }
        conn = community._connect()
        try:
            conn.executemany(
                "INSERT INTO checkins(station_id,verdict,created_at,client,status) VALUES(?,?,?,?,?)",
                [(sid, verdict, iso(when), "fixture", status)
                 for sid, rows in cases.items() for verdict, when, status in rows],
            )
            conn.execute(
                "INSERT INTO photos(station_id,ext,size,created_at,client) VALUES(?,?,?,?,?)",
                ("photo-only", "jpg", 1, iso(now), "fixture"),
            )
            conn.commit()
        finally:
            conn.close()
        with patch.object(community, "datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            summaries = community._summaries_uncached()["stations"]
        expected = {"window": 2, "equal-success": 0, "recovered": 1,
                    "hidden-success": 1, "hidden-failure": 0, "old-only": 0, "photo-only": 0}
        for sid, count in expected.items():
            with self.subTest(station=sid):
                self.assertEqual(summaries[sid]["recent_failure_count"], count)
                self.assertEqual(summaries[sid]["recent_failure"], bool(count))
        self.assertEqual(summaries["window"]["checkins"]["total"], 3)
        self.assertEqual(summaries["photo-only"]["checkins"]["total"], 0)
        self.assertFalse(summaries["hidden-success"]["recent_success"])

    def test_summary_query_count_does_not_grow_with_station_count(self) -> None:
        now = iso(datetime.now(timezone.utc))
        connect = community._connect
        queries = []

        def traced_connect():
            connection = connect()
            connection.set_trace_callback(queries.append)
            return connection

        def measure(station_count):
            conn = connect()
            try:
                conn.execute("DELETE FROM checkins")
                conn.executemany(
                    "INSERT INTO checkins(station_id,verdict,created_at,client) VALUES(?,?,?,?)",
                    [(str(i), "failed", now, "fixture") for i in range(station_count)],
                )
                conn.commit()
            finally:
                conn.close()
            queries.clear()
            with patch.object(community, "_connect", side_effect=traced_connect):
                summary = community._summaries_uncached()
            self.assertEqual(len(summary["stations"]), station_count)
            self.assertTrue(all(row["recent_failure_count"] == 1 for row in summary["stations"].values()))
            return sum(query.lstrip().upper().startswith("SELECT") for query in queries)

        small_count = measure(2)
        large_count = measure(100)
        self.assertEqual(large_count, small_count)
        self.assertLessEqual(large_count, 8)

    def test_free_verification_ladder(self) -> None:
        live_item = {"verification": {"live_verified": True}}
        self.assertEqual(community.free_verification_level(live_item, None), "live")
        plain = {"verification": {}, "corroboration": {"independent_sources": 1}}
        self.assertEqual(
            community.free_verification_level(plain, {"recent_success": True}), "community"
        )
        corroborated = {"verification": {}, "corroboration": {"independent_sources": 3}}
        self.assertEqual(community.free_verification_level(corroborated, None), "corroborated")
        self.assertEqual(community.free_verification_level(plain, None), "reported")


if __name__ == "__main__":
    unittest.main()
