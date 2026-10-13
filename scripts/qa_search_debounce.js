#!/usr/bin/env node
/* Exercise real search input timing and selection without contacting a geocoder. */
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { installDeterministicBasemap } = require('./qa_browser_harness');

const baseUrl = (process.argv[2] || 'http://127.0.0.1:8773').replace(/\/$/, '');
const chromePath = process.env.VOLTA_QA_CHROME;

(async () => {
  const browser = await chromium.launch({ headless: true, ...(chromePath ? { executablePath: chromePath } : {}) });
  try {
    const context = await browser.newContext({
      viewport: { width: 390, height: 844 }, reducedMotion: 'reduce', serviceWorkers: 'block',
    });
    const page = await context.newPage();
    const searches = [];
    const chargerRequests = [];
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await installDeterministicBasemap(page);
    await page.route('**/api/chargers?**', route => {
      chargerRequests.push(route.request().url());
      return route.fulfill({ json: { stations: [], leads: [], meta: { mode: 'overview' } } });
    });
    await page.route('**/api/geocode?**', route => {
      const query = new URL(route.request().url()).searchParams.get('q');
      searches.push(query);
      return route.fulfill({ json: { results: [{ name: query, label: `${query}, Spain`, lat: 39.47, lon: -0.377 }] } });
    });
    await page.goto(baseUrl, { waitUntil: 'networkidle' });
    // Input actions must not race the CI runner's load or animation scheduling.
    await page.clock.install();
    const pauseClock = async () => page.clock.pauseAt(new Date(await page.evaluate(() => Date.now()) + 100));
    await pauseClock();
    const input = page.getByRole('combobox', { name: 'Enter home address or city' });
    await input.pressSequentially('Valencia');
    await page.clock.runFor(249);
    assert.deepEqual(searches, [], 'typing must not send partial city names');
    await page.clock.runFor(1);
    // Let query notifications and entrance animations complete after the boundary.
    await page.clock.resume();
    await page.getByRole('option', { name: 'Valencia Valencia, Spain' }).waitFor({ state: 'attached' });
    assert.deepEqual(searches, ['Valencia'], 'a settled city needs one request');

    await pauseClock();
    const requestsBeforeChange = chargerRequests.length;
    await input.fill('Madrid');
    assert.equal(await page.getByRole('option').count(), 0, 'old suggestions must disappear immediately');
    await input.press('Enter');
    assert.equal(chargerRequests.length, requestsBeforeChange, 'Enter during debounce must not select the previous city');
    await page.clock.resume();
    await page.getByRole('option', { name: 'Madrid Madrid, Spain' }).waitFor({ state: 'attached' });
    assert.deepEqual(searches, ['Valencia', 'Madrid']);

    await pauseClock();
    await input.fill('Bogota');
    await input.fill('');
    await page.clock.runFor(350);
    assert.deepEqual(searches, ['Valencia', 'Madrid'], 'clearing cancels a pending lookup');
    assert.equal(await page.getByRole('option').count(), 0);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ ok: true, typedCity: 'Valencia', rapidTypingRequests: 1, staleSelectionBlocked: true, clearCancelsLookup: true }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
