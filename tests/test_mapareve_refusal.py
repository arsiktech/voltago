#!/usr/bin/env python3
"""A MapaREVE refusal (403/429) pauses every request, then allows one probe."""
from __future__ import annotations

import io
import json
import sys
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server  # noqa: E402


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def ok(*_args, **_kwargs):
    return _Response(json.dumps({"data": [], "pagination": {}}).encode())


def refused(code: int = 403):
    def raise_refusal(request, **_kwargs):
        raise urllib.error.HTTPError(request.full_url, code, "Forbidden", {}, None)
    return raise_refusal


class MapaReveRefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reset()
        self.clock = 1_000_000.0
        clock = patch("server.time.time", side_effect=lambda: self.clock)
        clock.start()
        self.addCleanup(clock.stop)
        self.addCleanup(self.reset)

    @staticmethod
    def reset() -> None:
        server._MAPAREVE_BLOCK.update(until=0.0, pause=0.0, probing=False)

    def fetch(self):
        return server._fetch_mapareve_page(1, b"{}")

    def test_refusal_pauses_every_later_request_without_network(self) -> None:
        with patch("server.public_get_urlopen", side_effect=refused()):
            with self.assertRaises(urllib.error.HTTPError):
                self.fetch()
        status = server.mapareve_block_status()
        self.assertTrue(status["refused"])
        self.assertEqual(status["pause_seconds"], int(server.MAPAREVE_BLOCK_PAUSE_SECONDS))
        with patch("server.public_get_urlopen") as network:
            with self.assertRaises(server.MapaReveRefused):
                self.fetch()
            network.assert_not_called()

    def test_429_is_a_refusal_but_other_errors_are_not(self) -> None:
        with patch("server.public_get_urlopen", side_effect=refused(429)):
            with self.assertRaises(urllib.error.HTTPError):
                self.fetch()
        self.assertTrue(server.mapareve_block_status()["refused"])
        self.reset()
        for error in (refused(500), TimeoutError("slow")):
            with self.subTest(error=error), patch("server.public_get_urlopen", side_effect=error):
                with self.assertRaises(Exception):
                    self.fetch()
                self.assertFalse(server.mapareve_block_status()["refused"])

    def test_after_the_pause_one_successful_probe_clears_the_refusal(self) -> None:
        with patch("server.public_get_urlopen", side_effect=refused()):
            with self.assertRaises(urllib.error.HTTPError):
                self.fetch()
        self.clock += server.MAPAREVE_BLOCK_PAUSE_SECONDS + 1
        with patch("server.public_get_urlopen", side_effect=ok):
            self.assertEqual(self.fetch(), {"data": [], "pagination": {}})
        self.assertFalse(server.mapareve_block_status()["refused"])

    def test_refused_probe_doubles_the_pause_up_to_the_maximum(self) -> None:
        pauses = []
        with patch("server.public_get_urlopen", side_effect=refused()):
            for _ in range(6):
                with self.assertRaises(urllib.error.HTTPError):
                    self.fetch()
                pauses.append(server.mapareve_block_status()["pause_seconds"])
                self.clock += server._MAPAREVE_BLOCK["pause"] + 1
        base = int(server.MAPAREVE_BLOCK_PAUSE_SECONDS)
        self.assertEqual(pauses[:3], [base, base * 2, base * 4])
        self.assertEqual(max(pauses), int(server.MAPAREVE_BLOCK_PAUSE_MAX_SECONDS))

    def test_probe_timeout_waits_one_more_pause_without_doubling(self) -> None:
        with patch("server.public_get_urlopen", side_effect=refused()):
            with self.assertRaises(urllib.error.HTTPError):
                self.fetch()
        self.clock += server.MAPAREVE_BLOCK_PAUSE_SECONDS + 1
        with patch("server.public_get_urlopen", side_effect=TimeoutError("slow")):
            with self.assertRaises(TimeoutError):
                self.fetch()
        status = server.mapareve_block_status()
        self.assertTrue(status["refused"])
        self.assertEqual(status["pause_seconds"], int(server.MAPAREVE_BLOCK_PAUSE_SECONDS))
        with self.assertRaises(server.MapaReveRefused):
            self.fetch()

    def test_only_one_probe_goes_out_at_a_time(self) -> None:
        with patch("server.public_get_urlopen", side_effect=refused()):
            with self.assertRaises(urllib.error.HTTPError):
                self.fetch()
        self.clock += server.MAPAREVE_BLOCK_PAUSE_SECONDS + 1
        entered, release = threading.Event(), threading.Event()

        def slow_ok(*args, **kwargs):
            entered.set()
            release.wait(5)
            return ok()

        results = []
        with patch("server.public_get_urlopen", side_effect=slow_ok) as network:
            probe = threading.Thread(target=lambda: results.append(self.fetch()))
            probe.start()
            self.assertTrue(entered.wait(5))
            with self.assertRaises(server.MapaReveRefused):
                self.fetch()
            release.set()
            probe.join(5)
            self.assertEqual(network.call_count, 1)
        self.assertEqual(len(results), 1)
        self.assertFalse(server.mapareve_block_status()["refused"])

    def test_a_refused_first_page_fails_the_live_fetch_and_source_health_reports_it(self) -> None:
        with patch("server.public_get_urlopen", side_effect=refused()):
            with self.assertRaises(urllib.error.HTTPError):
                server.fetch_mapareve(max_price=5.0)
        with patch("server.public_get_urlopen") as network:
            with self.assertRaises(server.MapaReveRefused):
                server.fetch_mapareve(max_price=5.0)
            network.assert_not_called()
        self.assertTrue(server.source_health_summary()["mapareve_refusal"]["refused"])


if __name__ == "__main__":
    unittest.main()
