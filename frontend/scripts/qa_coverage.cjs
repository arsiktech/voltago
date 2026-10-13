#!/usr/bin/env node
'use strict';
// Browser behavior against fixtures: current counts, search, status distinctions,
// refresh failures, untrusted source strings and a narrow-screen layout.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const base = (process.argv[2] || 'http://127.0.0.1:8876').replace(/\/$/, '');
const fixture = {
  global_adapters: [
    { id: 'ocm', name: 'OpenChargeMap', scope: 'Worldwide', implemented: true, status: 'not_run', records_added_by_global_adapter: 17, method: 'Public repository export' },
    { id: 'afdc', name: 'US AFDC / NREL', scope: 'US and CA', implemented: true, status: 'needs_credentials', records_added_by_global_adapter: 0, credential_requirement: 'Requires an authorized API key' },
  ],
  source_health: {
    generated_at: '2026-10-13T12:00:00Z',
    intelligence: { generated_at: '2026-10-12T12:00:00Z', total_records: 120, deduped_leads: 123, map_eligible_records: 111,
      by_country: { JP: 70, ES: 50, UNKNOWN: 3 },
      by_source: { OpenChargeMap: { total: 70, map_eligible: 60 }, 'Official source': { total: 50, map_eligible: 50 }, '<img src=x onerror=alert(1)>': { total: 3, map_eligible: 1 } } },
    global_coverage: { stats: { countries: { JP: 70, ES: 50 } }, source_manifests: [{ source: 'osm', scope: 'JP', status: 'failed', accepted: 0, errors: ['Provider unavailable'] }] },
    live_geography: { mapareve: 'Spain coverage envelope', gaia: 'Configured Alboraia station only' },
    live_cache: { mapareve: { has_payload: false, fresh: false }, gaia: { has_payload: true, stale: true } },
  },
  requested_source_strategy: { requested_apps: [{ name: 'Excluded app', status: 'removed-rights-blocked', method: 'No station ingestion', risk: 'Written API permission needed', source_url: 'javascript:alert(1)' }] },
};

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.VOLTA_QA_CHROME || '/usr/bin/chromium' });
  const checks = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, serviceWorkers: 'block' });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    let failed = false;
    await page.route('**/api/sources', route => failed ? route.fulfill({ status: 503, body: '{}' }) : route.fulfill({ json: fixture }));
    await page.goto(base + '/coverage/');
    await page.locator('#inventory').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#record-count').innerText(), '120');
    assert.equal(await page.locator('#country-count').innerText(), '2');
    assert.equal(await page.locator('#map-count').innerText(), '111');
    assert.match(await page.locator('#country-explanation').innerText(), /collection manifest/);
    assert.equal(await page.locator('#loaded-sources img').count(), 0);
    assert.equal(await page.locator('#networks a').count(), 0);
    checks.push('Counts use the current API; source strings cannot create HTML or unsafe links');

    await page.getByLabel('Find a country').fill('Japan');
    assert.equal(await page.locator('#countries tr').count(), 1);
    assert.match(await page.locator('#countries').innerText(), /Japan\s+JP\s+70/);
    await page.getByLabel('Find a country').fill('Canada');
    assert.match(await page.locator('#countries').innerText(), /No countries match/);
    await page.getByLabel('Find a source').fill('Official');
    assert.equal(await page.locator('#loaded-sources tr').count(), 1);
    checks.push('Source and country searches filter the reported inventory and have empty states');

    assert.match(await page.locator('#adapters').innerText(), /Not run/);
    assert.match(await page.locator('#adapters').innerText(), /Needs API key/);
    assert.match(await page.locator('#adapters').innerText(), /17/);
    assert.match(await page.locator('#live-sources').innerText(), /No cached response/);
    assert.match(await page.locator('#live-sources').innerText(), /Stale cached response/);
    assert.match(await page.locator('#networks').innerText(), /Excluded/);
    checks.push('Existing records, collector state, live-cache state and blocked sources stay distinct');

    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ colorScheme: 'dark' });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert(await page.getByLabel('Find a country').evaluate(node => node.getBoundingClientRect().height >= 44));
    await page.screenshot({ path: '/tmp/voltago-coverage-mobile.png', fullPage: true });
    checks.push('Mobile dark layout stays within the viewport with 44px controls and scrollable tables');

    failed = true;
    await page.getByRole('button', { name: 'Refresh data' }).click();
    await page.locator('#error').waitFor({ state: 'visible' });
    assert.match(await page.locator('#error').innerText(), /previous response/);
    assert.equal(await page.locator('#record-count').innerText(), '120');
    checks.push('A failed refresh keeps the previous data explicitly labelled out of date');

    await page.reload();
    await page.locator('#error').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#inventory').isVisible(), false);
    assert.match(await page.locator('#error').innerText(), /No deployment counts/);
    assert.equal(await page.getByRole('heading', { name: 'Connectors, vehicles & product limits' }).isVisible(), true);
    assert.deepEqual(errors, []);
    checks.push('An initial API failure makes no deployment or coverage claims');
    console.log(JSON.stringify({ ok: true, checks }, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
