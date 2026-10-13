#!/usr/bin/env node
/* Real browser contract: defer directory evidence without losing usable cards. */
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { installDeterministicBasemap } = require('./qa_browser_harness');
const baseUrl = (process.argv[2] || 'http://127.0.0.1:8773').replace(/\/$/, '');
const chromePath = process.env.VOLTA_QA_CHROME;
const snapshot = 'a'.repeat(64);
const nextSnapshot = 'b'.repeat(64);
const station = {
  id: 'qa:detail', name: 'Details fixture', lat: 39.47, lon: -0.377,
  lead: true, reliability: 'directory', price_min: 0.2, is_cheap: true,
  verification: { location_verified: true, live_verified: false, level: 'directory' },
  decision: { level: 'check', label: 'Check first', reason: 'Verify at the source before driving.' },
  source_url: 'https://example.com/source',
  primary_source: { name: 'QA directory', url: 'https://example.com/source', kind: 'station_page', quality: 'exact' },
  sources: [{ name: 'QA source', url: 'https://example.com/source', license: 'CC BY 4.0', attribution: 'QA attribution' }],
};

(async () => {
  const browser = await chromium.launch({ headless: true, ...(chromePath ? { executablePath: chromePath } : {}) });
  try {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: 'reduce', serviceWorkers: 'block' });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await installDeterministicBasemap(page);
    let currentSnapshot = snapshot;
    let outcome = 'pending';
    let releaseResponse;
    const requests = [];
    await page.route('**/api/chargers?**', route => {
      assert.equal(new URL(route.request().url()).searchParams.get('summary'), 'true');
      return route.fulfill({ json: { stations: [], leads: [{ ...station, detail_snapshot: currentSnapshot }], meta: { mode: 'overview' } } });
    });
    await page.route('**/api/station?**', async route => {
      const params = new URL(route.request().url()).searchParams;
      assert.equal(params.get('id'), station.id);
      requests.push(params.get('snapshot'));
      if (outcome === 'pending') await new Promise(resolve => { releaseResponse = resolve; });
      if (outcome === 'changed') return route.fulfill({ status: 409, json: { error: 'snapshot_changed' } });
      if (outcome === 'failed') return route.fulfill({ status: 503, json: {} });
      return route.fulfill({ json: { station: { ...station, charge_claim: { summary: 'Full deferred evidence confirmed.' } }, snapshot: params.get('snapshot') } });
    });
    await page.goto(baseUrl, { waitUntil: 'networkidle' });
    const card = page.locator('article').filter({ has: page.getByRole('heading', { name: station.name, exact: true }) });
    await card.waitFor();
    assert.equal(requests.length, 0, 'closed cards must not fetch evidence');
    const details = card.getByRole('button', { name: `Source details for ${station.name}` });
    await details.click();
    await card.getByText('Loading source details…').waitFor();
    await page.waitForFunction(() => document.body.textContent.includes('QA attribution'));
    await card.getByRole('link', { name: `Open ${station.name} in Maps` }).waitFor();
    assert.deepEqual(requests, [snapshot]);
    outcome = 'success';
    releaseResponse();
    await card.getByText('Full deferred evidence confirmed.').waitFor();
    await details.click();
    await card.getByText('Full deferred evidence confirmed.').waitFor({ state: 'detached' });

    outcome = 'failed';
    await details.click();
    await card.getByRole('button', { name: 'Retry details' }).waitFor();
    assert.equal(await card.getByText('Full deferred evidence confirmed.').count(), 0, 'failed refresh must not present cached full evidence');
    outcome = 'success';
    await card.getByRole('button', { name: 'Retry details' }).click();
    await card.getByText('Full deferred evidence confirmed.').waitFor();
    await details.click();
    await card.getByText('Full deferred evidence confirmed.').waitFor({ state: 'detached' });

    outcome = 'changed';
    await details.click();
    await card.getByRole('button', { name: 'Refresh chargers' }).waitFor();
    outcome = 'success';
    currentSnapshot = nextSnapshot;
    await card.getByRole('button', { name: 'Refresh chargers' }).click();
    await card.getByText('Full deferred evidence confirmed.').waitFor();
    assert.equal(requests.at(-1), nextSnapshot);
    await details.click();
    await card.getByText('Full deferred evidence confirmed.').waitFor({ state: 'detached' });

    const beforeOffline = requests.length;
    await context.setOffline(true);
    await page.evaluate(() => window.dispatchEvent(new Event('offline')));
    await details.click();
    await card.getByText('Full source details are unavailable offline. Saved source links remain below.').waitFor();
    assert.equal(requests.length, beforeOffline);
    await card.getByText('Attribution: QA attribution').waitFor();
    assert.equal(await card.getByText('Full deferred evidence confirmed.').count(), 0);
    assert.deepEqual(errors, []);
    await context.close();

    // A failed optional chunk must not replace the usable card with its outer error boundary.
    const blocked = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: 'block' });
    const blockedPage = await blocked.newPage();
    await installDeterministicBasemap(blockedPage);
    await blockedPage.route('**/api/chargers?**', route => route.fulfill({ json: { stations: [], leads: [station], meta: { mode: 'overview' } } }));
    await blockedPage.route('**/assets/StationDetails-*.js', route => route.abort());
    await blockedPage.goto(baseUrl, { waitUntil: 'networkidle' });
    await blockedPage.getByRole('button', { name: `Source details for ${station.name}` }).click();
    await blockedPage.getByText('Details could not load. Check your connection and reload the page, or use the source link above.').waitFor();
    await blockedPage.getByRole('link', { name: `Open ${station.name} in Maps` }).waitFor();
    await blocked.close();
    console.log(JSON.stringify({ ok: true, deferred: true, loading: true, retry: true, snapshotRefresh: true, offline: true, chunkFailureKeepsCard: true }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
