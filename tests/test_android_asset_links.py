#!/usr/bin/env python3
"""Digital Asset Links for the Android app (a Trusted Web Activity).

Chrome opens the Android app full screen only when https://voltago.app/.well-known/assetlinks.json
names the app's package and signing certificate, is served with HTTP 200 and a JSON content
type, and is not redirected. These tests keep the file, the Android build and the server in step.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server

ASSET_LINKS = ROOT / "frontend" / "public" / ".well-known" / "assetlinks.json"
APP_GRADLE = ROOT / "android" / "app" / "build.gradle.kts"
FINGERPRINT = re.compile(r"^(?:[0-9A-F]{2}:){31}[0-9A-F]{2}$")


def gradle_value(name: str) -> str:
    match = re.search(rf'^\s*(?:val\s+)?{name}\s*=\s*"([^"]+)"', APP_GRADLE.read_text(encoding="utf-8"), re.M)
    if not match:
        raise AssertionError(f"{name} missing from {APP_GRADLE}")
    return match.group(1)


class AssetLinksSourceTests(unittest.TestCase):
    def test_statement_names_the_android_app_and_its_certificates(self) -> None:
        statements = json.loads(ASSET_LINKS.read_text(encoding="utf-8"))
        self.assertIsInstance(statements, list)
        self.assertEqual(len(statements), 1)
        statement = statements[0]
        self.assertEqual(statement["relation"], ["delegate_permission/common.handle_all_urls"])
        target = statement["target"]
        self.assertEqual(target["namespace"], "android_app")
        self.assertEqual(target["package_name"], gradle_value("applicationId"))
        fingerprints = target["sha256_cert_fingerprints"]
        self.assertGreaterEqual(len(fingerprints), 1)
        self.assertEqual(len(set(fingerprints)), len(fingerprints))
        for fingerprint in fingerprints:
            self.assertRegex(fingerprint, FINGERPRINT)

    def test_android_app_trusts_the_host_that_serves_the_file(self) -> None:
        self.assertEqual(gradle_value("twaHost"), "voltago.app")


class AssetLinksServingTests(unittest.TestCase):
    def test_served_as_json_without_redirect_or_app_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "index.html").write_text('<div id="root">SPA SHELL</div>', encoding="utf-8")
            (root / ".well-known").mkdir()
            shutil.copy(ASSET_LINKS, root / ".well-known" / "assetlinks.json")

            with mock.patch.object(server, "WEB_DIST", root):
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                thread.start()
                try:
                    url = f"http://127.0.0.1:{httpd.server_port}/.well-known/assetlinks.json"
                    request = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
                    with urllib.request.urlopen(request, timeout=3) as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.geturl(), url)
                        self.assertEqual(response.headers.get_content_type(), "application/json")
                        # Small enough to stay uncompressed for every Digital Asset Links client.
                        self.assertIsNone(response.headers.get("Content-Encoding"))
                        body = response.read()
                    self.assertEqual(json.loads(body), json.loads(ASSET_LINKS.read_bytes()))
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                    thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
