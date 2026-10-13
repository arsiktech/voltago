#!/usr/bin/env python3
"""Fail-closed offline-PWA source contract.

The shell may cache only static assets plus overview=true source-backed leads. Live charger
responses must remain network-only, and an offline fallback must mark the response stale and
not live before React sees it.
"""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


class PWAOfflineContractTests(unittest.TestCase):
    def test_production_entry_registers_the_same_origin_service_worker(self):
        source = (FRONTEND / "src" / "main.tsx").read_text(encoding="utf-8")
        self.assertIn("import.meta.env.PROD", source)
        self.assertIn("navigator.serviceWorker.register('/sw.js')", source)

    def test_service_worker_caches_only_overview_charger_responses(self):
        source = (FRONTEND / "public" / "sw.js").read_text(encoding="utf-8")
        self.assertIn("url.searchParams.get('overview') !== 'true'", source)
        self.assertIn("url.pathname === '/api/chargers'", source)
        self.assertIn("isDefaultOverviewRequest", source)
        self.assertIn("url.searchParams.get('bbox') !== DEFAULT_OVERVIEW_BBOX", source)
        self.assertIn("DEFAULT_OVERVIEW_URL", source)

    def test_service_worker_snapshot_matches_the_broad_default_overview_request(self):
        """The broad default request is the only source-backed response allowed offline.

        If this URL drifts from chargersUrl(DEFAULT_FILTERS), a controlled offline
        reload misses its cache and strands the driver with an empty error state.
        """
        source = (FRONTEND / "public" / "sw.js").read_text(encoding="utf-8")

        self.assertIn(
            "DEFAULT_OVERVIEW_URL = '/api/chargers?include_leads=true&max_price=5.0&overview=true&bbox=35.8%2C-9.6%2C43.9%2C3.4&compact=true'",
            source,
        )
        self.assertIn("url.searchParams.get('max_price') !== '5.0'", source)
        self.assertIn("const CACHE_VERSION = `${CACHE_PREFIX}v5`", source)

    def test_only_fixed_landing_area_can_be_cached_not_global_or_user_views(self):
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const scope = { URL, Request, self: { location: { origin: 'https://volta.test' }, addEventListener() {} } };
vm.createContext(scope);
vm.runInContext(fs.readFileSync('frontend/public/sw.js', 'utf8') + '\nthis.allowed = isDefaultOverviewRequest; this.defaultURL = DEFAULT_OVERVIEW_URL;', scope);
const query = 'https://volta.test/api/chargers?include_leads=true&max_price=5.0&overview=true&compact=true';
const guard = url => scope.allowed(new Request(url));
console.log(JSON.stringify([
  guard('https://volta.test' + scope.defaultURL),
  guard('https://volta.test' + scope.defaultURL + '&force=true'),
  guard(query),
  guard(query + '&bbox=35.6,139.6,35.8,139.8'),
  guard(query + '&bbox=-90,-180,90,180'),
  guard('https://volta.test' + scope.defaultURL + '&lat=40&lon=-3'),
]));
"""
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), [True, True, False, False, False, False])

    def test_served_shell_audit_checks_the_same_overview_guard(self):
        audit = (ROOT / "scripts" / "qa_pwa_shell.py").read_text(encoding="utf-8")

        self.assertIn("url.searchParams.get('overview') !== 'true'", audit)
        self.assertNotIn("overview') === 'true'", audit)

    def test_navigation_cannot_poison_the_shell_cache_with_api_or_non_html_content(self):
        source = (FRONTEND / "public" / "sw.js").read_text(encoding="utf-8")

        self.assertIn("function isCacheableShellNavigation", source)
        self.assertIn("url.pathname.startsWith('/api/')", source)
        self.assertIn("contentType.includes('text/html')", source)
        self.assertIn("if (isCacheableShellNavigation(request, response) && cache)", source)
        self.assertNotIn("if (response.ok && cache) {\n      const navigationKey", source)

    def test_first_online_visit_precaches_the_complete_build_and_default_overview(self):
        vite = (FRONTEND / "vite.config.ts").read_text(encoding="utf-8")
        worker = (FRONTEND / "public" / "sw.js").read_text(encoding="utf-8")
        browser_contract = (ROOT / "scripts" / "qa_cdn_blocked_ui.js").read_text(encoding="utf-8")

        self.assertIn("manifest: true", vite)
        self.assertIn("/.vite/manifest.json", worker)
        self.assertIn("primeDefaultOverview", worker)
        ready = browser_contract.index("navigator.serviceWorker.ready")
        offline = browser_contract.index("context.setOffline(true)", ready)
        reload = browser_contract.index("await page.reload", ready)
        self.assertLess(offline, reload, "offline proof must not need a second online priming reload")

    def test_bundled_worker_uses_static_cache_without_intercepting_external_or_api_requests(self):
        """MapLibre 6 has a real worker asset; script-only routing strands it offline."""
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const origin = 'https://volta.test';
const cachedUrl = `${origin}/assets/maplibre-gl-worker-cached.js`;
const freshUrl = `${origin}/assets/maplibre-gl-worker-fresh.js`;
const entries = new Map([[cachedUrl, { body: 'cached worker' }]]);
const handlers = {};
const networkCalls = [];
const writes = [];
let offline = true;
const cache = {
  match: async (request) => entries.get(request.url),
  put: async (request, response) => {
    entries.set(request.url, response);
    writes.push(request.url);
  },
};
vm.runInNewContext(fs.readFileSync('frontend/public/sw.js', 'utf8'), {
  URL,
  self: { location: { origin }, addEventListener: (type, handler) => { handlers[type] = handler; } },
  caches: { open: async () => cache },
  fetch: async (request) => {
    networkCalls.push(request.url);
    if (offline) throw new Error('offline');
    return { ok: true, body: 'network worker', clone() { return this; } };
  },
});
async function dispatch(url) {
  let response;
  handlers.fetch({
    request: { method: 'GET', url, mode: 'same-origin', destination: 'worker' },
    respondWith: (pending) => { response = pending; },
  });
  return response === undefined ? null : (await response).body;
}
(async () => {
  const cached = await dispatch(cachedUrl);
  const offlineNetworkCalls = networkCalls.length;
  offline = false;
  const fresh = await dispatch(freshUrl);
  const external = await dispatch('https://elsewhere.test/worker.js');
  const api = await dispatch(`${origin}/api/worker.js`);
  console.log(JSON.stringify({ cached, offlineNetworkCalls, fresh, external, api, networkCalls, writes }));
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
        result = subprocess.run(
            ['node', '-e', script], cwd=ROOT, capture_output=True, text=True,
            check=True, timeout=10,
        )
        self.assertEqual(json.loads(result.stdout), {
            'cached': 'cached worker',
            'offlineNetworkCalls': 0,
            'fresh': 'network worker',
            'external': None,
            'api': None,
            'networkCalls': ['https://volta.test/assets/maplibre-gl-worker-fresh.js'],
            'writes': ['https://volta.test/assets/maplibre-gl-worker-fresh.js'],
        })

    def test_cache_writes_are_best_effort_and_cannot_break_online_fetches(self):
        source = (FRONTEND / "public" / "sw.js").read_text(encoding="utf-8")
        self.assertIn("async function putBestEffort", source)
        self.assertIn("await putBestEffort(cache, cacheKey, response.clone())", source)

    def test_active_worker_falls_back_to_network_when_cache_storage_is_unavailable(self):
        source = (FRONTEND / "public" / "sw.js").read_text(encoding="utf-8")
        self.assertIn("async function openCacheBestEffort", source)
        self.assertIn("const cache = await openCacheBestEffort(OVERVIEW_CACHE)", source)
        self.assertIn("const cache = await openCacheBestEffort(SHELL_CACHE)", source)
        self.assertIn("if (!cache) throw error", source)
        self.assertNotIn("async function overviewResponse(request) {\n  const cache = await caches.open", source)
        self.assertNotIn("async function navigationResponse(request) {\n  const cache = await caches.open", source)
        self.assertNotIn("async function staticResponse(request) {\n  const cache = await caches.open", source)

    def test_offline_overview_fallback_is_explicitly_stale_and_not_live(self):
        source = (FRONTEND / "public" / "sw.js").read_text(encoding="utf-8")
        self.assertIn("Offline — showing previously cached source-backed leads. Nothing is live.", source)
        self.assertIn("stale: true", source)
        self.assertIn("stations: []", source)


if __name__ == "__main__":
    unittest.main()
