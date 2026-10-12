#!/usr/bin/env python3
"""PWA/mobile shell checks for Volta (React SPA).

Ported 2026-07-02 from the retired vanilla static/ shell contract to the frontend/ SPA:
the source of truth is frontend/index.html + frontend/public/manifest.webmanifest, and the
served shell is the built SPA (hashed /assets/* bundles parsed from the served HTML).

Requires a running local server that serves the BUILT SPA, for example:
  npm --prefix frontend run build
  VOLTA_QA_BASE_URL=http://127.0.0.1:8773 python3 scripts/qa_pwa_shell.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
BASE_URL = os.environ.get("VOLTA_QA_BASE_URL", "http://127.0.0.1:8773").rstrip("/")


class HeadCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: List[Dict[str, str]] = []
        self.metas: List[Dict[str, str]] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, str | None]]) -> None:
        data = {key: value or "" for key, value in attrs}
        if tag == "link":
            self.links.append(data)
        elif tag == "meta":
            self.metas.append(data)
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data

    def link(self, rel: str) -> Dict[str, str] | None:
        rel = rel.lower()
        for item in self.links:
            tokens = item.get("rel", "").lower().split()
            if rel in tokens:
                return item
        return None

    def meta(self, **criteria: str) -> Dict[str, str] | None:
        for item in self.metas:
            if all(item.get(key) == value for key, value in criteria.items()):
                return item
        return None


def fetch(path: str, timeout: int = 10) -> Tuple[int, str, bytes]:
    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=timeout) as response:
        return response.status, response.headers.get("Content-Type", ""), response.read()


def check(condition: bool, message: str, failures: List[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: List[str] = []
    try:
        # --- Source-of-truth shell files (frontend/) ---
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        manifest = json.loads((FRONTEND / "public" / "manifest.webmanifest").read_text(encoding="utf-8"))
        icon_text = (FRONTEND / "public" / "volta-icon.svg").read_text(encoding="utf-8")
        main_source = (FRONTEND / "src" / "main.tsx").read_text(encoding="utf-8")
        service_worker = (FRONTEND / "public" / "sw.js").read_text(encoding="utf-8")
        parser = HeadCollector()
        parser.feed(html)

        viewport = parser.meta(name="viewport")
        description = parser.meta(name="description")
        manifest_link = parser.link("manifest")
        icon_link = parser.link("icon")

        check(bool(parser.title.strip()), "HTML title is missing", failures)
        check("Volta Go" in parser.title, "HTML title does not identify Volta Go", failures)
        check(viewport is not None, "mobile viewport meta is missing", failures)
        if viewport:
            content = viewport.get("content", "")
            check("width=device-width" in content, "viewport missing width=device-width", failures)
            check("initial-scale=1" in content, "viewport missing initial-scale=1", failures)
            check("viewport-fit=cover" in content, "viewport missing viewport-fit=cover for mobile safe areas", failures)
        theme_metas = [m for m in parser.metas if m.get("name") == "theme-color"]
        check(
            bool(theme_metas) and all(re.fullmatch(r"#[0-9a-fA-F]{6}", m.get("content", "")) for m in theme_metas),
            "theme-color metas missing or not solid hex colors",
            failures,
        )
        check(description is not None and "charging" in description.get("content", "").lower(), "description meta missing charging context", failures)
        check(manifest_link is not None and manifest_link.get("href") == "/manifest.webmanifest", "manifest link missing or not root-relative", failures)
        check(icon_link is not None and icon_link.get("href") == "/volta-icon.svg", "icon link missing or not root-relative", failures)
        check('id="root"' in html, "SPA root mount point missing from index.html", failures)
        check("navigator.serviceWorker.register('/sw.js')" in main_source, "production service-worker registration is missing", failures)
        check("url.searchParams.get('overview') !== 'true'" in service_worker, "service worker does not scope API caching to overview=true", failures)
        check("stale: true" in service_worker and "Nothing is live" in service_worker, "offline overview fallback is not explicitly stale/not-live", failures)
        check("url.pathname === '/api/chargers'" in service_worker, "service worker charger-route guard is missing", failures)

        check("Volta Go" in str(manifest.get("name", "")), "manifest name must identify Volta Go", failures)
        check(manifest.get("short_name") == "Volta Go", "manifest short_name must be Volta Go", failures)
        check(manifest.get("start_url") == "/", "manifest start_url must be /", failures)
        check(manifest.get("display") == "standalone", "manifest display must be standalone", failures)
        check(re.fullmatch(r"#[0-9a-fA-F]{6}", manifest.get("theme_color", "")) is not None, "manifest theme_color missing solid hex", failures)
        check(re.fullmatch(r"#[0-9a-fA-F]{6}", manifest.get("background_color", "")) is not None, "manifest background_color missing solid hex", failures)
        icons = manifest.get("icons") or []
        check(bool(icons), "manifest icons list is empty", failures)
        icon = icons[0] if icons else {}
        check(bool(icon.get("src")), "manifest icon src missing", failures)
        check("512x512" in icon.get("sizes", ""), "manifest icon must advertise 512x512", failures)
        purpose_tokens = set(icon.get("purpose", "").split())
        check({"any", "maskable"}.issubset(purpose_tokens), "manifest icon purpose must include any and maskable", failures)

        svg_root = ET.fromstring(icon_text)
        check(svg_root.tag.endswith("svg"), "volta icon is not valid SVG", failures)
        check(bool(svg_root.attrib.get("viewBox")), "volta icon missing viewBox", failures)

        # --- Served shell (the BUILT SPA from the running server) ---
        status, content_type, body = fetch("/")
        served = body.decode("utf-8", "replace")
        static_checks: Dict[str, Dict[str, Any]] = {"/": {"status": status, "content_type": content_type, "bytes": len(body)}}
        check(status == 200, f"/ returned HTTP {status}", failures)
        check(re.search(r"text/html", content_type) is not None, f"/ content-type {content_type!r} is not text/html", failures)
        check('id="root"' in served, "served / is not the SPA shell (no #root)", failures)
        check(len(body) >= 1200, f"/ response too small: {len(body)} bytes", failures)

        # Hashed bundle names come from the served HTML itself.
        js_match = re.search(r'src="(/assets/[^"]+\.js)"', served)
        css_match = re.search(r'href="(/assets/[^"]+\.css)"', served)
        check(js_match is not None, "served / has no /assets/*.js module bundle (SPA not built?)", failures)
        check(css_match is not None, "served / has no /assets/*.css stylesheet", failures)
        bundle_targets: List[Tuple[str, str, int]] = []
        if js_match:
            bundle_targets.append((js_match.group(1), r"javascript", 50000))
        if css_match:
            bundle_targets.append((css_match.group(1), r"text/css", 8000))
        bundle_targets.extend(
            [
                ("/manifest.webmanifest", r"(application/manifest\+json|application/json|octet-stream)", 150),
                ("/volta-icon.svg", r"image/svg\+xml", 500),
                ("/sw.js", r"(application|text)/(javascript|x-javascript)", 1500),
            ]
        )
        for path, content_type_pattern, min_size in bundle_targets:
            status, content_type, body = fetch(path)
            static_checks[path] = {"status": status, "content_type": content_type, "bytes": len(body)}
            check(status == 200, f"{path} returned HTTP {status}", failures)
            check(re.search(content_type_pattern, content_type) is not None, f"{path} content-type {content_type!r} did not match {content_type_pattern}", failures)
            check(len(body) >= min_size, f"{path} response too small: {len(body)} bytes", failures)

        print(json.dumps({
            "ok": not failures,
            "base_url": BASE_URL,
            "title": parser.title.strip(),
            "manifest": {
                "name": manifest.get("name"),
                "display": manifest.get("display"),
                "start_url": manifest.get("start_url"),
                "icons": len(icons),
            },
            "static_checks": static_checks,
            "failures": failures,
        }, ensure_ascii=False, indent=2))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ET.ParseError, AssertionError) as exc:
        print(json.dumps({"ok": False, "base_url": BASE_URL, "error": f"{type(exc).__name__}: {exc}", "failures": failures}, ensure_ascii=False, indent=2))
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
