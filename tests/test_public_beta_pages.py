#!/usr/bin/env python3
"""Contracts for the distinct public pages required by TestFlight beta review."""

from __future__ import annotations

import tempfile
import threading
import unittest
import urllib.request
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from http.server import ThreadingHTTPServer

import server


PUBLIC = ROOT / "frontend" / "public"
CONTACT = "arsik2121@icloud.com"


class PublicBetaPageSourceTests(unittest.TestCase):
    def test_pages_are_distinct_complete_and_contactable(self) -> None:
        pages = {}
        for route in ("privacy", "terms", "support"):
            path = PUBLIC / route / "index.html"
            self.assertTrue(path.is_file(), path)
            html = path.read_text(encoding="utf-8")
            pages[route] = html
            self.assertNotIn(CONTACT, html)
            self.assertNotIn("mailto:", html)
            self.assertIn('aria-label="arsik2121 at icloud dot com"', html)
            self.assertIn('<span class="email-at" aria-hidden="true"></span>', html)
            self.assertIn("<span>arsik2121</span>", html)
            self.assertIn("<span>icloud.com</span>", html)
            self.assertIn('name="viewport"', html)
            self.assertIn("Volta Go", html)
            self.assertNotIn("[INSERT", html)
            self.assertNotIn("DO NOT PUBLISH", html)
            self.assertNotIn("<script", html.lower())

        self.assertEqual(len(set(pages.values())), 3)
        self.assertIn("location", pages["privacy"].lower())
        self.assertIn("search", pages["privacy"].lower())
        self.assertIn("ip address", pages["privacy"].lower())
        self.assertIn("retention", pages["privacy"].lower())
        self.assertIn("testflight", pages["terms"].lower())
        self.assertIn("do not use", pages["terms"].lower())
        self.assertIn("build number", pages["support"].lower())


class PublicBetaPageServingTests(unittest.TestCase):
    def test_extensionless_routes_serve_directory_indexes_not_spa_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "index.html").write_text("SPA SHELL", encoding="utf-8")
            for route in ("privacy", "terms", "support"):
                page_dir = root / route
                page_dir.mkdir()
                (page_dir / "index.html").write_text(f"PAGE {route}", encoding="utf-8")

            with mock.patch.object(server, "WEB_DIST", root):
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f"http://127.0.0.1:{httpd.server_port}"
                    for route in ("privacy", "terms", "support"):
                        with urllib.request.urlopen(f"{base}/{route}", timeout=3) as response:
                            self.assertEqual(response.status, 200)
                            self.assertEqual(response.headers.get_content_type(), "text/html")
                            self.assertEqual(response.read().decode("utf-8"), f"PAGE {route}")
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                    thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
