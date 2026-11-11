#!/usr/bin/env python3
"""Contracts for the distinct public pages required by TestFlight beta review."""

from __future__ import annotations

import re
import tempfile
import unittest
import urllib.error
import urllib.request
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from http.server import ThreadingHTTPServer

import server
from tests import _support  # noqa: E402


def setUpModule():
    _support.isolate_community_store()


PUBLIC = ROOT / "frontend" / "public"


def support_email() -> tuple[str, str, str]:
    text = (ROOT / "frontend" / "src" / "lib" / "supportContact.ts").read_text(encoding="utf-8")
    match = re.search(r"^export const SUPPORT_EMAIL = '([^']+)'$", text, re.M)
    if not match:
        raise AssertionError("SUPPORT_EMAIL constant missing")
    email = match.group(1)
    local, domain = email.split("@", 1)
    return email, local, domain


class PublicBetaPageSourceTests(unittest.TestCase):
    def test_pages_are_distinct_complete_and_contactable(self) -> None:
        email, local, domain = support_email()
        aria = f'{local} at {domain.replace(".", " dot ")}'
        pages = {}
        for route in ("privacy", "terms", "support"):
            path = PUBLIC / route / "index.html"
            self.assertTrue(path.is_file(), path)
            html = path.read_text(encoding="utf-8")
            pages[route] = html
            self.assertNotIn(email, html)
            self.assertNotIn("icloud.com", html)
            self.assertNotIn("mailto:", html)
            self.assertIn(f'aria-label="{aria}"', html)
            self.assertIn('<span class="email-at" aria-hidden="true"></span>', html)
            self.assertIn(f"<span>{local}</span>", html)
            self.assertIn(f"<span>{domain}</span>", html)
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
        self.assertNotIn("testflight beta", pages["terms"].lower())
        self.assertIn("do not use", pages["terms"].lower())
        self.assertIn("build number", pages["support"].lower())
        self.assertIn("smartcar", pages["privacy"].lower())
        self.assertIn("vehicle connection is enabled", pages["privacy"].lower())
        self.assertIn("saved chargers", pages["privacy"].lower())
        self.assertNotIn("icloud", pages["privacy"].lower())

    def test_support_address_matches_the_constant_everywhere(self) -> None:
        email, _, domain = support_email()
        self.assertTrue(domain.endswith("voltago.app"), email)
        swift = (ROOT / "ios" / "Volta" / "Volta" / "SettingsView.swift").read_text(encoding="utf-8")
        self.assertIn("https://voltago.app/support", swift)
        self.assertNotIn("icloud.com", swift)
        for relative in (
            "docs/appstore/metadata.md",
            "docs/appstore/support.md",
            "docs/appstore/privacy.md",
            "docs/appstore/terms.md",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn(email, text, relative)
            self.assertNotIn("icloud.com", text, relative)


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
                stop_server = _support.serve_in_thread(httpd)
                try:
                    base = f"http://127.0.0.1:{httpd.server_port}"
                    for route in ("privacy", "terms", "support"):
                        with urllib.request.urlopen(f"{base}/{route}", timeout=3) as response:
                            self.assertEqual(response.status, 200)
                            self.assertEqual(response.headers.get_content_type(), "text/html")
                            self.assertEqual(response.read().decode("utf-8"), f"PAGE {route}")
                finally:
                    stop_server()

    def test_about_and_pricing_are_real_404s_and_matrix_stays_the_app(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "index.html").write_text('<div id="root">SPA SHELL</div>', encoding="utf-8")
            page_dir = root / "privacy"
            page_dir.mkdir()
            (page_dir / "index.html").write_text("PAGE privacy", encoding="utf-8")

            with mock.patch.object(server, "WEB_DIST", root):
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                stop_server = _support.serve_in_thread(httpd)
                try:
                    base = f"http://127.0.0.1:{httpd.server_port}"

                    def body(path: str, method: str = "GET") -> tuple[int, str]:
                        request = urllib.request.Request(base + path, method=method)
                        try:
                            with urllib.request.urlopen(request, timeout=3) as response:
                                return response.status, response.read().decode("utf-8")
                        except urllib.error.HTTPError as error:
                            return error.code, error.read().decode("utf-8")

                    for path in ("/about", "/about/", "/pricing", "/pricing/", "/not-a-page"):
                        status, payload = body(path)
                        self.assertEqual(status, 404, path)
                        self.assertIn("404", payload, path)
                        self.assertNotIn("SPA SHELL", payload, path)
                        self.assertNotIn('id="root"', payload, path)
                        status, payload = body(path, "HEAD")
                        self.assertEqual(status, 404, f"HEAD {path}")
                        self.assertNotIn("SPA SHELL", payload)
                    for path in ("/matrix", "/matrix/"):
                        for method in ("GET", "HEAD"):
                            status, payload = body(path, method)
                            self.assertEqual(status, 200, f"{method} {path}")
                            if method == "GET":
                                self.assertIn("SPA SHELL", payload, path)
                    status, payload = body("/privacy")
                    self.assertEqual((status, payload), (200, "PAGE privacy"))
                    status, home = body("/")
                    self.assertEqual(status, 200)
                    self.assertIn("SPA SHELL", home)
                finally:
                    stop_server()


if __name__ == "__main__":
    unittest.main()
